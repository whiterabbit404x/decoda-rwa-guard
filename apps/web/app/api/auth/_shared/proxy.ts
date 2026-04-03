import { cookies } from 'next/headers';
import { NextResponse } from 'next/server';

import { normalizeApiBaseUrl } from '../../../api-config';
import { getRuntimeConfig } from '../../../runtime-config';

const SESSION_COOKIE_NAME = 'decoda_session';
const CSRF_COOKIE_NAME = 'decoda_csrf';
const JSON_HEADERS = {
  'Cache-Control': 'no-store',
  'Content-Type': 'application/json',
};

export const dynamic = 'force-dynamic';
export const revalidate = 0;

export type AuthProxyMethod = 'GET' | 'POST';

type AuthCookieAction = 'set-session' | 'clear-session' | 'none';

function errorResponse(status: number, body: Record<string, unknown>) {
  return Response.json(body, {
    status,
    headers: JSON_HEADERS,
  });
}

function parseCookie(headerValue: string | null, cookieName: string) {
  if (!headerValue) return null;
  const match = headerValue
    .split(';')
    .map((entry) => entry.trim())
    .find((entry) => entry.startsWith(`${cookieName}=`));

  if (!match) return null;
  const raw = match.slice(cookieName.length + 1).trim();
  return raw ? decodeURIComponent(raw) : null;
}

function readSetCookieHeaders(response: Response): string[] {
  const headersWithGetSetCookie = response.headers as Headers & { getSetCookie?: () => string[] };
  if (typeof headersWithGetSetCookie.getSetCookie === 'function') {
    return headersWithGetSetCookie.getSetCookie();
  }
  const combinedHeader = response.headers.get('set-cookie');
  return combinedHeader ? [combinedHeader] : [];
}

function readCookieValueFromSetCookie(setCookieHeaders: string[], cookieName: string): string | null {
  for (const setCookieHeader of setCookieHeaders) {
    const firstPair = setCookieHeader.split(';', 1)[0]?.trim() ?? '';
    if (!firstPair.startsWith(`${cookieName}=`)) {
      continue;
    }
    const rawValue = firstPair.slice(cookieName.length + 1).trim();
    return rawValue ? decodeURIComponent(rawValue) : null;
  }
  return null;
}

function authCookieOptions() {
  const isProd = process.env.NODE_ENV === 'production';
  return {
    httpOnly: true,
    secure: isProd,
    sameSite: 'lax' as const,
    path: '/',
    maxAge: 60 * 60 * 24,
  };
}

function csrfCookieOptions() {
  const isProd = process.env.NODE_ENV === 'production';
  return {
    httpOnly: false,
    secure: isProd,
    sameSite: 'lax' as const,
    path: '/',
    maxAge: 60 * 60 * 24,
  };
}

function readRequestSessionToken(request: Request) {
  const authorizationHeader = request.headers.get('authorization');
  if (authorizationHeader) {
    return authorizationHeader;
  }

  const cookieHeader = request.headers.get('cookie');
  const sessionToken = parseCookie(cookieHeader, SESSION_COOKIE_NAME);
  if (!sessionToken) {
    return null;
  }
  return `Bearer ${sessionToken}`;
}

function validateCsrf(request: Request) {
  if (request.method === 'GET') {
    return true;
  }

  const cookieHeader = request.headers.get('cookie');
  const cookieToken = parseCookie(cookieHeader, CSRF_COOKIE_NAME);
  const headerToken = request.headers.get('x-csrf-token');
  return Boolean(cookieToken && headerToken && cookieToken === headerToken);
}

async function readJsonBody(request: Request) {
  try {
    return await request.json();
  } catch {
    return undefined;
  }
}

async function buildBackendResponse(response: Response, cookieAction: AuthCookieAction = 'none') {
  const contentType = response.headers.get('content-type') ?? '';
  const responseBody = contentType.toLowerCase().includes('application/json')
    ? await response.json().catch(() => ({ detail: 'Backend returned invalid JSON.' }))
    : {
      detail: (await response.text().catch(() => '')).trim() || (response.ok ? 'Request completed.' : 'Request failed. Please try again.'),
    };

  const proxyResponse = NextResponse.json(responseBody, {
    status: response.status,
    headers: {
      'Cache-Control': 'no-store',
    },
  });

  if (cookieAction === 'set-session') {
    const responseAccessToken = typeof responseBody.access_token === 'string' ? responseBody.access_token : '';
    const backendSetCookieHeaders = readSetCookieHeaders(response);
    const backendSessionCookie = readCookieValueFromSetCookie(backendSetCookieHeaders, SESSION_COOKIE_NAME)
      || readCookieValueFromSetCookie(backendSetCookieHeaders, 'decoda-pilot-access-token');
    const sessionToken = responseAccessToken || backendSessionCookie;

    if (sessionToken) {
      const csrfToken = crypto.randomUUID().replace(/-/g, '');
      proxyResponse.cookies.set(SESSION_COOKIE_NAME, sessionToken, authCookieOptions());
      proxyResponse.cookies.set(CSRF_COOKIE_NAME, csrfToken, csrfCookieOptions());
    }

    if (typeof responseBody.access_token === 'string') {
      delete responseBody.access_token;
    }
  }

  if (cookieAction === 'clear-session') {
    proxyResponse.cookies.set(SESSION_COOKIE_NAME, '', { ...authCookieOptions(), maxAge: 0 });
    proxyResponse.cookies.set(CSRF_COOKIE_NAME, '', { ...csrfCookieOptions(), maxAge: 0 });
  }

  return proxyResponse;
}

export async function proxyAuthRequest(request: Request, backendPath: string, method: AuthProxyMethod, options?: { requireAuth?: boolean; cookieAction?: AuthCookieAction }) {
  const runtimeConfig = getRuntimeConfig();
  const backendApiUrl = normalizeApiBaseUrl(runtimeConfig.apiUrl);

  if (!runtimeConfig.configured || !backendApiUrl) {
    return errorResponse(500, {
      detail: runtimeConfig.diagnostic ?? 'Web server runtime auth proxy is not configured with a valid backend API URL.',
      code: 'invalid_runtime_config',
      authTransport: 'same-origin proxy',
      backendApiUrl,
      configured: false,
    });
  }

  if (options?.requireAuth && method !== 'GET' && !validateCsrf(request)) {
    return errorResponse(403, {
      detail: 'CSRF token missing or invalid.',
      code: 'csrf_invalid',
    });
  }

  const headers = new Headers();
  headers.set('Accept', 'application/json');

  if (method !== 'GET') {
    headers.set('Content-Type', 'application/json');
  }

  const xWorkspaceId = request.headers.get('x-workspace-id');
  if (xWorkspaceId) {
    headers.set('X-Workspace-Id', xWorkspaceId);
  }

  const authorization = readRequestSessionToken(request);
  if (authorization) {
    headers.set('Authorization', authorization);
  } else if (options?.requireAuth) {
    return errorResponse(401, {
      detail: 'Authorization is required for this auth action.',
      code: 'missing_authorization',
      authTransport: 'same-origin proxy',
      backendApiUrl,
      configured: true,
    });
  }

  const init: RequestInit = {
    method,
    headers,
    cache: 'no-store',
  };

  if (method !== 'GET') {
    init.body = JSON.stringify(await readJsonBody(request) ?? {});
  }

  try {
    const response = await fetch(`${backendApiUrl}${backendPath}`, init);
    return await buildBackendResponse(response, options?.cookieAction ?? 'none');
  } catch {
    return errorResponse(502, {
      detail: 'We could not reach the authentication service. Please try again shortly.',
      code: 'backend_unreachable',
      authTransport: 'same-origin proxy',
      backendApiUrl,
      configured: true,
    });
  }
}

export async function getCsrfToken() {
  const cookieStore = await cookies();
  return cookieStore.get(CSRF_COOKIE_NAME)?.value ?? null;
}
