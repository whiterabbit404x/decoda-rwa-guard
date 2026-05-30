/**
 * Production-readiness tests for the /threat page.
 * Source-level: reads .tsx / .ts files and asserts structural correctness.
 * No browser required.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function appSource(fileName: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', fileName), 'utf-8');
}

// ─── 1. Page renders "Threat Monitoring" heading ──────────────────────────────

test('/threat page has h1 Threat Monitoring for accessibility and SEO', () => {
  const page = appSource('(product)/threat/page.tsx');
  expect(page).toContain('<h1>Threat Monitoring</h1>');
});

test('ThreatPageHeader renders h2 Threat Monitoring as the visible panel heading', () => {
  const header = appSource('threat/threat-page-header.tsx');
  expect(header).toContain('Threat Monitoring');
  expect(header).toContain('<h2');
});

// ─── 2. KPI cards are present ─────────────────────────────────────────────────

test('/threat page renders seven KPI tiles', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain('Protected Assets');
  expect(panel).toContain('Monitoring Systems');
  expect(panel).toContain('Latest Telemetry');
  expect(panel).toContain('Active Detections');
  expect(panel).toContain('Open Alerts');
  expect(panel).toContain('Active Incidents');
  expect(panel).toContain('Evidence Freshness');
});

test('Evidence Freshness KPI uses evidenceFreshnessLabel helper', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain('evidenceFreshnessLabel()');
  expect(panel).toContain("return 'Fresh'");
  expect(panel).toContain("return 'Stale'");
  expect(panel).toContain("return 'Missing'");
  expect(panel).toContain("return 'Unknown'");
});

// ─── 3. Workflow renders Evidence and Response nodes ──────────────────────────

test('pipeline workflow includes all 11 nodes including Evidence and Response', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  const nodesMatch = panel.match(/const PIPELINE_NODES = \[([\s\S]*?)\] as const/);
  expect(nodesMatch).not.toBeNull();
  const nodesText = nodesMatch![1];
  expect(nodesText).toContain("'Asset'");
  expect(nodesText).toContain("'Target'");
  expect(nodesText).toContain("'System'");
  expect(nodesText).toContain("'Heartbeat'");
  expect(nodesText).toContain("'Poll'");
  expect(nodesText).toContain("'Telemetry'");
  expect(nodesText).toContain("'Detection'");
  expect(nodesText).toContain("'Alert'");
  expect(nodesText).toContain("'Incident'");
  expect(nodesText).toContain("'Evidence'");
  expect(nodesText).toContain("'Response'");
});

// ─── 4. Detection truth model ─────────────────────────────────────────────────

test('Detection pipeline node is Evaluated when lastDetectionAt exists but no active records', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  const nodeStatusesText = panel.slice(
    panel.indexOf('const nodeStatuses:'),
    panel.indexOf('// Empty state'),
  );
  const detectionLine = nodeStatusesText.match(/Detection:.*,/)?.[0] ?? '';
  // Complete only when activeDetectionRecordsOk is true
  expect(detectionLine).toContain("activeDetectionRecordsOk ? 'Complete'");
  // Evaluated as fallback when evaluation ran but no active records
  expect(detectionLine).toContain("detectionEvaluationOk ? 'Evaluated'");
  // Must not short-circuit to Complete without checking activeDetectionRecordsOk
  expect(detectionLine).not.toContain("detectionEvaluationOk ? 'Complete'");
});

test('Active Detections KPI uses detections.length not a detection-exists flag', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain('String(detections.length)');
  expect(panel).not.toContain("'Detection exists'");
});

// ─── 5. Alert and Incident truth model ───────────────────────────────────────

test('Alert pipeline node is Not required when detection evaluation ran but no active detections', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  const nodeStatusesText = panel.slice(
    panel.indexOf('const nodeStatuses:'),
    panel.indexOf('// Empty state'),
  );
  const alertLine = nodeStatusesText.match(/Alert:.*,/)?.[0] ?? '';
  expect(alertLine).toContain("'Not required'");
  expect(alertLine).not.toContain("'Blocked'");
});

test('Incident pipeline node is Not required when no active alerts', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  const nodeStatusesText = panel.slice(
    panel.indexOf('const nodeStatuses:'),
    panel.indexOf('// Empty state'),
  );
  const incidentLine = nodeStatusesText.match(/Incident:.*,/)?.[0] ?? '';
  expect(incidentLine).toContain("'Not required'");
  expect(incidentLine).not.toContain("'Blocked'");
});

// ─── 6. LIVE mode simulator wording guard ─────────────────────────────────────

test('threat-monitoring-panel does not contain "Generate Simulator Signal"', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).not.toContain('Generate Simulator Signal');
});

test('LIVE mode uses "Check Worker Status" not simulator wording in Case E', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  const caseEBlock = panel.slice(panel.indexOf('// Case E'), panel.indexOf('// Case F'));
  expect(caseEBlock).toContain('Check Worker Status');
  expect(caseEBlock).not.toContain('Generate Simulator Signal');
  expect(caseEBlock).not.toContain('Simulation signal');
});

test('simulator CTA uses neutral "Create test signal" wording gated by isSimulatorMode', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  const occurrences = [...panel.matchAll(/Create test signal/g)];
  for (const match of occurrences) {
    const precedingText = panel.slice(Math.max(0, (match.index ?? 0) - 400), match.index ?? 0);
    expect(precedingText).toContain('isSimulatorMode');
  }
});

test('Case F CTA is "Review monitoring rules" not fake "Run Detection"', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  const caseFBlock = panel.slice(panel.indexOf('// Case F'), panel.indexOf('// Case F2'));
  expect(caseFBlock).toContain('Review monitoring rules');
  expect(caseFBlock).not.toContain('Run Detection');
});

test('LIVE mode threat page header does not render disabled Generate evidence package as primary action', () => {
  const header = appSource('threat/threat-page-header.tsx');
  expect(header).toContain('!proofChainDisabled');
  expect(header).not.toContain('disabled={proofChainDisabled}');
});

// ─── 7. Telemetry empty state is context-aware ───────────────────────────────

test('telemetry tab does not say "No events yet" when lastTelemetryAt exists', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  const telemetryTabSection = panel.slice(
    panel.indexOf('activeTab === \'telemetry\''),
    panel.indexOf('activeTab === \'detections\''),
  );
  // "No events yet" must not appear unguarded in the telemetry tab
  const noEventsIdx = telemetryTabSection.indexOf('No events yet');
  expect(noEventsIdx).toBe(-1);
});

test('telemetry tab shows "Runtime telemetry freshness" when lastTelemetryAt exists but list is empty', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain('Runtime telemetry freshness');
  expect(panel).toContain('Telemetry list is not loaded in this view.');
});

// ─── 8. Diagnostics are inside a collapsed details block ──────────────────────

test('diagnostics are inside a <details> element not the primary UI', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  // The <details> element must exist
  expect(panel).toContain('<details');
  // Diagnostics summary must say "Diagnostics"
  expect(panel).toContain('Diagnostics');
  // TechnicalRuntimeDetails must be inside a details block
  const detailsIdx = panel.indexOf('<details');
  const technicalIdx = panel.indexOf('<TechnicalRuntimeDetails');
  expect(detailsIdx).toBeGreaterThan(-1);
  expect(technicalIdx).toBeGreaterThan(-1);
  expect(technicalIdx).toBeGreaterThan(detailsIdx);
});

test('TechnicalRuntimeDetails component itself uses <details> for collapsible rendering', () => {
  const tech = appSource('threat/technical-runtime-details.tsx');
  expect(tech).toContain('<details className="tableMeta">');
  expect(tech).not.toContain('<details open');
});

// ─── 9. No /anomalies endpoint is fetched ─────────────────────────────────────

test('threat-monitoring-panel does not fetch /anomalies', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).not.toContain('/anomalies');
});

// ─── 10. No corrupted mojibake characters ─────────────────────────────────────

test('no mojibake in threat-monitoring-panel', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).not.toContain('閑');
  expect(panel).not.toContain('鰹');
  expect(panel).not.toContain('�');
  expect(panel).not.toContain('ï¿½');
});

test('no mojibake in threat-page-header', () => {
  const header = appSource('threat/threat-page-header.tsx');
  expect(header).not.toContain('閑');
  expect(header).not.toContain('�');
});

test('no mojibake in response-action-panel', () => {
  const rap = appSource('threat/response-action-panel.tsx');
  expect(rap).not.toContain('閑');
  expect(rap).not.toContain('�');
});

// ─── 11. Action buttons do not link back to /threat (fake self-loop CTAs) ─────

test('threat header action links do not href back to /threat', () => {
  const header = appSource('threat/threat-page-header.tsx');
  expect(header).not.toContain('href="/threat"');
});

test('response action panel buttons do not link back to /threat', () => {
  const rap = appSource('threat/response-action-panel.tsx');
  expect(rap).not.toContain('href="/threat"');
});

test('detection feed empty state CTAs do not link back to /threat', () => {
  const feed = appSource('threat/detection-feed.tsx');
  expect(feed).not.toContain('href="/threat"');
});

// ─── 12. Response action panel has polished action cards ─────────────────────

test('response action panel shows four operational action cards in live mode', () => {
  const rap = appSource('threat/response-action-panel.tsx');
  expect(rap).toContain('Review alerts');
  expect(rap).toContain('Open incident queue');
  expect(rap).toContain('Configure response policy');
  expect(rap).toContain('Export evidence');
});

test('response action panel has helper text for each action', () => {
  const rap = appSource('threat/response-action-panel.tsx');
  expect(rap).toContain('Inspect alert candidates');
  expect(rap).toContain('Review active incident workflow');
  expect(rap).toContain('Define escalation');
  expect(rap).toContain('Download evidence-ready records');
});

test('response action panel shows no action message when no alerts or incidents', () => {
  const rap = appSource('threat/response-action-panel.tsx');
  expect(rap).toContain('No response action required.');
  expect(rap).toContain('no open alert has escalated to an incident');
});

// ─── 13. Header actions link to real pages ────────────────────────────────────

test('threat page header links to real product pages not placeholders', () => {
  const header = appSource('threat/threat-page-header.tsx');
  expect(header).toContain('href="/alerts"');
  expect(header).toContain('href="/incidents"');
  expect(header).toContain('href="/exports"');
  expect(header).toContain('href="/monitored-systems"');
});
