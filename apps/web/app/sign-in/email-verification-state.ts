/**
 * The one sign-in refusal a user can resolve on their own.
 *
 * A wrong password is a dead end by design. An unverified address is not: the account
 * and the password are both correct, and the only missing piece is proof of the inbox.
 * Presented as a bare sentence ("Verify your email before signing in.") that state left
 * the user with nothing to click, so this module gives the screen the two things it
 * needs — a reliable way to RECOGNISE the state, and copy that names the next step.
 *
 * Recognition is by the backend's error CODE, never by matching the sentence shown to
 * the customer: copy changes, and a screen that branches on prose would silently offer
 * the wrong recovery action the day it does.
 */

/** Backend code (services/api/app/pilot.py: EMAIL_NOT_VERIFIED_CODE). */
export const EMAIL_NOT_VERIFIED_CODE = 'EMAIL_NOT_VERIFIED';

export const EMAIL_NOT_VERIFIED_HEADING = 'Verify your email to continue';

export const EMAIL_NOT_VERIFIED_BODY =
  'This account exists, but its email address has not been verified yet. '
  + 'Send yourself a new verification link to finish signing in.';

/**
 * True only for the unverified-email refusal.
 *
 * Every other 403 — a suspension, a CSRF rejection — is deliberately excluded: those
 * are not resolved by resending a verification link, and offering that action would
 * tell the user something untrue about why they were refused.
 */
export function isEmailNotVerifiedResponse(status: number, body: unknown): boolean {
  if (status !== 403) {
    return false;
  }

  const payload = (body && typeof body === 'object' ? body : {}) as Record<string, unknown>;
  return payload.code === EMAIL_NOT_VERIFIED_CODE;
}

/** Carries the backend's error code alongside the message shown to the user. */
export class AuthStateError extends Error {
  readonly code: string;

  constructor(message: string, code: string) {
    super(message);
    this.name = 'AuthStateError';
    this.code = code;
  }
}

/** True when a thrown value is the unverified-email state. */
export function isEmailNotVerifiedError(error: unknown): boolean {
  return error instanceof AuthStateError && error.code === EMAIL_NOT_VERIFIED_CODE;
}

/** Seconds before "Resend verification email" becomes available again. */
export const VERIFICATION_RESEND_COOLDOWN_SECONDS = 30;

export function formatVerificationResendLabel(secondsRemaining: number, sending: boolean): string {
  if (sending) {
    return 'Sending...';
  }
  if (secondsRemaining <= 0) {
    return 'Resend verification email';
  }
  const seconds = Math.ceil(secondsRemaining);
  return `Resend available in ${seconds} second${seconds === 1 ? '' : 's'}`;
}

export type ResendOutcome = { tone: 'ok' | 'error'; message: string };

/**
 * What to tell the user after a resend attempt.
 *
 * The success line is deliberately conditional ("if that account still needs
 * verifying"): the endpoint is unauthenticated and answers identically for every
 * address, so promising that a message was sent would state more than the response
 * actually establishes.
 */
export function describeResendOutcome(response: { ok: boolean; status?: number }, body: unknown): ResendOutcome {
  const payload = (body && typeof body === 'object' ? body : {}) as Record<string, unknown>;
  const detail = typeof payload.detail === 'string' && payload.detail.trim() ? payload.detail.trim() : '';

  if (response.ok) {
    return {
      tone: 'ok',
      message: 'If that account still needs verifying, a new link is on its way. Check your inbox and spam folder.',
    };
  }

  if (response.status === 429) {
    return {
      tone: 'error',
      message: detail || 'Too many requests. Wait a moment before asking for another link.',
    };
  }

  return {
    tone: 'error',
    message: detail || 'We could not send a verification link just now. Please try again shortly.',
  };
}
