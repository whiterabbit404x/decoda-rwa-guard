import { normalizeApiBaseUrl } from 'app/api-config';
import { getRuntimeConfig } from 'app/runtime-config';
import { normalizeWorkspaceHeaderValue } from 'app/workspace-header';
import { FetchTimeoutError, fetchWithTimeout } from 'app/fetch-with-timeout';

export const PROXY_TIMEOUT_MS = 30000;

export function jsonError(status: number, body: Record<string, unknown>) {
  return Response.json(body, {
    status,
    headers: { 'Cache-Control': 'no-store', 'Content-Type': 'application/json' },
  });
}

export async function buildProxyResponse(response: Response): Promise<Response> {
  const contentType = response.headers.get('content-type') ?? '';
  const isJson = contentType.toLowerCase().includes('application/json');
  const payload = isJson
    ? await response.json().catch(() => ({ detail: 'Backend returned invalid JSON.' }))
    : {
        detail: (await response.text().catch(() => '')).trim() ||
          (response.ok ? 'Request completed.' : 'Request failed. Please try again.'),
      };
  return Response.json(payload, { status: response.status, headers: { 'Cache-Control': 'no-store' } });
}

export function resolveBackend(): { url: string } | Response {
  const runtimeConfig = getRuntimeConfig();
  const backendApiUrl = normalizeApiBaseUrl(runtimeConfig.apiUrl);
  if (!runtimeConfig.configured || !backendApiUrl) {
    return jsonError(500, {
      detail: runtimeConfig.diagnostic ?? 'Web runtime proxy is not configured with a valid backend API URL.',
      code: 'invalid_runtime_config',
      transport: 'same-origin proxy',
      configured: false,
    });
  }
  return { url: backendApiUrl };
}

/** Authorization + workspace scoping. The workspace header is forwarded so the
 *  backend resolves the caller's workspace — never a client-chosen tenant. */
export function baseHeaders(request: Request): Headers | null {
  const authorization = request.headers.get('authorization')?.trim() || null;
  if (!authorization) return null;
  const headers = new Headers();
  headers.set('Accept', 'application/json');
  headers.set('Authorization', authorization);
  const workspaceId = normalizeWorkspaceHeaderValue(request.headers.get('x-workspace-id'));
  if (workspaceId) headers.set('X-Workspace-Id', workspaceId);
  return headers;
}

/** Proxy one integrity sub-path. ``method`` is GET for read-only routes. */
export async function proxyIntegrity(
  request: Request,
  assetId: string,
  suffix: string,
  method: 'GET' | 'POST',
  timeoutMessage: string,
): Promise<Response> {
  const resolved = resolveBackend();
  if (resolved instanceof Response) return resolved;
  const headers = baseHeaders(request);
  if (!headers) {
    return jsonError(401, {
      detail: 'Authorization is required.',
      code: 'missing_authorization',
      transport: 'same-origin proxy',
    });
  }
  if (method === 'POST') {
    const csrfToken = request.headers.get('x-csrf-token');
    if (csrfToken) headers.set('X-CSRF-Token', csrfToken);
  }
  try {
    const response = await fetchWithTimeout(
      `${resolved.url}/assets/${encodeURIComponent(assetId)}/integrity${suffix}`,
      { method, headers, cache: 'no-store' },
      PROXY_TIMEOUT_MS,
    );
    return buildProxyResponse(response);
  } catch (error) {
    if (error instanceof FetchTimeoutError) {
      return jsonError(504, { detail: timeoutMessage, code: 'backend_timeout', transport: 'same-origin proxy' });
    }
    return jsonError(502, { detail: 'Backend unreachable.', code: 'backend_unreachable', transport: 'same-origin proxy' });
  }
}
