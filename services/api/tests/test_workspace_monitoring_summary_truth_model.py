from __future__ import annotations

from datetime import datetime, timedelta, timezone

from services.api.app.workspace_monitoring_summary import build_workspace_monitoring_summary


def _now() -> datetime:
    return datetime(2026, 4, 15, 12, 0, tzinfo=timezone.utc)


def test_summary_includes_unified_truth_model_fields() -> None:
    now = _now()
    summary = build_workspace_monitoring_summary(
        now=now,
        workspace_configured=True,
        monitoring_mode='live',
        runtime_status='healthy',
        configured_systems=3,
        monitored_systems_count=4,
        reporting_systems=2,
        protected_assets=5,
        last_poll_at=now,
        last_heartbeat_at=now,
        last_telemetry_at=now - timedelta(seconds=30),
        telemetry_kind='target_event',
        last_detection_at=now,
        evidence_source='live',
        status_reason=None,
        configuration_reason=None,
        valid_protected_asset_count=5,
        linked_monitored_system_count=3,
        persisted_enabled_config_count=3,
        valid_target_system_link_count=3,
        telemetry_window_seconds=300,
    )
    assert summary['workspace_configured'] is True
    assert summary['runtime_status'] == 'healthy'
    assert summary['coverage_counts']['configured_systems'] == 3
    assert summary['coverage_counts']['monitored_systems_count'] == 4
    assert summary['freshness'] == summary['freshness_status']
    assert summary['confidence'] == summary['confidence_status']
    assert summary['last_poll_at'] is not None
    assert summary['last_heartbeat_at'] is not None
    assert summary['last_telemetry_at'] is not None
    assert summary['telemetry_kind'] == 'target_event'
    assert summary['evidence_source'] == 'live'
    assert summary['status_reason'] is None
    assert summary['configuration_reason'] is None
    assert summary['protected_assets_count'] == 5
    assert summary['reporting_systems_count'] == 2
    assert summary['monitored_systems_count'] == 4
    assert summary['valid_protected_asset_count'] == 5
    assert summary['linked_monitored_system_count'] == 3
    assert summary['persisted_enabled_config_count'] == 3
    assert summary['valid_target_system_link_count'] == 3


def test_heartbeat_recent_without_telemetry_sets_unavailable_freshness() -> None:
    now = _now()
    summary = build_workspace_monitoring_summary(
        now=now,
        workspace_configured=True,
        monitoring_mode='live',
        runtime_status='idle',
        configured_systems=2,
        monitored_systems_count=2,
        reporting_systems=0,
        protected_assets=2,
        last_poll_at=None,
        last_heartbeat_at=now,
        last_telemetry_at=None,
        telemetry_kind=None,
        last_detection_at=None,
        evidence_source='live',
        status_reason='no_reporting_systems',
        configuration_reason=None,
        valid_protected_asset_count=2,
        linked_monitored_system_count=2,
        persisted_enabled_config_count=2,
        valid_target_system_link_count=2,
        telemetry_window_seconds=300,
    )
    assert summary['freshness_status'] == 'unavailable'
    assert summary['last_telemetry_at'] is None
    assert 'heartbeat_without_telemetry_timestamp' in summary['contradiction_flags']


def test_poll_recent_without_telemetry_sets_unavailable_freshness() -> None:
    now = _now()
    summary = build_workspace_monitoring_summary(
        now=now,
        workspace_configured=True,
        monitoring_mode='live',
        runtime_status='idle',
        configured_systems=2,
        monitored_systems_count=2,
        reporting_systems=0,
        protected_assets=2,
        last_poll_at=now,
        last_heartbeat_at=None,
        last_telemetry_at=None,
        telemetry_kind=None,
        last_detection_at=None,
        evidence_source='live',
        status_reason='no_reporting_systems',
        configuration_reason=None,
        valid_protected_asset_count=2,
        linked_monitored_system_count=2,
        persisted_enabled_config_count=2,
        valid_target_system_link_count=2,
        telemetry_window_seconds=300,
    )
    assert summary['freshness_status'] == 'unavailable'
    assert summary['last_telemetry_at'] is None
    assert 'poll_without_telemetry_timestamp' in summary['contradiction_flags']


def test_telemetry_recent_with_reporting_is_fresh_and_high_confidence() -> None:
    now = _now()
    summary = build_workspace_monitoring_summary(
        now=now,
        workspace_configured=True,
        monitoring_mode='live',
        runtime_status='healthy',
        configured_systems=3,
        monitored_systems_count=3,
        reporting_systems=2,
        protected_assets=2,
        last_poll_at=now,
        last_heartbeat_at=now,
        last_telemetry_at=now - timedelta(seconds=40),
        telemetry_kind='target_event',
        last_detection_at=now - timedelta(seconds=20),
        evidence_source='live',
        status_reason=None,
        configuration_reason=None,
        valid_protected_asset_count=2,
        linked_monitored_system_count=3,
        persisted_enabled_config_count=3,
        valid_target_system_link_count=3,
        telemetry_window_seconds=300,
    )
    assert summary['freshness_status'] == 'fresh'
    assert summary['confidence_status'] == 'high'
    assert summary['contradiction_flags'] == []


def test_workspace_unconfigured_cannot_report_monitored_or_protected_counts() -> None:
    now = _now()
    summary = build_workspace_monitoring_summary(
        now=now,
        workspace_configured=False,
        monitoring_mode='offline',
        runtime_status='offline',
        configured_systems=0,
        monitored_systems_count=1,
        reporting_systems=0,
        protected_assets=1,
        last_poll_at=now,
        last_heartbeat_at=None,
        last_telemetry_at=None,
        telemetry_kind=None,
        last_detection_at=None,
        evidence_source='none',
        status_reason='workspace_not_configured',
        configuration_reason='target_system_linkage_invalid',
        valid_protected_asset_count=0,
        linked_monitored_system_count=0,
        persisted_enabled_config_count=1,
        valid_target_system_link_count=0,
        telemetry_window_seconds=300,
    )
    assert 'workspace_unconfigured_with_coverage' in summary['contradiction_flags']


def test_healthy_status_without_reporting_is_flagged() -> None:
    now = _now()
    summary = build_workspace_monitoring_summary(
        now=now,
        workspace_configured=True,
        monitoring_mode='live',
        runtime_status='healthy',
        configured_systems=3,
        monitored_systems_count=3,
        reporting_systems=0,
        protected_assets=3,
        last_poll_at=now,
        last_heartbeat_at=now,
        last_telemetry_at=None,
        telemetry_kind=None,
        last_detection_at=None,
        evidence_source='live',
        status_reason='no_reporting_systems',
        configuration_reason=None,
        valid_protected_asset_count=3,
        linked_monitored_system_count=3,
        persisted_enabled_config_count=3,
        valid_target_system_link_count=3,
        telemetry_window_seconds=300,
    )
    assert 'healthy_without_reporting_systems' in summary['contradiction_flags']


def test_workspace_configured_with_missing_required_link_counts_is_flagged() -> None:
    now = _now()
    summary = build_workspace_monitoring_summary(
        now=now,
        workspace_configured=True,
        monitoring_mode='live',
        runtime_status='healthy',
        configured_systems=1,
        monitored_systems_count=1,
        reporting_systems=1,
        protected_assets=1,
        last_poll_at=now,
        last_heartbeat_at=now,
        last_telemetry_at=now,
        telemetry_kind='target_event',
        last_detection_at=now,
        evidence_source='live',
        status_reason=None,
        configuration_reason=None,
        valid_protected_asset_count=1,
        linked_monitored_system_count=1,
        persisted_enabled_config_count=0,
        valid_target_system_link_count=1,
        telemetry_window_seconds=300,
    )
    assert 'workspace_configured_missing_required_links' in summary['contradiction_flags']


def test_simulator_telemetry_timestamp_is_preserved_from_persisted_event() -> None:
    now = _now()
    simulator_event_at = now - timedelta(seconds=45)
    summary = build_workspace_monitoring_summary(
        now=now,
        workspace_configured=True,
        monitoring_mode='simulator',
        runtime_status='healthy',
        configured_systems=1,
        monitored_systems_count=1,
        reporting_systems=1,
        protected_assets=1,
        last_poll_at=now,
        last_heartbeat_at=now,
        last_telemetry_at=simulator_event_at,
        telemetry_kind='target_event',
        last_detection_at=None,
        evidence_source='simulator',
        status_reason=None,
        configuration_reason=None,
        valid_protected_asset_count=1,
        linked_monitored_system_count=1,
        persisted_enabled_config_count=1,
        valid_target_system_link_count=1,
        telemetry_window_seconds=300,
    )
    assert summary['last_telemetry_at'] == simulator_event_at.isoformat()
    assert summary['telemetry_kind'] == 'target_event'
    assert summary['freshness_status'] == 'fresh'
    assert summary['confidence_status'] == 'medium'
