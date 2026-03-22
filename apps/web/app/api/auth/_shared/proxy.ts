import { normalizeApiBaseUrl } from '../../../api-config';
import { getRuntimeConfig } from '../../../runtime-config';

const TOKEN_COOKIE_NAME = 'decoda-pilot-access-token';
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

function getAuthorizationHeader(request: Request) {
  const authorizationHeader = request.headers.get('authorization');
  if (authorizationHeader) {
    return authorizationHeader;
  }

  const cookieHeader = request.headers.get('cookie');
  if (!cookieHeader) {
    return null;
  }

  const tokenCookie = cookieHeader
    .split(';')
    .map((entry) => entry.trim())
    .find((entry) => entry.startsWith(`${TOKEN_COOKIE_NAME}=`));

  if (!tokenCookie) {
    return null;
  }

  const tokenValue = tokenCookie.slice(TOKEN_COOKIE_NAME.length + 1).trim();
  if (!tokenValue) {
    return null;
  }

  return `Bearer ${decodeURIComponent(tokenValue)}`;
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
  const cacheControlHeaders = {
    'Cache-Control': 'no-store',
  };

  if (contentType.toLowerCase().includes('application/json')) {
    const payload = await response.json().catch(() => ({ detail: 'Backend returned invalid JSON.' }));
    return Response.json(payload, {
      status: response.status,
      headers: cacheControlHeaders,
    });
  }

  const bodyText = await response.text();
  return new Response(bodyText, {
    status: response.status,
    headers: {
      ...cacheControlHeaders,
      'Content-Type': contentType || 'text/plain; charset=utf-8',
    },
  });
}

export async function proxyAuthRequest(request: Request, backendPath: string, method: AuthProxyMethod, options?: { requireAuth?: boolean }) {
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
    return await buildBackendResponse(response);
  } catch (error) {
    const detail = error instanceof Error
      ? error.message
      : 'Unknown error while contacting backend API.';

    return errorResponse(502, {
      detail: `The web auth proxy could not reach the backend API at ${backendApiUrl}. ${detail}`,
      code: 'backend_unreachable',
      authTransport: 'same-origin proxy',
      backendApiUrl,
      configured: true,
    });
  }
}
