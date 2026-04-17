import type { MonitoringRuntimeStatus } from './monitoring-status-contract';

type WorkspaceMonitoringSummary = NonNullable<MonitoringRuntimeStatus['workspace_monitoring_summary']>;

export type WorkspaceMonitoringTruth = {
  workspace_slug: string | null;
  workspace_name: string | null;
  workspace_configured: boolean;
  monitoring_mode: 'live' | 'hybrid' | 'simulator' | 'offline' | 'unavailable';
  runtime_status: 'provisioning' | 'healthy' | 'degraded' | 'idle' | 'failed' | 'disabled' | 'offline';
  configured_systems: number;
  monitored_systems_count: number;
  reporting_systems: number;
  protected_assets_count: number;
  freshness_status: 'fresh' | 'stale' | 'unavailable';
  confidence_status: 'high' | 'medium' | 'low' | 'unavailable';
  last_poll_at: string | null;
  last_heartbeat_at: string | null;
  last_telemetry_at: string | null;
  last_coverage_telemetry_at: string | null;
  telemetry_kind: 'coverage' | 'target_event' | null;
  last_detection_at: string | null;
  evidence_source: 'live' | 'simulator' | 'replay' | 'none';
  configuration_reason_codes: string[];
  status_reason: string | null;
  configuration_reason: string | null;
  valid_protected_asset_count: number;
  linked_monitored_system_count: number;
  persisted_enabled_config_count: number;
  valid_target_system_link_count: number;
  contradiction_flags: string[];
};

const DEFAULT_TRUTH: WorkspaceMonitoringTruth = {
  workspace_slug: null,
  workspace_name: null,
  workspace_configured: false,
  monitoring_mode: 'unavailable',
  runtime_status: 'offline',
  configured_systems: 0,
  monitored_systems_count: 0,
  reporting_systems: 0,
  protected_assets_count: 0,
  freshness_status: 'unavailable',
  confidence_status: 'unavailable',
  last_poll_at: null,
  last_heartbeat_at: null,
  last_telemetry_at: null,
  last_coverage_telemetry_at: null,
  telemetry_kind: null,
  last_detection_at: null,
  evidence_source: 'none',
  configuration_reason_codes: ['summary_unavailable'],
  status_reason: 'summary_unavailable',
  configuration_reason: 'summary_unavailable',
  valid_protected_asset_count: 0,
  linked_monitored_system_count: 0,
  persisted_enabled_config_count: 0,
  valid_target_system_link_count: 0,
  contradiction_flags: [],
};

function asTrimmedString(value: unknown): string | null {
  const normalized = String(value ?? '').trim();
  return normalized ? normalized : null;
}

function asTimestamp(value: unknown): string | null {
  const normalized = asTrimmedString(value);
  if (!normalized) {
    return null;
  }
  return Number.isFinite(new Date(normalized).getTime()) ? normalized : null;
}

function asReasonCodes(value: unknown, fallback: string[] = []): string[] {
  if (!Array.isArray(value)) {
    return fallback;
  }
  const normalized = value
    .map((item) => asTrimmedString(item))
    .filter((item): item is string => Boolean(item));
  return normalized.length > 0 ? normalized : fallback;
}

export function resolveWorkspaceMonitoringTruthFromSummary(summary: WorkspaceMonitoringSummary | null | undefined): WorkspaceMonitoringTruth {
  if (!summary) {
    return DEFAULT_TRUTH;
  }
  const monitoring_mode = summary.monitoring_mode === 'live'
    || summary.monitoring_mode === 'hybrid'
    || summary.monitoring_mode === 'simulator'
    || summary.monitoring_mode === 'offline'
    || summary.monitoring_mode === 'unavailable'
    ? summary.monitoring_mode
    : 'unavailable';
  const runtime_status = summary.runtime_status === 'provisioning'
    || summary.runtime_status === 'healthy'
    || summary.runtime_status === 'degraded'
    || summary.runtime_status === 'idle'
    || summary.runtime_status === 'failed'
    || summary.runtime_status === 'disabled'
    || summary.runtime_status === 'offline'
    ? summary.runtime_status
    : 'offline';
  const freshness_status = summary.freshness_status === 'fresh'
    || summary.freshness_status === 'stale'
    || summary.freshness_status === 'unavailable'
    ? summary.freshness_status
    : 'unavailable';
  const confidence_status = summary.confidence_status === 'high'
    || summary.confidence_status === 'medium'
    || summary.confidence_status === 'low'
    || summary.confidence_status === 'unavailable'
    ? summary.confidence_status
    : 'unavailable';
  const evidence_source = summary.evidence_source === 'live'
    || summary.evidence_source === 'simulator'
    || summary.evidence_source === 'replay'
    || summary.evidence_source === 'none'
    ? summary.evidence_source
    : 'none';
  const configured_systems = Number(summary.configured_systems ?? summary.coverage_state?.configured_systems ?? 0);
  const reporting_systems = Number(summary.reporting_systems ?? summary.coverage_state?.reporting_systems ?? 0);
  const monitored_systems_count = Number(summary.monitored_systems_count ?? configured_systems);
  const protected_assets_count = Number(summary.protected_assets_count ?? summary.protected_assets ?? summary.coverage_state?.protected_assets ?? 0);
  const valid_protected_asset_count = Number(summary.valid_protected_asset_count ?? 0);
  const linked_monitored_system_count = Number(summary.linked_monitored_system_count ?? 0);
  const persisted_enabled_config_count = Number(summary.persisted_enabled_config_count ?? 0);
  const valid_target_system_link_count = Number(summary.valid_target_system_link_count ?? 0);
  const contradictions = [...(summary.contradiction_flags ?? [])];

  if (summary.runtime_status === 'offline' && summary.last_telemetry_at) {
    contradictions.push('offline_with_current_telemetry');
  }
  if (reporting_systems <= 0 && summary.runtime_status === 'healthy') {
    contradictions.push('healthy_without_reporting_systems');
  }
  if (summary.freshness_status === 'unavailable' && summary.last_telemetry_at) {
    contradictions.push('telemetry_unavailable_with_timestamp');
  }
  if (!summary.workspace_configured && (configured_systems > 0 || monitored_systems_count > 0 || protected_assets_count > 0)) {
    contradictions.push('workspace_unconfigured_with_coverage');
  }
  if (configured_systems === 0 && reporting_systems === 0 && summary.last_telemetry_at) {
    contradictions.push('zero_coverage_with_live_telemetry');
  }
  if (
    summary.last_poll_at
    && !summary.last_telemetry_at
    && summary.monitoring_mode === 'live'
    && summary.evidence_source === 'live'
  ) {
    contradictions.push('poll_without_telemetry_timestamp');
  }
  if (
    summary.last_heartbeat_at
    && !summary.last_telemetry_at
    && summary.monitoring_mode === 'live'
    && summary.evidence_source === 'live'
  ) {
    contradictions.push('heartbeat_without_telemetry_timestamp');
  }
  if (
    summary.workspace_configured
    && (
      valid_protected_asset_count <= 0
      || linked_monitored_system_count <= 0
      || persisted_enabled_config_count <= 0
      || valid_target_system_link_count <= 0
    )
  ) {
    contradictions.push('workspace_configured_missing_required_links');
  }
  return {
    workspace_slug: null,
    workspace_name: null,
    workspace_configured: Boolean(summary.workspace_configured),
    monitoring_mode,
    runtime_status,
    configured_systems,
    monitored_systems_count,
    reporting_systems,
    protected_assets_count,
    freshness_status,
    confidence_status,
    last_poll_at: asTimestamp(summary.last_poll_at),
    last_heartbeat_at: asTimestamp(summary.last_heartbeat_at),
    last_telemetry_at: asTimestamp(summary.last_telemetry_at),
    last_coverage_telemetry_at: asTimestamp(summary.last_coverage_telemetry_at),
    telemetry_kind: summary.telemetry_kind ?? null,
    last_detection_at: asTimestamp(summary.last_detection_at),
    evidence_source,
    configuration_reason_codes: asReasonCodes((summary as Record<string, unknown>).configuration_reason_codes, []),
    status_reason: asTrimmedString(summary.status_reason),
    configuration_reason: asTrimmedString(summary.configuration_reason),
    valid_protected_asset_count,
    linked_monitored_system_count,
    persisted_enabled_config_count,
    valid_target_system_link_count,
    contradiction_flags: [...new Set(contradictions)],
  };
}

export function resolveWorkspaceMonitoringTruth(status: MonitoringRuntimeStatus | null): WorkspaceMonitoringTruth {
  const truth = resolveWorkspaceMonitoringTruthFromSummary(status?.workspace_monitoring_summary);
  const workspaceRecord = (status as Record<string, unknown> | null)?.workspace;
  const workspaceName = workspaceRecord && typeof workspaceRecord === 'object'
    ? asTrimmedString((workspaceRecord as Record<string, unknown>).name)
    : null;
  return {
    ...truth,
    workspace_slug: asTrimmedString((status as Record<string, unknown> | null)?.workspace_slug),
    workspace_name: workspaceName ?? asTrimmedString((status as Record<string, unknown> | null)?.workspace_name),
    configuration_reason_codes: truth.configuration_reason_codes.length > 0
      ? truth.configuration_reason_codes
      : asReasonCodes((status as Record<string, unknown> | null)?.configuration_reason_codes, ['summary_unavailable']),
    status_reason: truth.status_reason
      ?? asTrimmedString((status as Record<string, unknown> | null)?.status_reason)
      ?? asTrimmedString((status as Record<string, unknown> | null)?.degraded_reason),
    configuration_reason: truth.configuration_reason
      ?? asTrimmedString((status as Record<string, unknown> | null)?.configuration_reason),
  };
}

export function hasLiveTelemetry(truth: WorkspaceMonitoringTruth): boolean {
  const lastCoverageTelemetryAt = truth.last_coverage_telemetry_at ?? (truth.telemetry_kind === 'coverage' ? truth.last_telemetry_at : null);
  return truth.runtime_status !== 'offline'
    && truth.workspace_configured
    && (truth.monitoring_mode === 'live' || truth.monitoring_mode === 'hybrid')
    && truth.evidence_source === 'live'
    && truth.freshness_status === 'fresh'
    && truth.confidence_status !== 'unavailable'
    && truth.reporting_systems > 0
    && Boolean(lastCoverageTelemetryAt)
    && !truth.contradiction_flags.includes('offline_with_current_telemetry')
    && !truth.contradiction_flags.includes('telemetry_unavailable_with_timestamp')
    && !truth.contradiction_flags.includes('zero_coverage_with_live_telemetry')
    && !truth.contradiction_flags.includes('workspace_configured_missing_required_links')
    && !truth.contradiction_flags.includes('poll_without_telemetry_timestamp')
    && !truth.contradiction_flags.includes('heartbeat_without_telemetry_timestamp');
}

export function monitoringHealthyCopyAllowed(truth: WorkspaceMonitoringTruth): boolean {
  return truth.runtime_status === 'healthy' && truth.reporting_systems > 0;
}
