import type { DashboardPayloadState } from './dashboard-data';

export type CustomerFacingBadgeState =
  | 'live'
  | 'degraded'
  | 'offline'
  | 'stale'
  | 'limited_coverage'
  | 'delayed'
  | 'unavailable';

export function mapPayloadStateToCustomerBadge(state: DashboardPayloadState | 'live_degraded'): CustomerFacingBadgeState {
  switch (state) {
    case 'live':
      return 'live';
    case 'live_degraded':
      return 'degraded';
    case 'fallback':
      return 'limited_coverage';
    case 'sample':
      return 'unavailable';
    default:
      return 'unavailable';
  }
}

export function mapSourceToCustomerBadge(source: 'live' | 'fallback', degraded?: boolean): CustomerFacingBadgeState {
  if (source === 'live' && !degraded) {
    return 'live';
  }

  if (source === 'live' && degraded) {
    return 'degraded';
  }

  return 'limited_coverage';
}
