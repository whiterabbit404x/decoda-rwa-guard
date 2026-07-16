"""
Screen 4 "Run Diagnostic" + runtime-evidence tests.

Covers the two production gaps this change closes:

1. Root-cause drift: the monitoring worker writes provider_health / coverage rows keyed
   by the RAW targets(id) (migration 0082), but the Screen-4 enrichment read them by the
   uuid5 *canonical* monitored_targets(id). They never joined, so a freshly polled target
   still read 0/1 with no latency. The enrichment must now read evidence by targets.id.

2. The "Run Diagnostic" action must run a REAL bounded provider probe (chain id, latest
   block, deployed bytecode) and persist truthful evidence + heartbeat — never fabricate
   health, be idempotent, and stay workspace-scoped.

These use lightweight fake connections + monkeypatched RPC so they run without a live DB
or network.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from services.api.app import pilot


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


WS = '11111111-1111-1111-1111-111111111111'
TARGET_ID = '22222222-2222-2222-2222-222222222222'
SYSTEM_ID = '33333333-3333-3333-3333-333333333333'
ASSET_ID = '44444444-4444-4444-4444-444444444444'
USDC = '0x833589fcd6edb6e08f4c7c32d4f71b54bda02913'
ALCHEMY = 'base-mainnet.g.alchemy.com'
NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# 1. Root-cause: enrichment reads worker evidence keyed by RAW targets(id).
# ---------------------------------------------------------------------------
def _worker_keyed_conn(*, provider_rows, coverage_rows=None, latency_rows=None):
    """Fake conn that returns provider-health/coverage rows keyed like the worker writes them."""
    class _Conn:
        def execute(self, query, params=None):
            q = ' '.join(str(query).split()).upper()
            if 'FROM MONITOR_CHECKPOINT' in q:
                return _Result([{'monitored_system_id': SYSTEM_ID, 'latest_block': 8453999}])
            if 'FROM PROVIDER_HEALTH_RECORDS' in q and 'LATENCY_MS IS NOT NULL' in q:
                return _Result(latency_rows or [])
            if 'FROM PROVIDER_HEALTH_RECORDS' in q:
                return _Result(provider_rows)
            if 'FROM TARGET_COVERAGE_RECORDS' in q:
                return _Result(coverage_rows or [])
            return _Result([])

    return _Conn()


def _usdc_target():
    return {
        'id': TARGET_ID, 'name': 'USDC monitor', 'target_type': 'contract',
        'chain_network': 'base', 'chain_id': 8453, 'contract_identifier': USDC, 'wallet_address': None,
        'asset_id': ASSET_ID, 'asset_name': 'USDC', 'asset_missing': False,
        'monitoring_mode': 'poll', 'monitoring_enabled': True, 'enabled': True, 'monitored_system_id': SYSTEM_ID,
        'target_metadata': {'rpc_sources': {'primary_host': ALCHEMY, 'explanation': 'Primary route.'}},
    }


def _usdc_system():
    return {
        'id': SYSTEM_ID, 'target_id': TARGET_ID, 'asset_id': ASSET_ID, 'chain': 'base',
        'is_enabled': True, 'runtime_status': 'healthy', 'last_heartbeat': NOW.isoformat(),
        'last_event_at': NOW.isoformat(), 'coverage_reason': 'covered', 'freshness_status': 'fresh',
        'asset_name': 'USDC', 'target_name': 'USDC monitor',
    }


def test_enrichment_reads_worker_evidence_keyed_by_raw_target_id():
    """A provider_health row written under the RAW targets(id) must surface as 1/1 healthy.

    This is the exact drift the production symptom (0/1, empty latency) came from: the
    worker writes targets.id, so the enrichment must read by targets.id, not the uuid5 id.
    """
    provider_rows = [{
        'target_id': TARGET_ID,  # RAW targets.id — exactly what the worker persists
        'status': 'healthy', 'latency_ms': 63, 'checked_at': NOW,
        'evidence_source': 'live', 'provider_type': ALCHEMY, 'error_message': None,
    }]
    coverage_rows = [{
        'target_id': TARGET_ID, 'coverage_status': 'reporting', 'last_poll_at': NOW,
        'last_heartbeat_at': NOW, 'last_telemetry_at': NOW, 'last_detection_at': None,
        'evidence_source': 'live', 'computed_at': NOW,
    }]
    enrichment = pilot._build_monitoring_sources_enrichment(
        _worker_keyed_conn(provider_rows=provider_rows, coverage_rows=coverage_rows),
        workspace_id=WS, assets=[{'id': ASSET_ID, 'name': 'USDC'}],
        targets=[_usdc_target()], systems=[_usdc_system()], now=NOW,
    )
    source = enrichment['sources'][0]
    assert source['provider'] == ALCHEMY
    assert source['median_latency_ms'] == 63
    assert source['coverage_state'] == 'reporting'

    ph = enrichment['provider_health']
    # One healthy primary, no fallback => 1/1, never 0/1.
    assert ph['total'] == 1
    assert ph['healthy_count'] == 1


def test_enrichment_without_evidence_still_reads_provisioning_not_healthy():
    """No provider-health row => the source is not healthy and provider health is 0/anything."""
    enrichment = pilot._build_monitoring_sources_enrichment(
        _worker_keyed_conn(provider_rows=[]),
        workspace_id=WS, assets=[{'id': ASSET_ID, 'name': 'USDC'}],
        targets=[_usdc_target()], systems=[_usdc_system()], now=NOW,
    )
    assert enrichment['provider_health']['healthy_count'] == 0


def test_cross_workspace_evidence_cannot_satisfy_this_target():
    """A provider-health row keyed to a DIFFERENT target id must not make this source healthy."""
    other_rows = [{
        'target_id': 'ffffffff-ffff-ffff-ffff-ffffffffffff',  # different target/workspace
        'status': 'healthy', 'latency_ms': 10, 'checked_at': NOW,
        'evidence_source': 'live', 'provider_type': ALCHEMY, 'error_message': None,
    }]
    enrichment = pilot._build_monitoring_sources_enrichment(
        _worker_keyed_conn(provider_rows=other_rows),
        workspace_id=WS, assets=[{'id': ASSET_ID, 'name': 'USDC'}],
        targets=[_usdc_target()], systems=[_usdc_system()], now=NOW,
    )
    source = enrichment['sources'][0]
    # The unrelated healthy row is never attached to this target.
    assert source['median_latency_ms'] is None
    assert enrichment['provider_health']['healthy_count'] == 0


def test_provider_health_loader_query_is_workspace_scoped():
    """The provider-health read must filter by workspace_id (no cross-tenant leakage)."""
    seen = {}

    class _Conn:
        def execute(self, query, params=None):
            seen['q'] = ' '.join(str(query).split()).upper()
            seen['p'] = params
            return _Result([])

    pilot._load_latest_provider_health_by_target(_Conn(), workspace_id=WS, canonical_ids=[TARGET_ID])
    assert 'WHERE WORKSPACE_ID = %S::UUID' in seen['q']
    assert seen['p'][0] == WS


# ---------------------------------------------------------------------------
# 2. QuickNode degradation must not overwrite healthy Alchemy polling.
# ---------------------------------------------------------------------------
def test_pick_provider_health_prefers_primary_over_newer_degraded_stream():
    records = [
        {'provider_type': 'quicknode.stream', 'status': 'degraded', 'latency_ms': 900, 'checked_at': '2026-07-16T12:05:00Z'},
        {'provider_type': ALCHEMY, 'status': 'healthy', 'latency_ms': 55, 'checked_at': '2026-07-16T12:00:00Z'},
    ]
    picked = pilot._pick_provider_health_record(records, ALCHEMY)
    assert picked['provider_type'] == ALCHEMY
    assert picked['status'] == 'healthy'


def test_quicknode_degradation_does_not_erase_healthy_alchemy_status():
    """A newer degraded QuickNode stream record must not flip the Alchemy-primary source."""
    provider_rows = [
        {'target_id': TARGET_ID, 'status': 'degraded', 'latency_ms': 900, 'checked_at': datetime(2026, 7, 16, 12, 5, tzinfo=timezone.utc),
         'evidence_source': 'live', 'provider_type': 'quicknode.stream', 'error_message': 'stream_lag'},
        {'target_id': TARGET_ID, 'status': 'healthy', 'latency_ms': 55, 'checked_at': datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc),
         'evidence_source': 'live', 'provider_type': ALCHEMY, 'error_message': None},
    ]
    enrichment = pilot._build_monitoring_sources_enrichment(
        _worker_keyed_conn(provider_rows=provider_rows),
        workspace_id=WS, assets=[{'id': ASSET_ID, 'name': 'USDC'}],
        targets=[_usdc_target()], systems=[_usdc_system()], now=NOW,
    )
    source = enrichment['sources'][0]
    assert source['provider'] == ALCHEMY
    assert source['median_latency_ms'] == 55  # Alchemy latency, not QuickNode's 900


# ---------------------------------------------------------------------------
# 3. P95 truthfulness — never fabricated with insufficient samples.
# ---------------------------------------------------------------------------
def test_p95_none_below_sample_floor():
    assert pilot._p95_from_samples([42.0]) is None
    assert pilot._p95_from_samples([float(i) for i in range(pilot._P95_MIN_SAMPLES - 1)]) is None


def test_p95_computed_with_enough_samples():
    samples = [float(i) for i in range(1, 101)]  # 1..100
    p95 = pilot._p95_from_samples(samples)
    assert p95 == 95.0


def test_enrichment_marks_p95_insufficient_with_single_sample():
    provider_rows = [{
        'target_id': TARGET_ID, 'status': 'healthy', 'latency_ms': 63, 'checked_at': NOW,
        'evidence_source': 'live', 'provider_type': ALCHEMY, 'error_message': None,
    }]
    enrichment = pilot._build_monitoring_sources_enrichment(
        _worker_keyed_conn(provider_rows=provider_rows, latency_rows=[{'target_id': TARGET_ID, 'latency_ms': 63}]),
        workspace_id=WS, assets=[{'id': ASSET_ID, 'name': 'USDC'}],
        targets=[_usdc_target()], systems=[_usdc_system()], now=NOW,
    )
    source = enrichment['sources'][0]
    assert source['p95_insufficient'] is True
    assert source['p95_latency_ms'] is None
    assert source['p95_sample_count'] == 1


# ---------------------------------------------------------------------------
# 4. _diagnose_target: real bounded probe persists truthful evidence.
# ---------------------------------------------------------------------------
class _RecordingConn:
    def __init__(self):
        self.calls: list[tuple[str, tuple]] = []

    def execute(self, query, params=None):
        self.calls.append((' '.join(str(query).split()).upper(), tuple(params or ())))
        return _Result([])

    def find(self, needle):
        return [c for c in self.calls if needle in c[0]]


def _stub_probe(monkeypatch, result):
    monkeypatch.setattr(pilot, '_diagnostic_probe', lambda rpc_url, *, address, is_contract: result)


def _stub_resolve(monkeypatch, *, chain_id=8453, rpc_url=f'https://{ALCHEMY}/v2/SECRETKEY'):
    import services.api.app.evm_activity_provider as evm
    monkeypatch.setattr(evm, 'resolve_chain_rpc', lambda network: {
        'network': network, 'expected_chain_id': chain_id, 'rpc_url': rpc_url,
        'rpc_url_env': 'BASE_EVM_RPC_URL', 'rpc_urls': [rpc_url],
    })


def _diag_target(**over):
    base = {
        'id': TARGET_ID, 'workspace_id': WS, 'name': 'USDC monitor', 'chain_network': 'base',
        'chain_id': 8453, 'target_type': 'contract', 'contract_identifier': USDC, 'wallet_address': None,
        'asset_id': ASSET_ID, 'enabled': True, 'monitoring_enabled': True,
        'linked_asset_id': ASSET_ID, 'asset_name': 'USDC',
        'monitored_system_id': SYSTEM_ID, 'monitored_system_enabled': True,
        'monitoring_config_id': 'cfg-1', 'monitoring_config_enabled': True, 'provider_type': 'evm_rpc',
    }
    base.update(over)
    return base


def test_diagnose_healthy_contract_writes_real_evidence(monkeypatch):
    _stub_resolve(monkeypatch)
    _stub_probe(monkeypatch, {
        'ok': True, 'latency_ms': 72, 'chain_id': 8453, 'latest_block': 8453999,
        'bytecode_present': True, 'error': None,
    })
    conn = _RecordingConn()
    result = pilot._diagnose_target(conn, workspace_id=WS, target=_diag_target(), correlation_id='corr-1', now=NOW)

    assert result['provider_health_status'] == 'healthy'
    assert result['reachable'] is True
    assert result['chain_id_ok'] is True
    assert result['latency_ms'] == 72
    assert result['latest_block'] == 8453999
    assert result['bytecode_present'] is True
    assert result['provider_host'] == ALCHEMY  # host only — no secret key leaked
    assert 'SECRETKEY' not in repr(result)

    # provider-health row is written under the RAW targets(id) with a REAL latency.
    # Canonical helper param order:
    # (id, workspace_id, host, target_id, status, checked_at, latency_ms, error, evidence_source, metadata)
    ph = conn.find('INSERT INTO PROVIDER_HEALTH_RECORDS')
    assert len(ph) == 1
    params = ph[0][1]
    assert params[1] == WS                 # workspace_id
    assert params[2] == ALCHEMY            # provider host (redacted) as provider_type
    assert params[3] == TARGET_ID          # target_id = raw targets.id
    assert params[4] == 'healthy'          # status
    assert params[6] == 72                 # latency_ms (measured, not None)
    assert params[8] == 'live'             # evidence_source
    # Diagnostic evidence is tagged so it can never impersonate a scheduled worker poll.
    assert '"actor_type":"diagnostic"' in params[9]
    assert '"trigger":"manual_diagnostic"' in params[9]

    # A monitored-system heartbeat + coverage are written.
    hb = conn.find('UPDATE MONITORED_SYSTEMS')
    assert hb and hb[0][1][0] == 'healthy'  # runtime_status
    assert conn.find('INSERT INTO TARGET_COVERAGE_RECORDS')
    # The diagnostic must NOT advance targets.last_checked_at (the scheduled worker's
    # due-selection cursor) — that would delay the continuous worker's own first poll.
    assert not conn.find('UPDATE TARGETS')

    steps = {s['step']: s for s in result['steps']}
    assert steps['verify_linkage']['status'] == 'pass'
    assert steps['confirm_chain_id']['status'] == 'pass'
    assert steps['confirm_bytecode']['status'] == 'pass'
    assert steps['write_heartbeat']['status'] == 'pass'


def test_diagnose_contract_does_not_require_native_transfer(monkeypatch):
    """A contract target is proven via chain id + bytecode — no telemetry/transfer needed.

    The RecordingConn returns NO telemetry rows for anything, yet the diagnostic still
    reports healthy provider + polling evidence purely from the RPC probe.
    """
    _stub_resolve(monkeypatch)
    _stub_probe(monkeypatch, {
        'ok': True, 'latency_ms': 40, 'chain_id': 8453, 'latest_block': 8454000,
        'bytecode_present': True, 'error': None,
    })
    result = pilot._diagnose_target(_RecordingConn(), workspace_id=WS, target=_diag_target(), correlation_id='c', now=NOW)
    assert result['provider_health_status'] == 'healthy'
    assert result['evidence_source'] == 'live'


def test_diagnose_unreachable_provider_is_error_never_healthy(monkeypatch):
    _stub_resolve(monkeypatch)
    _stub_probe(monkeypatch, {
        'ok': False, 'latency_ms': None, 'chain_id': None, 'latest_block': None,
        'bytecode_present': None, 'error': 'timed out',
    })
    conn = _RecordingConn()
    result = pilot._diagnose_target(conn, workspace_id=WS, target=_diag_target(), correlation_id='c', now=NOW)
    assert result['provider_health_status'] == 'error'
    assert result['reachable'] is False
    assert result['evidence_source'] == 'none'
    ph = conn.find('INSERT INTO PROVIDER_HEALTH_RECORDS')[0][1]
    assert ph[4] == 'error'                # status
    assert ph[6] is None                   # no latency invented when call did complete? measured elapsed is fine; here probe returned None
    assert ph[8] == 'none'                 # evidence_source: never 'live' when nothing was observed
    # System heartbeat records failed runtime status (fail-closed).
    assert conn.find('UPDATE MONITORED_SYSTEMS')[0][1][0] == 'failed'


def test_diagnose_chain_mismatch_is_degraded(monkeypatch):
    _stub_resolve(monkeypatch, chain_id=8453)
    _stub_probe(monkeypatch, {
        'ok': True, 'latency_ms': 50, 'chain_id': 1, 'latest_block': 1234,  # Ethereum, not Base
        'bytecode_present': True, 'error': None,
    })
    result = pilot._diagnose_target(_RecordingConn(), workspace_id=WS, target=_diag_target(), correlation_id='c', now=NOW)
    assert result['provider_health_status'] == 'degraded'
    assert result['chain_id_ok'] is False


def test_diagnose_reports_missing_linkage(monkeypatch):
    _stub_resolve(monkeypatch)
    _stub_probe(monkeypatch, {
        'ok': True, 'latency_ms': 50, 'chain_id': 8453, 'latest_block': 1, 'bytecode_present': True, 'error': None,
    })
    target = _diag_target(linked_asset_id=None, monitoring_config_id=None)
    result = pilot._diagnose_target(_RecordingConn(), workspace_id=WS, target=target, correlation_id='c', now=NOW)
    linkage = next(s for s in result['steps'] if s['step'] == 'verify_linkage')
    assert linkage['status'] == 'fail'
    assert 'asset_link_missing' in linkage['gaps']
    assert 'monitoring_config_missing' in linkage['gaps']


def test_diagnose_writes_workspace_scoped_evidence(monkeypatch):
    """Every persisted row carries the diagnostic's workspace_id (no cross-tenant write)."""
    _stub_resolve(monkeypatch)
    _stub_probe(monkeypatch, {
        'ok': True, 'latency_ms': 30, 'chain_id': 8453, 'latest_block': 7, 'bytecode_present': True, 'error': None,
    })
    conn = _RecordingConn()
    pilot._diagnose_target(conn, workspace_id=WS, target=_diag_target(), correlation_id='c', now=NOW)
    ph = conn.find('INSERT INTO PROVIDER_HEALTH_RECORDS')[0][1]
    assert ph[1] == WS
    cov = conn.find('INSERT INTO TARGET_COVERAGE_RECORDS')[0][1]
    assert WS in cov


# ---------------------------------------------------------------------------
# 5. Idempotency + orchestration.
# ---------------------------------------------------------------------------
def test_advisory_lock_helper_reports_locked_and_free():
    class _Locked:
        def execute(self, query, params=None):
            return _Result([{'locked': False}])

    class _Free:
        def execute(self, query, params=None):
            return _Result([{'locked': True}])

    assert pilot._try_acquire_diagnostic_lock(_Locked(), workspace_id=WS) is False
    assert pilot._try_acquire_diagnostic_lock(_Free(), workspace_id=WS) is True


def _wire_diagnostic(monkeypatch, conn, *, targets, diag_result=None):
    import contextlib
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda c: None)
    monkeypatch.setattr(pilot, 'log_audit', lambda *a, **k: None)
    monkeypatch.setattr(pilot, '_publish_source_event', lambda *a, **k: None)
    monkeypatch.setattr(
        pilot, 'require_ops_rbac_guard',
        lambda c, r: ({'id': 'user-1'}, {'workspace_id': WS}),
    )
    monkeypatch.setattr(pilot, '_load_diagnostic_targets', lambda c, *, workspace_id, target_id: targets)
    if diag_result is not None:
        monkeypatch.setattr(
            pilot, '_diagnose_target',
            lambda c, *, workspace_id, target, correlation_id, now: {**diag_result, 'target_id': target.get('id')},
        )

    @contextlib.contextmanager
    def _fake_pg():
        yield conn

    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)


class _LockConn(_RecordingConn):
    def __init__(self, *, locked_free=True):
        super().__init__()
        self._locked_free = locked_free
        self.autocommit = False

    def execute(self, query, params=None):
        q = ' '.join(str(query).split()).upper()
        self.calls.append((q, tuple(params or ())))
        if 'PG_TRY_ADVISORY_LOCK' in q:
            return _Result([{'locked': self._locked_free}])
        return _Result([])


def test_run_source_diagnostic_blocks_duplicate_concurrent_run(monkeypatch):
    conn = _LockConn(locked_free=False)  # lock already held by another run
    _wire_diagnostic(monkeypatch, conn, targets=[_diag_target()], diag_result={'provider_health_status': 'healthy', 'reachable': True})
    with pytest.raises(pilot.HTTPException) as exc:
        pilot.run_source_diagnostic(object(), {})
    assert exc.value.status_code == 409


def test_run_source_diagnostic_happy_path_returns_structured_result(monkeypatch):
    conn = _LockConn(locked_free=True)
    _wire_diagnostic(
        monkeypatch, conn, targets=[_diag_target()],
        diag_result={'provider_health_status': 'healthy', 'reachable': True, 'name': 'USDC monitor'},
    )
    out = pilot.run_source_diagnostic(object(), {})
    assert out['correlation_id']
    assert out['summary']['targets_evaluated'] == 1
    assert out['summary']['healthy'] == 1
    assert len(out['results']) == 1
    # The advisory lock is released...
    assert conn.find('PG_ADVISORY_UNLOCK')
    # ...but the diagnostic must NOT write the continuous-worker heartbeat, so a manual
    # Run Diagnostic can never make the scheduled worker appear alive (Section 4).
    assert not conn.find('INSERT INTO MONITORING_HEARTBEATS')


def test_run_source_diagnostic_single_target_not_found(monkeypatch):
    conn = _LockConn(locked_free=True)
    _wire_diagnostic(monkeypatch, conn, targets=[], diag_result={'provider_health_status': 'healthy', 'reachable': True})
    with pytest.raises(pilot.HTTPException) as exc:
        pilot.run_source_diagnostic(object(), {'target_id': TARGET_ID})
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# 6. Canonical provider-health persistence (shared scheduled-worker + diagnostic).
#    The scheduled worker previously wrote latency_ms=None; this helper records the
#    REAL measured latency, provider host, chain/block, and consecutive streak.
# ---------------------------------------------------------------------------
class _HelperConn:
    def __init__(self, prev_metadata=None):
        self.inserts: list[tuple] = []
        self._prev = prev_metadata

    def execute(self, query, params=None):
        q = ' '.join(str(query).split()).upper()
        if q.startswith('SELECT') and 'FROM PROVIDER_HEALTH_RECORDS' in q:
            return _Result([{'metadata': self._prev}] if self._prev is not None else [])
        if q.startswith('INSERT INTO PROVIDER_HEALTH_RECORDS'):
            self.inserts.append(tuple(params or ()))
        return _Result([])


def test_persist_provider_health_records_real_latency_and_worker_tag():
    import json
    conn = _HelperConn()
    rid = pilot.persist_provider_health_evidence(
        conn, workspace_id=WS, target_id=TARGET_ID, provider_host=ALCHEMY,
        status='healthy', success=True, latency_ms=84, chain_id=8453, latest_block=8454321,
        evidence_source='live', actor_type='worker', trigger='scheduled_poll',
    )
    assert rid
    # (id, workspace_id, host, target_id, status, checked_at, latency_ms, error, evidence_source, metadata)
    p = conn.inserts[0]
    assert p[1] == WS and p[2] == ALCHEMY and p[3] == TARGET_ID
    assert p[4] == 'healthy'
    assert p[6] == 84                       # REAL measured latency (not None as before)
    assert p[8] == 'live'
    meta = json.loads(p[9])
    assert meta['actor_type'] == 'worker' and meta['trigger'] == 'scheduled_poll'
    assert meta['chain_id'] == 8453 and meta['latest_block'] == 8454321
    assert meta['consecutive_success'] == 1 and meta['consecutive_failure'] == 0


def test_persist_provider_health_latency_none_when_no_rpc():
    """No RPC executed (backoff/mismatch) => latency must stay None, never invented."""
    conn = _HelperConn()
    pilot.persist_provider_health_evidence(
        conn, workspace_id=WS, target_id=TARGET_ID, provider_host=ALCHEMY,
        status='degraded', success=False, latency_ms=None, evidence_source='none',
        error_category='provider_backoff_active',
    )
    assert conn.inserts[0][6] is None


def test_persist_provider_health_consecutive_streak_increments():
    import json
    conn = _HelperConn(prev_metadata={'consecutive_success': 3, 'consecutive_failure': 0})
    pilot.persist_provider_health_evidence(
        conn, workspace_id=WS, target_id=TARGET_ID, provider_host=ALCHEMY,
        status='healthy', success=True, latency_ms=50, evidence_source='live',
    )
    assert json.loads(conn.inserts[0][9])['consecutive_success'] == 4


def test_persist_provider_health_failure_resets_success_streak():
    import json
    conn = _HelperConn(prev_metadata={'consecutive_success': 5, 'consecutive_failure': 0})
    pilot.persist_provider_health_evidence(
        conn, workspace_id=WS, target_id=TARGET_ID, provider_host=ALCHEMY,
        status='error', success=False, latency_ms=1200, evidence_source='none', error_category='timeout',
    )
    meta = json.loads(conn.inserts[0][9])
    assert meta['consecutive_success'] == 0 and meta['consecutive_failure'] == 1


def test_p95_exactly_min_samples_produces_value():
    samples = [float(i) for i in range(1, pilot._P95_MIN_SAMPLES + 1)]  # exactly the floor
    assert pilot._p95_from_samples(samples) is not None
    assert pilot._p95_from_samples([float(i) for i in range(pilot._P95_MIN_SAMPLES - 1)]) is None


# ---------------------------------------------------------------------------
# 7. Newly activated target is immediately due for its first scheduled poll.
#    (Root rule: last_checked_at IS NULL => due now; nothing sets an inherited stamp.)
# ---------------------------------------------------------------------------
def test_newly_activated_target_is_immediately_due():
    """The worker's canonical due rule: a never-polled target (last_checked_at IS NULL) is
    due on the FIRST cycle; a recently-checked one waits its interval; a stale one is due."""
    from datetime import timedelta
    from services.api.app.monitoring_runner import _target_selected_for_live_poll
    now = NOW
    # Newly activated USDC target — never polled — must be due immediately.
    assert _target_selected_for_live_poll(None, 60, now) is True
    # Just polled 10s ago on a 60s interval — not yet due.
    assert _target_selected_for_live_poll(now - timedelta(seconds=10), 60, now) is False
    # Last polled 5 minutes ago (or an old/inherited stamp) — due again.
    assert _target_selected_for_live_poll(now - timedelta(seconds=300), 60, now) is True
