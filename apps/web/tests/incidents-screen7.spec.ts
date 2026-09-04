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

test('full investigation tabs exist exactly', () => {
  // Screen 7 information architecture: the Case File beside the queue is a compact
  // SUMMARY of the case; the tabbed forensic record lives on Open Full Investigation,
  // which has the main content width to render it without compressing it.
  const tabs = appSource('incident-case-file-tabs.tsx');
  expect(tabs).toContain("label: 'Overview'");
  expect(tabs).toContain("label: 'Timeline'");
  expect(tabs).toContain("label: 'Alerts'");
  expect(tabs).toContain("label: 'Evidence'");
  expect(tabs).toContain("label: 'Response Actions'");
  expect(tabs).toContain("label: 'AI Investigation'");
});

test('the Case File is a summary, not a second copy of the forensic record', () => {
  const panel = appSource('incidents-panel.tsx');
  // It answers "what is this case and what state is it in" and hands over.
  expect(panel).toContain('Integrity Summary');
  expect(panel).toContain('Open Full Investigation');
  // The detail — artifact directory, lifecycle chronology, AI triage — is not
  // rendered into the narrow column.
  expect(panel).not.toContain('IncidentEvidenceTab');
  expect(panel).not.toContain('IncidentForensicTimeline');
  expect(panel).not.toContain('AiInvestigationPanel');
});

test('investigation progress comes from the canonical workflow stages (not a browser-inferred checklist)', () => {
  const panel = appSource('incidents-panel.tsx');
  // The Case File states how far the case has got, folded from the persisted Screen 7
  // workflow stages + the canonical AI Investigation Summary state — never the old
  // locally inferred five-step checklist that disagreed with the full incident page.
  const full = appSource('forensic-investigator-panel.tsx');
  expect(panel).toContain('aria-label="Investigation progress"');
  expect(panel).toContain('const workflowStages = analysis?.workflow_stages ?? [];');
  expect(panel).toContain('summarizeWorkflowProgress(workflowStages)');
  expect(panel).toContain('investigationSummaryState(analysis.status, investigation?.ai_triage?.status)');
  // The full stage checklist stays on the full investigation workspace, where it is
  // not pushed below the fold by a 360px column.
  expect(full).toContain('Investigation Workflow');
  expect(full).toContain('stages={analysis.workflow_stages ?? []}');
  // The legacy, browser-inferred progress labels are gone (they are what disagreed with
  // the canonical full page).
  expect(panel).not.toContain("label: 'Alert Received'");
  expect(panel).not.toContain("label: 'Investigation Started'");
  expect(panel).not.toContain("label: 'Evidence Collected'");
  expect(panel).not.toContain('function recommendedNextAction');
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
  // The Response Actions tab fails closed on an empty action list (prop is `actions`).
  expect(panel).toContain('actions.length === 0');
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
  expect(panel).toContain("const ALERTS_TAB_HEADERS = ['Alert ID', 'Severity', 'Title', 'Detection Type', 'Detected By', 'Confidence', 'Status', 'Action']");
});

test('the linked alert is reachable, and the link says where it actually goes', () => {
  // Screen 6 has no per-alert route. A control promising to open "this alert" that
  // in fact lands on the list would be a false claim, so the label names the list.
  const panel = appSource('incidents-panel.tsx');
  expect(panel).toContain('View in Alerts');
  expect(panel).not.toContain('/alerts/${linkedAlert.id}');
});

test('evidence tab columns exist', () => {
  const panel = appSource('incidents-panel.tsx');
  expect(panel).toContain("const EVIDENCE_HEADERS = ['Evidence ID', 'Type', 'Source', 'Created', 'In Package', 'Action']");
});

test('response actions tab columns exist', () => {
  const panel = appSource('incidents-panel.tsx');
  expect(panel).toContain("const RESPONSE_HEADERS = ['Action', 'Type', 'Status', 'Requires Approval', 'Evidence Source', 'Action']");
});
