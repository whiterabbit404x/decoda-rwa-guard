from __future__ import annotations

from datetime import datetime
from typing import Any


CANONICAL_RUNTIME_STATUS = {'provisioning', 'healthy', 'degraded', 'idle', 'failed', 'disabled', 'offline'}
CANONICAL_MONITORING_STATUS = {'active', 'idle', 'degraded', 'offline', 'error'}
CANONICAL_TELEMETRY_FRESHNESS = {'fresh', 'stale', 'unavailable'}
CANONICAL_CONFIDENCE = {'high', 'medium', 'low', 'unavailable'}
CANONICAL_EVIDENCE_SOURCE = {'live', 'simulator', 'replay', 'none'}


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _normalized_runtime_status(value: str) -> str:
    return value if value in CANONICAL_RUNTIME_STATUS else 'offline'


def _normalized_monitoring_status(*, runtime_status: str, monitoring_status: str | None = None) -> str:
    if monitoring_status in CANONICAL_MONITORING_STATUS:
        return monitoring_status
    if runtime_status == 'healthy':
        return 'active'
    if runtime_status in {'provisioning', 'idle'}:
        return 'idle'
    if runtime_status == 'degraded':
        return 'degraded'
    if runtime_status == 'failed':
        return 'error'
    return 'offline'


def _normalized_telemetry_freshness(value: str) -> str:
    return value if value in CANONICAL_TELEMETRY_FRESHNESS else 'unavailable'


def _normalized_confidence(value: str) -> str:
    return value if value in CANONICAL_CONFIDENCE else 'unavailable'


def _normalized_evidence_source(value: str) -> str:
    return value if value in CANONICAL_EVIDENCE_SOURCE else 'none'


def build_workspace_monitoring_summary(
    *,
    now: datetime,
    workspace_configured: bool,
    configuration_reason_codes: list[str] | None,
    query_failure_detected: bool,
    schema_drift_detected: bool,
    missing_telemetry_only: bool,
    monitoring_mode: str,
    runtime_status: str,
    configured_systems: int,
    monitored_systems_count: int | None,
    reporting_systems: int,
    protected_assets: int,
    last_poll_at: datetime | None,
    last_heartbeat_at: datetime | None,
    last_telemetry_at: datetime | None,
    last_coverage_telemetry_at: datetime | None,
    telemetry_kind: str | None,
    last_detection_at: datetime | None,
    evidence_source: str,
    status_reason: str | None,
    configuration_reason: str | None,
    valid_protected_asset_count: int,
    linked_monitored_system_count: int,
    persisted_enabled_config_count: int,
    valid_target_system_link_count: int,
    telemetry_window_seconds: int,
    active_alerts_count: int = 0,
    active_incidents_count: int = 0,
) -> dict[str, Any]:
    normalized_monitored = max(int(monitored_systems_count if monitored_systems_count is not None else configured_systems), 0)
    normalized_reporting = max(int(reporting_systems), 0)
    normalized_assets = max(int(protected_assets), 0)
    normalized_runtime = _normalized_runtime_status(runtime_status)
    normalized_monitoring_status = _normalized_monitoring_status(runtime_status=normalized_runtime)
    normalized_evidence = _normalized_evidence_source(evidence_source)
    normalized_telemetry_kind = telemetry_kind if telemetry_kind in {'coverage', 'target_event'} else None
    telemetry_timestamp = None
    if normalized_telemetry_kind in {'coverage', 'target_event'}:
        telemetry_timestamp = last_telemetry_at
        if telemetry_timestamp is None and normalized_telemetry_kind == 'coverage':
            telemetry_timestamp = last_coverage_telemetry_at
    freshness_status = _normalized_telemetry_freshness(
        'fresh'
        if telemetry_timestamp and int((now - telemetry_timestamp).total_seconds()) <= telemetry_window_seconds
        else ('stale' if telemetry_timestamp else 'unavailable')
    )
    confidence_status = _normalized_confidence(
        'high'
        if (
            last_coverage_telemetry_at
            and workspace_configured
            and normalized_reporting > 0
            and normalized_evidence == 'live'
            and int((now - last_coverage_telemetry_at).total_seconds()) <= telemetry_window_seconds
        )
        else 'unavailable'
    )
    evidence_source_summary = (
        'live'
        if (
            normalized_evidence == 'live'
            and workspace_configured
            and normalized_reporting > 0
            and freshness_status == 'fresh'
        )
        else normalized_evidence
    )
    contradiction_flags: list[str] = []
    if normalized_runtime == 'offline' and freshness_status == 'fresh':
        contradiction_flags.append('offline_with_current_telemetry')
    if freshness_status == 'unavailable' and confidence_status == 'high':
        contradiction_flags.append('telemetry_unavailable_with_high_confidence')
    if last_heartbeat_at and telemetry_timestamp is None:
        contradiction_flags.append('heartbeat_without_telemetry_timestamp')
    if last_poll_at and telemetry_timestamp is None:
        contradiction_flags.append('poll_without_telemetry_timestamp')
    if normalized_reporting == 0 and normalized_runtime == 'healthy':
        contradiction_flags.append('healthy_without_reporting_systems')
    live_telemetry_verified = normalized_evidence == 'live' and confidence_status == 'high'
    if live_telemetry_verified and telemetry_timestamp is None:
        contradiction_flags.append('live_telemetry_verified_without_timestamp')
    idle_with_continuous_healthy_monitoring = (
        normalized_runtime == 'idle'
        and normalized_reporting > 0
        and freshness_status == 'fresh'
        and confidence_status == 'high'
        and normalized_evidence == 'live'
    )
    if idle_with_continuous_healthy_monitoring and not status_reason:
        contradiction_flags.append('idle_runtime_with_active_monitoring_claim')
    workspace_has_coverage = (
        normalized_monitored > 0
        or normalized_reporting > 0
        or normalized_assets > 0
        or telemetry_timestamp is not None
        or last_poll_at is not None
        or last_heartbeat_at is not None
    )
    if not workspace_configured and workspace_has_coverage:
        contradiction_flags.append('workspace_unconfigured_with_coverage')
    if (
        workspace_configured
        and (
            valid_protected_asset_count <= 0
            or linked_monitored_system_count <= 0
            or persisted_enabled_config_count <= 0
            or valid_target_system_link_count <= 0
        )
    ):
        contradiction_flags.append('workspace_configured_missing_required_links')
    contradiction_flags = sorted(set(contradiction_flags))
    normalized_status_reason = str(status_reason).strip() if isinstance(status_reason, str) and status_reason.strip() else None
    resolved_status_reason = normalized_status_reason or (f'guard:{contradiction_flags[0]}' if contradiction_flags else None)
    field_reason_codes = {
        'protected_assets': [],
        'configured_systems': [],
        'reporting_systems': [],
        'last_poll_at': [],
        'last_heartbeat_at': [],
        'last_telemetry_at': [],
    }
    if not workspace_configured and not workspace_has_coverage:
        for key in field_reason_codes:
            field_reason_codes[key] = ['unconfigured_workspace']
    normalized_reason_codes = [str(code) for code in (configuration_reason_codes or []) if str(code).strip()]
    return {
        'workspace_configured': bool(workspace_configured),
        'monitoring_mode': monitoring_mode,
        'runtime_status': normalized_runtime,
        'monitoring_status': normalized_monitoring_status,
        'configured_systems': max(int(configured_systems), 0),
        'reporting_systems': normalized_reporting,
        'protected_assets': normalized_assets,
        'coverage_counts': {
            'configured_systems': max(int(configured_systems), 0),
            'monitored_systems_count': normalized_monitored,
            'reporting_systems': normalized_reporting,
            'protected_assets': normalized_assets,
        },
        'last_poll_at': _isoformat(last_poll_at),
        'last_heartbeat_at': _isoformat(last_heartbeat_at),
        'last_telemetry_at': _isoformat(telemetry_timestamp),
        'last_coverage_telemetry_at': _isoformat(last_coverage_telemetry_at),
        'telemetry_kind': normalized_telemetry_kind,
        'last_detection_at': _isoformat(last_detection_at),
        'telemetry_freshness': freshness_status,
        'freshness': freshness_status,
        'freshness_status': freshness_status,
        'confidence': confidence_status,
        'confidence_level': confidence_status,
        'confidence_status': confidence_status,
        'reporting_systems_count': normalized_reporting,
        'monitored_systems_count': normalized_monitored,
        'protected_assets_count': normalized_assets,
        'active_alerts_count': max(int(active_alerts_count), 0),
        'active_incidents_count': max(int(active_incidents_count), 0),
        'evidence_source_summary': evidence_source_summary,
        'evidence_source': normalized_evidence,
        'configuration_reason': configuration_reason,
        'configuration_reason_codes': normalized_reason_codes,
        'field_reason_codes': field_reason_codes,
        'valid_protected_asset_count': max(int(valid_protected_asset_count), 0),
        'linked_monitored_system_count': max(int(linked_monitored_system_count), 0),
        'persisted_enabled_config_count': max(int(persisted_enabled_config_count), 0),
        'valid_target_system_link_count': max(int(valid_target_system_link_count), 0),
        'status_reason': resolved_status_reason,
        'contradiction_flags': contradiction_flags,
    }


def build_workspace_monitoring_summary_fallback(
    *,
    status_reason: str,
    workspace_configured: bool = False,
    runtime_status: str = 'offline',
    monitoring_status: str | None = None,
    telemetry_freshness: str = 'unavailable',
    confidence: str = 'unavailable',
) -> dict[str, Any]:
    normalized_runtime = _normalized_runtime_status(runtime_status)
    return {
        'workspace_configured': bool(workspace_configured),
        'monitoring_mode': 'offline',
        'runtime_status': normalized_runtime,
        'monitoring_status': _normalized_monitoring_status(runtime_status=normalized_runtime, monitoring_status=monitoring_status),
        'configured_systems': 0,
        'reporting_systems': 0,
        'protected_assets': 0,
        'coverage_counts': {'configured_systems': 0, 'monitored_systems_count': 0, 'reporting_systems': 0, 'protected_assets': 0},
        'last_poll_at': None,
        'last_heartbeat_at': None,
        'last_telemetry_at': None,
        'last_coverage_telemetry_at': None,
        'telemetry_kind': None,
        'last_detection_at': None,
        'telemetry_freshness': _normalized_telemetry_freshness(telemetry_freshness),
        'freshness': _normalized_telemetry_freshness(telemetry_freshness),
        'freshness_status': _normalized_telemetry_freshness(telemetry_freshness),
        'confidence': _normalized_confidence(confidence),
        'confidence_level': _normalized_confidence(confidence),
        'confidence_status': _normalized_confidence(confidence),
        'reporting_systems_count': 0,
        'monitored_systems_count': 0,
        'protected_assets_count': 0,
        'active_alerts_count': 0,
        'active_incidents_count': 0,
        'evidence_source_summary': 'none',
        'evidence_source': 'none',
        'configuration_reason': status_reason,
        'configuration_reason_codes': [status_reason] if status_reason else [],
        'field_reason_codes': {
            'protected_assets': ['unconfigured_workspace'],
            'configured_systems': ['unconfigured_workspace'],
            'reporting_systems': ['unconfigured_workspace'],
            'last_poll_at': ['unconfigured_workspace'],
            'last_heartbeat_at': ['unconfigured_workspace'],
            'last_telemetry_at': ['unconfigured_workspace'],
        },
        'valid_protected_asset_count': 0,
        'linked_monitored_system_count': 0,
        'persisted_enabled_config_count': 0,
        'valid_target_system_link_count': 0,
        'status_reason': status_reason,
        'contradiction_flags': [],
    }
