import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function readApp(relative: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', relative), 'utf-8');
}

test('dashboard and threat pages render contradiction-safe fallback copy paths', () => {
  const dashboard = readApp('dashboard-page-content.tsx');
  const threat = readApp('threat-operations-panel.tsx');
  const presentation = readApp('monitoring-status-presentation.ts');
  const truth = readApp('workspace-monitoring-truth.ts');

  expect(dashboard).toContain('const guardedPresentation = (monitoringTruth.guard_flags ?? []).length > 0;');
  expect(dashboard).toContain('const safeMonitoringSummary = telemetryUnavailable');
  expect(threat).toContain('import { hasLiveTelemetry, monitoringHealthyCopyAllowed } from \'./workspace-monitoring-truth\';');
  expect(threat).toContain("showLiveTelemetry ? `Live telemetry ${telemetryLabel}` : 'Current telemetry unavailable'");
  expect(threat).toContain('const dbPersistenceOutageReason = truth.db_failure_reason || null;');
  expect(threat).toContain('Persistence outage active: {dbPersistenceOutageReason}.');
  expect(threat).toContain('&& !dbPersistenceOutageActive');
  expect(presentation).toContain('if (truth.db_failure_reason) {');
  expect(presentation).toContain('Telemetry verification paused while monitoring persistence is unavailable.');
  expect(truth).toContain('&& !truth.db_failure_reason');
  expect(truth).toContain('&& (truth.guard_flags ?? []).length === 0');
  expect(presentation).toContain('Monitoring copy guarded due to contradictory runtime signals.');
});
