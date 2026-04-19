import type { MonitoringRuntimeStatus, WorkspaceMonitoringSummary } from './monitoring-status-contract';

export type WorkspaceMonitoringTruth = {
  workspace_slug: string | null;
  workspace_name: string | null;
  workspace_configured: boolean;
  runtime_status: 'live' | 'healthy' | 'degraded' | 'offline' | 'idle';
  monitoring_status: 'live' | 'limited' | 'offline';
  monitoring_mode?: 'live' | 'hybrid' | 'simulator' | 'offline' | 'unavailable';
  configured_systems?: number;
  monitored_systems_count: number;
  reporting_systems_count: number;
  protected_assets_count: number;
  telemetry_freshness: 'fresh' | 'stale' | 'unavailable';
  confidence: 'high' | 'medium' | 'low' | 'unavailable';
  last_poll_at: string | null;
  last_heartbeat_at: string | null;
  last_telemetry_at: string | null;
  last_coverage_telemetry_at?: string | null;
  telemetry_kind?: 'coverage' | 'target_event' | null;
  last_detection_at?: string | null;
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
  last_coverage_telemetry_at: null,
  telemetry_kind: null,
  last_detection_at: null,
  active_alerts_count: 0,
  active_incidents_count: 0,
  evidence_source_summary: 'none',
  status_reason: 'summary_unavailable',
  contradiction_flags: [],
  guard_flags: [],
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

function asCount(value: unknown): number {
  const numeric = Number(value ?? 0);
  return Number.isFinite(numeric) ? numeric : 0;
}

export function resolveWorkspaceMonitoringTruthFromSummary(summary: WorkspaceMonitoringSummary | null | undefined): WorkspaceMonitoringTruth {
  if (!summary) {
    return DEFAULT_TRUTH;
  }
  const summaryRecord = summary as unknown as Record<string, unknown>;
  const runtimeStatusRaw = String(summary.runtime_status ?? summaryRecord.runtime_status ?? 'offline').toLowerCase();
  const runtimeStatus = runtimeStatusRaw === 'healthy'
    ? 'live'
    : runtimeStatusRaw === 'failed'
      ? 'offline'
      : runtimeStatusRaw === 'provisioning' || runtimeStatusRaw === 'disabled'
        ? 'idle'
        : runtimeStatusRaw;
  const monitoringStatusRaw = String(
    summary.monitoring_status
    ?? summaryRecord.monitoring_status
    ?? (runtimeStatus === 'live' ? 'live' : 'limited'),
  ).toLowerCase();
  const monitoringStatus = monitoringStatusRaw === 'active' ? 'live' : (monitoringStatusRaw === 'offline' ? 'offline' : monitoringStatusRaw === 'live' ? 'live' : 'limited');
  const telemetryFreshness = String(summary.telemetry_freshness ?? summaryRecord.freshness_status ?? 'unavailable').toLowerCase() as WorkspaceMonitoringTruth['telemetry_freshness'];
  const confidence = String(summary.confidence ?? summaryRecord.confidence_status ?? 'unavailable').toLowerCase() as WorkspaceMonitoringTruth['confidence'];
  const resolvedStatusReason = asTrimmedString(summary.status_reason);
  const telemetryKind = asTrimmedString(summaryRecord.telemetry_kind) as WorkspaceMonitoringTruth['telemetry_kind'];
  const lastCoverageTelemetryAt = asTimestamp(summaryRecord.last_coverage_telemetry_at);
  let lastTelemetryAt = asTimestamp(summary.last_telemetry_at);
  if (!lastTelemetryAt && lastCoverageTelemetryAt && telemetryKind === 'coverage') {
    lastTelemetryAt = lastCoverageTelemetryAt;
  }
  const reportingSystemsCount = asCount((summary as Record<string, unknown>).reporting_systems_count ?? summaryRecord.reporting_systems);
  const monitoredSystemsCount = asCount((summary as Record<string, unknown>).monitored_systems_count ?? summaryRecord.monitored_systems_count ?? summaryRecord.configured_systems);
  const protectedAssetsCount = asCount((summary as Record<string, unknown>).protected_assets_count ?? summaryRecord.protected_assets_count ?? summaryRecord.protected_assets);
  const lastHeartbeatAt = asTimestamp(summary.last_heartbeat_at);
  const lastPollAt = asTimestamp(summary.last_poll_at);
  const evidenceSourceSummary = String(summary.evidence_source_summary ?? summaryRecord.evidence_source ?? 'none').toLowerCase() as WorkspaceMonitoringTruth['evidence_source_summary'];
  const contradictionFlags = Array.isArray((summary as Record<string, unknown>).contradiction_flags)
    ? ((summary as Record<string, unknown>).contradiction_flags as unknown[])
        .map((value) => asTrimmedString(value))
        .filter((value): value is string => Boolean(value))
    : [];
  const derivedContradictionFlags = [...contradictionFlags];
  if ((runtimeStatus === 'offline' || runtimeStatus === 'failed') && telemetryFreshness === 'fresh') {
    derivedContradictionFlags.push('offline_with_current_telemetry');
  }
  if (telemetryFreshness === 'unavailable' && confidence === 'high') {
    derivedContradictionFlags.push('telemetry_unavailable_with_high_confidence');
  }
  if (lastHeartbeatAt && !lastTelemetryAt) {
    derivedContradictionFlags.push('heartbeat_without_telemetry_timestamp');
  }
  if (lastPollAt && !lastTelemetryAt) {
    derivedContradictionFlags.push('poll_without_telemetry_timestamp');
  }
  if (reportingSystemsCount <= 0 && runtimeStatus === 'live') {
    derivedContradictionFlags.push('live_monitoring_without_reporting_systems');
  }
  const effectiveTelemetryTimestamp = lastTelemetryAt ?? lastCoverageTelemetryAt;
  if (!effectiveTelemetryTimestamp && evidenceSourceSummary === 'live' && confidence === 'high') {
    derivedContradictionFlags.push('live_telemetry_verified_without_timestamp');
  }
  if (runtimeStatus === 'idle' && evidenceSourceSummary === 'live' && confidence === 'high' && telemetryFreshness === 'fresh') {
    derivedContradictionFlags.push('idle_runtime_with_active_monitoring_claim');
  }
  const workspaceConfigured = Boolean(summary.workspace_configured);
  const workspaceHasCoverage = monitoredSystemsCount > 0 || reportingSystemsCount > 0 || protectedAssetsCount > 0 || Boolean(lastTelemetryAt || lastHeartbeatAt || lastPollAt);
  if (!workspaceConfigured && workspaceHasCoverage) {
    derivedContradictionFlags.push('workspace_unconfigured_with_coverage');
  }
  const validProtectedAssetCount = asCount(summaryRecord.valid_protected_asset_count);
  const linkedMonitoredSystemCount = asCount(summaryRecord.linked_monitored_system_count);
  const persistedEnabledConfigCount = asCount(summaryRecord.persisted_enabled_config_count);
  const validTargetSystemLinkCount = asCount(summaryRecord.valid_target_system_link_count);
  if (workspaceConfigured && [validProtectedAssetCount, linkedMonitoredSystemCount, persistedEnabledConfigCount, validTargetSystemLinkCount].some((count) => count === 0) && [validProtectedAssetCount, linkedMonitoredSystemCount, persistedEnabledConfigCount, validTargetSystemLinkCount].some((count) => count > 0)) {
    derivedContradictionFlags.push('workspace_configured_missing_required_links');
  }
  const normalizedContradictionFlags = [...new Set(derivedContradictionFlags)].sort();
  const declaredGuardFlags = Array.isArray((summary as Record<string, unknown>).guard_flags)
    ? ((summary as Record<string, unknown>).guard_flags as unknown[])
        .map((value) => asTrimmedString(value))
        .filter((value): value is string => Boolean(value))
    : [];
  const derivedGuardFlags = normalizedContradictionFlags.filter((flag) => (
    flag === 'offline_with_current_telemetry'
    || flag === 'telemetry_unavailable_with_high_confidence'
    || flag === 'live_monitoring_without_reporting_systems'
    || flag === 'live_telemetry_verified_without_timestamp'
    || flag === 'idle_runtime_with_active_monitoring_claim'
  ));
  const normalizedGuardFlags = [...new Set([...declaredGuardFlags, ...derivedGuardFlags])].sort();
  const normalizedStatusReason = normalizedGuardFlags.length > 0
    ? `guard:${normalizedGuardFlags[0]}`
    : resolvedStatusReason;
  return {
    workspace_slug: null,
    workspace_name: null,
    workspace_configured: workspaceConfigured,
    runtime_status: runtimeStatus as WorkspaceMonitoringTruth['runtime_status'],
    monitoring_status: monitoringStatus,
    monitored_systems_count: monitoredSystemsCount,
    reporting_systems_count: reportingSystemsCount,
    protected_assets_count: protectedAssetsCount,
    telemetry_freshness: telemetryFreshness,
    confidence,
    last_poll_at: lastPollAt,
    last_heartbeat_at: lastHeartbeatAt,
    last_telemetry_at: lastTelemetryAt,
    last_coverage_telemetry_at: lastCoverageTelemetryAt,
    telemetry_kind: telemetryKind,
    last_detection_at: asTimestamp(summaryRecord.last_detection_at),
    active_alerts_count: Number(summary.active_alerts_count ?? 0),
    active_incidents_count: Number(summary.active_incidents_count ?? 0),
    evidence_source_summary: evidenceSourceSummary,
    status_reason: normalizedStatusReason,
    contradiction_flags: normalizedContradictionFlags,
    guard_flags: normalizedGuardFlags,
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
  };
}

export function hasLiveTelemetry(truth: WorkspaceMonitoringTruth): boolean {
  const telemetryTimestamp = truth.last_telemetry_at ?? truth.last_coverage_telemetry_at ?? null;
  const monitoringStatus = truth.monitoring_status ?? (truth.runtime_status === 'live' ? 'live' : 'limited');
  return truth.runtime_status === 'live'
    && truth.workspace_configured
    && monitoringStatus === 'live'
    && truth.evidence_source_summary === 'live'
    && truth.telemetry_freshness === 'fresh'
    && truth.confidence !== 'unavailable'
    && truth.reporting_systems_count > 0
    && Boolean(telemetryTimestamp)
    && (truth.guard_flags ?? []).length === 0
    && (truth.contradiction_flags ?? []).length === 0;
}

export function monitoringHealthyCopyAllowed(truth: WorkspaceMonitoringTruth): boolean {
  const monitoringStatus = truth.monitoring_status ?? (truth.runtime_status === 'live' ? 'live' : 'limited');
  return truth.runtime_status === 'live'
    && monitoringStatus === 'live'
    && truth.reporting_systems_count > 0
    && (truth.guard_flags ?? []).length === 0
    && (truth.contradiction_flags ?? []).length === 0;
}
