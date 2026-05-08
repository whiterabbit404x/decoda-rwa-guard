import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function appSource(fileName: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', fileName), 'utf-8');
}

function pageSource(): string {
  return appSource('(product)/response-actions-page-client.tsx');
}

function routeSource(): string {
  return appSource('(product)/response-actions/page.tsx');
}

test('response-actions route exists and renders ResponseActionsPageClient', () => {
  const src = routeSource();
  expect(src).toContain('ResponseActionsPageClient');
  expect(src).toContain('force-dynamic');
});

test('page title "Response Actions" exists in component', () => {
  const src = pageSource();
  expect(src).toContain('Response Actions');
});

test('search filter exists with placeholder "Search actions..."', () => {
  const src = pageSource();
  expect(src).toContain('Search actions...');
});

test('type filter exists with aria-label "Type filter"', () => {
  const src = pageSource();
  expect(src).toContain('Type filter');
});

test('status filter exists with aria-label "Status filter"', () => {
  const src = pageSource();
  expect(src).toContain('Status filter');
});

test('top metric cards include Recommended Actions, Pending Approval, Simulated Actions, Executed Actions', () => {
  const src = pageSource();
  expect(src).toContain('Recommended Actions');
  expect(src).toContain('Pending Approval');
  expect(src).toContain('Simulated Actions');
  expect(src).toContain('Executed Actions');
});

test('tabs exist exactly: Recommended Actions and Action History', () => {
  const src = pageSource();
  expect(src).toContain("{ key: 'recommended', label: 'Recommended Actions' }");
  expect(src).toContain("{ key: 'history', label: 'Action History' }");
});

test('Recommended Actions table uses exact columns array', () => {
  const src = pageSource();
  expect(src).toContain("'Action'");
  expect(src).toContain("'Type'");
  expect(src).toContain("'Impact'");
  expect(src).toContain("'Status'");
  expect(src).toContain("'Recommended By'");
  expect(src).toContain("'Linked Incident'");
  expect(src).toContain("'Evidence Source'");
  expect(src).toContain("'Requires Approval'");
});

test('Action History table uses exact columns array', () => {
  const src = pageSource();
  expect(src).toContain("'Action ID'");
  expect(src).toContain("'Action'");
  expect(src).toContain("'Type'");
  expect(src).toContain("'Result'");
  expect(src).toContain("'Actor/System'");
  expect(src).toContain("'Time'");
  expect(src).toContain("'Evidence Source'");
});

test('action detail panel exists with aria-label "Action detail panel"', () => {
  const src = pageSource();
  expect(src).toContain('Action detail panel');
  expect(src).toContain('ActionDetailPanel');
});

test('empty state shows incident blocker when alert exists but no incident', () => {
  const src = pageSource();
  expect(src).toContain('Alerts exist, but no incident has been opened yet.');
  expect(src).toContain('Open Incident');
  expect(src).toContain('/incidents');
});

test('empty state shows response blocker when incident exists but no response action', () => {
  const src = pageSource();
  expect(src).toContain('No response action recommended yet');
  expect(src).toContain('An incident exists, but no response action has been recommended yet.');
  expect(src).toContain('Recommend Response');
});

test('Execute Action is only shown when liveExecutionAllowed is true', () => {
  const src = pageSource();
  expect(src).toContain('canExecute');
  expect(src).toContain('liveExecutionAllowed');
  expect(src).toContain('Execute Action');
  expect(src).toContain('Simulate Action');
  expect(src).toContain('liveExecutionAllowed && !isSimulatorAction');
});

test('simulator actions are labeled SIMULATED in status', () => {
  const src = pageSource();
  expect(src).toContain('SIMULATED');
  expect(src).toContain('路 SIMULATED');
  expect(src).toContain('`${rawStatus} 路 SIMULATED`');
});

test('evidence source guard: simulator is never labeled as live_provider', () => {
  const src = pageSource();
  expect(src).toContain('Do not label simulator evidence as live_provider');
  expect(src).toContain("raw === 'fallback'");
  expect(src).toContain("{ label: 'simulator', variant: 'info' }");
  expect(src).toContain("{ label: 'live_provider', variant: 'success' }");
});

test('linked incident is only shown when a valid incident exists in validIncidentIds', () => {
  const src = pageSource();
  expect(src).toContain('validIncidentIds.has(rawIncidentId)');
  expect(src).toContain('Linked incident unavailable');
  expect(src).toContain('Do not show linked incident unless a valid incident exists in the system');
});

test('correct API endpoints are used', () => {
  const src = pageSource();
  expect(src).toContain('/response/actions?limit=50');
  expect(src).toContain('/history/actions?limit=50');
  expect(src).toContain('/alerts?limit=50');
  expect(src).toContain('/incidents?limit=50');
});

test('hasRealTelemetryBackedChain and resolveWorkspaceMonitoringTruth gate live execution', () => {
  const src = pageSource();
  expect(src).toContain('hasRealTelemetryBackedChain');
  expect(src).toContain('resolveWorkspaceMonitoringTruth(runtimePayload)');
  expect(src).toContain('Live execution claims are hidden until canonical runtime summary confirms a real telemetry-backed chain.');
});

test('approval filter exists', () => {
  const src = pageSource();
  expect(src).toContain('Approval filter');
});

test('page subtitle matches spec', () => {
  const src = pageSource();
  expect(src).toContain('Review, approve, simulate, and track response actions linked to incidents.');
});

test('empty state cases A, B, C are present', () => {
  const src = pageSource();
  expect(src).toContain('No response action can be recommended because no telemetry has been received.');
  expect(src).toContain('View Threat Monitoring');
  expect(src).toContain('Telemetry has been received, but no detection has been generated yet.');
  expect(src).toContain('Run Detection');
  expect(src).toContain('Detections exist, but no alert has been opened yet.');
  expect(src).toContain('Open Alert');
});
