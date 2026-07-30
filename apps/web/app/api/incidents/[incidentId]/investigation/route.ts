import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for the Digital Forensics Investigator (Screen 7) overview.
// GET /incidents/{id}/investigation — deterministic forensic analysis + workflow +
// AI triage status summary for the incident.
export async function GET(
  request: Request,
  { params }: { params: Promise<{ incidentId: string }> },
): Promise<Response> {
  const { incidentId } = await params;
  return proxyJsonToBackend(request, {
    backendPath: `/incidents/${encodeURIComponent(incidentId)}/investigation`,
    method: 'GET',
  });
}
