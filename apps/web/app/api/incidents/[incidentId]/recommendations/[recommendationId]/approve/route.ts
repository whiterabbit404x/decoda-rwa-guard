import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Approve an AI recommendation. Records the human decision; executes no on-chain action.
export async function POST(
  request: Request,
  { params }: { params: Promise<{ incidentId: string; recommendationId: string }> },
): Promise<Response> {
  const { incidentId, recommendationId } = await params;
  return proxyJsonToBackend(request, {
    backendPath: `/incidents/${encodeURIComponent(incidentId)}/recommendations/${encodeURIComponent(recommendationId)}/approve`,
    method: 'POST',
    forwardBody: true,
  });
}
