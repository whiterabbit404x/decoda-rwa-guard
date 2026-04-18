import { expect, test } from '@playwright/test';

import { resolveRuntimeStatus } from '../app/use-live-workspace-feed';
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

  test('offline only when runtime-status is explicitly offline/error', async () => {
    const explicitOffline = resolveRuntimeStatus({ monitoring_status: 'offline', mode: 'OFFLINE' } as MonitoringRuntimeStatus, true);
    const explicitError = resolveRuntimeStatus({ monitoring_status: 'error', mode: 'OFFLINE' } as MonitoringRuntimeStatus, true);

    expect(explicitOffline.offline).toBe(true);
    expect(explicitError.offline).toBe(true);
  });

  test('intermittent runtime fetch failure keeps last good runtime without false offline regression', async () => {
    const lastKnownGood = resolveRuntimeStatus(
      { monitoring_status: 'active', mode: 'LIVE' } as MonitoringRuntimeStatus,
      true,
    );
    const transientFailure = resolveRuntimeStatus(null, false, lastKnownGood.nextRuntime);

    expect(transientFailure.nextRuntime).toEqual(lastKnownGood.nextRuntime);
    expect(transientFailure.offline).toBe(false);
    expect(transientFailure.fetchWarning).toBe(true);
  });
});
