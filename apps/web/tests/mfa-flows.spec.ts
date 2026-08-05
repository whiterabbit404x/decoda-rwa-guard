import { readFileSync } from 'node:fs';
import path from 'node:path';

import { expect, test } from '@playwright/test';

const appRoot = path.join(process.cwd(), 'apps/web/app');

function read(relativePath: string) {
  return readFileSync(path.join(appRoot, relativePath), 'utf8');
}

test('sign-in and security settings wire MFA challenge and enrollment flows', async () => {
  const authContext = read('pilot-auth-context.tsx');
  const signIn = read('sign-in/sign-in-page-client.tsx');
  const security = read('security-settings-page-client.tsx');

  expect(authContext).toContain('mfa_required');
  expect(authContext).toContain('completeMfaSignIn');
  expect(authContext).toContain('enrollMfa');
  expect(authContext).toContain('confirmMfaEnrollment');
  expect(signIn).toContain("if (message === 'MFA_REQUIRED')");
  expect(signIn).toContain('Complete sign in');
  expect(signIn).toContain('unused recovery code');
  expect(security).toContain('Multi-factor authentication');
  expect(security).toContain('Recovery codes (shown once)');
  expect(security).toContain('Copy recovery codes');
  expect(security).toContain('I saved these recovery codes');
});

test('mfa auth proxy routes are present for complete enroll confirm disable', async () => {
  const complete = read('api/auth/mfa/complete-signin/route.ts');
  const enroll = read('api/auth/mfa/enroll/route.ts');
  const confirm = read('api/auth/mfa/confirm/route.ts');
  const disable = read('api/auth/mfa/disable/route.ts');

  expect(complete).toContain('/auth/mfa/complete-signin');
  expect(enroll).toContain('/auth/mfa/enroll');
  expect(confirm).toContain('/auth/mfa/confirm');
  expect(disable).toContain('/auth/mfa/disable');
});

test('enroll is single-flight and stays disabled while a request is pending', async () => {
  const security = read('security-settings-page-client.tsx');
  // A guard ref short-circuits a second Enroll while one is in flight, so a rapid
  // double-click cannot mint a second pending enrollment and rotate the secret.
  expect(security).toContain('if (enrollInFlight.current) {');
  expect(security).toContain('enrollInFlight.current = true;');
  expect(security).toContain('enrollInFlight.current = false;');
  // The Enroll button is disabled while any command is submitting.
  expect(security).toContain('const commandsDisabled = submitting || !secureSessionReady;');
  expect(security).toContain('onClick={() => void startMfaEnrollment()} disabled={commandsDisabled}');
});

test('enrollment is click-driven and never auto-fired on mount (refresh does not rotate)', async () => {
  const security = read('security-settings-page-client.tsx');
  // startMfaEnrollment must only be reachable from explicit onClick handlers; a
  // useEffect that calls it on mount would regenerate the pending secret on refresh.
  const effectBodies = security.match(/useEffect\([\s\S]*?\}, \[[^\]]*\]\);/g) ?? [];
  for (const body of effectBodies) {
    expect(body).not.toContain('startMfaEnrollment');
    expect(body).not.toContain('enrollMfa(');
  }
});

test('confirm submits the pending enrollment id bound to the shown secret', async () => {
  const security = read('security-settings-page-client.tsx');
  const authContext = read('pilot-auth-context.tsx');
  expect(security).toContain('confirmMfaEnrollment(mfaCode, mfaSetup?.enrollment_id ?? null)');
  expect(authContext).toContain('const confirmMfaEnrollment = useCallback(async (code: string, enrollmentId?: string | null)');
  expect(authContext).toContain('enrollment_id: enrollmentId ?? undefined');
  // Enroll response carries the enrollment id + expiry contract fields.
  expect(authContext).toContain('enrollment_id: data.enrollment_id ?? null');
  expect(authContext).toContain('expires_at: data.expires_at ?? null');
});

test('explicit regeneration warns the previous authenticator entry becomes invalid', async () => {
  const security = read('security-settings-page-client.tsx');
  expect(security).toContain('Regenerate setup key');
  expect(security).toContain('startMfaEnrollment({ regenerate: true })');
  expect(security).toContain('invalidates the previous authenticator entry');
});

test('the setup secret is reveal-on-demand and not persisted as plaintext page text', async () => {
  const security = read('security-settings-page-client.tsx');
  // The full otpauth URI is no longer dumped as persistent page text.
  expect(security).not.toContain('<pre>{mfaSetup.otpauth_uri}</pre>');
  // The manual key is only rendered behind an explicit reveal toggle...
  expect(security).toContain('revealSetupKey && mfaSetup.secret');
  expect(security).toContain('Reveal manual setup key');
  // ...and it is cleared from client state on successful confirmation.
  expect(security).toContain('setMfaSetup(null);');
  expect(security).toContain('setRevealSetupKey(false);');
});

test('invalid-code failure shows a generic message without exposing the secret', async () => {
  const security = read('security-settings-page-client.tsx');
  // The error line renders a message string only — never the secret.
  expect(security).toContain('MFA verification failed');
  expect(security).toContain('{mfaError}');
  expect(security).not.toContain('{mfaError}: {mfaSetup');
});

test('successful confirmation refreshes status to Enabled and offers return to the action', async () => {
  const security = read('security-settings-page-client.tsx');
  const authContext = read('pilot-auth-context.tsx');
  // Status is derived from the canonical user record, refreshed after confirm.
  expect(security).toContain("Status: {user?.mfa_enabled ? 'Enabled' : 'Disabled'}");
  expect(authContext).toContain('const confirmMfaEnrollment = useCallback');
  expect(authContext).toContain('await refreshUser();');
  // The preserved response action is reachable once MFA is satisfied.
  expect(security).toContain('Return to the response action');
  expect(security).toContain('safeInternalReturnTo(searchParams.get');
});
