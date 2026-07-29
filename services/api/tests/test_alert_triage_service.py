"""Service-level tests for the Alert Triage Agent (Screen 6).

Uses the repo's fake-connection unit style (no real DB / network / AI). Proves:
the triage run persists clusters + memberships, is AI-optional and fail-closed,
never creates an incident, writes the audit trail, and is role-gated.
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager

import pytest

from services.api.app import pilot
from services.api.app.domains.alert_triage import ai_enrichment, service


class FakeResult:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class ServiceFakeConn:
    def __init__(self, *, active_alerts=None, existing_clusters=None, existing_members=None,
                 schema_ok=True, active_run=None):
        self.active_alerts = active_alerts or []
        self.existing_clusters = existing_clusters or []
        self.existing_members = existing_members or []
        self.schema_ok = schema_ok
        self.active_run = active_run
        self.executed: list = []
        self.inserts: dict = defaultdict(list)
        self.committed = False

    def execute(self, query, params=None):
        n = ' '.join(str(query).split())
        self.executed.append((n, params))
        if 'to_regclass' in n:
            return FakeResult(row={'ok': self.schema_ok})
        if "FROM alert_triage_runs WHERE workspace_id = %s AND status = 'running'" in n:
            return FakeResult(row=self.active_run)
        if n.startswith('INSERT INTO alert_triage_runs'):
            self.inserts['runs'].append(params); return FakeResult()
        if n.startswith('INSERT INTO alert_cluster_history'):
            self.inserts['history'].append(params); return FakeResult()
        if 'FROM alerts a' in n and 'LEFT JOIN targets' in n:
            return FakeResult(rows=[dict(r) for r in self.active_alerts])
        if n.startswith('SELECT id, fingerprint, derived_severity, recommendation_category, member_count'):
            if "status = 'archived'" in n:
                return FakeResult(rows=[])  # no archived clusters in these fixtures
            return FakeResult(rows=[dict(r) for r in self.existing_clusters])
        if 'SELECT alert_id, cluster_id FROM alert_cluster_members' in n:
            return FakeResult(rows=[dict(r) for r in self.existing_members])
        if n.startswith('INSERT INTO alert_clusters'):
            self.inserts['clusters'].append(params); return FakeResult()
        if n.startswith('DELETE FROM alert_cluster_members'):
            self.inserts['member_deletes'].append(params); return FakeResult()
        if n.startswith('INSERT INTO alert_cluster_members'):
            self.inserts['members'].append(params); return FakeResult()
        if n.startswith('UPDATE alert_clusters'):
            self.inserts['cluster_update'].append(params); return FakeResult()
        if n.startswith('UPDATE alert_triage_runs'):
            self.inserts['run_update'].append((n, params)); return FakeResult()
        return FakeResult()

    def commit(self):
        self.committed = True

    def rollback(self):
        pass


def _alert_row(**over):
    base = {
        'id': 'a1', 'workspace_id': 'ws1', 'alert_type': 'wallet_transfer',
        'title': 'Monitored wallet transfer', 'severity': 'high', 'status': 'open',
        'module_key': None, 'target_id': 'tgt1', 'detection_id': 'det1', 'incident_id': None,
        'occurrence_count': 1, 'created_at': '2026-07-29T00:00:00+00:00', 'opened_at': None,
        'payload': {'rule_key': 'smoke_wallet_transfer', 'from_address': '0xAAA', 'tx_hash': '0x1'},
        'findings': None, 'tx_hash': '0x1', 'from_address': '0xAAA', 'to_address': '0xBBB',
        'contract_address': None, 'chain_id': '8453', 'confidence': '0.9',
        'detection_type': 'monitored_wallet_transfer', 'detector_kind': None,
        'primary_asset_id': 'asset1', 'primary_asset_name': 'US Treasury Bond', 'asset_risk_tier': 'high',
    }
    base.update(over)
    return base


def _two_related_plus_unrelated():
    a1 = _alert_row(id='a1', payload={'rule_key': 'smoke_wallet_transfer', 'from_address': '0xAAA', 'tx_hash': '0x1'})
    a2 = _alert_row(id='a2', payload={'rule_key': 'smoke_wallet_transfer', 'from_address': '0xAAA', 'tx_hash': '0x2'})
    oracle = _alert_row(id='a3', severity='medium', detection_type='oracle_price_deviation',
                        primary_asset_id='asset2', primary_asset_name='Stablecoin X', chain_id='1',
                        payload={'rule_key': 'oracle_dev'})
    return [a1, a2, oracle]


class _FakeLiveProvider:
    name = 'openai'
    simulated = False

    def narrate(self, context):
        return 'Two related transfers on the monitored asset were grouped by shared evidence.'


def _audit_capture(monkeypatch):
    audits: list = []
    monkeypatch.setattr(pilot, 'log_audit', lambda *a, **k: audits.append(k))
    return audits


# ── Happy path (rules-based) ───────────────────────────────────────────────
def test_run_triage_persists_clusters_and_is_rules_based_with_mock(monkeypatch):
    audits = _audit_capture(monkeypatch)
    conn = ServiceFakeConn(active_alerts=_two_related_plus_unrelated())
    out = service.run_triage(
        conn, workspace_id='ws1', user_id='u1', triggered_by='user',
        provider_override=ai_enrichment.MockAlertNarrativeProvider(),
        config={'enabled': False, 'provider': ''}, commit=True,
    )
    assert out['status'] == 'completed'
    # Mock provider is simulated -> the run is rules-based, never labelled AI-assisted.
    assert out['triage_run']['mode'] == 'rules_based'
    assert out['triage_run']['clusters_total'] == 1
    assert out['triage_run']['unclustered_count'] == 1
    assert conn.inserts['clusters'], 'a cluster must be persisted'
    assert len(conn.inserts['members']) == 2
    assert conn.committed is True
    actions = {k.get('action') for k in audits}
    assert 'alerts.triage.run_started' in actions
    assert 'alerts.triage.run_completed' in actions


def test_run_triage_never_creates_an_incident(monkeypatch):
    _audit_capture(monkeypatch)
    conn = ServiceFakeConn(active_alerts=_two_related_plus_unrelated())
    service.run_triage(conn, workspace_id='ws1', user_id='u1', triggered_by='system',
                       config={'enabled': False, 'provider': ''}, commit=True)
    joined = ' '.join(n for n, _ in conn.executed).lower()
    # Screen 6 recommends escalation but never opens an incident itself.
    for forbidden in ('insert into incidents', 'update incidents', 'update alerts set incident_id'):
        assert forbidden not in joined


# ── AI fallbacks ───────────────────────────────────────────────────────────
def test_ai_failure_falls_back_to_rules_based(monkeypatch):
    _audit_capture(monkeypatch)
    conn = ServiceFakeConn(active_alerts=_two_related_plus_unrelated())
    out = service.run_triage(conn, workspace_id='ws1', user_id='u1', triggered_by='user',
                             provider_override=ai_enrichment.FailingNarrativeProvider(),
                             config={'enabled': True, 'provider': 'openai'}, commit=True)
    assert out['status'] == 'completed'
    assert out['triage_run']['mode'] == 'rules_based'
    assert out['triage_run']['ai_error'] == 'RuntimeError'
    assert conn.inserts['clusters']  # clustering still persisted despite AI failure


def test_ai_disabled_completes_rules_based(monkeypatch):
    _audit_capture(monkeypatch)
    conn = ServiceFakeConn(active_alerts=_two_related_plus_unrelated())
    out = service.run_triage(conn, workspace_id='ws1', user_id='u1', triggered_by='user',
                             provider_override=None, config={'enabled': False, 'provider': ''}, commit=True)
    assert out['triage_run']['mode'] == 'rules_based'


def test_live_provider_success_marks_ai_assisted(monkeypatch):
    _audit_capture(monkeypatch)
    conn = ServiceFakeConn(active_alerts=_two_related_plus_unrelated())
    out = service.run_triage(conn, workspace_id='ws1', user_id='u1', triggered_by='user',
                             provider_override=_FakeLiveProvider(),
                             config={'enabled': True, 'provider': 'openai'}, commit=True)
    assert out['triage_run']['mode'] == 'ai_assisted'
    assert out['triage_run']['ai_error'] is None


# ── Guards ─────────────────────────────────────────────────────────────────
def test_schema_not_ready_raises_503(monkeypatch):
    _audit_capture(monkeypatch)
    conn = ServiceFakeConn(active_alerts=[], schema_ok=False)
    with pytest.raises(pilot.HTTPException) as exc:
        service.run_triage(conn, workspace_id='ws1', user_id='u1', config={'enabled': False}, commit=True)
    assert exc.value.status_code == 503


def test_run_already_in_progress_is_reported_not_duplicated(monkeypatch):
    _audit_capture(monkeypatch)
    conn = ServiceFakeConn(active_alerts=_two_related_plus_unrelated(), active_run={'id': 'run-x', 'started_at': 'now'})
    out = service.run_triage(conn, workspace_id='ws1', user_id='u1', config={'enabled': False}, commit=True)
    assert out['status'] == 'already_running'
    assert conn.inserts['runs'] == []  # no second run row inserted


def test_empty_alert_set_completes_with_zero_clusters(monkeypatch):
    _audit_capture(monkeypatch)
    conn = ServiceFakeConn(active_alerts=[])
    out = service.run_triage(conn, workspace_id='ws1', user_id='u1', config={'enabled': False, 'provider': ''}, commit=True)
    assert out['status'] == 'completed'
    assert out['triage_run']['clusters_total'] == 0
    assert out['triage_run']['unclustered_count'] == 0


# ── load_active_alerts filtering + missing asset ───────────────────────────
def test_load_active_alerts_excludes_terminal_and_tolerates_missing_asset():
    rows = [
        _alert_row(id='open1'),
        _alert_row(id='resolved1', status='resolved'),
        _alert_row(id='suppressed_unlinked', status='suppressed', detection_id=None,
                   payload={'rule_key': 'x'}),
        # Missing asset relationship entirely (no asset name / id / target).
        _alert_row(id='orphan', primary_asset_id=None, primary_asset_name=None, target_id=None),
    ]
    conn = ServiceFakeConn(active_alerts=rows)
    active, truncated = service.load_active_alerts(conn, workspace_id='ws1')
    ids = {r['id'] for r in active}
    assert 'open1' in ids
    assert 'resolved1' not in ids            # terminal excluded
    assert 'suppressed_unlinked' not in ids  # unlinked suppression excluded
    assert 'orphan' in ids                   # kept (truthful fallback, not dropped)
    assert truncated is False


# ── Authorization wrapper ──────────────────────────────────────────────────
@contextmanager
def _fake_pg(conn):
    yield conn


def test_request_triage_is_role_gated(monkeypatch):
    conn = ServiceFakeConn(active_alerts=_two_related_plus_unrelated())
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda c: None)

    # Denied: the ops-RBAC guard raises 403 and no run is created.
    def _deny(_c, _r):
        raise pilot.HTTPException(status_code=403, detail='forbidden')
    monkeypatch.setattr(pilot, 'require_ops_rbac_guard', _deny)
    with pytest.raises(pilot.HTTPException) as exc:
        service.request_triage(request=object())
    assert exc.value.status_code == 403
    assert conn.inserts['runs'] == []


def test_request_triage_runs_when_authorized(monkeypatch):
    conn = ServiceFakeConn(active_alerts=_two_related_plus_unrelated())
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda c: None)
    monkeypatch.setattr(pilot, 'log_audit', lambda *a, **k: None)
    monkeypatch.setattr(pilot, 'require_ops_rbac_guard', lambda _c, _r: ({'id': 'u1'}, {'workspace_id': 'ws1'}))
    out = service.request_triage(request=object())
    assert out['status'] == 'completed'
    # The run was recorded as triggered by the authenticated user (not the system).
    assert conn.inserts['runs'][0][2] == 'user'
