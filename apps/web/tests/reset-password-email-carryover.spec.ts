import { readFileSync } from 'node:fs';
import path from 'node:path';
import { expect, test } from '@playwright/test';

import {
  RESET_PASSWORD_PATH,
  buildResetPasswordHref,
  buildResetRequestPayload,
  buildResetSubmissionPayload,
  normalizeResetEmail,
  resolveResetRequestEmail,
} from '../app/password-reset-request';

const SIGN_IN_CLIENT_PATH = path.resolve(__dirname, '../app/sign-in/sign-in-page-client.tsx');
const RESET_CLIENT_PATH = path.resolve(__dirname, '../app/reset-password/reset-password-client.tsx');
const RESET_MODULE_PATH = path.resolve(__dirname, '../app/password-reset-request.ts');
const REQUEST_FORM_PATH = path.resolve(__dirname, '../app/reset-password/components/request-reset-form.tsx');
const NEW_PASSWORD_FORM_PATH = path.resolve(__dirname, '../app/reset-password/components/set-new-password-form.tsx');

const signInSource = readFileSync(SIGN_IN_CLIENT_PATH, 'utf8');
const resetClientSource = readFileSync(RESET_CLIENT_PATH, 'utf8');
const resetModuleSource = readFileSync(RESET_MODULE_PATH, 'utf8');
const requestFormSource = readFileSync(REQUEST_FORM_PATH, 'utf8');
const newPasswordFormSource = readFileSync(NEW_PASSWORD_FORM_PATH, 'utf8');

// The pilot account that was surfacing on /reset-password for unrelated users.
const PILOT_EMAIL = 'decoda.guard@gmail.com';

test.describe('sign-in email is carried to the reset page', () => {
  test('an entered sign-in email travels to /reset-password as a url-encoded param', () => {
    expect(buildResetPasswordHref('thanhdat852@gmail.com')).toBe(
      '/reset-password?email=thanhdat852%40gmail.com',
    );
  });

  test('the carried address survives a round trip back into the reset field', () => {
    const href = buildResetPasswordHref('thanhdat852@gmail.com');
    const carried = new URL(href, 'https://rwa.decodasecurity.com').searchParams.get('email');

    expect(resolveResetRequestEmail(carried)).toBe('thanhdat852@gmail.com');
  });

  test('addresses needing escaping are encoded, not truncated', () => {
    const href = buildResetPasswordHref('first+tag@example.co.uk');

    expect(href).toBe('/reset-password?email=first%2Btag%40example.co.uk');
    expect(resolveResetRequestEmail(new URL(href, 'https://example.test').searchParams.get('email')))
      .toBe('first+tag@example.co.uk');
  });

  test('surrounding whitespace from the sign-in box is trimmed, not dropped', () => {
    expect(buildResetPasswordHref('  thanhdat852@gmail.com  ')).toBe(
      '/reset-password?email=thanhdat852%40gmail.com',
    );
  });

  test('the sign-in page builds the link from the entered email, not a constant path', () => {
    expect(signInSource).toContain('buildResetPasswordHref(email)');
    expect(signInSource).not.toContain('href="/reset-password"');
  });
});

test.describe('one user never inherits another user\'s email', () => {
  test('each address produces only its own reset link', () => {
    const first = buildResetPasswordHref('thanhdat852@gmail.com');
    const second = buildResetPasswordHref('someone.else@example.com');

    expect(first).not.toBe(second);
    expect(first).not.toContain('someone.else');
    expect(second).not.toContain('thanhdat852');
  });

  test('the reset field is seeded per-visit from the url, holding no cross-user state', () => {
    expect(resolveResetRequestEmail('thanhdat852@gmail.com')).toBe('thanhdat852@gmail.com');
    expect(resolveResetRequestEmail('someone.else@example.com')).toBe('someone.else@example.com');
    // A later visit with no param is blank again — nothing carries over.
    expect(resolveResetRequestEmail(null)).toBe('');
  });

  test('the reset page reads the address from the query string only', () => {
    expect(resetClientSource).toContain("resolveResetRequestEmail(searchParams.get('email'))");
    expect(resetClientSource).not.toMatch(/localStorage|sessionStorage|document\.cookie/);
  });
});

test.describe('a direct /reset-password visit shows a blank email field', () => {
  test('a missing, empty, or malformed param resolves to blank', () => {
    expect(resolveResetRequestEmail(null)).toBe('');
    expect(resolveResetRequestEmail(undefined)).toBe('');
    expect(resolveResetRequestEmail('')).toBe('');
    expect(resolveResetRequestEmail('   ')).toBe('');
    expect(resolveResetRequestEmail('not-an-email')).toBe('');
    expect(resolveResetRequestEmail('@example.com')).toBe('');
  });

  test('an over-long param is rejected rather than rendered back into the field', () => {
    const oversized = `${'a'.repeat(250)}@example.com`;

    expect(oversized.length).toBeGreaterThan(254);
    expect(resolveResetRequestEmail(oversized)).toBe('');
  });

  test('a reset-email link carries only a token, so the field opens blank', () => {
    const url = new URL('/reset-password?token=abc123', 'https://rwa.decodasecurity.com');

    expect(url.searchParams.get('token')).toBe('abc123');
    expect(resolveResetRequestEmail(url.searchParams.get('email'))).toBe('');
  });

  test('the empty field is opted out of browser credential autofill', () => {
    // An unnamed <input type="email"> on a same-origin page is a credential-manager
    // fill target: that is how a saved pilot account appeared for other users.
    expect(requestFormSource).toContain('id="reset-request-email"');
    expect(requestFormSource).toContain('name="reset_request_email"');
    expect(requestFormSource).toContain('autoComplete="off"');
    expect(requestFormSource).toContain('data-lpignore="true"');
    expect(requestFormSource).toContain('data-1p-ignore');
    expect(requestFormSource).toContain('htmlFor="reset-request-email"');
    // The new-password boxes must not be filled with a saved current password either.
    expect(newPasswordFormSource).toContain('autoComplete="new-password"');
    expect(newPasswordFormSource).not.toContain('autoComplete="current-password"');
  });
});

test.describe('no pilot account is hardcoded or defaulted', () => {
  test('the reset flow source contains no real account addresses', () => {
    // Only these generic placeholders may appear as literal addresses in the
    // auth sources; any other hardcoded address is a defaulted account.
    const allowedPlaceholders = new Set(['you@company.com']);

    for (const source of [resetModuleSource, resetClientSource, signInSource, requestFormSource, newPasswordFormSource]) {
      expect(source).not.toContain(PILOT_EMAIL);
      expect(source).not.toContain('decoda.guard');

      const literalAddresses = source.match(/[\w.+-]+@[\w-]+\.[\w.-]+/g) ?? [];
      expect(literalAddresses.filter((address) => !allowedPlaceholders.has(address))).toEqual([]);
    }
  });

  test('an absent email never falls back to the pilot or any other account', () => {
    for (const empty of [null, undefined, '', '   ', 'garbage']) {
      expect(resolveResetRequestEmail(empty)).toBe('');
      expect(resolveResetRequestEmail(empty)).not.toBe(PILOT_EMAIL);
      expect(buildResetPasswordHref(empty)).toBe(RESET_PASSWORD_PATH);
      expect(buildResetPasswordHref(empty)).not.toContain('decoda.guard');
      expect(buildResetRequestPayload(empty)).toBeNull();
    }
  });

  test('a blank field sends no reset request rather than a defaulted one', () => {
    expect(buildResetRequestPayload('')).toBeNull();
    // Submission is gated on a payload the blank field cannot produce.
    expect(resetClientSource).toContain('canSubmit={Boolean(requestPayload)}');
    expect(resetClientSource).toContain('if (!requestPayload) {');
    expect(requestFormSource).toContain('disabled={!canSubmit || submitting}');
  });
});

test.describe('the reset request targets exactly the submitted email', () => {
  test('the request body carries the submitted address and nothing else', () => {
    expect(buildResetRequestPayload('thanhdat852@gmail.com')).toEqual({ email: 'thanhdat852@gmail.com' });
    expect(Object.keys(buildResetRequestPayload('thanhdat852@gmail.com') ?? {})).toEqual(['email']);
  });

  test('a pilot address is only ever used when a user types it themselves', () => {
    // Not special-cased in either direction: typed in, it is the target like any other.
    expect(buildResetRequestPayload(PILOT_EMAIL)).toEqual({ email: PILOT_EMAIL });
    expect(buildResetRequestPayload('founder@example.com')).toEqual({ email: 'founder@example.com' });
    expect(buildResetRequestPayload('scale@example.com')).toEqual({ email: 'scale@example.com' });
  });

  test('the reset page posts the built payload to the forgot-password endpoint', () => {
    expect(resetClientSource).toContain("fetch('/api/auth/forgot-password'");
    expect(resetClientSource).toContain('JSON.stringify(requestPayload)');
  });
});

test.describe('only a reset token can change a password', () => {
  test('a missing or blank token produces no submission at all', () => {
    expect(buildResetSubmissionPayload(null, 'NewPassword123')).toBeNull();
    expect(buildResetSubmissionPayload(undefined, 'NewPassword123')).toBeNull();
    expect(buildResetSubmissionPayload('', 'NewPassword123')).toBeNull();
    expect(buildResetSubmissionPayload('   ', 'NewPassword123')).toBeNull();
  });

  test('an email address can never stand in for a token', () => {
    expect(buildResetSubmissionPayload(null, 'NewPassword123')).toBeNull();

    const authorized = buildResetSubmissionPayload('reset-token-123', 'NewPassword123');
    expect(authorized).toEqual({ token: 'reset-token-123', password: 'NewPassword123' });
    // The submission is token-addressed: no email field travels with it.
    expect(Object.keys(authorized ?? {}).sort()).toEqual(['password', 'token']);
  });

  test('a blank password is not submitted even with a valid token', () => {
    expect(buildResetSubmissionPayload('reset-token-123', '')).toBeNull();
  });

  test('the reset page gates submission on the built payload, not on the email field', () => {
    expect(resetClientSource).toContain('JSON.stringify(submissionPayload)');
    expect(resetClientSource).toContain('if (!submissionPayload)');
    // A verified token is a precondition of submitting at all.
    expect(resetClientSource).toContain('if (submitting || !canSubmitNewPassword(tokenState)) return;');

    // No address — carried, typed, or displayed — is read by the password change.
    const submitBlock = resetClientSource.slice(
      resetClientSource.indexOf('async function submitReset'),
      resetClientSource.indexOf('if (succeeded) {'),
    );
    expect(submitBlock.length).toBeGreaterThan(0);
    expect(submitBlock).toContain('buildResetSubmissionPayload(token, password)');
    for (const addressSource of ['accountEmail', 'tokenState.email', 'searchParams', 'requestEmail']) {
      expect(submitBlock).not.toContain(addressSource);
    }

    // The address on the reset screen is display-only and never re-submitted.
    expect(newPasswordFormSource).toContain('readOnly');
    expect(newPasswordFormSource).not.toContain('onChange={(event) => onAccountEmailChange');
  });
});

test.describe('normalizeResetEmail shape rules', () => {
  test('accepts plausible addresses and rejects everything else', () => {
    expect(normalizeResetEmail('user@example.com')).toBe('user@example.com');
    expect(normalizeResetEmail('User.Name+tag@sub.example.co')).toBe('User.Name+tag@sub.example.co');
    expect(normalizeResetEmail('user@example')).toBe('');
    expect(normalizeResetEmail('user example.com')).toBe('');
    expect(normalizeResetEmail(42 as unknown as string)).toBe('');
  });

  test('preserves the case the user typed rather than rewriting their address', () => {
    expect(normalizeResetEmail('User@Example.com')).toBe('User@Example.com');
  });
});
