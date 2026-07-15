/**
 * Contracts for onboarding status handling:
 * - 401 from an onboarding API call must show the session-expired state (never OFFLINE)
 * - The workspace-monitoring-truth guard-suppression model is unchanged
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import type { MonitoringRuntimeStatus } from '../app/monitoring-status-contract';
import { resolveWorkspaceMonitoringTruth } from '../app/workspace-monitoring-truth';

const clientSrc = fs.readFileSync(
  path.join(__dirname, '..', 'app', '(product)', 'onboarding-page-client.tsx'),
  'utf-8',
);

function liveRuntimeFixture(overrides: Record<string, unknown> = {}): MonitoringRuntimeStatus {
  return {
    runtime_status: 'live',
    status_reason: 'live_runtime_verified',
    reporting_systems_count: 2,
    protected_assets_count: 1,
    last_poll_at: '2026-05-29T10:00:00Z',
    last_heartbeat_at: '2026-05-29T10:00:00Z',
    last_detection_at: '2026-05-29T09:55:00Z',
    contradiction_flags: [],
    ...overrides,
  } as unknown as MonitoringRuntimeStatus;
}

test.describe('onboarding 401 shows session-expired, never OFFLINE', () => {
  test('onboarding client tracks a sessionExpired state', () => {
    expect(clientSrc).toContain('sessionExpired');
    expect(clientSrc).toContain('setSessionExpired');
  });

  test('a 401 response sets the session-expired state', () => {
    expect(clientSrc).toContain('res.status === 401');
    const idx = clientSrc.indexOf('res.status === 401');
    const branch = clientSrc.slice(idx, idx + 80);
    expect(branch).toContain('setSessionExpired(true)');
  });

  test('session-expired notice + sign-in link are rendered', () => {
    expect(clientSrc).toContain('Session expired');
    expect(clientSrc).toContain('session-expired-notice');
    expect(clientSrc).toContain('/sign-in');
  });
});

test.describe('guard suppression: poll_without_telemetry_timestamp (unchanged model)', () => {
  test('live_runtime_verified with last_poll_at but no telemetry has no guard override', () => {
    const truth = resolveWorkspaceMonitoringTruth(liveRuntimeFixture({ last_telemetry_at: null }));
    expect(truth.status_reason).toBe('live_runtime_verified');
    expect(truth.guard_flags).not.toContain('poll_without_telemetry_timestamp');
  });

  test('live_runtime_verified with last_heartbeat_at but no telemetry has no guard override', () => {
    const truth = resolveWorkspaceMonitoringTruth(liveRuntimeFixture({ last_poll_at: null, last_telemetry_at: null }));
    expect(truth.status_reason).toBe('live_runtime_verified');
    expect(truth.guard_flags).not.toContain('heartbeat_without_telemetry_timestamp');
  });

  test('non-live backend still fires poll_without_telemetry_timestamp guard', () => {
    const truth = resolveWorkspaceMonitoringTruth(
      liveRuntimeFixture({ runtime_status: 'degraded', status_reason: 'stale_telemetry', last_telemetry_at: null }),
    );
    expect(truth.guard_flags).toContain('poll_without_telemetry_timestamp');
  });

  test('live_runtime_verified fixture has no guard-based status_reason override', () => {
    const truth = resolveWorkspaceMonitoringTruth(liveRuntimeFixture({ last_telemetry_at: null }));
    expect(truth.status_reason).not.toMatch(/^guard:/);
  });
});

test.describe('workspace monitoring truth model reads fixture counts', () => {
  test('live_runtime_verified fixture has no guard flags', () => {
    const truth = resolveWorkspaceMonitoringTruth(liveRuntimeFixture({ last_telemetry_at: null }));
    expect(truth.runtime_status).toBe('live');
    expect(truth.status_reason).toBe('live_runtime_verified');
    expect((truth.guard_flags ?? []).length).toBe(0);
  });

  test('protected_assets_count reads from fixture', () => {
    expect(resolveWorkspaceMonitoringTruth(liveRuntimeFixture()).protected_assets_count).toBe(1);
  });

  test('reporting_systems_count reads from fixture', () => {
    expect(resolveWorkspaceMonitoringTruth(liveRuntimeFixture()).reporting_systems_count).toBe(2);
  });
});
