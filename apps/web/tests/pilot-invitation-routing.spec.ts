import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import {
  acceptInvitationPath,
  invitationDestination,
  invitationSignInHref,
  invitationSignUpHref,
} from '../app/invitation-routing';
import {
  INVITED_ACCOUNT_EXISTS,
  INVITED_CONFIRM_PASSWORD_LABEL,
  INVITED_EMAIL_HINT,
  INVITED_EMAIL_LABEL,
  INVITED_PASSWORD_MISMATCH,
  INVITED_SUBMIT_CTA,
} from '../app/signup-access';

// Invitation acceptance, routed on whether the approved address has an account.
//
// The bug: every route out of the invitation email led to /sign-in. A brand-new
// approved applicant has no Decoda password, so that screen could only tell them
// "Invalid email or password" — the invitation worked, the approval was real,
// and onboarding still dead-ended.
//
// These run without a web server, like the other approval-only specs, so they
// stay reliable in CI. They pin two things a screenshot review would miss: that
// the DESTINATION comes from the backend's answer rather than a page's guess,
// and that every hop in the chain carries the same invitation forward.

const APP_DIR = path.join(__dirname, '..', 'app');

function read(...segments: string[]): string {
  return fs.readFileSync(path.join(APP_DIR, ...segments), 'utf-8');
}

const ACCEPT_CLIENT = read('accept-invitation', 'accept-invitation-client.tsx');
const ACCEPT_PAGE = read('accept-invitation', 'page.tsx');
const SIGN_IN_CLIENT = read('sign-in', 'sign-in-page-client.tsx');
const SIGN_IN_PAGE = read('sign-in', 'page.tsx');
const SIGN_UP_CLIENT = read('sign-up', 'sign-up-page-client.tsx');
const AUTH_CONTEXT = read('pilot-auth-context.tsx');

const TOKEN = 'tok-en/with spaces';
const ENCODED = encodeURIComponent(TOKEN);

// ── the fork ────────────────────────────────────────────────────────────────

test('a valid invitation for an address with no account goes to signup', () => {
  expect(invitationDestination({ valid: true, account_exists: false }, TOKEN))
    .toBe(`/sign-up?invite=${ENCODED}`);
});

test('a valid invitation for an address that already has an account goes to sign-in', () => {
  expect(invitationDestination({ valid: true, account_exists: true }, TOKEN))
    .toBe(invitationSignInHref(TOKEN));
});

test('an unanswered account_exists routes to sign-in, not to a form that would fail', () => {
  // Fail closed, and fail USEFUL: sign-in states its own outcome truthfully to
  // someone who turns out to have no account. A signup form that will 409 on
  // submit does not.
  for (const lookup of [{ valid: true }, { valid: true, account_exists: null }]) {
    expect(invitationDestination(lookup, TOKEN)).toBe(invitationSignInHref(TOKEN));
  }
});

test('an invitation the backend did not confirm routes nowhere at all', () => {
  expect(invitationDestination({ valid: false, account_exists: false }, TOKEN)).toBeNull();
  expect(invitationDestination(null, TOKEN)).toBeNull();
  expect(invitationDestination(undefined, TOKEN)).toBeNull();
  // No token is no invitation, whatever a payload claims.
  expect(invitationDestination({ valid: true, account_exists: false }, '')).toBeNull();
});

// ── the invitation survives every hop ───────────────────────────────────────

test('every destination carries the same invitation, url-encoded', () => {
  expect(acceptInvitationPath(TOKEN)).toBe(`/accept-invitation?token=${ENCODED}`);
  expect(invitationSignUpHref(TOKEN)).toBe(`/sign-up?invite=${ENCODED}`);
  // `next` carries an already-encoded path, so it is encoded once more as a
  // query value. Decoding it once must give back exactly the activation path —
  // the assertion below this one checks that round trip.
  expect(invitationSignInHref(TOKEN)).toBe(
    `/sign-in?invite=${ENCODED}&next=${encodeURIComponent(acceptInvitationPath(TOKEN))}`,
  );
});

test('the sign-in destination lands on activation, not on a dashboard the account cannot use', () => {
  const url = new URL(invitationSignInHref(TOKEN), 'https://app.example');
  expect(url.searchParams.get('invite')).toBe(TOKEN);
  expect(url.searchParams.get('next')).toBe(acceptInvitationPath(TOKEN));
});

test('an invited sign-in for an address with no account says so instead of bouncing', () => {
  // /sign-up's own "Already have an account? Sign in" link lands on this screen.
  // Redirecting that click straight back would make the link look broken, so the
  // screen states the backend's answer and offers the account-creation form.
  expect(SIGN_IN_CLIENT).toContain("invitation.account_exists === false");
  expect(SIGN_IN_CLIENT).toContain('There is no Decoda account for');
  expect(SIGN_IN_CLIENT).not.toContain('routedToSignUp');
});

test('"Create one" on an invited sign-in keeps the invitation', () => {
  // The link that used to hard-code /sign-up. A bare /sign-up lands an approved
  // applicant on the approval-only state, one click from the account they were
  // invited to create.
  expect(SIGN_IN_CLIENT).toContain("href={invitationToken ? invitationSignUpHref(invitationToken) : '/sign-up'}");
  expect(SIGN_IN_CLIENT).not.toContain('<Link href="/sign-up" className="siLink"');
});

test('"Sign in" on invitation-aware signup keeps the invitation', () => {
  expect(SIGN_UP_CLIENT).toContain('href={invitationSignInHref(invitationToken)}');
});

test('the accept page offers both invitation-aware routes and no bare ones', () => {
  expect(ACCEPT_CLIENT).toContain('invitationSignUpHref(token)');
  expect(ACCEPT_CLIENT).toContain('invitationSignInHref(token)');
  expect(ACCEPT_CLIENT).not.toContain('href={`/sign-in?next=');
});

test('sign-in reads the invitation from any link an approved applicant may hold', () => {
  // resolveInvitationToken accepts ?invite, ?token and a same-origin ?next, and
  // its own spec pins that. What matters here is that /sign-in uses it at all.
  expect(SIGN_IN_PAGE).toContain('resolveInvitationToken');
  expect(SIGN_IN_PAGE).toContain('invitationToken={invitationToken}');
});

test('authenticating inside an invitation flow lands on activation', () => {
  expect(SIGN_IN_CLIENT).toContain(
    "const targetPath = invitationToken ? acceptInvitationPath(invitationToken) : (nextPath ?? '/dashboard');",
  );
  // One redirect helper for both the password path and the MFA path, so the
  // invitation cannot survive one and be dropped by the other.
  expect(SIGN_IN_CLIENT).toContain("confirmSessionAndRedirect('password-signin')");
  expect(SIGN_IN_CLIENT).toContain("confirmSessionAndRedirect('mfa-complete')");
});

// ── the page states what the server said ────────────────────────────────────

test('the accept page routes on the backend answer, never on its own guess', () => {
  expect(ACCEPT_CLIENT).toContain('invitationDestination(');
  expect(ACCEPT_CLIENT).toContain('account_exists: invitation.account_exists');
  // Armed only with no session cookie: /sign-up bounces a cookie-holding visitor
  // straight back here, so a stale cookie must not start a ping-pong.
  expect(ACCEPT_PAGE).toContain("cookieStore.get('decoda_session')");
  expect(ACCEPT_CLIENT).toContain('hasSessionCookie');
});

test('the primary action for a brand-new approved applicant creates an account', () => {
  const newUserBlock = ACCEPT_CLIENT.slice(ACCEPT_CLIENT.indexOf('accountExists ? ('));
  const createIndex = newUserBlock.indexOf('Create your Decoda account');
  const signInIndex = newUserBlock.indexOf('Already have an account? Sign in');
  expect(createIndex).toBeGreaterThan(-1);
  expect(signInIndex).toBeGreaterThan(-1);
  // The account-creation CTA is the primary button; sign-in is the secondary.
  expect(newUserBlock).toContain('className="suSubmitBtn"');
  expect(newUserBlock.indexOf('className="pilotRequestSecondary"')).toBeLessThan(signInIndex + 200);
});

test('an invitation that is no longer usable offers a way back in, not only the form', () => {
  // "Already accepted" is the common case behind this state, and the account it
  // activated still works.
  expect(ACCEPT_CLIENT).toContain("isAuthenticated ? '/dashboard' : '/sign-in'");
  expect(ACCEPT_CLIENT).toContain('Go to your dashboard');
});

// ── invitation-aware signup ─────────────────────────────────────────────────

test('the invited form locks the approved address and asks for the password twice', () => {
  expect(SIGN_UP_CLIENT).toContain('{INVITED_EMAIL_LABEL}');
  expect(SIGN_UP_CLIENT).toContain('{INVITED_EMAIL_HINT}');
  expect(SIGN_UP_CLIENT).toContain('value={gate.invitation.email}');
  expect(SIGN_UP_CLIENT).toContain('readOnly');
  expect(SIGN_UP_CLIENT).toContain('{INVITED_CONFIRM_PASSWORD_LABEL}');
  expect(SIGN_UP_CLIENT).toContain('id="su-confirm-password"');
  expect(SIGN_UP_CLIENT).toContain('if (password !== confirmPassword)');
  expect(INVITED_PASSWORD_MISMATCH).toContain('match');
  expect(INVITED_SUBMIT_CTA).toBe('Activate Pilot');
});

test('the invited form posts only a name and a password, never an address', () => {
  const submit = SIGN_UP_CLIENT.slice(
    SIGN_UP_CLIENT.indexOf('await signUpWithInvitation({'),
    SIGN_UP_CLIENT.indexOf('if (result.accountExists)'),
  );
  expect(submit).toContain('token: invitationToken');
  expect(submit).toContain('password,');
  expect(submit).toContain('full_name: fullName');
  // The address is the backend's to decide, from the invitation the token
  // resolves to. Sending one would be a field to tamper with.
  expect(submit).not.toContain('email:');
  expect(submit).not.toContain('workspace_name');
  expect(submit).not.toContain('plan');
});

test('an address that already has an account is sent to sign in, not refused into a dead end', () => {
  expect(SIGN_UP_CLIENT).toContain('router.replace(invitationSignInHref(invitationToken))');
  expect(INVITED_ACCOUNT_EXISTS).toContain('Sign in');
});

test('the invited signup call reaches its own backend route and nothing else', () => {
  expect(AUTH_CONTEXT).toContain("const proxyUrl = '/api/pilot-invitations/signup';");
  // 409 is a routing signal, not a stack trace to print at the applicant.
  expect(AUTH_CONTEXT).toContain('return { user: null, accountExists: true };');
  // Activation is an authenticated mutation, so the anti-CSRF token must exist
  // before the very next call.
  const invited = AUTH_CONTEXT.slice(AUTH_CONTEXT.indexOf('const signUpWithInvitation'));
  expect(invited.indexOf('await fetchAndStoreCsrfToken();')).toBeGreaterThan(-1);
});

test('the same-origin proxy exists and writes the session cookie', () => {
  const route = fs.readFileSync(
    path.join(APP_DIR, 'api', 'pilot-invitations', 'signup', 'route.ts'),
    'utf-8',
  );
  expect(route).toContain("proxyAuthRequest(request, '/pilot-invitations/signup', 'POST', { cookieAction: 'set-session' })");
});

// ── the loophole stays closed ───────────────────────────────────────────────

test('signup without an invitation still shows the approval-only state', () => {
  // Fail closed: no token, no form. The gate starts in approval_required and the
  // submit handler refuses outside the invited state regardless.
  expect(SIGN_UP_CLIENT).toContain(
    "invitationToken ? { kind: 'checking' } : { kind: 'approval_required', message: null },",
  );
  expect(SIGN_UP_CLIENT).toContain("if (gate.kind !== 'invited') {");
});

test('this fix opens no second activation path', () => {
  for (const forbidden of ['/api/workspaces', "'/api/auth/select-workspace'", 'createWorkspace']) {
    expect(SIGN_UP_CLIENT, `signup reaches ${forbidden}`).not.toContain(forbidden);
    expect(ACCEPT_CLIENT, `the accept page reaches ${forbidden}`).not.toContain(forbidden);
  }
  // The one activation call, unchanged.
  expect(ACCEPT_CLIENT).toContain("fetch('/api/pilot-invitations/accept'");
});

test('activation is attempted at most once per page load', () => {
  expect(ACCEPT_CLIENT).toContain('const activationStarted = useRef(false);');
  expect(ACCEPT_CLIENT).toContain('activationStarted.current = true;');
  expect(ACCEPT_CLIENT).toContain('const routed = useRef(false);');
});

test('the invitation page leaks no token in a referrer', () => {
  expect(ACCEPT_PAGE).toContain("referrer: 'no-referrer'");
  expect(ACCEPT_PAGE).toContain('robots: { index: false, follow: false }');
});
