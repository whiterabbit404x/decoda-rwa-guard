from __future__ import annotations

from datetime import datetime, timedelta, timezone

from services.api.app.workspace_monitoring_summary import build_workspace_monitoring_summary


def _now() -> datetime:
    return datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc)


def _build_summary(**overrides: object) -> dict[str, object]:
    now = _now()
    params: dict[str, object] = {
        'now': now,
        'workspace_configured': True,
        'configuration_reason_codes': None,
        'query_failure_detected': False,
        'schema_drift_detected': False,
        'missing_telemetry_only': False,
        'monitoring_mode': 'live',
        'runtime_status': 'live',
        'configured_systems': 2,
        'monitored_systems_count': 2,
        'reporting_systems': 1,
        'protected_assets': 2,
        'last_poll_at': now,
        'last_heartbeat_at': now,
        'last_telemetry_at': now - timedelta(seconds=30),
        'last_coverage_telemetry_at': now - timedelta(seconds=30),
        'telemetry_kind': 'target_event',
        'last_detection_at': now,
        'evidence_source': 'live',
        'status_reason': None,
        'configuration_reason': None,
        'valid_protected_asset_count': 2,
        'linked_monitored_system_count': 2,
        'persisted_enabled_config_count': 2,
        'valid_target_system_link_count': 2,
        'telemetry_window_seconds': 300,
        'active_alerts_count': 1,
        'active_incidents_count': 2,
    }
    params.update(overrides)
    return build_workspace_monitoring_summary(**params)


def test_summary_returns_only_strict_contract_fields() -> None:
    summary = _build_summary()
    assert set(summary.keys()) == {
        'workspace_configured',
        'runtime_status',
        'monitoring_status',
        'last_poll_at',
        'last_heartbeat_at',
        'last_telemetry_at',
        'telemetry_freshness',
        'confidence',
        'reporting_systems_count',
        'monitored_systems_count',
        'protected_assets_count',
        'active_alerts_count',
        'active_incidents_count',
        'evidence_source_summary',
        'status_reason',
    }


def test_runtime_status_is_normalized_to_contract_values() -> None:
    assert _build_summary(runtime_status='healthy')['runtime_status'] == 'live'
    assert _build_summary(runtime_status='failed')['runtime_status'] == 'offline'
    assert _build_summary(runtime_status='disabled')['runtime_status'] == 'idle'
    assert _build_summary(runtime_status='bogus')['runtime_status'] == 'offline'


def test_monitoring_status_is_normalized_to_contract_values() -> None:
    assert _build_summary(runtime_status='offline')['monitoring_status'] == 'offline'
    assert _build_summary(reporting_systems=0, runtime_status='live')['monitoring_status'] == 'limited'
    assert _build_summary(runtime_status='live', reporting_systems=1)['monitoring_status'] == 'live'


def test_coverage_telemetry_can_backfill_last_telemetry_at() -> None:
    now = _now()
    summary = _build_summary(
        last_telemetry_at=None,
        last_coverage_telemetry_at=now - timedelta(seconds=45),
        telemetry_kind='coverage',
    )
    assert summary['last_telemetry_at'] == (now - timedelta(seconds=45)).isoformat()
    assert summary['telemetry_freshness'] == 'fresh'


def test_guard_reason_is_exposed_as_status_reason() -> None:
    summary = _build_summary(runtime_status='offline')
    assert summary['status_reason'] == 'guard:offline_with_current_telemetry'

