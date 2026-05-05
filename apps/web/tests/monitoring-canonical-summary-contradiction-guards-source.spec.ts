import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function appSource(fileName: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', fileName), 'utf-8');
}

test('threat operations page renders from canonical summary fields and preserves contradiction-safe badges', () => {
  const threatPanel = appSource('threat-operations-panel.tsx');

  expect(threatPanel).toContain('const truth = feed.monitoring.truth;');
  expect(threatPanel).toContain('const canonicalPresentation = feed.monitoring.presentation;');
});

test('monitoring overview and system status keep source labeling explicit for simulator/live data', () => {
  const overviewPanel = appSource('monitoring-overview-panel.tsx');
  const systemStatus = appSource('system-status-panel.tsx');

  expect(systemStatus).toContain('presentation.statusLabel');
  expect(systemStatus).toContain('monitoringStatusToBadgeState(presentation.status)');
});

test('dashboard CTA and summary transitions do not allow optimistic healthy states without backend evidence', () => {
  const dashboardPage = appSource('dashboard-page-content.tsx');

  expect(dashboardPage).toContain('const safeMonitoringSummary = telemetryUnavailable');
  expect(dashboardPage).toContain("? 'Telemetry currently unavailable.'");
  expect(dashboardPage).toContain("? monitoringPresentation.summary");
  expect(dashboardPage).toContain(": 'Monitoring state requires attention.'");
  expect(dashboardPage).toContain('monitoringHealthyCopyAllowed(monitoringTruth)');
  expect(dashboardPage).toContain("&& monitoringTruth.monitoring_status === 'live'");
});
