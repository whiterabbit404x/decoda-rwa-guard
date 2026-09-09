import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import {
  ACCOUNT_CREATED_BODY,
  APPROVAL_ONLY_BODY,
  APPROVAL_ONLY_HEADLINE,
  REQUEST_PILOT_CTA,
  acceptInvitationPath,
  resolveInvitationToken,
} from '../app/signup-access';

// The /sign-up loophole, pinned.
//
// The backend has refused to provision a tenant for an unapproved account since
// the approval-only change (services/api/app/pilot.py). What remained was the
// PAGE: it collected "Workspace name" behind a "Create workspace" button, which
// told an unapproved visitor that signing up produces a Decoda workspace. It did
// not — and a product that says otherwise is lying about access in exactly the
// way this codebase refuses to lie about monitoring status.
//
// These run without a web server, like the other approval-only specs, so they
// stay reliable in CI.

const APP_DIR = path.join(__dirname, '..', 'app');

function read(...segments: string[]): string {
  return fs.readFileSync(path.join(APP_DIR, ...segments), 'utf-8');
}

const SIGN_UP_CLIENT = read('sign-up', 'sign-up-page-client.tsx');
const SIGN_UP_PAGE = read('sign-up', 'page.tsx');

// ── 1-2: the ungated page offers no workspace creation ──────────────────────

test('the ungated signup page states that access is approval-only', () => {
  expect(APPROVAL_ONLY_HEADLINE).toBe('Pilot access is approval-only');
  expect(SIGN_UP_CLIENT).toContain('APPROVAL_ONLY_HEADLINE');
  expect(SIGN_UP_CLIENT).toContain('APPROVAL_ONLY_BODY');
  // The body says what signing up does NOT do, rather than leaving it implied.
  expect(APPROVAL_ONLY_BODY).toContain('does not create a workspace');
  expect(APPROVAL_ONLY_BODY).toContain('start monitoring');
});

test('the page has no workspace-creation input or control at all', () => {
  // The field and the button that made the promise. Neither may come back:
  // whatever a visitor typed there named nothing the backend would create.
  expect(SIGN_UP_CLIENT).not.toContain('su-workspace-name');
  expect(SIGN_UP_CLIENT).not.toContain('WORKSPACE NAME');
  expect(SIGN_UP_CLIENT).not.toContain('Create workspace');
  expect(SIGN_UP_CLIENT).not.toContain('Creating workspace');
  expect(SIGN_UP_CLIENT).not.toContain('setWorkspaceName');
});

test('the ungated state renders no credential inputs, because it renders no form', () => {
  // Every input in the file lives in the invited branch. The approval-required
  // branch is copy plus two links, so there is nothing to submit.
  const gateBranch = SIGN_UP_CLIENT.slice(
    SIGN_UP_CLIENT.indexOf("{gate.kind === 'approval_required' ?"),
    SIGN_UP_CLIENT.indexOf("{gate.kind === 'invited' && accountCreated ?"),
  );
  expect(gateBranch.length).toBeGreaterThan(0);
  expect(gateBranch).not.toContain('<input');
  expect(gateBranch).not.toContain('<form');
  expect(gateBranch).not.toContain('type="password"');
});

test('the ungated page fails closed: no token means the gate, before any fetch', () => {
  // The initial state is decided from the URL alone, so the approval-only state
  // is what renders first. There is no window in which a form exists.
  expect(SIGN_UP_CLIENT).toContain(
    "invitationToken ? { kind: 'checking' } : { kind: 'approval_required', message: null },",
  );
});

// ── 10: the CTA an unapproved visitor is offered ────────────────────────────

test('the unapproved visitor is offered Request Pilot and sign-in, nothing else', () => {
  expect(REQUEST_PILOT_CTA).toBe('Request Pilot');
  expect(SIGN_UP_CLIENT).toContain('href="/request-pilot"');
  expect(SIGN_UP_CLIENT).toContain('href="/sign-in"');
});

test('no signup copy promises access that approval has not granted', () => {
  for (const forbidden of ['Start free', 'Start Free', 'free trial', 'Start monitoring', 'Get started with']) {
    expect(SIGN_UP_CLIENT, `signup still promises "${forbidden}"`).not.toContain(forbidden);
  }
  // The body may name a workspace, a Pilot, and monitoring — but only to say
  // that signing up produces none of them.
  expect(APPROVAL_ONLY_BODY).toContain('does not create a workspace, activate a Pilot, or start monitoring');
});

// ── 11: no OAuth shortcut around the gate ───────────────────────────────────

test('the signup page offers no social sign-up path around the approval gate', () => {
  // There is no OAuth account path in this product (the only OAuth is the
  // workspace-scoped Slack install). The disabled Google/GitHub buttons implied
  // a second onboarding route existed; they are gone rather than decorative.
  for (const forbidden of ['Continue with Google', 'Continue with GitHub', 'suSocialBtn', 'GoogleIcon']) {
    expect(SIGN_UP_CLIENT, `signup still renders ${forbidden}`).not.toContain(forbidden);
  }
});

// ── 2, 7: the invited state ─────────────────────────────────────────────────

test('the form appears only for an invitation the backend resolved', () => {
  // valid !== true, a non-OK status, or a missing invitation body all land in
  // the approval-required state — never in a usable form.
  expect(SIGN_UP_CLIENT).toContain("if (!response.ok || payload.valid !== true || !payload.invitation) {");
  expect(SIGN_UP_CLIENT).toContain("setGate({ kind: 'approval_required', message: invitationRefusalMessage(payload) });");
  expect(SIGN_UP_CLIENT).toContain('/api/pilot-invitations?token=');
});

test('an unreadable invitation lookup is not treated as an approved one', () => {
  const catchBlock = SIGN_UP_CLIENT.slice(SIGN_UP_CLIENT.indexOf('} catch {'));
  expect(catchBlock).toContain("setGate({ kind: 'approval_required', message: INVITATION_UNAVAILABLE });");
});

test('the approved address is server-supplied and read-only in the form', () => {
  expect(SIGN_UP_CLIENT).toContain('value={gate.invitation.email}');
  expect(SIGN_UP_CLIENT).toContain('readOnly');
  expect(SIGN_UP_CLIENT).toContain('aria-readonly="true"');
  // No state variable backs the address, so there is nothing for a typed value
  // to overwrite — the page cannot post an address the backend did not name.
  expect(SIGN_UP_CLIENT).not.toContain('setEmail(');
  expect(SIGN_UP_CLIENT).not.toContain("const [email, setEmail]");
});

test('the submitted address is the invitation address, not page input', () => {
  // Stronger than it used to be: the form no longer submits an address at all.
  // The backend reads the approved one from the invitation the token resolves to
  // (services/api/app/domains/tenancy/endpoints.py:signup_invited_user), so there
  // is no address field in the payload for a browser to edit.
  const submit = SIGN_UP_CLIENT.slice(
    SIGN_UP_CLIENT.indexOf('await signUpWithInvitation({'),
    SIGN_UP_CLIENT.indexOf('if (result.accountExists)'),
  );
  expect(submit).toContain('token: invitationToken');
  expect(submit).not.toContain('email:');
  // The address the applicant SEES is still the one the backend named.
  expect(SIGN_UP_CLIENT).toContain('value={gate.invitation.email}');
});

test('the submit handler refuses to run outside the invited state', () => {
  expect(SIGN_UP_CLIENT).toContain("if (gate.kind !== 'invited') {");
});

test('the signup payload carries no tenant, plan, role, or admin field', () => {
  for (const forbidden of ['organization_id', 'plan:', 'is_internal_admin', 'entitlement', 'role:']) {
    expect(SIGN_UP_CLIENT, `signup payload mentions ${forbidden}`).not.toContain(forbidden);
  }
});

// ── 6: activation stays on the single existing path ─────────────────────────

test('a created account is sent to accept its invitation, not into the product', () => {
  // Signing up produces an account and nothing else, so the success state points
  // at the one activation path rather than at a dashboard it cannot load.
  expect(ACCOUNT_CREATED_BODY).toContain('accept your invitation');
  expect(SIGN_UP_CLIENT).toContain('acceptInvitationPath(invitationToken)');
  expect(SIGN_UP_CLIENT).not.toContain("router.push('/dashboard')");
});

test('signup creates no second activation path of its own', () => {
  for (const forbidden of ['/api/workspaces', "'/api/auth/select-workspace'", 'createWorkspace']) {
    expect(SIGN_UP_CLIENT, `signup reaches ${forbidden}`).not.toContain(forbidden);
  }
});

test('the accept page hands the invitation to signup instead of dropping it', () => {
  const acceptClient = read('accept-invitation', 'accept-invitation-client.tsx');
  // Built by app/invitation-routing.ts now, so every screen composes the URL from
  // one definition. That module's own spec pins the string it produces.
  expect(acceptClient).toContain('invitationSignUpHref(token)');
});

// ── token resolution ────────────────────────────────────────────────────────

test('an invitation token is read from any link an approved applicant may hold', () => {
  expect(resolveInvitationToken(new URLSearchParams('invite=abc'))).toBe('abc');
  expect(resolveInvitationToken(new URLSearchParams('token=abc'))).toBe('abc');
  expect(resolveInvitationToken(new URLSearchParams('next=/accept-invitation?token=abc'))).toBe('abc');
});

test('no token is found where there is none', () => {
  expect(resolveInvitationToken(null)).toBe('');
  expect(resolveInvitationToken(new URLSearchParams(''))).toBe('');
  expect(resolveInvitationToken(new URLSearchParams('next=/dashboard'))).toBe('');
  expect(resolveInvitationToken(new URLSearchParams('invite=   '))).toBe('');
});

test('a token is never read out of an off-site or unrelated next parameter', () => {
  // `next` is followed only when it is a same-origin /accept-invitation path, so
  // a crafted link cannot steer the lookup somewhere else.
  expect(resolveInvitationToken(new URLSearchParams('next=https://evil.example/accept-invitation?token=abc'))).toBe('');
  expect(resolveInvitationToken(new URLSearchParams('next=//evil.example/accept-invitation?token=abc'))).toBe('');
  expect(resolveInvitationToken(new URLSearchParams('next=/settings?token=abc'))).toBe('');
});

test('the activation path is the existing accept-invitation route', () => {
  expect(acceptInvitationPath('a b/c')).toBe('/accept-invitation?token=a%20b%2Fc');
});

// ── the signed-in case ──────────────────────────────────────────────────────

test('an already-signed-in visitor is redirected rather than shown a signup form', () => {
  expect(SIGN_UP_PAGE).toContain('const cookieStore = await cookies();');
  expect(SIGN_UP_PAGE).toContain("redirect(invitationToken ? acceptInvitationPath(invitationToken) : '/dashboard');");
  expect(SIGN_UP_CLIENT).toContain("router.replace(invitationToken ? acceptInvitationPath(invitationToken) : '/dashboard');");
});
