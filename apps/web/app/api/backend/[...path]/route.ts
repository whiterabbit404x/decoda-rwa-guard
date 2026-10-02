/**
 * /api/backend/* — the browser's only way to the RWA Guard API.
 *
 * Client components call `${apiUrl}/…` with `apiUrl` = '/api/backend' (see
 * BROWSER_API_BASE), so the browser never talks to the API directly and never
 * holds a session token. proxy.ts has already replaced any browser-supplied
 * credentials with the server's own: the bearer token from the HttpOnly
 * `decoda_session` cookie and, for a Decoda session, the BFF secret and the
 * live AuthKit session id. This route forwards them with the request's
 * workspace, CSRF and content headers, and streams the answer back
 * (JSON, downloads and server-sent events alike).
 *
 * Refused here: `/auth/*` (session-issuing endpoints go through
 * /api/auth/*, which keep tokens out of the browser), cross-origin writes, and
 * any path that could leave the API's base URL.
 */
import { normalizeApiBaseUrl } from 'app/api-config';
import { forwardIdentityHeaders } from 'app/api/_shared/identity-headers';
import { isSameOrigin } from 'app/decoda-identity';
import { getRuntimeConfig } from 'app/runtime-config';
import { normalizeWorkspaceHeaderValue } from 'app/workspace-header';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

const HEADERS_TIMEOUT_MS = (() => {
  const configured = Number(process.env.BACKEND_PROXY_HEADERS_TIMEOUT_MS);
  return Number.isFinite(configured) && configured > 0 ? configured : 30_000;
})();

const FORWARDED_REQUEST_HEADERS = ['accept', 'content-type', 'x-csrf-token', 'last-event-id', 'idempotency-key', 'x-request-id'] as const;
const RETURNED_RESPONSE_HEADERS = [
  'content-type',
  'content-disposition',
  'cache-control',
  'etag',
  'last-modified',
  'x-request-id',
  'x-decoda-error-code',
  'x-decoda-correlation-id',
  'x-decoda-db-classification',
  'retry-after',
] as const;
const SEGMENT = /^[A-Za-z0-9._~:@!$&'()*+,;=-]+$/;

function refuse(status: number, code: string, detail: string): Response {
  return Response.json({ detail, code, transport: 'same-origin proxy' }, { status, headers: { 'Cache-Control': 'no-store' } });
}

async function forward(request: Request, context: { params: Promise<{ path: string[] }> }): Promise<Response> {
  const { path } = await context.params;
  const segments = Array.isArray(path) ? path : [];
  if (segments.length === 0 || segments.some((segment) => !SEGMENT.test(segment) || segment === '.' || segment === '..')) {
    return refuse(404, 'not_found', 'Not found.');
  }
  if (segments[0] === 'auth') {
    return refuse(404, 'not_found', 'Not found.');
  }
  const method = request.method.toUpperCase();
  if (!['GET', 'HEAD'].includes(method) && !isSameOrigin(request)) {
    return refuse(403, 'origin_rejected', 'Cross-origin requests are not allowed.');
  }

  const runtimeConfig = getRuntimeConfig();
  const backendApiUrl = normalizeApiBaseUrl(runtimeConfig.apiUrl);
  if (!runtimeConfig.configured || !backendApiUrl) {
    return refuse(500, 'invalid_runtime_config', runtimeConfig.diagnostic ?? 'Web runtime proxy is not configured with a valid backend API URL.');
  }

  const headers = new Headers();
  const authorization = request.headers.get('authorization');
  if (authorization) headers.set('Authorization', authorization);
  forwardIdentityHeaders(request.headers, headers);
  const workspaceId = normalizeWorkspaceHeaderValue(request.headers.get('x-workspace-id'));
  if (workspaceId) headers.set('X-Workspace-Id', workspaceId);
  for (const name of FORWARDED_REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }

  const search = new URL(request.url).search;
  const target = `${backendApiUrl}/${segments.map((segment) => encodeURIComponent(segment)).join('/')}${search}`;

  // A bound on the wait for response HEADERS only: a long download or an
  // event stream must not be cut off once it has started. A client that goes
  // away aborts the upstream call too.
  const controller = new AbortController();
  const abort = () => controller.abort();
  request.signal.addEventListener('abort', abort, { once: true });
  const timer = setTimeout(abort, HEADERS_TIMEOUT_MS);
  let upstream: Response;
  try {
    const hasBody = !['GET', 'HEAD'].includes(method);
    upstream = await fetch(target, {
      method,
      headers,
      body: hasBody ? request.body : undefined,
      cache: 'no-store',
      redirect: 'manual',
      signal: controller.signal,
      ...(hasBody ? { duplex: 'half' } : {}),
    } as RequestInit);
  } catch {
    const timedOut = !request.signal.aborted;
    return refuse(timedOut ? 504 : 499, timedOut ? 'backend_timeout' : 'client_closed', timedOut ? 'Timed out waiting for backend response.' : 'Request cancelled.');
  } finally {
    clearTimeout(timer);
  }

  const responseHeaders = new Headers({ 'Cache-Control': 'no-store' });
  for (const name of RETURNED_RESPONSE_HEADERS) {
    const value = upstream.headers.get(name);
    if (value) responseHeaders.set(name, value);
  }
  if ((upstream.headers.get('content-type') ?? '').includes('text/event-stream')) {
    responseHeaders.set('X-Accel-Buffering', 'no');
  }
  return new Response(upstream.status === 204 || method === 'HEAD' ? null : upstream.body, {
    status: upstream.status >= 300 && upstream.status < 400 ? 502 : upstream.status,
    headers: responseHeaders,
  });
}

export const GET = forward;
export const HEAD = forward;
export const POST = forward;
export const PUT = forward;
export const PATCH = forward;
export const DELETE = forward;
