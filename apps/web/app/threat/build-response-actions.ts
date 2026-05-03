import type { ResponseActionCapability } from '../response-action-capabilities';
import type { ResponseAction } from './response-action-panel';

type ThreatActionButtonState = {
  disabled: boolean;
  reason: string;
  noOpMessage: string;
  nextStepLabel: string;
  nextStepHref: string;
};

type ThreatActionButtonId =
  | 'sim-notify-team'
  | 'sim-revoke-approval'
  | 'rec-freeze-wallet'
  | 'rec-disable-monitored-system'
  | 'live-freeze-wallet'
  | 'live-revoke-approval';

export function buildResponseActionsModel(actionCapabilities: Record<string, ResponseActionCapability>) {
  const responseActionCapabilities = Object.entries(actionCapabilities)
    .filter(([, enabled]) => enabled)
    .map(([key]) => key.replaceAll('_', ' '));

  const actionButtons = {} as Record<ThreatActionButtonId, ThreatActionButtonState>;
  return { responseActionCapabilities, actionButtons };
}

export function buildResponseActionList(params: {
  actionButtons: Record<ThreatActionButtonId, ThreatActionButtonState>;
  onSimNotifyTeam: () => void;
  onSimRevokeApproval: () => void;
}): Array<ResponseAction> {
  return [
    {
      id: 'sim-notify-team',
      label: 'Run simulated response',
      state: 'simulation_only',
      disabled: params.actionButtons['sim-notify-team'].disabled,
      reason: params.actionButtons['sim-notify-team'].reason,
      onClick: params.onSimNotifyTeam,
    },
    {
      id: 'sim-revoke-approval',
      label: 'Revoke approval',
      state: 'manual_recommendation',
      disabled: params.actionButtons['sim-revoke-approval'].disabled,
      reason: params.actionButtons['sim-revoke-approval'].reason,
      onClick: params.onSimRevokeApproval,
    },
  ];
}
