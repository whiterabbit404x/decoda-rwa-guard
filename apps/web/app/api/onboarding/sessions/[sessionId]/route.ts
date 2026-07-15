import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// GET /api/onboarding/sessions/{id} — authoritative session snapshot (steps, findings,
// benchmark, proposal). Used for restore-on-refresh and the polling fallback. Same-origin
// proxy so the browser never talks to the backend directly.
export async function GET(
  request: Request,
  { params }: { params: Promise<{ sessionId: string }> },
): Promise<Response> {
  const { sessionId } = await params;
  return proxyJsonToBackend(request, {
    backendPath: `/api/onboarding/sessions/${encodeURIComponent(sessionId)}`,
    method: 'GET',
  });
}
