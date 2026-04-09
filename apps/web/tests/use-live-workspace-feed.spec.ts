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

  test('ancillary endpoint failures degrade but do not force offline when runtime-status is healthy', async () => {
    const runtime = resolveRuntimeStatus({ monitoring_status: 'active', mode: 'LIVE' } as MonitoringRuntimeStatus, true);
    const health = deriveWorkspaceHealth(runtime, true);

    expect(health.degraded).toBe(true);
    expect(health.offline).toBe(false);
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
