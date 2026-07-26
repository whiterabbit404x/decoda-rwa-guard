import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for Start Investigation (Screen 5). The backend action is
// idempotent: it returns an existing open alert/incident instead of creating a
// duplicate. The backend status code (201 created / 200 existing / 409 / 422) is
// preserved so the client can react precisely.
export async function POST(
  request: Request,
  { params }: { params: Promise<{ detectionId: string }> },
): Promise<Response> {
  const { detectionId } = await params;
  if (!detectionId || typeof detectionId !== 'string') {
    return Response.json({ detail: 'Missing detection id.', code: 'invalid_params' }, { status: 400 });
  }
  return proxyJsonToBackend(request, {
    backendPath: `/threat-monitoring/detections/${encodeURIComponent(detectionId)}/investigate`,
    method: 'POST',
    forwardBody: true,
  });
}
