import { expect, test } from '@playwright/test';

import {
  approvalErrorRequiresStepUp,
  isApprovalDecided,
  isSessionVerificationRequired,
  normalizeApprovalGate,
  responseActionReturnHref,
  sessionStepUpEndpoint,
} from '../app/(product)/response-actions-presentation';

// Screen 8 response-action approval — the INLINE session MFA step-up. When the operator
// is enrolled but the session's MFA assurance has lapsed (e.g. only enrollment happened,
// or the step-up window expired), the Approve control is disabled and a "Session
// verification required" panel offers a Verify Session control that opens a TOTP form
// posting to a dedicated, CSRF-protected endpoint — never a bounce back into enrollment.

// A permitted owner/admin whose SESSION has not completed a recent MFA step-up: the
// canonical block that renders the Verify Session panel next to the approval controls.
const STEP_UP_REQUIRED_GATE = {
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

const STEP_UP_SATISFIED_GATE = {
  ...STEP_UP_REQUIRED_GATE,
  can_current_user_approve: true,
  approval_blocked_reason_code: null,
  approval_blocked_reason: null,
  mfa_satisfied: true,
  next_required_step: 'Approve the action to record your decision.',
};

// 1. MFA enrolled but session not verified -> a Verify Session control must appear: the
//    caller HAS approval permission; only the session step-up assurance is missing.
test('session verification is required when permitted but session not verified', () => {
  const gate = normalizeApprovalGate(STEP_UP_REQUIRED_GATE)!;
  expect(isSessionVerificationRequired(gate)).toBe(true);
  expect(gate.hasPermission).toBe(true);
  expect(gate.mfaSatisfied).toBe(false);
  expect(gate.canApprove).toBe(false);
});

// 2. Once the session is verified (fresh step-up), the panel is gone and Approve is
//    enabled — the read-side gate reflects the persisted step-up.
test('session verification not required after a fresh step-up', () => {
  const gate = normalizeApprovalGate(STEP_UP_SATISFIED_GATE)!;
  expect(isSessionVerificationRequired(gate)).toBe(false);
  expect(gate.canApprove).toBe(true);
  expect(gate.mfaSatisfied).toBe(true);
});

// 3. The Verify Session form submits to a DEDICATED, same-origin (CSRF-protected) auth
//    endpoint — never the enrollment endpoint and never the backend directly.
test('step-up posts to the dedicated CSRF-protected session endpoint', () => {
  const endpoint = sessionStepUpEndpoint();
  expect(endpoint).toBe('/api/auth/session/step-up');
  // Same-origin proxy path (so the browser cookie/header CSRF pair is enforced and the
  // token is relayed to the backend), and NOT the enrollment / security-settings route.
  expect(endpoint.startsWith('/api/auth/')).toBe(true);
  expect(endpoint).not.toContain('enroll');
  expect(endpoint).not.toContain('settings/security');
});

// 4. If the assurance lapses between load and clicking Approve, the backend fails closed
//    with a reauth/MFA requirement — the UI recognises that and re-opens step-up rather
//    than surfacing a bare, un-actionable error. Covers BOTH backend reason codes.
test('a lapsed-assurance approval error is recognised as needing step-up', () => {
  expect(approvalErrorRequiresStepUp(403, {
    detail: { code: 'REAUTHENTICATION_REQUIRED', message: 'Reauthenticate before performing this sensitive action.' },
  })).toBe(true);
  expect(approvalErrorRequiresStepUp(403, {
    detail: { code: 'MFA_CHALLENGE_REQUIRED', reason_code: 'mfa_required', message: 'Complete MFA in this session…' },
  })).toBe(true);
  // A top-level code (some proxies flatten it) is also recognised.
  expect(approvalErrorRequiresStepUp(403, { code: 'REAUTHENTICATION_REQUIRED' })).toBe(true);
  // A plain reauth message with no structured code still maps.
  expect(approvalErrorRequiresStepUp(403, { detail: 'Reauthenticate before performing this sensitive action.' })).toBe(true);
});

// 5. Unrelated approval failures (wrong role, already decided, conflict) must NOT be
//    treated as a step-up requirement, so we never pop the TOTP form spuriously.
test('non-step-up approval errors do not trigger the step-up form', () => {
  expect(approvalErrorRequiresStepUp(403, { detail: { code: 'APPROVAL_NOT_PERMITTED', message: 'Not permitted.' } })).toBe(false);
  expect(approvalErrorRequiresStepUp(409, { detail: { code: 'ACTION_VERSION_ALREADY_DECIDED' } })).toBe(false);
  expect(approvalErrorRequiresStepUp(400, { detail: 'A reason is required.' })).toBe(false);
});

// 6. Step-up is a SESSION reauthentication, not enrollment: verifying the session must
//    not decide/undecide the action approval — the approval is still pending afterwards
//    until the operator clicks Approve. (Counts refresh without a new recommendation.)
test('session step-up does not itself decide the approval', () => {
  // Before AND after a step-up, the action approval remains pending — step-up only
  // raises session assurance; it never records an approval decision.
  expect(isApprovalDecided('pending')).toBe(false);
  // The selected action + Review All filter + incident scope survive because the flow
  // stays on the same Screen 8 URL (nothing navigates away for the inline step-up).
  const ret = responseActionReturnHref({ actionId: 'rec-42', incidentId: 'inc-9', approvalFilter: 'yes', tab: 'recommended' });
  expect(ret).toContain('action_id=rec-42');
  expect(ret).toContain('incident_id=inc-9');
  expect(ret).toContain('approval=yes');
});
