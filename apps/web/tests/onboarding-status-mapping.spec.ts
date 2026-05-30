/**
 * Contracts for onboarding status mapping:
 * - 401 from /onboarding/progress must not render OFFLINE
 * - Expired token must show session-expired state
 * - runtime_status=live must render LIVE on onboarding page
 * - poll_without_telemetry_timestamp guard suppressed when live_runtime_verified
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

test.describe('onboarding 401 must not render OFFLINE', () => {
  test('onboarding client contains sessionExpired state', () => {
    expect(clientSrc).toContain('sessionExpired');
  });

  test('onboarding client checks response.status === 401', () => {
    expect(clientSrc).toContain('response.status === 401');
  });

  test('401 branch calls setSessionExpired and returns before setErrorMsg', () => {
    // Verify the 401 guard block ends with return before the setErrorMsg branch
    const idx401 = clientSrc.indexOf('response.status === 401');
    expect(idx401).toBeGreaterThan(-1);
    // Extract just the if-body: from the 401 check to the closing brace + return
    const branchEnd = clientSrc.indexOf('return;\n    }', idx401);
    expect(branchEnd).toBeGreaterThan(idx401);
    const branch = clientSrc.slice(idx401, branchEnd + 10);
    expect(branch).toContain('setSessionExpired');
    // setErrorMsg must NOT appear inside the 401 branch itself
    expect(branch).not.toContain('setErrorMsg');
  });

  test('onboarding client shows session-expired notice copy', () => {
    expect(clientSrc).toContain('Session expired');
    expect(clientSrc).toContain('session-expired-notice');
  });

  test('onboarding client shows sign-in link on session expired', () => {
    expect(clientSrc).toContain('/sign-in');
  });
});

test.describe('runtime-status fallback when progress unavailable', () => {
  test('onboarding client imports useRuntimeSummary', () => {
    expect(clientSrc).toContain('useRuntimeSummary');
  });

  test('onboarding client contains progressFromRuntimeSummary helper', () => {
    expect(clientSrc).toContain('progressFromRuntimeSummary');
  });

  test('onboarding client uses effectiveState derived from runtime summary fallback', () => {
    expect(clientSrc).toContain('effectiveState');
    expect(clientSrc).toContain('progressFromRuntimeSummary(summary)');
  });

  test('progressFromRuntimeSummary maps live runtime to all steps complete', () => {
    // When runtime_status=live, all four onboarding steps should be complete
    expect(clientSrc).toContain("runtime_status === 'live'");
    expect(clientSrc).toContain("status_reason === 'live_runtime_verified'");
  });
});

test.describe('guard suppression: poll_without_telemetry_timestamp', () => {
  test('live_runtime_verified with last_poll_at but no telemetry has no guard override', () => {
    const truth = resolveWorkspaceMonitoringTruth(
      liveRuntimeFixture({ last_telemetry_at: null }),
    );
    expect(truth.status_reason).toBe('live_runtime_verified');
    expect(truth.guard_flags).not.toContain('poll_without_telemetry_timestamp');
  });

  test('live_runtime_verified with last_heartbeat_at but no telemetry has no guard override', () => {
    const truth = resolveWorkspaceMonitoringTruth(
      liveRuntimeFixture({ last_poll_at: null, last_telemetry_at: null }),
    );
    expect(truth.status_reason).toBe('live_runtime_verified');
    expect(truth.guard_flags).not.toContain('heartbeat_without_telemetry_timestamp');
  });

  test('non-live backend still fires poll_without_telemetry_timestamp guard', () => {
    const truth = resolveWorkspaceMonitoringTruth(
      liveRuntimeFixture({
        runtime_status: 'degraded',
        status_reason: 'stale_telemetry',
        last_telemetry_at: null,
      }),
    );
    expect(truth.guard_flags).toContain('poll_without_telemetry_timestamp');
  });

  test('live_runtime_verified fixture has no guard-based status_reason override', () => {
    const truth = resolveWorkspaceMonitoringTruth(liveRuntimeFixture({ last_telemetry_at: null }));
    expect(truth.status_reason).not.toMatch(/^guard:/);
  });
});

test.describe('onboarding banner shows LIVE for live_runtime_verified', () => {
  test('truth model: live_runtime_verified fixture has no guard flags', () => {
    const truth = resolveWorkspaceMonitoringTruth(liveRuntimeFixture({ last_telemetry_at: null }));
    // Guard flags being empty means deriveBannerState can use the live runtime path
    expect(truth.runtime_status).toBe('live');
    expect(truth.status_reason).toBe('live_runtime_verified');
    expect((truth.guard_flags ?? []).length).toBe(0);
  });

  test('truth model: protected_assets_count reads from fixture', () => {
    const truth = resolveWorkspaceMonitoringTruth(liveRuntimeFixture());
    expect(truth.protected_assets_count).toBe(1);
  });

  test('truth model: reporting_systems_count reads from fixture', () => {
    const truth = resolveWorkspaceMonitoringTruth(liveRuntimeFixture());
    expect(truth.reporting_systems_count).toBe(2);
  });
});
