import type { MonitoringRuntimeStatus, WorkspaceMonitoringSummary } from './monitoring-status-contract';

export type WorkspaceMonitoringTruth = {
  workspace_slug: string | null;
  workspace_name: string | null;
  workspace_configured: boolean;
  runtime_status: 'live' | 'degraded' | 'offline' | 'idle';
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
  continuity_status: 'continuous_live' | 'continuous_no_evidence' | 'degraded' | 'offline' | 'idle_no_telemetry';
  continuity_reason_codes: string[];
  status_reason: string | null;
  db_failure_classification?: string | null;
  db_failure_reason?: string | null;
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
  continuity_status: 'idle_no_telemetry',
  continuity_reason_codes: ['summary_unavailable'],
  status_reason: 'summary_unavailable',
  db_failure_classification: null,
  db_failure_reason: null,
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
  const runtimeStatus = summary.runtime_status;
  const monitoringStatus = summary.monitoring_status;
  const telemetryFreshness = summary.telemetry_freshness;
  const confidence = summary.confidence;
  const resolvedStatusReason = asTrimmedString(summary.status_reason);
  const dbFailureClassification = asTrimmedString((summary as Record<string, unknown>).db_failure_classification);
  const dbFailureReason = asTrimmedString((summary as Record<string, unknown>).db_failure_reason)
    ?? (resolvedStatusReason && resolvedStatusReason.toLowerCase().includes('database') ? resolvedStatusReason : null);
  const lastCoverageTelemetryAt = asTimestamp((summary as Record<string, unknown>).last_coverage_telemetry_at);
  const telemetryKind = null;
  const lastTelemetryAt = asTimestamp(summary.last_telemetry_at);
  const lastDetectionAt = asTimestamp(summary.last_detection_at);
  const reportingSystemsCount = asCount(summary.reporting_systems_count ?? (summary as Record<string, unknown>).reporting_systems);
  const monitoredSystemsCount = asCount(summary.monitored_systems_count ?? (summary as Record<string, unknown>).configured_systems);
  const protectedAssetsCount = asCount(summary.protected_assets_count ?? (summary as Record<string, unknown>).protected_assets);
  const lastHeartbeatAt = asTimestamp(summary.last_heartbeat_at);
  const lastPollAt = asTimestamp(summary.last_poll_at);
  const evidenceSourceSummary = summary.evidence_source_summary;
  const contradictionFlags = Array.isArray(summary.contradiction_flags)
    ? (summary.contradiction_flags as unknown[])
        .map((value) => asTrimmedString(value))
        .filter((value): value is string => Boolean(value))
    : [];
  const workspaceConfigured = Boolean(summary.workspace_configured);
  const runtimeStatusLabel = String(runtimeStatus ?? '').trim().toLowerCase();
  const normalizedRuntimeStatus = runtimeStatusLabel === 'healthy' ? 'live' : runtimeStatusLabel;
  const normalizedTelemetryFreshness = telemetryFreshness === 'fresh' || telemetryFreshness === 'stale' || telemetryFreshness === 'unavailable'
    ? telemetryFreshness
    : ((summary as Record<string, unknown>).freshness_status as WorkspaceMonitoringTruth['telemetry_freshness']) ?? 'unavailable';
  const normalizedConfidence = confidence === 'high' || confidence === 'medium' || confidence === 'low' || confidence === 'unavailable'
    ? confidence
    : ((summary as Record<string, unknown>).confidence_status as WorkspaceMonitoringTruth['confidence']) ?? 'unavailable';
  const normalizedEvidenceSource = evidenceSourceSummary === 'live' || evidenceSourceSummary === 'simulator' || evidenceSourceSummary === 'replay' || evidenceSourceSummary === 'none'
    ? evidenceSourceSummary
    : ((summary as Record<string, unknown>).evidence_source as WorkspaceMonitoringTruth['evidence_source_summary']) ?? 'none';
  const continuityStatusValue = asTrimmedString((summary as Record<string, unknown>).continuity_status);
  const continuityStatus = continuityStatusValue === 'continuous_live'
    || continuityStatusValue === 'continuous_no_evidence'
    || continuityStatusValue === 'degraded'
    || continuityStatusValue === 'offline'
    || continuityStatusValue === 'idle_no_telemetry'
    ? continuityStatusValue
    : 'idle_no_telemetry';
  const continuityReasonCodes = Array.isArray((summary as Record<string, unknown>).continuity_reason_codes)
    ? ((summary as Record<string, unknown>).continuity_reason_codes as unknown[])
        .map((value) => asTrimmedString(value))
        .filter((value): value is string => Boolean(value))
    : [];
  const normalizedContradictionFlags = [...new Set(contradictionFlags)].sort();
  const declaredGuardFlags = Array.isArray(summary.guard_flags)
    ? (summary.guard_flags as unknown[])
        .map((value) => asTrimmedString(value))
        .filter((value): value is string => Boolean(value))
    : [];
  const normalizedGuardFlags = [...new Set(declaredGuardFlags)].sort();
  return {
    workspace_slug: null,
    workspace_name: null,
    workspace_configured: workspaceConfigured,
    runtime_status: normalizedRuntimeStatus as WorkspaceMonitoringTruth['runtime_status'],
    monitoring_status: monitoringStatus,
    monitored_systems_count: monitoredSystemsCount,
    reporting_systems_count: reportingSystemsCount,
    protected_assets_count: protectedAssetsCount,
    telemetry_freshness: normalizedTelemetryFreshness,
    confidence: normalizedConfidence,
    last_poll_at: lastPollAt,
    last_heartbeat_at: lastHeartbeatAt,
    last_telemetry_at: lastTelemetryAt,
    last_coverage_telemetry_at: lastCoverageTelemetryAt,
    telemetry_kind: telemetryKind,
    last_detection_at: lastDetectionAt,
    active_alerts_count: Number(summary.active_alerts_count ?? 0),
    active_incidents_count: Number(summary.active_incidents_count ?? 0),
    evidence_source_summary: normalizedEvidenceSource,
    continuity_status: continuityStatus,
    continuity_reason_codes: continuityReasonCodes,
    status_reason: resolvedStatusReason,
    db_failure_classification: dbFailureClassification,
    db_failure_reason: dbFailureReason,
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
    && !truth.db_failure_reason
    && (truth.contradiction_flags ?? []).length === 0;
}

export function hasRealTelemetryBackedChain(truth: WorkspaceMonitoringTruth): boolean {
  const continuityIsLive = truth.continuity_status === 'continuous_live';
  return hasLiveTelemetry(truth)
    && continuityIsLive
    && (truth.guard_flags ?? []).length === 0
    && (truth.contradiction_flags ?? []).length === 0
    && !truth.db_failure_reason;
}

export function monitoringHealthyCopyAllowed(truth: WorkspaceMonitoringTruth): boolean {
  const monitoringStatus = truth.monitoring_status ?? (truth.runtime_status === 'live' ? 'live' : 'limited');
  return truth.runtime_status === 'live'
    && monitoringStatus === 'live'
    && truth.reporting_systems_count > 0
    && hasLiveTelemetry(truth)
    && truth.continuity_status === 'continuous_live'
    && (truth.guard_flags ?? []).length === 0
    && (truth.contradiction_flags ?? []).length === 0
    && !truth.db_failure_reason;
}
