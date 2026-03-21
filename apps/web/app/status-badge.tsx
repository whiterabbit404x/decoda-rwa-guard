import { DashboardPayloadState } from './dashboard-data';

type StatusBadgeProps = {
  state: DashboardPayloadState | 'live_degraded';
  compact?: boolean;
};

export function getStatusBadgeLabel(state: StatusBadgeProps['state']) {
  switch (state) {
    case 'live':
      return 'Live';
    case 'live_degraded':
      return 'Live (degraded)';
    case 'fallback':
      return 'Fallback';
    case 'sample':
      return 'Sample';
    default:
      return 'Unavailable';
  }
}

export default function StatusBadge({ state, compact = false }: StatusBadgeProps) {
  return <span className={`statusBadge statusBadge-${state}${compact ? ' compact' : ''}`}>{getStatusBadgeLabel(state)}</span>;
}
