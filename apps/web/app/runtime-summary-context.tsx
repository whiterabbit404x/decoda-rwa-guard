'use client';

import { createContext, useContext, useEffect, useMemo, useState } from 'react';

import { usePilotAuth } from './pilot-auth-context';
import { fetchRuntimeStatusDeduped } from './runtime-status-client';
import { resolveWorkspaceMonitoringTruth, type WorkspaceMonitoringTruth } from './workspace-monitoring-truth';

export type ProviderHealthInfo = {
  name: string;
  status: 'connected' | 'not_connected' | 'unknown';
  chain: string | null;
  last_check: string | null;
  error_message: string | null;
};

export type WorkerHealthInfo = {
  status: 'running' | 'stopped' | 'unknown';
  last_heartbeat: string | null;
  last_poll: string | null;
  last_telemetry: string | null;
  consecutive_failures: number;
  next_poll: string | null;
};

type RuntimeSummaryContextValue = {
  summary: WorkspaceMonitoringTruth;
  runtime: import('./monitoring-status-contract').WorkspaceMonitoringRuntime | null;
  loading: boolean;
  reasonMessageForCode: (code: string) => string;
  evidenceLabel: string;
  existsLabel: string;
  missingLabel: string;
  nextActionLabel: string;
  fixCtaLabel: string;
  providerHealth: ProviderHealthInfo;
  workerHealth: WorkerHealthInfo;
};

const REASON_CODE_MESSAGES: Record<string, string> = {
  summary_unavailable: 'Runtime summary is unavailable. Recheck workspace connectivity.',
  workspace_unconfigured: 'Workspace setup is incomplete. Finish onboarding to enable live monitoring.',
  no_reporting_systems: 'No monitored systems are reporting. Enable and verify monitoring sources.',
  stale_telemetry: 'Telemetry is stale. Investigate worker health and source ingestion lag.',
  no_live_evidence: 'No live evidence has been persisted yet. Trigger and validate a real detection path.',
  runtime_contradiction_asset_monitoring_attached_but_no_monitored_systems: 'Assets are registered, but monitoring is not attached to any running systems.',
  runtime_contradiction_asset_count_mismatch_runtime_vs_registry: 'Asset counts are out of sync between registry and runtime.',
  runtime_contradiction_healthy_claim_with_reporting_systems_zero: 'Health cannot be verified because no systems are reporting.',
  runtime_contradiction_live_claim_with_no_telemetry: 'Live mode cannot be verified because telemetry is missing.',
  runtime_contradiction_simulator_evidence_rendered_as_live_provider: 'Simulator evidence was labeled as live provider data.',
  runtime_contradiction_alert_without_detection: 'An alert exists without linked detection evidence.',
  runtime_contradiction_incident_without_alert: 'An incident exists without a linked alert.',
  runtime_contradiction_response_action_without_incident: 'A response action exists without a linked incident.',
  live_monitoring_without_reporting_systems: 'Live monitoring requires at least one reporting monitored system.',
  asset_monitoring_attached_but_no_monitored_systems: 'Assets are configured but no monitored system is attached.',
  simulator_evidence_claimed_as_live_provider: 'Simulator telemetry is being represented as live provider evidence.',
  alert_exists_without_detection: 'Alerts must be backed by at least one detection.',
  incident_exists_without_alert: 'Incidents must be linked to at least one alert.',
  response_action_exists_without_incident: 'Response actions must be linked to an incident.',
  cross_page_count_mismatch: 'Cross-page count mismatch detected. Reconcile canonical runtime totals before proceeding.',
};

const NEXT_ACTION_LABELS: Record<string, string> = {
  add_asset: 'Add a protected asset',
  verify_asset: 'Verify asset',
  create_monitoring_target: 'Create monitoring target',
  enable_monitored_system: 'Enable monitored system',
  start_simulator_signal: 'Start telemetry signal',
  view_detection: 'Review detections',
  open_incident: 'Open incident',
  export_evidence_package: 'Export evidence package',
  resolve_runtime_contradictions: 'Resolve runtime contradictions',
  review_reason_codes: 'Review runtime reason codes',
};

const RuntimeSummaryContext = createContext<RuntimeSummaryContextValue | null>(null);

function defaultSummary(): WorkspaceMonitoringTruth {
  return resolveWorkspaceMonitoringTruth(null);
}

function deriveProviderHealth(payload: import('./monitoring-status-contract').MonitoringRuntimeStatus | null): ProviderHealthInfo {
  if (!payload) {
    return { name: 'Ethereum RPC', status: 'unknown', chain: null, last_check: null, error_message: 'Runtime status unavailable' };
  }
  const reachable = payload.provider_reachable;
  const health = payload.provider_health;
  const name = (payload.provider_name as string | null | undefined) ?? 'Ethereum RPC';
  const chain = (payload.provider_kind as string | null | undefined) ?? null;
  const providerHealthRecords = Array.isArray(payload.provider_health) ? payload.provider_health as Record<string, unknown>[] : [];
  const firstHealthRecord = providerHealthRecords.length > 0 ? providerHealthRecords[0] : null;
  const lastCheck = (payload.refreshed_at as string | null | undefined)
    ?? (firstHealthRecord?.checked_at as string | null | undefined)
    ?? (payload.last_poll_at as string | null | undefined)
    ?? null;
  let status: ProviderHealthInfo['status'] = 'unknown';
  if (reachable === true || health === 'healthy') status = 'connected';
  else if (reachable === false || health === 'degraded') status = 'not_connected';
  // Fallback: use target_coverage metadata when direct provider fields are absent.
  // The production flat API response returns provider_status inside target_coverage[].metadata.
  if (status === 'unknown' && Array.isArray(payload.target_coverage) && payload.target_coverage.length > 0) {
    const hasLive = payload.target_coverage.some((tc) => tc?.metadata?.provider_status === 'live');
    if (hasLive) {
      status = 'connected';
    } else if (payload.target_coverage.some((tc) => tc?.metadata?.provider_status === 'degraded')) {
      status = 'not_connected';
    }
  }
  const errorMessage = status === 'not_connected'
    ? ((payload.degraded_reason as string | null | undefined) ?? 'Provider not reachable. Check EVM_RPC_URL / STAGING_EVM_RPC_URL.')
    : null;
  return { name, status, chain, last_check: lastCheck, error_message: errorMessage };
}

function deriveWorkerHealth(
  payload: import('./monitoring-status-contract').MonitoringRuntimeStatus | null,
  summary: WorkspaceMonitoringTruth,
): WorkerHealthInfo {
  const loopHealth = payload?.background_loop_health;
  const loopRunning = payload?.loop_running ?? loopHealth?.loop_running;
  let status: WorkerHealthInfo['status'] = 'unknown';
  if (loopRunning === true) status = 'running';
  else if (loopRunning === false) status = 'stopped';
  else if (summary.last_heartbeat_at) status = 'running';
  const consecutiveFailures = payload?.consecutive_failures ?? loopHealth?.consecutive_failures ?? 0;
  const nextPoll = payload?.next_retry_at ?? loopHealth?.next_retry_at ?? null;
  return {
    status,
    last_heartbeat: summary.last_heartbeat_at,
    last_poll: summary.last_poll_at,
    last_telemetry: summary.last_telemetry_at,
    consecutive_failures: Number(consecutiveFailures ?? 0),
    next_poll: nextPoll as string | null,
  };
}

export function RuntimeSummaryProvider({ children }: { children: React.ReactNode }) {
  const { authHeaders, isAuthenticated } = usePilotAuth();
  const [summary, setSummary] = useState<WorkspaceMonitoringTruth>(defaultSummary);
  const [runtime, setRuntime] = useState<import('./monitoring-status-contract').WorkspaceMonitoringRuntime | null>(null);
  const [rawPayload, setRawPayload] = useState<import('./monitoring-status-contract').MonitoringRuntimeStatus | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!isAuthenticated) return;
    setLoading(true);
    void fetchRuntimeStatusDeduped(authHeaders())
      .then((payload) => {
        setSummary(resolveWorkspaceMonitoringTruth(payload));
        setRuntime(payload?.workspace_monitoring_runtime ?? null);
        setRawPayload(payload ?? null);
      })
      .catch(() => { setSummary(resolveWorkspaceMonitoringTruth(null)); setRuntime(null); setRawPayload(null); })
      .finally(() => setLoading(false));
  }, [authHeaders, isAuthenticated]);

  const value = useMemo<RuntimeSummaryContextValue>(() => {
    const reasons = summary.continuity_reason_codes ?? [];
    const topReason = reasons[0] ?? summary.status_reason ?? 'summary_unavailable';
    const reasonMessageForCode = (code: string) => REASON_CODE_MESSAGES[code] ?? `Runtime condition: ${code.replaceAll('_', ' ')}.`;
    const evidenceLabel = summary.evidence_source_summary === 'live' ? 'Live provider evidence' : summary.evidence_source_summary === 'none' ? 'No evidence configured' : 'Simulator evidence';
    const existsLabel = `${summary.protected_assets_count} assets, ${summary.reporting_systems_count} reporting systems, ${summary.active_alerts_count} active alerts`;
    const missingLabel = reasonMessageForCode(topReason);
    const nextRequiredAction = summary.next_required_action ?? 'review_reason_codes';
    const nextActionLabel = NEXT_ACTION_LABELS[nextRequiredAction] ?? 'Review runtime reason codes';
    const fixCtaLabel = nextRequiredAction === 'resolve_runtime_contradictions'
      ? 'Fix monitoring contradictions'
      : 'Review monitoring setup';
    const providerHealth = deriveProviderHealth(rawPayload);
    const workerHealth = deriveWorkerHealth(rawPayload, summary);
    return { summary, runtime, loading, reasonMessageForCode, evidenceLabel, existsLabel, missingLabel, nextActionLabel, fixCtaLabel, providerHealth, workerHealth };
  }, [summary, runtime, rawPayload, loading]);

  return <RuntimeSummaryContext.Provider value={value}>{children}</RuntimeSummaryContext.Provider>;
}

export function useRuntimeSummary() {
  const context = useContext(RuntimeSummaryContext);
  if (!context) {
    throw new Error('useRuntimeSummary must be used within RuntimeSummaryProvider');
  }
  return context;
}
