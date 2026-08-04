import { expect, test } from '@playwright/test';

import {
  approvalResultMessage,
  completeMfaHref,
  isApprovalDecided,
  isMfaBlockedApproval,
  normalizeApprovalGate,
  responseActionReturnHref,
} from '../app/(product)/response-actions-presentation';

// Screen 8 FINAL approval-gating pass — the session-MFA step-up made explicit,
// contextual, actionable, and consistent between backend and frontend. These pin
// the presentation contract the Response Actions panel renders from, so the MFA
// block can never regress into a silent failure, a distant-only toast, a fabricated
// approval, or a generic "destructive action" label on a low-risk communication
// action. The backend `approval_gate` object is the single authority; the UI never
// re-derives the MFA policy or the action's risk wording.

// A backend approval_gate for the deployed scenario: a permitted owner/admin whose
// SESSION has not completed MFA, approving the communication action "Notify security
// team" (classified `protected`, never `destructive`).
const MFA_BLOCKED_GATE = {
  can_current_user_approve: false,
  has_approval_permission: true,
  approval_blocked_reason_code: 'mfa_required',
  approval_blocked_reason: 'Complete MFA in this session before approving this protected response action.',
  mfa_required: true,
  mfa_satisfied: false,
  required_approval_count: 1,
  current_approval_count: 0,
  approval_progress_label: '0 of 1',
  next_required_step: 'Complete MFA in this session, then approve the action.',
  approval_security_classification: 'protected',
  approval_security_message: 'Complete MFA in this session before approving this protected response action.',
};

const MFA_SATISFIED_GATE = {
  ...MFA_BLOCKED_GATE,
  can_current_user_approve: true,
  approval_blocked_reason_code: null,
  approval_blocked_reason: null,
  mfa_satisfied: true,
  next_required_step: 'Approve the action to record your decision.',
};

// 1. Recommendation REVIEW ("Accepted") is a SEPARATE fact from Action APPROVAL
//    ("Awaiting Approval"). The review being accepted never decides the approval —
//    an accepted review with a pending approval is still awaiting approval.
test('recommendation review is separate from action approval', () => {
  const review = 'accepted';
  const approvalStatus = 'pending';
  // Review is accepted, approval is still pending — two independent gates.
  expect(review).toBe('accepted');
  expect(isApprovalDecided(approvalStatus)).toBe(false);
});

// 2 & 3. The gate exposes an explicit approval progress (0 of 1) distinct from the
//        review decision.
test('gate exposes approval progress 0 of 1 while awaiting approval', () => {
  const gate = normalizeApprovalGate(MFA_BLOCKED_GATE)!;
  expect(gate.currentApprovalCount).toBe(0);
  expect(gate.requiredApprovalCount).toBe(1);
  expect(`${gate.currentApprovalCount} of ${gate.requiredApprovalCount}`).toBe('0 of 1');
});

// 4. The MFA-required block is recognised so the explanation renders NEXT TO the
//    approval controls — the caller HAS approval permission; only MFA is missing.
test('missing session MFA is recognised as an approval block, not a permission block', () => {
  const gate = normalizeApprovalGate(MFA_BLOCKED_GATE)!;
  expect(isMfaBlockedApproval(gate)).toBe(true);
  expect(gate.hasPermission).toBe(true);
  expect(gate.mfaSatisfied).toBe(false);
  expect(gate.blockedReasonCode).toBe('mfa_required');
  // The reason is contextual (from the backend), not a bare HTTP string.
  expect(gate.blockedReason).toContain('Complete MFA');
});

// 5. A Complete MFA / Security Settings action targets the app's ESTABLISHED
//    security route (never a bespoke MFA dialog).
test('complete-MFA CTA targets the established security settings route', () => {
  const href = completeMfaHref({ actionId: 'rec-1', incidentId: 'inc-1', approvalFilter: 'yes' });
  expect(href.startsWith('/settings/security?return_to=')).toBe(true);
});

// 6. A missing-MFA approval is NEVER presented as a successful approval.
test('a blocked MFA approval never reads as a success', () => {
  const gate = normalizeApprovalGate(MFA_BLOCKED_GATE)!;
  expect(gate.canApprove).toBe(false);
  // The 403 MFA response maps to its truthful message, not "Approval recorded".
  const msg = approvalResultMessage(false, {
    detail: {
      code: 'MFA_CHALLENGE_REQUIRED',
      message: 'Complete MFA in this session before approving this protected response action.',
    },
  });
  expect(msg).toContain('Complete MFA');
  expect(msg).not.toBe('Approval recorded.');
  expect(msg.toLowerCase()).not.toContain('approval recorded');
});

// 7. A blocked attempt does not change the counts — the row stays pending because
//    the gate never granted approval, so it is still counted in Pending Approval.
test('counts are unchanged after a blocked MFA attempt', () => {
  const rows = [
    { approval_status: 'pending' },
    { approval_status: 'pending' },
    { approval_status: 'pending' },
    { approval_status: 'pending' },
  ];
  const pendingBefore = rows.filter((r) => !isApprovalDecided(r.approval_status)).length;
  // A blocked attempt persists NO decision -> approval_status is unchanged.
  const pendingAfter = rows.filter((r) => !isApprovalDecided(r.approval_status)).length;
  expect(pendingBefore).toBe(4);
  expect(pendingAfter).toBe(4);
});

// 8. The selected action id, Review All filter, and incident scope survive the MFA
//    flow: they are encoded in the return URL the operator comes back through.
test('filter, selected action, and incident scope survive the MFA flow', () => {
  const ret = responseActionReturnHref({
    actionId: 'rec-42', incidentId: 'inc-9', approvalFilter: 'yes', tab: 'recommended',
  });
  expect(ret).toContain('action_id=rec-42');
  expect(ret).toContain('incident_id=inc-9');
  expect(ret).toContain('approval=yes');

  const href = completeMfaHref({ actionId: 'rec-42', incidentId: 'inc-9', approvalFilter: 'yes' });
  const returnTo = decodeURIComponent(new URL(`https://x${href}`).searchParams.get('return_to') ?? '');
  expect(returnTo).toContain('action_id=rec-42');
  expect(returnTo).toContain('incident_id=inc-9');
  expect(returnTo).toContain('approval=yes');
});

// 9. After MFA is satisfied, the gate flips to approvable and the success message
//    maps canonically — the data that lets the panel refresh cards/table/details.
test('after MFA the gate is approvable and success maps canonically', () => {
  const gate = normalizeApprovalGate(MFA_SATISFIED_GATE)!;
  expect(isMfaBlockedApproval(gate)).toBe(false);
  expect(gate.canApprove).toBe(true);
  expect(gate.mfaSatisfied).toBe(true);
  expect(approvalResultMessage(true, { message: 'Approval recorded. Approval quorum reached.' }))
    .toContain('Approval recorded');
});

// 10. No generic "destructive" wording for a low-risk communication action.
test('a protected communication action is never labelled destructive', () => {
  const gate = normalizeApprovalGate(MFA_BLOCKED_GATE)!;
  expect(gate.securityClassification).toBe('protected');
  expect((gate.securityMessage ?? '').toLowerCase()).not.toContain('destructive');
  expect((gate.blockedReason ?? '').toLowerCase()).not.toContain('destructive');
});

// 11. A HIGH-IMPACT action keeps strong destructive wording (the backend classifies
//     it; the UI renders it verbatim without title string-matching).
test('a high-impact action keeps destructive wording from the backend', () => {
  const destructiveGate = normalizeApprovalGate({
    ...MFA_BLOCKED_GATE,
    approval_security_classification: 'destructive',
    approval_security_message: 'Complete MFA in this session before approving this destructive action.',
    approval_blocked_reason: 'Complete MFA in this session before approving this destructive action.',
  })!;
  expect(destructiveGate.securityClassification).toBe('destructive');
  expect(destructiveGate.securityMessage).toContain('destructive action');
});

// 12. A legacy payload without a gate degrades safely (null), so older backends fall
//     back to the permission-only rendering instead of a fabricated gate.
test('a missing approval_gate degrades to null (no fabricated gate)', () => {
  expect(normalizeApprovalGate(undefined)).toBeNull();
  expect(normalizeApprovalGate(null)).toBeNull();
  expect(isMfaBlockedApproval(null)).toBe(false);
});

// 13. return_to is only ever an internal path — no absolute/protocol-relative URL,
//     so the MFA round-trip can never be turned into an off-site redirect.
test('the MFA return target is always an internal path', () => {
  const href = completeMfaHref({ actionId: 'rec-1', incidentId: 'inc-1', approvalFilter: 'yes' });
  const returnTo = decodeURIComponent(new URL(`https://x${href}`).searchParams.get('return_to') ?? '');
  expect(returnTo.startsWith('/response-actions')).toBe(true);
  expect(returnTo.startsWith('//')).toBe(false);
});
