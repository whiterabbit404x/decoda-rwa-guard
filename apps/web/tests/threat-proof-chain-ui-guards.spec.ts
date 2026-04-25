import fs from 'node:fs';
import path from 'node:path';

import { expect, test } from '@playwright/test';

import { monitoringHealthyCopyAllowed, resolveWorkspaceMonitoringTruth } from '../app/workspace-monitoring-truth';
import type { MonitoringRuntimeStatus } from '../app/monitoring-status-contract';

function appSource(fileName: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', fileName), 'utf-8');
}

test('threat page only enables evidence-linked signals when detection+evidence timeline links are present', () => {
  const threatPage = appSource('threat-operations-panel.tsx');

  expect(threatPage).toContain("const hasDetectionTimelineLink = timelineLinkNames.has('detection');");
  expect(threatPage).toContain("const hasEvidenceTimelineLink = timelineLinkNames.has('telemetry_event') || timelineLinkNames.has('detection_evidence');");
  expect(threatPage).toContain('const showEvidenceLinkedSignals = hasDetectionTimelineLink && hasEvidenceTimelineLink;');
  expect(threatPage).toContain('No evidence-linked threat signals');
});

test('simulator-generated proof chain is visibly labeled simulated in the threat UI', () => {
  const threatPage = appSource('threat-operations-panel.tsx');

  expect(threatPage).toContain('Generate simulator proof chain');
  expect(threatPage).toContain('Simulator proof chain generated and monitoring status refreshed.');
  expect(threatPage).toContain('SIMULATOR MODE');
  expect(threatPage).toContain("mode: 'simulated'");
});

test('contradictions block healthy copy claims', () => {
  const payload: MonitoringRuntimeStatus = {
    workspace_monitoring_summary: {
      workspace_configured: true,
      monitoring_mode: 'live',
      runtime_status: 'healthy',
      monitoring_status: 'live',
      configured_systems: 1,
      reporting_systems: 1,
      protected_assets: 1,
      coverage_state: { configured_systems: 1, reporting_systems: 1, protected_assets: 1 },
      freshness_status: 'fresh',
      confidence_status: 'high',
      last_heartbeat_at: '2026-04-25T10:00:00.000Z',
      last_telemetry_at: '2026-04-25T10:00:00.000Z',
      last_poll_at: '2026-04-25T10:00:00.000Z',
      last_detection_at: '2026-04-25T10:00:00.000Z',
      evidence_source: 'live',
      evidence_source_summary: 'live',
      continuity_status: 'continuous_live',
      contradiction_flags: ['open_alerts_without_detection_evidence'],
    },
  } as MonitoringRuntimeStatus;

  const truth = resolveWorkspaceMonitoringTruth(payload);

  expect(monitoringHealthyCopyAllowed(truth)).toBe(false);
  expect(truth.contradiction_flags).toContain('open_alerts_without_detection_evidence');
});
