/**
 * Screen 7 – Incidents / Investigation Workflow contract tests.
 * Source-level: reads .tsx files and asserts on string/structural presence.
 * No browser required.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function appSource(fileName: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', fileName), 'utf-8');
}

test('/incidents route file exists and exports a default page component', () => {
  const src = appSource('(product)/incidents/page.tsx');
  expect(src).toContain('export default function IncidentsPage');
  expect(src).toContain('<IncidentsPanel />');
});

test('page title and subtitle exist', () => {
  const src = appSource('(product)/incidents/page.tsx');
  expect(src).toContain('<h1>Incidents</h1>');
  expect(src).toContain('Investigate alert-driven incidents, evidence, and response progress.');
});

test('search filter exists', () => {
  const panel = appSource('incidents-panel.tsx');
  expect(panel).toContain('Search incidents...');
  expect(panel).toContain('aria-label="Search incidents"');
});

test('severity filter exists', () => {
  const panel = appSource('incidents-panel.tsx');
  expect(panel).toContain('aria-label="Severity filter"');
  expect(panel).toContain('All Severities');
});

test('status filter exists', () => {
  const panel = appSource('incidents-panel.tsx');
  expect(panel).toContain('aria-label="Status filter"');
  expect(panel).toContain('All Statuses');
});

test('assignee filter exists', () => {
  const panel = appSource('incidents-panel.tsx');
  expect(panel).toContain('Assignee user ID...');
  expect(panel).toContain('aria-label="Assignee filter"');
});

test('top metric cards exist', () => {
  const panel = appSource('incidents-panel.tsx');
  expect(panel).toContain('Open Incidents');
  expect(panel).toContain('Critical Incidents');
  expect(panel).toContain('In Investigation');
  expect(panel).toContain('Awaiting Response');
});

test('incident table columns exist exactly', () => {
  const panel = appSource('incidents-panel.tsx');
  expect(panel).toContain(
    "const INCIDENT_TABLE_HEADERS = ['Incident ID', 'Severity', 'Title', 'Asset', 'Status', 'Created', 'Action']",
  );
});

test('detail tabs exist exactly', () => {
  const panel = appSource('incidents-panel.tsx');
  expect(panel).toContain("label: 'Overview'");
  expect(panel).toContain("label: 'Timeline'");
  expect(panel).toContain("label: 'Alerts'");
  expect(panel).toContain("label: 'Evidence'");
  expect(panel).toContain("label: 'Response Actions'");
});

test('incident progress checklist exists', () => {
  const panel = appSource('incidents-panel.tsx');
  expect(panel).toContain('Alert Received');
  expect(panel).toContain('Investigation Started');
  expect(panel).toContain('Evidence Collected');
  expect(panel).toContain('Response Initiated');
  expect(panel).toContain('Resolution');
});

test('empty state shows no telemetry blocker', () => {
  const panel = appSource('incidents-panel.tsx');
  expect(panel).toContain('No incidents can be opened because no telemetry has been received.');
  expect(panel).toContain('View Threat Monitoring');
  expect(panel).toContain("ctaHref: '/threat'");
});

test('empty state shows telemetry exists but no detection blocker', () => {
  const panel = appSource('incidents-panel.tsx');
  expect(panel).toContain('Telemetry has been received, but no detection has been generated yet.');
});

test('empty state shows detection exists but no alert blocker', () => {
  const panel = appSource('incidents-panel.tsx');
  expect(panel).toContain('Detections exist, but no alert has been opened yet.');
  expect(panel).toContain('Open Alert');
});

test('empty state shows alert exists but no incident blocker', () => {
  const panel = appSource('incidents-panel.tsx');
  expect(panel).toContain('No incidents opened');
  expect(panel).toContain('Alerts exist, but no incident has been opened yet.');
  expect(panel).toContain('Open Incident');
});

test('page does not show linked alert unless valid alert exists', () => {
  const panel = appSource('incidents-panel.tsx');
  expect(panel).toContain('const hasLinkedAlert = !!incident.source_alert_id');
  expect(panel).toContain('Linked alert unavailable');
  expect(panel).toContain('No alert link will be shown without a valid alert.');
});

test('page does not show response action ready unless valid incident/action exists', () => {
  const panel = appSource('incidents-panel.tsx');
  expect(panel).toContain('No response action recommended yet.');
  expect(panel).toContain('responseActions.length === 0');
});

test('page does not label simulator evidence as live_provider', () => {
  const panel = appSource('incidents-panel.tsx');
  expect(panel).toContain('Simulator evidence must not be labeled as live_provider.');
  expect(panel).toContain("return { label: 'simulator', variant: 'info' }");
  expect(panel).toContain("raw === 'live' || raw === 'live_provider'");
});

test('export evidence CTA links to evidence route', () => {
  const panel = appSource('incidents-panel.tsx');
  expect(panel).toContain('Export Evidence');
  expect(panel).toContain('href="/evidence"');
});

test('timeline tab columns exist', () => {
  const panel = appSource('incidents-panel.tsx');
  expect(panel).toContain("const TIMELINE_HEADERS = ['Time', 'Event', 'Actor / System', 'Result', 'Evidence Source']");
});

test('alerts tab columns exist', () => {
  const panel = appSource('incidents-panel.tsx');
  expect(panel).toContain("const ALERTS_TAB_HEADERS = ['Alert ID', 'Severity', 'Title', 'Detection Type', 'Confidence', 'Status']");
});

test('evidence tab columns exist', () => {
  const panel = appSource('incidents-panel.tsx');
  expect(panel).toContain("const EVIDENCE_HEADERS = ['Evidence ID', 'Type', 'Source', 'Created', 'In Package', 'Action']");
});

test('response actions tab columns exist', () => {
  const panel = appSource('incidents-panel.tsx');
  expect(panel).toContain("const RESPONSE_HEADERS = ['Action', 'Type', 'Status', 'Requires Approval', 'Evidence Source', 'Action']");
});
