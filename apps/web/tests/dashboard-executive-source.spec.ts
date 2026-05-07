import { expect, test } from '@playwright/test';
import * as fs from 'node:fs';
import * as path from 'node:path';

const EXEC_SUMMARY_PATH = path.join(
  __dirname,
  '../app/dashboard-executive-summary.tsx',
);
const HYDRATOR_PATH = path.join(
  __dirname,
  '../app/dashboard-live-hydrator.tsx',
);
const STYLES_PATH = path.join(__dirname, '../app/styles.css');

function readSource(filePath: string): string {
  return fs.readFileSync(filePath, 'utf8');
}

test.describe('Dashboard Executive Summary – source-level contracts', () => {
  test('dashboard route renders DashboardExecutiveSummary via hydrator', () => {
    const hydrator = readSource(HYDRATOR_PATH);
    expect(hydrator).toContain("import DashboardExecutiveSummary from './dashboard-executive-summary'");
    expect(hydrator).toContain('<DashboardExecutiveSummary');
    expect(hydrator).not.toContain('DashboardPageContent');
  });

  test('top metric cards exist exactly: Protected Assets, Monitored Systems, Active Alerts, Open Incidents, System Health', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain('"Protected Assets"');
    expect(source).toContain('"Monitored Systems"');
    expect(source).toContain('"Active Alerts"');
    expect(source).toContain('"Open Incidents"');
    expect(source).toContain('"System Health"');
  });

  test('Risk Overview section exists', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain('Risk Overview');
    expect(source).toContain('aria-label="Risk Overview"');
  });

  test('Recent Alerts section exists', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain('Recent Alerts');
    expect(source).toContain('aria-label="Recent Alerts"');
  });

  test('Recent Incidents section exists', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain('Recent Incidents');
    expect(source).toContain('aria-label="Recent Incidents"');
  });

  test('System Health compact card exists', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain('aria-label="System Health"');
    expect(source).toContain('SystemHealthCompactCard');
  });

  test('Next Required Action exists', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain('Next Required Action');
    expect(source).toContain('data-next-required-action');
    expect(source).toContain('NextRequiredActionCard');
  });

  test('dashboard does not show "Healthy" when monitoring_status is degraded/offline', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain('healthProvable');
    expect(source).toContain("monitoringHealthyCopyAllowed");
    const healthyAssignment = source.indexOf("'Healthy'");
    const healthProvableCheck = source.indexOf('healthProvable');
    expect(healthyAssignment).toBeGreaterThan(-1);
    expect(healthProvableCheck).toBeGreaterThan(-1);
    expect(healthProvableCheck).toBeLessThan(healthyAssignment);
  });

  test('dashboard does not label simulator evidence as live', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain('isSimulator');
    expect(source).toContain("'Live provider'");
    const simulatorCheck = source.indexOf('isSimulator');
    const liveProviderLabel = source.indexOf("'Live provider'");
    expect(simulatorCheck).toBeLessThan(liveProviderLabel);
    expect(source).toContain("'Simulator'");
  });

  test('page title is Dashboard', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain('>Dashboard<');
  });

  test('page subtitle mentions assets, monitoring, alerts, incidents, and system health', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain('protected assets');
    expect(source).toContain('monitoring coverage');
    expect(source).toContain('alerts');
    expect(source).toContain('incidents');
    expect(source).toContain('system health');
  });

  test('uses canonical useRuntimeSummary hook for metric data', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain("from './runtime-summary-context'");
    expect(source).toContain('useRuntimeSummary()');
  });

  test('uses monitoringHealthyCopyAllowed before displaying healthy state', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain("from './workspace-monitoring-truth'");
    expect(source).toContain('monitoringHealthyCopyAllowed');
  });

  test('CSS defines exec metric row and exec section cards', () => {
    const css = readSource(STYLES_PATH);
    expect(css).toContain('.execMetricRow');
    expect(css).toContain('.execMetricCard');
    expect(css).toContain('.execSectionCard');
    expect(css).toContain('.execMainGrid');
    expect(css).toContain('.execBottomGrid');
    expect(css).toContain('.execNextActionBanner');
  });

  test('alert empty state explains exact blocker reason based on telemetry/detection state', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain('no telemetry has been received');
    expect(source).toContain('no detection has been generated');
  });

  test('incident empty state explains exact blocker reason', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain('No incidents yet because no detection has been generated');
  });

  test('simulator alerts are labeled Simulator not live', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain("alert.source === 'fallback'");
    expect(source).toContain('label="Simulator"');
  });

  test('simulator incidents are labeled Simulator not live', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain("incident.source === 'fallback'");
  });

  test('reason codes handle object values safely', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain('function humanizeReason');
    expect(source).toContain('objectValue.code ?? objectValue.reason ?? objectValue.message');
    expect(source).toContain('JSON.stringify(objectValue)');
    expect(source).not.toContain('status_reason.replaceAll');
  });

  test('runtime null or undefined fields are guarded before map/slice and numeric rendering', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain('function safeArray');
    expect(source).toContain('function safeNumber');
    expect(source).toContain('safeArray<ThreatDetection>(data?.threatDashboard?.active_alerts).slice(0, 5)');
    expect(source).toContain('safeArray<ResilienceIncident>(data?.resilienceDashboard?.latest_incidents).slice(0, 5)');
    expect(source).toContain('safeNumber(summary.protected_assets_count)');
  });
});
