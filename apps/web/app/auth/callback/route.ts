/**
 * GET /auth/callback — RWA Guard's WorkOS redirect URI.
 *
 * AuthKit verifies the PKCE verifier and sealed state, exchanges the code and
 * seals the session into this host's HttpOnly `wos-session` cookie. It then
 * continues to /auth/session (carried in the sealed state), where the RWA
 * Guard API verifies everything again before issuing an RWA Guard session.
 */
import { handleAuth } from '@workos-inc/authkit-nextjs';
import { NextResponse } from 'next/server';

import { configuredPublicOrigin, publicUrl } from '../../decoda-identity';

export const dynamic = 'force-dynamic';

export const GET = handleAuth({
  returnPathname: '/auth/session',
  // Unconfigured means Decoda sign-in is off; AuthKit then uses the request's origin.
  baseURL: configuredPublicOrigin(),
  onError: async ({ error, request }) => {
    const code = (error as { code?: unknown } | null)?.code;
    // Only the error class and code: never the query string (it carries the
    // authorization code) and never provider payloads.
    console.error('[guard:identity] sign-in callback failed', {
      name: error instanceof Error ? error.name : typeof error,
      code: typeof code === 'string' ? code : null,
    });
    return NextResponse.redirect(publicUrl('/sign-in?reason=callback&stale=1', request), 303);
  },
});
