export function buildTechnicalRuntimeDetails(params: {
  provenanceLabel: string;
  provenanceExplanation: string;
  lastSuccessfulRefreshAt: string | null;
  lastSuccessfulRuntimeRefreshAt: string | null;
  lastSuccessfulTimelineRefreshAt: string | null;
  continuityChecks: string[];
  reconcileUiState: string;
  activeReconcileId: string | null;
  lastSuccessfulReconcileAt: string | null;
  loopState: string;
  consecutiveFailures: number;
  lastSuccessfulCycle: string | null;
  ensuringProofChain: boolean;
  proofChainEnabled: boolean;
  formatAbsoluteTime: (value: string | null) => string;
}) {
  return {
    summaryLine: `Data provenance (${params.provenanceLabel}): ${params.provenanceExplanation}`,
    diagnostics: [
      `Last successful monitoring refresh: ${params.formatAbsoluteTime(params.lastSuccessfulRefreshAt)}`,
      `Last successful runtime refresh: ${params.formatAbsoluteTime(params.lastSuccessfulRuntimeRefreshAt)}`,
      `Last successful timeline refresh: ${params.formatAbsoluteTime(params.lastSuccessfulTimelineRefreshAt)}`,
    ],
    continuityChecks: params.continuityChecks,
    reconcileInternals: [
      `state=${params.reconcileUiState}`,
      `active_reconcile_id=${params.activeReconcileId ?? 'none'}`,
      `last_successful_reconcile_at=${params.formatAbsoluteTime(params.lastSuccessfulReconcileAt)}`,
    ],
    loopHealthInternals: [
      `loop_state=${params.loopState}`,
      `consecutive_failures=${params.consecutiveFailures}`,
      `last_successful_cycle=${params.formatAbsoluteTime(params.lastSuccessfulCycle)}`,
    ],
    proofChainInternals: [
      `ensuring=${params.ensuringProofChain ? 'yes' : 'no'}`,
      `proof_chain_enabled=${params.proofChainEnabled ? 'yes' : 'no'}`,
    ],
  };
}
