from __future__ import annotations

from datetime import datetime
from typing import Any


CANONICAL_RUNTIME_STATUS = {'live', 'degraded', 'offline', 'idle'}
CANONICAL_MONITORING_STATUS = {'healthy', 'degraded', 'offline', 'not_configured'}
CANONICAL_TELEMETRY_FRESHNESS = {'fresh', 'stale', 'unavailable'}
CANONICAL_CONFIDENCE = {'high', 'medium', 'low', 'unavailable'}
CANONICAL_EVIDENCE_SOURCE = {'live_provider', 'simulator', 'none'}
CANONICAL_CONTINUITY_STATUS = {'continuous_live', 'continuous_no_evidence', 'degraded', 'offline', 'idle_no_telemetry'}
CANONICAL_SUMMARY_KEYS = (
    'workspace_configured',
    'runtime_status',
    'monitoring_status',
    'freshness_status',
    'confidence_status',
    'protected_assets',
    'monitored_systems',
    'reporting_systems',
    'last_poll_at',
    'last_heartbeat_at',
    'last_telemetry_at',
    'last_detection_at',
    'reason_codes',
    'next_required_action',
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
    'top_banner_reasons',
    'guard_flags',
    'status_reason',
    'db_failure_classification',
    'db_failure_reason',
    'runtime_setup_chain',
)

RUNTIME_SETUP_STEP_ORDER = (
    'workspace_created',
    'asset_created',
    'asset_verified',
    'monitoring_target_created',
    'monitored_system_created',
    'worker_reporting',
    'telemetry_received',
    'detection_created',
    'alert_created',
    'incident_opened',
    'response_ready',
    'evidence_export_ready',
)

NEXT_ACTION_BY_STEP = {
    'asset_created': 'add_asset',
    'asset_verified': 'verify_asset',
    'monitoring_target_created': 'create_monitoring_target',
    'monitored_system_created': 'enable_monitored_system',
    'worker_reporting': 'start_simulator_signal',
    'telemetry_received': 'view_detection',
    'detection_created': 'view_detection',
    'alert_created': 'open_incident',
    'incident_opened': 'open_incident',
    'response_ready': 'export_evidence_package',
    'evidence_export_ready': 'export_evidence_package',
}
HARD_GUARD_FLAGS = {
    'offline_with_current_telemetry',
    'telemetry_unavailable_with_high_confidence',
    'live_monitoring_without_reporting_systems',
    'live_telemetry_verified_without_timestamp',
    'idle_runtime_with_active_monitoring_claim',
    'workspace_unconfigured_with_reporting_systems',
    'evidence_none_with_high_confidence',
    'heartbeat_only_with_live_claim',
    'live_evidence_without_live_telemetry_kind',
    'reporting_coverage_without_target_telemetry',
    'asset_monitoring_attached_but_no_monitored_systems',
    'ui_protected_assets_positive_but_runtime_zero',
    'ui_live_monitoring_claim_without_telemetry',
    'ui_healthy_claim_with_zero_reporting_systems',
    'simulator_evidence_claimed_as_live_provider',
    'alert_exists_without_detection',
    'incident_exists_without_alert',
    'response_action_exists_without_incident',
}
HARD_GUARD_PRIORITY = (
    'offline_with_current_telemetry',
    'telemetry_unavailable_with_high_confidence',
    'live_monitoring_without_reporting_systems',
    'live_telemetry_verified_without_timestamp',
    'idle_runtime_with_active_monitoring_claim',
    'workspace_unconfigured_with_reporting_systems',
    'evidence_none_with_high_confidence',
    'heartbeat_only_with_live_claim',
    'live_evidence_without_live_telemetry_kind',
    'reporting_coverage_without_target_telemetry',
    'asset_monitoring_attached_but_no_monitored_systems',
    'ui_protected_assets_positive_but_runtime_zero',
    'ui_live_monitoring_claim_without_telemetry',
    'ui_healthy_claim_with_zero_reporting_systems',
    'simulator_evidence_claimed_as_live_provider',
    'alert_exists_without_detection',
    'incident_exists_without_alert',
    'response_action_exists_without_incident',
)


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _normalized_runtime_status(value: str) -> str:
    normalized = str(value or '').strip().lower()
    if normalized in CANONICAL_RUNTIME_STATUS:
        return normalized
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
    workspace_configured: bool,
) -> str:
    if not workspace_configured:
        return 'not_configured'
    if runtime_status == 'offline':
        return 'offline'
    if contradiction_flags:
        return 'degraded'
    if reporting_systems_count <= 0:
        return 'degraded'
    if runtime_status == 'live' and telemetry_freshness == 'fresh':
        return 'healthy'
    return 'degraded'


def _normalized_telemetry_freshness(value: str) -> str:
    return value if value in CANONICAL_TELEMETRY_FRESHNESS else 'unavailable'


def _normalized_confidence(value: str) -> str:
    return value if value in CANONICAL_CONFIDENCE else 'unavailable'


def _normalized_evidence_source(value: str) -> str:
    normalized = str(value or '').strip().lower()
    if normalized in {'live', 'provider', 'live_provider'}:
        return 'live_provider'
    if normalized in {'simulator', 'replay'}:
        return 'simulator'
    return normalized if normalized in CANONICAL_EVIDENCE_SOURCE else 'none'


def _normalized_continuity_status(value: str) -> str:
    return value if value in CANONICAL_CONTINUITY_STATUS else 'idle_no_telemetry'




def _build_v2_summary(payload: dict[str, Any]) -> dict[str, Any]:
    runtime_chain = dict(payload.get('runtime_setup_chain') or {})
    workflow_steps = list(runtime_chain.get('steps') or payload.get('workflow_steps') or [])
    current_step = runtime_chain.get('current_step') or payload.get('current_step')
    contradiction_flags = sorted({str(flag).strip() for flag in payload.get('contradiction_flags', []) if str(flag).strip()})
    workspace = {
        'id': str(payload.get('workspace_id') or '').strip() or None,
        'name': str(payload.get('workspace_name') or '').strip() or None,
        'configured': bool(payload.get('workspace_configured', False)),
    }
    statuses = {
        'runtime': _normalized_runtime_status(str(payload.get('runtime_status', 'offline'))),
        'monitoring': _normalized_monitoring_status(
            runtime_status=_normalized_runtime_status(str(payload.get('runtime_status', 'offline'))),
            reporting_systems_count=max(int(payload.get('reporting_systems', payload.get('reporting_systems_count', 0)) or 0), 0),
            telemetry_freshness=_normalized_telemetry_freshness(str(payload.get('freshness_status', payload.get('telemetry_freshness', 'unavailable')))),
            contradiction_flags=contradiction_flags,
            workspace_configured=bool(payload.get('workspace_configured', False)),
        ),
        'freshness': _normalized_telemetry_freshness(str(payload.get('freshness_status', payload.get('telemetry_freshness', 'unavailable')))),
        'confidence': _normalized_confidence(str(payload.get('confidence_status', payload.get('confidence', 'unavailable')))),
    }
    counts = {
        'protected_assets': max(int(payload.get('protected_assets', payload.get('protected_assets_count', 0)) or 0), 0),
        'monitoring_targets': max(int(payload.get('monitored_systems', payload.get('monitored_systems_count', 0)) or 0), 0),
        'monitored_systems': max(int(payload.get('monitored_systems', payload.get('monitored_systems_count', 0)) or 0), 0),
        'reporting_systems': max(int(payload.get('reporting_systems', payload.get('reporting_systems_count', 0)) or 0), 0),
        'active_alerts': max(int(payload.get('active_alerts_count', 0) or 0), 0),
        'open_incidents': max(int(payload.get('active_incidents_count', 0) or 0), 0),
    }
    timestamps = {
        'last_poll_at': payload.get('last_poll_at') if isinstance(payload.get('last_poll_at'), str) else None,
        'last_heartbeat_at': payload.get('last_heartbeat_at') if isinstance(payload.get('last_heartbeat_at'), str) else None,
        'last_telemetry_at': payload.get('last_telemetry_at') if isinstance(payload.get('last_telemetry_at'), str) else None,
        'last_detection_at': payload.get('last_detection_at') if isinstance(payload.get('last_detection_at'), str) else None,
    }
    return {
        'workspace': workspace,
        'workspace_id': workspace['id'],
        'workspace_name': workspace['name'],
        'workspace_configured': bool(payload.get('workspace_configured', False)),
        'monitoring_status': statuses['monitoring'],
        'freshness_status': _normalized_telemetry_freshness(str(payload.get('freshness_status', payload.get('telemetry_freshness', 'unavailable')))),
        'confidence_status': _normalized_confidence(str(payload.get('confidence_status', payload.get('confidence', 'unavailable')))),
        'protected_assets': counts['protected_assets'],
        'monitoring_targets': counts['monitoring_targets'],
        'monitored_systems': counts['monitored_systems'],
        'reporting_systems': counts['reporting_systems'],
        'active_alerts': counts['active_alerts'],
        'open_incidents': counts['open_incidents'],
        'timestamps': timestamps,
        'evidence_source': _normalized_evidence_source(str(payload.get('evidence_source', payload.get('evidence_source_summary', 'none')))),
        'counts': counts,
        'statuses': statuses,
        'reason_codes': sorted({str(code).strip() for code in payload.get('reason_codes', []) if str(code).strip()}),
        'contradiction_flags': contradiction_flags,
        'next_required_action': str(payload.get('next_required_action')).strip() if isinstance(payload.get('next_required_action'), str) and str(payload.get('next_required_action')).strip() else 'review_reason_codes',
        'workflow_steps': workflow_steps,
        'current_step': str(current_step or 'asset_created'),
    }
def _canonical_summary(payload: dict[str, Any]) -> dict[str, Any]:
    canonical = {
        'workspace_configured': workspace['configured'],
        'runtime_status': _normalized_runtime_status(str(payload.get('runtime_status', 'offline'))),
        'monitoring_status': (
            payload.get('monitoring_status')
            if payload.get('monitoring_status') in CANONICAL_MONITORING_STATUS
            else 'not_configured'
        ),
        'freshness_status': statuses['freshness'],
        'confidence_status': statuses['confidence'],
        'protected_assets': max(int(payload.get('protected_assets', payload.get('protected_assets_count', 0))), 0),
        'monitored_systems': max(int(payload.get('monitored_systems', payload.get('monitored_systems_count', 0))), 0),
        'reporting_systems': max(int(payload.get('reporting_systems', payload.get('reporting_systems_count', 0))), 0),
        'last_poll_at': payload.get('last_poll_at') if isinstance(payload.get('last_poll_at'), str) else None,
        'last_heartbeat_at': payload.get('last_heartbeat_at') if isinstance(payload.get('last_heartbeat_at'), str) else None,
        'last_telemetry_at': payload.get('last_telemetry_at') if isinstance(payload.get('last_telemetry_at'), str) else None,
        'last_detection_at': payload.get('last_detection_at') if isinstance(payload.get('last_detection_at'), str) else None,
        'telemetry_freshness': _normalized_telemetry_freshness(str(payload.get('telemetry_freshness', 'unavailable'))),
        'confidence': _normalized_confidence(str(payload.get('confidence', 'unavailable'))),
        'reporting_systems_count': max(int(payload.get('reporting_systems_count', 0)), 0),
        'monitored_systems_count': max(int(payload.get('monitored_systems_count', 0)), 0),
        'protected_assets_count': max(int(payload.get('protected_assets_count', 0)), 0),
        'active_alerts_count': max(int(payload.get('active_alerts_count', 0)), 0),
        'active_incidents_count': max(int(payload.get('active_incidents_count', 0)), 0),
        'evidence_source_summary': _normalized_evidence_source(str(payload.get('evidence_source_summary', payload.get('evidence_source', 'none')))),
        'continuity_status': _normalized_continuity_status(str(payload.get('continuity_status') or 'idle_no_telemetry')),
        'continuity_reason_codes': sorted({str(code).strip() for code in payload.get('continuity_reason_codes', []) if str(code).strip()}),
        'continuity_signals': dict(payload.get('continuity_signals') or {}),
        'continuity_slo_pass': bool(payload.get('continuity_slo_pass', False)),
        'heartbeat_age_seconds': payload.get('heartbeat_age_seconds') if payload.get('heartbeat_age_seconds') is None else max(int(payload.get('heartbeat_age_seconds') or 0), 0),
        'event_ingestion_age_seconds': payload.get('event_ingestion_age_seconds') if payload.get('event_ingestion_age_seconds') is None else max(int(payload.get('event_ingestion_age_seconds') or 0), 0),
        'detection_eval_age_seconds': payload.get('detection_eval_age_seconds') if payload.get('detection_eval_age_seconds') is None else max(int(payload.get('detection_eval_age_seconds') or 0), 0),
        'required_thresholds_seconds': dict(payload.get('required_thresholds_seconds') or {}),
        'ingestion_freshness': str(payload.get('ingestion_freshness') or 'missing').strip().lower(),
        'detection_pipeline_freshness': str(payload.get('detection_pipeline_freshness') or 'missing').strip().lower(),
        'worker_heartbeat_freshness': str(payload.get('worker_heartbeat_freshness') or 'missing').strip().lower(),
        'event_throughput_window': str(payload.get('event_throughput_window') or 'no_events').strip().lower(),
        'event_throughput_window_seconds': max(int(payload.get('event_throughput_window_seconds', 0) or 0), 0),
        'contradiction_flags': sorted({str(flag).strip() for flag in payload.get('contradiction_flags', []) if str(flag).strip()}),
        'top_banner_reasons': [str(reason).strip() for reason in payload.get('top_banner_reasons', []) if str(reason).strip()],
        'guard_flags': sorted({str(flag).strip() for flag in payload.get('guard_flags', []) if str(flag).strip()}),
        'reason_codes': sorted({str(code).strip() for code in payload.get('reason_codes', []) if str(code).strip()}),
        'next_required_action': str(payload.get('next_required_action')).strip() if isinstance(payload.get('next_required_action'), str) and str(payload.get('next_required_action')).strip() else 'review_reason_codes',
        'status_reason': str(payload.get('status_reason')).strip() if isinstance(payload.get('status_reason'), str) and str(payload.get('status_reason')).strip() else None,
        'db_failure_classification': str(payload.get('db_failure_classification')).strip() if isinstance(payload.get('db_failure_classification'), str) and str(payload.get('db_failure_classification')).strip() else None,
        'db_failure_reason': str(payload.get('db_failure_reason')).strip() if isinstance(payload.get('db_failure_reason'), str) and str(payload.get('db_failure_reason')).strip() else None,
        'runtime_setup_chain': dict(payload.get('runtime_setup_chain') or {}),
    }
    canonical_v2 = _build_v2_summary(canonical)
    canonical['summary_v2'] = canonical_v2
    canonical['monitoring_targets'] = canonical_v2['monitoring_targets']
    canonical['active_alerts'] = canonical_v2['active_alerts']
    canonical['open_incidents'] = canonical_v2['open_incidents']
    canonical['evidence_source'] = canonical_v2['evidence_source']
    canonical['workflow_steps'] = canonical_v2['workflow_steps']
    canonical['current_step'] = canonical_v2['current_step']
    return {**{key: canonical[key] for key in CANONICAL_SUMMARY_KEYS}, **{
        'summary_v2': canonical['summary_v2'],
        'monitoring_targets': canonical['monitoring_targets'],
        'active_alerts': canonical['active_alerts'],
        'open_incidents': canonical['open_incidents'],
        'evidence_source': canonical['evidence_source'],
        'workflow_steps': canonical['workflow_steps'],
        'current_step': canonical['current_step'],
    }}


def resolve_next_required_action(runtime_setup_chain: dict[str, Any] | None) -> str:
    chain = runtime_setup_chain if isinstance(runtime_setup_chain, dict) else {}
    steps = chain.get('steps') if isinstance(chain.get('steps'), list) else []
    for step in steps:
        if not isinstance(step, dict):
            continue
        if str(step.get('status') or 'pending').strip().lower() == 'complete':
            continue
        step_id = str(step.get('id') or '').strip()
        if step_id:
            return NEXT_ACTION_BY_STEP.get(step_id, 'review_reason_codes')
    return 'review_reason_codes'


def build_runtime_setup_chain(*, counters: dict[str, int], timestamps: dict[str, str | None]) -> dict[str, Any]:
    steps: list[dict[str, str]] = []
    for step_id in RUNTIME_SETUP_STEP_ORDER:
        status = 'pending'
        reason = 'Awaiting prerequisite canonical records.'
        if step_id == 'workspace_created':
            status = 'complete' if counters.get('workspaces_count', 0) > 0 else 'pending'
            reason = 'Workspace context is available.' if status == 'complete' else 'Create or select a workspace first.'
        elif step_id == 'asset_created':
            status = 'complete' if counters.get('assets_count', 0) > 0 else 'pending'
            reason = 'Protected assets exist in assets table.' if status == 'complete' else 'Add at least one protected asset.'
        elif step_id == 'asset_verified':
            status = 'complete' if counters.get('verified_assets_count', 0) > 0 else ('blocked' if counters.get('assets_count', 0) > 0 else 'pending')
            reason = 'At least one asset has canonical verification status.' if status == 'complete' else ('Assets exist but none are verified yet.' if status == 'blocked' else 'Create an asset before verification.')
        elif step_id == 'monitoring_target_created':
            status = 'complete' if counters.get('targets_count', 0) > 0 else ('blocked' if counters.get('verified_assets_count', 0) > 0 else 'pending')
            reason = 'Targets exist in targets table.' if status == 'complete' else ('Verified assets exist but no monitoring target is linked yet.' if status == 'blocked' else 'Verify an asset first.')
        elif step_id == 'monitored_system_created':
            status = 'complete' if counters.get('monitored_systems_count', 0) > 0 else ('blocked' if counters.get('targets_count', 0) > 0 else 'pending')
            reason = 'Monitored systems exist in monitored_systems table.' if status == 'complete' else ('Targets exist but monitored systems are not configured.' if status == 'blocked' else 'Create a monitoring target first.')
        elif step_id == 'worker_reporting':
            status = 'complete' if timestamps.get('last_heartbeat_at') else ('blocked' if counters.get('enabled_monitored_systems_count', 0) > 0 else 'pending')
            reason = 'Worker heartbeat/poll timestamps are present.' if status == 'complete' else ('Enabled monitored systems exist but no worker heartbeat yet.' if status == 'blocked' else 'Enable monitored systems first.')
        elif step_id == 'telemetry_received':
            status = 'complete' if timestamps.get('last_telemetry_at') else ('blocked' if timestamps.get('last_heartbeat_at') else 'pending')
            reason = 'Telemetry events recorded in telemetry_events table.' if status == 'complete' else ('Worker is reporting but telemetry events are still missing.' if status == 'blocked' else 'Start worker reporting and simulator signals first.')
        elif step_id == 'detection_created':
            status = 'complete' if counters.get('detections_count', 0) > 0 else ('blocked' if timestamps.get('last_telemetry_at') else 'pending')
            reason = 'Detections exist in detections table.' if status == 'complete' else ('Telemetry exists but no detections were created yet.' if status == 'blocked' else 'Wait for telemetry ingestion first.')
        elif step_id == 'alert_created':
            status = 'complete' if counters.get('alerts_count', 0) > 0 else ('blocked' if counters.get('detections_count', 0) > 0 else 'pending')
            reason = 'Alerts exist in alerts table.' if status == 'complete' else ('Detections exist but no alerts are open/recorded yet.' if status == 'blocked' else 'Wait for detection creation first.')
        elif step_id == 'incident_opened':
            status = 'complete' if counters.get('incidents_count', 0) > 0 else ('blocked' if counters.get('alerts_count', 0) > 0 else 'pending')
            reason = 'Incidents exist in incidents table.' if status == 'complete' else ('Alerts exist but no incident is opened yet.' if status == 'blocked' else 'Wait for alert creation first.')
        elif step_id == 'response_ready':
            ready = counters.get('response_actions_count', 0) > 0 or counters.get('evidence_count', 0) > 0
            status = 'complete' if ready else ('blocked' if counters.get('incidents_count', 0) > 0 else 'pending')
            reason = 'Response actions/evidence are available.' if status == 'complete' else ('Incident is open but response action/evidence is missing.' if status == 'blocked' else 'Open an incident first.')
        elif step_id == 'evidence_export_ready':
            status = 'complete' if counters.get('evidence_count', 0) > 0 else ('blocked' if counters.get('response_actions_count', 0) > 0 else 'pending')
            reason = 'Evidence export artifacts are available.' if status == 'complete' else ('Response is ready but evidence export package is not available yet.' if status == 'blocked' else 'Prepare response evidence first.')
        steps.append({'id': step_id, 'status': status, 'reason': reason})
    current_step = next((step['id'] for step in steps if step['status'] != 'complete'), RUNTIME_SETUP_STEP_ORDER[-1])
    return {'current_step': current_step, 'steps': steps}


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
    response_actions_count: int = 0,
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
            and normalized_evidence == 'live_provider'
            and int((now - last_coverage_telemetry_at).total_seconds()) <= telemetry_window_seconds
        )
        else 'unavailable'
    )
    evidence_source_summary = (
        'live_provider'
        if (
            normalized_evidence == 'live_provider'
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
            and normalized_evidence == 'live_provider'
        )
    )
    if normalized_reporting == 0 and monitoring_claimed_healthy:
        contradiction_flags.append('live_monitoring_without_reporting_systems')
    live_telemetry_verified = normalized_evidence == 'live_provider' and confidence_status == 'high'
    if live_telemetry_verified and telemetry_timestamp is None:
        contradiction_flags.append('live_telemetry_verified_without_timestamp')
    if not workspace_configured and normalized_reporting > 0:
        contradiction_flags.append('workspace_unconfigured_with_reporting_systems')
    if normalized_evidence == 'none' and confidence_status == 'high':
        contradiction_flags.append('evidence_none_with_high_confidence')
    if (
        last_heartbeat_at is not None
        and telemetry_timestamp is None
        and normalized_runtime in {'live'}
    ):
        contradiction_flags.append('heartbeat_only_with_live_claim')
    if normalized_evidence == 'live_provider' and normalized_telemetry_kind not in {'target_event', 'coverage'}:
        contradiction_flags.append('live_evidence_without_live_telemetry_kind')
    if (
        normalized_reporting > 0
        and normalized_runtime == 'live'
        and normalized_evidence == 'live_provider'
        and normalized_telemetry_kind == 'coverage'
        and last_telemetry_at is None
    ):
        contradiction_flags.append('reporting_coverage_without_target_telemetry')
    normalized_status_reason = str(status_reason).strip() if isinstance(status_reason, str) and status_reason.strip() else None
    db_persistence_is_available = bool(db_persistence_available)
    degraded_reason_explicit = normalized_status_reason is not None and normalized_status_reason.startswith('runtime_status_degraded:')
    idle_with_continuous_healthy_monitoring = (
        normalized_runtime == 'idle'
        and normalized_reporting > 0
        and freshness_status == 'fresh'
        and confidence_status == 'high'
        and normalized_evidence == 'live_provider'
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
    if normalized_assets > 0 and normalized_monitored <= 0:
        contradiction_flags.append('asset_monitoring_attached_but_no_monitored_systems')
    if normalized_assets <= 0 and protected_assets > 0:
        contradiction_flags.append('ui_protected_assets_positive_but_runtime_zero')
    if normalized_reporting == 0 and normalized_runtime == 'live':
        contradiction_flags.append('ui_healthy_claim_with_zero_reporting_systems')
    if telemetry_timestamp is None and normalized_runtime == 'live':
        contradiction_flags.append('ui_live_monitoring_claim_without_telemetry')
    if normalized_evidence == 'simulator' and confidence_status == 'high':
        contradiction_flags.append('simulator_evidence_claimed_as_live_provider')
    if active_alerts_count > 0 and last_detection_at is None:
        contradiction_flags.append('alert_exists_without_detection')
    if active_incidents_count > 0 and int(active_alerts_count) <= 0:
        contradiction_flags.append('incident_exists_without_alert')
    if int(response_actions_count) > 0 and int(active_incidents_count) <= 0:
        contradiction_flags.append('response_action_exists_without_incident')
    contradiction_flags = sorted(set(contradiction_flags))
    guard_flags = sorted(flag for flag in contradiction_flags if flag in HARD_GUARD_FLAGS)
    normalized_monitoring_status = _normalized_monitoring_status(
        runtime_status=normalized_runtime,
        reporting_systems_count=normalized_reporting,
        telemetry_freshness=freshness_status,
        contradiction_flags=contradiction_flags,
        workspace_configured=bool(workspace_configured),
    )
    if not db_persistence_is_available:
        if normalized_runtime == 'live':
            normalized_runtime = 'degraded'
        if normalized_monitoring_status == 'live':
            normalized_monitoring_status = 'degraded'
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
    if prioritized_guard:
        if normalized_runtime == 'live':
            normalized_runtime = 'degraded'
        normalized_monitoring_status = 'offline' if normalized_runtime == 'offline' else 'degraded'
        if freshness_status == 'fresh':
            freshness_status = 'stale'
        confidence_status = 'unavailable'
        evidence_source_summary = 'none'
    resolved_status_reason = f'guard:{prioritized_guard}' if prioritized_guard else normalized_status_reason
    summary = {
        'workspace_configured': bool(workspace_configured),
        'runtime_status': normalized_runtime,
        'monitoring_status': normalized_monitoring_status,
        'freshness_status': freshness_status,
        'confidence_status': confidence_status,
        'protected_assets': normalized_assets,
        'monitored_systems': normalized_monitored,
        'reporting_systems': normalized_reporting,
        'last_poll_at': _isoformat(last_poll_at),
        'last_heartbeat_at': _isoformat(last_heartbeat_at),
        'last_telemetry_at': _isoformat(telemetry_timestamp),
        'last_detection_at': _isoformat(last_detection_at),
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
        'reason_codes': sorted({*configuration_reason_codes, *contradiction_flags}),
        'next_required_action': 'review_reason_codes',
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
        'freshness_status': normalized_freshness,
        'confidence_status': _normalized_confidence(confidence),
        'protected_assets': 0,
        'monitored_systems': 0,
        'reporting_systems': 0,
        'last_poll_at': None,
        'last_heartbeat_at': None,
        'last_telemetry_at': None,
        'last_detection_at': None,
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
        'reason_codes': ['runtime_status_fallback'],
        'next_required_action': 'review_reason_codes',
        'status_reason': status_reason,
        'db_failure_classification': None,
        'db_failure_reason': None,
    }
    return _canonical_summary(summary)
