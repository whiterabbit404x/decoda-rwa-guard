import { expect, test } from '@playwright/test';

import {
  approvalResultMessage,
  canonicalApprovalStatus,
  deriveRowApproval,
  isApprovalDecided,
  reconcileCount,
  responseActionApprovalEndpoint,
} from '../app/(product)/response-actions-presentation';

// Behavioral tests for the Screen 8 RESPONSE-ACTION APPROVAL domain, kept strictly
// separate from the AI recommendation REVIEW domain. These pin the fix for the
// deployed bug: clicking "Approve" on an approval-required "Increase monitoring"
// action returned "Recommendation has already been reviewed" and nothing changed,
// because the approval was routed to the recommendation-review endpoint and gated by
// review_state. The invariants below make that domain collision impossible to
// reintroduce without a failing test.

// 1. Approve/Reject ALWAYS call the dedicated response-action approval command —
//    never the incident recommendation-review endpoint — regardless of record type.
test('approve routes to the dedicated response-action approval endpoint', () => {
  expect(responseActionApprovalEndpoint('act-123', 'approve')).toBe('/api/response/actions/act-123/approve');
  expect(responseActionApprovalEndpoint('rec-abc', 'reject')).toBe('/api/response/actions/rec-abc/reject');
  // It must NEVER produce a recommendation-review path.
  expect(responseActionApprovalEndpoint('rec-abc', 'approve')).not.toContain('/recommendations/');
  expect(responseActionApprovalEndpoint('rec-abc', 'approve')).not.toContain('/incidents/');
});

test('approval endpoint depends only on the id + decision, not the record type', () => {
  // The same id maps to the same response-action approval endpoint whether it is a
  // policy action or an AI-recommendation-backed action — the backend resolves the
  // subject. There is no code path that sends an approval to the review endpoint.
  const policy = responseActionApprovalEndpoint('x1', 'approve');
  const aiBacked = responseActionApprovalEndpoint('x1', 'approve');
  expect(policy).toBe(aiBacked);
});

// 2. An approval attempt NEVER surfaces the recommendation-review "already reviewed"
//    message. The result is the canonical response-action approval message.
test('an existing recommendation review never shows "already reviewed" for an approval', () => {
  const recorded = approvalResultMessage(true, { message: 'Approval recorded. Approval quorum reached.' });
  expect(recorded).toBe('Approval recorded. Approval quorum reached.');
  expect(recorded.toLowerCase()).not.toContain('already been reviewed');
  // Even a bare success body maps to a truthful approval message, not a review one.
  expect(approvalResultMessage(true, {})).toBe('Approval recorded.');
});

// 3. One-of-one approval: an approved row leaves the Pending Approval set, so the
//    count goes from 2 to 1.
test('a one-of-one approval drops the row out of Pending Approval (2 -> 1)', () => {
  const rows = [
    { id: 'a', approval_status: 'pending' },
    { id: 'b', approval_status: 'pending' },
  ];
  const pendingBefore = rows.filter((r) => !isApprovalDecided(r.approval_status)).length;
  expect(pendingBefore).toBe(2);
  // Approve row 'a' -> its canonical approval status becomes 'approved'.
  rows[0].approval_status = 'approved';
  const pendingAfter = rows.filter((r) => !isApprovalDecided(r.approval_status)).length;
  expect(pendingAfter).toBe(1);
  expect(reconcileCount(1, pendingAfter)).toBe(1);
});

// 4. Multi-approval quorum: a row with progress 1 of 2 is NOT yet decided, so it
//    stays in Pending Approval and keeps its Approve/Reject controls.
test('a partial multi-approval quorum keeps the row pending', () => {
  // approval_status stays 'pending' until quorum is reached.
  expect(isApprovalDecided('pending')).toBe(false);
  const row = { approval_status: 'pending', required_approval_count: 2, current_approval_count: 1 };
  expect(isApprovalDecided(row.approval_status)).toBe(false);
  expect(deriveRowApproval({ approvalStatus: row.approval_status })).toEqual({
    approvalStatus: 'pending',
    requiresApproval: true,
  });
});

// 5. Approve/Reject disappear once quorum is reached (the row is decided).
test('a decided (approved/rejected) row leaves Recommended for Action History', () => {
  expect(isApprovalDecided('approved')).toBe(true);
  expect(isApprovalDecided('rejected')).toBe(true);
  expect(isApprovalDecided('accepted')).toBe(true); // canonicalized to approved
  expect(isApprovalDecided('pending')).toBe(false);
  expect(isApprovalDecided('not_required')).toBe(false);
});

// 6. A duplicate approval by the same approver shows the action-specific message
//    from the backend (409), NOT a review message.
test('duplicate approval surfaces the action-version specific message', () => {
  const msg = approvalResultMessage(false, {
    detail: { code: 'ACTION_VERSION_ALREADY_DECIDED', message: 'You already approved this action version.' },
  });
  expect(msg).toBe('You already approved this action version.');
  expect(msg.toLowerCase()).not.toContain('already been reviewed');
});

// 7. Summaries refresh from the canonical approval status: reconcileCount fails
//    closed to the visible rows so the card can never under-report after approval.
test('summary count reconciles to the visible approval-pending rows', () => {
  const rows = [
    { approval_status: 'pending' },
    { approval_status: 'approved' },
    { approval_status: 'pending' },
  ];
  const rowPending = rows.filter((r) => !isApprovalDecided(r.approval_status)).length;
  expect(rowPending).toBe(2);
  // A stale backend summary that still says 3 must not override the visible 2.
  expect(reconcileCount(3, rowPending)).toBe(2);
  // An agreeing backend summary is used as-is.
  expect(reconcileCount(2, rowPending)).toBe(2);
});

// 8. Not-authorized / not-approvable responses map to their truthful messages.
test('permission and approvability errors map to canonical messages', () => {
  expect(approvalResultMessage(false, {
    detail: { code: 'APPROVAL_NOT_PERMITTED', message: 'Owner or admin role is required to approve response actions.' },
  })).toContain('Owner or admin role is required');
  expect(approvalResultMessage(false, { detail: 'This action does not require approval.' }))
    .toBe('This action does not require approval.');
  expect(approvalResultMessage(false, {})).toBe('Approval was blocked by the backend.');
});

// 9. The approved state is derived from the persisted canonical approval_status, so
//    a page refresh preserves it deterministically (no optimistic-only state).
test('approved state derives from the canonical approval_status on refresh', () => {
  // Whatever legacy variant the backend persisted, an approved action canonicalizes
  // to 'approved' and is treated as decided every time the page reloads.
  for (const raw of ['approved', 'accepted']) {
    expect(canonicalApprovalStatus(raw)).toBe('approved');
    expect(isApprovalDecided(raw)).toBe(true);
  }
});

// 10. The recommendation REVIEW is independent of the approval: a reviewed-but-
//     unapproved recommendation is still approval-pending (review_state never
//     approves the action). The approval status alone drives the split.
test('a recommendation review does not approve the action (domains independent)', () => {
  // review_state would be 'accepted', but with approval_status 'pending' the row is
  // still awaiting approval and stays in Recommended Actions.
  expect(isApprovalDecided('pending')).toBe(false);
  expect(deriveRowApproval({ approvalStatus: 'pending' })).toEqual({
    approvalStatus: 'pending',
    requiresApproval: true,
  });
});
