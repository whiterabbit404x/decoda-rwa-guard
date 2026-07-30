import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for re-running the deterministic forensic investigation.
// POST /incidents/{id}/investigations — rebuild the analysis from a fresh evidence
// snapshot and hand off the AI narrative (idempotent). Never executes a response
// action.
export async function POST(
  request: Request,
  { params }: { params: Promise<{ incidentId: string }> },
): Promise<Response> {
  const { incidentId } = await params;
  return proxyJsonToBackend(request, {
    backendPath: `/incidents/${encodeURIComponent(incidentId)}/investigations`,
    method: 'POST',
  });
}
