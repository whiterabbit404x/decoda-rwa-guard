import { expect, test } from '@playwright/test';

import { normalizeMonitoringPresentation } from '../app/monitoring-status-presentation';
import type { WorkspaceMonitoringTruth } from '../app/workspace-monitoring-truth';

function makeTruth(partial: Partial<WorkspaceMonitoringTruth>): WorkspaceMonitoringTruth {
  return {
    workspace_configured: true,
    monitoring_mode: 'live',
    runtime_status: 'healthy',
    configured_systems: 3,
    monitored_systems_count: 3,
    reporting_systems: 3,
    protected_assets_count: 3,
    freshness_status: 'fresh',
    confidence_status: 'high',
    last_poll_at: '2026-04-13T10:00:00Z',
    last_heartbeat_at: '2026-04-13T10:00:00Z',
    last_telemetry_at: '2026-04-13T10:00:00Z',
    last_coverage_telemetry_at: '2026-04-13T10:00:00Z',
    telemetry_kind: 'target_event',
    last_detection_at: '2026-04-13T10:00:00Z',
    evidence_source: 'live',
    status_reason: null,
    contradiction_flags: [],
    ...partial,
  };
}

test.describe('monitoring status presentation adapter', () => {
  test('normalizes internal runtime states to enterprise-safe statuses', async () => {
    const statuses = [
      normalizeMonitoringPresentation(makeTruth({ runtime_status: 'healthy' })).status,
      normalizeMonitoringPresentation(makeTruth({ runtime_status: 'degraded' })).status,
      normalizeMonitoringPresentation(makeTruth({ runtime_status: 'offline' })).status,
      normalizeMonitoringPresentation(makeTruth({ freshness_status: 'stale' })).status,
      normalizeMonitoringPresentation(makeTruth({ monitoring_mode: 'simulator' })).status,
      normalizeMonitoringPresentation(makeTruth({ evidence_source: 'simulator' })).status,
      normalizeMonitoringPresentation(makeTruth({ evidence_source: 'replay' })).status,
    ];

    const allowed = new Set(['live', 'degraded', 'offline', 'stale', 'limited coverage']);
    statuses.forEach((value) => expect(allowed.has(value)).toBe(true));
    expect(statuses).toContain('limited coverage');
  });

  test('never overstates weak evidence as verified/live telemetry', async () => {
    const weak = normalizeMonitoringPresentation(makeTruth({
      confidence_status: 'unavailable',
      freshness_status: 'unavailable',
      last_telemetry_at: null,
    }));

    expect(weak.status).toBe('degraded');
    expect(weak.evidence).toBe('unavailable');
    expect(weak.confidence).toBe('telemetry unavailable');
    expect(weak.statusLabel).not.toBe('LIVE');
  });

  test('exposes only enterprise-safe evidence and freshness values', async () => {
    const value = normalizeMonitoringPresentation(makeTruth({
      confidence_status: 'medium',
      freshness_status: 'fresh',
    }));
    expect(['verified', 'recent', 'delayed', 'unavailable']).toContain(value.evidence);
    expect(['verified', 'recent', 'delayed', 'unavailable']).toContain(value.freshness);
    expect(['verified telemetry', 'recent telemetry', 'limited telemetry', 'telemetry unavailable']).toContain(value.confidence);
  });

  test('treats idle / limited coverage as non-offline', async () => {
    const idle = normalizeMonitoringPresentation(makeTruth({
      runtime_status: 'idle',
      monitoring_mode: 'live',
      confidence_status: 'unavailable',
    }));
    expect(idle.status).not.toBe('offline');
    expect(idle.statusLabel).not.toBe('OFFLINE');
  });

  test('does not treat polling heartbeats alone as live telemetry', async () => {
    const idleHealthy = normalizeMonitoringPresentation(makeTruth({
      runtime_status: 'idle',
      confidence_status: 'low',
      freshness_status: 'unavailable',
      evidence_source: 'none',
      last_telemetry_at: null,
    }));
    expect(idleHealthy.freshness).toBe('unavailable');
    expect(idleHealthy.confidence).toBe('limited telemetry');
    expect(idleHealthy.status).toBe('limited coverage');
  });

  test('exposes telemetry / heartbeat / poll timestamp labels', async () => {
    const value = normalizeMonitoringPresentation(makeTruth({
      last_telemetry_at: '2026-04-13T10:00:00Z',
      last_heartbeat_at: null,
      last_poll_at: '2026-04-13T09:59:00Z',
    }));
    expect(value.telemetryTimestampLabel).toContain('Telemetry timestamp');
    expect(value.heartbeatTimestampLabel).toContain('unavailable');
    expect(value.pollTimestampLabel).toContain('Poll timestamp');
  });

  test('keeps telemetry freshness wording tied to telemetry timestamp and freshness status', async () => {
    const value = normalizeMonitoringPresentation(makeTruth({
      freshness_status: 'unavailable',
      last_telemetry_at: null,
      last_heartbeat_at: '2026-04-13T10:01:00Z',
      last_poll_at: '2026-04-13T10:02:00Z',
    }));
    expect(value.summary).toContain('Telemetry freshness unavailable.');
  });

  test('treats fresh coverage telemetry as live even without detections', async () => {
    const value = normalizeMonitoringPresentation(makeTruth({
      telemetry_kind: 'coverage',
      last_telemetry_at: null,
      last_coverage_telemetry_at: '2026-04-13T10:00:00Z',
      last_detection_at: null,
      confidence_status: 'medium',
    }));
    expect(value.status).toBe('live');
    expect(value.summary).toContain('Live telemetry verified.');
    expect(value.summary).toContain('No recent detections.');
  });

  test('uses coverage telemetry timestamp label even when target-event telemetry is absent', async () => {
    const value = normalizeMonitoringPresentation(makeTruth({
      runtime_status: 'degraded',
      freshness_status: 'stale',
      telemetry_kind: 'target_event',
      last_telemetry_at: null,
      last_coverage_telemetry_at: '2026-04-13T10:00:00Z',
      last_detection_at: null,
    }));
    expect(value.telemetryTimestampLabel).toContain('Telemetry timestamp:');
    expect(value.telemetryTimestampLabel).not.toContain('unavailable');
  });

  test('keeps telemetry timestamp unavailable when no coverage telemetry exists', async () => {
    const value = normalizeMonitoringPresentation(makeTruth({
      telemetry_kind: 'target_event',
      last_telemetry_at: null,
      last_coverage_telemetry_at: null,
      last_detection_at: null,
    }));
    expect(value.telemetryTimestampLabel).toContain('unavailable');
  });

  test('keeps live status when coverage is fresh and detections are historical only', async () => {
    const value = normalizeMonitoringPresentation(makeTruth({
      telemetry_kind: 'coverage',
      last_coverage_telemetry_at: '2026-04-13T10:00:00Z',
      last_telemetry_at: '2026-04-13T10:00:00Z',
      last_detection_at: '2026-04-13T09:00:00Z',
      confidence_status: 'high',
      freshness_status: 'fresh',
      runtime_status: 'healthy',
    }));
    expect(value.status).toBe('live');
    expect(value.summary).toContain('Historical detections only.');
  });
});
