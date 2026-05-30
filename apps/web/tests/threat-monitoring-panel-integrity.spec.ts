/**
 * Integrity tests for the refactored ThreatMonitoringPanel.
 * Source-level: reads .tsx files and asserts structural correctness.
 * No browser required.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function appSource(fileName: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', fileName), 'utf-8');
}

// ─── 1. /threat does not fetch nonexistent endpoints ──────────────────────────

test('threat-monitoring-panel does not fetch /telemetry endpoint', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  // The panel must not call the workspace-level /telemetry endpoint which does not exist.
  expect(panel).not.toContain('`${apiUrl}/telemetry`');
  expect(panel).not.toContain("apiUrl}/telemetry'");
  expect(panel).not.toContain('/telemetry`, {');
});

test('threat-monitoring-panel does not fetch /anomalies endpoint', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  // The panel must not call /anomalies which has no backend route.
  expect(panel).not.toContain('`${apiUrl}/anomalies`');
  expect(panel).not.toContain("apiUrl}/anomalies'");
  expect(panel).not.toContain('/anomalies`, {');
});

test('threat-monitoring-panel only fetches known backend endpoints', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  // The only permitted direct fetch is /detections which the backend provides.
  expect(panel).toContain('`${apiUrl}/detections`');
  // Ensure no other ad-hoc endpoint strings beside detections are fetched
  const fetchMatches = [...panel.matchAll(/fetch\(\s*`\$\{apiUrl\}\/([^`]+)`/g)].map((m) => m[1]);
  for (const endpoint of fetchMatches) {
    expect(['detections', 'detections?limit=50'].some((allowed) => endpoint.startsWith(allowed))).toBe(true);
  }
});

// ─── 2. No corrupted text appears ─────────────────────────────────────────────

test('no mojibake character 閻 appears in threat-monitoring-panel', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).not.toContain('閻');
  expect(panel).not.toContain('閻');
});

test('telemetry summary line uses · separator, not corrupted characters', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain('· Last:');
  expect(panel).toContain('· None received');
});

// ─── 3. Simulator evidence is never labeled live_provider ─────────────────────

test('evidencePill guards simulator source before allowing live_provider label', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  const fnStart = panel.indexOf('function evidencePill');
  const fnEnd = panel.indexOf('function nodeStatusVariant');
  const fnText = panel.slice(fnStart, fnEnd);

  const simulatorGuardPos = fnText.indexOf("workspaceSource === 'simulator'");
  const liveProviderPos = fnText.indexOf("label: 'live_provider'");

  // Simulator guard must appear before the live_provider label assignment
  expect(simulatorGuardPos).toBeGreaterThan(-1);
  expect(liveProviderPos).toBeGreaterThan(-1);
  expect(simulatorGuardPos).toBeLessThan(liveProviderPos);
});

test('evidencePill returns simulator label when evidence source is simulator', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain("raw === 'simulator'");
  expect(panel).toContain("label: 'simulator', variant: 'info'");
});

test('simulator workspace evidence_source forces simulator label over live_provider', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  // workspaceSource check must exist and guard the simulator path
  expect(panel).toContain("workspaceSource === 'simulator'");
  // After the simulator guard, live_provider must not be reachable for simulator sources
  const guardIdx = panel.indexOf("workspaceSource === 'simulator'");
  const liveIdx = panel.indexOf("label: 'live_provider'");
  expect(guardIdx).toBeLessThan(liveIdx);
});

// ─── 4. Live status only shown when telemetry/detection evidence exists ────────

test('freshnessLabel returns "No telemetry" when last_telemetry_at is null', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  // Cannot claim fresh/live when there is no telemetry timestamp.
  expect(panel).toContain("return 'No telemetry'");
});

test('metric tile for Latest Telemetry uses freshnessLabel which guards null telemetry', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain('Latest Telemetry');
  expect(panel).toContain('freshnessLabel()');
});

test('ThreatOverviewCard is driven by buildSecurityWorkspaceStatus not inline live claims', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  // SecurityWorkspaceStatus is built via the canonical builder, not ad-hoc
  expect(panel).toContain('buildSecurityWorkspaceStatus(');
  expect(panel).toContain('<ThreatOverviewCard');
  // No direct "live monitoring is healthy" copy invented in the panel
  expect(panel.toLowerCase()).not.toContain('live monitoring is healthy');
});

// ─── 5. Empty state CTAs link to real next actions, not back to /threat ────────

test('Case E simulator empty state does not link back to /threat', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  const caseEBlock = panel.slice(panel.indexOf('// Case E'), panel.indexOf('// Case F'));
  expect(caseEBlock).not.toContain("'/threat'");
  // Must reference real pages for both simulator and non-simulator paths
  expect(caseEBlock).toContain('/monitored-systems');
  expect(caseEBlock).toContain('/system-health');
});

test('Case F empty state does not link back to /threat', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  const caseFBlock = panel.slice(panel.indexOf('// Case F'), panel.indexOf('// Case G'));
  expect(caseFBlock).not.toContain("ctaHref: '/threat'");
  expect(caseFBlock).toContain("ctaHref: '/monitoring-sources'");
});

test('telemetry tab empty state simulator CTA does not link back to /threat', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  // In the telemetry tab empty state, the simulator CTA must not link to /threat
  expect(panel).not.toContain("ctaHref={isSimulatorMode ? '/threat'");
});

// ─── 6. Newer components are integrated into ThreatMonitoringPanel ────────────

test('ThreatMonitoringPanel imports ThreatPageHeader', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain("import ThreatPageHeader from './threat/threat-page-header'");
});

test('ThreatMonitoringPanel imports ThreatOverviewCard', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain("import ThreatOverviewCard from './threat/threat-overview-card'");
});

test('ThreatMonitoringPanel imports MonitoringHealthCard', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain("import MonitoringHealthCard from './threat/monitoring-health-card'");
});

test('ThreatMonitoringPanel imports DetectionFeed', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain("import DetectionFeed from './threat/detection-feed'");
});

test('ThreatMonitoringPanel imports AlertIncidentChain', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain("import AlertIncidentChain from './threat/alert-incident-chain'");
});

test('ThreatMonitoringPanel imports ResponseActionPanel', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain("import ResponseActionPanel from './threat/response-action-panel'");
});

test('ThreatMonitoringPanel imports TechnicalRuntimeDetails', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain("import TechnicalRuntimeDetails from './threat/technical-runtime-details'");
});

test('TechnicalRuntimeDetails appears after ResponseActionPanel in ThreatMonitoringPanel', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  const responseActionIdx = panel.indexOf('<ResponseActionPanel');
  const technicalDetailsIdx = panel.indexOf('<TechnicalRuntimeDetails');
  expect(responseActionIdx).toBeGreaterThan(-1);
  expect(technicalDetailsIdx).toBeGreaterThan(-1);
  expect(responseActionIdx).toBeLessThan(technicalDetailsIdx);
});

test('ThreatOverviewCard appears before AlertIncidentChain in ThreatMonitoringPanel', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  const overviewIdx = panel.indexOf('<ThreatOverviewCard');
  const chainIdx = panel.indexOf('<AlertIncidentChain');
  expect(overviewIdx).toBeGreaterThan(-1);
  expect(chainIdx).toBeGreaterThan(-1);
  expect(overviewIdx).toBeLessThan(chainIdx);
});

// ─── 7. Technical details remain collapsible, not primary UI ──────────────────

test('TechnicalRuntimeDetails component uses <details> for collapsible rendering', () => {
  const tech = appSource('threat/technical-runtime-details.tsx');
  expect(tech).toContain('<details className="tableMeta">');
  expect(tech).not.toContain('<details open');
});
