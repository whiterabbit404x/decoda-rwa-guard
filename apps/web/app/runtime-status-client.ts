'use client';

import type { MonitoringRuntimeStatus } from './monitoring-status-contract';

const RUNTIME_STATUS_PROXY_PATH = '/api/ops/monitoring/runtime-status';

const inflightByWorkspace = new Map<string, Promise<MonitoringRuntimeStatus | null>>();

function workspaceKeyFromHeaders(headers: Record<string, string>): string {
  return String(headers['x-workspace-id'] ?? 'default');
}

export async function fetchRuntimeStatusDeduped(headers: Record<string, string>): Promise<MonitoringRuntimeStatus | null> {
  const workspaceKey = workspaceKeyFromHeaders(headers);
  const existing = inflightByWorkspace.get(workspaceKey);
  if (existing) {
    return existing;
  }
  const request = fetch(RUNTIME_STATUS_PROXY_PATH, { headers, cache: 'no-store' })
    .then(async (response) => {
      if (!response.ok) {
        return null;
      }
      return await response.json() as MonitoringRuntimeStatus;
    })
    .catch(() => null)
    .finally(() => {
      inflightByWorkspace.delete(workspaceKey);
    });
  inflightByWorkspace.set(workspaceKey, request);
  return await request;
}
