import {
  CustomerStatusBadgeInputState,
  CustomerStatusBadgeState,
  mapPayloadStateToCustomerBadge,
} from './customer-status-badge';

type StatusBadgeProps = {
  state: CustomerStatusBadgeInputState;
  compact?: boolean;
};

export function getStatusBadgeLabel(state: CustomerStatusBadgeState) {
  switch (state) {
    case 'live':
      return 'Live';
    case 'live_degraded':
      return 'Live (degraded)';
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

export default function StatusBadge({ state, compact = false }: StatusBadgeProps) {
  const normalizedState = mapPayloadStateToCustomerBadge(state);
  return <span className={`statusBadge statusBadge-${normalizedState}${compact ? ' compact' : ''}`}>{getStatusBadgeLabel(normalizedState)}</span>;
}
