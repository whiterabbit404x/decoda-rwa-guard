import { readFileSync } from 'node:fs';
import { join } from 'node:path';

import { expect, test } from '@playwright/test';

import { MFA_SETUP_PATH, isMfaSetupPath, mfaGateVariant } from '../app/mfa-required-gate';
import type { PilotMfaState } from '../app/pilot-auth-context';

const APP_DIR = join(__dirname, '..', 'app');

function state(overrides: Partial<PilotMfaState> = {}): PilotMfaState {
  return {
    required: true,
    enrolled: false,
    session_verified: false,
    satisfied: false,
    code: 'MFA_ENROLLMENT_REQUIRED',
    enforcement: 'all_members',
    plan: 'pilot',
    workspace_id: 'ws-1',
    ...overrides,
  };
}

test.describe('the Pilot MFA gate reflects the backend, and never replaces it', () => {
  test('an unenrolled Pilot operator is sent to enroll', () => {
    expect(mfaGateVariant(state())).toBe('enroll');
  });

  test('an enrolled operator on a password-only session is sent to verify', () => {
    expect(
      mfaGateVariant(state({ enrolled: true, code: 'MFA_CHALLENGE_REQUIRED' })),
    ).toBe('verify');
  });

  test('a satisfied session sees no gate', () => {
    expect(
      mfaGateVariant(state({ enrolled: true, session_verified: true, satisfied: true, code: null })),
    ).toBeNull();
  });

  test('a workspace whose policy does not cover this account sees no gate', () => {
    expect(mfaGateVariant(state({ required: false, satisfied: true, code: null }))).toBeNull();
  });

  test('an API that reports no MFA state shows no gate — the server still refuses', () => {
    // Absent is "the backend did not say", not "satisfied". Showing a gate on a
    // guess would block operators the server would have let through; the server
    // is the control either way.
    expect(mfaGateVariant(undefined)).toBeNull();
  });

  test('the setup page itself is never gated, or the gate would be a lockout', () => {
    expect(isMfaSetupPath(MFA_SETUP_PATH)).toBe(true);
    expect(isMfaSetupPath(`${MFA_SETUP_PATH}/`)).toBe(true);
    expect(isMfaSetupPath('/settings')).toBe(false);
    expect(isMfaSetupPath('/dashboard')).toBe(false);
    expect(isMfaSetupPath(null)).toBe(false);
  });
});

test.describe('the copy states a requirement, not a suggestion', () => {
  const source = readFileSync(join(APP_DIR, 'mfa-required-gate.tsx'), 'utf8');

  test('it uses the mandated sentences', () => {
    expect(source).toContain('Multi-factor authentication is required for Pilot access.');
    expect(source).toContain('Set up an authenticator before accessing this workspace.');
  });

  test('no customer-visible sentence softens it into a suggestion', () => {
    // Only the COPY block is customer-visible; the file's comments explain WHY
    // the requirement is not optional and must not be caught by this sweep.
    const copyBlock = source.slice(source.indexOf('const COPY'), source.indexOf('/** Which remedy'));
    expect(copyBlock).toContain('is required for Pilot access');
    for (const weasel of ['optional', 'recommended', 'we suggest', 'you may want', 'if you like']) {
      expect(copyBlock.toLowerCase()).not.toContain(weasel);
    }
  });

  test('it says the server is the control', () => {
    expect(source).toContain('enforces this on the server');
  });
});

test.describe('the gate is wired where the product is rendered', () => {
  const route = readFileSync(join(APP_DIR, 'authenticated-route.tsx'), 'utf8');

  test('AuthenticatedRoute renders it before the product children', () => {
    expect(route).toContain('mfaGateVariant(user?.mfa)');
    expect(route).toContain('<MfaRequired');
    expect(route.indexOf('mfaGateVariant(user?.mfa)')).toBeLessThan(
      route.lastIndexOf('return <>{children}</>;'),
    );
  });

  test('it exempts the setup path so enrollment stays reachable', () => {
    expect(route).toContain('!isMfaSetupPath(pathname)');
  });

  test('it carries the requested page so the operator can be returned to it', () => {
    expect(route).toContain('returnTo={currentPath}');
  });
});

test.describe('the setup page states the requirement and offers the way back', () => {
  const page = readFileSync(join(APP_DIR, 'security-settings-page-client.tsx'), 'utf8');

  test('a mandatory requirement is announced on the page it is satisfied on', () => {
    expect(page).toContain('mfa-mandatory-notice');
    expect(page).toContain('Multi-factor authentication is required for Pilot access.');
  });

  test('the return happens only after the one-time recovery codes are saved', () => {
    expect(page).toContain('recoveryCodesAcknowledged && returnTo');
    expect(page).toContain('mfa-return-to');
  });

  test('the requirement is read from the backend, not inferred in the browser', () => {
    expect(page).toContain("user?.mfa?.required");
    expect(page).toContain("user?.mfa?.satisfied === false");
  });
});
