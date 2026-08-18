"""Screen 11 Governance Guard — backend unit tests.

Deterministic, DB-free tests using recording fake connections, mirroring the
project's existing test convention (fake connection + monkeypatched pilot
helpers). Covers risk classification, deterministic posture scoring, the
version-guarded settings update, the security-policy approval workflow
(separation of duties, execute-once, idempotency, reject), audit before/after +
secret hygiene, tenant isolation, and the anomaly engine.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from services.api.app import governance as g
from services.api.app import pilot


UTC = timezone.utc
NOW = datetime(2026, 8, 18, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Recording fake connection
# ---------------------------------------------------------------------------

class _Cur:
    def __init__(self, rows):
        self._rows = rows or []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class RecordingConnection:
    """Fake DB connection: routes each SQL to a responder and records writes."""

    def __init__(self, responder):
        self._responder = responder
        self.executed: list[tuple[str, tuple]] = []
        self.commits = 0

    def execute(self, sql, params=None):
        self.executed.append((sql, tuple(params) if params else ()))
        return _Cur(self._responder(sql, params or ()))

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass

    # helpers for assertions
    def wrote(self) -> bool:
        return any(sql.strip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')) for sql, _ in self.executed)


@pytest.fixture(autouse=True)
def _fixed_clock_and_audit(monkeypatch):
    monkeypatch.setattr(pilot, 'utc_now', lambda: NOW)
    audits: list[dict] = []

    def _capture(connection, *, action, entity_type, entity_id, request, user_id, workspace_id, metadata=None):
        audits.append({
            'action': action, 'entity_type': entity_type, 'entity_id': entity_id,
            'user_id': user_id, 'workspace_id': workspace_id, 'metadata': metadata or {},
        })

    monkeypatch.setattr(pilot, 'log_audit', _capture)
    monkeypatch.setattr(pilot, '_json_dumps', lambda v: json.dumps(v, default=str))
    return audits


# ===========================================================================
# Risk classification (§8)
# ===========================================================================

def test_classify_mfa_weakening_is_critical_and_requires_approval():
    r = g.classify_security_policy_change(
        {'mfa_enforcement': 'all_members', 'reauthentication_minutes': 30},
        {'mfa_enforcement': 'optional', 'reauthentication_minutes': 30},
    )
    assert r['risk_level'] == g.RISK_CRITICAL
    assert r['requires_approval'] is True
    assert 'MFA enforcement weakened' in r['risk_reason']


def test_classify_session_window_lengthening_is_high():
    r = g.classify_security_policy_change(
        {'mfa_enforcement': 'all_members', 'reauthentication_minutes': 15},
        {'mfa_enforcement': 'all_members', 'reauthentication_minutes': 120},
    )
    assert r['risk_level'] == g.RISK_HIGH
    assert r['requires_approval'] is True


def test_classify_strengthening_is_low_no_approval():
    r = g.classify_security_policy_change(
        {'mfa_enforcement': 'optional', 'reauthentication_minutes': 60},
        {'mfa_enforcement': 'all_members', 'reauthentication_minutes': 30},
    )
    assert r['risk_level'] == g.RISK_LOW
    assert r['requires_approval'] is False


def test_classify_audit_action_levels():
    assert g.classify_audit_action('auth.mfa_disabled') == g.RISK_CRITICAL
    assert g.classify_audit_action('member.role_update') == g.RISK_HIGH
    assert g.classify_audit_action('invitation.create') == g.RISK_MEDIUM
    assert g.classify_audit_action('workspace.settings_updated') == g.RISK_LOW
    # governance.change_executed inherits the change's own risk from metadata
    assert g.classify_audit_action('governance.change_executed', {'risk_level': 'critical'}) == 'critical'


# ===========================================================================
# Deterministic policy posture (§5, §31, §43)
# ===========================================================================

def _posture_responder(*, mfa='all_members', minutes=30, last_evaluated=None,
                       anomaly_counts=None, dormant=0):
    anomaly_counts = anomaly_counts or {}

    def responder(sql, params):
        s = ' '.join(sql.split())
        if 'FROM workspace_auth_policies' in s:
            return [{'mfa_enforcement': mfa, 'reauthentication_minutes': minutes}]
        if 'FROM workspace_settings WHERE workspace_id' in s:
            return [{'workspace_id': 'ws', 'timezone': 'UTC', 'currency': 'USD',
                     'version': 1, 'governance_last_evaluated_at': last_evaluated, 'updated_at': NOW}]
        if 'GROUP BY severity' in s:
            return [{'severity': k, 'count': v} for k, v in anomaly_counts.items()]
        if 'FROM workspace_members wm' in s:
            return [{'count': dormant}]
        raise AssertionError(f'unexpected SQL: {s}')

    return responder


def test_posture_not_evaluated_is_unknown_not_low(monkeypatch):
    monkeypatch.setattr(pilot, '_workspace_auth_policy',
                        lambda c, w: {'mfa_enforcement': 'all_members', 'reauthentication_minutes': 30})
    conn = RecordingConnection(_posture_responder(last_evaluated=None))
    out = g.compute_policy_posture(conn, 'ws', now=NOW)
    assert out['access_risk'] == 'unknown'
    assert out['evidence_status'] == 'insufficient'
    # never-evaluated access control keeps its points UNEARNED (fail-closed)
    anomaly_control = next(c for c in out['controls'] if c['key'] == 'no_critical_access_anomalies')
    assert anomaly_control['status'] == 'unknown'
    assert anomaly_control['points_earned'] == 0
    # and the GET-style computation must NOT write
    assert conn.wrote() is False


def test_posture_evaluated_zero_findings_is_low_and_distinct_from_unavailable(monkeypatch):
    monkeypatch.setattr(pilot, '_workspace_auth_policy',
                        lambda c, w: {'mfa_enforcement': 'all_members', 'reauthentication_minutes': 30})
    conn = RecordingConnection(_posture_responder(last_evaluated=NOW - timedelta(hours=1), anomaly_counts={}))
    out = g.compute_policy_posture(conn, 'ws', now=NOW)
    assert out['access_risk'] == 'low'
    assert out['risk_reduction_percent'] == 100  # every applicable control passes
    assert out['evidence_status'] == 'complete'


def test_posture_weakened_controls_lower_score(monkeypatch):
    monkeypatch.setattr(pilot, '_workspace_auth_policy',
                        lambda c, w: {'mfa_enforcement': 'optional', 'reauthentication_minutes': 120})
    conn = RecordingConnection(_posture_responder(mfa='optional', minutes=120,
                                                  last_evaluated=NOW - timedelta(hours=1), anomaly_counts={}))
    out = g.compute_policy_posture(conn, 'ws', now=NOW)
    assert out['risk_reduction_percent'] < 100
    mfa_control = next(c for c in out['controls'] if c['key'] == 'mfa_enforcement')
    assert mfa_control['status'] == 'fail'


def test_posture_open_critical_anomaly_makes_access_risk_critical(monkeypatch):
    monkeypatch.setattr(pilot, '_workspace_auth_policy',
                        lambda c, w: {'mfa_enforcement': 'all_members', 'reauthentication_minutes': 30})
    conn = RecordingConnection(_posture_responder(last_evaluated=NOW - timedelta(hours=1),
                                                  anomaly_counts={'critical': 1}))
    out = g.compute_policy_posture(conn, 'ws', now=NOW)
    assert out['access_risk'] == 'critical'


# ===========================================================================
# General settings update — optimistic concurrency (§8, §19)
# ===========================================================================

def _settings_update_conn(*, current_version=5, update_returns_version=6, name='Acme Capital'):
    def responder(sql, params):
        s = ' '.join(sql.split())
        if s.startswith('SELECT workspace_id, timezone, currency, version'):
            return [{'workspace_id': 'ws', 'timezone': 'UTC', 'currency': 'USD',
                     'version': current_version, 'governance_last_evaluated_at': None, 'updated_at': NOW}]
        if s.startswith('SELECT name FROM workspaces'):
            return [{'name': name}]
        if s.startswith('INSERT INTO workspace_settings'):
            return []
        if s.startswith('UPDATE workspace_settings') and 'RETURNING version' in s:
            return [{'version': update_returns_version}] if update_returns_version is not None else []
        if s.startswith('UPDATE workspaces SET name'):
            return []
        raise AssertionError(f'unexpected SQL: {s}')

    return RecordingConnection(responder)


def test_settings_update_success_bumps_version_and_audits_before_after(_fixed_clock_and_audit):
    conn = _settings_update_conn(current_version=5, update_returns_version=6)
    out = g.perform_settings_update(
        conn, workspace_id='ws', user_id='u1', actor_role='admin',
        name='Acme Capital 2', timezone='Europe/London', currency='EUR',
        expected_version=5, request=None,
    )
    assert out['version'] == 6
    assert out['timezone'] == 'Europe/London' and out['currency'] == 'EUR'
    audit = _fixed_clock_and_audit[-1]
    assert audit['action'] == 'workspace.settings_updated'
    assert audit['metadata']['before']['currency'] == 'USD'
    assert audit['metadata']['after']['currency'] == 'EUR'
    assert audit['metadata']['version_from'] == 5 and audit['metadata']['version_to'] == 6


def test_settings_update_stale_version_conflicts_409(_fixed_clock_and_audit):
    conn = _settings_update_conn(current_version=8, update_returns_version=None)
    with pytest.raises(pilot.HTTPException) as exc:
        g.perform_settings_update(
            conn, workspace_id='ws', user_id='u1', actor_role='admin',
            name=None, timezone='Europe/London', currency=None,
            expected_version=5, request=None,
        )
    assert exc.value.status_code == 409
    assert exc.value.detail['code'] == 'STALE_VERSION'
    # no audit written on conflict
    assert not _fixed_clock_and_audit


def test_settings_update_rejects_bad_timezone(_fixed_clock_and_audit):
    conn = _settings_update_conn()
    with pytest.raises(pilot.HTTPException) as exc:
        g.perform_settings_update(
            conn, workspace_id='ws', user_id='u1', actor_role='admin',
            name=None, timezone='Mars/Olympus', currency=None,
            expected_version=5, request=None,
        )
    assert exc.value.status_code == 422


# ===========================================================================
# Security-policy change flow (§9, §32, §33) + approval workflow (§5, §9, §10)
# ===========================================================================

def test_low_risk_security_change_applies_directly(monkeypatch, _fixed_clock_and_audit):
    monkeypatch.setattr(pilot, '_workspace_auth_policy',
                        lambda c, w: {'mfa_enforcement': 'optional', 'reauthentication_minutes': 60})

    def responder(sql, params):
        s = ' '.join(sql.split())
        if s.startswith('INSERT INTO workspace_auth_policies'):
            return []
        raise AssertionError(f'unexpected SQL: {s}')

    conn = RecordingConnection(responder)
    out = g.submit_security_change(conn, workspace_id='ws', user_id='u1', actor_role='admin',
                                   proposed={'mfa_enforcement': 'all_members', 'reauthentication_minutes': 30},
                                   request=None)
    assert out['status'] == 'applied'
    assert any(a['action'] == 'governance.security_policy_updated' for a in _fixed_clock_and_audit)


def test_critical_security_change_creates_pending_and_does_not_mutate(monkeypatch, _fixed_clock_and_audit):
    monkeypatch.setattr(pilot, '_workspace_auth_policy',
                        lambda c, w: {'mfa_enforcement': 'all_members', 'reauthentication_minutes': 30})

    def responder(sql, params):
        s = ' '.join(sql.split())
        if 'FROM governance_change_requests' in s and 'status = \'pending\'' in s:
            return []  # no existing pending
        if s.startswith('INSERT INTO governance_change_requests'):
            return []
        raise AssertionError(f'unexpected SQL: {s}')

    conn = RecordingConnection(responder)
    out = g.submit_security_change(conn, workspace_id='ws', user_id='u1', actor_role='admin',
                                   proposed={'mfa_enforcement': 'optional', 'reauthentication_minutes': 30},
                                   request=None)
    assert out['status'] == 'pending_approval'
    assert out['risk_level'] == 'critical'
    # canonical store (workspace_auth_policies) must NOT have been written.
    assert not any('workspace_auth_policies' in sql for sql, _ in conn.executed)
    assert any(a['action'] == 'governance.change_requested' for a in _fixed_clock_and_audit)


def _approval_conn(row):
    """Connection modelling one change request row + its state transitions."""
    state = {'row': dict(row)}

    def responder(sql, params):
        s = ' '.join(sql.split())
        if s.startswith('SELECT id, requested_by_user_id, change_type'):
            # tenant-scoped read: params = (request_id, workspace_id)
            r = state['row']
            if params[1] != r['workspace_id'] or params[0] != r['id']:
                return []
            return [r]
        if s.startswith("UPDATE governance_change_requests SET status = 'rejected'"):
            if state['row']['status'] != 'pending':
                return []
            state['row']['status'] = 'rejected'
            return [{'id': state['row']['id']}]
        if s.startswith("UPDATE governance_change_requests SET status = 'completed'"):
            if state['row']['status'] != 'pending':
                return []
            state['row']['status'] = 'completed'
            return [{'proposed_value': state['row']['proposed_value'],
                     'before_value': state['row']['before_value'],
                     'risk_level': state['row']['risk_level']}]
        if s.startswith('SELECT status FROM governance_change_requests'):
            return [{'status': state['row']['status']}]
        if s.startswith('INSERT INTO workspace_auth_policies'):
            return []
        raise AssertionError(f'unexpected SQL: {s}')

    return RecordingConnection(responder), state


def _pending_row():
    return {
        'id': 'req1', 'workspace_id': 'ws', 'requested_by_user_id': 'requester',
        'change_type': 'security_policy', 'target': 'workspace_auth_policy',
        'before_value': {'mfa_enforcement': 'all_members', 'reauthentication_minutes': 30},
        'proposed_value': {'mfa_enforcement': 'optional', 'reauthentication_minutes': 30},
        'risk_level': 'critical', 'status': 'pending',
    }


def test_self_approval_is_blocked(_fixed_clock_and_audit):
    conn, _ = _approval_conn(_pending_row())
    with pytest.raises(pilot.HTTPException) as exc:
        g.decide_change_request(conn, workspace_id='ws', approver_id='requester', approver_role='owner',
                                request_id='req1', decision='approve', note=None, request=None)
    assert exc.value.status_code == 403
    assert exc.value.detail['code'] == 'SELF_APPROVAL_FORBIDDEN'


def test_approve_by_other_executes_once_and_applies(_fixed_clock_and_audit):
    conn, state = _approval_conn(_pending_row())
    out = g.decide_change_request(conn, workspace_id='ws', approver_id='approver', approver_role='admin',
                                  request_id='req1', decision='approve', note='ok', request=None)
    assert out['status'] == 'completed'
    assert state['row']['status'] == 'completed'
    # canonical apply happened exactly once
    assert sum(1 for sql, _ in conn.executed if 'INSERT INTO workspace_auth_policies' in sql) == 1
    actions = {a['action'] for a in _fixed_clock_and_audit}
    assert 'governance.change_executed' in actions
    assert 'governance.security_policy_updated' in actions


def test_repeated_approval_is_idempotent(_fixed_clock_and_audit):
    conn, state = _approval_conn(_pending_row())
    g.decide_change_request(conn, workspace_id='ws', approver_id='approver', approver_role='admin',
                            request_id='req1', decision='approve', note=None, request=None)
    applies_after_first = sum(1 for sql, _ in conn.executed if 'INSERT INTO workspace_auth_policies' in sql)
    out2 = g.decide_change_request(conn, workspace_id='ws', approver_id='approver', approver_role='admin',
                                   request_id='req1', decision='approve', note=None, request=None)
    assert out2['status'] == 'completed' and out2.get('idempotent') is True
    # no second apply
    applies_total = sum(1 for sql, _ in conn.executed if 'INSERT INTO workspace_auth_policies' in sql)
    assert applies_total == applies_after_first == 1


def test_reject_leaves_policy_unchanged(_fixed_clock_and_audit):
    conn, state = _approval_conn(_pending_row())
    out = g.decide_change_request(conn, workspace_id='ws', approver_id='approver', approver_role='admin',
                                  request_id='req1', decision='reject', note='no', request=None)
    assert out['status'] == 'rejected'
    assert not any('INSERT INTO workspace_auth_policies' in sql for sql, _ in conn.executed)
    assert any(a['action'] == 'governance.change_rejected' for a in _fixed_clock_and_audit)


def test_cross_workspace_approval_is_not_found(_fixed_clock_and_audit):
    conn, _ = _approval_conn(_pending_row())
    with pytest.raises(pilot.HTTPException) as exc:
        # approver in a DIFFERENT workspace must never reach another tenant's request
        g.decide_change_request(conn, workspace_id='other-ws', approver_id='approver', approver_role='admin',
                                request_id='req1', decision='approve', note=None, request=None)
    assert exc.value.status_code == 404


def test_audit_metadata_contains_no_secret_fields(_fixed_clock_and_audit):
    conn, _ = _approval_conn(_pending_row())
    g.decide_change_request(conn, workspace_id='ws', approver_id='approver', approver_role='admin',
                            request_id='req1', decision='approve', note=None, request=None)
    banned = ('password', 'secret', 'token', 'private_key', 'mfa_secret', 'bearer')
    for a in _fixed_clock_and_audit:
        blob = json.dumps(a['metadata']).lower()
        assert not any(b in blob for b in banned), a


# ===========================================================================
# Anomaly engine (§13, §14) — deterministic, evidence-backed
# ===========================================================================

def _ev(eid, action, user_id, meta, minutes_offset=0):
    return {
        'id': eid, 'action': action, 'entity_id': 'x', 'user_id': user_id,
        'metadata': meta, 'created_at': NOW + timedelta(minutes=minutes_offset),
    }


def test_privilege_escalation_detected_with_before_after():
    events = [_ev('e1', 'member.role_update', 'actor',
                  {'previous_role': 'analyst', 'new_role': 'admin', 'target_user_id': 'tgt'})]
    findings = g.detect_anomalies(events, now=NOW)
    esc = [f for f in findings if f['type'] == 'PRIVILEGE_ESCALATION']
    assert len(esc) == 1 and esc[0]['severity'] == 'high'
    assert esc[0]['user_id'] == 'tgt'


def test_escalation_to_owner_is_critical():
    events = [_ev('e1', 'member.role_update', 'actor',
                  {'previous_role': 'admin', 'new_role': 'owner', 'target_user_id': 'tgt'})]
    findings = g.detect_anomalies(events, now=NOW)
    esc = [f for f in findings if f['type'] == 'PRIVILEGE_ESCALATION'][0]
    assert esc['severity'] == 'critical'


def test_no_escalation_without_prior_role_evidence():
    # legacy audit row: only new role recorded -> cannot prove escalation -> no finding
    events = [_ev('e1', 'member.role_update', 'actor', {'role': 'admin'})]
    findings = g.detect_anomalies(events, now=NOW)
    assert not [f for f in findings if f['type'] == 'PRIVILEGE_ESCALATION']


def test_role_downgrade_is_not_escalation():
    events = [_ev('e1', 'member.role_update', 'actor',
                  {'previous_role': 'admin', 'new_role': 'analyst', 'target_user_id': 'tgt'})]
    findings = g.detect_anomalies(events, now=NOW)
    assert not [f for f in findings if f['type'] == 'PRIVILEGE_ESCALATION']


def test_role_change_burst_detected():
    events = [_ev(f'e{i}', 'member.role_update', 'actor',
                  {'previous_role': 'viewer', 'new_role': 'analyst', 'target_user_id': f't{i}'}, minutes_offset=i)
              for i in range(3)]
    findings = g.detect_anomalies(events, now=NOW)
    assert [f for f in findings if f['type'] == 'ROLE_CHANGE_BURST']


def test_mfa_disabled_flagged():
    events = [_ev('e1', 'auth.mfa_disabled', 'u1', {})]
    findings = g.detect_anomalies(events, now=NOW)
    mfa = [f for f in findings if f['type'] == 'MFA_WEAKENED']
    assert mfa and mfa[0]['severity'] == 'high'


def test_mfa_enforcement_weakened_across_policy_updates_is_critical():
    events = [
        _ev('e1', 'workspace.auth_policy_updated', 'u1', {'mfa_enforcement': 'all_members'}, minutes_offset=0),
        _ev('e2', 'workspace.auth_policy_updated', 'u1', {'mfa_enforcement': 'optional'}, minutes_offset=10),
    ]
    findings = g.detect_anomalies(events, now=NOW)
    weak = [f for f in findings if f['type'] == 'MFA_WEAKENED' and f['evidence'].get('signal') == 'workspace_mfa_enforcement_weakened']
    assert weak and weak[0]['severity'] == 'critical'


def test_rapid_security_changes_detected():
    events = [_ev(f'e{i}', 'workspace.api_key.create', 'actor', {}, minutes_offset=i) for i in range(5)]
    findings = g.detect_anomalies(events, now=NOW)
    assert [f for f in findings if f['type'] == 'RAPID_SECURITY_CHANGES']


def test_new_admin_high_risk_action_detected():
    events = [
        _ev('e1', 'member.role_update', 'owner-actor',
            {'previous_role': 'analyst', 'new_role': 'admin', 'target_user_id': 'newadmin'}, minutes_offset=0),
        _ev('e2', 'workspace.auth_policy_updated', 'newadmin', {'mfa_enforcement': 'optional'}, minutes_offset=30),
    ]
    findings = g.detect_anomalies(events, now=NOW)
    na = [f for f in findings if f['type'] == 'NEW_ADMIN_HIGH_RISK_ACTION']
    assert na and na[0]['user_id'] == 'newadmin'


def test_normal_baseline_produces_no_findings():
    events = [
        _ev('e1', 'auth.signin', 'u1', {}, minutes_offset=0),
        _ev('e2', 'workspace.settings_updated', 'u1', {}, minutes_offset=5),
        _ev('e3', 'invitation.create', 'u1', {}, minutes_offset=10),
    ]
    findings = g.detect_anomalies(events, now=NOW)
    assert findings == []


def test_empty_evidence_produces_no_findings():
    assert g.detect_anomalies([], now=NOW) == []


def test_supported_anomaly_types_are_the_evidence_backed_set():
    # Guardrail: unsupported/untrustworthy rules must NOT be advertised as supported.
    assert 'NEW_SOURCE_IP' not in g.SUPPORTED_ANOMALY_TYPES
    assert 'FAILED_LOGIN_BURST' not in g.SUPPORTED_ANOMALY_TYPES
    assert 'ALLOWLIST_WEAKENED' not in g.SUPPORTED_ANOMALY_TYPES
    assert set(g.SUPPORTED_ANOMALY_TYPES) == {
        'PRIVILEGE_ESCALATION', 'ROLE_CHANGE_BURST', 'MFA_WEAKENED',
        'RAPID_SECURITY_CHANGES', 'NEW_ADMIN_HIGH_RISK_ACTION',
    }


# ===========================================================================
# member.role_update audit evidence enhancement (§7) — feeds the anomaly engine
# ===========================================================================

def test_member_role_update_audit_records_previous_and_new_role(monkeypatch, _fixed_clock_and_audit):
    from contextlib import contextmanager

    class Conn:
        def execute(self, query, params=None):
            q = ' '.join(str(query).split())
            class R:
                def fetchone(self_inner):
                    if 'SELECT id, user_id, role FROM workspace_members' in q:
                        return {'id': 'm1', 'user_id': 'target-user', 'role': 'workspace_member'}
                    if "role = 'owner'" in q:
                        return {'count': 3}
                    return None
                def fetchall(self_inner):
                    return []
            return R()

        def commit(self):
            return None

    @contextmanager
    def fake_pg():
        yield Conn()

    monkeypatch.setattr(pilot, 'pg_connection', fake_pg)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda c: None)
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda c, r: ({'id': 'actor'}, {'workspace_id': 'ws'}))

    from fastapi import Request
    req = Request({'type': 'http', 'headers': []})
    out = pilot.update_workspace_member('m1', {'role': 'admin'}, req)
    assert out['role'] == 'admin'

    role_audit = [a for a in _fixed_clock_and_audit if a['action'] == 'member.role_update']
    assert role_audit, 'expected a member.role_update audit event'
    meta = role_audit[-1]['metadata']
    # analyst (workspace_member) -> admin is an escalation; both sides recorded.
    assert meta['previous_role'] == 'analyst'
    assert meta['new_role'] == 'admin'
    assert meta['target_user_id'] == 'target-user'
    assert meta['role'] == 'admin'  # backward-compatible key retained


# ===========================================================================
# Wrapper-level integration: RBAC + tenant isolation glue
# ===========================================================================

def _wrap_conn(responder):
    from contextlib import contextmanager
    conn = RecordingConnection(responder)

    @contextmanager
    def fake_pg():
        yield conn

    return conn, fake_pg


def test_update_workspace_settings_requires_expected_version(monkeypatch):
    _, fake_pg = _wrap_conn(lambda s, p: [])
    monkeypatch.setattr(pilot, 'pg_connection', fake_pg)
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    from fastapi import Request
    with pytest.raises(pilot.HTTPException) as exc:
        g.update_workspace_settings({'timezone': 'UTC'}, Request({'type': 'http', 'headers': []}))
    assert exc.value.status_code == 422


def test_get_policy_posture_wrapper_is_read_only(monkeypatch):
    conn, fake_pg = _wrap_conn(_posture_responder(last_evaluated=None))
    monkeypatch.setattr(pilot, 'pg_connection', fake_pg)
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda c: None)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda c, r: {'id': 'u1'})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda c, u, w: {'workspace_id': 'ws', 'role': 'admin', 'workspace': {'id': 'ws'}})
    monkeypatch.setattr(pilot, '_workspace_auth_policy', lambda c, w: {'mfa_enforcement': 'all_members', 'reauthentication_minutes': 30})
    from fastapi import Request
    out = g.get_policy_posture(Request({'type': 'http', 'headers': []}))
    assert out['access_risk'] == 'unknown'
    # A read/refresh must never write.
    assert conn.wrote() is False
    assert conn.commits == 0
