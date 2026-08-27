'use client';

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';

import { usePilotAuth } from './pilot-auth-context';
import { runtimeReasonMessage } from './runtime-reason-copy';
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
  refresh: () => Promise<void>;
};

const NEXT_ACTION_LABELS: Record<string, string> = {
  add_asset: 'Add a protected asset',
  verify_asset: 'Verify asset',
  create_monitoring_target: 'Create monitoring target',
  enable_monitored_system: 'Enable monitored system',
  start_simulator_signal: 'Start telemetry signal',
  view_detection: 'Review detections',
  diagnose_ingestion: 'Diagnose ingestion',
  open_incident: 'Open incident',
  export_evidence_package: 'Export evidence package',
  resolve_runtime_contradictions: 'Resolve runtime contradictions',
  review_reason_codes: 'Review runtime reason codes',
};

// Longer than RUNTIME_STATUS_FRESHNESS_MS in runtime-status-client so a revalidation
// tick actually reaches the backend instead of replaying the cached payload.
const RUNTIME_STATUS_REVALIDATE_MS = 90_000;

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
  // last_poll_at is the canonical "Provider poll" timestamp shown in the telemetry timeline.
  // It must be the primary source for "Last check" so both UI sections show the same value.
  // Fall back to provider_health_records.checked_at, then refreshed_at.
  const lastCheck = (payload.last_poll_at as string | null | undefined)
    ?? (firstHealthRecord?.checked_at as string | null | undefined)
    ?? (payload.refreshed_at as string | null | undefined)
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

  const load = useCallback(async (forceRefresh = false): Promise<void> => {
    if (!isAuthenticated) return;
    setLoading(true);
    try {
      const payload = await fetchRuntimeStatusDeduped(authHeaders(), forceRefresh ? { forceRefresh: true } : undefined);
      setSummary(resolveWorkspaceMonitoringTruth(payload));
      setRuntime(payload?.workspace_monitoring_runtime ?? null);
      setRawPayload(payload ?? null);
    } catch {
      setSummary(resolveWorkspaceMonitoringTruth(null));
      setRuntime(null);
      setRawPayload(null);
    } finally {
      setLoading(false);
    }
  }, [authHeaders, isAuthenticated]);

  // `refresh` forces a fresh runtime-status fetch, bypassing the freshness cache, so callers
  // (e.g. after activation or a monitored-systems repair) see canonical counts and the global
  // setup banner stops reporting "no protected assets" once one exists.
  const refresh = useCallback(async (): Promise<void> => {
    await load(true);
  }, [load]);

  useEffect(() => {
    void load();
  }, [load]);

  // Revalidate on an interval so a limitation the backend has already cleared cannot
  // stay pinned to the banner for the lifetime of the client session. Without this the
  // provider fetched exactly once per mount, so a status_reason that has since become
  // null kept rendering until a full page reload. The interval is deliberately longer
  // than the runtime-status client's freshness window so both providers share one
  // network fetch per cycle and the hot path stays light.
  useEffect(() => {
    if (!isAuthenticated) return;
    const timer = setInterval(() => { void load(); }, RUNTIME_STATUS_REVALIDATE_MS);
    return () => clearInterval(timer);
  }, [isAuthenticated, load]);

  const value = useMemo<RuntimeSummaryContextValue>(() => {
    const reasons = summary.continuity_reason_codes ?? [];
    const topReason = reasons[0] ?? summary.status_reason ?? 'summary_unavailable';
    // Guard-derived reasons arrive as `guard:<flag>`; runtimeReasonMessage resolves
    // both spellings to the same sentence so the raw wire code never reaches a
    // customer (e.g. "Runtime condition: guard:incident exists without alert").
    const reasonMessageForCode = runtimeReasonMessage;
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
    return { summary, runtime, loading, reasonMessageForCode, evidenceLabel, existsLabel, missingLabel, nextActionLabel, fixCtaLabel, providerHealth, workerHealth, refresh };
  }, [summary, runtime, rawPayload, loading, refresh]);

  return <RuntimeSummaryContext.Provider value={value}>{children}</RuntimeSummaryContext.Provider>;
}

export function useRuntimeSummary() {
  const context = useContext(RuntimeSummaryContext);
  if (!context) {
    throw new Error('useRuntimeSummary must be used within RuntimeSummaryProvider');
  }
  return context;
}
