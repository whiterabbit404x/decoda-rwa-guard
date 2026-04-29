from __future__ import annotations

from datetime import datetime, timedelta, timezone

from services.api.app.workspace_monitoring_summary import build_workspace_monitoring_summary


def _base(**overrides):
    now = datetime(2026, 4, 29, 12, 0, tzinfo=timezone.utc)
    payload = dict(
        now=now,
        workspace_configured=True,
        configuration_reason_codes=[],
        query_failure_detected=False,
        schema_drift_detected=False,
        missing_telemetry_only=False,
        monitoring_mode='live',
        runtime_status='live',
        configured_systems=1,
        monitored_systems_count=1,
        reporting_systems=1,
        protected_assets=1,
        last_poll_at=now,
        last_heartbeat_at=now,
        last_telemetry_at=now - timedelta(seconds=30),
        last_coverage_telemetry_at=now - timedelta(seconds=30),
        telemetry_kind='coverage',
        last_detection_at=now - timedelta(seconds=25),
        evidence_source='live',
        status_reason=None,
        configuration_reason=None,
        valid_protected_asset_count=1,
        linked_monitored_system_count=1,
        persisted_enabled_config_count=1,
        valid_target_system_link_count=1,
        telemetry_window_seconds=300,
    )
    payload.update(overrides)
    return build_workspace_monitoring_summary(**payload)


def test_runtime_status_contradictions_required_matrix_fail_closed():
    s1 = _base(runtime_status='offline')
    assert 'offline_with_current_telemetry' in s1['contradiction_flags']

    s2 = _base(reporting_systems=0)
    assert 'live_monitoring_without_reporting_systems' in s2['contradiction_flags']

    s3 = _base(last_telemetry_at=None, last_coverage_telemetry_at=None, telemetry_kind=None)
    assert 'telemetry_unavailable_with_high_confidence' not in s3['contradiction_flags']

    s4 = _base(workspace_configured=False)
    assert 'workspace_unconfigured_with_coverage' in s4['contradiction_flags']

    s5 = _base(evidence_source='none')
    assert s5['evidence_source_summary'] == 'none'

    s6 = _base(last_poll_at=None, last_telemetry_at=None, telemetry_kind=None)
    assert 'heartbeat_without_telemetry_timestamp' in s6['contradiction_flags']

    s7 = _base(evidence_source='simulator')
    assert s7['evidence_source_summary'] != 'live'

    for summary in (s1, s2, s4, s6):
        assert bool(summary['contradiction_flags'])
        assert summary['runtime_status'] != 'live' or summary['monitoring_status'] != 'live'


def test_runtime_status_target_coverage_reporting_without_telemetry_guard_present_in_runtime_logic():
    source = open('services/api/app/monitoring_runner.py', encoding='utf-8').read()
    assert "coverage_status != 'reporting' or coverage_telemetry_at is None" in source
    assert 'reporting_systems_zero_with_healthy' in source
