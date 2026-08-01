"""Tests for the Playbook Execution Agent backend logic (Screen 8).

Covers:
- response_action_playbook_profile: deterministic, idempotent, priority escalation,
  honest fallback for unknown action types.
- build_response_action_safety_checks: deterministic pass/warning/fail/unknown from
  canonical facts; fails closed when data is missing; conflict detection.
- summarize_safety_checks: overall verdict aggregation.
- response_action_safety_checks endpoint: 404, read-only (no INSERT/UPDATE),
  workspace-scoped.
- simulate_all_eligible_response_actions: simulates only eligible actions, skips
  already-simulated/terminal, workspace-scoped, idempotent, uses execution_state
  (not the constrained lifecycle status).
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from services.api.app import pilot


# ── Helpers ──────────────────────────────────────────────────────────────────

class _Row:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows if rows is not None else []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


def _fake_request(workspace_id: str = 'ws-1') -> SimpleNamespace:
    return SimpleNamespace(headers={'x-workspace-id': workspace_id})


# ── response_action_playbook_profile ──────────────────────────────────────────

def test_playbook_profile_is_deterministic_for_known_type():
    a = pilot.response_action_playbook_profile('freeze_wallet')
    b = pilot.response_action_playbook_profile('freeze_wallet')
    assert a == b
    assert a['runbook_id'] == 'RBK-CT-01'
    assert a['priority'] == 'critical'
    assert a['category'] == 'Containment'
    assert a['requires_approval'] is True
    assert a['runbook_steps'], 'known runbook must expose steps'


def test_playbook_profile_never_random_across_calls():
    first = pilot.response_action_playbook_profile('notify_team')
    for _ in range(5):
        assert pilot.response_action_playbook_profile('notify_team') == first


def test_playbook_profile_priority_escalates_with_risk_but_never_deescalates():
    # notify_team default priority is low; a critical risk escalates it.
    escalated = pilot.response_action_playbook_profile('notify_team', risk_level='critical')
    assert escalated['priority'] == 'critical'
    # freeze_wallet default is critical; a low risk must NOT de-escalate it.
    not_deescalated = pilot.response_action_playbook_profile('freeze_wallet', risk_level='low')
    assert not_deescalated['priority'] == 'critical'


def test_playbook_profile_unknown_type_is_honest():
    profile = pilot.response_action_playbook_profile('some_ai_only_action', risk_level='medium')
    assert profile['runbook_id'] is None
    assert profile['blast_radius'] == 'unknown'
    assert profile['reversibility'] == 'unknown'
    assert profile['priority'] == 'medium'


def test_playbook_profile_normalizes_legacy_alias():
    # pause_asset -> pause_mint_redeem
    profile = pilot.response_action_playbook_profile('pause_asset')
    assert profile['action_type'] == 'pause_mint_redeem'
    assert profile['runbook_id'] == 'RBK-CT-02'


# ── build_response_action_safety_checks ───────────────────────────────────────

class _SafetyChecksConnection:
    """Serves the incident lookup and conflict-count queries build_ issues."""

    def __init__(self, *, incident_row=None, conflict_count=0):
        self._incident_row = incident_row
        self._conflict_count = conflict_count
        self.executed: list[str] = []

    def execute(self, statement, params=None):
        normalized = ' '.join(str(statement).split())
        self.executed.append(normalized)
        if 'FROM incidents WHERE id' in normalized:
            return _Row(self._incident_row)
        if 'count(*) AS n FROM response_actions' in normalized:
            return _Row({'n': self._conflict_count})
        raise AssertionError(f'unexpected statement: {normalized!r}')


def _check(checks, key):
    for c in checks:
        if c['key'] == key:
            return c
    raise AssertionError(f'missing check {key}')


def test_safety_checks_active_incident_passes():
    conn = _SafetyChecksConnection(incident_row={'status': 'open', 'workflow_status': 'investigating'})
    action = {
        'id': 'a-1', 'incident_id': 'inc-1', 'action_type': 'notify_team',
        'mode': 'recommended', 'status': 'pending', 'execution_state': 'proposed',
        'created_at': pilot.utc_now_iso(),
    }
    checks = pilot.build_response_action_safety_checks(conn, action, {'workspace_id': 'ws-1'})
    assert _check(checks, 'incident_active')['status'] == 'pass'


def test_safety_checks_missing_incident_is_unknown_not_pass():
    conn = _SafetyChecksConnection(incident_row=None)
    action = {'id': 'a-1', 'incident_id': None, 'action_type': 'notify_team', 'mode': 'recommended'}
    checks = pilot.build_response_action_safety_checks(conn, action, {'workspace_id': 'ws-1'})
    incident_check = _check(checks, 'incident_active')
    assert incident_check['status'] == 'unknown'
    # No conflict detection possible without an incident either.
    assert _check(checks, 'no_conflict')['status'] == 'unknown'


def test_safety_checks_resolved_incident_warns():
    conn = _SafetyChecksConnection(incident_row={'status': 'resolved', 'workflow_status': 'resolved'})
    action = {'id': 'a-1', 'incident_id': 'inc-1', 'action_type': 'notify_team', 'mode': 'recommended'}
    checks = pilot.build_response_action_safety_checks(conn, action, {'workspace_id': 'ws-1'})
    assert _check(checks, 'incident_active')['status'] == 'warning'


def test_safety_checks_live_execution_disabled_is_warning(monkeypatch):
    monkeypatch.setattr(pilot, 'env_flag', lambda name, default=False: False if name == 'LIVE_ACTION_EXECUTION_ENABLED' else default)
    conn = _SafetyChecksConnection(incident_row={'status': 'open', 'workflow_status': 'open'})
    action = {'id': 'a-1', 'incident_id': 'inc-1', 'action_type': 'freeze_wallet', 'mode': 'recommended'}
    checks = pilot.build_response_action_safety_checks(conn, action, {'workspace_id': 'ws-1'})
    live = _check(checks, 'live_execution')
    assert live['status'] == 'warning'
    assert 'dry-run' in live['detail'].lower()


def test_safety_checks_not_simulated_is_unknown():
    conn = _SafetyChecksConnection(incident_row={'status': 'open', 'workflow_status': 'open'})
    action = {'id': 'a-1', 'incident_id': 'inc-1', 'action_type': 'notify_team', 'mode': 'recommended', 'execution_state': 'proposed'}
    checks = pilot.build_response_action_safety_checks(conn, action, {'workspace_id': 'ws-1'})
    assert _check(checks, 'simulation_passed')['status'] == 'unknown'


def test_safety_checks_simulated_passes():
    conn = _SafetyChecksConnection(incident_row={'status': 'open', 'workflow_status': 'open'})
    action = {'id': 'a-1', 'incident_id': 'inc-1', 'action_type': 'notify_team', 'mode': 'simulated', 'execution_state': 'simulated'}
    checks = pilot.build_response_action_safety_checks(conn, action, {'workspace_id': 'ws-1'})
    assert _check(checks, 'simulation_passed')['status'] == 'pass'


def test_safety_checks_conflict_detection_fails():
    conn = _SafetyChecksConnection(incident_row={'status': 'open', 'workflow_status': 'open'}, conflict_count=1)
    action = {'id': 'a-1', 'incident_id': 'inc-1', 'action_type': 'freeze_wallet', 'mode': 'recommended'}
    checks = pilot.build_response_action_safety_checks(conn, action, {'workspace_id': 'ws-1'})
    assert _check(checks, 'no_conflict')['status'] == 'fail'


def test_safety_checks_approval_required_without_approval_warns():
    conn = _SafetyChecksConnection(incident_row={'status': 'open', 'workflow_status': 'open'})
    action = {'id': 'a-1', 'incident_id': 'inc-1', 'action_type': 'freeze_wallet', 'mode': 'recommended'}
    checks = pilot.build_response_action_safety_checks(conn, action, {'workspace_id': 'ws-1'})
    assert _check(checks, 'approval_quorum')['status'] == 'warning'


def test_safety_checks_approval_present_passes():
    conn = _SafetyChecksConnection(incident_row={'status': 'open', 'workflow_status': 'open'})
    action = {'id': 'a-1', 'incident_id': 'inc-1', 'action_type': 'freeze_wallet', 'mode': 'recommended', 'approved_at': pilot.utc_now_iso()}
    checks = pilot.build_response_action_safety_checks(conn, action, {'workspace_id': 'ws-1'})
    assert _check(checks, 'approval_quorum')['status'] == 'pass'


def test_safety_checks_stale_recommendation_warns():
    conn = _SafetyChecksConnection(incident_row={'status': 'open', 'workflow_status': 'open'})
    old = (datetime.now(timezone.utc) - timedelta(hours=200)).isoformat()
    action = {'id': 'a-1', 'incident_id': 'inc-1', 'action_type': 'notify_team', 'mode': 'recommended', 'status': 'pending', 'created_at': old}
    checks = pilot.build_response_action_safety_checks(conn, action, {'workspace_id': 'ws-1'})
    assert _check(checks, 'recommendation_fresh')['status'] == 'warning'


def test_safety_checks_never_all_pass_when_data_missing():
    """A recommended, un-simulated, un-approved destructive action with live off
    must not report a green wall — at least one non-pass check must exist."""
    conn = _SafetyChecksConnection(incident_row=None)
    action = {'id': 'a-1', 'incident_id': None, 'action_type': 'freeze_wallet', 'mode': 'recommended'}
    checks = pilot.build_response_action_safety_checks(conn, action, {'workspace_id': 'ws-1'})
    statuses = {c['status'] for c in checks}
    assert statuses != {'pass'}, 'must not present all-pass when facts are missing'


# ── summarize_safety_checks ───────────────────────────────────────────────────

def test_summarize_overall_fail_when_any_fail():
    checks = [
        pilot._safety_check('a', 'A', 'pass', ''),
        pilot._safety_check('b', 'B', 'fail', ''),
        pilot._safety_check('c', 'C', 'warning', ''),
    ]
    summary = pilot.summarize_safety_checks(checks)
    assert summary['overall'] == 'fail'
    assert summary['counts']['fail'] == 1
    assert summary['total'] == 3


def test_summarize_overall_warning_when_unknown_or_warning():
    checks = [pilot._safety_check('a', 'A', 'pass', ''), pilot._safety_check('b', 'B', 'unknown', '')]
    assert pilot.summarize_safety_checks(checks)['overall'] == 'warning'


def test_summarize_overall_pass_when_all_pass():
    checks = [pilot._safety_check('a', 'A', 'pass', ''), pilot._safety_check('b', 'B', 'pass', '')]
    assert pilot.summarize_safety_checks(checks)['overall'] == 'pass'


# ── response_action_safety_checks endpoint (read-only) ─────────────────────────

class _EndpointConnection:
    def __init__(self, *, action_row, incident_row=None, conflict_count=0):
        self._action_row = action_row
        self._incident_row = incident_row
        self._conflict_count = conflict_count
        self.executed: list[str] = []

    def execute(self, statement, params=None):
        normalized = ' '.join(str(statement).split())
        self.executed.append(normalized)
        if 'FROM response_actions WHERE id = %s::uuid AND workspace_id' in normalized and 'count(' not in normalized:
            return _Row(self._action_row)
        if 'FROM incidents WHERE id' in normalized:
            return _Row(self._incident_row)
        if 'count(*) AS n FROM response_actions' in normalized:
            return _Row({'n': self._conflict_count})
        raise AssertionError(f'unexpected statement: {normalized!r}')

    def commit(self):
        raise AssertionError('safety-checks endpoint must not commit (read-only)')


def _bootstrap_endpoint(monkeypatch, connection, *, workspace_id='ws-1'):
    @contextmanager
    def _fake_pg():
        yield connection

    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_a, **_k: {'id': 'user-1'})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda *_a, **_k: {'workspace_id': workspace_id, 'role': 'admin'})


def test_safety_checks_endpoint_returns_checks_and_is_read_only(monkeypatch):
    conn = _EndpointConnection(
        action_row={'id': 'a-1', 'workspace_id': 'ws-1', 'incident_id': 'inc-1', 'alert_id': None,
                    'action_type': 'freeze_wallet', 'mode': 'recommended', 'status': 'pending',
                    'execution_state': 'proposed', 'approved_at': None, 'approved_by_user_id': None,
                    'created_at': pilot.utc_now_iso()},
        incident_row={'status': 'open', 'workflow_status': 'investigating'},
    )
    _bootstrap_endpoint(monkeypatch, conn)

    result = pilot.response_action_safety_checks('a-1', _fake_request())

    assert result['action_id'] == 'a-1'
    assert isinstance(result['checks'], list) and len(result['checks']) >= 6
    assert 'summary' in result and 'overall' in result['summary']
    assert result['playbook']['runbook_id'] == 'RBK-CT-01'
    # Read-only: no INSERT/UPDATE was issued.
    assert not any('INSERT' in s or 'UPDATE' in s for s in conn.executed)


def test_safety_checks_endpoint_404_when_action_missing(monkeypatch):
    conn = _EndpointConnection(action_row=None)
    _bootstrap_endpoint(monkeypatch, conn)
    with pytest.raises(HTTPException) as exc:
        pilot.response_action_safety_checks('missing', _fake_request())
    assert exc.value.status_code == 404


# ── simulate_all_eligible_response_actions ────────────────────────────────────

class _SimulateAllConnection:
    def __init__(self, rows):
        self._rows = rows
        self.executed: list[tuple[str, object]] = []
        self.committed = False

    def execute(self, statement, params=None):
        normalized = ' '.join(str(statement).split())
        self.executed.append((normalized, params))
        if 'FROM response_actions' in normalized and 'SELECT id, action_type, mode, status, execution_state, incident_id' in normalized:
            return _Row(rows=self._rows)
        if 'UPDATE response_actions' in normalized:
            return _Row(None)
        raise AssertionError(f'unexpected statement: {normalized!r}')

    def commit(self):
        self.committed = True


def _bootstrap_simulate_all(monkeypatch, connection, *, workspace_id='ws-1'):
    @contextmanager
    def _fake_pg():
        yield connection

    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, '_require_workspace_permission', lambda *_a, **_k: (
        {'id': 'user-1'}, {'workspace_id': workspace_id, 'role': 'admin'}))
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)


def test_simulate_all_simulates_only_eligible(monkeypatch):
    rows = [
        {'id': 'r1', 'action_type': 'notify_team', 'mode': 'recommended', 'status': 'pending', 'execution_state': 'proposed', 'incident_id': 'inc-1'},
        {'id': 'r2', 'action_type': 'freeze_wallet', 'mode': 'simulated', 'status': 'pending', 'execution_state': 'simulated', 'incident_id': 'inc-1'},  # already simulated
        {'id': 'r3', 'action_type': 'notify_team', 'mode': 'live', 'status': 'executed', 'execution_state': 'confirmed', 'incident_id': 'inc-1'},  # terminal/executed
        {'id': 'r4', 'action_type': 'revoke_approval', 'mode': 'recommended', 'status': 'pending', 'execution_state': 'proposed', 'incident_id': 'inc-1'},
    ]
    conn = _SimulateAllConnection(rows)
    _bootstrap_simulate_all(monkeypatch, conn)

    result = pilot.simulate_all_eligible_response_actions(_fake_request(), incident_id='inc-1')

    assert set(result['simulated_ids']) == {'r1', 'r4'}
    assert result['counts']['simulated'] == 2
    skipped_ids = {s['id'] for s in result['skipped']}
    assert skipped_ids == {'r2', 'r3'}
    # Every UPDATE writes execution_state, never the constrained lifecycle status.
    updates = [s for s, _ in conn.executed if 'UPDATE response_actions' in s]
    assert len(updates) == 2
    for u in updates:
        assert "execution_state = 'simulated'" in u
        assert "status = 'simulated'" not in u
    assert conn.committed


def test_simulate_all_idempotent_when_nothing_eligible(monkeypatch):
    rows = [
        {'id': 'r1', 'action_type': 'notify_team', 'mode': 'simulated', 'status': 'pending', 'execution_state': 'simulated', 'incident_id': 'inc-1'},
    ]
    conn = _SimulateAllConnection(rows)
    _bootstrap_simulate_all(monkeypatch, conn)

    result = pilot.simulate_all_eligible_response_actions(_fake_request(), incident_id='inc-1')
    assert result['simulated_ids'] == []
    assert result['counts']['simulated'] == 0
    assert not conn.committed, 'no commit when nothing was simulated'


def test_simulate_all_scopes_query_to_workspace(monkeypatch):
    rows = [{'id': 'r1', 'action_type': 'notify_team', 'mode': 'recommended', 'status': 'pending', 'execution_state': 'proposed', 'incident_id': None}]
    conn = _SimulateAllConnection(rows)
    _bootstrap_simulate_all(monkeypatch, conn, workspace_id='ws-scoped')

    pilot.simulate_all_eligible_response_actions(_fake_request('ws-scoped'), incident_id=None)
    select = [(s, p) for s, p in conn.executed if 'SELECT id, action_type, mode, status, execution_state, incident_id' in s]
    assert select, 'expected a scoped SELECT'
    # workspace_id is the first bound parameter of the list query.
    assert select[0][1][0] == 'ws-scoped'
