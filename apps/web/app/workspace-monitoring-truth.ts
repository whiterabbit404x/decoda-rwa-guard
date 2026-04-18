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
  const telemetry_freshness = summary.telemetry_freshness === 'fresh'
    || summary.telemetry_freshness === 'stale'
    || summary.telemetry_freshness === 'unavailable'
    ? summary.telemetry_freshness
    : 'unavailable';
  const confidence = summary.confidence === 'high'
    || summary.confidence === 'medium'
    || summary.confidence === 'low'
    || summary.confidence === 'unavailable'
    ? summary.confidence
    : 'unavailable';
  const evidence_source_summary = summary.evidence_source_summary === 'live'
    || summary.evidence_source_summary === 'simulator'
    || summary.evidence_source_summary === 'replay'
    || summary.evidence_source_summary === 'none'
    ? summary.evidence_source_summary
    : 'none';
  return {
    workspace_slug: null,
    workspace_name: null,
    workspace_configured: Boolean(summary.workspace_configured),
    runtime_status,
    monitoring_status,
    monitored_systems_count: Number(summary.monitored_systems_count ?? 0),
    reporting_systems_count: Number(summary.reporting_systems_count ?? 0),
    protected_assets_count: Number(summary.protected_assets_count ?? 0),
    telemetry_freshness,
    confidence,
    last_poll_at: asTimestamp(summary.last_poll_at),
    last_heartbeat_at: asTimestamp(summary.last_heartbeat_at),
    last_telemetry_at: asTimestamp(summary.last_telemetry_at),
    active_alerts_count: Number(summary.active_alerts_count ?? 0),
    active_incidents_count: Number(summary.active_incidents_count ?? 0),
    evidence_source_summary,
    status_reason: asTrimmedString(summary.status_reason),
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
  return truth.runtime_status !== 'offline'
    && truth.workspace_configured
    && truth.evidence_source_summary === 'live'
    && truth.telemetry_freshness === 'fresh'
    && truth.confidence !== 'unavailable'
    && truth.reporting_systems_count > 0
    && Boolean(truth.last_telemetry_at);
}

export function monitoringHealthyCopyAllowed(truth: WorkspaceMonitoringTruth): boolean {
  return truth.runtime_status === 'healthy' && truth.reporting_systems_count > 0;
}
