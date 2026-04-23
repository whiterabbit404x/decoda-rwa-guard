import { normalizeApiBaseUrl } from 'app/api-config';
import { getRuntimeConfig } from 'app/runtime-config';
import { normalizeWorkspaceHeaderValue } from 'app/workspace-header';

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

  const requestUrl = `${backendApiUrl}/ops/monitoring/runtime-status`;

  for (let attempt = 0; attempt <= MAX_TIMEOUT_RETRIES; attempt += 1) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), PROXY_TIMEOUT_MS);
    try {
      const response = await fetch(requestUrl, {
        method: 'GET',
        headers: buildForwardHeaders(request),
        cache: 'no-store',
        next: { revalidate: 0 },
        signal: controller.signal,
      });
      clearTimeout(timeoutId);
      const payload = await response.json().catch(() => ({}));
      return Response.json(payload, {
        status: response.status,
        headers: {
          'Cache-Control': 'no-store',
          Vary: 'Authorization, X-Workspace-Id, X-CSRF-Token, Cookie',
        },
      });
    } catch (error) {
      clearTimeout(timeoutId);
      const isTimeout = error instanceof Error && error.name === 'AbortError';
      if (isTimeout && attempt < MAX_TIMEOUT_RETRIES) {
        continue;
      }

      if (isTimeout) {
        return jsonError(504, { detail: 'Timed out waiting for backend runtime status.', code: 'backend_timeout', transport: 'same-origin proxy' });
      }

      return jsonError(502, { detail: 'Backend unreachable.', code: 'backend_unreachable', transport: 'same-origin proxy' });
    }
  }

  return jsonError(504, { detail: 'Timed out waiting for backend runtime status.', code: 'backend_timeout', transport: 'same-origin proxy' });
}
