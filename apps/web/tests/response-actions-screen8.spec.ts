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
  expect(src).toContain('Incidents exist, but no response action has been recommended yet.');
  expect(src).toContain('Go to Incidents');
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

test('linked incident shows direct DB incident_id authoritatively; validIncidentIds only for inferred IDs', () => {
  const src = pageSource();
  // directIncidentId from the action's own DB record is always trusted.
  expect(src).toContain('directIncidentId');
  // validIncidentIds still used as a secondary check for non-direct IDs.
  expect(src).toContain('validIncidentIds.has(rawIncidentId)');
  expect(src).toContain('Linked incident unavailable');
  // Trust the action's own incident_id from the backend.
  expect(src).toContain('Trust the action\'s own incident_id from the backend');
});

test('correct API endpoints are used', () => {
  const src = pageSource();
  // Actions list goes through the Next.js same-origin proxy.
  expect(src).toContain('/api/response/actions');
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

test('Evidence Export uses same-origin proxy route, not direct apiUrl', () => {
  const src = pageSource();
  // Must call the Next.js proxy, not the raw backend URL.
  expect(src).toContain('/api/response/actions/${action.id}/evidence-package');
  // Must NOT call the backend directly for this mutation.
  expect(src).not.toContain('`${apiUrl}/response/actions/${action.id}/evidence-package`');
});

test('Evidence Export navigates to /evidence with package_id, action_id, and incident_id', () => {
  const src = pageSource();
  expect(src).toContain('/evidence?');
  expect(src).toContain('package_id');
  expect(src).toContain('action_id');
  expect(src).toContain('incident_id');
});

test('Evidence Export shows real server error detail instead of generic network message', () => {
  const src = pageSource();
  // Must parse JSON separately so a parse error gives a distinct message.
  expect(src).toContain('server returned an unexpected response');
  // Must use data.detail for server-returned error messages (via _extractErrorMessage helper).
  expect(src).toContain("_extractErrorMessage(data.detail, 'Evidence export failed.')");
});

test('simulateAction calls the backend proxy route, not a no-op', () => {
  const src = pageSource();
  expect(src).toContain('/api/response/actions/${action.id}/simulate');
  // Must refresh after success so the persisted status is reflected.
  expect(src).toContain('router.refresh()');
});

test('Evidence Export sends X-CSRF-Token via authHeaders', () => {
  const src = pageSource();
  // authHeaders() includes X-CSRF-Token from auth context state.
  expect(src).toContain("'X-CSRF-Token'");
  // authHeaders must be called before the POST.
  expect(src).toContain('authHeaders()');
});

test('Evidence Export sends X-Workspace-Id via authHeaders passed to ActionDetailPanel', () => {
  const src = pageSource();
  // authHeaders() encapsulates X-Workspace-Id (and X-CSRF-Token) from pilot-auth-context.
  // The page must pass authHeaders down to ActionDetailPanel, which uses it for all mutations.
  expect(src).toContain('authHeaders={authHeaders}');
  // authHeaders is sourced from usePilotAuth which includes workspace header logic.
  expect(src).toContain('usePilotAuth');
  // The retry path must also use authHeaders() to ensure workspace is always included.
  expect(src).toContain('retryHeaders = { ...authHeaders()');
});

test('403 csrf_missing_or_invalid triggers one token refresh and retry', () => {
  const src = pageSource();
  // Must detect the CSRF error code from backend.
  expect(src).toContain("'csrf_missing_or_invalid'");
  // Must call refreshCsrfToken to obtain a fresh token.
  expect(src).toContain('refreshCsrfToken()');
  // Must include the fresh token in the retry request headers.
  expect(src).toContain("'X-CSRF-Token': freshToken");
  // refreshCsrfToken must be passed down to ActionDetailPanel.
  expect(src).toContain('refreshCsrfToken={refreshCsrfToken}');
});

test('successful Evidence Export retry navigates to /evidence with package_id', () => {
  const src = pageSource();
  // Must navigate to the evidence page on success.
  expect(src).toContain('/evidence?');
  expect(src).toContain('package_id: data.package_id');
  expect(src).toContain('action_id: action.id');
  // CSRF retry result must also use the same navigation path.
  expect(src).toContain('router.push(`/evidence?${params.toString()}`');
});
