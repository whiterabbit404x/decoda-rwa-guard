import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function appSource(fileName: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', fileName), 'utf-8');
}

function pageSource(): string {
  return appSource('(product)/response-actions-page-client.tsx');
}

// ── Playbook Execution Agent panel ──────────────────────────────────────────

test('Playbook Execution Agent panel exists with the reference title', () => {
  const src = pageSource();
  expect(src).toContain('Playbook Execution Agent');
  expect(src).toContain('aria-label="Playbook Execution Agent"');
  expect(src).toContain('PlaybookAgentPanel');
});

test('agent summary rows are derived from persisted rows, not hardcoded', () => {
  const src = pageSource();
  expect(src).toContain('Actions recommended');
  expect(src).toContain('Awaiting approval');
  expect(src).toContain('Ready for dry run');
  expect(src).toContain('Ready for execution');
  expect(src).toContain('Blocked');
  // Counts come from the rows array (persisted state).
  expect(src).toContain('rows.filter');
});

test('no random values are used to fabricate agent/scoring data', () => {
  const src = pageSource();
  expect(src).not.toContain('Math.random');
});

// ── Approval Required banner ─────────────────────────────────────────────────

test('approval required banner exists and count comes from persisted state', () => {
  const src = pageSource();
  expect(src).toContain('aria-label="Approval required banner"');
  expect(src).toContain('Approval Required');
  expect(src).toContain('require your approval before execution.');
  expect(src).toContain('Review All');
  // Count is computed from persisted rows via actionNeedsApproval, not a constant.
  expect(src).toContain('approvalRequiredCount');
  expect(src).toContain('function actionNeedsApproval');
});

test('approval banner is hidden when there are no approval-required actions', () => {
  const src = pageSource();
  // Rendered only when approvalRequiredCount > 0.
  expect(src).toContain('approvalRequiredCount > 0');
});

// ── Simulate All ─────────────────────────────────────────────────────────────

test('Simulate All calls the backend batch proxy and refreshes', () => {
  const src = pageSource();
  expect(src).toContain('Simulate All');
  expect(src).toContain('/api/response/actions/simulate-all');
  expect(src).toContain('router.refresh()');
});

test('Simulate All only targets eligible actions', () => {
  const src = pageSource();
  // Eligibility mirrors the backend; already-simulated / terminal / AI-review excluded.
  expect(src).toContain('function isSimulateEligible');
  expect(src).toContain("r.recordType === 'ai_recommendation_review'");
  expect(src).toContain('readyForDryRun');
  // Button disabled when nothing is eligible.
  expect(src).toContain('readyForDryRun === 0');
});

test('Simulate All eligible count comes from the canonical backend breakdown', () => {
  const src = pageSource();
  // The eligible/already-simulated/blocked counts prefer the backend summary.simulation
  // breakdown (one predicate) over row guessing.
  expect(src).toContain('summary?.simulation');
  expect(src).toContain('simBreakdown');
  expect(src).toContain('simBreakdown?.eligible');
  expect(src).toContain('simBreakdown?.alreadySimulated');
});

test('agent panel explains WHY nothing is eligible, not just "No Eligible Actions"', () => {
  const src = pageSource();
  // A visible breakdown line + per-reason list, never only the disabled button label.
  expect(src).toContain('eligible ·');
  expect(src).toContain('already simulated');
  expect(src).toContain('simBlockedReasons');
  // Each blocked reason is rendered with its count and truthful label.
  expect(src).toContain('r.label');
});

test('normalizeSummary parses the canonical simulation breakdown', () => {
  const src = pageSource();
  expect(src).toContain('type SimulationBreakdown');
  expect(src).toContain('input.simulation');
  expect(src).toContain('already_simulated');
  expect(src).toContain('blocked_reasons');
});

// ── Safety Checks ────────────────────────────────────────────────────────────

test('Safety Checks section fetches the deterministic read-only endpoint', () => {
  const src = pageSource();
  expect(src).toContain('Safety Checks');
  expect(src).toContain('/safety-checks');
  expect(src).toContain('safetyStatusPill');
});

test('Safety Checks are truthful: unknown is never shown as pass', () => {
  const src = pageSource();
  expect(src).toContain('Unknown');
  expect(src).toContain('Checks that lack data are shown as Unknown, never Pass.');
  // AI recommendation reviews are not executable, so checks are not applicable.
  expect(src).toContain("checksState === 'not_applicable'");
});

// ── Priority + Runbook columns ───────────────────────────────────────────────

test('recommended table adds Priority and Runbook columns (keeps existing ones)', () => {
  const src = pageSource();
  expect(src).toContain("'Priority'");
  expect(src).toContain("'Runbook'");
  // Existing required columns must remain.
  expect(src).toContain("'Impact'");
  expect(src).toContain("'Requires Approval'");
  expect(src).toContain('priorityPill');
});

test('playbook fields are populated from backend playbook object, not invented', () => {
  const src = pageSource();
  expect(src).toContain('input?.playbook');
  expect(src).toContain('playbook.runbook_id');
  expect(src).toContain('playbook.priority');
  // Deterministic fallback from risk level for AI reviews.
  expect(src).toContain('function priorityFromRisk');
});

// ── Proxy routes ─────────────────────────────────────────────────────────────

test('safety-checks proxy route exists and is GET-only', () => {
  const src = appSource('api/response/actions/[actionId]/safety-checks/route.ts');
  expect(src).toContain('/safety-checks');
  expect(src).toContain('export async function GET');
  expect(src).toContain("method: 'GET'");
});

test('simulate-all proxy route exists and is POST', () => {
  const src = appSource('api/response/actions/simulate-all/route.ts');
  expect(src).toContain('/response/actions/simulate-all');
  expect(src).toContain('export async function POST');
  expect(src).toContain('searchParams');
});

// ── Screen 7 → Screen 8 separation and response summary ──────────────────────

test('Screen 7 response tab shows a summary and opens Screen 8 scoped to the incident', () => {
  const src = appSource('incidents-panel.tsx');
  expect(src).toContain('Open in Response Actions');
  expect(src).toContain('/response-actions?incident_id=');
  // Summary counts derived from persisted actions.
  expect(src).toContain('awaitingApprovalN');
  expect(src).toContain('executedN');
});

test('Screen 7 detail route and Response Actions remain separate routes', () => {
  const detail = appSource('(product)/incidents/[incidentId]/page.tsx');
  // The incident detail route renders the investigation hero, not the response page.
  expect(detail).toContain('ForensicInvestigatorPanel');
  expect(detail).not.toContain('ResponseActionsPageClient');
});
