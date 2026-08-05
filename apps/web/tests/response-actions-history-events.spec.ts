import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import {
  actionHistoryEventKind,
  approvalHistoryPresentation,
  compareHistoryRecency,
  isApprovalHistoryEvent,
} from '../app/(product)/response-actions-presentation';

// Action History AUDIT-event presentation (Screen 8). These pin the fix for the
// reported bug: after a successful approval the Action History tab showed only the
// OLDER AI recommendation-REVIEW row (Decision: Accepted, dated at review time), with
// no clearly-identifiable "Response Action Approved" event. The invariants below make
// the two domains — recommendation REVIEW vs response-action APPROVAL — distinct,
// ordered newest-first, and impossible to reconflate without a failing test.

function pageSource(): string {
  return fs.readFileSync(
    path.join(__dirname, '..', 'app', '(product)', 'response-actions-page-client.tsx'),
    'utf-8',
  );
}

const APPROVED_ROW = {
  id: 'audit-approve-1',
  action_type: 'response_action.approved',
  object_type: 'response_action',
  timestamp: '2026-08-05T10:15:00Z',
  details_json: {
    event_type: 'response_action_approved',
    display_label: 'Response Action Approved',
    type_label: 'Action Approval',
    action_id: 'rec-1',
    action_title: 'Increase monitoring',
    incident_id: 'inc-1',
    actor: 'approver@example.com',
    decision: 'approved',
    previous_lifecycle: 'Awaiting Approval',
    new_lifecycle: 'Approved',
    approved_count: 1,
    required_quorum: 1,
    approval_progress_label: '1 of 1',
    policy: 'owner_admin_single_approver',
    result_summary: 'Success',
  },
};

// 1. Classification trusts the backend canonical event_type first.
test('actionHistoryEventKind classifies approval events by canonical event_type', () => {
  expect(actionHistoryEventKind(APPROVED_ROW)).toBe('response_action_approved');
  expect(
    actionHistoryEventKind({ action_type: 'response_action.rejected', details_json: { event_type: 'response_action_rejected' } }),
  ).toBe('response_action_rejected');
  expect(
    actionHistoryEventKind({ action_type: 'x', details_json: { event_type: 'response_action_approval_blocked' } }),
  ).toBe('response_action_approval_blocked');
});

// 2. Legacy rows (no enriched details) still classify via the raw action_type enum.
test('actionHistoryEventKind falls back to the raw action_type for legacy rows', () => {
  expect(actionHistoryEventKind({ action_type: 'response_action.approved' })).toBe('response_action_approved');
  expect(actionHistoryEventKind({ action_type: 'response_action.approval_attempt_blocked' })).toBe(
    'response_action_approval_blocked',
  );
  // A non-approval audit row is "other" and never treated as an approval event.
  expect(actionHistoryEventKind({ action_type: 'incident.timeline_note_added' })).toBe('other');
});

// 3. A recommendation REVIEW is NEVER an approval event (domain separation).
test('a recommendation review row is not an approval history event', () => {
  const reviewRow = {
    record_type: 'ai_recommendation_review',
    review_state: 'accepted',
    decision: 'accepted',
    reviewed_at: '2026-07-14T09:00:00Z',
  } as any;
  expect(isApprovalHistoryEvent(reviewRow)).toBe(false);
  expect(actionHistoryEventKind(reviewRow)).toBe('other');
});

// 4. A rich approved event maps to a truthful, self-describing "Action Approval".
test('approvalHistoryPresentation maps a rich approved event', () => {
  const p = approvalHistoryPresentation(APPROVED_ROW);
  expect(p.kind).toBe('response_action_approved');
  expect(p.label).toBe('Response Action Approved');
  expect(p.typeLabel).toBe('Action Approval');
  expect(p.result).toBe('Success');
  expect(p.decision).toBe('approved');
  expect(p.decisionLabel).toBe('Approved');
  expect(p.previousLifecycle).toBe('Awaiting Approval');
  expect(p.newLifecycle).toBe('Approved');
  expect(p.progressLabel).toBe('1 of 1');
  expect(p.actor).toBe('approver@example.com');
  // It must NOT read as an AI recommendation review.
  expect(p.typeLabel.toLowerCase()).not.toContain('recommendation');
  expect(p.decisionLabel).not.toBe('Accepted');
});

// 5. A legacy sparse approved row still renders the canonical label/type + result.
test('approvalHistoryPresentation degrades gracefully for a legacy sparse row', () => {
  const p = approvalHistoryPresentation({ action_type: 'response_action.approved' });
  expect(p.label).toBe('Response Action Approved');
  expect(p.typeLabel).toBe('Action Approval');
  expect(p.result).toBe('Success');
});

// 6. A rejected event and a blocked attempt are their own truthful presentations.
test('approvalHistoryPresentation maps rejected + blocked events distinctly', () => {
  const rejected = approvalHistoryPresentation({
    action_type: 'response_action.rejected',
    details_json: { event_type: 'response_action_rejected', display_label: 'Response Action Rejected', decision: 'rejected', result_summary: 'Success' },
  });
  expect(rejected.label).toBe('Response Action Rejected');
  expect(rejected.decisionLabel).toBe('Rejected');
  expect(rejected.result).toBe('Success');

  const blocked = approvalHistoryPresentation({
    action_type: 'response_action.approval_attempt_blocked',
    details_json: { event_type: 'response_action_approval_blocked', display_label: 'Approval Attempt Blocked', result_summary: 'MFA Required' },
  });
  expect(blocked.label).toBe('Approval Attempt Blocked');
  expect(blocked.result).toBe('MFA Required');
});

// 7. Newest-first ordering: the just-completed approval (Aug 5) sorts ABOVE the older
//    recommendation review (Jul 14) — never below it.
test('compareHistoryRecency orders the newest event first', () => {
  const approval = { time: '2026-08-05T10:15:00Z' };
  const review = { time: '2026-07-14T09:00:00Z' };
  const sorted = [review, approval].sort(compareHistoryRecency);
  expect(sorted[0]).toBe(approval);
  expect(sorted[1]).toBe(review);
  // Rows with no time sort last (deterministic), never above a real event.
  const noTime = { time: null };
  expect([noTime, approval].sort(compareHistoryRecency)[0]).toBe(approval);
});

// 8. The page assembles ONE newest-first stream and keeps the review row's decision
//    in the REVIEW domain (review_state), never from approval_status.
test('page assembles history newest-first and keeps review/approval domains separate', () => {
  const src = pageSource();
  // Newest-first across both event streams.
  expect(src).toContain('.sort(compareHistoryRecency)');
  // Approval-domain audit events are rendered richly via the shared helpers.
  expect(src).toContain('isApprovalHistoryEvent(input)');
  expect(src).toContain('approvalHistoryPresentation(input)');
  // The review row's decision comes from review_state — NOT approval_status.
  expect(src).toContain("String(input?.review_state || input?.decision || '')");
  expect(src).not.toContain("approvalStatus === 'approved' || approvalStatus === 'rejected'");
  // Only GENUINE reviews (accepted/rejected) become review history rows.
  expect(src).toContain("row.decision === 'accepted' || row.decision === 'rejected'");
});

// 9. The Approve pill exists for a successful response-action approval event.
test('a successful approval renders an Approved decision pill', () => {
  const src = pageSource();
  expect(src).toContain('<StatusPill label="Approved" variant="success" />');
  // Previous -> new lifecycle and quorum progress are rendered for approval events.
  expect(src).toContain('row.previousLifecycle');
  expect(src).toContain('row.newLifecycle');
  expect(src).toContain('row.progressLabel');
});

// 10. The "Requires Approval" filter is applied ONLY to Recommended Actions and never
//     to Action History, so it can never hide an approval audit event.
test('the approval filter never applies to Action History rows', () => {
  const src = pageSource();
  // The recommended list honors approvalFilter…
  expect(src).toContain('matchesApproval');
  // …but the history filter depends ONLY on the free-text search.
  const historyFilter = src.slice(src.indexOf('const filteredHistory'));
  const historyBody = historyFilter.slice(0, historyFilter.indexOf('}, [historyRows'));
  expect(historyBody).toContain('historyRows.filter');
  expect(historyBody).not.toContain('approvalFilter');
  expect(historyBody).not.toContain('requiresApproval');
});

// 11. After a successful approval the Action History query is refreshed immediately
//     (onDataChanged bumps the reload key, re-fetching /history/actions).
test('history refreshes immediately after a successful approval', () => {
  const src = pageSource();
  // The approve handler re-fetches on success.
  expect(src).toContain('if (res.ok) onDataChanged();');
  // onDataChanged is wired to a reload key that re-runs the data load (incl. history).
  expect(src).toContain('const reloadActions = () => setReloadKey((k) => k + 1);');
  expect(src).toContain('reloadKey]');
  // History is loaded from the persisted audit endpoint, so it survives refresh.
  expect(src).toContain('/history/actions?limit=50');
});
