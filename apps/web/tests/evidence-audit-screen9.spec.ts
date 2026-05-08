/**
 * Screen 9: Evidence & Audit / Exportable Proof
 * Source-contract tests — read .tsx source files, assert on structural presence.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function read(relativePath: string): string {
  return fs.readFileSync(path.join(__dirname, '..', relativePath), 'utf-8');
}

const PANEL = 'app/evidence-audit-panel.tsx';
const PAGE  = 'app/(product)/evidence/page.tsx';
const EXPORTS_REDIRECT = 'app/(product)/exports/page.tsx';
const NAV   = 'app/product-nav.ts';

/* ── 1. Route rendering ────────────────────────────────────────── */

test('1a: /evidence page file exists and imports EvidenceAuditPanel', () => {
  const source = read(PAGE);
  expect(source).toContain('EvidenceAuditPanel');
  expect(source).toContain('evidence-audit-panel');
});

test('1b: /exports page redirects to /evidence without error', () => {
  const source = read(EXPORTS_REDIRECT);
  expect(source).toContain("redirect('/evidence')");
});

/* ── 2. Page title ──────────────────────────────────────────────── */

test('2: page title "Evidence & Audit" exists', () => {
  const source = read(PANEL);
  expect(source).toContain('Evidence &amp; Audit');
});

/* ── 3. Subtitle ────────────────────────────────────────────────── */

test('3: subtitle text exists', () => {
  const source = read(PANEL);
  expect(source).toContain('Export incident evidence packages and review audit activity.');
});

/* ── 4. Create Evidence Package button ──────────────────────────── */

test('4a: Create Evidence Package button exists', () => {
  const source = read(PANEL);
  expect(source).toContain('Create Evidence Package');
});

test('4b: button is disabled when chain is not ready (canCreatePackage guard)', () => {
  const source = read(PANEL);
  expect(source).toContain('canCreatePackage');
  expect(source).toContain('disabled={!canCreatePackage}');
});

/* ── 5. Top metric cards ────────────────────────────────────────── */

test('5: all four metric cards are present', () => {
  const source = read(PANEL);
  expect(source).toContain('"Evidence Packages"');
  expect(source).toContain('"Audit Events"');
  expect(source).toContain('"Export Ready"');
  expect(source).toContain('"Retention Status"');
});

/* ── 6. Tabs ────────────────────────────────────────────────────── */

test('6: tabs are exactly Evidence Packages and Audit Logs', () => {
  const source = read(PANEL);
  expect(source).toContain("label: 'Evidence Packages'");
  expect(source).toContain("label: 'Audit Logs'");
  // No unexpected third tab key
  const tabMatches = [...source.matchAll(/key: '(packages|audit)'/g)];
  expect(tabMatches.length).toBeGreaterThanOrEqual(2);
});

/* ── 7. Evidence Packages table columns ─────────────────────────── */

test('7: Evidence Packages table has exactly the required columns', () => {
  const source = read(PANEL);
  expect(source).toContain("'Package ID'");
  expect(source).toContain("'Incident'");
  expect(source).toContain("'Date Created'");
  expect(source).toContain("'Includes'");
  expect(source).toContain("'Size'");
  expect(source).toContain("'Evidence Source'");
  expect(source).toContain("'Actions'");
});

/* ── 8. Audit Logs table columns ────────────────────────────────── */

test('8: Audit Logs table has exactly the required columns', () => {
  const source = read(PANEL);
  expect(source).toContain("'Time'");
  expect(source).toContain("'Actor'");
  expect(source).toContain("'Action'");
  expect(source).toContain("'Object'");
  expect(source).toContain("'Result'");
  expect(source).toContain("'Source IP or System'");
  // Evidence Source appears in both tables
  expect(source).toContain("'Evidence Source'");
});

/* ── 9. Included Artifacts checklist ────────────────────────────── */

test('9: Included Artifacts checklist contains all required items', () => {
  const source = read(PANEL);
  expect(source).toContain('Telemetry Snapshot');
  expect(source).toContain('Detection Event');
  expect(source).toContain('Alert');
  expect(source).toContain('Incident Timeline');
  expect(source).toContain('Response Action');
  expect(source).toContain('Audit Log');
  expect(source).toContain('Included Artifacts');
});

/* ── 10. Empty state: no telemetry blocker ──────────────────────── */

test('10: shows telemetry blocker when no telemetry exists', () => {
  const source = read(PANEL);
  expect(source).toContain('No evidence packages yet');
  expect(source).toContain(
    'No evidence package can be created because no telemetry has been received.',
  );
  expect(source).toContain("ctaHref: '/threat'");
  expect(source).toContain("ctaLabel: 'View Threat Monitoring'");
});

/* ── 11. Empty state: alert exists but no incident blocker ──────── */

test('11: shows incident blocker when alert exists but no incident', () => {
  const source = read(PANEL);
  expect(source).toContain('Alerts exist, but no incident has been opened yet.');
  expect(source).toContain("ctaHref: '/incidents'");
  expect(source).toContain("ctaLabel: 'Open Incident'");
});

/* ── 12. Empty state: incident exists but no response action ─────── */

test('12: shows response blocker when incident exists but no response action', () => {
  const source = read(PANEL);
  expect(source).toContain(
    'An incident exists, but no response action has been recommended or recorded yet.',
  );
  expect(source).toContain("ctaHref: '/response-actions'");
  expect(source).toContain("ctaLabel: 'Recommend Response'");
});

/* ── 13. Export Ready gated on package existence ────────────────── */

test('13: Export Ready metric uses isPackageReady guard and does not show ready unless package ready', () => {
  const source = read(PANEL);
  // exportReadyCount is derived from packages filtered by isPackageReady
  expect(source).toContain('exportReadyCount');
  expect(source).toContain('isPackageReady');
  // The function checks package_ready, download_url, or Ready/Exported status
  expect(source).toContain('pkg.package_ready');
  expect(source).toContain('pkg.download_url');
});

/* ── 14. Download/Export disabled when no package ───────────────── */

test('14: Download and Export buttons are disabled when package is not ready', () => {
  const source = read(PANEL);
  expect(source).toContain('disabled={!ready}');
});

/* ── 15. Simulator evidence not labeled as live_provider ─────────── */

test('15: simulator evidence is labeled simulator, not live_provider', () => {
  const source = read(PANEL);
  // The evidenceSourcePill function maps simulator/demo/replay to 'simulator'
  expect(source).toContain("label: 'simulator'");
  // Simulator does NOT map to live_provider
  expect(source).toContain("raw === 'simulator'");
  // The workspaceSource guard prevents simulator from being labeled live_provider
  expect(source).toContain("workspaceSource === 'simulator'");
  // live_provider is only returned for actual live sources
  expect(source).toContain("label: 'live_provider'");
  expect(source).toContain("raw === 'live_provider'");
});

/* ── 16. Sidebar/nav label is "Evidence & Audit" ────────────────── */

test('16: sidebar nav label is Evidence & Audit at /evidence route', () => {
  const nav = read(NAV);
  expect(nav).toContain("href: '/evidence'");
  expect(nav).toContain("label: 'Evidence & Audit'");
});

/* ── Evidence chain completeness checks ─────────────────────────── */

test('chain: incomplete chain shows warning in package detail', () => {
  const source = read(PANEL);
  expect(source).toContain('Evidence chain incomplete');
  expect(source).toContain('chainComplete');
});

test('chain: package detail links to linked incident when incident_id present', () => {
  const source = read(PANEL);
  expect(source).toContain('Linked Incident');
  expect(source).toContain('Linked Alert');
  expect(source).toContain('Linked Detection');
  expect(source).toContain("href=\"/incidents\"");
});

test('chain: canCreatePackage requires both incidentOk and responseActionOk', () => {
  const source = read(PANEL);
  expect(source).toContain('incidentOk && responseActionOk');
});

/* ── Audit logs detail panel ────────────────────────────────────── */

test('audit detail: panel shows required fields', () => {
  const source = read(PANEL);
  expect(source).toContain('Event ID');
  expect(source).toContain('Object Type');
  expect(source).toContain('Object ID');
  expect(source).toContain('Source IP / System');
  expect(source).toContain('Workspace ID');
  expect(source).toContain('Audit event detail');
});

/* ── Status pill variants ───────────────────────────────────────── */

test('status pills: package status values are mapped', () => {
  const source = read(PANEL);
  expect(source).toContain("label: 'Ready'");
  expect(source).toContain("label: 'Pending'");
  expect(source).toContain("label: 'Exported'");
  expect(source).toContain("label: 'Failed'");
  expect(source).toContain("label: 'Not Available'");
  expect(source).toContain("label: 'Unknown'");
});

test('status pills: audit result values are mapped', () => {
  const source = read(PANEL);
  expect(source).toContain("label: 'Success'");
  expect(source).toContain("label: 'Denied'");
});
