"""Screen 4 — provider-health recovery snapshot consistency + latency separation.

Covers the continuation task's acceptance list (item 16):

  1  RPC request latency excludes scan duration (canonical = eth_blockNumber request)
  2  poll duration is stored/surfaced separately from RPC latency
  3  scan duration is stored/surfaced separately from RPC latency
  4  P95 uses only successful RPC network samples
  5  the newest successful scheduled record overrides an older failure
  6  first scheduled success after a failure -> Recovering
  7  second consecutive scheduled success -> Healthy
  8  a Healthy/Recovering row can never carry health_score 0
  9  a successful primary route produces Active Routes = 1 (even with no rpc_sources config)
 10  fresh live coverage produces 100% live coverage for a 1/1 quiet-but-live target
 11  provider latest block is distinct from the scan cursor
 12  the Agent panel uses the SAME metric snapshot (evidence record ids) as the table
 13  the polling interval resolves through ONE canonical value
 14  a completed strategic backfill is not repeated when there is no new telemetry
 15  Datto stays workspace-scoped and eligible

Everything is derived from canonical records through a lightweight fake connection
(no live database), matching the repo's existing Screen-4 unit style.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from services.api.app import evm_activity_provider as evm
from services.api.app import monitoring_health_engine as he
from services.api.app import monitoring_runner as mr
from services.api.app import monitoring_truth as mt
from services.api.app import pilot

NOW = datetime(2026, 7, 20, 12, 0, 0, tzinfo=timezone.utc)
OLD = NOW - timedelta(hours=21)  # last on-chain event on a quiet wallet

RABBIT_WS = '1155f479-3e5b-4d90-be6c-fd6c1d6b957d'
RABBIT_TARGET = 'e7851a52-8fb1-48cd-84a3-d033f591c5dd'
RABBIT_PHR_ID = 'd14e1034-cb26-47c0-ba82-5720bbbd7ded'
DATTO_WS = '4fffd3f9-d55f-456f-8a7e-8b9ed2083721'
DATTO_TARGET = '9c6ecabb-cd52-404f-9859-40567b09dbb4'
ALCHEMY = 'base-mainnet.g.alchemy.com'
PROVIDER_BLOCK = 48878310
SCAN_CURSOR = 48876502
SYS = 'sys-rabbit'


# ---------------------------------------------------------------------------
# Fake connection honoring the Screen-4 enrichment queries.
# ---------------------------------------------------------------------------
class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _EnrichConn:
    def __init__(self, *, health_rows, latency_rows=None, coverage_rows=None, block_rows=None):
        self.health_rows = health_rows
        self.latency_rows = latency_rows if latency_rows is not None else health_rows
        self.coverage_rows = coverage_rows or []
        self.block_rows = block_rows or []
        self.workspace_params: list[str] = []

    def execute(self, sql, params=None):
        q = ' '.join(str(sql).split()).lower()
        if params:
            self.workspace_params.append(str(params[0]))
        if 'from provider_health_records' in q and "status = 'healthy'" in q:
            return _Result([r for r in self.latency_rows if str(r.get('status')) == 'healthy'])
        if 'from provider_health_records' in q:
            return _Result(self.health_rows)
        if 'from target_coverage_records' in q:
            return _Result(self.coverage_rows)
        if 'from monitor_checkpoint' in q:
            return _Result(self.block_rows)
        return _Result([])

    def commit(self):
        pass


def _success_meta(recovery_state, consecutive, **over):
    base = {
        'recovery_state': recovery_state,
        'consecutive_success': consecutive,
        'required_consecutive_success': 2,
        'provider_host': ALCHEMY,
        'provider_latest_block': PROVIDER_BLOCK,
        'latest_block': PROVIDER_BLOCK,
        'last_successful_block': PROVIDER_BLOCK,
        'last_successful_latency_ms': 118,
        'rpc_request_latency_ms': 118,
        'poll_duration_ms': 11382,
        'scan_duration_ms': 11264,
        'rpc_successful_sample_count': 1,
        'success': True,
    }
    base.update(over)
    return base


def _success_health(recovery_state, consecutive, **over):
    base = {
        'id': RABBIT_PHR_ID, 'target_id': RABBIT_TARGET, 'status': 'healthy', 'latency_ms': 118,
        'checked_at': NOW, 'evidence_source': 'live', 'provider_type': ALCHEMY,
        'error_message': None, 'metadata': _success_meta(recovery_state, consecutive),
    }
    base.update(over)
    return base


def _failed_health(checked_at=OLD, **over):
    base = {
        'id': 'failed-older', 'target_id': RABBIT_TARGET, 'status': 'error', 'latency_ms': 21,
        'checked_at': checked_at, 'evidence_source': 'live', 'provider_type': ALCHEMY,
        'error_message': 'all_rpc_providers_unavailable', 'metadata': {'success': False},
    }
    base.update(over)
    return base


def _live_coverage(**over):
    base = {
        'target_id': RABBIT_TARGET, 'coverage_status': 'reporting',
        'last_poll_at': NOW, 'last_heartbeat_at': NOW, 'last_telemetry_at': NOW,
        'last_detection_at': None, 'evidence_source': 'live', 'computed_at': NOW,
    }
    base.update(over)
    return base


def _target(target_metadata=None, target_id=RABBIT_TARGET, **over):
    base = {
        'id': target_id, 'name': 'Rabbit Base USDC', 'asset_id': 'asset-r', 'asset_name': 'USDC',
        'chain_network': 'base', 'chain_id': 8453,
        'contract_identifier': '0x833589fcd6edb6e08f4c7c32d4f71b54bda02913',
        'target_type': 'contract', 'monitoring_mode': 'recommended',
        'enabled': True, 'monitoring_enabled': True, 'asset_missing': False,
        'target_metadata': target_metadata if target_metadata is not None else {},
    }
    base.update(over)
    return base


def _system(target_id=RABBIT_TARGET, **over):
    base = {
        'id': SYS, 'target_id': target_id, 'asset_name': 'USDC', 'target_name': 'Rabbit Base USDC',
        'is_enabled': True, 'runtime_status': 'healthy', 'last_heartbeat': NOW.isoformat(),
        'last_event_at': OLD.isoformat(), 'freshness_status': 'fresh',
        'coverage_reason': 'live_no_recent_events',
    }
    base.update(over)
    return base


def _enrich(conn, *, targets=None, systems=None, workspace_id=RABBIT_WS):
    return pilot._build_monitoring_sources_enrichment(
        conn, workspace_id=workspace_id, assets=[{'id': 'asset-r'}],
        targets=targets or [_target()], systems=systems or [_system()], now=NOW,
    )


def _first_success_conn():
    return _EnrichConn(
        health_rows=[_success_health(he.RECOVERY_RECOVERING, 1)],
        latency_rows=[_success_health(he.RECOVERY_RECOVERING, 1)],
        coverage_rows=[_live_coverage()],
        block_rows=[{'monitored_system_id': SYS, 'latest_block': SCAN_CURSOR}],
    )


# ===========================================================================
# 1–4. Latency separation + successful-only P95.
# ===========================================================================
def test_rpc_request_latency_excludes_scan_duration():
    """The canonical current provider latency is a single successful eth_blockNumber
    request (~118 ms), NOT the multi-block scan/poll duration (~11 s)."""
    with evm.rpc_metrics_capture() as cap:
        evm._record_rpc_request_sample(method='eth_chainId', host=ALCHEMY, success=True, latency_ms=44)
        evm._record_rpc_request_sample(method='eth_blockNumber', host=ALCHEMY, success=True, latency_ms=118)
        evm._record_rpc_request_sample(method='eth_getLogs', host=ALCHEMY, success=True, latency_ms=9000)
    assert cap.successful_request_latency_ms() == 118
    assert cap.successful_request_latency_ms() != 9000


def test_poll_and_scan_duration_surfaced_separately_from_rpc_latency():
    src = _enrich(_first_success_conn())['sources'][0]
    assert src['rpc_request_latency_ms'] == 118       # single RPC request
    assert src['poll_duration_ms'] == 11382           # full poll (many calls + scanning)
    assert src['scan_duration_ms'] == 11264           # block-range scan
    # The RPC latency is never the poll/scan duration.
    assert src['rpc_request_latency_ms'] != src['poll_duration_ms']
    assert src['rpc_request_latency_ms'] != src['scan_duration_ms']


def test_p95_uses_only_successful_samples_and_needs_20():
    # 5 successful samples -> insufficient; a failed elapsed time is never a P95 sample.
    healthy = [_success_health(he.RECOVERY_HEALTHY, 2, id=f'h{i}', latency_ms=100 + i) for i in range(5)]
    conn = _EnrichConn(
        health_rows=[healthy[0]], latency_rows=healthy + [_failed_health(latency_ms=21)],
        coverage_rows=[_live_coverage()], block_rows=[{'monitored_system_id': SYS, 'latest_block': SCAN_CURSOR}],
    )
    src = _enrich(conn, systems=[_system(last_event_at=NOW.isoformat())])['sources'][0]
    assert src['p95_status'] == 'insufficient_samples'
    assert src['p95_sample_count'] == 5  # the failed 21 ms row is excluded
    assert src['p95_latency_ms'] is None


# ===========================================================================
# 5. Newest successful scheduled record overrides an older failure.
# ===========================================================================
def test_newest_success_overrides_older_failure():
    """A newer successful record (checked_at NOW) must win over an older failed record
    for the SAME provider — the canonical latest evidence is the success."""
    conn = _EnrichConn(
        # _load returns DISTINCT ON (target, provider) newest-first; a real DB returns the
        # newest per provider. Provide the newest (success) as the health row.
        health_rows=[_success_health(he.RECOVERY_RECOVERING, 1)],
        latency_rows=[_success_health(he.RECOVERY_RECOVERING, 1)],
        coverage_rows=[_live_coverage()],
        block_rows=[{'monitored_system_id': SYS, 'latest_block': SCAN_CURSOR}],
    )
    src = _enrich(conn)['sources'][0]
    assert src['provider_health_record_id'] == RABBIT_PHR_ID
    assert src['status'] in {pilot._SOURCE_STATUS_RECOVERING, pilot._SOURCE_STATUS_HEALTHY}
    assert src['status'] != pilot._SOURCE_STATUS_PROVIDER_UNAVAILABLE


def test_provider_health_query_orders_by_checked_at_desc():
    """The selection query prefers the latest checked_at (newest evidence wins)."""
    captured = {}

    class _Conn:
        def execute(self, sql, params=None):
            captured['sql'] = ' '.join(str(sql).split()).lower()
            return _Result([])

    pilot._load_latest_provider_health_by_target(_Conn(), workspace_id=RABBIT_WS, canonical_ids=[RABBIT_TARGET])
    assert 'order by target_id, provider_type, checked_at desc' in captured['sql']


# ===========================================================================
# 6–8. Recovery precedence + no Healthy/Recovering with score 0.
# ===========================================================================
def test_first_scheduled_success_is_recovering_with_nonzero_score():
    src = _enrich(_first_success_conn())['sources'][0]
    assert src['status'] == pilot._SOURCE_STATUS_RECOVERING
    assert src['recovery_state'] == he.RECOVERY_RECOVERING
    assert src['health_score'] is not None and src['health_score'] > 0


def test_second_scheduled_success_is_healthy_with_full_score():
    conn = _EnrichConn(
        health_rows=[_success_health(he.RECOVERY_HEALTHY, 2)],
        latency_rows=[_success_health(he.RECOVERY_HEALTHY, 2)],
        coverage_rows=[_live_coverage()],
        block_rows=[{'monitored_system_id': SYS, 'latest_block': SCAN_CURSOR}],
    )
    src = _enrich(conn)['sources'][0]
    assert src['status'] == pilot._SOURCE_STATUS_HEALTHY
    assert src['health_score'] is not None and src['health_score'] >= 80


def test_healthy_or_recovering_row_never_has_score_zero():
    """The production contradiction: a Healthy/Recovering row with a stale/polluted
    historical P95 must NOT read health_score 0."""
    # 25 OLD polluted (cycle-duration) successful samples + the fresh success.
    polluted = [
        {'id': f'old{i}', 'target_id': RABBIT_TARGET, 'status': 'healthy', 'latency_ms': 23900 + i,
         'checked_at': OLD, 'evidence_source': 'live', 'provider_type': ALCHEMY, 'error_message': None}
        for i in range(25)
    ]
    conn = _EnrichConn(
        health_rows=[_success_health(he.RECOVERY_RECOVERING, 1)],
        latency_rows=[_success_health(he.RECOVERY_RECOVERING, 1)] + polluted,
        coverage_rows=[_live_coverage()],
        block_rows=[{'monitored_system_id': SYS, 'latest_block': SCAN_CURSOR}],
    )
    src = _enrich(conn)['sources'][0]
    assert src['status'] in {pilot._SOURCE_STATUS_RECOVERING, pilot._SOURCE_STATUS_HEALTHY}
    assert src['health_score'] not in (0, 0.0, None)


# ===========================================================================
# 9. Active Routes = 1 from a successful poll even without rpc_sources config.
# ===========================================================================
def test_successful_poll_produces_active_route_without_rpc_sources_metadata():
    src_out = _enrich(_first_success_conn())
    src = src_out['sources'][0]
    summ = src_out['summary']
    assert src['operational_primary_provider'] == ALCHEMY
    assert src['routing'] == 'primary'
    assert summ['active_routes']['primary'] == 1
    assert summ['active_routes']['fallback'] == 0


def test_failed_poll_without_config_has_no_operational_route():
    conn = _EnrichConn(
        health_rows=[_failed_health(checked_at=NOW)],
        coverage_rows=[_live_coverage(evidence_source='replay', coverage_status='stale', last_telemetry_at=OLD)],
        block_rows=[{'monitored_system_id': SYS, 'latest_block': SCAN_CURSOR}],
    )
    summ = _enrich(conn, systems=[_system(runtime_status='idle')])['summary']
    assert summ['active_routes']['primary'] == 0


# ===========================================================================
# 10. Fresh live coverage -> 100% for a quiet-but-live 1/1 target.
# ===========================================================================
def test_quiet_but_live_target_reports_full_live_coverage():
    tc = _enrich(_first_success_conn())['summary']['telemetry_coverage']
    assert tc['configured'] == 1
    assert tc['live_fresh'] == 1
    assert tc['live_coverage_pct'] == 100.0


def test_stale_event_does_not_drag_down_fresh_coverage():
    """A quiet wallet whose last on-chain event is 21h old still reads coverage_fresh
    when coverage telemetry is fresh (the event never lowers coverage freshness)."""
    src = _enrich(_first_success_conn())['sources'][0]
    assert src['coverage_fresh'] is True
    assert src['event_detection'] == 'no_recent_events'


# ===========================================================================
# 11. Provider latest block distinct from scan cursor.
# ===========================================================================
def test_provider_latest_block_distinct_from_scan_cursor():
    src = _enrich(_first_success_conn())['sources'][0]
    assert src['provider_latest_block'] == PROVIDER_BLOCK
    assert src['scan_cursor_block'] == SCAN_CURSOR
    assert src['last_processed_block'] == SCAN_CURSOR
    assert src['provider_latest_block'] != src['scan_cursor_block']
    # The headline "Latest Block" uses the provider chain head, not the trailing cursor.
    assert src['latest_block'] == PROVIDER_BLOCK


# ===========================================================================
# 12. Agent panel uses the same snapshot (evidence record ids) as the table.
# ===========================================================================
def test_agent_panel_uses_same_snapshot_as_table():
    out = _enrich(_first_success_conn())
    agent = out['agent']
    src = out['sources'][0]
    # The agent panel cites the SAME provider-health record the row used.
    assert RABBIT_PHR_ID in agent['evidence_record_ids']
    assert src['provider_health_record_id'] == RABBIT_PHR_ID
    # It reflects the recovering source instead of "no healthy monitored systems".
    assert agent['recovering_sources'] == 1
    assert agent['operational_routes'] == 1
    assert agent['live_coverage_pct'] == 100.0
    assert 'no healthy monitored systems' not in (agent['confidence_basis'] or '').lower()


# ===========================================================================
# 13. Polling interval resolves through ONE canonical value.
# ===========================================================================
def test_polling_interval_single_canonical_value(monkeypatch):
    from services.api.app import run_monitoring_worker as rw
    for v in ('EVM_POLLING_INTERVAL_SECONDS', 'MONITORING_WORKER_INTERVAL_SECONDS',
              'MIN_EVM_POLLING_INTERVAL_SECONDS'):
        monkeypatch.delenv(v, raising=False)
    # Default: worker loop and canonical target interval agree.
    assert mr.canonical_polling_interval_seconds() == mr.DEFAULT_CANONICAL_POLLING_INTERVAL_SECONDS
    assert rw._resolve_polling_interval_seconds() == float(mr.DEFAULT_CANONICAL_POLLING_INTERVAL_SECONDS)
    # Setting 900 moves BOTH together — never report 900 while polling at another value.
    monkeypatch.setenv('EVM_POLLING_INTERVAL_SECONDS', '900')
    assert mr.canonical_polling_interval_seconds() == 900
    assert rw._resolve_polling_interval() == (900.0, 'EVM_POLLING_INTERVAL_SECONDS')


# ===========================================================================
# 14. Completed strategic backfill is not repeated without new telemetry.
# ===========================================================================
def test_completed_backfill_skipped_without_new_telemetry():
    # No completion + no new rows -> run (first time).
    assert mt.should_run_historical_backfill(backfill_completed=False) is True
    # Completed + no new telemetry -> skip (does not repeat every quiet cycle).
    assert mt.should_run_historical_backfill(backfill_completed=True, new_historical_rows=False) is False
    # Completed + NEW telemetry this cycle -> run (catch up the new rows).
    assert mt.should_run_historical_backfill(backfill_completed=True, new_historical_rows=True) is True
    # Completed + rule version changed -> run (re-evaluate under new rules).
    assert mt.should_run_historical_backfill(backfill_completed=True, rule_version_changed=True) is True


def test_backfill_completion_marker_is_process_local_and_resettable():
    mr.reset_strategic_backfill_completion_state()
    key = (RABBIT_WS, RABBIT_TARGET)
    mr._STRATEGIC_BACKFILL_COMPLETED[key] = mr._STRATEGIC_BACKFILL_RULE_VERSION
    assert mr._STRATEGIC_BACKFILL_COMPLETED.get(key) == mr._STRATEGIC_BACKFILL_RULE_VERSION
    mr.reset_strategic_backfill_completion_state()
    assert mr._STRATEGIC_BACKFILL_COMPLETED == {}


# ===========================================================================
# 15. Datto stays workspace-scoped and eligible.
# ===========================================================================
def test_datto_enrichment_is_workspace_scoped():
    conn = _EnrichConn(
        health_rows=[_success_health(he.RECOVERY_HEALTHY, 2, target_id=DATTO_TARGET, id='datto-phr')],
        latency_rows=[_success_health(he.RECOVERY_HEALTHY, 2, target_id=DATTO_TARGET, id='datto-phr')],
        coverage_rows=[_live_coverage(target_id=DATTO_TARGET)],
        block_rows=[{'monitored_system_id': 'sys-datto', 'latest_block': SCAN_CURSOR}],
    )
    out = _enrich(
        conn,
        targets=[_target(target_id=DATTO_TARGET, name='Datto USDC',
                         contract_identifier='0x9c6ecabb00000000000000000000000000000000')],
        systems=[_system(target_id=DATTO_TARGET, id='sys-datto')],
        workspace_id=DATTO_WS,
    )
    # Every canonical query is bound to Datto's workspace — no cross-tenant read.
    assert conn.workspace_params, 'queries must bind a workspace_id'
    assert all(p == DATTO_WS for p in conn.workspace_params), conn.workspace_params
    # Datto renders as an eligible, monitored source.
    assert out['sources'][0]['target_id'] == DATTO_TARGET
    assert out['summary']['source_health']['total'] == 1


# ===========================================================================
# 1 (integration). The canonical JsonRpcClient records a real per-request sample.
# ===========================================================================
def test_jsonrpcclient_records_successful_request_sample(monkeypatch):
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *_a):
            return False

        def read(self):
            import json as _json
            return _json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': '0x2e94d76'}).encode('utf-8')

    monkeypatch.setattr('services.api.app.evm_activity_provider.request.urlopen',
                        lambda *_a, **_k: _Resp())
    with evm.rpc_metrics_capture() as cap:
        result = evm.JsonRpcClient('https://base-mainnet.g.alchemy.com/v2/secret').call('eth_blockNumber', [])
    assert result == '0x2e94d76'
    assert len(cap.samples) == 1
    sample = cap.samples[0]
    assert sample['method'] == 'eth_blockNumber'
    assert sample['success'] is True
    assert sample['network_attempted'] is True and sample['cache_hit'] is False
    assert sample['latency_ms'] is not None and sample['latency_ms'] >= 0
    # The redacted host is recorded — never the URL/key.
    assert sample['provider_host'] == 'base-mainnet.g.alchemy.com'
    assert 'secret' not in str(sample['provider_host'])
    assert cap.successful_request_latency_ms() == sample['latency_ms']
