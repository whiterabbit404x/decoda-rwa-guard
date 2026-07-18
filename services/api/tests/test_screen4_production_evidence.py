"""Screen 4 production-evidence truthfulness (2026-07-18 Rabbit + Datto RPC outage).

The latest scheduled poll for both workspaces FAILED (all_rpc_providers_unavailable:
QuickNode TLS alert, Alchemy HTTP 429). The canonical runtime truth is
chosen_evidence_source=replay / no_fresh_coverage_telemetry / monitoring_status=degraded.

Screen 4 must therefore, during the outage:
  * count fresh LIVE telemetry coverage as 0/1 (0.0%) — replay/historical evidence and a
    stale coverage timestamp never count as live coverage, even when the monitored
    system row still says freshness_status='fresh';
  * still report CONFIGURED coverage as 1/1 and HISTORICAL evidence as available;
  * show the row status as "Provider Unavailable" with reason rpc_providers_unavailable,
    NEVER "awaiting poll" (a completed failed poll already ran);
  * show P95 as "no successful samples" (a failed call's elapsed time never enters P95),
    and label a 20+-sample P95 as HISTORICAL when the provider is failing now.

These map to task tests 1–9, 13–15. Everything is derived from canonical records via a
lightweight fake connection (no live database).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from services.api.app import monitoring_health_engine as he
from services.api.app import pilot

# The failed Rabbit scheduled poll timestamp from production evidence.
NOW = datetime(2026, 7, 18, 13, 50, 54, tzinfo=timezone.utc)
# Last coverage telemetry is ~21h old (replay) — far outside the 900s freshness window.
STALE_TELEMETRY_AT = datetime(2026, 7, 17, 16, 22, 35, tzinfo=timezone.utc)

RABBIT_WS = '1155f479-3e5b-4d90-be6c-fd6c1d6b957d'
RABBIT_TARGET = 'e7851a52-8fb1-48cd-84a3-d033f591c5dd'
DATTO_WS = '4fffd3f9-d55f-456f-8a7e-8b9ed2083721'
DATTO_TARGET = '9c6ecabb-cd52-40859-4859-40567b09dbb4'  # shape only; scoping is what matters
QUICKNODE_HOST = 'base-mainnet.quiknode.pro'


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _EnrichConn:
    """Honors the P95 status='healthy' filter like a real DB, and records the
    workspace_id bound to every query so scoping can be asserted."""

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
        rows: list = []
        if 'from provider_health_records' in q and "status = 'healthy'" in q:
            rows = [r for r in self.latency_rows if str(r.get('status')) == 'healthy']
        elif 'from provider_health_records' in q:
            rows = self.health_rows
        elif 'from target_coverage_records' in q:
            rows = self.coverage_rows
        elif 'from monitor_checkpoint' in q:
            rows = self.block_rows
        return _Result(rows)

    def commit(self):
        pass


def _target(workspace_id=RABBIT_WS, target_id=RABBIT_TARGET, **over):
    base = {
        'id': target_id, 'name': 'Rabbit Base USDC', 'asset_id': 'asset-r', 'asset_name': 'USDC',
        'chain_network': 'base', 'chain_id': 8453,
        'contract_identifier': '0x833589fcd6edb6e08f4c7c32d4f71b54bda02913',
        'target_type': 'contract', 'monitoring_mode': 'recommended',
        'enabled': True, 'monitoring_enabled': True, 'asset_missing': False,
        'target_metadata': {'rpc_sources': {'primary_host': QUICKNODE_HOST,
                                             'fallback_host': 'base-mainnet.g.alchemy.com'}},
    }
    base.update(over)
    return base


def _system(**over):
    # Worker heartbeat is FRESH (worker alive) and the loop is idle between polls, but
    # the system row still (wrongly) carries freshness_status='fresh' from replay data.
    base = {
        'id': 'sys-r', 'target_id': RABBIT_TARGET, 'asset_name': 'USDC', 'target_name': 'Rabbit Base USDC',
        'is_enabled': True, 'runtime_status': 'idle',
        'last_heartbeat': NOW.isoformat(),
        'last_event_at': STALE_TELEMETRY_AT.isoformat(),
        'freshness_status': 'fresh', 'coverage_reason': 'awaiting_poll',
    }
    base.update(over)
    return base


def _failed_health(**over):
    base = {
        'target_id': RABBIT_TARGET, 'status': 'error', 'latency_ms': 21,
        'checked_at': NOW, 'evidence_source': 'live', 'provider_type': QUICKNODE_HOST,
        'error_message': 'all_rpc_providers_unavailable', 'metadata': None,
    }
    base.update(over)
    return base


def _replay_coverage(**over):
    base = {
        'target_id': RABBIT_TARGET, 'coverage_status': 'stale',
        'last_poll_at': NOW, 'last_heartbeat_at': NOW, 'last_telemetry_at': STALE_TELEMETRY_AT,
        'last_detection_at': None, 'evidence_source': 'replay', 'computed_at': NOW,
    }
    base.update(over)
    return base


def _enrich(conn, targets=None, systems=None, workspace_id=RABBIT_WS):
    return pilot._build_monitoring_sources_enrichment(
        conn, workspace_id=workspace_id, assets=[{'id': 'asset-r'}],
        targets=targets or [_target()], systems=systems or [_system()], now=NOW,
    )


# ===========================================================================
# 1–2. Replay / historical evidence never counts as fresh LIVE coverage.
# ===========================================================================
def test_replay_evidence_is_not_fresh_live_coverage():
    conn = _EnrichConn(health_rows=[_failed_health()], coverage_rows=[_replay_coverage()])
    tc = _enrich(conn)['summary']['telemetry_coverage']
    assert tc['configured'] == 1, 'the target is still configured'
    assert tc['live_fresh'] == 0, 'replay evidence must not count as live'
    assert tc['live_coverage_pct'] == 0.0
    assert tc['coverage_pct'] == 0.0, 'legacy coverage_pct now carries live semantics'
    assert tc['fresh'] == 0
    assert tc['replay_only'] == 1
    assert tc['historical_available'] is True


def test_historical_reporting_does_not_make_live_coverage_100():
    """A system row that still says freshness_status='fresh' from old replay data must
    not inflate live coverage — the coverage timestamp is 21h stale and evidence=replay."""
    conn = _EnrichConn(health_rows=[_failed_health()], coverage_rows=[_replay_coverage()])
    tc = _enrich(conn)['summary']['telemetry_coverage']
    assert tc['coverage_pct'] != 100.0
    assert tc['live_coverage_pct'] == 0.0


def test_stale_live_coverage_record_is_not_fresh_live_coverage():
    """The realistic production case: the last coverage record is 'live' but ~21h old
    (no new coverage persisted for the failed poll) and the latest poll failed. A stale
    live record must NOT count as fresh live coverage."""
    stale_live = _replay_coverage(evidence_source='live')  # live source but stale timestamp
    conn = _EnrichConn(health_rows=[_failed_health()], coverage_rows=[stale_live])
    tc = _enrich(conn)['summary']['telemetry_coverage']
    assert tc['live_fresh'] == 0
    assert tc['live_coverage_pct'] == 0.0
    assert tc['configured'] == 1
    assert tc['historical_available'] is True


def test_fresh_worker_heartbeat_alone_does_not_create_live_coverage():
    """Worker heartbeat is fresh but no live provider evidence + no coverage record =>
    0 live coverage (heartbeat proves the worker is alive, not that telemetry arrived)."""
    conn = _EnrichConn(health_rows=[], coverage_rows=[])
    tc = _enrich(conn)['summary']['telemetry_coverage']
    assert tc['configured'] == 1
    assert tc['live_fresh'] == 0
    assert tc['live_coverage_pct'] == 0.0


# ===========================================================================
# 3–4. Completed failed poll => Provider Unavailable, never "awaiting poll".
# ===========================================================================
def test_failed_completed_poll_is_provider_unavailable_not_awaiting_poll():
    conn = _EnrichConn(health_rows=[_failed_health()], coverage_rows=[_replay_coverage()])
    src = _enrich(conn)['sources'][0]
    assert src['status'] == pilot._SOURCE_STATUS_PROVIDER_UNAVAILABLE
    assert src['status'] != pilot._SOURCE_STATUS_WARNING
    assert src['status_reason'] == 'rpc_providers_unavailable'
    assert 'awaiting' not in str(src['status_reason'])


def test_latest_provider_failure_controls_status_reason():
    """Even with a fresh heartbeat and an idle loop, the LATEST failed provider
    observation's error controls the status reason."""
    conn = _EnrichConn(
        health_rows=[_failed_health(error_message='all_rpc_providers_unavailable')],
        coverage_rows=[_replay_coverage()],
    )
    src = _enrich(conn)['sources'][0]
    assert src['status_reason'] == 'rpc_providers_unavailable'
    # Source Health card reads 0/1 with a health score of 0 (hard failure precedence).
    summary = _enrich(_EnrichConn(health_rows=[_failed_health()], coverage_rows=[_replay_coverage()]))['summary']
    assert summary['source_health']['healthy'] == 0
    assert src['health_score'] is not None and src['health_score'] <= 20


def test_no_provider_observation_ever_is_awaiting_first_poll():
    """With NO provider record at all, an idle worker with a heartbeat is provisioning /
    awaiting first poll — distinct from a completed failed poll."""
    conn = _EnrichConn(health_rows=[], coverage_rows=[])
    src = _enrich(conn)['sources'][0]
    assert src['status'] == pilot._SOURCE_STATUS_WARNING
    assert src['status_reason'] in {'awaiting_poll', 'awaiting_first_poll', 'awaiting_next_poll'}


# ===========================================================================
# 5–8. P95: failed elapsed excluded, zero-sample + historical labelling.
# ===========================================================================
def test_failed_latency_does_not_enter_p95_zero_samples():
    conn = _EnrichConn(
        health_rows=[_failed_health(latency_ms=21)],
        latency_rows=[_failed_health(latency_ms=21) for _ in range(30)],  # all failed
        coverage_rows=[_replay_coverage()],
    )
    src = _enrich(conn)['sources'][0]
    assert src['p95_status'] == 'no_successful_samples'
    assert src['p95_sample_count'] == 0
    assert src['p95_latency_ms'] is None
    assert src['p95_is_historical'] is False


def test_historical_successful_p95_is_labelled_historical():
    """20+ OLD successful samples (yesterday) while the provider is failing NOW => the
    P95 (e.g. 23,912 ms) is available but flagged historical, not current health."""
    old = STALE_TELEMETRY_AT
    healthy_old = [
        {'target_id': RABBIT_TARGET, 'status': 'healthy', 'latency_ms': 23900 + i,
         'checked_at': old, 'evidence_source': 'live', 'provider_type': QUICKNODE_HOST,
         'error_message': None}
        for i in range(25)
    ]
    conn = _EnrichConn(
        health_rows=[_failed_health()],           # latest observation FAILED
        latency_rows=healthy_old,                 # 25 old successful samples
        coverage_rows=[_replay_coverage()],
    )
    src = _enrich(conn)['sources'][0]
    assert src['p95_status'] == 'available'
    assert src['p95_sample_count'] == 25
    assert src['p95_latency_ms'] is not None
    assert src['p95_is_historical'] is True
    assert src['p95_last_sample_at'] is not None


def test_recent_successful_p95_is_not_historical():
    healthy_now = [
        {'target_id': RABBIT_TARGET, 'status': 'healthy', 'latency_ms': 120 + i,
         'checked_at': NOW, 'evidence_source': 'live', 'provider_type': QUICKNODE_HOST,
         'error_message': None}
        for i in range(25)
    ]
    conn = _EnrichConn(
        health_rows=[healthy_now[0]], latency_rows=healthy_now,
        coverage_rows=[{'target_id': RABBIT_TARGET, 'coverage_status': 'reporting',
                        'last_poll_at': NOW, 'last_heartbeat_at': NOW, 'last_telemetry_at': NOW,
                        'last_detection_at': None, 'evidence_source': 'live', 'computed_at': NOW}],
    )
    src = _enrich(conn, systems=[_system(runtime_status='healthy', last_event_at=NOW.isoformat())])['sources'][0]
    assert src['p95_status'] == 'available'
    assert src['p95_is_historical'] is False


# ===========================================================================
# 9. QuickNode STREAM and QuickNode RPC are separate: a failed RPC poll headline
#    is not rescued by a stream record, and the RPC host is the configured provider.
# ===========================================================================
def test_quicknode_rpc_failure_is_the_headline_not_the_stream():
    src = _enrich(
        _EnrichConn(health_rows=[_failed_health()], coverage_rows=[_replay_coverage()])
    )['sources'][0]
    # The configured/primary provider is the QuickNode RPC host; its failed observation
    # drives the row — the stream webhook is a separate route type, not RPC verification.
    assert src['primary_provider'] == QUICKNODE_HOST
    assert src['status'] == pilot._SOURCE_STATUS_PROVIDER_UNAVAILABLE


# ===========================================================================
# 13. Workspace scoping: every canonical query is bound to the caller's workspace.
# ===========================================================================
def test_rabbit_and_datto_calculations_are_workspace_scoped():
    rabbit_conn = _EnrichConn(health_rows=[_failed_health()], coverage_rows=[_replay_coverage()])
    _enrich(rabbit_conn, workspace_id=RABBIT_WS)
    assert rabbit_conn.workspace_params, 'queries must bind a workspace_id'
    assert all(p == RABBIT_WS for p in rabbit_conn.workspace_params), rabbit_conn.workspace_params

    datto_conn = _EnrichConn(health_rows=[], coverage_rows=[])
    _enrich(
        datto_conn,
        targets=[_target(workspace_id=DATTO_WS, target_id=DATTO_TARGET, id=DATTO_TARGET, name='Datto USDC')],
        systems=[_system(target_id=DATTO_TARGET)],
        workspace_id=DATTO_WS,
    )
    assert all(p == DATTO_WS for p in datto_conn.workspace_params), datto_conn.workspace_params


# ===========================================================================
# 14–15. Recovery: first success after failure => Recovering; second => Healthy.
# ===========================================================================
def _recovering_health(recovery_state, consecutive):
    return {
        'target_id': RABBIT_TARGET, 'status': 'healthy', 'latency_ms': 130,
        'checked_at': NOW, 'evidence_source': 'live', 'provider_type': QUICKNODE_HOST,
        'error_message': None,
        'metadata': {
            'recovery_state': recovery_state,
            'consecutive_success': consecutive,
            'required_consecutive_success': 2,
            'provider_host': QUICKNODE_HOST,
            'last_successful_block': 8453200 + consecutive,
            'last_successful_latency_ms': 130,
        },
    }


def test_first_successful_recovery_poll_is_recovering():
    live_cov = {'target_id': RABBIT_TARGET, 'coverage_status': 'reporting',
                'last_poll_at': NOW, 'last_heartbeat_at': NOW, 'last_telemetry_at': NOW,
                'last_detection_at': None, 'evidence_source': 'live', 'computed_at': NOW}
    conn = _EnrichConn(
        health_rows=[_recovering_health(he.RECOVERY_RECOVERING, 1)],
        latency_rows=[_recovering_health(he.RECOVERY_RECOVERING, 1)],
        coverage_rows=[live_cov],
    )
    src = _enrich(conn, systems=[_system(runtime_status='healthy', last_event_at=NOW.isoformat())])['sources'][0]
    assert src['status'] == pilot._SOURCE_STATUS_RECOVERING
    assert src['recovery_state'] == he.RECOVERY_RECOVERING


def test_second_successful_recovery_poll_is_healthy():
    live_cov = {'target_id': RABBIT_TARGET, 'coverage_status': 'reporting',
                'last_poll_at': NOW, 'last_heartbeat_at': NOW, 'last_telemetry_at': NOW,
                'last_detection_at': None, 'evidence_source': 'live', 'computed_at': NOW}
    conn = _EnrichConn(
        health_rows=[_recovering_health(he.RECOVERY_HEALTHY, 2)],
        latency_rows=[_recovering_health(he.RECOVERY_HEALTHY, 2)],
        coverage_rows=[live_cov],
    )
    src = _enrich(conn, systems=[_system(runtime_status='healthy', last_event_at=NOW.isoformat())])['sources'][0]
    assert src['status'] == pilot._SOURCE_STATUS_HEALTHY
    # Second consecutive success => live coverage returns to 1/1.
    tc = _enrich(conn, systems=[_system(runtime_status='healthy', last_event_at=NOW.isoformat())])['summary']['telemetry_coverage']
    assert tc['live_fresh'] == 1
    assert tc['live_coverage_pct'] == 100.0
