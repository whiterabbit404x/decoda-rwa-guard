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
  expect(security).toContain('Multi-factor authentication');
  expect(security).toContain('Recovery codes (shown once)');
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
