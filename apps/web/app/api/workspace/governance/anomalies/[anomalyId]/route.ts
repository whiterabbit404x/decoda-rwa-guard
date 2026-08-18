import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for a single access-anomaly finding (Screen 11 anomaly detail).
export async function GET(request: Request, { params }: { params: Promise<{ anomalyId: string }> }): Promise<Response> {
  const { anomalyId } = await params;
  return proxyJsonToBackend(request, { backendPath: `/workspace/governance/anomalies/${encodeURIComponent(anomalyId)}`, method: 'GET' });
}
