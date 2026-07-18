"""Onboarding existing-setup detection + provider-backoff retry gating (Screen 4 part 6).

Covers:
  * A workspace that already has an active monitoring target is detected on open —
    onboarding surfaces "Existing monitoring setup detected" with view / retry /
    repair actions instead of starting a new activation flow.
  * Retry is DISABLED while an RPC provider backoff is active, and no new discovery
    run is enqueued (so a provider outage cannot spawn duplicate targets/runs).
  * Once the backoff clears, retry enqueues exactly one run.
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from types import SimpleNamespace

from services.api.app import onboarding_agent as oa
from services.api.app import evm_activity_provider as eap
from services.api.app import pilot

WS = 'ws-1'
SESSION = '11111111-1111-1111-1111-111111111111'


class _Result:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows if rows is not None else ([] if row is None else [row])

    def fetchone(self):
        return self._row

    def fetchall(self):
        return list(self._rows)


class _Conn:
    def __init__(self, *, active_targets=None, session_row=None):
        self.active_targets = active_targets or []
        self.session_row = session_row
        self.inserts = defaultdict(list)
        self.executed: list[str] = []

    def execute(self, query, params=None):
        n = ' '.join(str(query).split())
        low = n.lower()
        self.executed.append(n)
        if low.startswith('select id, name, target_type, chain_network') and 'from targets' in low:
            return _Result(rows=self.active_targets)
        if low.startswith('select * from onboarding_sessions'):
            return _Result(row=self.session_row)
        if low.startswith('insert into onboarding_sessions'):
            self.inserts['sessions'].append(params)
            return _Result(None)
        if 'insert into onboarding_runs' in low or low.startswith('insert into onboarding_runs'):
            self.inserts['runs'].append(params)
            return _Result(row={'id': 'run-1'})
        if low.startswith('insert'):
            self.inserts['other'].append((n, params))
        if 'from onboarding_runs' in low and 'select' in low:
            return _Result(row=None)   # no active run (dedupe check)
        return _Result(None)

    def commit(self):
        pass

    def rollback(self):
        pass


@contextmanager
def _fake_pg(conn):
    yield conn


def _bootstrap(monkeypatch, conn):
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(pilot, '_require_workspace_permission',
                        lambda *a, **k: ({'id': 'user-1'}, {'workspace_id': WS, 'role': 'owner'}))
    monkeypatch.setattr(oa, '_audit', lambda *a, **k: None)


def _req():
    return SimpleNamespace(headers={'x-workspace-id': WS}, client=SimpleNamespace(host='127.0.0.1'))


def _active_target():
    return {
        'id': '9c6ecabb-cd52-404f-9859-40567b09dbb4', 'name': 'USDC Base',
        'target_type': 'contract', 'chain_network': 'base-mainnet',
        'contract_identifier': '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48', 'wallet_address': None,
    }


# ---------------------------------------------------------------------------
# 8. Existing onboarding configuration is detected.
# ---------------------------------------------------------------------------

def test_existing_setup_detected_on_open(monkeypatch):
    conn = _Conn(active_targets=[_active_target()])
    _bootstrap(monkeypatch, conn)
    eap.reset_rpc_provider_state()

    result = oa.create_or_resume_session({}, _req())

    assert result['status'] == 'existing_setup_detected'
    assert result['existing_setup_detected'] is True
    assert result['message'] == 'Existing monitoring setup detected'
    assert result['available_actions'] == ['view_existing', 'retry_provider_verification', 'repair_configuration']
    assert result['active_target_count'] == 1
    assert result['active_targets'][0]['target_id'] == _active_target()['id']
    # No new onboarding session was created (no new activation flow started).
    assert conn.inserts['sessions'] == []


def test_detect_helper_reports_no_setup_when_no_active_targets(monkeypatch):
    conn = _Conn(active_targets=[])
    out = oa._detect_existing_monitoring_setup(conn, workspace_id=WS)
    assert out['existing_setup'] is False
    assert out['active_target_count'] == 0


def test_force_new_bypasses_existing_setup_detection(monkeypatch):
    """force_new is an intentional add-new action and must skip existing-setup detection."""
    conn = _Conn(active_targets=[_active_target()])
    _bootstrap(monkeypatch, conn)
    # Stop the flow right after detection would have run, so we only assert the branch
    # was bypassed (a parse error proves we proceeded past detection).
    monkeypatch.setattr(oa, '_parse_session_inputs', lambda payload: (_ for _ in ()).throw(RuntimeError('proceeded')))
    try:
        oa.create_or_resume_session({'force_new': True, 'resume': False}, _req())
    except RuntimeError as exc:
        assert str(exc) == 'proceeded'
    else:
        raise AssertionError('force_new must bypass existing-setup detection and proceed')


# ---------------------------------------------------------------------------
# 10 + 9. Retry disabled during provider backoff; no duplicate run enqueued.
# ---------------------------------------------------------------------------

def test_retry_disabled_during_provider_backoff(monkeypatch):
    conn = _Conn(session_row={'id': SESSION, 'workspace_id': WS, 'status': 'partial'})
    _bootstrap(monkeypatch, conn)
    monkeypatch.setattr(oa, 'build_session_snapshot', lambda *a, **k: {'session': {'status': 'partial'}})
    eap.reset_rpc_provider_state()
    eap.record_rpc_rate_limited(600.0, host='base-mainnet.g.alchemy.com')  # arm backoff
    assert eap.rpc_provider_backoff_active() is True

    result = oa.retry_session(SESSION, _req())

    assert result['retry_disabled'] is True
    assert result['message'] == 'RPC providers are temporarily unavailable.'
    assert result['provider_verification']['backoff_active'] is True
    assert result['provider_verification']['retry_after_seconds'] is not None
    # No new discovery run was enqueued — a provider outage cannot spawn duplicates.
    assert conn.inserts['runs'] == []


def test_retry_enqueues_run_when_backoff_cleared(monkeypatch):
    conn = _Conn(session_row={'id': SESSION, 'workspace_id': WS, 'status': 'partial'})
    _bootstrap(monkeypatch, conn)
    monkeypatch.setattr(oa, 'build_session_snapshot', lambda *a, **k: {'session': {}})
    monkeypatch.setattr(oa, 'publish_event', lambda *a, **k: None)
    monkeypatch.setattr(oa, '_maybe_run_inline', lambda *a, **k: None)
    monkeypatch.setattr(oa, '_reload_snapshot', lambda *a, **k: {'session': {}})
    monkeypatch.setattr(oa, '_enqueue_run', lambda *a, **k: conn.inserts['runs'].append(k) or {'id': 'run-1'})
    eap.reset_rpc_provider_state()
    assert eap.rpc_provider_backoff_active() is False

    oa.retry_session(SESSION, _req())

    assert len(conn.inserts['runs']) == 1, 'exactly one run enqueued when backoff is clear'
