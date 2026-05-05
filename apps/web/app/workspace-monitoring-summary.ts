import type { MonitoringRuntimeStatus } from './monitoring-status-contract';

export type WorkspaceMonitoringSummaryContract = {
  workspace_configured: boolean;
  monitoring_status: 'live' | 'limited' | 'offline';
  freshness_status: 'fresh' | 'stale' | 'unavailable';
  confidence_status: 'high' | 'medium' | 'low' | 'unavailable';
  protected_assets: number;
  monitoring_targets: number;
  monitored_systems: number;
  reporting_systems: number;
  active_alerts: number;
  open_incidents: number;
  last_heartbeat_at: string | null;
  last_poll_at: string | null;
  last_telemetry_at: string | null;
  last_detection_at: string | null;
  evidence_source: 'live' | 'simulator' | 'replay' | 'none';
  reason_codes: string[];
  contradiction_flags: string[];
  next_required_action: string;
  current_step: string;
  workflow_steps: unknown[];
};

export const DEFAULT_WORKSPACE_MONITORING_SUMMARY: WorkspaceMonitoringSummaryContract = {
  workspace_configured: false,
  monitoring_status: 'offline',
  freshness_status: 'unavailable',
  confidence_status: 'unavailable',
  protected_assets: 0,
  monitoring_targets: 0,
  monitored_systems: 0,
  reporting_systems: 0,
  active_alerts: 0,
  open_incidents: 0,
  last_heartbeat_at: null,
  last_poll_at: null,
  last_telemetry_at: null,
  last_detection_at: null,
  evidence_source: 'none',
  reason_codes: [],
  contradiction_flags: [],
  next_required_action: 'review_reason_codes',
  current_step: 'asset_created',
  workflow_steps: [],
};

function asCount(value: unknown): number {
  const numeric = Number(value ?? 0);
  return Number.isFinite(numeric) ? numeric : 0;
}

function asStringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value.map((entry) => String(entry ?? '').trim()).filter(Boolean);
}

export function resolveWorkspaceMonitoringSummaryContract(
  runtimeStatus: MonitoringRuntimeStatus | null | undefined,
): WorkspaceMonitoringSummaryContract {
  const runtimeRecord = (runtimeStatus ?? {}) as Record<string, unknown>;
  const summary = runtimeStatus?.workspace_monitoring_summary as Record<string, unknown> | undefined;
  const monitoringRuntime = runtimeStatus?.workspace_monitoring_runtime;
  const counts = monitoringRuntime?.counts;
  return {
    workspace_configured: Boolean(summary?.workspace_configured ?? runtimeStatus?.workspace_configured ?? false),
    monitoring_status: (summary?.monitoring_status ?? runtimeStatus?.monitoring_status ?? 'offline') as WorkspaceMonitoringSummaryContract['monitoring_status'],
    freshness_status: (summary?.freshness_status ?? runtimeStatus?.freshness_status ?? summary?.telemetry_freshness ?? 'unavailable') as WorkspaceMonitoringSummaryContract['freshness_status'],
    confidence_status: (summary?.confidence_status ?? runtimeStatus?.confidence_status ?? summary?.confidence ?? 'unavailable') as WorkspaceMonitoringSummaryContract['confidence_status'],
    protected_assets: asCount(summary?.protected_assets ?? summary?.protected_assets_count ?? runtimeStatus?.protected_assets ?? counts?.protected_assets),
    monitoring_targets: asCount(summary?.monitoring_targets ?? runtimeStatus?.targets_monitored ?? counts?.monitoring_targets),
    monitored_systems: asCount(summary?.monitored_systems ?? summary?.monitored_systems_count ?? runtimeStatus?.monitored_systems ?? counts?.monitored_systems),
    reporting_systems: asCount(summary?.reporting_systems ?? summary?.reporting_systems_count ?? runtimeStatus?.reporting_systems ?? counts?.reporting_systems),
    active_alerts: asCount(summary?.active_alerts ?? summary?.active_alerts_count ?? runtimeStatus?.open_alerts ?? counts?.active_alerts),
    open_incidents: asCount(summary?.open_incidents ?? summary?.active_incidents_count ?? runtimeStatus?.active_incidents ?? counts?.open_incidents),
    last_heartbeat_at: String(summary?.last_heartbeat_at ?? runtimeStatus?.last_heartbeat_at ?? null) || null,
    last_poll_at: String(summary?.last_poll_at ?? runtimeStatus?.last_poll_at ?? null) || null,
    last_telemetry_at: String(summary?.last_telemetry_at ?? runtimeStatus?.last_telemetry_at ?? null) || null,
    last_detection_at: String(summary?.last_detection_at ?? runtimeStatus?.last_detection_at ?? null) || null,
    evidence_source: (summary?.evidence_source ?? summary?.evidence_source_summary ?? runtimeStatus?.evidence_source ?? 'none') as WorkspaceMonitoringSummaryContract['evidence_source'],
    reason_codes: asStringList(summary?.reason_codes ?? runtimeStatus?.reason_codes),
    contradiction_flags: asStringList(summary?.contradiction_flags ?? runtimeStatus?.contradiction_flags),
    next_required_action: String(summary?.next_required_action ?? runtimeStatus?.next_required_action ?? 'review_reason_codes'),
    current_step: String(summary?.current_step ?? runtimeRecord.current_step ?? 'asset_created'),
    workflow_steps: Array.isArray(summary?.workflow_steps) ? summary?.workflow_steps : (Array.isArray(runtimeRecord.workflow_steps) ? runtimeRecord.workflow_steps as unknown[] : []),
  };
}
