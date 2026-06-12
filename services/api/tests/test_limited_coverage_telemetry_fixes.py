"""
Tests for the LIMITED COVERAGE regression where fresh live telemetry was not
counted as coverage even when the worker was actively polling.

Root causes addressed:
1. telemetry_kind stays None when canonical_last_telemetry_at is None but
   monitoring_event_receipts has coverage data → fixed by receipts fallback.
2. workspace_unconfigured_with_reporting_systems hard guard fires when
   monitoring_event_receipts has data but monitored_systems.asset_id is NULL →
   fixed by workspace_configured override via coverage receipts path.
3. _loose_target_rows_flag fires as false positive when canonical reporting is 0
   but legacy/receipts reporting > 0 → fixed by gating on reporting_systems == 0.
4. _row_has_valid_target_asset_link returns False when monitored_systems.asset_id
   is NULL even though the target has a valid asset → fixed by NULL fallback.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from services.api.app.workspace_monitoring_summary import (
    build_workspace_monitoring_summary,
    _normalized_monitoring_status,
)
from services.api.app.runtime_truthfulness import compute_signal_freshness


NOW = datetime(2026, 6, 12, 12, 0, 0, tzinfo=timezone.utc)
TELEMETRY_WINDOW = 900  # 15 minutes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fresh_ts(seconds_ago: int = 60) -> datetime:
    from datetime import timedelta
    return NOW - timedelta(seconds=seconds_ago)


def _summary(
    *,
    reporting_systems: int = 1,
    coverage_ts: datetime | None = None,
    canonical_ts: datetime | None = None,
    workspace_configured: bool = True,
    evidence_source: str = 'live',
    telemetry_kind: str | None = 'coverage',
    runtime_status: str = 'healthy',
    valid_link_count: int = 1,
    valid_asset_count: int = 1,
    linked_system_count: int = 1,
    persisted_config_count: int = 1,
    protected_assets: int = 1,
) -> dict:
    return build_workspace_monitoring_summary(
        now=NOW,
        workspace_configured=workspace_configured,
        configuration_reason_codes=[],
        query_failure_detected=False,
        schema_drift_detected=False,
        missing_telemetry_only=False,
        monitoring_mode='live',
        runtime_status=runtime_status,
        configured_systems=1,
        monitored_systems_count=1,
        reporting_systems=reporting_systems,
        protected_assets=protected_assets,
        last_poll_at=fresh_ts(60),
        last_heartbeat_at=fresh_ts(60),
        last_telemetry_at=canonical_ts,
        last_coverage_telemetry_at=coverage_ts or canonical_ts,
        telemetry_kind=telemetry_kind,
        last_detection_at=None,
        evidence_source=evidence_source,
        status_reason=None,
        configuration_reason=None,
        valid_protected_asset_count=valid_asset_count,
        linked_monitored_system_count=linked_system_count,
        persisted_enabled_config_count=persisted_config_count,
        valid_target_system_link_count=valid_link_count,
        telemetry_window_seconds=TELEMETRY_WINDOW,
    )


# ---------------------------------------------------------------------------
# Test 1: fresh live telemetry (canonical path) → freshness=fresh, no guards
# ---------------------------------------------------------------------------

def test_fresh_canonical_telemetry_is_fresh():
    ts = fresh_ts(60)
    s = _summary(canonical_ts=ts, coverage_ts=ts, reporting_systems=1)
    assert s['telemetry_freshness'] == 'fresh'
    assert s['confidence'] in {'high', 'medium'}
    assert s['monitoring_status'] == 'live'
    assert s['guard_flags'] == []


# ---------------------------------------------------------------------------
# Test 2: coverage-receipts-only path (no canonical, no legacy) → still fresh
# ---------------------------------------------------------------------------

def test_coverage_receipts_only_telemetry_is_fresh():
    """When canonical_last_telemetry_at is None but last_coverage_telemetry_at
    is set (from monitoring_event_receipts), telemetry_freshness must be 'fresh'
    when the worker passes telemetry_kind='coverage' (Fix 1)."""
    ts = fresh_ts(60)
    # Simulate: canonical=None, coverage=ts, telemetry_kind='coverage'
    s = _summary(
        canonical_ts=None,
        coverage_ts=ts,
        telemetry_kind='coverage',
        reporting_systems=1,
        workspace_configured=True,
    )
    assert s['telemetry_freshness'] == 'fresh', (
        f"Expected fresh but got {s['telemetry_freshness']}. "
        f"last_telemetry_at={s.get('last_telemetry_at')} guard_flags={s.get('guard_flags')}"
    )
    assert 'poll_without_telemetry_timestamp' not in (s.get('guard_flags') or [])


# ---------------------------------------------------------------------------
# Test 3: workspace_configured=True with fresh coverage → no hard guards fire
# ---------------------------------------------------------------------------

def test_no_hard_guards_when_workspace_configured_and_fresh_coverage():
    ts = fresh_ts(60)
    s = _summary(
        canonical_ts=ts,
        coverage_ts=ts,
        workspace_configured=True,
        reporting_systems=1,
        valid_link_count=1,
        valid_asset_count=1,
        persisted_config_count=1,
    )
    assert s['guard_flags'] == [], f"Unexpected guard_flags: {s['guard_flags']}"
    assert s['monitoring_status'] == 'live'


# ---------------------------------------------------------------------------
# Test 4: workspace_configured=False with reporting_systems>0 fires hard guard
# (this is the CORRECT behavior when workspace really is unconfigured)
# ---------------------------------------------------------------------------

def test_unconfigured_workspace_with_reporting_fires_guard():
    ts = fresh_ts(60)
    s = _summary(
        canonical_ts=ts,
        coverage_ts=ts,
        workspace_configured=False,
        reporting_systems=1,
        valid_link_count=0,
        valid_asset_count=0,
        persisted_config_count=0,
        linked_system_count=0,
    )
    assert 'workspace_unconfigured_with_reporting_systems' in (s.get('guard_flags') or s.get('contradiction_flags') or [])


# ---------------------------------------------------------------------------
# Test 5: stale telemetry stays limited — freshness override is truthful
# ---------------------------------------------------------------------------

def test_stale_telemetry_stays_stale():
    stale_ts = fresh_ts(1800)  # 30 minutes ago, beyond 900s window
    s = _summary(canonical_ts=stale_ts, coverage_ts=stale_ts, reporting_systems=1)
    assert s['telemetry_freshness'] == 'stale'
    assert s['monitoring_status'] == 'limited'
    assert s['confidence'] in {'low', 'unavailable'}


# ---------------------------------------------------------------------------
# Test 6: simulator evidence never claims live_provider
# ---------------------------------------------------------------------------

def test_simulator_evidence_not_claimed_as_live():
    ts = fresh_ts(60)
    s = _summary(
        canonical_ts=ts,
        coverage_ts=ts,
        evidence_source='simulator',
        reporting_systems=1,
    )
    assert s.get('evidence_source_summary') != 'live_provider'
    assert s.get('monitoring_status') != 'live'


# ---------------------------------------------------------------------------
# Test 7: no telemetry at all → freshness=unavailable
# ---------------------------------------------------------------------------

def test_no_telemetry_is_unavailable():
    s = _summary(
        canonical_ts=None,
        coverage_ts=None,
        telemetry_kind=None,
        reporting_systems=0,
        workspace_configured=True,
        valid_link_count=0,
        valid_asset_count=0,
        persisted_config_count=0,
        linked_system_count=0,
    )
    assert s['telemetry_freshness'] == 'unavailable'


# ---------------------------------------------------------------------------
# Test 8: replay/demo evidence never counts as live
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("source", ["replay", "none", ""])
def test_non_live_evidence_not_counted_as_live(source: str):
    ts = fresh_ts(60)
    s = _summary(
        canonical_ts=ts,
        coverage_ts=ts,
        evidence_source=source,
        reporting_systems=1,
    )
    assert s.get('evidence_source_summary') not in {'live_provider', 'live'}


# ---------------------------------------------------------------------------
# Test 9: _normalized_monitoring_status — healthy + fresh → live
# ---------------------------------------------------------------------------

def test_normalized_monitoring_status_healthy_fresh_is_live():
    status = _normalized_monitoring_status(
        runtime_status='healthy',
        reporting_systems_count=1,
        telemetry_freshness='fresh',
        contradiction_flags=[],
        workspace_configured=True,
    )
    assert status == 'live'


def test_normalized_monitoring_status_with_contradiction_is_limited():
    status = _normalized_monitoring_status(
        runtime_status='healthy',
        reporting_systems_count=1,
        telemetry_freshness='fresh',
        contradiction_flags=['some_soft_contradiction'],
        workspace_configured=True,
    )
    assert status == 'limited'


# ---------------------------------------------------------------------------
# Test 10: compute_signal_freshness sanity
# ---------------------------------------------------------------------------

def test_compute_signal_freshness_recent():
    from services.api.app.runtime_truthfulness import compute_signal_freshness
    ts = fresh_ts(60)
    result = compute_signal_freshness(ts.isoformat(), NOW, 900)
    assert result == 'current'


def test_compute_signal_freshness_stale():
    from services.api.app.runtime_truthfulness import compute_signal_freshness
    ts = fresh_ts(1800)
    result = compute_signal_freshness(ts.isoformat(), NOW, 900)
    assert result == 'stale'


def test_compute_signal_freshness_none():
    from services.api.app.runtime_truthfulness import compute_signal_freshness
    result = compute_signal_freshness(None, NOW, 900)
    assert result == 'unavailable'
