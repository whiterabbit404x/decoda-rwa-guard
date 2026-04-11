import { normalizeApiBaseUrl } from 'app/api-config';
import { getRuntimeConfig } from 'app/runtime-config';

export const dynamic = 'force-dynamic';
export const revalidate = 0;
const PROXY_TIMEOUT_MS = 15000;

const JSON_HEADERS = {
  'Cache-Control': 'no-store',
  'Content-Type': 'application/json',
};

function jsonError(status: number, body: Record<string, unknown>) {
  return Response.json(body, {
    status,
    headers: JSON_HEADERS,
  });
}

function readBearerToken(request: Request) {
  const authorizationHeader = request.headers.get('authorization')?.trim();
  if (!authorizationHeader) {
    return null;
  }
  return authorizationHeader;
}

async function readRequestBody(request: Request) {
  try {
    return await request.json();
  } catch {
    return {};
  }
}

async function buildProxyResponse(response: Response) {
  console.info('[monitoring-reconcile-proxy] backend response received', { status: response.status });
  const contentType = response.headers.get('content-type') ?? '';
  const isJson = contentType.toLowerCase().includes('application/json');
  console.info('[monitoring-reconcile-proxy] backend response parsing', { isJson });
  const payload = isJson
    ? await response.json().catch(() => ({ detail: 'Backend returned invalid JSON.' }))
    : {
      detail: (await response.text().catch(() => '')).trim() || (response.ok ? 'Request completed.' : 'Request failed. Please try again.'),
    };
  console.info('[monitoring-reconcile-proxy] backend response parsed');

  return Response.json(payload, {
    status: response.status,
    headers: {
      'Cache-Control': 'no-store',
    },
  });
}

export async function POST(request: Request) {
  const runtimeConfig = getRuntimeConfig();
  const backendApiUrl = normalizeApiBaseUrl(runtimeConfig.apiUrl);

  if (!runtimeConfig.configured || !backendApiUrl) {
    return jsonError(500, {
      detail: runtimeConfig.diagnostic ?? 'Web server runtime proxy is not configured with a valid backend API URL.',
      code: 'invalid_runtime_config',
      transport: 'same-origin proxy',
      backendApiUrl,
      configured: false,
    });
  }

  const authorization = readBearerToken(request);
  if (!authorization) {
    return jsonError(401, {
      detail: 'Authorization is required.',
      code: 'missing_authorization',
      transport: 'same-origin proxy',
      backendApiUrl,
      configured: true,
    });
  }

  const headers = new Headers();
  headers.set('Accept', 'application/json');
  headers.set('Content-Type', 'application/json');
  headers.set('Authorization', authorization);

  const workspaceId = request.headers.get('x-workspace-id');
  if (workspaceId) {
    headers.set('X-Workspace-Id', workspaceId);
  }

  const csrfToken = request.headers.get('x-csrf-token');
  if (csrfToken) {
    headers.set('X-CSRF-Token', csrfToken);
  }

  const body = await readRequestBody(request);
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), PROXY_TIMEOUT_MS);
  console.info('[monitoring-reconcile-proxy] forwarding request to backend', { backendApiUrl });

  try {
    const response = await fetch(`${backendApiUrl}/monitoring/systems/reconcile`, {
      method: 'POST',
      headers,
      cache: 'no-store',
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    clearTimeout(timeoutId);
    return buildProxyResponse(response);
  } catch (error) {
    clearTimeout(timeoutId);
    if (error instanceof Error && error.name === 'AbortError') {
      return jsonError(504, {
        detail: 'Timed out waiting for backend reconcile response.',
        code: 'backend_timeout',
        transport: 'same-origin proxy',
        backendApiUrl,
        configured: true,
      });
    }
    return jsonError(502, {
      detail: 'Backend unreachable.',
      code: 'backend_unreachable',
      transport: 'same-origin proxy',
      backendApiUrl,
      configured: true,
    });
  }
}
