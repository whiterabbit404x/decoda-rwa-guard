import type { RuntimeConfig } from './runtime-config-schema';

/**
 * Canonical name of the HttpOnly session cookie the auth proxy
 * (app/api/auth/_shared/proxy.ts) sets on sign-in and clears on sign-out.
 */
export const SESSION_COOKIE_NAME = 'decoda_session';

export function shouldRedirectUnauthenticatedProductAccess(
  token: string | undefined,
  runtimeConfig: Pick<RuntimeConfig, 'liveModeEnabled'>,
): boolean {
  return runtimeConfig.liveModeEnabled && !token;
}

/**
 * Server-side session HINT for public (unprotected) pages.
 *
 * This is deliberately NOT an authentication decision. Cookie presence only tells the
 * server whether a session MIGHT exist, so it is used for exactly one thing: deciding
 * whether an auth-dependent control starts in a neutral "checking" state instead of
 * flashing the signed-out controls before the canonical session check resolves.
 *
 * Authentication itself stays with the backend: PilotAuthProvider restores the session
 * through /api/auth/me, and only that answer may render an authenticated state. A stale
 * or forged cookie therefore never produces a signed-in UI — it fails closed to the
 * signed-out controls once /api/auth/me answers.
 */
export type LandingSessionHint = 'session-cookie-present' | 'no-session-cookie';

export function readLandingSessionHint(token: string | undefined | null): LandingSessionHint {
  return token && token.trim() ? 'session-cookie-present' : 'no-session-cookie';
}
