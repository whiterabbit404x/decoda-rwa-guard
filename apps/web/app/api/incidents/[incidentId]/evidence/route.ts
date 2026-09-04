import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for the Screen 7 forensic evidence directory.
// GET /incidents/{id}/evidence — the incident's collected artifacts grouped into
// the four provenance domains, the evidence-snapshot integrity state, and the
// linked Screen 9 evidence package when one exists.
export async function GET(
  request: Request,
  { params }: { params: Promise<{ incidentId: string }> },
): Promise<Response> {
  const { incidentId } = await params;
  return proxyJsonToBackend(request, {
    backendPath: `/incidents/${encodeURIComponent(incidentId)}/evidence`,
    method: 'GET',
  });
}
