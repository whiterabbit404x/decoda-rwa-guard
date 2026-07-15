import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// POST /api/onboarding/sessions/{id}/retry — retry only failed / incomplete steps.
export async function POST(
  request: Request,
  { params }: { params: Promise<{ sessionId: string }> },
): Promise<Response> {
  const { sessionId } = await params;
  return proxyJsonToBackend(request, {
    backendPath: `/api/onboarding/sessions/${encodeURIComponent(sessionId)}/retry`,
    method: 'POST',
    forwardBody: true,
  });
}
