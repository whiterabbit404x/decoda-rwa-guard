import { normalizeApiBaseUrl } from 'app/api-config';
import { getRuntimeConfig } from 'app/runtime-config';
import { normalizeWorkspaceHeaderValue } from 'app/workspace-header';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin SSE proxy for live onboarding progress. Mirrors /api/stream/alerts: streams the
// backend Server-Sent Events response body through unbuffered so the browser never opens a
// cross-origin EventSource against a non-browser-reachable backend URL. The client falls back
// to polling GET /api/onboarding/sessions/{id} when this stream is unavailable.
const FORWARDED_REQUEST_HEADERS = [
  'authorization',
  'x-workspace-id',
  'x-csrf-token',
  'cookie',
  'last-event-id',
] as const;

function buildForwardHeaders(request: Request): Headers {
  const headers = new Headers();
  headers.set('Accept', 'text/event-stream');
  headers.set('Cache-Control', 'no-cache');

  for (const name of FORWARDED_REQUEST_HEADERS) {
    const value = request.headers.get(name);
    if (value === null) continue;
    if (name === 'x-workspace-id') {
      const workspaceId = normalizeWorkspaceHeaderValue(value);
      if (workspaceId) headers.set(name, workspaceId);
      continue;
    }
    headers.set(name, value);
  }

  return headers;
}

export async function GET(
  request: Request,
  { params }: { params: Promise<{ sessionId: string }> },
): Promise<Response> {
  const { sessionId } = await params;
  const runtimeConfig = getRuntimeConfig();
  const backendApiUrl = normalizeApiBaseUrl(runtimeConfig.apiUrl);

  if (!runtimeConfig.configured || !backendApiUrl) {
    return Response.json(
      {
        detail: runtimeConfig.diagnostic ?? 'Web runtime proxy is not configured with a valid backend API URL.',
        code: 'invalid_runtime_config',
      },
      { status: 500 },
    );
  }

  const workspaceId = normalizeWorkspaceHeaderValue(request.headers.get('x-workspace-id') ?? '');
  if (!workspaceId) {
    return Response.json(
      { detail: 'Missing or invalid workspace context.', code: 'WORKSPACE_REQUIRED' },
      { status: 400 },
    );
  }

  const backendUrl = `${backendApiUrl}/api/onboarding/sessions/${encodeURIComponent(sessionId)}/events`;

  let backendResponse: Response;
  try {
    backendResponse = await fetch(backendUrl, {
      method: 'GET',
      headers: buildForwardHeaders(request),
      signal: request.signal,
    });
  } catch (err) {
    if (err instanceof Error && err.name === 'AbortError') {
      return new Response(null, { status: 499 });
    }
    return Response.json(
      { detail: 'Onboarding event stream backend unreachable.', code: 'backend_unreachable' },
      { status: 502 },
    );
  }

  if (!backendResponse.ok || !backendResponse.body) {
    const body = await backendResponse.json().catch(() => ({ detail: 'Onboarding event stream unavailable.' })) as Record<string, unknown>;
    return Response.json(body, { status: backendResponse.status });
  }

  return new Response(backendResponse.body, {
    status: 200,
    headers: {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache',
      'X-Accel-Buffering': 'no',
    },
  });
}
