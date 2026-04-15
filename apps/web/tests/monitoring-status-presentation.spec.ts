import { expect, test } from '@playwright/test';

import { normalizeMonitoringPresentation } from '../app/monitoring-status-presentation';
import type { MonitoringRuntimeStatus } from '../app/monitoring-status-contract';

test.describe('monitoring status presentation adapter', () => {
  test('normalizes internal runtime states to enterprise-safe statuses', async () => {
    const statuses = [
      normalizeMonitoringPresentation({ mode: 'LIVE', recent_evidence_state: 'real', recent_real_event_count: 2, checkpoint_age_seconds: 30 } as MonitoringRuntimeStatus).status,
      normalizeMonitoringPresentation({ mode: 'DEGRADED', recent_evidence_state: 'degraded', checkpoint_age_seconds: 900 } as MonitoringRuntimeStatus).status,
      normalizeMonitoringPresentation({ mode: 'OFFLINE', recent_evidence_state: 'missing' } as MonitoringRuntimeStatus).status,
      normalizeMonitoringPresentation({ mode: 'STALE', recent_evidence_state: 'real', checkpoint_age_seconds: 2000 } as MonitoringRuntimeStatus).status,
      normalizeMonitoringPresentation({ mode: 'LIMITED_COVERAGE', recent_evidence_state: 'real', synthetic_leak_detected: true } as MonitoringRuntimeStatus).status,
      normalizeMonitoringPresentation({ mode: 'LIVE', recent_evidence_state: 'demo' } as MonitoringRuntimeStatus).status,
      normalizeMonitoringPresentation({ mode: 'LIVE', detection_outcome: 'DEMO_ONLY' } as MonitoringRuntimeStatus).status,
    ];

    const allowed = new Set(['live', 'degraded', 'offline', 'stale', 'limited coverage']);
    statuses.forEach((value) => expect(allowed.has(value)).toBe(true));
    expect(statuses).toContain('limited coverage');
  });

  test('never overstates weak evidence as verified/live telemetry', async () => {
    const weak = normalizeMonitoringPresentation({
      mode: 'LIVE',
      recent_evidence_state: 'missing',
      recent_real_event_count: 0,
      recent_confidence_basis: 'demo_scenario',
    } as MonitoringRuntimeStatus);

    expect(weak.status).toBe('degraded');
    expect(weak.evidence).toBe('unavailable');
    expect(weak.confidence).toBe('telemetry unavailable');
    expect(weak.statusLabel).not.toBe('LIVE');
  });

  test('exposes only enterprise-safe evidence and freshness values', async () => {
    const value = normalizeMonitoringPresentation({ mode: 'LIVE', recent_evidence_state: 'real', recent_real_event_count: 1, checkpoint_age_seconds: 120 } as MonitoringRuntimeStatus);
    expect(['verified', 'recent', 'delayed', 'unavailable']).toContain(value.evidence);
    expect(['verified', 'recent', 'delayed', 'unavailable']).toContain(value.freshness);
    expect(['verified telemetry', 'recent telemetry', 'limited telemetry', 'telemetry unavailable']).toContain(value.confidence);
  });

  test('treats idle / limited coverage as non-offline', async () => {
    const idle = normalizeMonitoringPresentation({
      mode: 'LIMITED_COVERAGE',
      monitoring_status: 'idle',
      recent_evidence_state: 'missing',
      recent_real_event_count: 0,
    } as MonitoringRuntimeStatus);
    expect(idle.status).not.toBe('offline');
    expect(idle.statusLabel).not.toBe('OFFLINE');
  });

  test('does not treat polling heartbeats alone as live telemetry', async () => {
    const idleHealthy = normalizeMonitoringPresentation({
      mode: 'LIMITED_COVERAGE',
      monitoring_status: 'idle',
      monitored_systems: 3,
      systems_with_recent_heartbeat: 3,
      successful_detection_evaluation_recent: true,
      recent_evidence_state: 'missing',
      recent_real_event_count: 0,
      recent_confidence_basis: 'provider_evidence',
      last_heartbeat: new Date().toISOString(),
      workspace_monitoring_summary: {
        workspace_configured: true,
        monitoring_mode: 'live',
        runtime_status: 'idle',
        coverage_state: { configured_systems: 3, reporting_systems: 0, protected_assets: 3 },
        freshness_status: 'unavailable',
        confidence_status: 'low',
        last_heartbeat_at: new Date().toISOString(),
        last_telemetry_at: null,
        last_poll_at: new Date().toISOString(),
        last_detection_at: null,
        evidence_source: 'none',
        status_reason: 'no_reporting_systems',
        contradiction_flags: [],
      },
    } as MonitoringRuntimeStatus);
    expect(idleHealthy.freshness).toBe('unavailable');
    expect(idleHealthy.confidence).toBe('limited telemetry');
    expect(idleHealthy.status).toBe('limited coverage');
  });

  test('prefers successful monitoring cycle timestamp as last checkpoint label', async () => {
    const value = normalizeMonitoringPresentation({
      mode: 'LIVE',
      recent_evidence_state: 'real',
      recent_real_event_count: 0,
      last_confirmed_checkpoint: '2026-04-13T10:00:00Z',
    } as MonitoringRuntimeStatus);
    expect(value.lastCheckpointLabel).not.toContain('unavailable');
  });
});
