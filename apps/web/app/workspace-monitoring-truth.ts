import type { MonitoringRuntimeStatus } from './monitoring-status-contract';

type WorkspaceMonitoringSummary = NonNullable<MonitoringRuntimeStatus['workspace_monitoring_summary']>;

export type WorkspaceMonitoringTruth = {
  workspace_slug: string | null;
  workspace_name: string | null;
  workspace_configured: boolean;
  runtime_status: 'live' | 'degraded' | 'offline' | 'idle';
  monitoring_status: 'live' | 'limited' | 'offline';
  monitored_systems_count: number;
  reporting_systems_count: number;
  protected_assets_count: number;
  telemetry_freshness: 'fresh' | 'stale' | 'unavailable';
  confidence: 'high' | 'medium' | 'low' | 'unavailable';
  last_poll_at: string | null;
  last_heartbeat_at: string | null;
  last_telemetry_at: string | null;
  active_alerts_count: number;
  active_incidents_count: number;
  evidence_source_summary: 'live' | 'simulator' | 'replay' | 'none';
  status_reason: string | null;
  contradiction_flags: string[];
  guard_flags: string[];
};

const DEFAULT_TRUTH: WorkspaceMonitoringTruth = {
  workspace_slug: null,
  workspace_name: null,
  workspace_configured: false,
  runtime_status: 'offline',
  monitoring_status: 'offline',
  monitored_systems_count: 0,
  reporting_systems_count: 0,
  protected_assets_count: 0,
  telemetry_freshness: 'unavailable',
  confidence: 'unavailable',
  last_poll_at: null,
  last_heartbeat_at: null,
  last_telemetry_at: null,
  active_alerts_count: 0,
  active_incidents_count: 0,
  evidence_source_summary: 'none',
  status_reason: 'summary_unavailable',
  contradiction_flags: [],
  guard_flags: [],
};

const HARD_GUARD_FLAGS = new Set([
  'offline_with_current_telemetry',
  'telemetry_unavailable_with_high_confidence',
  'live_monitoring_without_reporting_systems',
  'live_telemetry_verified_without_timestamp',
  'idle_runtime_with_active_monitoring_claim',
]);

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

export function resolveWorkspaceMonitoringTruthFromSummary(summary: WorkspaceMonitoringSummary | null | undefined): WorkspaceMonitoringTruth {
  if (!summary) {
    return DEFAULT_TRUTH;
  }
  const runtimeStatusInput = String(summary.runtime_status ?? '').toLowerCase();
  const runtime_status = runtimeStatusInput === 'live' || runtimeStatusInput === 'healthy'
    ? 'live'
    : runtimeStatusInput === 'degraded'
      ? 'degraded'
      : runtimeStatusInput === 'idle'
        ? 'idle'
        : 'offline';
  const monitoringStatusInput = String(summary.monitoring_status ?? '').toLowerCase();
  const monitoring_status = monitoringStatusInput === 'live'
    || monitoringStatusInput === 'limited'
    || monitoringStatusInput === 'offline'
    ? monitoringStatusInput
    : (runtime_status === 'live' && Number(summary.reporting_systems_count ?? (summary as Record<string, unknown>).reporting_systems ?? 0) > 0
      ? 'live'
      : 'offline');
  const telemetryFreshnessInput = summary.telemetry_freshness ?? (summary as Record<string, unknown>).freshness_status;
  const telemetry_freshness = telemetryFreshnessInput === 'fresh'
    || telemetryFreshnessInput === 'stale'
    || telemetryFreshnessInput === 'unavailable'
    ? telemetryFreshnessInput
    : 'unavailable';
  const confidenceInput = summary.confidence ?? (summary as Record<string, unknown>).confidence_status;
  const confidence = confidenceInput === 'high'
    || confidenceInput === 'medium'
    || confidenceInput === 'low'
    || confidenceInput === 'unavailable'
    ? confidenceInput
    : 'unavailable';
  const evidenceInput = summary.evidence_source_summary ?? (summary as Record<string, unknown>).evidence_source;
  const evidence_source_summary = evidenceInput === 'live'
    || evidenceInput === 'simulator'
    || evidenceInput === 'replay'
    || evidenceInput === 'none'
    ? evidenceInput
    : 'none';
  const reporting_systems_count = Number(summary.reporting_systems_count ?? (summary as Record<string, unknown>).reporting_systems ?? 0);
  const monitored_systems_count = Number(summary.monitored_systems_count ?? 0);
  const protected_assets_count = Number(summary.protected_assets_count ?? (summary as Record<string, unknown>).protected_assets ?? 0);
  const suppliedFlags = Array.isArray((summary as Record<string, unknown>).contradiction_flags)
    ? ((summary as Record<string, unknown>).contradiction_flags as unknown[])
      .map((flag) => asTrimmedString(flag))
      .filter((flag): flag is string => Boolean(flag))
    : [];
  const contradictionFlags = new Set<string>(suppliedFlags);
  if (runtime_status === 'offline' && telemetry_freshness === 'fresh') {
    contradictionFlags.add('offline_with_current_telemetry');
  }
  if (telemetry_freshness === 'unavailable' && confidence === 'high') {
    contradictionFlags.add('telemetry_unavailable_with_high_confidence');
  }
  if (runtime_status === 'live' && reporting_systems_count === 0) {
    contradictionFlags.add('live_monitoring_without_reporting_systems');
  }
  const lastTelemetryAt = asTimestamp(summary.last_telemetry_at)
    ?? asTimestamp((summary as Record<string, unknown>).last_coverage_telemetry_at);
  if (asTimestamp(summary.last_heartbeat_at) && !lastTelemetryAt) {
    contradictionFlags.add('heartbeat_without_telemetry_timestamp');
  }
  if (asTimestamp(summary.last_poll_at) && !lastTelemetryAt) {
    contradictionFlags.add('poll_without_telemetry_timestamp');
  }
  if (evidence_source_summary === 'live' && confidence === 'high' && !lastTelemetryAt) {
    contradictionFlags.add('live_telemetry_verified_without_timestamp');
  }
  if (
    runtime_status === 'idle'
    && reporting_systems_count > 0
    && telemetry_freshness === 'fresh'
    && confidence === 'high'
    && evidence_source_summary === 'live'
  ) {
    contradictionFlags.add('idle_runtime_with_active_monitoring_claim');
  }
  if (!Boolean(summary.workspace_configured) && (monitored_systems_count > 0 || reporting_systems_count > 0 || protected_assets_count > 0)) {
    contradictionFlags.add('workspace_unconfigured_with_coverage');
  }
  if (
    Boolean(summary.workspace_configured)
    && (
      Number((summary as Record<string, unknown>).valid_protected_asset_count ?? 1) <= 0
      || Number((summary as Record<string, unknown>).linked_monitored_system_count ?? 1) <= 0
      || Number((summary as Record<string, unknown>).persisted_enabled_config_count ?? 1) <= 0
      || Number((summary as Record<string, unknown>).valid_target_system_link_count ?? 1) <= 0
    )
  ) {
    contradictionFlags.add('workspace_configured_missing_required_links');
  }
  const normalizedContradictionFlags = Array.from(contradictionFlags).sort();
  const guardFlags = normalizedContradictionFlags.filter((flag) => HARD_GUARD_FLAGS.has(flag));
  const resolvedStatusReason = asTrimmedString(summary.status_reason)
    ?? (guardFlags.length > 0 ? `guard:${guardFlags[0]}` : null);
  return {
    workspace_slug: null,
    workspace_name: null,
    workspace_configured: Boolean(summary.workspace_configured),
    runtime_status,
    monitoring_status,
    monitored_systems_count,
    reporting_systems_count,
    protected_assets_count,
    telemetry_freshness,
    confidence,
    last_poll_at: asTimestamp(summary.last_poll_at),
    last_heartbeat_at: asTimestamp(summary.last_heartbeat_at),
    last_telemetry_at: lastTelemetryAt,
    active_alerts_count: Number(summary.active_alerts_count ?? 0),
    active_incidents_count: Number(summary.active_incidents_count ?? 0),
    evidence_source_summary,
    status_reason: resolvedStatusReason,
    contradiction_flags: normalizedContradictionFlags,
    guard_flags: guardFlags,
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
    status_reason: truth.status_reason ?? asTrimmedString((status as Record<string, unknown> | null)?.status_reason),
  };
}

export function hasLiveTelemetry(truth: WorkspaceMonitoringTruth): boolean {
  if (truth.guard_flags.length > 0 || truth.contradiction_flags.length > 0) {
    return false;
  }
  return truth.runtime_status === 'live'
    && truth.workspace_configured
    && truth.monitoring_status === 'live'
    && truth.evidence_source_summary === 'live'
    && truth.telemetry_freshness === 'fresh'
    && truth.confidence !== 'unavailable'
    && truth.reporting_systems_count > 0
    && Boolean(truth.last_telemetry_at);
}

export function monitoringHealthyCopyAllowed(truth: WorkspaceMonitoringTruth): boolean {
  if (truth.guard_flags.length > 0 || truth.contradiction_flags.length > 0) {
    return false;
  }
  return truth.runtime_status === 'live'
    && truth.monitoring_status === 'live'
    && truth.reporting_systems_count > 0;
}
