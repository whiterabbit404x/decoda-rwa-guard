import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const LEGACY_PRESENTATION_SIGNALS = [
  'recent_evidence_state',
  'systems_with_recent_heartbeat',
  'successful_detection_evaluation_recent',
  'recent_confidence_basis',
  'status.mode',
  'degraded_reason',
  'liveFeed.degraded',
  'liveFeed.offline',
  'liveFeed.stale',
  'checkpoint_age_seconds',
  'last_confirmed_checkpoint',
  'last_detection_evaluation_at',
  'synthetic_leak_detected',
  'invalid_enabled_targets',
  'detection_outcome',
] as const;
const LEGACY_RUNTIME_FIELDS = [
  'feed.lastTelemetryAt',
  'feed.lastPollAt',
  'feed.lastHeartbeatAt',
  'runtimeStatus.last_telemetry_at',
  'runtimeStatus.last_poll_at',
  'runtimeStatus.last_heartbeat_at',
] as const;

function appSource(fileName: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', fileName), 'utf-8');
}

test('threat panel renders telemetry copy from truth model timestamps only', () => {
  const threat = appSource('threat-operations-panel.tsx');
  expect(threat).toContain('resolveWorkspaceMonitoringTruth');
  expect(threat).toContain('lastTelemetryAt: truth.last_telemetry_at');
  expect(threat).toContain('lastPollAt: truth.last_poll_at');
  expect(threat).toContain('const showLiveTelemetry = monitoringPresentation.hasLiveTelemetry;');
  expect(threat).toContain("{showLiveTelemetry ? `Live telemetry ${telemetryLabel}` : 'Current telemetry unavailable'}");
  expect(threat).toContain('Guarded fallback copy active');
  expect(threat).not.toContain('truth.last_telemetry_at ?? feed.lastTelemetryAt');
  expect(threat).not.toContain('truth.last_poll_at ?? feed.lastPollAt');
  expect(threat).not.toContain('feed.offline');
  expect(threat).not.toContain('feed.stale');
  expect(threat).not.toContain('feed.degraded');
  expect(threat).not.toContain('invalid_enabled_targets');
  expect(threat).not.toContain('systems_with_recent_heartbeat');
  LEGACY_RUNTIME_FIELDS.forEach((field) => {
    expect(threat).not.toContain(field);
  });
});

test('monitoring source files remain guarded from legacy presentation signals', () => {
  const guardedSources = [
    appSource('threat-operations-panel.tsx'),
    appSource('monitoring-status-presentation.ts'),
    appSource('workspace-monitoring-mode-banner.tsx'),
    appSource('monitoring-overview-panel.tsx'),
  ];

  guardedSources.forEach((fileSource) => {
    LEGACY_PRESENTATION_SIGNALS.forEach((legacySignal) => {
      expect(fileSource).not.toContain(legacySignal);
    });
  });
});

test('threat panel keeps telemetry and healthy monitoring copy guarded by truth constraints', () => {
  const threat = appSource('threat-operations-panel.tsx');

  expect(threat).toContain("showLiveTelemetry ? `Live telemetry ${telemetryLabel}` : 'Current telemetry unavailable'");
  expect(threat).toContain("Last telemetry: {showLiveTelemetry ? telemetryLabel : 'Not available'}");
  expect(threat).toContain('runtimeStatus === \'offline\'');
  expect(threat).toContain("return 'offline_no_telemetry';");
  expect(threat).toContain("monitoringHealthyCopyAllowed(truth) ? 'Monitoring healthy: telemetry and polling are current.' : 'Monitoring configured: waiting for reporting telemetry.'");
  expect(threat).toContain("reportingSystems > 0 ? 'No active detections, monitoring healthy' : 'No active detections, waiting for live telemetry'");
});
