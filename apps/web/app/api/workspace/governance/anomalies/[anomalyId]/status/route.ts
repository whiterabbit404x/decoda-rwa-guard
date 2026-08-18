import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for acknowledging/resolving/dismissing an anomaly (Screen 11).
export async function POST(request: Request, { params }: { params: Promise<{ anomalyId: string }> }): Promise<Response> {
  const { anomalyId } = await params;
  return proxyJsonToBackend(request, { backendPath: `/workspace/governance/anomalies/${encodeURIComponent(anomalyId)}/status`, method: 'POST', forwardBody: true });
}
