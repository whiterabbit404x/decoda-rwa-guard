import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin read-only proxy for the sub-threshold anomalies list (Screen 5,
// Anomalies tab). Anomalies are deviations that have NOT crossed detection criteria.
export async function GET(request: Request): Promise<Response> {
  return proxyJsonToBackend(request, {
    backendPath: '/threat-monitoring/anomalies',
    method: 'GET',
    searchParams: new URL(request.url).searchParams,
  });
}
