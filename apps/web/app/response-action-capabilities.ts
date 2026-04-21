export type ResponseActionMode = 'simulated' | 'recommended' | 'live';
export type LiveExecutionPath = 'safe' | 'governance' | 'manual_only' | 'unsupported';

export type ResponseActionCapability = {
  action_type: string;
  supported_modes: ResponseActionMode[];
  live_execution_path: LiveExecutionPath;
  reason?: string | null;
};

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
  const fallbackReason = String(payload?.reason || payload?.message || '').trim();
  if (executionState === 'simulated_executed' || executionState === 'live_executed') {
    return { isSuccess: true, text: 'Action executed.' };
  }
  if (executionState === 'proposed') {
    if (liveExecutionPath === 'safe') return { isSuccess: true, text: 'Proposed to Safe' };
    if (liveExecutionPath === 'governance') return { isSuccess: true, text: 'Governance action submitted' };
    if (liveExecutionPath === 'manual_only') return { isSuccess: false, text: 'Manual-only in live mode' };
  }
  if (executionState === 'unsupported') {
    return { isSuccess: false, text: 'Unsupported live action' };
  }
  if (fallbackReason) return { isSuccess: false, text: fallbackReason };
  return { isSuccess: false, text: 'Action could not be executed.' };
}
