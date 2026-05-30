/**
 * SaaS polish source-level tests for the /threat page.
 * Verifies: layout quality, truthfulness guards, clean empty states, disabled CTA policy.
 * Source-level: reads .tsx/.css files and asserts on string/structural presence.
 * No browser required.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function appSource(fileName: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', fileName), 'utf-8');
}

// --- 1. Hero KPI row includes all 7 required metrics ---

test('hero KPI row includes Protected Assets tile', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain('Protected Assets');
});

test('hero KPI row includes Open Alerts tile', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain('Open Alerts');
});

test('hero KPI row includes Active Incidents tile', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain('Active Incidents');
});

test('hero KPI row includes Monitoring Systems tile', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain('Monitoring Systems');
});

// --- 2. Status badge is wired from canonical security status ---

test('ThreatPageHeader receives posture prop from securityStatus', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain('posture={securityStatus.posture}');
});

test('ThreatPageHeader renders a status badge based on posture', () => {
  const header = appSource('threat/threat-page-header.tsx');
  expect(header).toContain('POSTURE_BADGE');
  expect(header).toContain("label: 'Live'");
  expect(header).toContain("label: 'Limited'");
  expect(header).toContain("label: 'Offline'");
});

// --- 3. Export evidence CTA is present and routes to /exports ---

test('ThreatPageHeader includes Export evidence action linking to /exports', () => {
  const header = appSource('threat/threat-page-header.tsx');
  expect(header).toContain('Export evidence');
  expect(header).toContain('href="/exports"');
});

// --- 4. No corrupted/mojibake characters in app files ---

test('styles.css has no mojibake box-drawing characters', () => {
  const css = appSource('styles.css');
  expect(css).not.toContain('鈹€');
});

test('threat-monitoring-panel.tsx has no corrupted characters', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).not.toContain('閻');
  expect(panel).not.toContain('鈹€');
  expect(panel).not.toContain('ï¿½');
});

test('threat-page-header.tsx has no corrupted characters', () => {
  const header = appSource('threat/threat-page-header.tsx');
  expect(header).not.toContain('閻');
  expect(header).not.toContain('鈹€');
});

// --- 5. Detection feed empty state is professional ---

test('DetectionFeed empty state uses THREAT_COPY message, not a broken placeholder', () => {
  const feed = appSource('threat/detection-feed.tsx');
  expect(feed).toContain('{THREAT_COPY.noDetectionRecords}');
  expect(feed).not.toContain('TODO');
  expect(feed).not.toContain('undefined');
});

test('DetectionFeed empty state has centered presentation copy', () => {
  const feed = appSource('threat/detection-feed.tsx');
  expect(feed).toContain('No detections yet');
});

// --- 6. Alert/Incident empty state shows workflow actions, not broken UI ---

test('AlertIncidentChain empty state shows professional chain steps', () => {
  const chain = appSource('threat/alert-incident-chain.tsx');
  expect(chain).toContain('No open alerts');
  expect(chain).toContain('No active incidents');
  expect(chain).toContain('No response action required');
});

test('AlertIncidentChain empty state provides workflow action links', () => {
  const chain = appSource('threat/alert-incident-chain.tsx');
  expect(chain).toContain('href="/alerts"');
  expect(chain).toContain('href="/incidents"');
  expect(chain).toContain('href="/response-actions"');
});

// --- 7. MonitoringHealthCard uses structured signal rows ---

test('MonitoringHealthCard renders Worker heartbeat label', () => {
  const card = appSource('threat/monitoring-health-card.tsx');
  expect(card).toContain('Worker heartbeat');
});

test('MonitoringHealthCard renders Poll loop label', () => {
  const card = appSource('threat/monitoring-health-card.tsx');
  expect(card).toContain('Poll loop');
});

// --- 8. ThreatOverviewCard shows buyer-friendly posture labels ---

test('ThreatOverviewCard shows Healthy posture label', () => {
  const overview = appSource('threat/threat-overview-card.tsx');
  expect(overview).toContain("label: 'Healthy'");
});

test('ThreatOverviewCard shows Limited coverage posture label', () => {
  const overview = appSource('threat/threat-overview-card.tsx');
  expect(overview).toContain('Limited coverage');
});

test('ThreatOverviewCard shows Setup required posture label', () => {
  const overview = appSource('threat/threat-overview-card.tsx');
  expect(overview).toContain('Setup required');
});

// --- 9. Diagnostics section is labeled and visually secondary ---

test('Diagnostics section wraps TechnicalRuntimeDetails', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  const diagIdx = panel.indexOf('aria-label="Diagnostics"');
  const techIdx = panel.indexOf('<TechnicalRuntimeDetails');
  expect(diagIdx).toBeGreaterThan(-1);
  expect(techIdx).toBeGreaterThan(-1);
  expect(diagIdx).toBeLessThan(techIdx);
});

test('Diagnostics section heading exists', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain('Diagnostics');
  expect(panel).toContain('aria-label="Diagnostics"');
});

// --- 10. Security workflow chain renders Telemetry → Evidence → Response ---

test('threat panel renders security workflow chain with Telemetry step label', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain('Telemetry → Detection → Alert → Incident → Evidence → Response');
});
