/**
 * Deterministic rules for the Forgot Password -> Reset Password hand-off.
 *
 * Two properties this module exists to guarantee:
 *
 *  1. A reset is only ever requested for an address the user actually supplied —
 *     typed on Sign In and carried across in the query string, or typed on the
 *     reset page itself. There is NO fallback, demo, or pilot account: when no
 *     address was supplied the field stays blank and no request is sent.
 *
 *  2. The final password change is authorized ONLY by the single-use, expiring
 *     token delivered in the reset email. The email field and the `email` query
 *     param are addressing information for the *request* step; they never appear
 *     in the reset submission and never authorize a password change.
 */

export const RESET_PASSWORD_PATH = '/reset-password';

// RFC 5321 practical maximum. A longer value is a malformed/attacker-supplied
// query param, not an address a user typed, so it resolves to blank.
const MAX_EMAIL_LENGTH = 254;
const EMAIL_SHAPE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

/**
 * The supplied address, or '' when nothing usable was supplied. Never substitutes
 * a default account for a missing or malformed value.
 */
export function normalizeResetEmail(value: string | null | undefined): string {
  if (typeof value !== 'string') {
    return '';
  }

  const trimmed = value.trim();
  if (!trimmed || trimmed.length > MAX_EMAIL_LENGTH || !EMAIL_SHAPE.test(trimmed)) {
    return '';
  }

  return trimmed;
}

/**
 * Target for the Sign In "Forgot password?" link. Carries the address the user
 * already typed so the reset page opens against THEIR account, and degrades to a
 * bare path (blank field) when the sign-in email box is empty or incomplete.
 */
export function buildResetPasswordHref(email: string | null | undefined): string {
  const normalized = normalizeResetEmail(email);
  if (!normalized) {
    return RESET_PASSWORD_PATH;
  }

  return `${RESET_PASSWORD_PATH}?email=${encodeURIComponent(normalized)}`;
}

/**
 * Initial value for the reset page's email field. A direct visit with no `email`
 * param yields '' — a blank field the user fills in themselves.
 */
export function resolveResetRequestEmail(param: string | null | undefined): string {
  return normalizeResetEmail(param);
}

/**
 * Body for POST /api/auth/forgot-password, or null when there is nothing to send.
 * The address sent is exactly the one submitted — nothing is substituted.
 */
export function buildResetRequestPayload(email: string | null | undefined): { email: string } | null {
  const normalized = normalizeResetEmail(email);
  return normalized ? { email: normalized } : null;
}

/**
 * Body for POST /api/auth/reset-password, or null when the request is not
 * authorized to proceed. Carries the reset token and the new password ONLY:
 * an email address can never stand in for a missing token.
 */
export function buildResetSubmissionPayload(
  token: string | null | undefined,
  password: string,
): { token: string; password: string } | null {
  const normalizedToken = typeof token === 'string' ? token.trim() : '';
  if (!normalizedToken || !password) {
    return null;
  }

  return { token: normalizedToken, password };
}
