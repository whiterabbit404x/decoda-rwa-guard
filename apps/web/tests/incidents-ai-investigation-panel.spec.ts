import { readFileSync } from 'node:fs';
import path from 'node:path';

import { expect, test } from '@playwright/test';

// Static-source assertions (the repo's established frontend test style — no running
// server) that pin the AI Investigation panel into the reachable Incidents UI. Both
// Screen 7 navigation controls — the table's "View Incident" and the Case File's
// "Open Full Investigation" — route to /incidents/[incidentId], and the panel is a tab
// of that route's case record, where it has the width its citations and lifecycle need.
const appRoot = path.join(process.cwd(), 'apps/web/app');

function read(relativePath: string) {
  return readFileSync(path.join(appRoot, relativePath), 'utf8');
}

test('AI Investigation panel is wired into the full investigation case record', async () => {
  const tabs = read('incident-case-file-tabs.tsx');

  // The merged AI Investigation component is imported and rendered in the case record.
  expect(tabs).toContain("import AiInvestigationPanel from './ai-investigation-panel'");
  expect(tabs).toContain('<AiInvestigationPanel incidentId={incidentId} />');
  // Rendered for the dedicated tab (not double-rendered elsewhere), so its /ai-triage
  // lifecycle does not start until an operator opens it.
  expect(tabs).toContain("activeTab === 'ai-investigation'");

  // The case record exposes an "AI Investigation" tab alongside the existing tabs.
  expect(tabs).toContain("{ key: 'ai-investigation', label: 'AI Investigation' }");

  // It is reachable from the incident queue in one click: the Case File's primary CTA
  // routes to the same canonical /incidents/{id} detail route.
  const panel = read('incidents-panel.tsx');
  expect(panel).toContain('Open Full Investigation');
  expect(panel).toContain('href={`/incidents/${encodeURIComponent(incident.id)}`}');
});

test('existing Overview/Timeline/Alerts/Evidence/Response Actions tabs are preserved', async () => {
  const tabs = read('incident-case-file-tabs.tsx');
  expect(tabs).toContain("{ key: 'overview',         label: 'Overview' }");
  expect(tabs).toContain("{ key: 'timeline',         label: 'Timeline' }");
  expect(tabs).toContain("{ key: 'alerts',           label: 'Alerts' }");
  expect(tabs).toContain("{ key: 'evidence',         label: 'Evidence' }");
  expect(tabs).toContain("{ key: 'response-actions', label: 'Response Actions' }");
  // Their tab bodies still render.
  expect(tabs).toContain("activeTab === 'overview'");
  expect(tabs).toContain("activeTab === 'timeline'");
  expect(tabs).toContain("activeTab === 'alerts'");
  expect(tabs).toContain("activeTab === 'evidence'");
  expect(tabs).toContain("activeTab === 'response-actions'");
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

test('regeneration uses an in-app modal, not a raw browser prompt()', async () => {
  const panel = read('ai-investigation-panel.tsx');

  // The browser prompt()/alert() flow is removed.
  expect(panel).not.toContain('window.prompt');
  expect(panel).not.toContain('window.alert');

  // An accessible in-app modal drives regeneration with a required reason field,
  // an in-app validation message, and Cancel / Regenerate controls.
  expect(panel).toContain('role="dialog"');
  expect(panel).toContain('aria-modal="true"');
  expect(panel).toContain('id="regen-reason"');
  expect(panel).toContain('A reason is required to regenerate the analysis.');
  expect(panel).toContain('onClick={closeRegenerate}');
  expect(panel).toContain('onClick={submitRegenerate}');
  // The reason is sent to the regenerate endpoint.
  expect(panel).toContain('/ai-triage/regenerate');
});

test('a mock/simulated run is labelled truthfully in the UI', async () => {
  const panel = read('ai-investigation-panel.tsx');
  // The panel surfaces the synthetic marker so a mock run is never shown as a real
  // model call, and prior analysis versions are indicated after regeneration.
  expect(panel).toContain('Simulated (mock)');
  expect(panel).toContain('state?.simulated');
  expect(panel).toContain('prior versions preserved');
});

test('mock provider/model/cost are displayed truthfully (Mock / Mock / $0.00 / not billed)', async () => {
  const panel = read('ai-investigation-panel.tsx');
  // Provider + model render through truthful formatters: a simulated run reads "Mock",
  // never a live model name (e.g. gpt-5.6-luna).
  expect(panel).toContain('formatProviderLabel(state?.provider)');
  expect(panel).toContain('formatModelLabel(state?.simulated, state?.model)');
  expect(panel).toContain("if (simulated) return 'Mock';");
  expect(panel).toContain("if (p === 'mock') return 'Mock';");
  // Cost is always formatted to two decimals -> "$0.00" for a mock run (never $0.02862).
  expect(panel).toContain('Number(state?.estimated_cost_usd ?? 0).toFixed(2)');
  // Explicit synthetic / not-billed label for a simulated run.
  expect(panel).toContain('Synthetic test result — not billed');
});

test('citations link to their telemetry/evidence record and missing evidence is shown', async () => {
  const panel = read('ai-investigation-panel.tsx');
  // Evidence citations render their ref (e.g. telemetry:<id>) so a citation links to
  // the concrete evidence record it grounds.
  expect(panel).toContain('Evidence citations');
  expect(panel).toContain('renderRefs');
  expect(panel).toContain('c.ref');
  // Missing information (evidence incomplete) is surfaced rather than hidden.
  expect(panel).toContain('Missing information');
  expect(panel).toContain('result.missing_information');
  // A validation failure renders its own branch and is never shown as a completed result.
  expect(panel).toContain("state.status === 'validation_failed'");
  expect(panel).toContain("['completed', 'completed_with_warnings'].includes(state?.status ?? '')");
});

test('incident detail route mounts the standalone AI panel exactly once', async () => {
  const detail = read('(product)/incidents/[incidentId]/page.tsx');
  // The detail route is the standalone forensic experience — the incidents list no longer
  // renders here (Screen 7 composition fix), and the page itself does not mount the AI
  // triage panel: the case record's own tab does, so it can never double-render.
  expect(detail).toContain('<ForensicInvestigatorPanel incidentId={incidentId} />');
  expect(detail).not.toContain('<IncidentsPanel');
  expect(detail).not.toContain('<AiInvestigationPanel');
  const tabs = read('incident-case-file-tabs.tsx');
  expect(tabs.split('<AiInvestigationPanel').length - 1).toBe(1);
  expect(tabs).toContain("activeTab === 'ai-investigation' && <AiInvestigationPanel");
  // The forensic hero renders the deterministic AI Investigation Summary from the
  // /investigation payload; the triage panel polls /ai-triage. Different lifecycles,
  // one mount each — the case record never re-fetches /investigation.
  expect(tabs).not.toContain('/investigation`');
  // The Case File beside the incident queue no longer mounts it at all.
  const panel = read('incidents-panel.tsx');
  expect(panel).not.toContain('AiInvestigationPanel');
});
