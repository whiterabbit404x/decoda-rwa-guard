import type { MonitoringRuntimeStatus, WorkspaceMonitoringSummary } from './monitoring-status-contract';

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
  const resolvedStatusReason = asTrimmedString(summary.status_reason);
  const lastTelemetryAt = asTimestamp(summary.last_telemetry_at);
  return {
    workspace_slug: null,
    workspace_name: null,
    workspace_configured: summary.workspace_configured,
    runtime_status: summary.runtime_status,
    monitoring_status: summary.monitoring_status,
    monitored_systems_count: Number(summary.monitored_systems_count ?? 0),
    reporting_systems_count: Number(summary.reporting_systems_count ?? 0),
    protected_assets_count: Number(summary.protected_assets_count ?? 0),
    telemetry_freshness: summary.telemetry_freshness,
    confidence: summary.confidence,
    last_poll_at: asTimestamp(summary.last_poll_at),
    last_heartbeat_at: asTimestamp(summary.last_heartbeat_at),
    last_telemetry_at: lastTelemetryAt,
    active_alerts_count: Number(summary.active_alerts_count ?? 0),
    active_incidents_count: Number(summary.active_incidents_count ?? 0),
    evidence_source_summary: summary.evidence_source_summary,
    status_reason: resolvedStatusReason,
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
  return truth.runtime_status === 'live'
    && truth.monitoring_status === 'live'
    && truth.reporting_systems_count > 0;
}
