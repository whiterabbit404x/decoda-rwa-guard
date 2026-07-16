import { normalizeApiBaseUrl } from 'app/api-config';
import { getRuntimeConfig } from 'app/runtime-config';
import { normalizeWorkspaceHeaderValue } from 'app/workspace-header';
import { FetchTimeoutError, fetchWithTimeout } from 'app/fetch-with-timeout';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// A real diagnostic performs live RPC I/O across up to a handful of targets, so it
// is allowed a longer proxy budget than the deterministic health check.
const PROXY_TIMEOUT_MS = 60000;

function jsonError(status: number, body: Record<string, unknown>) {
  return Response.json(body, {
    status,
    headers: { 'Cache-Control': 'no-store', 'Content-Type': 'application/json' },
  });
}

export async function POST(request: Request) {
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

  const authorization = request.headers.get('authorization');
  if (!authorization) {
    return jsonError(401, { detail: 'Authorization is required.', code: 'missing_authorization', transport: 'same-origin proxy' });
  }

  const headers = new Headers();
  headers.set('Accept', 'application/json');
  headers.set('Content-Type', 'application/json');
  headers.set('Authorization', authorization);
  const workspaceId = normalizeWorkspaceHeaderValue(request.headers.get('x-workspace-id'));
  if (workspaceId) {
    headers.set('X-Workspace-Id', workspaceId);
  }
  const csrfToken = request.headers.get('x-csrf-token');
  if (csrfToken) {
    headers.set('X-CSRF-Token', csrfToken);
  }

  // Forward an optional { target_id } body so a single-source diagnostic works too.
  const forwardedBody = await request.text().catch(() => '');

  try {
    const response = await fetchWithTimeout(
      `${backendApiUrl}/monitoring/sources/diagnostic`,
      { method: 'POST', headers, cache: 'no-store', body: forwardedBody || '{}' },
      PROXY_TIMEOUT_MS,
    );
    const payload = await response.json().catch(() => ({}));
    return Response.json(payload, { status: response.status, headers: { 'Cache-Control': 'no-store' } });
  } catch (error) {
    if (error instanceof FetchTimeoutError) {
      return jsonError(504, { detail: 'Timed out waiting for backend diagnostic.', code: 'backend_timeout', transport: 'same-origin proxy' });
    }
    return jsonError(502, { detail: 'Backend unreachable.', code: 'backend_unreachable', transport: 'same-origin proxy' });
  }
}
