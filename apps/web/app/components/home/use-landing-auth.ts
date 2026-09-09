'use client';

import { usePilotAuth, type PilotUser } from 'app/pilot-auth-context';

import type { LandingSessionHint } from '../../auth-guards';
import { resolveAuthNavState, type AuthNavState } from './auth-nav-state';

/**
 * The landing page's single read of the canonical session.
 *
 * Everything auth-aware on the public homepage (navbar, mobile menu, primary CTA) goes
 * through this hook, so no homepage surface fetches or caches session state of its own.
 * It reuses PilotAuthProvider — the same provider the dashboard and the protected route
 * guard use — rather than introducing a second authentication state system.
 */
export function useLandingAuth(sessionHint: LandingSessionHint): {
  state: AuthNavState;
  user: PilotUser | null;
  signOut: () => Promise<void>;
} {
  const { loading, isAuthenticated, user, signOut } = usePilotAuth();

  return {
    state: resolveAuthNavState({ loading, isAuthenticated, sessionHint }),
    user,
    signOut,
  };
}
