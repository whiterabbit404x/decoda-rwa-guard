import { readFileSync } from 'node:fs';
import path from 'node:path';

import { expect, test } from '@playwright/test';

// Static-source assertions (the repo's established frontend test style — no running
// server) that pin the AI Investigation panel into the reachable Incidents UI. The
// panel previously lived only on the standalone /incidents/[incidentId] route, which
// "View Incident" never navigates to; these guard the fix that surfaces it inside the
// case-file drawer that "View Incident" actually opens.
const appRoot = path.join(process.cwd(), 'apps/web/app');

function read(relativePath: string) {
  return readFileSync(path.join(appRoot, relativePath), 'utf8');
}

test('AI Investigation panel is wired into the incidents case-file drawer', async () => {
  const panel = read('incidents-panel.tsx');

  // The merged AI Investigation component is imported and rendered inside the drawer.
  expect(panel).toContain("import AiInvestigationPanel from './ai-investigation-panel'");
  expect(panel).toContain('<AiInvestigationPanel incidentId={incident.id} />');
  // Rendered for the dedicated tab (not double-rendered elsewhere).
  expect(panel).toContain("activeTab === 'ai-investigation'");

  // The drawer exposes an "AI Investigation" tab alongside the existing tabs.
  expect(panel).toContain("{ key: 'ai-investigation',  label: 'AI Investigation' }");
});

test('existing Overview/Timeline/Alerts/Evidence/Response Actions tabs are preserved', async () => {
  const panel = read('incidents-panel.tsx');
  expect(panel).toContain("{ key: 'overview',          label: 'Overview' }");
  expect(panel).toContain("{ key: 'timeline',          label: 'Timeline' }");
  expect(panel).toContain("{ key: 'alerts',            label: 'Alerts' }");
  expect(panel).toContain("{ key: 'evidence',          label: 'Evidence' }");
  expect(panel).toContain("{ key: 'response-actions',  label: 'Response Actions' }");
  // Their tab bodies still render.
  expect(panel).toContain("activeTab === 'overview'");
  expect(panel).toContain("activeTab === 'timeline'");
  expect(panel).toContain("activeTab === 'alerts'");
  expect(panel).toContain("activeTab === 'evidence'");
  expect(panel).toContain("activeTab === 'response-actions'");
});

test('Start AI Investigation button calls the authenticated per-incident ai-triage endpoint', async () => {
  const panel = read('ai-investigation-panel.tsx');

  // Visible primary button labelled exactly "Start AI Investigation".
  expect(panel).toContain('Start AI Investigation');

  // POSTs to the workspace-scoped, per-incident endpoint with auth headers.
  expect(panel).toContain('/incidents/${encodeURIComponent(incidentId)}/ai-triage');
  expect(panel).toContain("method: 'POST'");
  expect(panel).toContain('authHeaders()');

  // GET poll of the same endpoint drives the live state while a job is active.
  expect(panel).toContain('ACTIVE_STATES');
  expect(panel).toContain('setInterval');
});

test('AI panel renders all required triage lifecycle states', async () => {
  const panel = read('ai-investigation-panel.tsx');
  // Status labels for every state the task requires the UI to distinguish.
  expect(panel).toContain("disabled: 'AI triage disabled'");
  expect(panel).toContain("not_requested: 'Ready to analyze'");
  expect(panel).toContain("queued: 'Queued'");
  expect(panel).toContain("running: 'Investigating…'");
  expect(panel).toContain("completed: 'Completed'");
  expect(panel).toContain("validation_failed: 'Validation failed'");
  expect(panel).toContain("failed: 'Failed'");
  expect(panel).toContain("budget_blocked: 'Budget blocked'");
  // Migration-0123-not-applied fail-closed state.
  expect(panel).toContain("unavailable: 'Unavailable'");

  // Disabled + error + unavailable branches are all rendered.
  expect(panel).toContain("state?.status === 'disabled'");
  expect(panel).toContain("state?.status === 'unavailable'");
  expect(panel).toContain("['failed', 'validation_failed', 'budget_blocked'].includes(state.status)");
});

test('AI panel labels generated content and surfaces grounded citations', async () => {
  const panel = read('ai-investigation-panel.tsx');
  // Mandatory generated-content disclaimer.
  expect(panel).toContain('AI-generated analysis — verify before action.');
  // Grounded evidence citations are displayed.
  expect(panel).toContain('Evidence citations');
  expect(panel).toContain('c.ref');
  // Recommendations remain human-approved (approve/reject controls).
  expect(panel).toContain("review(r.recommendation_id, 'approve')");
  expect(panel).toContain("review(r.recommendation_id, 'reject')");
});

test('incident detail route renders the drawer (no duplicate standalone AI panel)', async () => {
  const detail = read('(product)/incidents/[incidentId]/page.tsx');
  expect(detail).toContain('<IncidentsPanel initialSelectedId={incidentId} />');
  // The standalone panel was removed to avoid double-rendering; the drawer tab covers it.
  expect(detail).not.toContain('<AiInvestigationPanel');
});
