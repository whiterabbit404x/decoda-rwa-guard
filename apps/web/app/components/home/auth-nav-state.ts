// ─────────────────────────────────────────────────────────────
// Pure resolution rules for the public landing page's auth-aware
// navigation. No fetching happens here: the canonical session comes
// from PilotAuthProvider (/api/auth/me), and these helpers only turn
// that answer plus the server-side cookie hint into what the navbar
// and the primary CTA should render.
// ─────────────────────────────────────────────────────────────

import type { LandingSessionHint } from '../../auth-guards';

/**
 * - `checking`      — the canonical session check has not answered yet and a session
 *                     cookie was present at request time. Render a neutral placeholder;
 *                     never the signed-out controls, never the signed-in controls.
 * - `authenticated` — /api/auth/me returned a user.
 * - `anonymous`     — /api/auth/me answered without a user, OR no session cookie exists,
 *                     OR the session could not be verified at all. Fail-closed default.
 */
export type AuthNavState = 'checking' | 'authenticated' | 'anonymous';

export type StartMonitoringTarget = 'dashboard' | 'sign-up';

export type AuthNavStateInput = {
  /** PilotAuthProvider `loading` — runtime config + session restore still in flight. */
  loading: boolean;
  /** PilotAuthProvider `isAuthenticated` — true only when the backend returned a user. */
  isAuthenticated: boolean;
  sessionHint: LandingSessionHint;
};

export function resolveAuthNavState({ loading, isAuthenticated, sessionHint }: AuthNavStateInput): AuthNavState {
  if (!loading) {
    // The backend has answered. This is the only path that can report `authenticated`,
    // and a failed/unreachable session check leaves `isAuthenticated` false, so an
    // unverifiable session reads as signed out rather than as signed in.
    return isAuthenticated ? 'authenticated' : 'anonymous';
  }

  // Still resolving. A request that carried no session cookie cannot become
  // authenticated (the session check is cookie-based), so the signed-out controls are
  // already correct and render immediately with no flicker. A request that DID carry a
  // cookie waits in a neutral state instead of flashing "Sign in".
  return sessionHint === 'session-cookie-present' ? 'checking' : 'anonymous';
}

/**
 * Where the primary "Start monitoring" CTA points.
 *
 * An authenticated visitor is sent straight to the product instead of back through
 * sign-up. While the session is still being checked the CTA also points at /dashboard:
 * that route is protected and re-validates the session on both the server and the
 * client, so a stale cookie fails closed to /sign-in rather than exposing anything.
 */
export function resolveStartMonitoringTarget(state: AuthNavState): StartMonitoringTarget {
  return state === 'anonymous' ? 'sign-up' : 'dashboard';
}

type AccountIdentity = { full_name?: string | null; email?: string | null } | null | undefined;

/** Display name for the account menu. Falls back to the email only inside the menu. */
export function accountDisplayName(user: AccountIdentity): string {
  const fullName = user?.full_name?.trim();
  if (fullName) {
    return fullName;
  }
  const email = user?.email?.trim();
  return email || 'Your account';
}

/**
 * Avatar initials. The public navbar shows only these — the full address stays inside
 * the opened account menu so a public page never renders the customer's email in the
 * always-visible header.
 */
export function accountInitials(user: AccountIdentity): string {
  const fullName = user?.full_name?.trim();
  if (fullName) {
    const letters = fullName
      .split(/\s+/)
      .slice(0, 2)
      .map((word) => word[0] ?? '')
      .join('')
      .toUpperCase();
    if (letters) {
      return letters;
    }
  }

  const localPart = user?.email?.trim().split('@')[0] ?? '';
  const emailInitial = localPart.replace(/[^a-z0-9]/gi, '').slice(0, 2).toUpperCase();
  return emailInitial || 'U';
}
