from __future__ import annotations

from datetime import datetime
from typing import Any


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def build_workspace_monitoring_summary(
    *,
    now: datetime,
    workspace_configured: bool,
    monitoring_mode: str,
    runtime_status: str,
    configured_systems: int,
    reporting_systems: int,
    protected_assets: int,
    last_poll_at: datetime | None,
    last_heartbeat_at: datetime | None,
    last_telemetry_at: datetime | None,
    last_detection_at: datetime | None,
    evidence_source: str,
    status_reason: str | None,
    telemetry_window_seconds: int,
) -> dict[str, Any]:
    normalized_configured = max(int(configured_systems), 0)
    normalized_reporting = max(int(reporting_systems), 0)
    normalized_assets = max(int(protected_assets), 0)
    normalized_mode = monitoring_mode if monitoring_mode in {'live', 'simulator', 'offline', 'unavailable'} else 'unavailable'
    normalized_runtime = runtime_status if runtime_status in {'provisioning', 'healthy', 'degraded', 'idle', 'failed', 'disabled', 'offline'} else 'offline'
    normalized_evidence = evidence_source if evidence_source in {'live', 'simulator', 'replay', 'none'} else 'none'
    telemetry_timestamp = (
        last_telemetry_at
        if workspace_configured and normalized_reporting > 0 and normalized_mode == 'live' and normalized_evidence == 'live'
        else None
    )
    freshness_status = (
        'fresh'
        if telemetry_timestamp and int((now - telemetry_timestamp).total_seconds()) <= telemetry_window_seconds
        else ('stale' if telemetry_timestamp else 'unavailable')
    )
    confidence_status = (
        'high'
        if telemetry_timestamp and normalized_reporting > 0
        else ('medium' if normalized_mode == 'simulator' and normalized_reporting > 0 else ('low' if workspace_configured else 'unavailable'))
    )
    summary = {
        'workspace_configured': bool(workspace_configured),
        'monitoring_mode': normalized_mode,
        'runtime_status': normalized_runtime,
        'configured_systems': normalized_configured,
        'reporting_systems': normalized_reporting,
        'protected_assets': normalized_assets,
        'coverage_state': {
            'configured_systems': normalized_configured,
            'reporting_systems': normalized_reporting,
            'protected_assets': normalized_assets,
        },
        'freshness_status': freshness_status,
        'confidence_status': confidence_status,
        'last_poll_at': _isoformat(last_poll_at),
        'last_heartbeat_at': _isoformat(last_heartbeat_at),
        'last_telemetry_at': _isoformat(telemetry_timestamp),
        'last_detection_at': _isoformat(last_detection_at),
        'evidence_source': normalized_evidence,
        'status_reason': status_reason,
        'contradiction_flags': [],
    }
    if summary['runtime_status'] == 'offline' and summary['last_telemetry_at']:
        summary['contradiction_flags'].append('offline_with_current_telemetry')
    if summary['reporting_systems'] == 0 and summary['runtime_status'] == 'healthy':
        summary['contradiction_flags'].append('healthy_without_reporting_systems')
    if summary['freshness_status'] == 'unavailable' and summary['last_telemetry_at']:
        summary['contradiction_flags'].append('telemetry_unavailable_with_timestamp')
    if summary['freshness_status'] == 'fresh' and summary['last_telemetry_at'] is None:
        summary['contradiction_flags'].append('telemetry_unavailable_marked_fresh')
    if (not summary['workspace_configured']) and (summary['configured_systems'] > 0 or summary['protected_assets'] > 0):
        summary['contradiction_flags'].append('workspace_unconfigured_with_coverage')
    if summary['configured_systems'] == 0 and summary['reporting_systems'] == 0 and summary['last_telemetry_at']:
        summary['contradiction_flags'].append('zero_coverage_with_live_telemetry')
    if (
        summary['last_poll_at']
        and summary['last_telemetry_at'] is None
        and summary['monitoring_mode'] == 'live'
        and summary['evidence_source'] == 'live'
    ):
        summary['contradiction_flags'].append('poll_without_telemetry_timestamp')
    if (
        summary['last_heartbeat_at']
        and summary['last_telemetry_at'] is None
        and summary['monitoring_mode'] == 'live'
        and summary['evidence_source'] == 'live'
    ):
        summary['contradiction_flags'].append('heartbeat_without_telemetry_timestamp')
    return summary
