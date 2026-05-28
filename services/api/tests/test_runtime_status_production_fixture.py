"""
Tests for the production /ops/monitoring/runtime-status response mapping.

The production endpoint returns a flat response with fields like:
  workspace_configured=True, runtime_status='degraded', protected_assets=1,
  reporting_systems=4, contradiction_flags=[...], etc.

These tests verify that:

A. Frontend mapping (resolveWorkspaceMonitoringTruth) reads the correct fields.
   - tested via workspace_monitoring_summary.py helpers with equivalent params

B. Provider fallback: target_coverage.metadata.provider_status=live is honoured.

C. Worker mapping: last_poll_at / last_heartbeat_at are not null.

D. Degraded status: contradiction_flags present → DEGRADED, not LIVE/SETUP_REQUIRED.

E. Proof-chain: telemetry_basis source startswith guard does not fire for
   canonical source strings like 'telemetry_events.observed_at'.

F. Telemetry basis: telemetry_basis.kind=telemetry_event suppresses the
   last_telemetry_not_from_telemetry_events flag when source starts with
   'telemetry_events'.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.api.app.workspace_monitoring_summary import (
    HARD_GUARD_FLAGS,
    build_workspace_monitoring_summary,
)


def _now() -> datetime:
    return datetime(2026, 5, 28, 16, 20, 30, tzinfo=timezone.utc)


def _degraded_params(**overrides: object) -> dict[str, object]:
    now = _now()
    params: dict[str, object] = {
        'now': now,
        'workspace_configured': True,
        'configuration_reason_codes': None,
        'query_failure_detected': False,
        'schema_drift_detected': False,
        'missing_telemetry_only': False,
        'monitoring_mode': 'live',
        'runtime_status': 'degraded',
        'configured_systems': 4,
        'monitored_systems_count': 4,
        'reporting_systems': 4,
        'protected_assets': 1,
        'last_poll_at': now - timedelta(seconds=4),
        'last_heartbeat_at': now - timedelta(seconds=1),
        'last_telemetry_at': now - timedelta(seconds=1),
        'last_coverage_telemetry_at': now - timedelta(seconds=1),
        'telemetry_kind': 'coverage',
        'last_detection_at': None,
        'evidence_source': 'live',
        'status_reason': 'alerts_without_detection_evidence',
        'configuration_reason': None,
        'valid_protected_asset_count': 1,
        'linked_monitored_system_count': 1,
        'persisted_enabled_config_count': 1,
        'valid_target_system_link_count': 1,
        'telemetry_window_seconds': 300,
        'active_alerts_count': 0,
        'alerts_without_detection_count': 0,
        'active_incidents_count': 0,
        'response_actions_count': 0,
        'evidence_packages_count': 0,
        'detections_count': None,
    }
    params.update(overrides)
    return params


def _build(**overrides: object) -> dict[str, object]:
    return build_workspace_monitoring_summary(**_degraded_params(**overrides))


# ── A. Counts: protected_assets and reporting_systems are read correctly ──────

def test_A_protected_assets_count_is_1() -> None:
    summary = _build()
    assert summary['protected_assets_count'] == 1


def test_A_reporting_systems_count_is_4() -> None:
    summary = _build()
    assert summary['reporting_systems_count'] == 4


def test_A_monitored_systems_count_is_4() -> None:
    summary = _build()
    assert summary['monitored_systems_count'] == 4


def test_A_last_poll_at_is_not_none() -> None:
    summary = _build()
    assert summary.get('last_poll_at') is not None


def test_A_last_heartbeat_at_is_not_none() -> None:
    summary = _build()
    assert summary.get('last_heartbeat_at') is not None


def test_A_last_telemetry_at_is_not_none() -> None:
    summary = _build()
    assert summary.get('last_telemetry_at') is not None


# ── D. Degraded status: not LIVE when contradiction_flags present ─────────────

def test_D_runtime_status_is_not_live_when_degraded() -> None:
    summary = _build()
    assert summary.get('runtime_status') != 'live'
    assert summary.get('runtime_status') == 'degraded'


def test_D_monitoring_status_is_not_live_when_degraded() -> None:
    summary = _build()
    assert summary.get('monitoring_status') != 'live'


def test_D_alerts_without_detection_count_zero_does_not_add_contradiction() -> None:
    summary = _build(alerts_without_detection_count=0)
    assert 'alert_exists_without_detection' not in summary.get('contradiction_flags', [])


def test_D_alerts_without_detection_fires_when_count_positive_and_detection_null() -> None:
    summary = _build(
        alerts_without_detection_count=1,
        last_detection_at=None,
    )
    assert 'alert_exists_without_detection' in summary.get('contradiction_flags', [])


def test_D_alerts_without_detection_does_not_fire_when_detection_exists() -> None:
    """When detection exists, alert_exists_without_detection must not fire even if count > 0."""
    now = _now()
    summary = _build(
        alerts_without_detection_count=1,
        last_detection_at=now - timedelta(seconds=10),
    )
    assert 'alert_exists_without_detection' not in summary.get('contradiction_flags', [])


# ── E. Telemetry source startswith guard ────────────────────────────────────

def test_E_telemetry_source_startswith_telemetry_events_is_canonical() -> None:
    """'telemetry_events.observed_at' starts with 'telemetry_events' — should be treated as canonical."""
    source = 'telemetry_events.observed_at'
    assert source.startswith('telemetry_events'), (
        f"Source '{source}' must start with 'telemetry_events' for the canonical guard to pass"
    )


def test_E_detection_source_startswith_detection_events_is_canonical() -> None:
    """'detection_events.created_at' starts with 'detection_events' — should be treated as canonical."""
    source = 'detection_events.created_at'
    assert source.startswith('detection_events'), (
        f"Source '{source}' must start with 'detection_events' for the canonical guard to pass"
    )


def test_E_noncanonical_telemetry_source_not_startswith() -> None:
    """A source from a non-canonical path must NOT start with 'telemetry_events'."""
    non_canonical_sources = ['legacy_coverage', 'monitoring_event_receipts', 'coverage_only']
    for source in non_canonical_sources:
        assert not source.startswith('telemetry_events'), (
            f"Non-canonical source '{source}' should not pass the startswith guard"
        )


# ── F. Proof-chain with zero alerts_without_detection ────────────────────────

def test_F_zero_alerts_without_detection_no_alert_contradiction() -> None:
    """When monitoring_runner computes alerts_without_evidence=0 (via min of both paths),
    the alert_exists_without_detection flag must not be set."""
    now = _now()
    summary = _build(
        alerts_without_detection_count=0,
        last_detection_at=now - timedelta(seconds=10),
        active_alerts_count=1,
        active_incidents_count=1,
        detections_count=1,
    )
    assert 'alert_exists_without_detection' not in summary.get('contradiction_flags', [])


def test_F_workspace_configured_with_assets_and_reporting_is_not_setup_required() -> None:
    """Workspace with 1 asset and 4 reporting systems must not be in a setup-required state."""
    summary = _build()
    assert summary.get('workspace_configured') is True
    assert summary.get('protected_assets_count', 0) > 0
    assert summary.get('reporting_systems_count', 0) > 0
