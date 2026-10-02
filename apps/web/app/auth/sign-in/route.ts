/**
 * GET /auth/sign-in — "Sign in with Decoda". RWA Guard's Initiate login URI in WorkOS.
 *
 * Starts the shared AuthKit flow (PKCE + sealed state). After the callback the
 * browser lands on /auth/session, which exchanges the verified sign-in for an
 * RWA Guard session and continues to a validated same-site path.
 *
 * `?reauth=1` is the Decoda step-up: WorkOS is asked to authenticate the
 * person again now (OIDC max_age=0) — with the MFA the Decoda environment
 * enforces — so the new session's authentication time satisfies RWA Guard's
 * recent-authentication gates. RWA Guard itself never asks for a second
 * factor from a Decoda session.
 */
import { getSignInUrl } from '@workos-inc/authkit-nextjs';
import { redirect } from 'next/navigation';
import type { NextRequest } from 'next/server';

import { decodaSignInEnabled, guardIdentityMode, workosConfigured } from '../../decoda-identity';
import { safeNextPath } from '../../decoda-identity-shared';

export const dynamic = 'force-dynamic';

export async function GET(request: NextRequest) {
  if (guardIdentityMode() === 'legacy') redirect('/sign-in');
  if (!workosConfigured() || !decodaSignInEnabled()) {
    console.error('[guard:identity] WorkOS is not configured; Decoda sign-in is unavailable');
    redirect('/access?reason=unavailable');
  }
  const next = safeNextPath(request.nextUrl.searchParams.get('next'));
  const reauthenticate = request.nextUrl.searchParams.get('reauth') === '1';
  const signInUrl = await getSignInUrl({
    returnTo: `/auth/session?next=${encodeURIComponent(next)}`,
    ...(reauthenticate ? { maxAge: 0 } : {}),
  });
  redirect(signInUrl);
}
