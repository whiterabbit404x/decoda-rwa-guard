'use client';

import { createContext, useContext, useEffect, useMemo, useState } from 'react';

import { usePilotAuth } from './pilot-auth-context';
import { fetchRuntimeStatusDeduped } from './runtime-status-client';
import { resolveWorkspaceMonitoringTruth, type WorkspaceMonitoringTruth } from './workspace-monitoring-truth';

type RuntimeSummaryContextValue = {
  summary: WorkspaceMonitoringTruth;
  runtime: import('./monitoring-status-contract').WorkspaceMonitoringRuntime | null;
  loading: boolean;
  reasonMessageForCode: (code: string) => string;
  evidenceLabel: string;
  existsLabel: string;
  missingLabel: string;
  nextActionLabel: string;
};

const REASON_CODE_MESSAGES: Record<string, string> = {
  summary_unavailable: 'Runtime summary is unavailable. Recheck workspace connectivity.',
  workspace_unconfigured: 'Workspace setup is incomplete. Finish onboarding to enable live monitoring.',
  no_reporting_systems: 'No monitored systems are reporting. Enable and verify monitoring sources.',
  stale_telemetry: 'Telemetry is stale. Investigate worker health and source ingestion lag.',
  no_live_evidence: 'No live evidence has been persisted yet. Trigger and validate a real detection path.',
};

const RuntimeSummaryContext = createContext<RuntimeSummaryContextValue | null>(null);

function defaultSummary(): WorkspaceMonitoringTruth {
  return resolveWorkspaceMonitoringTruth(null);
}

export function RuntimeSummaryProvider({ children }: { children: React.ReactNode }) {
  const { authHeaders, isAuthenticated } = usePilotAuth();
  const [summary, setSummary] = useState<WorkspaceMonitoringTruth>(defaultSummary);
  const [runtime, setRuntime] = useState<import('./monitoring-status-contract').WorkspaceMonitoringRuntime | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!isAuthenticated) return;
    setLoading(true);
    void fetchRuntimeStatusDeduped(authHeaders())
      .then((payload) => { setSummary(resolveWorkspaceMonitoringTruth(payload)); setRuntime(payload?.workspace_monitoring_runtime ?? null); })
      .catch(() => { setSummary(resolveWorkspaceMonitoringTruth(null)); setRuntime(null); })
      .finally(() => setLoading(false));
  }, [authHeaders, isAuthenticated]);

  const value = useMemo<RuntimeSummaryContextValue>(() => {
    const reasons = summary.continuity_reason_codes ?? [];
    const topReason = reasons[0] ?? summary.status_reason ?? 'summary_unavailable';
    const reasonMessageForCode = (code: string) => REASON_CODE_MESSAGES[code] ?? `Runtime condition: ${code.replaceAll('_', ' ')}.`;
    const evidenceLabel = summary.evidence_source_summary === 'live' ? 'Live provider evidence' : summary.evidence_source_summary === 'none' ? 'No evidence configured' : 'Simulator evidence';
    const existsLabel = `${summary.protected_assets_count} assets, ${summary.reporting_systems_count} reporting systems, ${summary.active_alerts_count} active alerts`;
    const missingLabel = reasonMessageForCode(topReason);
    const nextActionLabel = summary.next_required_action ? summary.next_required_action.replaceAll('_', ' ') : 'review reason codes';
    return { summary, runtime, loading, reasonMessageForCode, evidenceLabel, existsLabel, missingLabel, nextActionLabel };
  }, [summary, runtime, loading]);

  return <RuntimeSummaryContext.Provider value={value}>{children}</RuntimeSummaryContext.Provider>;
}

export function useRuntimeSummary() {
  const context = useContext(RuntimeSummaryContext);
  if (!context) {
    throw new Error('useRuntimeSummary must be used within RuntimeSummaryProvider');
  }
  return context;
}

