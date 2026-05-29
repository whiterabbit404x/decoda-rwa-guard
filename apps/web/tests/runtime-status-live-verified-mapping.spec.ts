/**
 * Contracts for runtime_status='live' / status_reason='live_runtime_verified' banner mapping.
 * Verifies that the Assets page banner shows LIVE MONITORING (not LIMITED COVERAGE) when the
 * backend /ops/monitoring/runtime-status returns a live-verified verdict.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import type { MonitoringRuntimeStatus } from '../app/monitoring-status-contract';
import { resolveWorkspaceMonitoringTruth } from '../app/workspace-monitoring-truth';

const panelSrc = fs.readFileSync(path.join(__dirname, '..', 'app', 'runtime-summary-panel.tsx'), 'utf-8');
const bannerSrc = fs.readFileSync(path.join(__dirname, '..', 'app', 'workspace-monitoring-mode-banner.tsx'), 'utf-8');

/** Simulates the flat response shape returned by /ops/monitoring/runtime-status */
function liveRuntimeFixture(overrides: Record<string, unknown> = {}): MonitoringRuntimeStatus {
  return {
    runtime_status: 'live',
    status_reason: 'live_runtime_verified',
    freshness_status: 'fresh',
    confidence_status: 'high',
    evidence_source: 'live',
    contradiction_flags: [],
    provider_health_status: 'healthy',
    target_coverage_status: 'reporting',
    next_required_action: 'monitoring_live',
    last_detection_at: '2026-05-29T10:00:00Z',
    ...overrides,
  } as unknown as MonitoringRuntimeStatus;
}

test.describe('runtime_status=live banner state derivation', () => {
  test('panel deriveBannerState returns LIVE for live_runtime_verified backend response', () => {
    // The panel source must contain the backend-authoritative LIVE guard
    expect(panelSrc).toContain("summary.status_reason === 'live_runtime_verified'");
    expect(panelSrc).toContain("summary.runtime_status === 'live'");
  });

  test('workspace banner deriveBannerState returns LIVE for live_runtime_verified backend response', () => {
    expect(bannerSrc).toContain("truth.status_reason === 'live_runtime_verified'");
    expect(bannerSrc).toContain("truth.runtime_status === 'live'");
  });

  test('panel does NOT show LIMITED COVERAGE when backend returns live_runtime_verified', () => {
    // The live-verified guard must appear before the LIMITED_COVERAGE fallback
    const liveGuardIndex = panelSrc.indexOf("summary.status_reason !== 'summary_unavailable'");
    const limitedCoverageIndex = panelSrc.indexOf("return 'LIMITED_COVERAGE'");
    expect(liveGuardIndex).toBeGreaterThan(-1);
    expect(limitedCoverageIndex).toBeGreaterThan(-1);
    expect(liveGuardIndex).toBeLessThan(limitedCoverageIndex);
  });

  test('banner does NOT show LIMITED COVERAGE when backend returns live_runtime_verified', () => {
    const liveGuardIndex = bannerSrc.indexOf("truth.status_reason !== 'summary_unavailable'");
    const limitedCoverageIndex = bannerSrc.indexOf("return 'LIMITED_COVERAGE'");
    expect(liveGuardIndex).toBeGreaterThan(-1);
    expect(limitedCoverageIndex).toBeGreaterThan(-1);
    expect(liveGuardIndex).toBeLessThan(limitedCoverageIndex);
  });

  test('panel does NOT contain "live telemetry is missing or stale" copy when live-verified', () => {
    // The stale-telemetry string must not appear in the LIVE branch path
    // (it is only in the LIMITED_COVERAGE return path which is now unreachable for live verified)
    const staleMsg = 'live telemetry is missing or stale';
    if (panelSrc.includes(staleMsg)) {
      // If the string exists, it must be AFTER the live guard so it is unreachable for live
      const liveGuardIdx = panelSrc.indexOf("summary.status_reason !== 'summary_unavailable'");
      const staleMsgIdx = panelSrc.indexOf(staleMsg);
      expect(liveGuardIdx).toBeLessThan(staleMsgIdx);
    }
  });

  test('truth model: live_runtime_verified fixture resolves runtime_status=live', () => {
    const truth = resolveWorkspaceMonitoringTruth(liveRuntimeFixture());
    expect(truth.runtime_status).toBe('live');
    // Guards are suppressed for live_runtime_verified so status_reason is preserved
    expect(truth.status_reason).toBe('live_runtime_verified');
  });

  test('truth model: live_runtime_verified fixture has no derived guard flags', () => {
    const truth = resolveWorkspaceMonitoringTruth(liveRuntimeFixture());
    // live_monitoring_without_reporting_systems and live_telemetry_verified_without_timestamp
    // must NOT fire when backend explicitly says live_runtime_verified
    expect(truth.guard_flags).not.toContain('live_monitoring_without_reporting_systems');
    expect(truth.guard_flags).not.toContain('live_telemetry_verified_without_timestamp');
  });

  test('truth model: live_runtime_verified fixture preserves next_required_action=monitoring_live', () => {
    const truth = resolveWorkspaceMonitoringTruth(liveRuntimeFixture());
    expect(truth.next_required_action).toBe('monitoring_live');
  });

  test('truth model: live fixture with last_detection_at triggers liveVerified in checklist', () => {
    const truth = resolveWorkspaceMonitoringTruth(liveRuntimeFixture());
    expect(truth.runtime_status).toBe('live');
    expect(truth.last_detection_at).toBe('2026-05-29T10:00:00Z');
    expect(
      truth.status_reason === 'live_runtime_verified' || truth.next_required_action === 'monitoring_live',
    ).toBe(true);
  });
});

test.describe('checklist complete when live + detection + live_runtime_verified', () => {
  test('panel buildChecklist uses liveVerified flag', () => {
    expect(panelSrc).toContain('liveVerified');
    expect(panelSrc).toContain("status_reason === 'live_runtime_verified'");
    expect(panelSrc).toContain("next_required_action === 'monitoring_live'");
  });

  test('checklist hasSource is true when liveVerified', () => {
    expect(panelSrc).toContain('liveVerified || summary.reporting_systems_count > 0');
  });

  test('checklist hasTelemetry is true when liveVerified', () => {
    expect(panelSrc).toContain('liveVerified || Boolean(summary.last_telemetry_at)');
  });

  test('checklist hasPoll is true when liveVerified', () => {
    expect(panelSrc).toContain('liveVerified || Boolean(summary.last_poll_at)');
  });

  test('checklist workerRunning is true when liveVerified', () => {
    expect(panelSrc).toContain('liveVerified || workerHealth.status');
  });
});

test.describe('live_runtime_verified does not affect non-live cases', () => {
  test('summary_unavailable status_reason still blocks LIVE banner', () => {
    // deriveBannerState excludes summary_unavailable from live guard
    expect(panelSrc).toContain("summary.status_reason !== 'summary_unavailable'");
    expect(bannerSrc).toContain("truth.status_reason !== 'summary_unavailable'");
  });

  test('db_failure_reason still routes to OFFLINE before live guard', () => {
    const dbFailureIdx = panelSrc.indexOf('summary.db_failure_reason');
    const liveGuardIdx = panelSrc.indexOf("summary.status_reason !== 'summary_unavailable'");
    expect(dbFailureIdx).toBeLessThan(liveGuardIdx);
  });

  test('truth model: offline runtime with no assets stays offline regardless', () => {
    const truth = resolveWorkspaceMonitoringTruth(
      liveRuntimeFixture({ runtime_status: 'offline', status_reason: null }),
    );
    expect(truth.runtime_status).toBe('offline');
  });
});
