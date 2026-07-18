"""Health-score precedence over latency + successful-only P95 (Screen 4 parts 4 & 5).

Covers:
  * A hard provider failure (all providers unavailable / success=false) caps the
    deterministic health score into the critical band — a fast failure elapsed time
    (e.g. 21 ms) can NEVER produce a high, healthy-looking score.
  * P95 latency is computed ONLY from successful provider requests; a failed call's
    elapsed time is surfaced as failure_elapsed_ms, never as a healthy P95.
  * The 0 / 1..19 / 20+ successful-sample states render as
    no_successful_samples / insufficient_samples / available.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from services.api.app import monitoring_health_engine as he
from services.api.app import pilot

NOW = datetime(2026, 7, 18, 12, 0, 0, tzinfo=timezone.utc)
WORKSPACE = '11111111-1111-1111-1111-111111111111'


# ===========================================================================
# Part 4 — engine: hard failure caps the score (no high score from fast failure)
# ===========================================================================

def test_failed_provider_cannot_receive_high_score_via_fast_latency():
    """success=False with a fast 21 ms elapsed must be Critical and capped <= 20,
    never a high score derived from the fast (failed) response time."""
    metrics = he.SourceMetrics(
        p95_latency_ms=21.0,          # a failed call's elapsed time, fed by mistake
        success=False,
        heartbeat_present=True,
        last_telemetry_at=NOW - timedelta(seconds=60),
    )
    a = he.assess_source_health(metrics, now=NOW)
    assert a.status == he.HEALTH_CRITICAL
    assert a.score is not None and a.score <= 20.0, f'score {a.score} must be capped into the critical band'
    assert 'provider_call_failed' in a.triggered_rules
    # Connectivity is the controlling factor, surfaced at 0 in the breakdown.
    assert a.component_scores.get('connectivity') == 0.0


def test_endpoint_unavailable_with_fast_latency_is_capped():
    metrics = he.SourceMetrics(p95_latency_ms=19.0, endpoint_unavailable=True)
    a = he.assess_source_health(metrics, now=NOW)
    assert a.status == he.HEALTH_CRITICAL
    assert a.score is not None and a.score <= 20.0


def test_hard_failure_with_no_other_evidence_scores_zero_not_none():
    """A pure hard failure (nothing else measured) scores 0 — a strong negative
    signal, never None/absent, and never a lucky high number."""
    a = he.assess_source_health(he.SourceMetrics(success=False), now=NOW)
    assert a.status == he.HEALTH_CRITICAL
    assert a.score == 0.0


def test_successful_observation_adds_connectivity_to_breakdown():
    metrics = he.SourceMetrics(
        p95_latency_ms=120.0, success=True, heartbeat_present=True,
        last_telemetry_at=NOW - timedelta(seconds=30),
    )
    a = he.assess_source_health(metrics, now=NOW)
    assert a.status == he.HEALTH_HEALTHY
    assert a.component_scores.get('connectivity') == 100.0


# ===========================================================================
# Part 5 — pilot: successful-only P95, failure elapsed separated
# ===========================================================================

def test_latency_samples_query_filters_to_successful_calls():
    """The P95 sample loader must only read status='healthy' rows (a real DB honors
    the WHERE clause; a failed call's elapsed latency is excluded at the source)."""
    captured = {}

    class _Conn:
        def execute(self, sql, params=None):
            captured['sql'] = ' '.join(str(sql).split())

            class _R:
                def fetchall(self_inner):
                    return []
            return _R()

    pilot._load_provider_latency_samples_by_target(
        _Conn(), workspace_id=WORKSPACE, target_ids=['t1'],
    )
    assert "status = 'healthy'" in captured['sql'], captured['sql']


class _EnrichConn:
    """Fake connection that honors the P95 status='healthy' filter like a real DB.

    ``health_rows`` are the latest per-provider health records; ``all_latency_rows``
    are every latency-bearing record. The samples query (which carries
    ``status = 'healthy'``) returns only the healthy subset — exactly what Postgres
    would do — so a failed call's elapsed latency can never leak into the P95.
    """

    def __init__(self, *, health_rows, all_latency_rows):
        self.health_rows = health_rows
        self.all_latency_rows = all_latency_rows

    def execute(self, sql, params=None):
        q = ' '.join(str(sql).split()).lower()
        rows: list = []
        if 'from provider_health_records' in q and "status = 'healthy'" in q:
            rows = [r for r in self.all_latency_rows if str(r.get('status')) == 'healthy']
        elif 'from provider_health_records' in q:
            rows = self.health_rows

        class _R:
            def __init__(self_inner, data):
                self_inner._data = data

            def fetchall(self_inner):
                return self_inner._data

            def fetchone(self_inner):
                return self_inner._data[0] if self_inner._data else None
        return _R(rows)

    def commit(self):
        pass


def _target(**over):
    base = {
        'id': 'target-1', 'name': 'USDC Base', 'asset_id': 'asset-1', 'asset_name': 'USDC',
        'chain_network': 'base', 'chain_id': 8453,
        'contract_identifier': '0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48',
        'target_type': 'contract', 'monitoring_mode': 'recommended', 'enabled': True,
        'monitoring_enabled': True,
        'target_metadata': {'rpc_sources': {'primary_host': 'base-mainnet.g.alchemy.com'}},
    }
    base.update(over)
    return base


def _system(**over):
    base = {
        'id': 'system-1', 'target_id': 'target-1', 'asset_name': 'USDC', 'target_name': 'USDC Base',
        'is_enabled': True, 'runtime_status': 'failed',
        'last_heartbeat': (NOW - timedelta(seconds=30)).isoformat(),
        'last_event_at': None, 'freshness_status': 'stale', 'coverage_reason': 'failed',
    }
    base.update(over)
    return base


def _health_record(status, latency_ms, host='base-mainnet.g.alchemy.com'):
    return {
        'target_id': 'target-1', 'status': status, 'latency_ms': latency_ms,
        'checked_at': NOW.isoformat(), 'evidence_source': 'live', 'provider_type': host,
        'error_message': None if status == 'healthy' else 'all_rpc_providers_unavailable',
    }


def _enrich(conn):
    return pilot._build_monitoring_sources_enrichment(
        conn, workspace_id=WORKSPACE, assets=[{'id': 'asset-1'}],
        targets=[_target()], systems=[_system()], now=NOW,
    )['sources'][0]


def test_failed_poll_source_is_critical_and_not_high_score():
    """The production case: latest scheduled poll failed (status=error, 21 ms elapsed,
    runtime failed). The Screen-4 row must be Critical with a capped score, never 99."""
    conn = _EnrichConn(
        health_rows=[_health_record('error', 21)],
        all_latency_rows=[_health_record('error', 21)],
    )
    src = _enrich(conn)
    assert src['health_status'] == he.HEALTH_CRITICAL
    assert src['health_score'] is not None and src['health_score'] <= 20.0
    # The failed elapsed time is surfaced separately, never as a healthy latency.
    assert src['successful_latency_ms'] is None
    assert src['failure_elapsed_ms'] == 21
    assert src['median_latency_ms'] is None
    # No successful samples → no P95.
    assert src['p95_latency_ms'] is None
    assert src['p95_status'] == 'no_successful_samples'
    # Breakdown makes provider unavailability the controlling factor.
    assert src['score_breakdown']['connectivity'] == 0.0


def test_failed_elapsed_not_included_in_successful_p95():
    """Even with 25 records present, if all are failed the P95 is unavailable and the
    single 21 ms elapsed is never presented as a healthy P95."""
    rows = [_health_record('error', 21) for _ in range(25)]
    conn = _EnrichConn(health_rows=[rows[0]], all_latency_rows=rows)
    src = _enrich(conn)
    assert src['p95_latency_ms'] is None
    assert src['p95_sample_count'] == 0
    assert src['p95_status'] == 'no_successful_samples'


def test_insufficient_successful_samples_renders_insufficient():
    """1..19 successful samples → insufficient_samples (no fabricated P95)."""
    healthy_rows = [_health_record('healthy', 100 + i) for i in range(5)]
    conn = _EnrichConn(
        health_rows=[_health_record('healthy', 104)],
        all_latency_rows=healthy_rows,
    )
    src = _enrich(conn)
    assert src['p95_status'] == 'insufficient_samples'
    assert src['p95_latency_ms'] is None
    assert src['p95_sample_count'] == 5


def test_enough_successful_samples_yields_p95():
    """20+ successful samples → a real nearest-rank P95, with the healthy latency
    surfaced as successful_latency_ms."""
    healthy_rows = [_health_record('healthy', 100 + i) for i in range(25)]
    conn = _EnrichConn(
        health_rows=[_health_record('healthy', 120)],
        all_latency_rows=healthy_rows,
    )
    src = _enrich(conn)
    assert src['p95_status'] == 'available'
    assert src['p95_sample_count'] == 25
    assert src['p95_latency_ms'] is not None
    assert src['successful_latency_ms'] == 120
