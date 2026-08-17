import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Dismiss an integration recommendation. The backend re-checks the
// monitoring.configure permission and audits the decision — button state is never authz.
export async function POST(
  request: Request,
  { params }: { params: Promise<{ recommendationId: string }> },
): Promise<Response> {
  const { recommendationId } = await params;
  return proxyJsonToBackend(request, {
    backendPath: `/integrations/recommendations/${encodeURIComponent(recommendationId)}/dismiss`,
    method: 'POST',
  });
}
