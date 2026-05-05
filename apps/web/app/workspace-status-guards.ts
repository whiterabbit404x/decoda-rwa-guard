import { mapPayloadStateToCustomerBadge, type CustomerStatusBadgeState } from './customer-status-badge';
import type { WorkspaceMonitoringSummaryContract } from './workspace-monitoring-summary';

export function statusBadgeStateFromSummary(summary: WorkspaceMonitoringSummaryContract): CustomerStatusBadgeState {
  if (summary.monitoring_status === 'offline') return 'offline';
  if (summary.freshness_status === 'stale') return 'stale';
  if (summary.monitoring_status === 'limited') return 'limited_coverage';
  if (summary.confidence_status === 'low' || summary.confidence_status === 'unavailable') return 'degraded';
  return mapPayloadStateToCustomerBadge('live');
}

export function statusLabelFromSummary(summary: WorkspaceMonitoringSummaryContract): string {
  if (!summary.workspace_configured) return 'Setup required';
  if (summary.monitoring_status === 'offline') return 'Offline';
  if (summary.monitoring_status === 'limited') return 'Limited coverage';
  if (summary.freshness_status === 'stale') return 'Stale';
  return 'Live';
}
