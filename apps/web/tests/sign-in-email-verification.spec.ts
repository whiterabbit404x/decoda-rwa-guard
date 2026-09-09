/**
 * The unverified-email state on Sign In, and the way out of it.
 *
 * The regression this covers: an account whose address was never verified was refused
 * with a bare sentence and no control to act on, so the only escape was finding an old
 * verification email. With a completed password reset now settling verification too,
 * this state should be rare — but when it does occur the screen has to name it and
 * offer the fix.
 *
 * Follows this suite's convention — pure state/presentation logic is exercised
 * directly, and the wiring that connects it to the rendered screen is asserted
 * against the component sources.
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { expect, test } from '@playwright/test';

import {
  AuthStateError,
  EMAIL_NOT_VERIFIED_BODY,
  EMAIL_NOT_VERIFIED_CODE,
  EMAIL_NOT_VERIFIED_HEADING,
  VERIFICATION_RESEND_COOLDOWN_SECONDS,
  describeResendOutcome,
  formatVerificationResendLabel,
  isEmailNotVerifiedError,
  isEmailNotVerifiedResponse,
} from '../app/sign-in/email-verification-state';

const signInSource = readFileSync(path.resolve(__dirname, '../app/sign-in/sign-in-page-client.tsx'), 'utf8');
const authContextSource = readFileSync(path.resolve(__dirname, '../app/pilot-auth-context.tsx'), 'utf8');
const resendRouteSource = readFileSync(path.resolve(__dirname, '../app/api/auth/resend-verification/route.ts'), 'utf8');

// ---------------------------------------------------------------------------
// Recognising the state
// ---------------------------------------------------------------------------
test.describe('the unverified-email refusal is recognised by code, not by copy', () => {
  test('the backend code identifies the state', () => {
    expect(isEmailNotVerifiedResponse(403, { code: EMAIL_NOT_VERIFIED_CODE })).toBe(true);
    expect(EMAIL_NOT_VERIFIED_CODE).toBe('EMAIL_NOT_VERIFIED');
  });

  test('another 403 is not mistaken for it', () => {
    // A suspension is also a 403 and also mentions the account, but resending a
    // verification link does nothing for it — offering that would be a false promise.
    expect(isEmailNotVerifiedResponse(403, { detail: 'This account is suspended.' })).toBe(false);
    expect(isEmailNotVerifiedResponse(403, { code: 'csrf_invalid' })).toBe(false);
    expect(isEmailNotVerifiedResponse(401, { detail: 'Invalid email or password.' })).toBe(false);
    expect(isEmailNotVerifiedResponse(429, { code: EMAIL_NOT_VERIFIED_CODE })).toBe(false);
  });

  test('a body that is missing, malformed, or not an object resolves to "not this state"', () => {
    for (const body of [null, undefined, 'EMAIL_NOT_VERIFIED', 42, []]) {
      expect(isEmailNotVerifiedResponse(403, body)).toBe(false);
    }
  });

  test('the sentence shown to the customer is never what the screen branches on', () => {
    // Copy changes; codes do not. A screen matching prose would silently offer the
    // wrong action the day the wording is edited.
    expect(isEmailNotVerifiedResponse(403, { detail: EMAIL_NOT_VERIFIED_HEADING })).toBe(false);
    expect(signInSource).not.toContain("=== 'Verify your email");
    expect(signInSource).not.toContain('.includes(\'Verify your email');
  });

  test('the thrown error carries the code through to the screen', () => {
    const error = new AuthStateError('Verify your email to continue.', EMAIL_NOT_VERIFIED_CODE);

    expect(isEmailNotVerifiedError(error)).toBe(true);
    expect(error).toBeInstanceOf(Error);
    expect(error.message).toBe('Verify your email to continue.');

    // Every other failure stays an ordinary error and falls through to the error line.
    expect(isEmailNotVerifiedError(new Error('Invalid email or password.'))).toBe(false);
    expect(isEmailNotVerifiedError(new AuthStateError('nope', 'SOMETHING_ELSE'))).toBe(false);
    expect(isEmailNotVerifiedError('MFA_REQUIRED')).toBe(false);
    expect(isEmailNotVerifiedError(null)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// What the user is told, and what they can do
// ---------------------------------------------------------------------------
test.describe('the state names the next step instead of only the refusal', () => {
  test('the heading is an instruction, not a dead end', () => {
    expect(EMAIL_NOT_VERIFIED_HEADING).toBe('Verify your email to continue');
    expect(EMAIL_NOT_VERIFIED_BODY).toContain('verification link');
  });

  test('the resend button reports sending, cooldown, and readiness', () => {
    expect(formatVerificationResendLabel(0, false)).toBe('Resend verification email');
    expect(formatVerificationResendLabel(0, true)).toBe('Sending...');
    expect(formatVerificationResendLabel(30, false)).toBe('Resend available in 30 seconds');
    expect(formatVerificationResendLabel(1, false)).toBe('Resend available in 1 second');
    // A part-second remaining still reads as a whole second, never as "0".
    expect(formatVerificationResendLabel(0.4, false)).toBe('Resend available in 1 second');
    // Sending wins over a stale cooldown so the button never claims to be idle mid-request.
    expect(formatVerificationResendLabel(12, true)).toBe('Sending...');
    expect(VERIFICATION_RESEND_COOLDOWN_SECONDS).toBeGreaterThan(0);
  });

  test('a successful resend promises only what the response establishes', () => {
    const outcome = describeResendOutcome({ ok: true, status: 200 }, { sent: true });

    expect(outcome.tone).toBe('ok');
    // The endpoint answers identically for every address, so the copy is conditional.
    // "We sent you an email" would state more than the backend actually confirmed.
    expect(outcome.message).toContain('If that account still needs verifying');
    expect(outcome.message).not.toContain('We sent');
  });

  test('a rate-limited or failed resend says so and does not claim a link was sent', () => {
    const limited = describeResendOutcome({ ok: false, status: 429 }, { detail: 'Too many authentication attempts. Please retry shortly.' });
    expect(limited.tone).toBe('error');
    expect(limited.message).toBe('Too many authentication attempts. Please retry shortly.');

    const failed = describeResendOutcome({ ok: false, status: 503 }, {});
    expect(failed.tone).toBe('error');
    expect(failed.message).toContain('could not send');

    const unreachable = describeResendOutcome({ ok: false, status: 0 }, { detail: 'We could not reach the account service. Please try again shortly.' });
    expect(unreachable.tone).toBe('error');
    expect(unreachable.message).toContain('could not reach');
  });
});

// ---------------------------------------------------------------------------
// Wiring: the screen actually renders and uses the above
// ---------------------------------------------------------------------------
test.describe('the sign-in screen wires the state to real controls', () => {
  test('the unverified panel is its own branch, not another error line', () => {
    expect(signInSource).toContain('isEmailNotVerifiedError(submitError)');
    expect(signInSource).toContain('data-testid="email-verification-required"');
    expect(signInSource).toContain('{unverifiedEmail ? (');
    expect(signInSource).toContain('EMAIL_NOT_VERIFIED_HEADING');
    expect(signInSource).toContain('EMAIL_NOT_VERIFIED_BODY');
  });

  test('it offers the primary recovery action and a secondary way out', () => {
    expect(signInSource).toContain('handleResendVerification');
    expect(signInSource).toContain('formatVerificationResendLabel');
    expect(signInSource).toContain('Use another account');
    expect(signInSource).toContain('handleUseAnotherAccount');
  });

  test('the resend button is disabled while sending and during the cooldown', () => {
    expect(signInSource).toContain('disabled={resending || resendCooldown > 0}');
    expect(signInSource).toContain('VERIFICATION_RESEND_COOLDOWN_SECONDS');
    // The cooldown only starts once the request was actually accepted.
    expect(signInSource).toContain('if (result.ok) {');
  });

  test('the panel names the account it is about, taken from the refused sign-in', () => {
    expect(signInSource).toContain('setUnverifiedEmail(email.trim())');
    expect(signInSource).toContain('Account: {unverifiedEmail}');
  });

  test('"Use another account" clears the password as well as the state', () => {
    // Leaving the previous account's password in the field would hand it to whichever
    // address is typed next.
    const handler = signInSource.slice(signInSource.indexOf('function handleUseAnotherAccount'));
    expect(handler.slice(0, handler.indexOf('}'))).toContain("setPassword('')");
  });

  test('the auth context raises the coded error and exposes the resend action', () => {
    expect(authContextSource).toContain('isEmailNotVerifiedResponse(response.status, data)');
    expect(authContextSource).toContain('new AuthStateError(');
    expect(authContextSource).toContain('resendVerificationEmail');
    expect(authContextSource).toContain("'/api/auth/resend-verification'");
  });

  test('the resend request goes through the same-origin auth proxy', () => {
    // Never straight to the backend from the browser: the proxy is where the API URL,
    // and the backend rate limit keyed to it, actually live.
    expect(resendRouteSource).toContain("proxyAuthRequest(request, '/auth/resend-verification', 'POST')");
    expect(signInSource).not.toContain('fetch(`${apiUrl}/auth/resend-verification');
  });

  test('the panel does not print a token, a password, or account internals', () => {
    // Scoped to the panel. The screen as a whole now carries an invitation token
    // in a lookup URL — the Pilot invitation flow signs in through here — and a
    // file-wide /token=/ would flag that unrelated, non-rendered use.
    const panelStart = signInSource.indexOf('<div className="siVerifyPanel" data-testid="email-verification-required">');
    const panel = signInSource.slice(panelStart, signInSource.indexOf('</div>', signInSource.indexOf('Use another account')));
    expect(panelStart).toBeGreaterThan(-1);
    for (const pattern of [/token/, /password/, /email_verified_at/]) {
      expect(panel).not.toMatch(pattern);
    }
    // And nothing anywhere on this screen renders a raw credential field.
    for (const pattern of [/password_hash/, /email_verified_at/]) {
      expect(signInSource).not.toMatch(pattern);
    }
  });
});
