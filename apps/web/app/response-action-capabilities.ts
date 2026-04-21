export type ResponseActionMode = 'simulated' | 'recommended' | 'live';
export type LiveExecutionPath = 'safe' | 'governance' | 'manual_only' | 'unsupported';

export type ResponseActionCapability = {
  action_type: string;
  supported_modes: ResponseActionMode[];
  live_execution_path: LiveExecutionPath;
  reason?: string | null;
};

export function capabilityMapFromPayload(payload: any): Record<string, ResponseActionCapability> {
  const rows = Array.isArray(payload?.actions) ? payload.actions : [];
  return rows.reduce<Record<string, ResponseActionCapability>>((acc, row) => {
    const actionType = String(row?.action_type || '').trim();
    if (!actionType) return acc;
    acc[actionType] = {
      action_type: actionType,
      supported_modes: Array.isArray(row?.supported_modes) ? row.supported_modes : ['simulated', 'recommended'],
      live_execution_path: row?.live_execution_path || 'unsupported',
      reason: row?.reason || null,
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
