import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for the persisted forensic investigation workflow stages.
// GET /incidents/{id}/workflow — canonical stage states derived from persisted facts.
export async function GET(
  request: Request,
  { params }: { params: Promise<{ incidentId: string }> },
): Promise<Response> {
  const { incidentId } = await params;
  return proxyJsonToBackend(request, {
    backendPath: `/incidents/${encodeURIComponent(incidentId)}/workflow`,
    method: 'GET',
  });
}
