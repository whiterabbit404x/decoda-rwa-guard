import type { MonitoringRuntimeStatus } from './monitoring-status-contract';

export type WorkspaceMonitoringTruth = {
  workspace_configured: boolean;
  monitoring_mode: 'live' | 'simulator' | 'offline' | 'unavailable';
  runtime_status: 'provisioning' | 'healthy' | 'degraded' | 'idle' | 'failed' | 'disabled' | 'offline';
  configured_systems: number;
  reporting_systems: number;
  protected_assets: number;
  freshness: 'fresh' | 'stale' | 'unavailable';
  confidence: 'high' | 'medium' | 'low' | 'unavailable';
  // Deprecated aliases
  freshness_status?: 'fresh' | 'stale' | 'unavailable';
  confidence_status?: 'high' | 'medium' | 'low' | 'unavailable';
  last_poll_at: string | null;
  last_heartbeat_at: string | null;
  last_telemetry_at: string | null;
  last_detection_at: string | null;
  evidence_source: 'live' | 'simulator' | 'replay' | 'none';
  status_reason: string | null;
  contradiction_flags: string[];
};

const DEFAULT_TRUTH: WorkspaceMonitoringTruth = {
  workspace_configured: false,
  monitoring_mode: 'unavailable',
  runtime_status: 'offline',
  configured_systems: 0,
  reporting_systems: 0,
  protected_assets: 0,
  freshness: 'unavailable',
  confidence: 'unavailable',
  last_poll_at: null,
  last_heartbeat_at: null,
  last_telemetry_at: null,
  last_detection_at: null,
  evidence_source: 'none',
  status_reason: 'summary_unavailable',
  contradiction_flags: [],
};

export function resolveWorkspaceMonitoringTruth(status: MonitoringRuntimeStatus | null): WorkspaceMonitoringTruth {
  const summary = status?.workspace_monitoring_summary;
  if (!summary) {
    return DEFAULT_TRUTH;
  }
  const configured_systems = Number(summary.configured_systems ?? summary.coverage_counts?.configured_systems ?? summary.coverage_state?.configured_systems ?? status?.configured_systems ?? status?.coverage_state?.configured_systems ?? 0);
  const reporting_systems = Number(summary.reporting_systems ?? summary.reporting_systems_count ?? summary.coverage_counts?.reporting_systems ?? summary.coverage_state?.reporting_systems ?? status?.reporting_systems ?? status?.coverage_state?.reporting_systems ?? 0);
  const protected_assets = Number(summary.protected_assets ?? summary.protected_assets_count ?? summary.coverage_counts?.protected_assets ?? summary.coverage_state?.protected_assets ?? status?.protected_assets ?? status?.coverage_state?.protected_assets ?? 0);
  const contradictions = [...(summary.contradiction_flags ?? [])];

  if (summary.runtime_status === 'offline' && summary.last_telemetry_at) {
    contradictions.push('offline_with_current_telemetry');
  }
  if (reporting_systems <= 0 && summary.runtime_status === 'healthy') {
    contradictions.push('healthy_without_reporting_systems');
  }
  const freshness = summary.freshness ?? summary.freshness_status ?? 'unavailable';
  const confidence = summary.confidence ?? summary.confidence_status ?? 'unavailable';
  if (freshness === 'unavailable' && summary.last_telemetry_at) {
    contradictions.push('telemetry_unavailable_with_timestamp');
  }
  if (!summary.workspace_configured && (configured_systems > 0 || protected_assets > 0)) {
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
  return {
    workspace_configured: Boolean(summary.workspace_configured),
    monitoring_mode: summary.monitoring_mode,
    runtime_status: summary.runtime_status,
    configured_systems,
    reporting_systems,
    protected_assets,
    freshness,
    confidence,
    freshness_status: freshness,
    confidence_status: confidence,
    last_poll_at: summary.last_poll_at,
    last_heartbeat_at: summary.last_heartbeat_at,
    last_telemetry_at: summary.last_telemetry_at,
    last_detection_at: summary.last_detection_at,
    evidence_source: summary.evidence_source,
    status_reason: summary.status_reason,
    contradiction_flags: [...new Set(contradictions)],
  };
}

export function hasLiveTelemetry(truth: WorkspaceMonitoringTruth): boolean {
  return truth.runtime_status !== 'offline'
    && truth.monitoring_mode === 'live'
    && truth.evidence_source === 'live'
    && truth.freshness === 'fresh'
    && truth.reporting_systems > 0
    && Boolean(truth.last_telemetry_at)
    && !truth.contradiction_flags.includes('offline_with_current_telemetry')
    && !truth.contradiction_flags.includes('telemetry_unavailable_with_timestamp')
    && !truth.contradiction_flags.includes('zero_coverage_with_live_telemetry')
    && !truth.contradiction_flags.includes('poll_without_telemetry_timestamp')
    && !truth.contradiction_flags.includes('heartbeat_without_telemetry_timestamp');
}

export function monitoringHealthyCopyAllowed(truth: WorkspaceMonitoringTruth): boolean {
  return truth.runtime_status === 'healthy' && truth.reporting_systems > 0;
}
