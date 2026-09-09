// ─────────────────────────────────────────────────────────────
// The /sign-up gate: what the page says, and how it finds an invitation.
//
// Signing up is not being approved. Decoda RWA Guard evaluations are granted by
// review (see app/pilot-request-copy.ts and services/api/app/pilot_access.py),
// so the public /sign-up route offers account creation ONLY to someone holding
// an invitation the backend still recognises. Everyone else is told the truth —
// access is approval-only — and pointed at the application.
//
// Nothing here authorizes anything. The token this module extracts is a lookup
// key sent to the backend; the backend decides whether it resolves, to which
// address, and whether it may still be used. A token in the URL is never
// evidence on its own, and the address shown to the applicant is the one the
// backend returns, never one typed into the browser.
// ─────────────────────────────────────────────────────────────

/** The ungated state: no invitation, so no workspace-creation flow. */
export const APPROVAL_ONLY_HEADLINE = 'Pilot access is approval-only';
export const APPROVAL_ONLY_BODY =
  'Decoda RWA Guard evaluations are available by invitation. Creating an account here does not create a workspace, activate a Pilot, or start monitoring — Decoda reviews and approves each evaluation first.';

/** The two ways forward from the ungated state, and nothing else. */
export const REQUEST_PILOT_CTA = 'Request Pilot';
export const SIGN_IN_PROMPT = 'Already approved?';
export const SIGN_IN_CTA = 'Sign in';

/** The gated state: an invitation the backend accepted. */
export const INVITED_HEADLINE = 'Create your Decoda account';
export const INVITED_SUBTITLE =
  'Your Pilot evaluation is approved. Create the account for the approved address, then accept the invitation to activate it.';

/**
 * Shown while the invitation is being checked. The form is NOT rendered during
 * this state: an unverified token must never produce a usable signup form, even
 * for the moment before the answer arrives.
 */
export const CHECKING_HEADLINE = 'Checking your invitation…';

/** After the account exists but before the evaluation does. */
export const ACCOUNT_CREATED_HEADLINE = 'Verify your email to continue';
export const ACCOUNT_CREATED_BODY =
  'Your account was created. Verify your email address, sign in, and then accept your invitation — your evaluation starts when you accept it.';

/** Fallback when the backend declines to resolve the token. */
export const INVITATION_UNAVAILABLE =
  'This invitation link is not valid, has expired, or has already been used.';

/**
 * The invitation token carried by a /sign-up URL, or '' when there is none.
 *
 * Three shapes are accepted, because an approved applicant can arrive by more
 * than one route and a dead end would push them back to the public form:
 *
 *   /sign-up?invite=<token>                       the canonical link
 *   /sign-up?token=<token>                        the invitation URL's own param
 *   /sign-up?next=/accept-invitation?token=<t>    bounced from the accept page
 *
 * Only a same-origin `next` path is read, so a crafted absolute URL cannot make
 * this page describe or post to somewhere else.
 */
export function resolveInvitationToken(
  params: URLSearchParams | { get(name: string): string | null } | null | undefined,
): string {
  if (!params) {
    return '';
  }
  const direct = (params.get('invite') ?? params.get('token') ?? '').trim();
  if (direct) {
    return direct;
  }
  const next = (params.get('next') ?? '').trim();
  if (!next.startsWith('/')) {
    return '';
  }
  const queryAt = next.indexOf('?');
  if (queryAt < 0) {
    return '';
  }
  const path = next.slice(0, queryAt);
  if (path !== '/accept-invitation') {
    return '';
  }
  return (new URLSearchParams(next.slice(queryAt + 1)).get('token') ?? '').trim();
}

/** Where an applicant goes to activate: the one activation path, not a new one. */
export function acceptInvitationPath(token: string): string {
  return `/accept-invitation?token=${encodeURIComponent(token)}`;
}
