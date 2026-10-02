import { getWorkOS, withAuth } from '@workos-inc/authkit-nextjs';
import { type NextRequest, NextResponse } from 'next/server';

import { proxyAuthRequest } from 'app/api/auth/_shared/proxy';
import { SESSION_COOKIE, clearAuthKitCookie, clearSessionCookies, decodaSignInEnabled, isSameOrigin, jsonError, publicUrl } from 'app/decoda-identity';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

/**
 * POST /api/auth/signout — end the RWA Guard session.
 *
 * The Guard session is revoked server-side (the API ends it even when the
 * Decoda platform currently refuses it) and the cookies are cleared. With the
 * shared Decoda identity the answer also carries the WorkOS logout URL, which
 * the page navigates to so WorkOS ends the Decoda session; WorkOS then returns
 * the browser to /sign-in?signed_out=1 (the sign-out redirect registered for
 * RWA Guard).
 */
export async function POST(request: NextRequest) {
  if (!decodaSignInEnabled()) {
    return proxyAuthRequest(request, '/auth/signout', 'POST', { requireAuth: true, cookieAction: 'clear-session' });
  }
  if (!isSameOrigin(request)) return jsonError(403, 'origin_rejected', 'Cross-origin requests are not allowed.');
  if (request.cookies.get(SESSION_COOKIE)?.value) {
    // Revoking the RWA Guard session is CSRF-checked like every other mutation.
    const proxied = await proxyAuthRequest(request, '/auth/signout', 'POST', { requireAuth: true, cookieAction: 'clear-session' });
    if (proxied.status === 403) return proxied;
  }
  // Without an RWA Guard session (e.g. from /access after a refused sign-in)
  // there is nothing to revoke here, but the Decoda session must still end.

  let redirect: string | null = null;
  const auth = await withAuth();
  if (auth.user && auth.sessionId) {
    redirect = getWorkOS().userManagement.getLogoutUrl({
      sessionId: auth.sessionId,
      returnTo: publicUrl('/sign-in?signed_out=1', request).toString(),
    });
  }
  const response = NextResponse.json({ signed_out: true, redirect }, { headers: { 'Cache-Control': 'no-store' } });
  clearSessionCookies(response);
  clearAuthKitCookie(response, request);
  return response;
}
