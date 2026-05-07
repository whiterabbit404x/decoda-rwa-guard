/**
 * Screen 6 – Alerts / Active Alerts contract tests.
 * Source-level: reads .tsx files and asserts on string/structural presence.
 * No browser required.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function appSource(fileName: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', fileName), 'utf-8');
}

// ── 1. /alerts route renders ─────────────────────────────────────
test('/alerts route file exists and exports a default page component', () => {
  const src = appSource('(product)/alerts/page.tsx');
  expect(src).toContain('export default function AlertsPage');
});

// ── 2. Page title "Active Alerts" exists ─────────────────────────
test('page title "Active Alerts" exists', () => {
  const src = appSource('(product)/alerts/page.tsx');
  expect(src).toContain('<h1>Active Alerts</h1>');
});

// ── 3. Search filter exists ───────────────────────────────────────
test('search filter exists', () => {
  const panel = appSource('alerts-panel.tsx');
  expect(panel).toContain('Search alerts...');
});

// ── 4. Severity filter exists ─────────────────────────────────────
test('severity filter exists', () => {
  const panel = appSource('alerts-panel.tsx');
  expect(panel).toContain('Severity filter');
});

// ── 5. Status filter exists ───────────────────────────────────────
test('status filter exists', () => {
  const panel = appSource('alerts-panel.tsx');
  expect(panel).toContain('Status filter');
});

// ── 6. Top metric cards exist ─────────────────────────────────────
test('metric card "Active Alerts" exists', () => {
  const panel = appSource('alerts-panel.tsx');
  expect(panel).toContain('Active Alerts');
});

test('metric card "Critical Alerts" exists', () => {
  const panel = appSource('alerts-panel.tsx');
  expect(panel).toContain('Critical Alerts');
});

test('metric card "High Confidence" exists', () => {
  const panel = appSource('alerts-panel.tsx');
  expect(panel).toContain('High Confidence');
});

test('metric card "Linked Incidents" exists', () => {
  const panel = appSource('alerts-panel.tsx');
  expect(panel).toContain('Linked Incidents');
});

// ── 7. Main table columns exist exactly ──────────────────────────
test('table column "Alert ID" exists', () => {
  const panel = appSource('alerts-panel.tsx');
  expect(panel).toContain('Alert ID');
});

test('table column "Severity" exists', () => {
  const panel = appSource('alerts-panel.tsx');
  expect(panel).toContain('Severity');
});

test('table column "Title" exists', () => {
  const panel = appSource('alerts-panel.tsx');
  expect(panel).toContain("'Title'");
});

test('table column "Asset" exists', () => {
  const panel = appSource('alerts-panel.tsx');
  expect(panel).toContain("'Asset'");
});

test('table column "Status" exists', () => {
  const panel = appSource('alerts-panel.tsx');
  expect(panel).toContain("'Status'");
});

test('table column "Time" exists', () => {
  const panel = appSource('alerts-panel.tsx');
  expect(panel).toContain("'Time'");
});

test('table column "Action" exists', () => {
  const panel = appSource('alerts-panel.tsx');
  expect(panel).toContain("'Action'");
});

// ── 8. Empty state Case A: no telemetry ─────────────────────────
test('empty state shows "No alerts yet because no telemetry has been received" when no telemetry exists', () => {
  const panel = appSource('alerts-panel.tsx');
  expect(panel).toContain('No alerts yet because no telemetry has been received.');
});

// ── 9. Empty state Case B: telemetry exists but no detection ────
test('empty state shows detection blocker when telemetry exists but no detection exists', () => {
  const panel = appSource('alerts-panel.tsx');
  expect(panel).toContain(
    'Telemetry has been received, but no detection has been generated yet.',
  );
});

// ── 10. Empty state Case C: detection exists but no alert ────────
test('empty state shows open-alert blocker when detection exists but no alert exists', () => {
  const panel = appSource('alerts-panel.tsx');
  expect(panel).toContain('Detections exist, but no alert has been opened yet.');
});

// ── 11. Linked to Incident requires valid incident_id ────────────
test('Linked to Incident status requires incident_id to be non-null', () => {
  const panel = appSource('alerts-panel.tsx');
  expect(panel).toContain('Linked to Incident');
  // resolvedStatus() guards against showing Linked to Incident when incident_id is absent
  expect(panel).toContain('linked_to_incident');
  expect(panel).toContain('incident_id');
  // The guard function must check for the absence of incident_id
  const fnStart = panel.indexOf('function resolvedStatus');
  const fnEnd = panel.indexOf('function fmt');
  const fnText = panel.slice(fnStart, fnEnd);
  expect(fnText).toContain('incident_id');
  expect(fnText).toContain("'linked_to_incident'");
});

// ── 12. Simulator evidence not labeled as live_provider ──────────
test('page does not label simulator evidence as live_provider', () => {
  const panel = appSource('alerts-panel.tsx');
  expect(panel).toContain("workspaceSource === 'simulator'");
  expect(panel).toContain("label: 'simulator'");

  const fnStart = panel.indexOf('function evidenceSourcePill');
  const fnEnd = panel.indexOf('function severityPill');
  const fnText = panel.slice(fnStart, fnEnd);
  const simulatorGuardPos = fnText.indexOf("workspaceSource === 'simulator'");
  const liveProviderBranchPos = fnText.indexOf("label: 'live_provider'");
  expect(simulatorGuardPos).toBeGreaterThan(-1);
  expect(liveProviderBranchPos).toBeGreaterThan(simulatorGuardPos);
});

// ── 13. Alert links to detection and incident fields ─────────────
test('page links alert to detection_id and shows warning when unavailable', () => {
  const panel = appSource('alerts-panel.tsx');
  expect(panel).toContain('detection_id');
  expect(panel).toContain('Detection link unavailable');
});

test('page links alert to incident_id and shows View Incident or Open Incident', () => {
  const panel = appSource('alerts-panel.tsx');
  expect(panel).toContain('incident_id');
  expect(panel).toContain('View Incident');
  expect(panel).toContain('Open Incident');
});

// ── 14. Page uses RuntimeSummaryPanel and AlertsPanel ────────────
test('page uses RuntimeSummaryPanel', () => {
  const src = appSource('(product)/alerts/page.tsx');
  expect(src).toContain('RuntimeSummaryPanel');
});

test('page uses AlertsPanel', () => {
  const src = appSource('(product)/alerts/page.tsx');
  expect(src).toContain('AlertsPanel');
});

// ── 15. Page subtitle is correct ─────────────────────────────────
test('page subtitle matches spec', () => {
  const src = appSource('(product)/alerts/page.tsx');
  expect(src).toContain('Review security alerts generated from telemetry and detections.');
});
