import { NextResponse } from 'next/server';

import { normalizeApiBaseUrl } from 'app/api-config';
import { getRuntimeConfig } from 'app/runtime-config';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

const CSRF_COOKIE_NAME = 'decoda_csrf';

function csrfCookieOptions() {
  const isProd = process.env.NODE_ENV === 'production';
  return {
    httpOnly: false as const,
    secure: isProd,
    sameSite: 'lax' as const,
    path: '/',
    maxAge: 60 * 60 * 24,
  };
}

// Proxy the backend CSRF token endpoint so the frontend receives an HMAC-signed
// token that the backend middleware will actually accept. The token is also stored
// in the decoda_csrf cookie so the double-submit cookie check in proxyAuthRequest
// (cookie value == X-CSRF-Token header) continues to work for auth-layer mutations.
export async function GET() {
  const runtimeConfig = getRuntimeConfig();
  const backendApiUrl = normalizeApiBaseUrl(runtimeConfig.apiUrl);

  if (!runtimeConfig.configured || !backendApiUrl) {
    return NextResponse.json({ csrfToken: null }, { headers: { 'Cache-Control': 'no-store' } });
  }

  try {
    const backendResponse = await fetch(`${backendApiUrl}/auth/csrf-token`, {
      method: 'GET',
      headers: { Accept: 'application/json' },
      cache: 'no-store',
    });

    if (!backendResponse.ok) {
      return NextResponse.json({ csrfToken: null }, { headers: { 'Cache-Control': 'no-store' } });
    }

    const data = await backendResponse.json().catch(() => ({}));
    const csrfToken = typeof data.csrf_token === 'string' ? data.csrf_token : null;

    const proxyResponse = NextResponse.json({ csrfToken }, { headers: { 'Cache-Control': 'no-store' } });

    if (csrfToken) {
      proxyResponse.cookies.set(CSRF_COOKIE_NAME, csrfToken, csrfCookieOptions());
    }

    return proxyResponse;
  } catch {
    return NextResponse.json({ csrfToken: null }, { headers: { 'Cache-Control': 'no-store' } });
  }
}
