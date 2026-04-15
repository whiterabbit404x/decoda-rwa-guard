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

function appSource(fileName: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', fileName), 'utf-8');
}

test('threat panel renders telemetry copy from truth model timestamps only', () => {
  const threat = appSource('threat-operations-panel.tsx');
  expect(threat).toContain('resolveWorkspaceMonitoringTruth');
  expect(threat).toContain('const lastTelemetryAt = truth.last_telemetry_at ?? feed.lastTelemetryAt;');
  expect(threat).toContain('const showLiveTelemetry = hasLiveTelemetry(truth);');
  expect(threat).toContain("{showLiveTelemetry ? `Live telemetry ${telemetryLabel}` : 'Current telemetry unavailable'}");
  expect(threat).toContain('Guarded fallback copy active');
});

test('monitoring source files remain guarded from legacy presentation signals', () => {
  const guardedSources = [
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
