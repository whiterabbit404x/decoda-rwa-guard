import { DashboardPayloadState } from './dashboard-data';
import { CustomerFacingBadgeState, mapPayloadStateToCustomerBadge } from './dashboard-status-presentation';

type StatusBadgeProps = {
  state: DashboardPayloadState | 'live_degraded' | CustomerFacingBadgeState;
  compact?: boolean;
};

function resolveCustomerFacingState(state: StatusBadgeProps['state']): CustomerFacingBadgeState {
  if (['degraded', 'offline', 'stale', 'limited_coverage', 'delayed', 'unavailable', 'live'].includes(state)) {
    return state as CustomerFacingBadgeState;
  }

  return mapPayloadStateToCustomerBadge(state);
}

export function getStatusBadgeLabel(state: StatusBadgeProps['state']) {
  switch (resolveCustomerFacingState(state)) {
    case 'live':
      return 'Live';
    case 'degraded':
      return 'Degraded';
    case 'offline':
      return 'Offline';
    case 'stale':
      return 'Stale';
    case 'limited_coverage':
      return 'Limited coverage';
    case 'delayed':
      return 'Delayed';
    default:
      return 'Unavailable';
  }
}

function getBadgeClassName(state: CustomerFacingBadgeState) {
  switch (state) {
    case 'limited_coverage':
      return 'fallback';
    case 'degraded':
    case 'delayed':
    case 'stale':
      return 'live_degraded';
    default:
      return state;
  }
}

export default function StatusBadge({ state, compact = false }: StatusBadgeProps) {
  const customerState = resolveCustomerFacingState(state);
  return <span className={`statusBadge statusBadge-${getBadgeClassName(customerState)}${compact ? ' compact' : ''}`}>{getStatusBadgeLabel(state)}</span>;
}
