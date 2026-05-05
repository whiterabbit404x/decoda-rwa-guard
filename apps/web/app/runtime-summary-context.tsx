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
  fixCtaLabel: string;
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
    const fixCtaLabel = summary.next_required_action === 'resolve_runtime_contradictions'
      ? 'Fix monitoring contradictions'
      : 'Review monitoring setup';
    return { summary, runtime, loading, reasonMessageForCode, evidenceLabel, existsLabel, missingLabel, nextActionLabel, fixCtaLabel };
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
