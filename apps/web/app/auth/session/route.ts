/**
 * GET /auth/session — turn the browser's verified AuthKit session into an RWA
 * Guard session, then continue to a validated same-site path.
 *
 * Reached after the AuthKit callback, after an expired RWA Guard session (the
 * AuthKit session is still live), and after a Decoda step-up. The Guard API
 * re-verifies the access token, asks WorkOS whether the session is live and
 * checks the organization's membership and RWA Guard entitlement on the Decoda
 * platform before issuing anything.
 *
 * Any previous RWA Guard session cookie is replaced or cleared here, so a
 * browser that changed hands never keeps the previous person's session.
 */
import { withAuth } from '@workos-inc/authkit-nextjs';
import { type NextRequest, NextResponse } from 'next/server';

import {
  clearAuthKitCookie,
  clearSessionCookies,
  decodaSignInEnabled,
  exchangeFailurePath,
  exchangeIdentity,
  guardIdentityMode,
  publicUrl,
  secureCookiesFor,
  setSessionCookies,
} from '../../decoda-identity';
import { safeNextPath } from '../../decoda-identity-shared';

export const dynamic = 'force-dynamic';

function go(request: NextRequest, path: string): NextResponse {
  return NextResponse.redirect(publicUrl(path, request), 303);
}

export async function GET(request: NextRequest) {
  const next = safeNextPath(request.nextUrl.searchParams.get('next'));
  if (guardIdentityMode() === 'legacy') return go(request, '/sign-in');
  if (!decodaSignInEnabled()) return go(request, '/access?reason=unavailable');

  const auth = await withAuth();
  if (!auth.user || !auth.accessToken) {
    const response = go(request, `/auth/sign-in?next=${encodeURIComponent(next)}`);
    clearSessionCookies(response);
    return response;
  }

  const outcome = await exchangeIdentity(auth.accessToken, request);
  if (outcome.ok) {
    const response = go(request, next);
    setSessionCookies(response, outcome.accessToken, secureCookiesFor(request));
    return response;
  }

  console.warn('[guard:identity] session exchange refused', { status: outcome.status, code: outcome.code });
  const response = go(request, exchangeFailurePath(outcome));
  clearSessionCookies(response);
  // A stale or invalid identity must sign in again; keeping the AuthKit cookie
  // would send the browser straight back here.
  if (outcome.status === 401) clearAuthKitCookie(response, request);
  return response;
}
