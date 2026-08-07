import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';
import { normalizeApiBaseUrl } from 'app/api-config';
import { FetchTimeoutError, fetchWithTimeout } from 'app/fetch-with-timeout';
import { getRuntimeConfig } from 'app/runtime-config';
import { normalizeWorkspaceHeaderValue } from 'app/workspace-header';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

const PROXY_TIMEOUT_MS = 30_000;

function jsonError(httpStatus: number, body: Record<string, unknown>): Response {
  return Response.json(body, {
    status: httpStatus,
    headers: { 'Cache-Control': 'no-store' },
  });
}

// Proxy GET /api/exports/[packageId]/manifest → backend GET /exports/{packageId}/manifest.
// Streams the machine-readable manifest (SHA-256 per file + manifest hash) back to
// the browser as a file download. Never exposes the backend URL or storage credentials.
export async function GET(
  request: Request,
  { params }: { params: Promise<{ packageId: string }> },
): Promise<Response> {
  const { packageId } = await params;

  const runtimeConfig = getRuntimeConfig();
  const backendApiUrl = normalizeApiBaseUrl(runtimeConfig.apiUrl);
  if (!runtimeConfig.configured || !backendApiUrl) {
    return jsonError(500, {
      detail: runtimeConfig.diagnostic ?? 'Web runtime proxy is not configured with a valid backend API URL.',
      code: 'invalid_runtime_config',
    });
  }

  const authorization = request.headers.get('authorization')?.trim() || null;
  if (!authorization) {
    return jsonError(401, { detail: 'Authorization is required.', code: 'missing_authorization' });
  }

  const backendHeaders = new Headers();
  backendHeaders.set('Authorization', authorization);
  const workspaceId = normalizeWorkspaceHeaderValue(request.headers.get('x-workspace-id'));
  if (workspaceId) {
    backendHeaders.set('X-Workspace-Id', workspaceId);
  }

  const backendUrl = `${backendApiUrl}/exports/${encodeURIComponent(packageId)}/manifest`;

  let response: Response;
  try {
    response = await fetchWithTimeout(
      backendUrl,
      { method: 'GET', headers: backendHeaders, cache: 'no-store' },
      PROXY_TIMEOUT_MS,
    );
  } catch (error) {
    if (error instanceof FetchTimeoutError) {
      return jsonError(504, { detail: 'Timed out waiting for backend.', code: 'backend_timeout' });
    }
    return jsonError(502, { detail: 'Backend unreachable.', code: 'backend_unreachable' });
  }

  if (!response.ok) {
    const ct = response.headers.get('content-type') ?? '';
    const isJson = ct.toLowerCase().includes('application/json');
    const payload = isJson
      ? await response.json().catch(() => ({ detail: 'Backend error.' }))
      : { detail: (await response.text().catch(() => '')).trim() || 'Manifest download failed.' };
    return Response.json(payload as Record<string, unknown>, {
      status: response.status,
      headers: { 'Cache-Control': 'no-store' },
    });
  }

  const buffer = await response.arrayBuffer();
  const contentType = response.headers.get('content-type') ?? 'application/json';
  const contentDisposition =
    response.headers.get('content-disposition') ??
    `attachment; filename="evidence-manifest-${packageId}.json"`;

  return new Response(buffer, {
    status: 200,
    headers: {
      'Content-Type': contentType,
      'Content-Disposition': contentDisposition,
      'Cache-Control': 'no-store',
    },
  });
}

// Proxy POST /api/exports/[packageId]/manifest → backend POST /exports/{packageId}/manifest.
// The backend reconstructs a retrievable integrity manifest for a Manifest-Missing
// package from its exact stored bytes, persists it, and returns the canonical
// package state. Recovery is authoritative on the backend — the browser only
// triggers it and displays the result.
export async function POST(
  request: Request,
  { params }: { params: Promise<{ packageId: string }> },
): Promise<Response> {
  const { packageId } = await params;
  return proxyJsonToBackend(request, {
    backendPath: `/exports/${encodeURIComponent(packageId)}/manifest`,
    method: 'POST',
  });
}
