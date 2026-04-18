import { expect, test } from '@playwright/test';

import { resolveRuntimeStatus } from '../app/use-live-workspace-feed';
import type { MonitoringRuntimeStatus } from '../app/monitoring-status-contract';

test.describe('useLiveWorkspaceFeed runtime semantics', () => {
  test('does not map active/idle/degraded runtime status to offline', async () => {
    const active = resolveRuntimeStatus({ monitoring_status: 'active', mode: 'LIVE' } as MonitoringRuntimeStatus, true);
    const idle = resolveRuntimeStatus({ monitoring_status: 'idle', mode: 'LIMITED_COVERAGE' } as MonitoringRuntimeStatus, true);
    const degraded = resolveRuntimeStatus({ monitoring_status: 'degraded', mode: 'DEGRADED' } as MonitoringRuntimeStatus, true);

    expect(active.offline).toBe(false);
    expect(active.explicitOfflineStreak).toBe(0);
    expect(idle.offline).toBe(false);
    expect(idle.explicitOfflineStreak).toBe(0);
    expect(degraded.offline).toBe(false);
    expect(degraded.explicitOfflineStreak).toBe(0);
  });

  test('offline only after repeated explicit offline/error statuses', async () => {
    const explicitOfflineFirst = resolveRuntimeStatus({ monitoring_status: 'offline', mode: 'OFFLINE' } as MonitoringRuntimeStatus, true);
    const explicitOfflineSecond = resolveRuntimeStatus(
      { monitoring_status: 'offline', mode: 'OFFLINE' } as MonitoringRuntimeStatus,
      true,
      explicitOfflineFirst.nextRuntime,
      explicitOfflineFirst.explicitOfflineStreak,
    );
    const explicitErrorSecond = resolveRuntimeStatus(
      { monitoring_status: 'error', mode: 'OFFLINE' } as MonitoringRuntimeStatus,
      true,
      explicitOfflineFirst.nextRuntime,
      explicitOfflineFirst.explicitOfflineStreak,
    );

    expect(explicitOfflineFirst.offline).toBe(false);
    expect(explicitOfflineFirst.explicitOfflineStreak).toBe(1);
    expect(explicitOfflineSecond.offline).toBe(true);
    expect(explicitErrorSecond.offline).toBe(true);
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
    expect(transientFailure.explicitOfflineStreak).toBe(0);
  });

  test('success → timeout → success does not trigger OFFLINE flicker', async () => {
    const firstSuccess = resolveRuntimeStatus(
      { monitoring_status: 'active', mode: 'LIVE' } as MonitoringRuntimeStatus,
      true,
    );
    const failedPoll = resolveRuntimeStatus(null, false, firstSuccess.nextRuntime);
    const secondSuccess = resolveRuntimeStatus(
      { monitoring_status: 'active', mode: 'LIVE' } as MonitoringRuntimeStatus,
      true,
      failedPoll.nextRuntime,
    );

    expect(firstSuccess.offline).toBe(false);
    expect(failedPoll.offline).toBe(false);
    expect(failedPoll.nextRuntime?.monitoring_status).toBe('active');
    expect(secondSuccess.offline).toBe(false);
    expect(secondSuccess.nextRuntime?.monitoring_status).toBe('active');
    expect(secondSuccess.explicitOfflineStreak).toBe(0);
  });
});
