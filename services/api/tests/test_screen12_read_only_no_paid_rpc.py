"""
Screen 12 must render health WITHOUT consuming paid RPC quota or mutating state.

Regression guards for the two production architecture bugs:

Problem 1 — a normal GET made a live Base RPC call merely to render health.
  * GET /ops/system-health performs NO live RPC network request.
  * GET /ops/reliability/overview performs NO live RPC network request.
  * Unknown/stale provider state stays truthful (never fabricated "healthy") when no
    fresh persisted provider evidence exists.
  * The explicit diagnostic path CAN still perform a live probe where intended.
  * The worker's own live RPC probe path is unchanged.

Problem 2 — a GET runtime-status reconciled (wrote) monitoring configuration.
  (Covered by the read-only assertions added to test_monitoring_runtime_status_states.py;
  this module covers the system-health / reliability RPC side.)

All Base RPC access on a page GET must come from observed state
(backoff / process cache / persisted provider_health_records) — never the provider.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from services.api.app import pilot
from services.api.app import reliability_agent
from services.api.app import system_health as sh

# Every RPC env var the resolver consults, cleared so a stray host in the runner env
# cannot make a "configured" test read "unconfigured" (or vice versa).
_RPC_ENV_VARS = (
    'EVM_RPC_URL', 'STAGING_EVM_RPC_URL',
    'EVM_RPC_URL_8453', 'EVM_RPC_URL_1', 'EVM_RPC_URL_42161',
    'BASE_EVM_RPC_URL', 'EVM_BASE_RPC_URL',
    'EVM_RPC_FAILOVER_URLS', 'EVM_RPC_FAILOVER_URLS_8453',
    'EVM_CHAIN_ID', 'STAGING_EVM_CHAIN_ID',
)


def _set_configured_rpc(monkeypatch):
    """Configure a Base RPC endpoint so the component reads 'configured' (never a
    'URL missing' short-circuit) — the endpoint must still not be dialed on a GET."""
    for var in _RPC_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv('EVM_RPC_URL', 'https://base-mainnet.g.alchemy.com/v2/test-key')
    monkeypatch.delenv('REDIS_URL', raising=False)
    monkeypatch.delenv('UPSTASH_REDIS_REST_URL', raising=False)


class _Cur:
    def __init__(self, one=None, all_=None):
        self._one = one
        self._all = all_ or []

    def fetchone(self):
        return self._one

    def fetchall(self):
        return self._all


class _SHConn:
    """Recording fake connection for build_system_health_snapshot.

    Returns benign rows for every health query and records all executed SQL so a test
    can assert a GET issues no write statement. The Base RPC observation query (the
    only one selecting ``metadata`` from provider_health_records) returns the
    configured ``observation`` row (or ``None`` for "no persisted evidence").
    """

    def __init__(self, *, observation=None, heartbeat=None, telemetry=None, detection=None):
        self.observation = observation
        self.heartbeat = heartbeat
        self.telemetry = telemetry
        self.detection = detection
        self.sql: list[str] = []

    def execute(self, query, params=None):
        self.sql.append(str(query))
        q = ' '.join(str(query).split()).lower()
        if q.startswith('select 1'):
            return _Cur(one={'value': 1})
        # The read-only Base RPC observation query (SELECT ... metadata ... LIMIT 1).
        if 'provider_health_records' in q and 'metadata' in q:
            return _Cur(one=self.observation)
        if 'monitoring_heartbeats' in q:
            return _Cur(one=({'last_heartbeat_at': self.heartbeat} if self.heartbeat else None))
        if 'telemetry_events' in q and 'count' in q:
            return _Cur(one={'cnt': 0})
        if 'telemetry_events' in q:
            return _Cur(one=({'observed_at': self.telemetry} if self.telemetry else None))
        if ('detection_events' in q or 'detections' in q) and 'count' in q:
            return _Cur(one={'cnt': 0})
        if 'detection_events' in q or 'detections' in q:
            return _Cur(one=({'created_at': self.detection} if self.detection else None))
        if 'monitoring_targets' in q:
            return _Cur(one={'cnt': 0})
        if 'provider_health_records' in q:
            return _Cur(one={'ok_cnt': 0, 'total': 0}, all_=[])
        return _Cur(one=None, all_=[])

    def write_statements(self) -> list[str]:
        out = []
        for stmt in self.sql:
            head = ' '.join(str(stmt).split()).strip().upper()
            if head.startswith(('INSERT', 'UPDATE', 'DELETE')):
                out.append(head[:48])
        return out


@contextmanager
def _pg(conn):
    yield conn


def _recording_urlopen():
    """A urlopen stand-in that records every call (a live provider dial) and returns a
    valid eth_blockNumber response, so a read-only path that wrongly probed is caught
    by a non-empty call list rather than by a swallowed exception."""
    calls: list[str] = []

    def _fn(req, timeout=None):
        calls.append(getattr(req, 'full_url', None) or str(req))
        resp = MagicMock()
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        resp.read.return_value = json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': '0x2d16800'}).encode()
        return resp

    _fn.calls = calls
    return _fn


def _quiet_side_channels(monkeypatch):
    """Silence infra checks irrelevant to the RPC-probe assertion (they never touch a
    paid provider) so the tests stay fast and hermetic."""
    monkeypatch.setattr(sh, '_check_alert_delivery', lambda: sh._component('unavailable', 'n/a'))
    monkeypatch.setattr(sh, '_check_redis', lambda: sh._component('unavailable', 'n/a'))


# ---------------------------------------------------------------------------
# 1. GET /ops/system-health performs no live RPC network request.
# 5. Unknown provider state stays truthful when no fresh evidence exists.
# ---------------------------------------------------------------------------

def test_system_health_get_makes_no_live_rpc_and_is_truthfully_unknown(monkeypatch):
    _set_configured_rpc(monkeypatch)
    sh._reset_rpc_health_cache()
    _quiet_side_channels(monkeypatch)
    conn = _SHConn(observation=None)  # no persisted provider evidence
    urlopen = _recording_urlopen()
    monkeypatch.setattr(sh, 'urlopen', urlopen)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _pg(conn))

    snapshot = sh.build_system_health_snapshot(None)  # default: read-only GET

    assert urlopen.calls == [], 'a page GET must not dial the RPC provider'
    base = snapshot['components']['base_rpc']
    # Truthful: unknown/unavailable, never fabricated healthy.
    assert base['status'] == 'unavailable'
    assert base['status'] != 'healthy'
    assert 'unknown' in base['message'].lower()
    assert not conn.write_statements(), 'system-health GET must not write'


def test_system_health_get_stale_evidence_is_degraded_not_healthy(monkeypatch):
    _set_configured_rpc(monkeypatch)
    sh._reset_rpc_health_cache()
    _quiet_side_channels(monkeypatch)
    stale_at = datetime.now(timezone.utc) - timedelta(hours=3)
    observation = {
        'status': 'healthy', 'checked_at': stale_at, 'latency_ms': 40,
        'metadata': {'provider_host': 'base-mainnet.g.alchemy.com', 'latest_block': 123, 'actor_type': 'worker'},
    }
    conn = _SHConn(observation=observation)
    urlopen = _recording_urlopen()
    monkeypatch.setattr(sh, 'urlopen', urlopen)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _pg(conn))

    snapshot = sh.build_system_health_snapshot(None)

    assert urlopen.calls == []
    base = snapshot['components']['base_rpc']
    # A stale observation must never read healthy just because the last poll was green.
    assert base['status'] == 'degraded'
    assert 'stale' in base['message'].lower()


def test_system_health_get_reflects_fresh_persisted_health_without_probe(monkeypatch):
    _set_configured_rpc(monkeypatch)
    sh._reset_rpc_health_cache()
    _quiet_side_channels(monkeypatch)
    fresh_at = datetime.now(timezone.utc) - timedelta(seconds=60)
    observation = {
        'status': 'healthy', 'checked_at': fresh_at, 'latency_ms': 42,
        'metadata': {'provider_host': 'base-mainnet.g.alchemy.com', 'latest_block': 47145728, 'actor_type': 'worker'},
    }
    conn = _SHConn(observation=observation)
    urlopen = _recording_urlopen()
    monkeypatch.setattr(sh, 'urlopen', urlopen)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _pg(conn))

    snapshot = sh.build_system_health_snapshot(None)

    assert urlopen.calls == [], 'fresh persisted evidence must be reflected without a probe'
    base = snapshot['components']['base_rpc']
    assert base['status'] == 'healthy'
    assert 'observed healthy' in base['message'].lower()
    assert 'base-mainnet.g.alchemy.com' in base['message']


def test_system_health_get_rpc_not_configured_is_unavailable_without_probe(monkeypatch):
    for var in _RPC_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv('REDIS_URL', raising=False)
    sh._reset_rpc_health_cache()
    _quiet_side_channels(monkeypatch)
    conn = _SHConn(observation=None)
    urlopen = _recording_urlopen()
    monkeypatch.setattr(sh, 'urlopen', urlopen)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _pg(conn))

    snapshot = sh.build_system_health_snapshot(None)

    assert urlopen.calls == []
    base = snapshot['components']['base_rpc']
    assert base['status'] == 'unavailable'
    assert 'missing' in base['message'].lower()


# ---------------------------------------------------------------------------
# 4. Repeated GETs are idempotent with respect to persistent application state.
# ---------------------------------------------------------------------------

def test_repeated_system_health_gets_are_idempotent_and_write_free(monkeypatch):
    _set_configured_rpc(monkeypatch)
    sh._reset_rpc_health_cache()
    _quiet_side_channels(monkeypatch)
    urlopen = _recording_urlopen()
    monkeypatch.setattr(sh, 'urlopen', urlopen)

    conns: list[_SHConn] = []

    def _factory():
        c = _SHConn(observation=None)
        conns.append(c)
        return _pg(c)

    monkeypatch.setattr(pilot, 'pg_connection', _factory)

    first = sh.build_system_health_snapshot(None)
    second = sh.build_system_health_snapshot(None)
    third = sh.build_system_health_snapshot(None)

    assert urlopen.calls == [], 'no GET, repeated or otherwise, may dial the provider'
    assert all(not c.write_statements() for c in conns), 'no GET may write persistent state'
    assert (
        first['components']['base_rpc']['status']
        == second['components']['base_rpc']['status']
        == third['components']['base_rpc']['status']
        == 'unavailable'
    )


# ---------------------------------------------------------------------------
# 2. GET /ops/reliability/overview performs no live RPC network request.
# ---------------------------------------------------------------------------

def test_reliability_overview_get_makes_no_live_rpc(monkeypatch):
    _set_configured_rpc(monkeypatch)
    sh._reset_rpc_health_cache()
    _quiet_side_channels(monkeypatch)
    urlopen = _recording_urlopen()
    monkeypatch.setattr(sh, 'urlopen', urlopen)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _pg(_SHConn(observation=None)))

    overview = reliability_agent.build_reliability_overview(SimpleNamespace(headers={}))

    assert urlopen.calls == [], 'the reliability overview GET must not dial the provider'
    assert 'components' in overview
    # No RPC component may be fabricated healthy without a live/fresh observation.
    for comp in overview['components']:
        if 'rpc' in str(comp.get('component', '')).lower():
            assert comp['status'] != 'healthy'


# ---------------------------------------------------------------------------
# 6. The explicit diagnostic path CAN still perform a live probe where intended.
# ---------------------------------------------------------------------------

def test_explicit_snapshot_probe_performs_live_call(monkeypatch):
    _set_configured_rpc(monkeypatch)
    sh._reset_rpc_health_cache()
    _quiet_side_channels(monkeypatch)
    conn = _SHConn(observation=None)
    urlopen = _recording_urlopen()
    monkeypatch.setattr(sh, 'urlopen', urlopen)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _pg(conn))

    snapshot = sh.build_system_health_snapshot(None, allow_live_rpc_probe=True)

    assert len(urlopen.calls) >= 1, 'the explicit diagnostic must be allowed to probe live'
    assert snapshot['components']['base_rpc']['status'] == 'healthy'  # from the live 0x2d16800 block


def test_run_diagnostic_requests_a_live_probe(monkeypatch):
    """The explicit RBAC-gated diagnostic (POST /ops/reliability/diagnostics/run) must
    pass allow_live_rpc_probe=True to the snapshot evaluator; the read overview must not."""
    captured: dict[str, bool] = {}

    class _Stop(Exception):
        pass

    def _fake_snapshot(request=None, *, allow_live_rpc_probe=False):
        captured['flag'] = allow_live_rpc_probe
        raise _Stop()

    monkeypatch.setattr(reliability_agent, '_snapshot', _fake_snapshot)
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _pg(_SHConn()))
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(
        pilot, '_require_workspace_permission',
        lambda *a, **k: ({'id': 'user-1'}, {'workspace_id': 'ws-1'}),
    )
    monkeypatch.setattr(pilot, 'utc_now', lambda: datetime.now(timezone.utc))

    with pytest.raises(_Stop):
        reliability_agent.run_diagnostic(SimpleNamespace(headers={}))

    assert captured.get('flag') is True


# ---------------------------------------------------------------------------
# 7. Existing worker RPC probe behavior is unchanged (the low-level live probe,
#    used by the worker's RPC recheck and the diagnostic, still dials the provider).
# ---------------------------------------------------------------------------

def test_worker_direct_rpc_probe_still_performs_live_call(monkeypatch):
    _set_configured_rpc(monkeypatch)
    sh._reset_rpc_health_cache()
    urlopen = _recording_urlopen()
    monkeypatch.setattr(sh, 'urlopen', urlopen)

    result = sh._check_rpc()

    assert len(urlopen.calls) >= 1, 'the direct probe (worker/diagnostic path) must remain live'
    assert result['status'] == 'healthy'
