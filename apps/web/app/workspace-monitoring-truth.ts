import type { MonitoringRuntimeStatus } from './monitoring-status-contract';

type WorkspaceMonitoringSummary = NonNullable<MonitoringRuntimeStatus['workspace_monitoring_summary']>;

export type WorkspaceMonitoringTruth = {
  workspace_slug: string | null;
  workspace_name: string | null;
  workspace_configured: boolean;
  runtime_status: 'provisioning' | 'healthy' | 'degraded' | 'idle' | 'failed' | 'disabled' | 'offline';
  monitoring_status: 'active' | 'idle' | 'degraded' | 'offline' | 'error';
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
  monitoring_mode?: 'live' | 'hybrid' | 'simulator' | 'offline' | 'unavailable';
  configured_systems?: number;
  last_coverage_telemetry_at?: string | null;
  telemetry_kind?: 'coverage' | 'target_event' | null;
  last_detection_at?: string | null;
  evidence_source?: 'live' | 'simulator' | 'replay' | 'none';
  configuration_reason?: string | null;
  configuration_reason_codes?: string[];
  valid_protected_asset_count?: number;
  linked_monitored_system_count?: number;
  persisted_enabled_config_count?: number;
  valid_target_system_link_count?: number;
  status_reason: string | null;
  contradiction_flags: string[];
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
  evidence_source: 'none',
  status_reason: 'summary_unavailable',
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

export function resolveWorkspaceMonitoringTruthFromSummary(summary: WorkspaceMonitoringSummary | null | undefined): WorkspaceMonitoringTruth {
  if (!summary) {
    return DEFAULT_TRUTH;
  }
  const runtime_status = summary.runtime_status === 'provisioning'
    || summary.runtime_status === 'healthy'
    || summary.runtime_status === 'degraded'
    || summary.runtime_status === 'idle'
    || summary.runtime_status === 'failed'
    || summary.runtime_status === 'disabled'
    || summary.runtime_status === 'offline'
    ? summary.runtime_status
    : 'offline';
  const monitoring_status = summary.monitoring_status === 'active'
    || summary.monitoring_status === 'idle'
    || summary.monitoring_status === 'degraded'
    || summary.monitoring_status === 'offline'
    || summary.monitoring_status === 'error'
    ? summary.monitoring_status
    : 'offline';
  const rawFreshness = (summary as Record<string, unknown>).telemetry_freshness ?? (summary as Record<string, unknown>).freshness_status;
  const telemetry_freshness = rawFreshness === 'fresh'
    || rawFreshness === 'stale'
    || rawFreshness === 'unavailable'
    ? rawFreshness
    : 'unavailable';
  const rawConfidence = (summary as Record<string, unknown>).confidence ?? (summary as Record<string, unknown>).confidence_status;
  const confidence = rawConfidence === 'high'
    || rawConfidence === 'medium'
    || rawConfidence === 'low'
    || rawConfidence === 'unavailable'
    ? rawConfidence
    : 'unavailable';
  const rawEvidence = (summary as Record<string, unknown>).evidence_source_summary ?? (summary as Record<string, unknown>).evidence_source;
  const evidence_source_summary = rawEvidence === 'live'
    || rawEvidence === 'simulator'
    || rawEvidence === 'replay'
    || rawEvidence === 'none'
    ? rawEvidence
    : 'none';
  const reporting_systems_count = Number(summary.reporting_systems_count ?? (summary as Record<string, unknown>).reporting_systems ?? 0);
  const monitored_systems_count = Number(summary.monitored_systems_count ?? (summary as Record<string, unknown>).configured_systems ?? 0);
  const protected_assets_count = Number(summary.protected_assets_count ?? (summary as Record<string, unknown>).protected_assets ?? 0);
  const lastTelemetryAt = asTimestamp(summary.last_telemetry_at)
    ?? asTimestamp((summary as Record<string, unknown>).last_coverage_telemetry_at);
  const contradiction_flags = Array.isArray((summary as Record<string, unknown>).contradiction_flags)
    ? ((summary as Record<string, unknown>).contradiction_flags as unknown[])
      .map((value) => asTrimmedString(value))
      .filter((value): value is string => Boolean(value))
    : [];
  const derivedFlags = new Set<string>(contradiction_flags);
  const hasTelemetryTimestamp = Boolean(lastTelemetryAt);
  const liveTelemetryVerified = evidence_source_summary === 'live' && confidence === 'high';
  if (runtime_status === 'offline' && telemetry_freshness === 'fresh') {
    derivedFlags.add('offline_with_current_telemetry');
  }
  if (telemetry_freshness === 'unavailable' && confidence === 'high') {
    derivedFlags.add('telemetry_unavailable_with_high_confidence');
  }
  if (reporting_systems_count === 0 && (runtime_status === 'healthy' || monitoring_status === 'active')) {
    derivedFlags.add('healthy_without_reporting_systems');
  }
  if (asTimestamp(summary.last_heartbeat_at) && !hasTelemetryTimestamp) {
    derivedFlags.add('heartbeat_without_telemetry_timestamp');
  }
  if (asTimestamp(summary.last_poll_at) && !hasTelemetryTimestamp) {
    derivedFlags.add('poll_without_telemetry_timestamp');
  }
  if (!Boolean(summary.workspace_configured) && (monitored_systems_count > 0 || reporting_systems_count > 0 || protected_assets_count > 0)) {
    derivedFlags.add('workspace_unconfigured_with_coverage');
  }
  const configuredLinksMissing =
    Boolean(summary.workspace_configured)
    && (
      Number((summary as Record<string, unknown>).valid_protected_asset_count ?? 0) <= 0
      || Number((summary as Record<string, unknown>).linked_monitored_system_count ?? 0) <= 0
      || Number((summary as Record<string, unknown>).persisted_enabled_config_count ?? 0) <= 0
      || Number((summary as Record<string, unknown>).valid_target_system_link_count ?? 0) <= 0
    );
  if (configuredLinksMissing) {
    derivedFlags.add('workspace_configured_missing_required_links');
  }
  if (liveTelemetryVerified && !hasTelemetryTimestamp) {
    derivedFlags.add('live_telemetry_verified_without_timestamp');
  }
  if (
    runtime_status === 'idle'
    && reporting_systems_count > 0
    && telemetry_freshness === 'fresh'
    && confidence === 'high'
    && evidence_source_summary === 'live'
    && !asTrimmedString(summary.status_reason)
  ) {
    derivedFlags.add('idle_runtime_with_active_monitoring_claim');
  }
  const resolvedStatusReason = asTrimmedString(summary.status_reason)
    ?? (derivedFlags.size > 0 ? `guard:${Array.from(derivedFlags).sort()[0]}` : null);
  return {
    workspace_slug: null,
    workspace_name: null,
    workspace_configured: Boolean(summary.workspace_configured),
    monitoring_mode: ((summary as Record<string, unknown>).monitoring_mode as WorkspaceMonitoringTruth['monitoring_mode']) ?? undefined,
    runtime_status,
    monitoring_status,
    configured_systems: Number((summary as Record<string, unknown>).configured_systems ?? monitored_systems_count),
    monitored_systems_count,
    reporting_systems_count,
    protected_assets_count,
    telemetry_freshness,
    confidence,
    last_poll_at: asTimestamp(summary.last_poll_at),
    last_heartbeat_at: asTimestamp(summary.last_heartbeat_at),
    last_telemetry_at: lastTelemetryAt,
    last_coverage_telemetry_at: asTimestamp((summary as Record<string, unknown>).last_coverage_telemetry_at),
    telemetry_kind: ((summary as Record<string, unknown>).telemetry_kind as WorkspaceMonitoringTruth['telemetry_kind']) ?? null,
    last_detection_at: asTimestamp((summary as Record<string, unknown>).last_detection_at),
    active_alerts_count: Number(summary.active_alerts_count ?? 0),
    active_incidents_count: Number(summary.active_incidents_count ?? 0),
    evidence_source_summary,
    evidence_source: evidence_source_summary,
    configuration_reason: asTrimmedString((summary as Record<string, unknown>).configuration_reason),
    configuration_reason_codes: Array.isArray((summary as Record<string, unknown>).configuration_reason_codes)
      ? ((summary as Record<string, unknown>).configuration_reason_codes as unknown[])
        .map((value) => asTrimmedString(value))
        .filter((value): value is string => Boolean(value))
      : [],
    valid_protected_asset_count: Number((summary as Record<string, unknown>).valid_protected_asset_count ?? 0),
    linked_monitored_system_count: Number((summary as Record<string, unknown>).linked_monitored_system_count ?? 0),
    persisted_enabled_config_count: Number((summary as Record<string, unknown>).persisted_enabled_config_count ?? 0),
    valid_target_system_link_count: Number((summary as Record<string, unknown>).valid_target_system_link_count ?? 0),
    status_reason: resolvedStatusReason,
    contradiction_flags: Array.from(derivedFlags).sort(),
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
    contradiction_flags: truth.contradiction_flags,
  };
}

export function hasLiveTelemetry(truth: WorkspaceMonitoringTruth): boolean {
  return truth.runtime_status !== 'offline'
    && truth.workspace_configured
    && truth.evidence_source_summary === 'live'
    && truth.telemetry_freshness === 'fresh'
    && truth.confidence !== 'unavailable'
    && truth.reporting_systems_count > 0
    && Boolean(truth.last_telemetry_at)
    && truth.contradiction_flags.length === 0;
}

export function monitoringHealthyCopyAllowed(truth: WorkspaceMonitoringTruth): boolean {
  return truth.runtime_status === 'healthy'
    && truth.reporting_systems_count > 0
    && truth.contradiction_flags.length === 0;
}
