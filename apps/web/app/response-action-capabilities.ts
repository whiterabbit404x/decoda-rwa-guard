export type ResponseActionMode = 'simulated' | 'recommended' | 'live';
export type LiveExecutionPath = 'safe' | 'governance' | 'manual_only' | 'unsupported';

export type ResponseActionCapability = {
  action_type: string;
  action_intent?: string;
  supported_modes: ResponseActionMode[];
  live_execution_path: LiveExecutionPath;
  reason?: string | null;
};
export const RESPONSE_ACTION_LIFECYCLE_STATES = ['simulation', 'manual', 'live', 'approval-required', 'executed', 'failed', 'rolled-back'] as const;

export type ExecutionStateLabel =
  | 'Simulated only'
  | 'Proposal created'
  | 'Awaiting multisig approval'
  | 'Submitted on-chain'
  | 'Confirmed on-chain'
  | 'Failed'
  | 'Unsupported provider'
  | 'Cancelled'
  | 'Pending';

export function executionStateLabel(executionState: string | undefined | null): ExecutionStateLabel {
  switch ((executionState ?? '').toLowerCase()) {
    case 'simulated':
    case 'simulated_executed':
      return 'Simulated only';
    case 'proposal_created':
    case 'proposed':
      return 'Proposal created';
    case 'awaiting_approval':
    case 'recommended_approved':
      return 'Awaiting multisig approval';
    case 'submitted':
      return 'Submitted on-chain';
    case 'confirmed':
      return 'Confirmed on-chain';
    case 'failed':
      return 'Failed';
    case 'unsupported':
    case 'live_manual_required':
      return 'Unsupported provider';
    case 'cancelled':
    case 'canceled':
      return 'Cancelled';
    default:
      return 'Pending';
  }
}

export function executionStateIsLive(executionState: string | undefined | null): boolean {
  return ['submitted', 'confirmed'].includes((executionState ?? '').toLowerCase());
}

const RESPONSE_ACTION_MODES: ResponseActionMode[] = ['simulated', 'recommended', 'live'];
const LIVE_EXECUTION_PATHS: LiveExecutionPath[] = ['safe', 'governance', 'manual_only', 'unsupported'];

export function capabilityMapFromPayload(payload: any): Record<string, ResponseActionCapability> {
  const rows: Array<Record<string, unknown>> = Array.isArray(payload?.actions) ? payload.actions : [];
  return rows.reduce((acc: Record<string, ResponseActionCapability>, row) => {
    const actionType = String(row?.action_type || '').trim();
    if (!actionType) return acc;
    const supportedModes = Array.isArray(row?.supported_modes)
      ? row.supported_modes.filter((mode): mode is ResponseActionMode => RESPONSE_ACTION_MODES.includes(mode as ResponseActionMode))
      : [];
    const liveExecutionPath = LIVE_EXECUTION_PATHS.includes(row?.live_execution_path as LiveExecutionPath)
      ? row.live_execution_path as LiveExecutionPath
      : 'unsupported';
    acc[actionType] = {
      action_type: actionType,
      action_intent: typeof row?.action_intent === 'string' ? row.action_intent : undefined,
      supported_modes: supportedModes.length ? supportedModes : ['simulated', 'recommended'],
      live_execution_path: liveExecutionPath,
      reason: typeof row?.reason === 'string' ? row.reason : null,
    };
    return acc;
  }, {});
}

export function actionModeLabel(mode: ResponseActionMode): 'SIMULATED' | 'RECOMMENDED (SIMULATED)' | 'LIVE' {
  if (mode === 'live') return 'LIVE';
  if (mode === 'recommended') return 'RECOMMENDED (SIMULATED)';
  return 'SIMULATED';
}

export function isActionDisabledInMode(capability: ResponseActionCapability | undefined, mode: ResponseActionMode): boolean {
  if (!capability) return mode === 'live';
  return !capability.supported_modes.includes(mode);
}

export function actionDisabledReason(capability: ResponseActionCapability | undefined, mode: ResponseActionMode): string | null {
  if (!isActionDisabledInMode(capability, mode)) return null;
  if (mode !== 'live') return 'Mode not supported for this action';
  return capability?.reason || 'Unsupported live action';
}

export function responseActionExecutionMessage(payload: any): { isSuccess: boolean; text: string } {
  const executionState = String(payload?.execution_state || '').trim().toLowerCase();
  const liveExecutionPath = String(payload?.live_execution_path || '').trim().toLowerCase();
  const txHash = String(payload?.tx_hash || '').trim();
  const fallbackReason = String(payload?.reason || payload?.message || '').trim();

  // Canonical states (new state machine)
  if (executionState === 'simulated') {
    return { isSuccess: true, text: 'Simulated only — no on-chain transaction submitted.' };
  }
  if (executionState === 'proposal_created') {
    return { isSuccess: true, text: 'Proposal created — awaiting multisig approval.' };
  }
  if (executionState === 'awaiting_approval') {
    return { isSuccess: true, text: 'Awaiting multisig approval.' };
  }
  if (executionState === 'submitted') {
    return { isSuccess: true, text: txHash ? `Submitted on-chain (tx: ${txHash.slice(0, 10)}…)` : 'Submitted on-chain.' };
  }
  if (executionState === 'confirmed') {
    return { isSuccess: true, text: txHash ? `Confirmed on-chain (tx: ${txHash.slice(0, 10)}…)` : 'Confirmed on-chain.' };
  }
  if (executionState === 'failed') {
    return { isSuccess: false, text: fallbackReason || 'Action failed.' };
  }
  if (executionState === 'cancelled' || executionState === 'canceled') {
    return { isSuccess: false, text: 'Action cancelled.' };
  }
  if (executionState === 'unsupported' || executionState === 'live_manual_required') {
    return { isSuccess: false, text: fallbackReason || 'Unsupported provider — live execution unavailable.' };
  }

  // Legacy states (backward compat)
  if (executionState === 'simulated_executed') {
    return { isSuccess: true, text: 'Simulated only — no on-chain transaction submitted.' };
  }
  if (executionState === 'recommended_approved' || executionState === 'approval_required') {
    return { isSuccess: true, text: 'Awaiting multisig approval.' };
  }
  if (executionState === 'rolled_back') {
    return { isSuccess: false, text: 'Action rolled back.' };
  }
  if (executionState === 'proposed') {
    if (liveExecutionPath === 'safe') return { isSuccess: true, text: 'Proposal created — awaiting Safe multisig approval.' };
    if (liveExecutionPath === 'governance') return { isSuccess: true, text: 'Governance proposal submitted.' };
    if (liveExecutionPath === 'manual_only') return { isSuccess: false, text: 'Requires manual execution in live mode.' };
    return { isSuccess: true, text: 'Proposal created — awaiting approval.' };
  }

  if (fallbackReason) return { isSuccess: false, text: fallbackReason };
  return { isSuccess: false, text: 'Action could not be executed.' };
}
