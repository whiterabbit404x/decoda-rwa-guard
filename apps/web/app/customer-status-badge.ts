export type CustomerStatusBadgeState =
  | 'live'
  | 'live_degraded'
  | 'degraded'
  | 'offline'
  | 'stale'
  | 'limited_coverage'
  | 'delayed'
  | 'unavailable';

export type LegacyCustomerStatusBadgeState = 'fallback' | 'sample' | 'demo';

export type CustomerStatusBadgeInputState =
  | CustomerStatusBadgeState
  | LegacyCustomerStatusBadgeState;

export function mapPayloadStateToCustomerBadge(
  state: CustomerStatusBadgeInputState
): CustomerStatusBadgeState {
  if (state === 'fallback' || state === 'sample' || state === 'demo') {
    return 'limited_coverage';
  }

  return state;
}
