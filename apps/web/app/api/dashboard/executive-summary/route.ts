import { normalizeApiBaseUrl } from 'app/api-config';
import { getRuntimeConfig } from 'app/runtime-config';
import { normalizeWorkspaceHeaderValue } from 'app/workspace-header';
import { FetchTimeoutError, fetchWithTimeout } from 'app/fetch-with-timeout';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

const PROXY_TIMEOUT_MS = 45000;
const MAX_TIMEOUT_RETRIES = 1;
const FORWARDED_HEADERS = ['authorization', 'x-workspace-id', 'x-csrf-token', 'cookie'] as const;

function jsonError(status: number, body: Record<string, unknown>) {
  return Response.json(body, {
    status,
    headers: {
      'Cache-Control': 'no-store',
      'Content-Type': 'application/json',
    },
  });
}

function buildForwardHeaders(request: Request) {
  const headers = new Headers();
  headers.set('Accept', 'application/json');

  FORWARDED_HEADERS.forEach((name) => {
    const value = request.headers.get(name);
    if (value === null) {
      return;
    }
    if (name === 'x-workspace-id') {
      const workspaceId = normalizeWorkspaceHeaderValue(value);
      if (workspaceId) {
        headers.set(name, workspaceId);
      }
      return;
    }
    headers.set(name, value);
  });

  return headers;
}

export async function GET(request: Request) {
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

  const requestUrl = `${backendApiUrl}/ops/dashboard/executive-summary`;

  for (let attempt = 0; attempt <= MAX_TIMEOUT_RETRIES; attempt += 1) {
    try {
      const response = await fetchWithTimeout(requestUrl, {
        method: 'GET',
        headers: buildForwardHeaders(request),
        cache: 'no-store',
        next: { revalidate: 0 },
      }, PROXY_TIMEOUT_MS);
      const payload = await response.json().catch(() => ({}));
      return Response.json(payload, {
        status: response.status,
        headers: {
          'Cache-Control': 'no-store',
          Vary: 'Authorization, X-Workspace-Id, X-CSRF-Token, Cookie',
        },
      });
    } catch (error) {
      const isTimeout = error instanceof FetchTimeoutError;
      if (isTimeout && attempt < MAX_TIMEOUT_RETRIES) {
        continue;
      }

      if (isTimeout) {
        return jsonError(504, { detail: 'Timed out waiting for the dashboard executive summary.', code: 'backend_timeout', transport: 'same-origin proxy' });
      }

      return jsonError(502, { detail: 'Backend unreachable.', code: 'backend_unreachable', transport: 'same-origin proxy' });
    }
  }

  return jsonError(504, { detail: 'Timed out waiting for the dashboard executive summary.', code: 'backend_timeout', transport: 'same-origin proxy' });
}
