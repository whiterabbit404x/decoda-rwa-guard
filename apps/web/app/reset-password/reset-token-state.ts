/**
 * The state machine behind /reset-password.
 *
 * Kept as pure functions, separate from the React components, so that the rules the
 * screen depends on — which step is shown, whether a link may still be used, whether
 * the password form is allowed to submit — are decided in one place and can be tested
 * without rendering anything.
 *
 * The governing rule: a token in the URL is a *claim*, not proof. Nothing about the
 * password form is enabled until the API has confirmed the link is still usable, and
 * an unrecognised or unreachable answer resolves to a blocked state, never an open one.
 */

/** Which of the three steps the page is on, decided by the URL alone. */
export type ResetFlowStep = 'request' | 'sent' | 'reset';

export type ResetTokenStatus = 'validating' | 'valid' | 'expired' | 'used' | 'invalid' | 'error';

export type ResetTokenState = {
  status: ResetTokenStatus;
  /** Account the token belongs to, resolved by the backend. Never from the URL. */
  email: string | null;
};

export const VALIDATING_STATE: ResetTokenState = { status: 'validating', email: null };

/**
 * A link opened from a reset email lands on the password step; every other visit
 * starts at the request step. There is no visit that shows both.
 */
export function resolveResetFlowStep(token: string | null | undefined): ResetFlowStep {
  return typeof token === 'string' && token.trim() ? 'reset' : 'request';
}

/** Body for POST /api/auth/reset-password/validate, or null when there is no token. */
export function buildResetValidationPayload(token: string | null | undefined): { token: string } | null {
  const normalized = typeof token === 'string' ? token.trim() : '';
  return normalized ? { token: normalized } : null;
}

/**
 * Interpret the validation response.
 *
 * Only the four states the API actually reports are trusted. Anything else — an HTTP
 * error, an unparseable body, a status string a future backend might add — becomes
 * `error`, which blocks the form and offers a retry rather than silently presenting a
 * password box that cannot work.
 */
export function interpretTokenValidation(response: { ok: boolean; status?: number }, body: unknown): ResetTokenState {
  const payload = (body && typeof body === 'object' ? body : {}) as Record<string, unknown>;

  if (!response.ok) {
    // 4xx from this endpoint means the link itself was rejected; anything else is
    // the service being unreachable, which is not the user's link being bad.
    const httpStatus = typeof response.status === 'number' ? response.status : 0;
    if (httpStatus >= 400 && httpStatus < 500) {
      return { status: 'invalid', email: null };
    }
    return { status: 'error', email: null };
  }

  const reported = typeof payload.status === 'string' ? payload.status : '';
  const email = typeof payload.email === 'string' && payload.email ? payload.email : null;

  if (reported === 'valid') {
    // A "valid" answer without an account to name is not a state we can act on.
    return email ? { status: 'valid', email } : { status: 'error', email: null };
  }
  if (reported === 'expired' || reported === 'used' || reported === 'invalid') {
    return { status: reported, email: null };
  }
  return { status: 'error', email: null };
}

/** True only for the one state in which a password may be submitted. */
export function canSubmitNewPassword(state: ResetTokenState): boolean {
  return state.status === 'valid';
}

export type ResetLinkProblem = {
  heading: string;
  body: string;
  /** Offer a fresh reset request; false for states where that is not the fix. */
  offerNewLink: boolean;
  retryable: boolean;
};

/**
 * Customer-facing copy for a link that cannot be used. Each state says what happened
 * and what to do next; none of them exposes token internals, and none of them tells
 * the user a blocked link might still work.
 */
export function describeResetLinkProblem(state: ResetTokenState): ResetLinkProblem | null {
  switch (state.status) {
    case 'expired':
      return {
        heading: 'Reset link expired',
        body: 'This password reset link is no longer valid. Request a new reset link to continue.',
        offerNewLink: true,
        retryable: false,
      };
    case 'used':
      return {
        heading: 'Reset link already used',
        body: 'This reset link has already been used. Sign in with your new password, or request another reset link.',
        offerNewLink: true,
        retryable: false,
      };
    case 'invalid':
      return {
        heading: 'Invalid reset link',
        body: 'We could not verify this password reset link. Request a new reset link to continue.',
        offerNewLink: true,
        retryable: false,
      };
    case 'error':
      return {
        heading: 'Could not verify this link',
        body: 'We could not reach the account service to verify your reset link. Check your connection and try again.',
        offerNewLink: true,
        retryable: true,
      };
    default:
      return null;
  }
}

/** Seconds a user must wait before "Resend reset email" becomes available again. */
export const RESEND_COOLDOWN_SECONDS = 30;

export function formatResendCooldown(secondsRemaining: number): string {
  if (secondsRemaining <= 0) {
    return 'Resend reset email';
  }
  const seconds = Math.ceil(secondsRemaining);
  return `Resend available in ${seconds} second${seconds === 1 ? '' : 's'}`;
}
