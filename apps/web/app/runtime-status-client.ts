'use client';

import type { MonitoringRuntimeStatus } from './monitoring-status-contract';

const RUNTIME_STATUS_PROXY_PATH = '/api/ops/monitoring/runtime-status';
// Keep runtime-status hot-path polling light while backend latency regression is being remediated.
const RUNTIME_STATUS_FRESHNESS_MS = 60_000;

const inflightByWorkspace = new Map<string, Promise<MonitoringRuntimeStatus | null>>();
const recentByWorkspace = new Map<string, { payload: MonitoringRuntimeStatus | null; fetchedAt: number }>();

function workspaceKeyFromHeaders(headers: Record<string, string>): string {
  const workspaceHeaderValue = Object.entries(headers).find(([key]) => key.toLowerCase() === 'x-workspace-id')?.[1] ?? 'default';
  return String(workspaceHeaderValue);
}

type RuntimeStatusFetchOptions = {
  forceRefresh?: boolean;
};

export async function fetchRuntimeStatusDeduped(
  headers: Record<string, string>,
  options?: RuntimeStatusFetchOptions,
): Promise<MonitoringRuntimeStatus | null> {
  const workspaceKey = workspaceKeyFromHeaders(headers);
  if (!options?.forceRefresh) {
    const cached = recentByWorkspace.get(workspaceKey);
    if (cached && (Date.now() - cached.fetchedAt) <= RUNTIME_STATUS_FRESHNESS_MS) {
      return cached.payload;
    }
  }
  const existing = inflightByWorkspace.get(workspaceKey);
  if (existing) {
    return existing;
  }
  const requestStartedAt = typeof performance !== 'undefined' ? performance.now() : Date.now();
  const request = fetch(RUNTIME_STATUS_PROXY_PATH, { headers, cache: 'no-store' })
    .then(async (response) => {
      const requestFinishedAt = typeof performance !== 'undefined' ? performance.now() : Date.now();
      if (process.env.NODE_ENV !== 'production') {
        console.info('[dashboard-perf] runtime-status fetch', {
          path: RUNTIME_STATUS_PROXY_PATH,
          ok: response.ok,
          status: response.status,
          durationMs: Number((requestFinishedAt - requestStartedAt).toFixed(1)),
          fetchedAt: new Date().toISOString(),
        });
      }
      if (!response.ok) {
        return null;
      }
      return await response.json() as MonitoringRuntimeStatus;
    })
    .catch(() => null)
    .then((payload) => {
      recentByWorkspace.set(workspaceKey, { payload, fetchedAt: Date.now() });
      return payload;
    })
    .finally(() => {
      inflightByWorkspace.delete(workspaceKey);
    });
  inflightByWorkspace.set(workspaceKey, request);
  return await request;
}

export function clearRuntimeStatusCacheForTests(): void {
  inflightByWorkspace.clear();
  recentByWorkspace.clear();
}
