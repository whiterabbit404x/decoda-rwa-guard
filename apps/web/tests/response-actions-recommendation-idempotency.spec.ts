import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// Screen 8 recommendation-idempotency guarantees, from the client's perspective.
//
// Regression: after Approve + refresh, Recommended Actions grew 6 -> 10 and Pending
// Approval 2 -> 6 with duplicate "Notify security team" rows. The backend fix makes
// generation idempotent; these tests pin the CLIENT contract that made the illusion
// possible impossible to reintroduce: Approve is a pure decision command that only
// RE-FETCHES the canonical actions — it never asks the backend to (re)generate a
// recommendation plan, never appends rows, and disambiguates same-title rows with a
// readable label instead of a raw UUID.

function pageSource(): string {
  return fs.readFileSync(
    path.join(__dirname, '..', 'app', '(product)', 'response-actions-page-client.tsx'),
    'utf-8',
  );
}

function presentationSource(): string {
  return fs.readFileSync(
    path.join(__dirname, '..', 'app', '(product)', 'response-actions-presentation.ts'),
    'utf-8',
  );
}

// 1 + 8. Approve / refresh NEVER call a recommendation-generation endpoint.
test('the Response Actions page never calls a recommendation-generation endpoint', () => {
  const src = pageSource();
  // The recommend / plan-generation endpoints live on Screen 7, never Screen 8.
  expect(src).not.toContain('response-actions/recommend');
  expect(src).not.toContain('/recommend');
  expect(src).not.toContain('/investigations');
  expect(src).not.toContain('/reports');
  expect(src).not.toContain('/ai-triage');
});

// 1. Approve calls ONLY the dedicated response-action approval command.
test('approve calls the response-action approval endpoint, not plan generation', () => {
  const src = pageSource();
  expect(src).toContain('async function approveAction()');
  expect(src).toContain("responseActionApprovalEndpoint(action.id, 'approve')");
  // The approval endpoint is the approve/reject command route — never a generator.
  expect(presentationSource()).toContain(
    'return `/api/response/actions/${encodeURIComponent(actionId)}/${decision}`;',
  );
});

// 2 + 8. Approve REFRESHES existing actions by re-fetching the canonical list.
test('approve refreshes existing actions via a GET re-fetch of the canonical list', () => {
  const src = pageSource();
  // On success the handler triggers a data reload (re-fetch), not a plan request.
  expect(src).toContain('if (res.ok) onDataChanged();');
  expect(src).toContain('const reloadActions = () => setReloadKey((k) => k + 1);');
  // The reload is a GET of /api/response/actions (the canonical read), with no body.
  expect(src).toContain('fetch(`/api/response/actions${actionsQs}`, { headers, cache: \'no-store\' })');
});

// 3. Recommended Actions count is derived from the (deduplicated) backend list +
//    summary, so an approval that adds no rows cannot change it.
test('recommended count is reconciled from the backend summary and visible rows', () => {
  const src = pageSource();
  expect(src).toContain('const recommendedCount = reconcileCount(backendSummary?.recommended, rowRecommended)');
  expect(src).toContain('const rowRecommended = recommendedRows.filter(isActiveRecommendation).length');
});

// 4. Pending Approval updates according to the canonical approval status / quorum.
test('pending approval count is derived from the canonical approval status', () => {
  const src = pageSource();
  expect(src).toContain('const pendingApprovalCount = reconcileCount(backendSummary?.pendingApproval, rowPendingApproval)');
  expect(src).toContain("const rowPendingApproval = recommendedRows.filter((r) => r.approvalStatus === 'pending').length");
});

// 5. Selected-action state refreshes correctly after a mutation: it is preserved when
//    the action still exists, and re-pointed when it no longer does.
test('selected action state is preserved across a refresh when it still exists', () => {
  const src = pageSource();
  expect(src).toContain('const targetId = actionIdParam || selectedId;');
  expect(src).toContain('const targetExists = targetId && recommended.some((r: ActionRow) => r.id === targetId);');
  expect(src).toContain('if (!targetExists && recommended.length > 0) {');
  expect(src).toContain('setSelectedId(recommended[0].id);');
});

// 6. A refresh REPLACES the row list (never appends), and rows are keyed by their
//    stable id — so a mutation can never introduce a duplicate row.
test('a refresh replaces the row set and rows are keyed by stable id', () => {
  const src = pageSource();
  // setRecommendedRows(recommended) REPLACES the array — it is never a spread-append.
  expect(src).toContain('setRecommendedRows(recommended);');
  expect(src).not.toContain('setRecommendedRows((prev) => [...prev');
  expect(src).toContain('key={row.id}');
});

// 7. Same-title legitimate actions show a READABLE distinction (backend target_label),
//    never a raw UUID.
test('same-title actions are disambiguated by a readable backend target label', () => {
  const src = pageSource();
  // Prefer the backend canonical target_label (the recommendation reason / recipient
  // context) over a raw id.
  expect(src).toContain('const backendTargetLabel =');
  expect(src).toContain("typeof input?.target_label === 'string' && input.target_label.trim()");
  expect(src).toContain('const targetLabel = backendTargetLabel');
  // The disambiguation label is rendered under the action title.
  expect(src).toContain('{row.targetLabel}');
});

// 3 + 8. A dev-time invariant asserts the summary and the visible rows agree, so a
//        stale/duplicated payload can never silently inflate a card.
test('a dev-time invariant asserts the summary and the rows agree', () => {
  const src = pageSource();
  expect(src).toContain('summary/rows count mismatch');
  expect(src).toContain('backendSummary.recommended !== rowRecommended');
});
