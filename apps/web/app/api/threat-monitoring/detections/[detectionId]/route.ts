import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin read-only proxy for a single threat detection + its evidence.
export async function GET(
  request: Request,
  { params }: { params: Promise<{ detectionId: string }> },
): Promise<Response> {
  const { detectionId } = await params;
  if (!detectionId || typeof detectionId !== 'string') {
    return Response.json({ detail: 'Missing detection id.', code: 'invalid_params' }, { status: 400 });
  }
  return proxyJsonToBackend(request, {
    backendPath: `/threat-monitoring/detections/${encodeURIComponent(detectionId)}`,
    method: 'GET',
  });
}
