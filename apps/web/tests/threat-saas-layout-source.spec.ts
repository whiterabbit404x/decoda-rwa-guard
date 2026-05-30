/**
 * SaaS layout source-level tests for the /threat page.
 * Verifies the polished enterprise threat monitoring layout requirements:
 * - No RuntimeSummaryPanel at top level of /threat route
 * - Workflow chain text is present
 * - No /anomalies fetch (no backend endpoint)
 * - No "Telemetry Events" KPI (would always show 0, contradicts UI)
 * - Diagnostics uses <details> collapsed at bottom
 * - No mojibake characters in threat components
 * - Simulator evidence never labeled live_provider
 * - No fake buttons linking back to /threat
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function appSource(fileName: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', fileName), 'utf-8');
}

// --- 1. /threat page does not render RuntimeSummaryPanel at top level ---

test('/threat page source does not render RuntimeSummaryPanel at top level', () => {
  const src = appSource('(product)/threat/page.tsx');
  expect(src).not.toContain('RuntimeSummaryPanel');
});

test('/threat page renders only ThreatMonitoringPanel', () => {
  const src = appSource('(product)/threat/page.tsx');
  expect(src).toContain('ThreatMonitoringPanel');
  // No hero section injected at the route level — header lives in ThreatMonitoringPanel
  expect(src).not.toContain('<h1>');
  expect(src).not.toContain('hero compactHero');
});

// --- 2. ThreatMonitoringPanel contains the workflow chain text ---

test('ThreatMonitoringPanel contains "Telemetry → Detection → Alert → Incident → Evidence → Response"', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain('Telemetry → Detection → Alert → Incident → Evidence → Response');
});

// --- 3. ThreatMonitoringPanel does not fetch /anomalies ---

test('ThreatMonitoringPanel does not fetch /anomalies endpoint', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).not.toContain('`${apiUrl}/anomalies`');
  expect(panel).not.toContain('/anomalies`, {');
  expect(panel).not.toContain("apiUrl}/anomalies'");
});

// --- 4. ThreatMonitoringPanel does not display "Telemetry Events" KPI ---

test('ThreatMonitoringPanel does not display "Telemetry Events" when telemetry list is not fetched', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  // The telemetry list endpoint does not exist at workspace scope.
  // Showing "Telemetry Events: 0" when no list was fetched is contradictory.
  expect(panel).not.toContain('Telemetry Events');
});

test('ThreatMonitoringPanel uses "Latest Telemetry" instead of "Telemetry Events"', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain('Latest Telemetry');
  expect(panel).toContain('Runtime summary signal');
});

// --- 5. Diagnostics uses <details> and is visually secondary ---

test('Diagnostics uses <details> element in ThreatMonitoringPanel', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  // Must use <details> so it renders collapsed by default
  expect(panel).toContain('<details');
  expect(panel).toContain('aria-label="Diagnostics"');
  // <summary> provides the toggle label
  expect(panel).toContain('<summary');
  // TechnicalRuntimeDetails must be inside the Diagnostics block
  const diagIdx = panel.indexOf('aria-label="Diagnostics"');
  const techIdx = panel.indexOf('<TechnicalRuntimeDetails');
  expect(diagIdx).toBeGreaterThan(-1);
  expect(techIdx).toBeGreaterThan(diagIdx);
});

test('Diagnostics is not open by default', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  // <details open would make it visible by default — that violates the collapsed requirement
  expect(panel).not.toContain('<details open');
});

// --- 6. No mojibake/corrupted characters in threat page and components ---

test('threat page.tsx has no mojibake characters', () => {
  const src = appSource('(product)/threat/page.tsx');
  expect(src).not.toContain('闻');
  expect(src).not.toContain('鐸');
  expect(src).not.toContain('ï¿½');
  expect(src).not.toContain('�');
});

test('threat-monitoring-panel.tsx has no mojibake characters', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).not.toContain('闻');
  expect(panel).not.toContain('鐸');
  expect(panel).not.toContain('ï¿½');
  expect(panel).not.toContain('�');
});

test('threat/threat-page-header.tsx has no mojibake characters', () => {
  const header = appSource('threat/threat-page-header.tsx');
  expect(header).not.toContain('闻');
  expect(header).not.toContain('鐸');
  expect(header).not.toContain('ï¿½');
});

test('threat/detection-feed.tsx has no mojibake characters', () => {
  const feed = appSource('threat/detection-feed.tsx');
  expect(feed).not.toContain('闻');
  expect(feed).not.toContain('ï¿½');
});

// --- 7. Simulator evidence is never labeled live_provider ---

test('evidencePill simulator guard appears before live_provider label assignment', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  const fnStart = panel.indexOf('function evidencePill');
  const fnEnd = panel.indexOf('function nodeStatusVariant');
  const fnText = panel.slice(fnStart, fnEnd);

  const simulatorGuardPos = fnText.indexOf("workspaceSource === 'simulator'");
  const liveProviderPos = fnText.indexOf("label: 'live_provider'");

  expect(simulatorGuardPos).toBeGreaterThan(-1);
  expect(liveProviderPos).toBeGreaterThan(-1);
  expect(simulatorGuardPos).toBeLessThan(liveProviderPos);
});

// --- 8. Fake buttons do not link back to /threat ---

test('Case F empty state (no detection) CTA does not link back to /threat', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  const caseFBlock = panel.slice(panel.indexOf('// Case F'), panel.indexOf('// Case G'));
  expect(caseFBlock).not.toContain("ctaHref: '/threat'");
  expect(caseFBlock).not.toContain("href=\"/threat\"");
});

test('Case G empty state (no alert) CTA does not link back to /threat', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  const caseGBlock = panel.slice(panel.indexOf('// Case G'), panel.indexOf('return null'));
  expect(caseGBlock).not.toContain("ctaHref: '/threat'");
  expect(caseGBlock).not.toContain("href=\"/threat\"");
});

test('ThreatPageHeader action links do not point to /threat', () => {
  const header = appSource('threat/threat-page-header.tsx');
  // None of the CTA buttons should loop back to the same /threat page
  const hrefMatches = [...header.matchAll(/href="([^"]+)"/g)].map((m) => m[1]);
  for (const href of hrefMatches) {
    expect(href).not.toBe('/threat');
  }
});
