import { normalizeApiBaseUrl } from 'app/api-config';
import { getRuntimeConfig } from 'app/runtime-config';
import { normalizeWorkspaceHeaderValue } from 'app/workspace-header';
import { FetchTimeoutError, fetchWithTimeout } from 'app/fetch-with-timeout';

// Same-origin JSON proxy helper for backend endpoints that the browser must NOT call
// directly. The backend base URL is resolved server-side from getRuntimeConfig() (API_URL),
// which the browser never sees — only NEXT_PUBLIC_API_URL is exposed client-side and it may
// be unset in production. Routing through this proxy is why telemetry / runtime-status work
// and is the transport every customer-facing read/write must use. Mirrors the existing
// telemetry/targets proxy routes (auth, workspace, CSRF forwarding; status preserved).

const PROXY_TIMEOUT_MS = 30000;

function jsonError(status: number, body: Record<string, unknown>): Response {
  return Response.json(body, {
    status,
    headers: { 'Cache-Control': 'no-store', 'Content-Type': 'application/json' },
  });
}

export type BackendProxyOptions = {
  /** Backend path beginning with a slash, e.g. '/alerts' or '/alerts/open-from-detection'. */
  backendPath: string;
  method: 'GET' | 'POST' | 'PATCH';
  /** Query params forwarded verbatim to the backend (used by the list endpoint filters). */
  searchParams?: URLSearchParams;
  /** Forward the JSON request body (POST/PATCH). */
  forwardBody?: boolean;
};

export async function proxyJsonToBackend(
  request: Request,
  options: BackendProxyOptions,
): Promise<Response> {
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

  const authorization = request.headers.get('authorization')?.trim() || null;
  if (!authorization) {
    return jsonError(401, {
      detail: 'Authorization is required.',
      code: 'missing_authorization',
      transport: 'same-origin proxy',
    });
  }

  const headers = new Headers();
  headers.set('Accept', 'application/json');
  headers.set('Authorization', authorization);
  const workspaceId = normalizeWorkspaceHeaderValue(request.headers.get('x-workspace-id'));
  if (workspaceId) {
    headers.set('X-Workspace-Id', workspaceId);
  }
  const csrfToken = request.headers.get('x-csrf-token');
  if (csrfToken) {
    headers.set('X-CSRF-Token', csrfToken);
  }

  let body: string | undefined;
  if (options.forwardBody) {
    headers.set('Content-Type', 'application/json');
    const parsed = await request.json().catch(() => ({}));
    body = JSON.stringify(parsed ?? {});
  }

  const qs = options.searchParams?.toString();
  const url = `${backendApiUrl}${options.backendPath}${qs ? `?${qs}` : ''}`;

  try {
    const response = await fetchWithTimeout(
      url,
      { method: options.method, headers, cache: 'no-store', body },
      PROXY_TIMEOUT_MS,
    );
    const contentType = response.headers.get('content-type') ?? '';
    const isJson = contentType.toLowerCase().includes('application/json');
    const payload = isJson
      ? await response.json().catch(() => ({ detail: 'Backend returned invalid JSON.' }))
      : {
          detail:
            (await response.text().catch(() => '')).trim() ||
            (response.ok ? 'Request completed.' : 'Request failed. Please try again.'),
        };
    // Preserve the backend status code so the frontend can distinguish 201 (created) /
    // 409 (already_exists) / 200 (suppressed) on the Open Alert path.
    return Response.json(payload, {
      status: response.status,
      headers: { 'Cache-Control': 'no-store' },
    });
  } catch (error) {
    if (error instanceof FetchTimeoutError) {
      return jsonError(504, {
        detail: 'Timed out waiting for backend response.',
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
