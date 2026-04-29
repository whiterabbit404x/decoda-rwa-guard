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
        'continuity_status',
        'continuity_reason_codes',
        'continuity_signals',
        'continuity_slo_pass',
        'heartbeat_age_seconds',
        'event_ingestion_age_seconds',
        'detection_eval_age_seconds',
        'required_thresholds_seconds',
        'ingestion_freshness',
        'detection_pipeline_freshness',
        'worker_heartbeat_freshness',
        'event_throughput_window',
        'event_throughput_window_seconds',
        'contradiction_flags',
        'guard_flags',
        'status_reason',
        'db_failure_classification',
        'db_failure_reason',
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
    assert 'reporting_coverage_without_target_telemetry' in summary['guard_flags']
    assert summary['telemetry_freshness'] == 'stale'


def test_guard_reason_is_exposed_as_status_reason() -> None:
    summary = _build_summary(runtime_status='offline')
    assert summary['status_reason'] == 'guard:offline_with_current_telemetry'


def test_idle_runtime_guard_skips_when_explicit_degraded_reason_is_present() -> None:
    summary = _build_summary(
        runtime_status='idle',
        status_reason='runtime_status_degraded:database_error',
    )
    assert 'idle_runtime_with_active_monitoring_claim' not in summary['contradiction_flags']
    assert summary['status_reason'] == 'runtime_status_degraded:database_error'


def test_status_reason_uses_deterministic_hard_guard_priority() -> None:
    summary = _build_summary(
        runtime_status='offline',
    )
    assert 'offline_with_current_telemetry' in summary['guard_flags']
    assert summary['status_reason'] == 'guard:offline_with_current_telemetry'


def test_continuity_and_guard_flags_can_coexist_without_false_offline_transition() -> None:
    summary = _build_summary(
        runtime_status='offline',
    )
    assert summary['continuity_status'] == 'idle_no_telemetry'
    assert 'offline_with_current_telemetry' in summary['guard_flags']
    assert summary['status_reason'] == 'guard:offline_with_current_telemetry'


def test_db_outage_forces_non_live_and_unavailable_confidence() -> None:
    summary = _build_summary(
        runtime_status='live',
        db_persistence_available=False,
        db_persistence_reason='Monitoring persistence unavailable',
    )
    assert summary['runtime_status'] == 'degraded'
    assert summary['monitoring_status'] == 'limited'
    assert summary['confidence'] == 'unavailable'
    assert summary['db_failure_classification'] == 'persistence_unavailable'
    assert summary['db_failure_reason'] == 'Monitoring persistence unavailable'
    assert summary['status_reason'] == 'Monitoring persistence unavailable'


def test_db_outage_prevents_fresh_without_db_backed_evidence() -> None:
    summary = _build_summary(
        db_persistence_available=False,
        db_persistence_reason='Monitoring loop running without database access',
    )
    assert summary['telemetry_freshness'] != 'fresh'
    assert summary['status_reason'] == 'Monitoring loop running without database access'


def test_hard_contradictions_fail_closed_semantics() -> None:
    summary = _build_summary(
        workspace_configured=False,
        reporting_systems=2,
        last_telemetry_at=None,
        last_coverage_telemetry_at=_now() - timedelta(seconds=30),
        telemetry_kind='coverage',
        evidence_source='none',
    )
    assert 'workspace_unconfigured_with_reporting_systems' in summary['guard_flags']
    assert 'workspace_unconfigured_with_coverage' in summary['contradiction_flags']
    assert summary['runtime_status'] != 'live'
    assert summary['monitoring_status'] != 'live'
    assert summary['telemetry_freshness'] != 'fresh'
    assert summary['confidence'] == 'unavailable'
    assert summary['evidence_source_summary'] == 'none'


def test_heartbeat_only_live_claim_is_hard_guarded() -> None:
    summary = _build_summary(
        runtime_status='live',
        last_telemetry_at=None,
        last_coverage_telemetry_at=None,
        telemetry_kind=None,
        evidence_source='live',
    )
    assert 'heartbeat_only_with_live_claim' in summary['guard_flags']
    assert 'live_evidence_without_live_telemetry_kind' in summary['guard_flags']
    assert summary['runtime_status'] == 'degraded'
    assert summary['monitoring_status'] == 'limited'
