/**
 * Regression tests: runtime_status=live + status_reason=live_runtime_verified must
 * produce Live / Healthy labels throughout the dashboard — not Degraded or Limited coverage.
 *
 * Root-cause: the workspace_unconfigured_with_coverage guard was firing when the backend
 * flat response omits workspace_configured, overriding status_reason to a guard value and
 * breaking every downstream live-verified check.
 */

import { expect, test } from '@playwright/test';

import type { MonitoringRuntimeStatus } from '../app/monitoring-status-contract';
import { resolveWorkspaceMonitoringTruth, resolveWorkspaceMonitoringTruthFromSummary } from '../app/workspace-monitoring-truth';
import { normalizeMonitoringPresentation } from '../app/monitoring-status-presentation';

/** Minimal live_runtime_verified fixture matching the production backend shape */
function liveVerifiedFixture(overrides: Record<string, unknown> = {}): MonitoringRuntimeStatus {
  return {
    runtime_status: 'live',
    status_reason: 'live_runtime_verified',
    freshness_status: 'fresh',
    confidence_status: 'high',
    evidence_source: 'live',
    contradiction_flags: [],
    provider_health_status: 'healthy',
    target_coverage_status: 'reporting',
    reporting_systems: 4,
    configured_systems: 4,
    protected_assets: 1,
    last_telemetry_at: '2026-05-29T10:00:00Z',
    last_detection_at: '2026-05-29T09:55:00Z',
    ...overrides,
  } as unknown as MonitoringRuntimeStatus;
}

// ── Truth model: guard suppression ────────────────────────────────────────────

test.describe('workspace_unconfigured_with_coverage guard is suppressed for live_runtime_verified', () => {
  test('guard_flags must be empty when backend says live_runtime_verified', () => {
    const truth = resolveWorkspaceMonitoringTruth(liveVerifiedFixture());
    expect(truth.guard_flags).not.toContain('workspace_unconfigured_with_coverage');
    expect(truth.guard_flags).toHaveLength(0);
  });

  test('contradiction_flags must be empty when backend says live_runtime_verified', () => {
    const truth = resolveWorkspaceMonitoringTruth(liveVerifiedFixture());
    expect(truth.contradiction_flags).not.toContain('workspace_unconfigured_with_coverage');
    expect(truth.contradiction_flags).toHaveLength(0);
  });

  test('status_reason is preserved as live_runtime_verified (not overridden by guard)', () => {
    const truth = resolveWorkspaceMonitoringTruth(liveVerifiedFixture());
    expect(truth.status_reason).toBe('live_runtime_verified');
  });

  test('runtime_status is live', () => {
    const truth = resolveWorkspaceMonitoringTruth(liveVerifiedFixture());
    expect(truth.runtime_status).toBe('live');
  });

  test('monitoring_status is live (not limited) when runtime is live_runtime_verified', () => {
    const truth = resolveWorkspaceMonitoringTruth(liveVerifiedFixture());
    expect(truth.monitoring_status).toBe('live');
  });
});

// ── Presentation: status must be 'live', not 'degraded' or 'limited coverage' ──

test.describe('normalizeMonitoringPresentation returns live status for live_runtime_verified', () => {
  test('presentation.status is live', () => {
    const truth = resolveWorkspaceMonitoringTruth(liveVerifiedFixture());
    const presentation = normalizeMonitoringPresentation(truth);
    expect(presentation.status).toBe('live');
  });

  test('presentation.statusLabel is LIVE (not DEGRADED or LIMITED COVERAGE)', () => {
    const truth = resolveWorkspaceMonitoringTruth(liveVerifiedFixture());
    const presentation = normalizeMonitoringPresentation(truth);
    expect(presentation.statusLabel).toBe('LIVE');
    expect(presentation.statusLabel).not.toBe('DEGRADED');
    expect(presentation.statusLabel).not.toContain('LIMITED');
  });

  test('presentation does not contain Degraded anywhere in statusLabel', () => {
    const truth = resolveWorkspaceMonitoringTruth(liveVerifiedFixture());
    const presentation = normalizeMonitoringPresentation(truth);
    expect(presentation.statusLabel.toLowerCase()).not.toContain('degraded');
  });

  test('presentation does not show Limited coverage', () => {
    const truth = resolveWorkspaceMonitoringTruth(liveVerifiedFixture());
    const presentation = normalizeMonitoringPresentation(truth);
    expect(presentation.status).not.toBe('limited coverage');
    expect(presentation.statusLabel).not.toContain('LIMITED COVERAGE');
  });

  test('presentation.freshness is verified when confidence is high', () => {
    const truth = resolveWorkspaceMonitoringTruth(liveVerifiedFixture());
    const presentation = normalizeMonitoringPresentation(truth);
    expect(presentation.freshness).toBe('verified');
  });
});

// ── Counts are preserved from the backend fixture ─────────────────────────────

test.describe('live_runtime_verified fixture preserves backend counts', () => {
  test('reporting_systems_count is 4 (not 0)', () => {
    const truth = resolveWorkspaceMonitoringTruth(liveVerifiedFixture());
    expect(truth.reporting_systems_count).toBe(4);
  });

  test('monitored_systems_count is 4 (not 0)', () => {
    const truth = resolveWorkspaceMonitoringTruth(liveVerifiedFixture());
    expect(truth.monitored_systems_count).toBe(4);
  });

  test('protected_assets_count is 1 (not 0)', () => {
    const truth = resolveWorkspaceMonitoringTruth(liveVerifiedFixture());
    expect(truth.protected_assets_count).toBe(1);
  });

  test('last_telemetry_at is present', () => {
    const truth = resolveWorkspaceMonitoringTruth(liveVerifiedFixture());
    expect(truth.last_telemetry_at).toBeTruthy();
  });

  test('last_detection_at is present', () => {
    const truth = resolveWorkspaceMonitoringTruth(liveVerifiedFixture());
    expect(truth.last_detection_at).toBeTruthy();
  });
});

// ── Flat summary shape (used by resolveWorkspaceMonitoringTruthFromSummary) ───

test.describe('resolveWorkspaceMonitoringTruthFromSummary with live_runtime_verified summary', () => {
  function liveSummary(overrides: Record<string, unknown> = {}) {
    return {
      runtime_status: 'live' as const,
      status_reason: 'live_runtime_verified',
      freshness_status: 'fresh',
      confidence_status: 'high',
      evidence_source: 'live',
      evidence_source_summary: 'live' as const,
      contradiction_flags: [],
      guard_flags: [],
      reporting_systems: 4,
      configured_systems: 4,
      protected_assets: 1,
      last_telemetry_at: '2026-05-29T10:00:00Z',
      last_detection_at: '2026-05-29T09:55:00Z',
      workspace_configured: false,
      ...overrides,
    };
  }

  test('guard_flags empty even with workspace_configured=false when live_runtime_verified', () => {
    const truth = resolveWorkspaceMonitoringTruthFromSummary(liveSummary() as any);
    expect(truth.guard_flags).not.toContain('workspace_unconfigured_with_coverage');
  });

  test('status_reason preserved as live_runtime_verified', () => {
    const truth = resolveWorkspaceMonitoringTruthFromSummary(liveSummary() as any);
    expect(truth.status_reason).toBe('live_runtime_verified');
  });

  test('monitoring_status is live', () => {
    const truth = resolveWorkspaceMonitoringTruthFromSummary(liveSummary() as any);
    expect(truth.monitoring_status).toBe('live');
  });

  test('presentation is live for flat summary shape', () => {
    const truth = resolveWorkspaceMonitoringTruthFromSummary(liveSummary() as any);
    const presentation = normalizeMonitoringPresentation(truth);
    expect(presentation.status).toBe('live');
    expect(presentation.statusLabel).toBe('LIVE');
  });
});

// ── Non-live cases are unaffected ─────────────────────────────────────────────

test.describe('non-live cases are not changed by the live_runtime_verified guard suppression', () => {
  test('degraded runtime with contradiction_flags stays degraded', () => {
    const truth = resolveWorkspaceMonitoringTruth(
      liveVerifiedFixture({
        runtime_status: 'degraded',
        status_reason: null,
        contradiction_flags: ['alert_without_detection'],
      }),
    );
    const presentation = normalizeMonitoringPresentation(truth);
    expect(presentation.status).not.toBe('live');
    expect(['degraded', 'limited coverage', 'offline', 'stale']).toContain(presentation.status);
  });

  test('offline runtime stays offline regardless of status_reason', () => {
    const truth = resolveWorkspaceMonitoringTruth(
      liveVerifiedFixture({ runtime_status: 'offline', status_reason: null }),
    );
    expect(truth.runtime_status).toBe('offline');
    const presentation = normalizeMonitoringPresentation(truth);
    expect(presentation.status).toBe('offline');
  });

  test('live runtime with critical backend contradiction_flags is still degraded', () => {
    const truth = resolveWorkspaceMonitoringTruth(
      liveVerifiedFixture({
        // backend says live but contradiction_flags contains a critical flag
        contradiction_flags: ['live_monitoring_without_reporting_systems'],
        status_reason: 'live_runtime_verified',
      }),
    );
    const presentation = normalizeMonitoringPresentation(truth);
    // hasCriticalContradiction fires → backend-authoritative path must NOT return live
    expect(presentation.status).not.toBe('live');
  });
});
