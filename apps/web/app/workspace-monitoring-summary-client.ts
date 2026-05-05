'use client';

import { fetchRuntimeStatusDeduped } from './runtime-status-client';
import { DEFAULT_WORKSPACE_MONITORING_SUMMARY, resolveWorkspaceMonitoringSummaryContract, type WorkspaceMonitoringSummaryContract } from './workspace-monitoring-summary';

export async function fetchWorkspaceMonitoringSummary(
  headers: Record<string, string>,
): Promise<WorkspaceMonitoringSummaryContract> {
  const runtime = await fetchRuntimeStatusDeduped(headers).catch(() => null);
  return runtime ? resolveWorkspaceMonitoringSummaryContract(runtime) : DEFAULT_WORKSPACE_MONITORING_SUMMARY;
}
