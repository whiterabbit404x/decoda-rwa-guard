/**
 * Shared Decoda identity for the RWA Guard web BFF — server-only.
 *
 * GUARD_IDENTITY_MODE (set to the same value as the Guard API)
 *   legacy  Guard's own sign-in, exactly as before (the default).
 *   dual    "Sign in with Decoda" (WorkOS AuthKit) is the primary path; the
 *           legacy password form stays for accounts not yet linked, until
 *           GUARD_LEGACY_PASSWORD_SUNSET.
 *   workos  "Sign in with Decoda" only.
 *
 * In both shared modes nobody registers here: Decoda accounts are created by
 * invitation, and people without access are sent to the Decoda website's
 * Request pilot page.
 *
 * The browser never holds an RWA Guard token: sessions live in the HttpOnly
 * `decoda_session` cookie, and this server attaches the bearer token, the BFF
 * secret and the live AuthKit session id to every backend call (see proxy.ts).
 * Nothing here decides access — the Guard API re-verifies the WorkOS token,
 * the live WorkOS session and the organization's entitlement itself.
 */
import type { NextResponse } from 'next/server';

import { normalizeApiBaseUrl } from './api-config';
import { accessReasonFor, type AccessReason, type DecodaSignInOptions } from './decoda-identity-shared';
import { getRuntimeConfig } from './runtime-config';

export type GuardIdentityMode = 'legacy' | 'dual' | 'workos';

export const SESSION_COOKIE = 'decoda_session';
export const CSRF_COOKIE = 'decoda_csrf';
/** Retired: the readable copy of the session token. Only ever cleared now. */
export const RETIRED_READABLE_TOKEN_COOKIE = 'decoda_access_token';
/** Request headers only this server may set on a backend call. */
export const PROXY_SECRET_HEADER = 'x-guard-proxy-secret';
export const IDENTITY_SESSION_HEADER = 'x-guard-identity-session';
export const SERVER_ONLY_REQUEST_HEADERS = ['authorization', PROXY_SECRET_HEADER, IDENTITY_SESSION_HEADER] as const;

const SESSION_MAX_AGE_SECONDS = 60 * 60 * 24;

export function guardIdentityMode(): GuardIdentityMode {
  const raw = (process.env.GUARD_IDENTITY_MODE ?? '').trim().toLowerCase();
  if (!raw) return 'legacy';
  if (raw === 'legacy' || raw === 'dual' || raw === 'workos') return raw;
  // An unknown value is a misconfiguration (the API refuses to start with it):
  // fail closed to the mode that offers no password form.
  return 'workos';
}

/** Everything AuthKit needs. Missing configuration means Decoda sign-in is unavailable (fail closed). */
export function workosConfigured(): boolean {
  return Boolean(
    (process.env.WORKOS_CLIENT_ID ?? '').trim() &&
      (process.env.WORKOS_API_KEY ?? '').trim() &&
      (process.env.WORKOS_COOKIE_PASSWORD ?? '').length >= 32 &&
      (process.env.NEXT_PUBLIC_WORKOS_REDIRECT_URI ?? '').trim(),
  );
}

/** Whether this deployment offers "Sign in with Decoda" (and runs AuthKit's session handling). */
export function decodaSignInEnabled(): boolean {
  return guardIdentityMode() !== 'legacy' && workosConfigured();
}

/** Whether the legacy password form may be offered (the API enforces the same rule). */
export function legacyPasswordsAllowed(now: Date = new Date()): boolean {
  const mode = guardIdentityMode();
  if (mode === 'legacy') return true;
  if (mode === 'workos') return false;
  const raw = (process.env.GUARD_LEGACY_PASSWORD_SUNSET ?? '').trim();
  if (!raw) return true;
  const sunset = new Date(raw);
  if (Number.isNaN(sunset.getTime())) return false;
  return now < sunset;
}

export function bffSecret(): string {
  return (process.env.GUARD_BFF_SHARED_SECRET ?? '').trim();
}

/**
 * The origin users reach RWA Guard on: the origin of Guard's registered WorkOS
 * redirect URI. A self-hosted `next start` reports its own bind address in
 * `request.url`, which would send users to the wrong host and drop the
 * cookies' Secure flag.
 */
export function configuredPublicOrigin(): string | undefined {
  const configured = (process.env.NEXT_PUBLIC_WORKOS_REDIRECT_URI ?? '').trim();
  if (!configured) return undefined;
  try {
    return new URL(configured).origin;
  } catch {
    return undefined;
  }
}

/** The public origin for this request: the configured one, else the request's own. */
export function publicOrigin(request: Request): string {
  return configuredPublicOrigin() ?? new URL(request.url).origin;
}

export function publicUrl(path: string, request: Request): URL {
  return new URL(path, publicOrigin(request));
}

export type DecodaLinks = { website: string; launcher: string; account: string; requestAccess: string; vault: string };

function httpsUrl(raw: string | undefined, fallback: string): string {
  const value = (raw ?? '').trim();
  if (!value) return fallback;
  try {
    const url = new URL(value);
    if (url.protocol !== 'https:' && !(process.env.NODE_ENV !== 'production' && url.protocol === 'http:')) return fallback;
    return url.toString().replace(/\/$/, '');
  } catch {
    return fallback;
  }
}

/**
 * A Decoda product's sign-in entry (its WorkOS Initiate login URI). With a
 * Decoda session already in the browser, AuthKit returns there without asking
 * again, and that product decides access itself.
 */
export function productEntryUrl(productUrl: string): string {
  return `${productUrl.replace(/\/+$/, '')}/auth/sign-in`;
}

/** Server-configured destinations for the product switcher (never user-supplied). */
export function decodaLinks(): DecodaLinks {
  const website = httpsUrl(process.env.DECODA_WEBSITE_URL, 'https://www.decodasecurity.com');
  return {
    website,
    launcher: `${website}/launcher`,
    account: `${website}/account`,
    requestAccess: `${website}/request-pilot?product=rwa_guard`,
    vault: productEntryUrl(httpsUrl(process.env.DECODA_VAULT_URL, 'https://vault.decodasecurity.com')),
  };
}

/**
 * What /sign-in offers, from the identity mode: nothing extra in `legacy` (RWA
 * Guard's own sign-in, unchanged); otherwise "Sign in with Decoda", the legacy
 * password form only while `dual` still allows it, and "Request access" on the
 * Decoda website. Never a way to create an RWA Guard account.
 */
export function decodaSignInOptions(query: { signedOut?: boolean; reason?: string | null } = {}): DecodaSignInOptions | undefined {
  if (guardIdentityMode() === 'legacy') return undefined;
  const enabled = decodaSignInEnabled();
  return {
    enabled,
    passwordFormAllowed: legacyPasswordsAllowed(),
    requestAccessUrl: decodaLinks().requestAccess,
    notice: !enabled
      ? 'Decoda sign-in is temporarily unavailable. Please try again shortly.'
      : query.signedOut
        ? 'You are signed out of Decoda.'
        : query.reason === 'session'
          ? 'Your session ended. Sign in with Decoda to continue.'
          : query.reason === 'callback'
            ? 'Sign-in could not be completed. Please try again.'
            : null,
  };
}

export function secureCookiesFor(request: Request): boolean {
  return process.env.NODE_ENV === 'production' || new URL(request.url).protocol === 'https:';
}

/** Defense in depth on top of the API's CSRF token: a state-changing request must come from this origin. */
export function isSameOrigin(request: Request): boolean {
  const origin = request.headers.get('origin');
  const host = request.headers.get('x-forwarded-host') ?? request.headers.get('host');
  if (!origin || !host) return false;
  try {
    return new URL(origin).host === host;
  } catch {
    return false;
  }
}

export function jsonError(status: number, code: string, message: string): Response {
  return Response.json({ detail: { code, message } }, { status, headers: { 'Cache-Control': 'no-store' } });
}

/** Headers every server → Guard API call carries for the end user (never secrets from the browser). */
export function serverCallHeaders(request: Request, extra: Record<string, string> = {}): Headers {
  const headers = new Headers({ Accept: 'application/json', ...extra });
  const secret = bffSecret();
  if (secret) headers.set(PROXY_SECRET_HEADER, secret);
  const requestId = request.headers.get('x-request-id');
  headers.set('X-Request-Id', requestId && /^[A-Za-z0-9._:-]{8,128}$/.test(requestId) ? requestId : `web_${crypto.randomUUID().replace(/-/g, '')}`);
  const userAgent = request.headers.get('user-agent');
  if (userAgent) headers.set('User-Agent', userAgent.slice(0, 300));
  return headers;
}

function backendBaseUrl(): string | null {
  const runtime = getRuntimeConfig();
  return runtime.configured ? normalizeApiBaseUrl(runtime.apiUrl) : null;
}

export async function callGuardApi(path: string, init: RequestInit & { timeoutMs?: number } = {}): Promise<Response> {
  const base = backendBaseUrl();
  if (!base) throw new Error('The RWA Guard API URL is not configured for this web deployment.');
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), init.timeoutMs ?? 15_000);
  try {
    return await fetch(`${base}${path}`, { ...init, cache: 'no-store', signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

export type ExchangeOutcome =
  | { ok: true; accessToken: string }
  | { ok: false; status: number; code: string; reason: AccessReason };

type ApiErrorBody = { detail?: { code?: unknown; reason?: unknown } | string };

/**
 * Trade the verified AuthKit access token for an RWA Guard session. The API
 * re-verifies the token, the live WorkOS session and the organization's
 * entitlement itself; this server only relays.
 */
export async function exchangeIdentity(accessToken: string, request: Request): Promise<ExchangeOutcome> {
  let upstream: Response;
  try {
    upstream = await callGuardApi('/auth/identity/exchange', {
      method: 'POST',
      headers: serverCallHeaders(request, { 'Content-Type': 'application/json' }),
      body: JSON.stringify({ access_token: accessToken }),
    });
  } catch {
    return { ok: false, status: 502, code: 'API_UNREACHABLE', reason: 'unavailable' };
  }
  const payload = (await upstream.json().catch(() => null)) as ({ access_token?: unknown } & ApiErrorBody) | null;
  if (upstream.ok && typeof payload?.access_token === 'string' && payload.access_token) {
    return { ok: true, accessToken: payload.access_token };
  }
  const detail = payload && typeof payload.detail === 'object' && payload.detail ? payload.detail : {};
  const code = typeof detail.code === 'string' ? detail.code : `HTTP_${upstream.status}`;
  const reason = typeof detail.reason === 'string' ? detail.reason : null;
  return { ok: false, status: upstream.status, code, reason: upstream.status >= 500 ? 'unavailable' : accessReasonFor(code, reason) };
}

/** Where a failed exchange sends the browser. A stale identity (401) must sign in again. */
export function exchangeFailurePath(outcome: Extract<ExchangeOutcome, { ok: false }>): string {
  if (outcome.status === 401) return '/sign-in?reason=session&stale=1';
  return `/access?reason=${outcome.reason}`;
}

function sessionCookieOptions(secure: boolean) {
  return { httpOnly: true, secure, sameSite: 'lax' as const, path: '/', maxAge: SESSION_MAX_AGE_SECONDS };
}

/**
 * The session lives ONLY in the HttpOnly cookie. `decoda_csrf` is the readable
 * double-submit value the client echoes as X-CSRF-Token; the API independently
 * requires its own HMAC-signed token (fetched through /api/auth/csrf).
 */
export function setSessionCookies(response: NextResponse, accessToken: string, secure: boolean): void {
  response.cookies.set(SESSION_COOKIE, accessToken, sessionCookieOptions(secure));
  response.cookies.set(CSRF_COOKIE, crypto.randomUUID().replace(/-/g, ''), { ...sessionCookieOptions(secure), httpOnly: false });
  response.cookies.set(RETIRED_READABLE_TOKEN_COOKIE, '', { ...sessionCookieOptions(secure), httpOnly: false, maxAge: 0 });
}

export function clearSessionCookies(response: NextResponse): void {
  for (const name of [SESSION_COOKIE, CSRF_COOKIE, RETIRED_READABLE_TOKEN_COOKIE]) {
    response.cookies.set(name, '', { path: '/', maxAge: 0, sameSite: 'lax' });
  }
}

/** Forget the AuthKit session cookie on this host (the WorkOS session itself is ended via the logout URL). */
export function clearAuthKitCookie(response: NextResponse, request: Request): void {
  response.cookies.set((process.env.WORKOS_COOKIE_NAME ?? '').trim() || 'wos-session', '', {
    path: '/',
    maxAge: 0,
    httpOnly: true,
    sameSite: 'lax',
    secure: secureCookiesFor(request),
  });
}
