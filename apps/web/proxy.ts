import { authkit, handleAuthkitProxy } from '@workos-inc/authkit-nextjs';
import type { NextRequest } from 'next/server';
import { NextRequest as ShadowRequest, NextResponse } from 'next/server';

import { SESSION_COOKIE, bffSecret, decodaSignInEnabled, publicOrigin } from './app/decoda-identity';
import { attachApiCredentials, stripBrowserCredentials } from './app/server-credentials';
import { buildContentSecurityPolicy } from './content-security-policy';

/**
 * Next.js request proxy (formerly "middleware").
 *
 * 1. Stamps a fresh CSP nonce on every document request.
 * 2. Credentials for the RWA Guard API are attached HERE, server-side, never
 *    by the browser. Every browser-supplied Authorization / proxy-secret /
 *    identity-session header is removed; on `/api/*` requests the session
 *    token is taken from the HttpOnly `decoda_session` cookie and, with the
 *    shared Decoda identity, the BFF secret and the id of the live AuthKit
 *    session are added (the API binds a Decoda session to the AuthKit session
 *    it was issued for). Route handlers forward them to the API unchanged.
 * 3. With the shared Decoda identity, runs AuthKit's session handling: the
 *    sealed `wos-session` cookie is verified and refreshed before it expires,
 *    so pages and route handlers can call `withAuth()`. It never redirects.
 */
export async function proxy(request: NextRequest) {
  const isApi = request.nextUrl.pathname.startsWith('/api/');
  const nonce = Buffer.from(crypto.randomUUID()).toString('base64');
  const development = process.env.NODE_ENV !== 'production' && process.env.APP_MODE !== 'production';
  const contentSecurityPolicy = buildContentSecurityPolicy(nonce, { development });

  const requestHeaders = new Headers(request.headers);
  stripBrowserCredentials(requestHeaders);
  if (isApi) {
    attachApiCredentials(requestHeaders, { sessionToken: request.cookies.get(SESSION_COOKIE)?.value, secret: bffSecret() });
  } else {
    requestHeaders.set('x-nonce', nonce);
    requestHeaders.set('Content-Security-Policy', contentSecurityPolicy);
  }

  let response: NextResponse;
  if (decodaSignInEnabled()) {
    // Header-only view of the request on the public origin: AuthKit reads the
    // URL (return paths, cookie Secure flag), headers and cookies; the body is
    // forwarded untouched by NextResponse.next().
    const url = new URL(`${request.nextUrl.pathname}${request.nextUrl.search}`, publicOrigin(request));
    const { session, headers } = await authkit(new ShadowRequest(url, { method: request.method, headers: requestHeaders }));
    if (isApi && session.user) attachApiCredentials(requestHeaders, { identitySessionId: session.sessionId });
    response = handleAuthkitProxy(new ShadowRequest(url, { method: request.method, headers: requestHeaders }), headers);
  } else {
    response = NextResponse.next({ request: { headers: requestHeaders } });
  }
  if (!isApi) response.headers.set('Content-Security-Policy', contentSecurityPolicy);
  return response;
}

export const config = {
  matcher: [
    // Documents: the CSP nonce (prefetches are rendered with the page's own).
    {
      source: '/((?!api|_next/static|_next/image|favicon.ico).*)',
      missing: [
        { type: 'header', key: 'next-router-prefetch' },
        { type: 'header', key: 'purpose', value: 'prefetch' },
      ],
    },
    // The BFF API routes: server-side credentials, always.
    '/api/:path*',
  ],
};
