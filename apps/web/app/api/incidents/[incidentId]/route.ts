import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for the incident detail surface so /incidents/{id} ("View Incident") always
// loads the persisted incident through the server-resolved backend URL.
// GET   /incidents/{id} — incident detail (deep-link load)
// PATCH /incidents/{id} — workflow/owner updates
export async function GET(
  request: Request,
  { params }: { params: Promise<{ incidentId: string }> },
): Promise<Response> {
  const { incidentId } = await params;
  return proxyJsonToBackend(request, {
    backendPath: `/incidents/${encodeURIComponent(incidentId)}`,
    method: 'GET',
  });
}

export async function PATCH(
  request: Request,
  { params }: { params: Promise<{ incidentId: string }> },
): Promise<Response> {
  const { incidentId } = await params;
  return proxyJsonToBackend(request, {
    backendPath: `/incidents/${encodeURIComponent(incidentId)}`,
    method: 'PATCH',
    forwardBody: true,
  });
}
