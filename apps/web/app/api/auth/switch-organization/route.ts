/**
 * POST /api/auth/switch-organization — open RWA Guard for another Decoda
 * organization. Body: { organizationId: <Decoda platform organization id> }.
 *
 * 1. The Guard API (bound to the live AuthKit session, BFF-only) confirms the
 *    person is an active member of that organization AND that it is entitled
 *    to RWA Guard, and only then names its WorkOS organization.
 * 2. AuthKit re-issues the session for that organization; WorkOS checks the
 *    membership again (an organization requiring SSO or MFA enrollment sends
 *    the person through AuthKit instead).
 * 3. The new access token is exchanged for a new RWA Guard session, bound to
 *    that organization.
 *
 * The browser never names a WorkOS id and never chooses an organization the
 * platform has not confirmed. Answers { ok, redirect }; the page performs a
 * full navigation so no state from the previous organization survives.
 */
import { getSignInUrl, refreshSession, withAuth } from '@workos-inc/authkit-nextjs';
import { type NextRequest, NextResponse } from 'next/server';

import { forwardIdentityHeaders } from 'app/api/_shared/identity-headers';
import {
  CSRF_COOKIE,
  callGuardApi,
  clearSessionCookies,
  decodaSignInEnabled,
  exchangeFailurePath,
  exchangeIdentity,
  isSameOrigin,
  jsonError,
  secureCookiesFor,
  setSessionCookies,
} from 'app/decoda-identity';

export const dynamic = 'force-dynamic';

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

type RefreshFailure = { cause?: { error?: string; rawData?: { authkit_redirect_url?: string } } };

export async function POST(request: NextRequest) {
  if (!isSameOrigin(request)) return jsonError(403, 'origin_rejected', 'Cross-origin requests are not allowed.');
  // Double-submit CSRF, like every other mutation of this BFF (the API's
  // switch-target is BFF-only and does not see the browser's CSRF token).
  const csrfCookie = request.cookies.get(CSRF_COOKIE)?.value ?? '';
  const csrfHeader = request.headers.get('x-csrf-token') ?? '';
  if (!csrfCookie || csrfCookie !== csrfHeader) return jsonError(403, 'csrf_invalid', 'CSRF token missing or invalid.');
  if (!decodaSignInEnabled()) return jsonError(404, 'not_found', 'Not found.');
  const body = (await request.json().catch(() => null)) as { organizationId?: unknown } | null;
  const organizationId = typeof body?.organizationId === 'string' ? body.organizationId : '';
  if (!UUID.test(organizationId)) return jsonError(422, 'VALIDATION_FAILED', 'Choose an organization.');

  const auth = await withAuth();
  const authorization = request.headers.get('authorization');
  if (!auth.user || !auth.sessionId || !authorization) return jsonError(401, 'AUTH_REQUIRED', 'Sign in to continue.');

  const headers = new Headers({ 'Content-Type': 'application/json', Accept: 'application/json', Authorization: authorization });
  forwardIdentityHeaders(request.headers, headers);
  let target: Response;
  try {
    target = await callGuardApi('/auth/identity/switch-target', {
      method: 'POST',
      headers,
      body: JSON.stringify({ organization_id: organizationId }),
    });
  } catch {
    return jsonError(502, 'API_UNREACHABLE', 'The RWA Guard API is unreachable. Try again shortly.');
  }
  const resolved = (await target.json().catch(() => null)) as { workos_organization_id?: unknown; detail?: unknown } | null;
  if (!target.ok || typeof resolved?.workos_organization_id !== 'string') {
    return NextResponse.json(resolved ?? { detail: { code: 'ORGANIZATION_SWITCH_FAILED', message: 'The organization could not be switched.' } }, {
      status: target.status || 502,
      headers: { 'Cache-Control': 'no-store' },
    });
  }
  const workosOrganizationId = resolved.workos_organization_id;

  let accessToken: string | undefined;
  try {
    accessToken = (await refreshSession({ organizationId: workosOrganizationId })).accessToken;
  } catch (error) {
    const cause = (error as RefreshFailure).cause;
    if (cause?.rawData?.authkit_redirect_url || cause?.error === 'sso_required' || cause?.error === 'mfa_enrollment') {
      // The organization demands a stronger or different sign-in: go through AuthKit.
      const redirect = await getSignInUrl({ organizationId: workosOrganizationId, returnTo: '/auth/session' });
      return NextResponse.json({ ok: true, redirect }, { headers: { 'Cache-Control': 'no-store' } });
    }
    return jsonError(409, 'ORGANIZATION_SWITCH_FAILED', 'The organization could not be switched. Sign in again and retry.');
  }
  if (!accessToken) return jsonError(401, 'AUTH_REQUIRED', 'Sign in to continue.');

  const outcome = await exchangeIdentity(accessToken, request);
  if (!outcome.ok) {
    const response = NextResponse.json({ ok: false, redirect: exchangeFailurePath(outcome) }, { headers: { 'Cache-Control': 'no-store' } });
    clearSessionCookies(response);
    return response;
  }
  const response = NextResponse.json({ ok: true, redirect: '/dashboard' }, { headers: { 'Cache-Control': 'no-store' } });
  setSessionCookies(response, outcome.accessToken, secureCookiesFor(request));
  return response;
}
