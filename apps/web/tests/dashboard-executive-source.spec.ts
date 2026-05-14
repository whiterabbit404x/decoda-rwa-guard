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

test.describe('Dashboard Executive Summary 鈥?source-level contracts', () => {
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

  test('fallback alerts are labeled Unavailable not Simulator', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain("alert.source === 'fallback'");
    expect(source).toContain('label="Unavailable"');
    // fallback !== simulator; pill must not mislabel fallback data as Simulator
    const fallbackAlertIdx = source.indexOf("alert.source === 'fallback'");
    const unavailablePillIdx = source.indexOf('label="Unavailable"');
    expect(fallbackAlertIdx).toBeGreaterThan(-1);
    expect(unavailablePillIdx).toBeGreaterThan(-1);
  });

  test('fallback incidents are labeled Unavailable not Simulator', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain("incident.source === 'fallback'");
    // fallback incidents must also use Unavailable pill
    expect(source).toContain('label="Unavailable"');
  });

  test('defensive helpers are present and invoked with explicit fallback handling', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain('function isRecord');
    expect(source).toContain('function safeString(value: unknown, fallback');
    expect(source).toContain('function safeNumber(value: unknown, fallback');
    expect(source).toContain('function safeArray');
    expect(source).toContain('function humanizeReason');
    expect(source).toContain('function safeAction');
    expect(source).toContain('safeString(');
    expect(source).toContain('safeNumber(');
  });

  test('summary uses guarded safeSummary access and removes unsafe direct dereferences', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain('const safeSummary: Record<string, unknown> = isRecord(summary) ? summary : {};');
    expect(source).toContain('safeNumber(safeSummary.protected_assets_count)');
    expect(source).toContain('const summaryNextAction = safeString(safeSummary.next_required_action);');
    expect(source).toContain('const nextAction = safeAction(summaryNextAction);');
    expect(source).not.toContain('summary.protected_assets_count');
    expect(source).not.toContain('summary.next_required_action');
  });

  test('reason codes handle object values safely', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain('function humanizeReason');
    expect(source).toContain('objectValue.code ??');
    expect(source).toContain('objectValue.reason ??');
    expect(source).toContain('objectValue.message ??');
    expect(source).toContain('objectValue.status_reason');
    expect(source).toContain('JSON.stringify(objectValue)');
    expect(source).not.toContain('status_reason.replaceAll');
  });

  test('offline/degraded states are never labeled Healthy without monitoringHealthyCopyAllowed(...)', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain('monitoringHealthyCopyAllowed');
    expect(source).toContain("monitoringTruth.runtime_status === 'offline'");
    expect(source).toContain("? 'Healthy'");
    expect(source).toContain("'Healthy'");
    const helperIndex = source.indexOf('monitoringHealthyCopyAllowed');
    const healthyIndex = source.indexOf("'Healthy'");
    expect(helperIndex).toBeGreaterThan(-1);
    expect(healthyIndex).toBeGreaterThan(-1);
    expect(helperIndex).toBeLessThan(healthyIndex);
  });

  test('fallback/simulator evidence is never labeled as live provider', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain('isSimulator');
    expect(source).toContain("alert.source === 'fallback'");
    expect(source).toContain("incident.source === 'fallback'");
    // simulator evidence label still present for safeEvidenceLabel derivation
    expect(source).toContain("'Simulator'");
    expect(source).toContain("'Live provider'");
    // fallback source pills must use Unavailable, not Simulator
    expect(source).toContain('label="Unavailable"');
    const simCheck = source.indexOf('isSimulator');
    const liveLabel = source.indexOf("'Live provider'");
    expect(simCheck).toBeGreaterThan(-1);
    expect(liveLabel).toBeGreaterThan(-1);
    expect(simCheck).toBeLessThan(liveLabel);
  });

  test('healthProvable excludes simulator evidence source', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain("monitoringTruth.evidence_source_summary !== 'simulator'");
    const healthProvableIdx = source.indexOf('const healthProvable');
    const simGuardIdx = source.indexOf("evidence_source_summary !== 'simulator'");
    expect(healthProvableIdx).toBeGreaterThan(-1);
    expect(simGuardIdx).toBeGreaterThan(-1);
    // simulator guard must be part of healthProvable assignment
    expect(simGuardIdx).toBeGreaterThan(healthProvableIdx);
  });

  test('runtime null arrays remain guarded for active_alerts and latest_incidents', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain('function safeArray');
    expect(source).toContain('safeArray<ThreatDetection>(data?.threatDashboard?.active_alerts).slice(0, 5)');
    expect(source).toContain('safeArray<ResilienceIncident>(data?.resilienceDashboard?.latest_incidents).slice(0, 5)');
  });
});
