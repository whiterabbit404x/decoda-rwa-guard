import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// POST /api/onboarding/sessions/{id}/discover — start deterministic discovery. Same-origin
// proxy; the backend returns a structured snapshot (a normal EOA yields a failed
// verify_bytecode step with NO_CONTRACT_BYTECODE, not a browser network error).
export async function POST(
  request: Request,
  { params }: { params: Promise<{ sessionId: string }> },
): Promise<Response> {
  const { sessionId } = await params;
  return proxyJsonToBackend(request, {
    backendPath: `/api/onboarding/sessions/${encodeURIComponent(sessionId)}/discover`,
    method: 'POST',
    forwardBody: true,
  });
}
