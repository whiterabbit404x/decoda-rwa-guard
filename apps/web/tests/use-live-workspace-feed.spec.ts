import fs from 'node:fs';
import path from 'node:path';

import { expect, test } from '@playwright/test';

import { deriveMonitoringProjection, deriveWorkspaceHealth, resolveRuntimeStatus } from '../app/use-live-workspace-feed';
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

  test('derives a single monitoring truth and presentation projection from runtime summary', async () => {
    const projection = deriveMonitoringProjection({
      monitoring_status: 'active',
      mode: 'LIVE',
      workspace_monitoring_summary: {
        runtime_status: 'healthy',
        monitoring_mode: 'live',
        workspace_configured: true,
        configured_systems: 4,
        monitored_systems_count: 4,
        reporting_systems: 3,
        protected_assets_count: 6,
        freshness_status: 'fresh',
        confidence_status: 'high',
        evidence_source: 'live',
        status_reason: null,
        contradiction_flags: [],
        last_telemetry_at: '2026-01-01T00:00:00.000Z',
      },
    } as MonitoringRuntimeStatus);

    expect(projection.truth.runtime_status).toBe('healthy');
    expect(projection.presentation.status).toBe('live');
    expect(projection.presentation.statusLabel).toBe('LIVE');
  });

  test('source-level regression: consumers avoid legacy feed offline/degraded/stale flags for monitoring display', async () => {
    const consumers = [
      'apps/web/app/threat-operations-panel.tsx',
      'apps/web/app/dashboard-page-content.tsx',
      'apps/web/app/workspace-ownership-bar.tsx',
    ];
    for (const relativePath of consumers) {
      const source = fs.readFileSync(path.resolve(process.cwd(), relativePath), 'utf8');
      expect(source.includes('feed.offline')).toBeFalsy();
      expect(source.includes('feed.degraded')).toBeFalsy();
      expect(source.includes('feed.stale')).toBeFalsy();
    }
  });
});
