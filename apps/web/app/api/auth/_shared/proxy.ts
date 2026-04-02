import { normalizeApiBaseUrl } from '../../../api-config';
import { getRuntimeConfig } from '../../../runtime-config';
import { AUTH_COOKIE_NAME, CSRF_COOKIE_NAME } from './session';

const JSON_HEADERS = {
  'Cache-Control': 'no-store',
  'Content-Type': 'application/json',
};

export const dynamic = 'force-dynamic';
export const revalidate = 0;

export type AuthProxyMethod = 'GET' | 'POST';

function errorResponse(status: number, body: Record<string, unknown>) {
  return Response.json(body, {
    status,
    headers: JSON_HEADERS,
  });
}

function getCookieValue(request: Request, name: string) {
  const cookieHeader = request.headers.get('cookie');
  if (!cookieHeader) {
    return null;
  }
  const tokenCookie = cookieHeader
    .split(';')
    .map((entry) => entry.trim())
    .find((entry) => entry.startsWith(`${name}=`));
  if (!tokenCookie) {
    return null;
  }
  const tokenValue = tokenCookie.slice(name.length + 1).trim();
  return tokenValue ? decodeURIComponent(tokenValue) : null;
}

function getAuthorizationHeader(request: Request) {
  const authorizationHeader = request.headers.get('authorization');
  if (authorizationHeader) {
    return authorizationHeader;
  }

  const tokenValue = getCookieValue(request, AUTH_COOKIE_NAME);
  return tokenValue ? `Bearer ${tokenValue}` : null;
}

function validateCsrf(request: Request): boolean {
  const csrfHeader = request.headers.get('x-csrf-token')?.trim();
  const csrfCookie = getCookieValue(request, CSRF_COOKIE_NAME);
  return Boolean(csrfHeader && csrfCookie && csrfHeader === csrfCookie);
}

async function readJsonBody(request: Request) {
  try {
    return await request.json();
  } catch {
    return undefined;
  }
}

async function buildBackendResponse(response: Response) {
  const contentType = response.headers.get('content-type') ?? '';

  if (contentType.toLowerCase().includes('application/json')) {
    const payload = await response.json().catch(() => ({ detail: 'Backend returned invalid JSON.' }));
    return Response.json(payload, {
      status: response.status,
      headers: { 'Cache-Control': 'no-store' },
    });
  }

  const bodyText = await response.text().catch(() => '');
  const fallbackMessage = response.ok
    ? 'Request completed.'
    : 'Request failed. Please try again.';
  return Response.json({ detail: bodyText?.trim() || fallbackMessage }, {
    status: response.status,
    headers: { 'Cache-Control': 'no-store' },
  });
}

export async function proxyAuthRequest(request: Request, backendPath: string, method: AuthProxyMethod, options?: { requireAuth?: boolean; requireCsrf?: boolean }) {
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

  if (options?.requireCsrf && method !== 'GET' && !validateCsrf(request)) {
    return errorResponse(403, {
      detail: 'CSRF validation failed. Reload and try again.',
      code: 'invalid_csrf',
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

  const authorization = getAuthorizationHeader(request);
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

  const init: RequestInit = { method, headers, cache: 'no-store' };
  if (method !== 'GET') {
    init.body = JSON.stringify(await readJsonBody(request) ?? {});
  }

  try {
    const response = await fetch(`${backendApiUrl}${backendPath}`, init);
    return await buildBackendResponse(response);
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
