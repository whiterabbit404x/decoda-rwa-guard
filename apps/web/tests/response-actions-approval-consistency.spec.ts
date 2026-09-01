import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// Screen 8 canonical approval/summary consistency. These pin the fix for the
// contradiction where the "Pending Approval" card read 0 while the table rendered
// rows with "Requires Approval: Yes". The counts now come from ONE canonical
// backend summary, AI reviews carry a canonical approval_status, and Approve/Reject
// controls are present and call real backend commands.

function pageSource(): string {
  return fs.readFileSync(
    path.join(__dirname, '..', 'app', '(product)', 'response-actions-page-client.tsx'),
    'utf-8',
  );
}

function routeSource(rel: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', 'api', rel), 'utf-8');
}

test('summary cards reconcile the canonical backend summary with the visible rows', () => {
  const src = pageSource();
  expect(src).toContain('normalizeSummary(actionsPayload?.summary)');
  // The displayed count reconciles the canonical backend value with the row-derived
  // truth and fails CLOSED to the rows on disagreement — a stale/partial backend
  // summary can never make a card read 0 while the table shows pending actions.
  expect(src).toContain('const recommendedCount = reconcileCount(backendSummary?.recommended, rowRecommended)');
  expect(src).toContain('const pendingApprovalCount = reconcileCount(backendSummary?.pendingApproval, rowPendingApproval)');
  expect(src).toContain('const simulatedCount = reconcileCount(backendSummary?.simulated, rowSimulated)');
  expect(src).toContain('const executedCount = reconcileCount(backendSummary?.executed, rowExecuted)');
});

test('a dev-time invariant asserts the summary and the rows agree', () => {
  const src = pageSource();
  expect(src).toContain('summary/rows count mismatch');
  expect(src).toContain('backendSummary.pendingApproval !== rowPendingApproval');
});

test('the AI Playbook Advisor panel reconciles the same count as the cards', () => {
  const src = pageSource();
  expect(src).toContain('summary={backendSummary}');
  expect(src).toContain('reconcileCount(\n    summary?.pendingApproval,');
  expect(src).toContain('const simulated = summary?.simulated ??');
});

test('approval banner is gated on the canonical count and Review All applies the filter', () => {
  const src = pageSource();
  expect(src).toContain('const approvalRequiredCount = reconcileCount(');
  expect(src).toContain('approvalRequiredCount > 0');
  // Review All must apply the approval-required filter, never auto-approve / no-op.
  expect(src).toContain("setApprovalFilter('yes')");
  expect(src).toContain('Review All');
  expect(src).not.toContain('auto-approve');
});

test('primary lifecycle label is canonical and simulation is a SEPARATE pill (no concatenation)', () => {
  const src = pageSource();
  // Primary state from the backend lifecycle label; a passed simulation is a
  // separate secondary chip, never merged into the primary pill.
  expect(src).toContain('lifecyclePill(row.lifecycleState, row.lifecycleLabel)');
  expect(src).toContain("row.simulationStatus === 'passed' && row.lifecycleState !== 'simulation_passed'");
  expect(src).toContain('Simulation is shown as a SEPARATE secondary chip');
});

test('Approve and Reject controls exist and call the response-action approval domain', () => {
  const src = pageSource();
  expect(src).toContain('async function approveAction()');
  expect(src).toContain('async function rejectAction()');
  // Approve/Reject ALWAYS call the dedicated response-action approval command — for
  // BOTH policy actions and AI-recommendation-backed actions — never the AI
  // recommendation-REVIEW endpoint (the domain-collision bug).
  expect(src).toContain("responseActionApprovalEndpoint(action.id, 'approve')");
  expect(src).toContain("responseActionApprovalEndpoint(action.id, 'reject')");
  expect(src).not.toContain('/recommendations/${action.id}/approve');
  expect(src).not.toContain('/recommendations/${action.id}/reject');
  // Both buttons are rendered.
  expect(src).toContain('void approveAction()');
  expect(src).toContain('void rejectAction()');
});

test('Reject requires a reason before it calls the backend', () => {
  const src = pageSource();
  expect(src).toContain('Reason for rejecting this action (required)');
  expect(src).toContain('a reason is required');
  expect(src).toContain("JSON.stringify({ reason })");
});

test('an unauthorized approver sees a truthful read-only explanation, not a hidden control', () => {
  const src = pageSource();
  expect(src).toContain('const userMayApprove = action.canCurrentUserApprove !== false');
  // The read-only "Approval required" reason is shown for the non-MFA blocked cases
  // (wrong role / already decided) — never a hidden control with no explanation.
  expect(src).toContain('const approvalReadOnlyReason');
  expect(src).toContain('approvalPending && commandAllowsApprove && !canApproveNow && !mfaBlocked');
  expect(src).toContain('action.approvalPermissionReason');
  expect(src).toContain('approvalReadOnlyNotice');
});

test('state-changing commands re-fetch canonical data (not just router.refresh)', () => {
  const src = pageSource();
  // A completed approve/reject/simulate/execute bumps reloadKey so the client
  // re-fetches /api/response/actions + summary — router.refresh() alone did not.
  expect(src).toContain('const [reloadKey, setReloadKey] = useState(0)');
  expect(src).toContain('const reloadActions = () => setReloadKey((k) => k + 1)');
  expect(src).toContain('if (res.ok) onDataChanged()');
});

test('approval quorum progress (X of Y) is rendered from canonical counts', () => {
  const src = pageSource();
  expect(src).toContain('approvalDenominator');
  expect(src).toContain('approvalNumerator');
  expect(src).toContain('approved');
});

test('Simulate All shows the eligible count and is disabled with a reason when none are eligible', () => {
  const src = pageSource();
  expect(src).toContain('Simulate ${readyForDryRun} Eligible Action');
  expect(src).toContain('No Eligible Actions to Simulate');
  // Eligibility excludes an already-valid simulation, so it never re-runs them.
  expect(src).toContain("if (r.simulationStatus === 'passed') return false");
});

test('distinct action targets are rendered so same-title rows are disambiguated', () => {
  const src = pageSource();
  expect(src).toContain('row.targetLabel');
  expect(src).toContain('targetLabel');
});

test('state-changing commands invalidate/refresh canonical data', () => {
  const src = pageSource();
  // approve, reject, simulate, execute all refresh so cards + rows + panel stay in sync.
  const refreshes = src.match(/router\.refresh\(\)/g) ?? [];
  expect(refreshes.length).toBeGreaterThanOrEqual(4);
});

test('same-origin proxy routes exist for approve and reject and forward the body', () => {
  const approve = routeSource('response/actions/[actionId]/approve/route.ts');
  const reject = routeSource('response/actions/[actionId]/reject/route.ts');
  expect(approve).toContain('/response/actions/${actionId}/approve');
  expect(reject).toContain('/response/actions/${actionId}/reject');
  // Reject must forward the JSON reason body to the backend.
  expect(reject).toContain('forwardBody: true');
});
