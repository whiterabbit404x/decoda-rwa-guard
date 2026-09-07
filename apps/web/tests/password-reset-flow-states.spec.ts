/**
 * The three states of /reset-password, and the rules that keep them apart.
 *
 * Covers the redesign's core promise: requesting a reset link and completing a reset
 * are separate steps, a token in the URL is never assumed valid, and no password
 * control is reachable until the API has confirmed the link.
 *
 * Follows this suite's convention — pure state/presentation logic is exercised
 * directly, and the wiring that connects it to the rendered screen is asserted
 * against the component sources.
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { expect, test } from '@playwright/test';

import {
  RESEND_COOLDOWN_SECONDS,
  buildResetValidationPayload,
  canSubmitNewPassword,
  describeResetLinkProblem,
  formatResendCooldown,
  interpretTokenValidation,
  resolveResetFlowStep,
  type ResetTokenState,
} from '../app/reset-password/reset-token-state';

const componentSource = (name: string) =>
  readFileSync(path.resolve(__dirname, `../app/reset-password/components/${name}`), 'utf8');

const clientSource = readFileSync(
  path.resolve(__dirname, '../app/reset-password/reset-password-client.tsx'),
  'utf8',
);
const requestFormSource = componentSource('request-reset-form.tsx');
const sentStateSource = componentSource('reset-email-sent-state.tsx');
const newPasswordFormSource = componentSource('set-new-password-form.tsx');
const validatingSource = componentSource('reset-token-validating.tsx');
const linkErrorSource = componentSource('reset-link-error-state.tsx');
const successSource = componentSource('reset-success-state.tsx');
const shellSource = componentSource('reset-password-shell.tsx');

const VALID_STATE: ResetTokenState = { status: 'valid', email: 'thanhdat852@gmail.com' };

// ---------------------------------------------------------------------------
// Which step the URL selects
// ---------------------------------------------------------------------------
test.describe('the url alone decides which recovery step is shown', () => {
  test('a bare visit, or one carrying only an email, is the request step', () => {
    expect(resolveResetFlowStep(null)).toBe('request');
    expect(resolveResetFlowStep(undefined)).toBe('request');
    expect(resolveResetFlowStep('')).toBe('request');
    expect(resolveResetFlowStep('   ')).toBe('request');
  });

  test('a link from a reset email is the new-password step', () => {
    const url = new URL('/reset-password?token=valid-token', 'https://rwa.decodasecurity.com');

    expect(resolveResetFlowStep(url.searchParams.get('token'))).toBe('reset');
  });

  test('the token screen never renders the send-reset-email form', () => {
    // The regression this replaces: both panels on screen at once, so a user
    // arriving from their inbox saw an email box they had no reason to fill in.
    expect(newPasswordFormSource).not.toContain('Send reset link');
    expect(newPasswordFormSource).not.toContain('forgot-password');
    expect(linkErrorSource).not.toContain('forgot-password');
    expect(successSource).not.toContain('forgot-password');

    // And the two flows are separate branches of the page, not siblings in one grid.
    expect(clientSource).toContain("return step === 'reset' ? (");
    expect(clientSource).not.toContain('twoColumnSection');
  });

  test('the request screen never renders the new-password form', () => {
    expect(requestFormSource).not.toContain('Reset password');
    expect(requestFormSource).not.toContain("type={showPassword ? 'text' : 'password'}");
    expect(requestFormSource).not.toContain('/api/auth/reset-password');
  });
});

// ---------------------------------------------------------------------------
// Token validation
// ---------------------------------------------------------------------------
test.describe('a token in the url is a claim, not proof', () => {
  test('the page starts in validating and asks the backend before showing a form', () => {
    expect(clientSource).toContain('useState<ResetTokenState>(VALIDATING_STATE)');
    expect(clientSource).toContain("fetch('/api/auth/reset-password/validate'");
    expect(validatingSource).toContain('Verifying reset link...');
    expect(validatingSource).toContain('aria-busy="true"');
    // The verifying screen offers no password controls at all.
    expect(validatingSource).not.toContain('<input');
  });

  test('only the validating state renders a spinner, and only valid renders the form', () => {
    expect(canSubmitNewPassword({ status: 'valid', email: 'user@example.com' })).toBe(true);

    for (const status of ['validating', 'expired', 'used', 'invalid', 'error'] as const) {
      expect(canSubmitNewPassword({ status, email: null })).toBe(false);
    }
  });

  test('a validated token yields the account the backend resolved', () => {
    const state = interpretTokenValidation(
      { ok: true, status: 200 },
      { status: 'valid', email: 'thanhdat852@gmail.com' },
    );

    expect(state).toEqual({ status: 'valid', email: 'thanhdat852@gmail.com' });
  });

  test('expired, used, and invalid answers are carried through as their own states', () => {
    for (const reported of ['expired', 'used', 'invalid'] as const) {
      expect(interpretTokenValidation({ ok: true, status: 200 }, { status: reported })).toEqual({
        status: reported,
        email: null,
      });
    }
  });

  test('anything unrecognised fails closed rather than opening the form', () => {
    const unusable = [
      [{ ok: true, status: 200 }, { status: 'probably-fine' }],
      [{ ok: true, status: 200 }, {}],
      [{ ok: true, status: 200 }, null],
      [{ ok: true, status: 200 }, 'not json'],
      // A "valid" answer with no account to name cannot be acted on.
      [{ ok: true, status: 200 }, { status: 'valid' }],
      [{ ok: true, status: 200 }, { status: 'valid', email: '' }],
    ] as const;

    for (const [response, body] of unusable) {
      const state = interpretTokenValidation(response, body);
      expect(state.status).not.toBe('valid');
      expect(canSubmitNewPassword(state)).toBe(false);
    }
  });

  test('a rejected link reads as invalid; an unreachable service reads as an error', () => {
    expect(interpretTokenValidation({ ok: false, status: 400 }, {})).toEqual({ status: 'invalid', email: null });
    expect(interpretTokenValidation({ ok: false, status: 404 }, {})).toEqual({ status: 'invalid', email: null });
    // 5xx is the service failing, not the user's link being bad — and must not be
    // reported to the user as an expired or invalid link.
    expect(interpretTokenValidation({ ok: false, status: 500 }, {})).toEqual({ status: 'error', email: null });
    expect(interpretTokenValidation({ ok: false, status: 503 }, {})).toEqual({ status: 'error', email: null });
  });

  test('validation never leaks token material back into the page', () => {
    expect(buildResetValidationPayload('reset-token-123')).toEqual({ token: 'reset-token-123' });
    expect(buildResetValidationPayload('  reset-token-123  ')).toEqual({ token: 'reset-token-123' });
    expect(buildResetValidationPayload('')).toBeNull();
    expect(buildResetValidationPayload(null)).toBeNull();

    for (const state of ['expired', 'used', 'invalid', 'error'] as const) {
      const problem = describeResetLinkProblem({ status: state, email: null });
      expect(problem).not.toBeNull();
      expect(`${problem?.heading} ${problem?.body}`).not.toMatch(/token|hash|user[_ ]?id/i);
    }
  });
});

// ---------------------------------------------------------------------------
// Blocked-link screens
// ---------------------------------------------------------------------------
test.describe('a link that cannot be used says so, and says what to do next', () => {
  test('expired links get their own card and a way to get a new one', () => {
    const problem = describeResetLinkProblem({ status: 'expired', email: null });

    expect(problem?.heading).toBe('Reset link expired');
    expect(problem?.body).toContain('no longer valid');
    expect(problem?.offerNewLink).toBe(true);
    expect(problem?.retryable).toBe(false);
  });

  test('invalid links are named as invalid, not as expired', () => {
    const problem = describeResetLinkProblem({ status: 'invalid', email: null });

    expect(problem?.heading).toBe('Invalid reset link');
    expect(problem?.offerNewLink).toBe(true);
  });

  test('an already-used link says so and points at sign-in', () => {
    const problem = describeResetLinkProblem({ status: 'used', email: null });

    expect(problem?.heading).toBe('Reset link already used');
    expect(problem?.body).toContain('already been used');
    expect(problem?.offerNewLink).toBe(true);
    expect(linkErrorSource).toContain('Back to sign in');
  });

  test('a service failure offers a retry instead of blaming the link', () => {
    const problem = describeResetLinkProblem({ status: 'error', email: null });

    expect(problem?.retryable).toBe(true);
    expect(problem?.body).not.toContain('expired');
    expect(problem?.body).not.toContain('invalid');
    expect(linkErrorSource).toContain('Try again');
  });

  test('valid and validating are not problems', () => {
    expect(describeResetLinkProblem(VALID_STATE)).toBeNull();
    expect(describeResetLinkProblem({ status: 'validating', email: null })).toBeNull();
  });

  test('a blocked link screen renders no password inputs', () => {
    expect(linkErrorSource).not.toContain('<input');
    expect(linkErrorSource).not.toContain('/api/auth/reset-password');
  });
});

// ---------------------------------------------------------------------------
// Check-your-email step
// ---------------------------------------------------------------------------
test.describe('the check-your-email step', () => {
  test('its wording does not disclose whether the account exists', () => {
    expect(sentStateSource).toContain('If an account exists for');
    expect(sentStateSource).not.toMatch(/we (sent|have sent) (you|your)/i);
    expect(sentStateSource).toContain("Check your spam or junk folder");
  });

  test('a resend cooldown is stated in the button itself', () => {
    expect(RESEND_COOLDOWN_SECONDS).toBeGreaterThan(0);
    expect(formatResendCooldown(RESEND_COOLDOWN_SECONDS)).toBe('Resend available in 30 seconds');
    expect(formatResendCooldown(1)).toBe('Resend available in 1 second');
    expect(formatResendCooldown(0)).toBe('Resend reset email');
    expect(formatResendCooldown(-5)).toBe('Resend reset email');
    expect(sentStateSource).toContain('disabled={cooling || resending}');
  });

  test('"use a different email" returns to a blank request screen', () => {
    expect(sentStateSource).toContain('Use a different email');
    // Clearing the carried address from the URL as well, so the page cannot reopen
    // pointed at an address the current user did not type.
    expect(clientSource).toContain('router.replace(RESET_PASSWORD_PATH)');
  });
});

// ---------------------------------------------------------------------------
// Submitting, success, and duplicate submission
// ---------------------------------------------------------------------------
test.describe('submitting a new password', () => {
  test('a submission in flight blocks a second one', () => {
    expect(clientSource).toContain('if (submitting || !canSubmitNewPassword(tokenState)) return;');
    expect(clientSource).toContain('if (submitting) return;');
    expect(newPasswordFormSource).toContain('disabled={submitting}');
    expect(newPasswordFormSource).toContain('aria-busy={submitting}');
    expect(newPasswordFormSource).toContain("{submitting ? 'Resetting...' : 'Reset password'}");
    expect(requestFormSource).toContain("{submitting ? 'Sending...' : 'Send reset link'}");
  });

  test('a link rejected at submit time drops back to the blocked-link screen', () => {
    // Otherwise the user is left retyping a password into a form that cannot succeed.
    expect(clientSource).toContain("setTokenState({ status: 'invalid', email: null });");
  });

  test('success is a screen of its own, not a status line under the form', () => {
    expect(successSource).toContain('Password updated');
    expect(successSource).toContain('Your password has been reset successfully.');
    expect(successSource).toContain('Continue to sign in');
    expect(successSource).toContain('Redirecting to sign in...');
    expect(successSource).not.toContain('<input');
    expect(clientSource).toContain('<ResetSuccessState redirecting />');
  });

  test('a completed reset does not sign the user in', () => {
    // The backend revokes sessions on reset; turning the link into a session would
    // make an intercepted reset email a login.
    expect(clientSource).not.toContain('/api/auth/signin');
    expect(successSource).not.toContain('/api/auth/signin');
    expect(clientSource).toContain("router.push('/sign-in')");
  });
});

// ---------------------------------------------------------------------------
// Accessibility
// ---------------------------------------------------------------------------
test.describe('the recovery screens are operable without a mouse', () => {
  test('every field has a label bound to its own control', () => {
    for (const [source, ids] of [
      [requestFormSource, ['reset-request-email']],
      [newPasswordFormSource, ['reset-new-password', 'reset-confirm-password', 'reset-account-email']],
    ] as const) {
      for (const id of ids) {
        expect(source).toContain(`htmlFor="${id}"`);
        expect(source).toContain(`id="${id}"`);
      }
    }
  });

  test('password visibility toggles are real buttons with a stateful label', () => {
    expect(newPasswordFormSource).toContain("aria-label={showPassword ? 'Hide new password' : 'Show new password'}");
    expect(newPasswordFormSource).toContain(
      "aria-label={showConfirmation ? 'Hide password confirmation' : 'Show password confirmation'}",
    );
    expect(newPasswordFormSource).toContain('aria-pressed={showPassword}');
    expect(newPasswordFormSource).toContain('aria-pressed={showConfirmation}');
    // type="button" keeps a toggle from submitting the form when activated by keyboard.
    const toggles = newPasswordFormSource.match(/className="siPasswordToggle"/g) ?? [];
    expect(toggles).toHaveLength(2);
    expect(newPasswordFormSource.match(/type="button"/g)?.length).toBeGreaterThanOrEqual(2);
  });

  test('both screens submit on Enter because they are forms, not click handlers', () => {
    for (const source of [requestFormSource, newPasswordFormSource]) {
      expect(source).toContain('<form');
      expect(source).toContain('type="submit"');
      expect(source).toContain('event.preventDefault();');
    }
  });

  test('errors are announced and tied to the field they describe', () => {
    expect(requestFormSource).toContain('id="reset-request-error"');
    expect(requestFormSource).toContain("aria-describedby={error ? 'reset-request-error' : undefined}");
    expect(requestFormSource).toContain('role="alert"');
    expect(newPasswordFormSource).toContain('id="reset-password-error"');
    expect(newPasswordFormSource).toContain("aria-describedby={error ? 'reset-password-error' : undefined}");
    expect(newPasswordFormSource).toContain('role="alert"');
  });

  test('the step indicator names the current step for assistive technology', () => {
    expect(shellSource).toContain("aria-current={state === 'active' ? 'step' : undefined}");
    expect(shellSource).toContain('Request link');
    expect(shellSource).toContain('Check email');
    expect(shellSource).toContain('Create new password');
  });
});
