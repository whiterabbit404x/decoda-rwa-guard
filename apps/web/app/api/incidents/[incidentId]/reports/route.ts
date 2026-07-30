import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for generating a persisted, cited forensic investigation report.
// POST /incidents/{id}/reports — deterministic forensic report (+ AI narrative when
// available). Does not overwrite prior AI report versions; executes nothing.
export async function POST(
  request: Request,
  { params }: { params: Promise<{ incidentId: string }> },
): Promise<Response> {
  const { incidentId } = await params;
  return proxyJsonToBackend(request, {
    backendPath: `/incidents/${encodeURIComponent(incidentId)}/reports`,
    method: 'POST',
  });
}
