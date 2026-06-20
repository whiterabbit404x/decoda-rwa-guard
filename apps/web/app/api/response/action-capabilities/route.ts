import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for GET /response/action-capabilities (Alerts page action capability map).
export async function GET(request: Request): Promise<Response> {
  return proxyJsonToBackend(request, {
    backendPath: '/response/action-capabilities',
    method: 'GET',
  });
}
