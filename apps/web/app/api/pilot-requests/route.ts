import { normalizeApiBaseUrl } from 'app/api-config';
import { getRuntimeConfig } from 'app/runtime-config';
import { FetchTimeoutError, fetchWithTimeout } from 'app/fetch-with-timeout';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

const PROXY_TIMEOUT_MS = 15000;

// Same-origin proxy for POST /pilot-requests. Unlike every other proxy in this
// app it forwards NO Authorization header, because the applicant has no account
// yet — that is the whole point of the approval-only flow.
//
// This proxy performs no validation and no authorization of its own. The backend
// validates the body, rate-limits by IP + address, and decides what is recorded;
// a second, weaker copy of those rules here would only be able to disagree with
// the one that matters.
export async function POST(request: Request): Promise<Response> {
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

  const body = await request.json().catch(() => ({}));
  try {
    const response = await fetchWithTimeout(
      `${backendApiUrl}/pilot-requests`,
      {
        method: 'POST',
        headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
        body: JSON.stringify(body ?? {}),
        cache: 'no-store',
      },
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
