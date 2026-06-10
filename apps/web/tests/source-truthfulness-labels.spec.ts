/**
 * Source truthfulness label tests.
 *
 * Verifies that the UI correctly labels data sources and never presents
 * fallback, simulated, or demo data as "Verified telemetry".
 *
 * Rules enforced:
 * - fallback source => never "Verified telemetry"
 * - simulated source => never "Verified telemetry"
 * - live source => may show "Verified telemetry" when confidence is high
 * - copy sanitizer must not hide source terminology
 */

import { expect, test } from '@playwright/test';

import { normalizeMonitoringPresentation } from '../app/monitoring-status-presentation';
import type { WorkspaceMonitoringTruth } from '../app/workspace-monitoring-truth';

function makeTruth(partial: Partial<WorkspaceMonitoringTruth>): WorkspaceMonitoringTruth {
  return {
    workspace_configured: true,
    monitoring_mode: 'live',
    runtime_status: 'live',
    monitoring_status: 'live',
    configured_systems: 3,
    monitored_systems_count: 3,
    reporting_systems_count: 3,
    protected_assets_count: 3,
    telemetry_freshness: 'fresh',
    confidence: 'high',
    last_poll_at: '2026-06-01T10:00:00Z',
    last_heartbeat_at: '2026-06-01T10:00:00Z',
    last_telemetry_at: '2026-06-01T10:00:00Z',
    last_coverage_telemetry_at: '2026-06-01T10:00:00Z',
    telemetry_kind: 'target_event',
    last_detection_at: '2026-06-01T09:55:00Z',
    evidence_source_summary: 'live',
    status_reason: 'live_runtime_verified',
    db_failure_classification: null,
    db_failure_reason: null,
    contradiction_flags: [],
    continuity_status: 'continuous_live',
    continuity_reason_codes: [],
    guard_flags: [],
    reason_codes: [],
    active_alerts_count: 0,
    active_incidents_count: 0,
    workspace_slug: null,
    workspace_name: null,
    ...partial,
  };
}

test.describe('source-aware telemetry labels', () => {
  test('live source with high confidence renders "verified telemetry"', () => {
    const p = normalizeMonitoringPresentation(makeTruth({
      evidence_source_summary: 'live',
      confidence: 'high',
      telemetry_freshness: 'fresh',
      status_reason: 'live_runtime_verified',
    }));
    expect(p.confidence).toBe('verified telemetry');
    expect(p.status).toBe('live');
  });

  test('simulator source never renders "verified telemetry"', () => {
    const p = normalizeMonitoringPresentation(makeTruth({
      evidence_source_summary: 'simulator',
      confidence: 'high',
      telemetry_freshness: 'fresh',
      monitoring_mode: 'simulator',
    }));
    expect(p.confidence).not.toBe('verified telemetry');
    expect(p.status).not.toBe('live');
  });

  test('replay source never renders "verified telemetry"', () => {
    const p = normalizeMonitoringPresentation(makeTruth({
      evidence_source_summary: 'replay',
      confidence: 'high',
      telemetry_freshness: 'fresh',
    }));
    expect(p.confidence).not.toBe('verified telemetry');
  });

  test('no-source (none) never renders "verified telemetry"', () => {
    const p = normalizeMonitoringPresentation(makeTruth({
      evidence_source_summary: 'none',
      confidence: 'high',
      telemetry_freshness: 'fresh',
      // No live source — this must not produce "verified telemetry"
      reporting_systems_count: 0,
    }));
    expect(p.confidence).not.toBe('verified telemetry');
  });

  test('simulator source renders "limited coverage" status', () => {
    const p = normalizeMonitoringPresentation(makeTruth({
      evidence_source_summary: 'simulator',
      monitoring_mode: 'simulator',
      telemetry_freshness: 'fresh',
    }));
    expect(p.status).toBe('limited coverage');
  });

  test('stale telemetry from live source renders "limited telemetry", not "verified telemetry"', () => {
    const p = normalizeMonitoringPresentation(makeTruth({
      evidence_source_summary: 'live',
      confidence: 'low',
      telemetry_freshness: 'stale',
    }));
    expect(p.confidence).not.toBe('verified telemetry');
    expect(p.status).not.toBe('live');
  });

  test('unavailable confidence never renders "verified telemetry"', () => {
    const p = normalizeMonitoringPresentation(makeTruth({
      evidence_source_summary: 'live',
      confidence: 'unavailable',
      telemetry_freshness: 'unavailable',
      last_telemetry_at: null,
    }));
    expect(p.confidence).toBe('telemetry unavailable');
    expect(p.confidence).not.toBe('verified telemetry');
  });
});
