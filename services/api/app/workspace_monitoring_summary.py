from __future__ import annotations

from datetime import datetime
from typing import Any


CANONICAL_RUNTIME_STATUS = {'live', 'degraded', 'offline', 'idle'}
CANONICAL_MONITORING_STATUS = {'live', 'limited', 'offline'}
CANONICAL_TELEMETRY_FRESHNESS = {'fresh', 'stale', 'unavailable'}
CANONICAL_CONFIDENCE = {'high', 'medium', 'low', 'unavailable'}
CANONICAL_EVIDENCE_SOURCE = {'live', 'simulator', 'replay', 'none'}
CANONICAL_CONTINUITY_STATUS = {'continuous_live', 'continuous_no_evidence', 'degraded', 'offline', 'idle_no_telemetry'}
CANONICAL_SUMMARY_KEYS = (
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
)
HARD_GUARD_FLAGS = {
    'offline_with_current_telemetry',
    'telemetry_unavailable_with_high_confidence',
    'live_monitoring_without_reporting_systems',
    'live_telemetry_verified_without_timestamp',
    'idle_runtime_with_active_monitoring_claim',
}
HARD_GUARD_PRIORITY = (
    'offline_with_current_telemetry',
    'telemetry_unavailable_with_high_confidence',
    'live_monitoring_without_reporting_systems',
    'live_telemetry_verified_without_timestamp',
    'idle_runtime_with_active_monitoring_claim',
)


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _normalized_runtime_status(value: str) -> str:
    normalized = str(value or '').strip().lower()
    if normalized in CANONICAL_RUNTIME_STATUS:
        return normalized
    if normalized in {'healthy'}:
        return 'live'
    if normalized in {'provisioning', 'disabled'}:
        return 'idle'
    if normalized in {'failed'}:
        return 'offline'
    return 'degraded' if normalized == 'degraded' else 'offline'


def _normalized_monitoring_status(
    *,
    runtime_status: str,
    reporting_systems_count: int,
    telemetry_freshness: str,
    contradiction_flags: list[str],
) -> str:
    if runtime_status == 'offline':
        return 'offline'
    if contradiction_flags:
        return 'limited'
    if reporting_systems_count <= 0:
        return 'limited'
    if runtime_status == 'live' and telemetry_freshness == 'fresh':
        return 'live'
    return 'limited'


def _normalized_telemetry_freshness(value: str) -> str:
    return value if value in CANONICAL_TELEMETRY_FRESHNESS else 'unavailable'


def _normalized_confidence(value: str) -> str:
    return value if value in CANONICAL_CONFIDENCE else 'unavailable'


def _normalized_evidence_source(value: str) -> str:
    return value if value in CANONICAL_EVIDENCE_SOURCE else 'none'


def _normalized_continuity_status(value: str) -> str:
    return value if value in CANONICAL_CONTINUITY_STATUS else 'idle_no_telemetry'


def _canonical_summary(payload: dict[str, Any]) -> dict[str, Any]:
    canonical = {
        'workspace_configured': bool(payload.get('workspace_configured', False)),
        'runtime_status': _normalized_runtime_status(str(payload.get('runtime_status', 'offline'))),
        'monitoring_status': (
            payload.get('monitoring_status')
            if payload.get('monitoring_status') in CANONICAL_MONITORING_STATUS
            else 'offline'
        ),
        'last_poll_at': payload.get('last_poll_at') if isinstance(payload.get('last_poll_at'), str) else None,
        'last_heartbeat_at': payload.get('last_heartbeat_at') if isinstance(payload.get('last_heartbeat_at'), str) else None,
        'last_telemetry_at': payload.get('last_telemetry_at') if isinstance(payload.get('last_telemetry_at'), str) else None,
        'telemetry_freshness': _normalized_telemetry_freshness(str(payload.get('telemetry_freshness', 'unavailable'))),
        'confidence': _normalized_confidence(str(payload.get('confidence', 'unavailable'))),
        'reporting_systems_count': max(int(payload.get('reporting_systems_count', 0)), 0),
        'monitored_systems_count': max(int(payload.get('monitored_systems_count', 0)), 0),
        'protected_assets_count': max(int(payload.get('protected_assets_count', 0)), 0),
        'active_alerts_count': max(int(payload.get('active_alerts_count', 0)), 0),
        'active_incidents_count': max(int(payload.get('active_incidents_count', 0)), 0),
        'evidence_source_summary': _normalized_evidence_source(str(payload.get('evidence_source_summary', 'none'))),
        'continuity_status': _normalized_continuity_status(str(payload.get('continuity_status') or 'idle_no_telemetry')),
        'continuity_reason_codes': sorted({str(code).strip() for code in payload.get('continuity_reason_codes', []) if str(code).strip()}),
        'continuity_signals': dict(payload.get('continuity_signals') or {}),
        'ingestion_freshness': str(payload.get('ingestion_freshness') or 'missing').strip().lower(),
        'detection_pipeline_freshness': str(payload.get('detection_pipeline_freshness') or 'missing').strip().lower(),
        'worker_heartbeat_freshness': str(payload.get('worker_heartbeat_freshness') or 'missing').strip().lower(),
        'event_throughput_window': str(payload.get('event_throughput_window') or 'no_events').strip().lower(),
        'event_throughput_window_seconds': max(int(payload.get('event_throughput_window_seconds', 0) or 0), 0),
        'contradiction_flags': sorted({str(flag).strip() for flag in payload.get('contradiction_flags', []) if str(flag).strip()}),
        'guard_flags': sorted({str(flag).strip() for flag in payload.get('guard_flags', []) if str(flag).strip()}),
        'status_reason': str(payload.get('status_reason')).strip() if isinstance(payload.get('status_reason'), str) and str(payload.get('status_reason')).strip() else None,
        'db_failure_classification': str(payload.get('db_failure_classification')).strip() if isinstance(payload.get('db_failure_classification'), str) and str(payload.get('db_failure_classification')).strip() else None,
        'db_failure_reason': str(payload.get('db_failure_reason')).strip() if isinstance(payload.get('db_failure_reason'), str) and str(payload.get('db_failure_reason')).strip() else None,
    }
    return {key: canonical[key] for key in CANONICAL_SUMMARY_KEYS}


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
    db_persistence_available: bool = True,
    db_persistence_reason: str | None = None,
) -> dict[str, Any]:
    normalized_monitored = max(int(monitored_systems_count if monitored_systems_count is not None else configured_systems), 0)
    normalized_reporting = max(int(reporting_systems), 0)
    normalized_assets = max(int(protected_assets), 0)
    normalized_runtime = _normalized_runtime_status(runtime_status)
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
    monitoring_claimed_healthy = (
        normalized_runtime == 'live'
        or (
            freshness_status == 'fresh'
            and confidence_status == 'high'
            and normalized_evidence == 'live'
        )
    )
    if normalized_reporting == 0 and monitoring_claimed_healthy:
        contradiction_flags.append('live_monitoring_without_reporting_systems')
    live_telemetry_verified = normalized_evidence == 'live' and confidence_status == 'high'
    if live_telemetry_verified and telemetry_timestamp is None:
        contradiction_flags.append('live_telemetry_verified_without_timestamp')
    normalized_status_reason = str(status_reason).strip() if isinstance(status_reason, str) and status_reason.strip() else None
    db_persistence_is_available = bool(db_persistence_available)
    degraded_reason_explicit = normalized_status_reason is not None and normalized_status_reason.startswith('runtime_status_degraded:')
    idle_with_continuous_healthy_monitoring = (
        normalized_runtime == 'idle'
        and normalized_reporting > 0
        and freshness_status == 'fresh'
        and confidence_status == 'high'
        and normalized_evidence == 'live'
        and not degraded_reason_explicit
    )
    if idle_with_continuous_healthy_monitoring:
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
    guard_flags = sorted(flag for flag in contradiction_flags if flag in HARD_GUARD_FLAGS)
    normalized_monitoring_status = _normalized_monitoring_status(
        runtime_status=normalized_runtime,
        reporting_systems_count=normalized_reporting,
        telemetry_freshness=freshness_status,
        contradiction_flags=contradiction_flags,
    )
    if not db_persistence_is_available:
        if normalized_runtime == 'live':
            normalized_runtime = 'degraded'
        if normalized_monitoring_status == 'live':
            normalized_monitoring_status = 'limited'
        confidence_status = 'unavailable'
        if freshness_status == 'fresh':
            freshness_status = 'stale'
        db_reason = str(db_persistence_reason or '').strip()
        if not db_reason:
            db_reason = (
                'Monitoring loop running without database access'
                if normalized_status_reason == 'Monitoring loop running without database access'
                else 'Monitoring persistence unavailable'
            )
        normalized_status_reason = db_reason
        normalized_db_failure_reason = db_reason
    else:
        normalized_db_failure_reason = None
    prioritized_guard = next((flag for flag in HARD_GUARD_PRIORITY if flag in guard_flags), None)
    resolved_status_reason = f'guard:{prioritized_guard}' if prioritized_guard else normalized_status_reason
    summary = {
        'workspace_configured': bool(workspace_configured),
        'runtime_status': normalized_runtime,
        'monitoring_status': normalized_monitoring_status,
        'last_poll_at': _isoformat(last_poll_at),
        'last_heartbeat_at': _isoformat(last_heartbeat_at),
        'last_telemetry_at': _isoformat(telemetry_timestamp),
        'telemetry_freshness': freshness_status,
        'confidence': confidence_status,
        'reporting_systems_count': normalized_reporting,
        'monitored_systems_count': normalized_monitored,
        'protected_assets_count': normalized_assets,
        'active_alerts_count': max(int(active_alerts_count), 0),
        'active_incidents_count': max(int(active_incidents_count), 0),
        'evidence_source_summary': evidence_source_summary,
        'continuity_status': 'idle_no_telemetry',
        'continuity_reason_codes': ['continuity_not_evaluated'],
        'continuity_signals': {},
        'ingestion_freshness': 'missing',
        'detection_pipeline_freshness': 'missing',
        'worker_heartbeat_freshness': 'missing',
        'event_throughput_window': 'no_events',
        'event_throughput_window_seconds': max(int(telemetry_window_seconds), 1),
        'contradiction_flags': contradiction_flags,
        'guard_flags': guard_flags,
        'status_reason': resolved_status_reason,
        'db_failure_classification': None if db_persistence_is_available else 'persistence_unavailable',
        'db_failure_reason': normalized_db_failure_reason,
    }
    return _canonical_summary(summary)


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
    normalized_freshness = _normalized_telemetry_freshness(telemetry_freshness)
    normalized_monitoring_status = (
        monitoring_status
        if monitoring_status in CANONICAL_MONITORING_STATUS
        else _normalized_monitoring_status(
            runtime_status=normalized_runtime,
            reporting_systems_count=0,
            telemetry_freshness=normalized_freshness,
            contradiction_flags=[],
        )
    )
    summary = {
        'workspace_configured': bool(workspace_configured),
        'runtime_status': normalized_runtime,
        'monitoring_status': normalized_monitoring_status,
        'last_poll_at': None,
        'last_heartbeat_at': None,
        'last_telemetry_at': None,
        'telemetry_freshness': normalized_freshness,
        'confidence': _normalized_confidence(confidence),
        'reporting_systems_count': 0,
        'monitored_systems_count': 0,
        'protected_assets_count': 0,
        'active_alerts_count': 0,
        'active_incidents_count': 0,
        'evidence_source_summary': 'none',
        'continuity_status': 'offline',
        'continuity_reason_codes': ['runtime_status_fallback'],
        'continuity_signals': {},
        'ingestion_freshness': 'missing',
        'detection_pipeline_freshness': 'missing',
        'worker_heartbeat_freshness': 'missing',
        'event_throughput_window': 'no_events',
        'event_throughput_window_seconds': 0,
        'contradiction_flags': [],
        'guard_flags': [],
        'status_reason': status_reason,
        'db_failure_classification': None,
        'db_failure_reason': None,
    }
    return _canonical_summary(summary)
