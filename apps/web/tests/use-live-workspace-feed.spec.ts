import { expect, test } from '@playwright/test';

import { deriveWorkspaceHealth, resolveRuntimeStatus } from '../app/use-live-workspace-feed';
import type { MonitoringRuntimeStatus } from '../app/monitoring-status-contract';

test.describe('useLiveWorkspaceFeed runtime semantics', () => {
  test('does not map active/idle/degraded runtime status to offline', async () => {
    const active = resolveRuntimeStatus({ monitoring_status: 'active', mode: 'LIVE' } as MonitoringRuntimeStatus, true);
    const idle = resolveRuntimeStatus({ monitoring_status: 'idle', mode: 'LIMITED_COVERAGE' } as MonitoringRuntimeStatus, true);
    const degraded = resolveRuntimeStatus({ monitoring_status: 'degraded', mode: 'DEGRADED' } as MonitoringRuntimeStatus, true);

    expect(active.offline).toBe(false);
    expect(idle.offline).toBe(false);
    expect(degraded.offline).toBe(false);
  });

  test('workspace truth summary exclusively drives degraded/offline state', async () => {
    const healthyRuntime = resolveRuntimeStatus({
      monitoring_status: 'degraded',
      mode: 'DEGRADED',
      workspace_monitoring_summary: { runtime_status: 'healthy' },
    } as MonitoringRuntimeStatus, true);
    const offlineRuntime = resolveRuntimeStatus({
      monitoring_status: 'active',
      mode: 'LIVE',
      workspace_monitoring_summary: { runtime_status: 'offline' },
    } as MonitoringRuntimeStatus, true);
    const unknownRuntime = resolveRuntimeStatus({ monitoring_status: 'active', mode: 'LIVE' } as MonitoringRuntimeStatus, true);
    const healthy = deriveWorkspaceHealth(healthyRuntime);
    const offline = deriveWorkspaceHealth(offlineRuntime);
    const unknown = deriveWorkspaceHealth(unknownRuntime);

    expect(healthy.degraded).toBe(false);
    expect(healthy.offline).toBe(false);
    expect(offline.degraded).toBe(true);
    expect(offline.offline).toBe(true);
    expect(unknown.degraded).toBe(true);
    expect(unknown.offline).toBe(true);
  });

  test('offline only when runtime-status is offline/error or runtime-status request fails', async () => {
    const explicitOffline = resolveRuntimeStatus({ monitoring_status: 'offline', mode: 'OFFLINE' } as MonitoringRuntimeStatus, true);
    const explicitError = resolveRuntimeStatus({ monitoring_status: 'error', mode: 'OFFLINE' } as MonitoringRuntimeStatus, true);
    const unreachableRuntime = resolveRuntimeStatus(null, false);

    expect(explicitOffline.offline).toBe(true);
    expect(explicitError.offline).toBe(true);
    expect(unreachableRuntime.offline).toBe(true);
  });
});
