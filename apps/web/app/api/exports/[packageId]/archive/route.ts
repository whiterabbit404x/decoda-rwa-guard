import { normalizeApiBaseUrl } from 'app/api-config';
import { FetchTimeoutError, fetchWithTimeout } from 'app/fetch-with-timeout';
import { getRuntimeConfig } from 'app/runtime-config';
import { normalizeWorkspaceHeaderValue } from 'app/workspace-header';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Evidence archives are larger than a JSON bundle (original artifacts + manifest +
// signature + report), so this allows more time than the JSON proxy.
const PROXY_TIMEOUT_MS = 60_000;

function jsonError(httpStatus: number, body: Record<string, unknown>): Response {
  return Response.json(body, {
    status: httpStatus,
    headers: { 'Cache-Control': 'no-store' },
  });
}

// Proxy GET /api/exports/[packageId]/archive → backend GET /exports/{id}/archive.
// Streams the structured evidence ZIP back to the browser. The backend enforces
// evidence.export and workspace scoping; this route never widens either, never
// exposes the backend URL or object-storage credentials, and never issues a
// signed URL that would bypass those checks.
//
// The filename comes from the BACKEND's Content-Disposition (derived from the
// server-generated package number). The [packageId] path segment is only ever
// URL-encoded into the upstream path — it is never used to build a filename, so a
// crafted package id cannot influence what the browser writes to disk.
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

  const backendUrl = `${backendApiUrl}/exports/${encodeURIComponent(packageId)}/archive`;

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
      : { detail: (await response.text().catch(() => '')).trim() || 'Evidence package download failed.' };
    return Response.json(payload as Record<string, unknown>, {
      status: response.status,
      headers: { 'Cache-Control': 'no-store' },
    });
  }

  const buffer = await response.arrayBuffer();
  return new Response(buffer, {
    status: 200,
    headers: {
      'Content-Type': response.headers.get('content-type') ?? 'application/zip',
      'Content-Disposition':
        response.headers.get('content-disposition') ?? 'attachment; filename="evidence-package.zip"',
      // Sealed evidence must never be cached by an intermediary or the browser.
      'Cache-Control': 'no-store, no-cache, must-revalidate, private, no-transform',
      'X-Content-Type-Options': 'nosniff',
    },
  });
}
