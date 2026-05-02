import type { ResponseActionCapability } from '../response-action-capabilities';

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
