import { normalizeApiBaseUrl } from 'app/api-config';
import { getRuntimeConfig } from 'app/runtime-config';
import { normalizeWorkspaceHeaderValue } from 'app/workspace-header';
import { FetchTimeoutError, fetchWithTimeout } from 'app/fetch-with-timeout';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

const PROXY_TIMEOUT_MS = 30000;

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

  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return jsonError(400, { detail: 'Request body must be JSON.', code: 'invalid_body' });
  }
  const txHash =
    body && typeof body === 'object' && typeof (body as Record<string, unknown>).tx_hash === 'string'
      ? ((body as Record<string, unknown>).tx_hash as string).trim()
      : '';
  if (!txHash) {
    return jsonError(400, { detail: 'tx_hash is required.', code: 'invalid_params' });
  }

  const headers = new Headers();
  headers.set('Accept', 'application/json');
  headers.set('Content-Type', 'application/json');
  const authorization = request.headers.get('authorization');
  if (authorization) {
    headers.set('Authorization', authorization);
  }
  const workspaceId = normalizeWorkspaceHeaderValue(request.headers.get('x-workspace-id'));
  if (workspaceId) {
    headers.set('X-Workspace-Id', workspaceId);
  }

  try {
    const response = await fetchWithTimeout(
      `${backendApiUrl}/ops/monitoring/diagnose-tx`,
      {
        method: 'POST',
        headers,
        cache: 'no-store',
        body: JSON.stringify({ tx_hash: txHash }),
      },
      PROXY_TIMEOUT_MS,
    );
    const payload = await response.json().catch(() => ({}));
    return Response.json(payload, {
      status: response.status,
      headers: { 'Cache-Control': 'no-store' },
    });
  } catch (error) {
    if (error instanceof FetchTimeoutError) {
      return jsonError(504, {
        detail: 'Timed out waiting for backend tx diagnosis.',
        code: 'backend_timeout',
        transport: 'same-origin proxy',
      });
    }
    return jsonError(502, {
      detail: 'Backend unreachable.',
      code: 'backend_unreachable',
      transport: 'same-origin proxy',
    });
  }
}
