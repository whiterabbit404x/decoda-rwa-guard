"""Screen 4 Datto provider-backoff truthfulness (production canary, 2026-07-22).

Production evidence: Alchemy returned HTTP 429 during startup and opened backoff; both
Base scheduled polls (Datto + Rabbit) were SKIPPED because provider backoff was active.
The newest scheduled provider snapshot for Datto is therefore degraded/skipped
(provider_backoff_active), and the target carries watcher_degraded_reason=
'provider_backoff_active'.

Screen 4 previously showed CONTRADICTORY values for Datto:
    Source Health 0/1, Live Coverage 0%, "provider polling unavailable"  (correct)
    Health score 100, Active Routes 1, Routing Primary                    (WRONG)

A configured route is NOT an operational route. The status, health score, operational
route count, routing badge and agent assessment must ALL derive from the SAME newest
scheduled provider snapshot. These tests lock that in for BOTH canonical backoff signals
(the degraded provider_health snapshot, and the target watcher flag), so a backoff-skipped
poll can never read healthy / score 100 / 1 active route / Primary.

Maps to task tests 3 (operational routes = 0), 4 (backoff never scores 100) and
5 (status/score/routes/assessment use one snapshot). Everything is derived from canonical
records through a lightweight fake connection (no live database).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from services.api.app import pilot

# Production facts (task PRODUCTION EVIDENCE).
DATTO_WS = '4fffd3f9-d55f-456f-8a7e-8b9ed2083721'
DATTO_TARGET = '9c6ecabb-cd52-404f-9859-40567b09dbb4'
DATTO_PHR_ID = '25686340-e0d0-44e0-8e38-081e72f45db0'  # the degraded/skipped snapshot
USDC = '0x833589fcd6edb6e08f4c7c32d4f71b54bda02913'
ALCHEMY = 'base-mainnet.g.alchemy.com'

NOW = datetime(2026, 7, 22, 15, 10, 0, tzinfo=timezone.utc)
# Backoff opened until 15:16:56Z; the last real telemetry is well outside the window.
STALE_AT = NOW - timedelta(hours=6)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)


class _EnrichConn:
    """Fake connection that honors the P95 status='healthy' filter like the real DB."""

    def __init__(self, *, health_rows, latency_rows=None, coverage_rows=None, block_rows=None):
        self.health_rows = health_rows
        self.latency_rows = latency_rows if latency_rows is not None else health_rows
        self.coverage_rows = coverage_rows or []
        self.block_rows = block_rows or []

    def execute(self, sql, params=None):
        q = ' '.join(str(sql).split()).lower()
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


def _target(**over):
    base = {
        'id': DATTO_TARGET, 'name': 'Datto Base USDC', 'asset_id': 'asset-d', 'asset_name': 'USDC',
        'chain_network': 'base', 'chain_id': 8453, 'contract_identifier': USDC,
        'target_type': 'contract', 'monitoring_mode': 'recommended',
        'enabled': True, 'monitoring_enabled': True, 'asset_missing': False,
        # Alchemy is the CONFIGURED primary provider (declared route).
        'target_metadata': {'rpc_sources': {'primary_host': ALCHEMY}},
        # Canonical target-level backoff flag set by the skip handler.
        'watcher_source_status': 'degraded',
        'watcher_degraded_reason': 'provider_backoff_active',
    }
    base.update(over)
    return base


def _system(**over):
    # The runtime row is STALE 'healthy' from before the outage — the backoff signal must
    # still win (fail-closed), never let a stale healthy runtime read as operational.
    base = {
        'id': 'sys-d', 'target_id': DATTO_TARGET, 'asset_name': 'USDC', 'target_name': 'Datto Base USDC',
        'is_enabled': True, 'runtime_status': 'healthy',
        'last_heartbeat': NOW.isoformat(), 'last_event_at': STALE_AT.isoformat(),
        'freshness_status': 'fresh', 'coverage_reason': 'live_no_recent_events',
    }
    base.update(over)
    return base


def _degraded_backoff_health(**over):
    """The newest scheduled provider snapshot: degraded because backoff was active."""
    base = {
        'id': DATTO_PHR_ID, 'target_id': DATTO_TARGET, 'status': 'degraded', 'latency_ms': None,
        'checked_at': NOW, 'evidence_source': 'none', 'provider_type': ALCHEMY,
        'error_message': 'provider_backoff_active', 'metadata': {'degraded_reason': 'provider_backoff_active'},
    }
    base.update(over)
    return base


def _stale_healthy_health(**over):
    """A stale HEALTHY snapshot from before the 429 — must NOT rescue a backoff-skipped poll."""
    base = {
        'id': 'old-healthy', 'target_id': DATTO_TARGET, 'status': 'healthy', 'latency_ms': 120,
        'checked_at': STALE_AT, 'evidence_source': 'live', 'provider_type': ALCHEMY,
        'error_message': None, 'metadata': {},
    }
    base.update(over)
    return base


def _enrich(conn, targets=None, systems=None):
    return pilot._build_monitoring_sources_enrichment(
        conn, workspace_id=DATTO_WS, assets=[{'id': 'asset-d'}],
        targets=targets or [_target()], systems=systems or [_system()], now=NOW,
    )


# ===========================================================================
# Task test 3: a configured-but-unavailable provider produces operational routes = 0.
# ===========================================================================
def test_configured_backoff_provider_is_not_an_operational_route():
    conn = _EnrichConn(health_rows=[_degraded_backoff_health()])
    out = _enrich(conn)
    src, summ, agent = out['sources'][0], out['summary'], out['agent']
    # Alchemy is the CONFIGURED primary, but backoff means it is NOT operational.
    assert src['configured_primary_provider'] == ALCHEMY
    assert src['operational_primary_provider'] is None
    assert summ['active_routes']['primary'] == 0, 'a configured route is not an operational route'
    assert agent['operational_routes'] == 0
    assert src['routing'] != 'primary'
    assert src['routing'] == 'unavailable'


def test_backoff_via_target_watcher_flag_alone_is_not_operational():
    """The realistic skip case: NO fresh provider_health row was written (the poll was
    skipped), only the target watcher flag is set. The source must still fail closed."""
    conn = _EnrichConn(health_rows=[_stale_healthy_health()])  # only a STALE healthy snapshot
    out = _enrich(conn)
    src, summ = out['sources'][0], out['summary']
    assert src['provider_backoff_active'] is True
    assert summ['active_routes']['primary'] == 0
    assert src['operational_primary_provider'] is None
    assert src['routing'] == 'unavailable'


# ===========================================================================
# Task test 4: provider backoff can never produce health score 100.
# ===========================================================================
def test_backoff_never_produces_health_score_100():
    for health in ([_degraded_backoff_health()], [_stale_healthy_health()]):
        out = _enrich(_EnrichConn(health_rows=health))
        src, summ = out['sources'][0], out['summary']
        assert src['health_score'] is not None
        assert src['health_score'] <= 20, f'backoff must score low, got {src["health_score"]}'
        assert src['health_score'] != 100
        # Overall Source Health card health_pct is the same low score, never 100.
        assert (summ['source_health']['health_pct'] or 0) <= 20
        assert summ['source_health']['healthy'] == 0
        assert summ['source_health']['total'] == 1


# ===========================================================================
# Task test 5: status, score, routes and assessment all use one snapshot.
# ===========================================================================
def test_status_score_routes_assessment_agree_on_one_snapshot():
    conn = _EnrichConn(health_rows=[_degraded_backoff_health()])
    out = _enrich(conn)
    src, summ, agent = out['sources'][0], out['summary'], out['agent']
    # Status: Provider Unavailable with the backoff reason.
    assert src['status'] == pilot._SOURCE_STATUS_PROVIDER_UNAVAILABLE
    assert src['status_reason'] == 'provider_backoff_active'
    # Score: low (hard-failure band), consistent with the unavailable status.
    assert src['health_score'] <= 20
    # Routes: zero operational; routing badge Unavailable, never Primary.
    assert summ['active_routes']['primary'] == 0
    assert src['routing'] == 'unavailable'
    # Assessment: the agent panel reflects a degraded provider, not "healthy".
    assert agent['operational_routes'] == 0
    assert agent['confidence'] in {'low', 'unavailable'}
    assert agent['state'] == 'attention_required'
    # The agent panel cites the SAME degraded snapshot the row used.
    assert src['provider_health_record_id'] == DATTO_PHR_ID
    assert DATTO_PHR_ID in agent['evidence_record_ids']


def test_live_coverage_is_zero_during_backoff():
    """Live telemetry coverage is 0% during backoff even though the target is configured 1/1."""
    conn = _EnrichConn(health_rows=[_degraded_backoff_health()])
    tc = _enrich(conn)['summary']['telemetry_coverage']
    assert tc['configured'] == 1
    assert tc['live_fresh'] == 0
    assert tc['live_coverage_pct'] == 0.0


def test_fresh_successful_poll_clears_backoff_and_restores_primary_route():
    """Regression guard: once a fresh SUCCESSFUL poll lands, the stale target backoff flag
    must NOT suppress the now-operational primary route (normal production restored)."""
    healthy_now = {
        'id': 'phr-fresh', 'target_id': DATTO_TARGET, 'status': 'healthy', 'latency_ms': 110,
        'checked_at': NOW, 'evidence_source': 'live', 'provider_type': ALCHEMY,
        'error_message': None, 'metadata': {},
    }
    coverage = [{
        'target_id': DATTO_TARGET, 'coverage_status': 'reporting', 'last_poll_at': NOW,
        'last_heartbeat_at': NOW, 'last_telemetry_at': NOW, 'last_detection_at': None,
        'evidence_source': 'live', 'computed_at': NOW,
    }]
    conn = _EnrichConn(health_rows=[healthy_now], latency_rows=[healthy_now], coverage_rows=coverage)
    # Target still carries the stale watcher backoff flag from the outage.
    out = _enrich(conn, systems=[_system(runtime_status='healthy', last_event_at=NOW.isoformat())])
    src, summ = out['sources'][0], out['summary']
    assert src['provider_backoff_active'] is False, 'a fresh success clears the stale flag'
    assert src['operational_primary_provider'] == ALCHEMY
    assert summ['active_routes']['primary'] == 1
    assert src['routing'] == 'primary'
