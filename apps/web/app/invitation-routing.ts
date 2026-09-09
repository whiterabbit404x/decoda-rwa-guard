// ─────────────────────────────────────────────────────────────
// Where an approved Pilot invitation sends the person holding it.
//
// The bug this module exists to prevent: every route out of the invitation email
// led to /sign-in, including for a brand-new applicant who has never had a
// Decoda account and therefore has no password. They arrived at "Invalid email
// or password" with nothing to do about it.
//
// The decision is NOT made here. `account_exists` is a database fact answered by
// the backend (GET /pilot-invitations → tenancy.endpoints.lookup_pilot_invitation),
// for a token that still resolves, about the address that invitation already
// names. This module only turns that answer into a URL, and carries the
// invitation token across every hop so no screen in the chain can drop it.
//
// Fail closed: an invitation the backend did not confirm routes nowhere. There
// is no destination for an unresolved token — an unverified invitation must
// never produce a usable account-creation form.
// ─────────────────────────────────────────────────────────────

/** The one activation path. Every other route in this file eventually returns here. */
export function acceptInvitationPath(token: string): string {
  return `/accept-invitation?token=${encodeURIComponent(token)}`;
}

/**
 * Invitation-aware signup, for an approved address with no account yet.
 *
 * `invite` is the canonical parameter; /sign-up reads the token from it, checks
 * it with the backend, and renders the account form only if the backend still
 * recognises it.
 */
export function invitationSignUpHref(token: string): string {
  return `/sign-up?invite=${encodeURIComponent(token)}`;
}

/**
 * Sign-in, for an approved address that already has an account.
 *
 * Carries the invitation twice on purpose: `invite` so the screen can say what
 * the sign-in is FOR and keep the "Create one" link inside the invitation flow,
 * and `next` so the post-authentication redirect lands on activation rather than
 * on a dashboard the account cannot use yet. MFA does not navigate away from
 * /sign-in, so both survive the challenge.
 */
export function invitationSignInHref(token: string): string {
  return `/sign-in?invite=${encodeURIComponent(token)}&next=${encodeURIComponent(acceptInvitationPath(token))}`;
}

/** What the backend said about the token in the URL. */
export type InvitationLookup = {
  valid: boolean;
  account_exists?: boolean | null;
};

/**
 * Where an UNAUTHENTICATED holder of this invitation belongs.
 *
 * `null` means "nowhere": the invitation did not resolve, so the page states the
 * backend's reason instead of routing anyone into a form that cannot succeed.
 *
 * `account_exists` missing or null is treated as "an account exists", the same
 * fail-closed answer the backend's own probe gives when it cannot read. Sign-in
 * reports its outcome honestly to someone who turns out to have no account;
 * a signup form that will 409 on submit does not.
 */
export function invitationDestination(
  lookup: InvitationLookup | null | undefined,
  token: string,
): string | null {
  if (!lookup || !lookup.valid || !token) {
    return null;
  }
  return lookup.account_exists === false ? invitationSignUpHref(token) : invitationSignInHref(token);
}
