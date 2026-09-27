import { type NextRequest, NextResponse } from 'next/server';

import { forwardIdentityHeaders } from 'app/api/_shared/identity-headers';
import { callGuardApi, decodaLinks, decodaSignInEnabled, jsonError } from 'app/decoda-identity';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

/**
 * GET /api/auth/identity-context — the Decoda organizations this person can
 * switch into (with their RWA Guard access state) and every Decoda product's
 * state for the current organization, plus the server-configured Decoda
 * destinations, for the product/organization switcher. Platform organization
 * ids only; WorkOS ids never reach the browser.
 */
export async function GET(request: NextRequest) {
  if (!decodaSignInEnabled()) return jsonError(404, 'not_found', 'Not found.');
  const authorization = request.headers.get('authorization');
  if (!authorization) return jsonError(401, 'AUTH_REQUIRED', 'Sign in to continue.');
  const headers = new Headers({ Accept: 'application/json', Authorization: authorization });
  forwardIdentityHeaders(request.headers, headers);
  let upstream: Response;
  try {
    upstream = await callGuardApi('/auth/identity/context', { headers });
  } catch {
    return jsonError(502, 'API_UNREACHABLE', 'The RWA Guard API is unreachable. Try again shortly.');
  }
  const body = (await upstream.json().catch(() => null)) as Record<string, unknown> | null;
  if (!upstream.ok || !body) {
    return NextResponse.json(body ?? { detail: { code: 'IDENTITY_CONTEXT_UNAVAILABLE', message: 'Decoda organizations are unavailable right now.' } }, {
      status: upstream.ok ? 502 : upstream.status,
      headers: { 'Cache-Control': 'no-store' },
    });
  }
  return NextResponse.json({ ...body, links: decodaLinks() }, { headers: { 'Cache-Control': 'no-store' } });
}
