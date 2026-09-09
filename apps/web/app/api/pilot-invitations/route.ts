import { normalizeApiBaseUrl } from 'app/api-config';
import { getRuntimeConfig } from 'app/runtime-config';
import { FetchTimeoutError, fetchWithTimeout } from 'app/fetch-with-timeout';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

const PROXY_TIMEOUT_MS = 15000;

// Same-origin proxy for GET /pilot-invitations?token=… — unauthenticated,
// because someone opening an invitation link has not signed in yet. The token is
// the credential; the backend resolves it by hash and decides what, if anything,
// to describe. Nothing about the invitation is inferred here.
export async function GET(request: Request): Promise<Response> {
  const runtimeConfig = getRuntimeConfig();
  const backendApiUrl = normalizeApiBaseUrl(runtimeConfig.apiUrl);
  if (!runtimeConfig.configured || !backendApiUrl) {
    return Response.json(
      {
        detail: runtimeConfig.diagnostic ?? 'Web runtime proxy is not configured with a valid backend API URL.',
        code: 'invalid_runtime_config',
      },
      { status: 500, headers: { 'Cache-Control': 'no-store' } },
    );
  }
  const token = new URL(request.url).searchParams.get('token') ?? '';
  const search = new URLSearchParams({ token });
  try {
    const response = await fetchWithTimeout(
      `${backendApiUrl}/pilot-invitations?${search.toString()}`,
      { method: 'GET', headers: { Accept: 'application/json' }, cache: 'no-store' },
      PROXY_TIMEOUT_MS,
    );
    const payload = await response.json().catch(() => ({ detail: 'Backend returned invalid JSON.' }));
    return Response.json(payload, { status: response.status, headers: { 'Cache-Control': 'no-store' } });
  } catch (error) {
    if (error instanceof FetchTimeoutError) {
      return Response.json(
        { detail: 'Timed out waiting for backend response.', code: 'backend_timeout' },
        { status: 504, headers: { 'Cache-Control': 'no-store' } },
      );
    }
    return Response.json(
      { detail: 'Backend unreachable.', code: 'backend_unreachable' },
      { status: 502, headers: { 'Cache-Control': 'no-store' } },
    );
  }
}
