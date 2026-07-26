import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin read-only proxy for the canonical Threat Monitoring summary (Screen 5).
// GET only — opening/refreshing Screen 5 never mutates state on the backend.
export async function GET(request: Request): Promise<Response> {
  return proxyJsonToBackend(request, {
    backendPath: '/threat-monitoring/summary',
    method: 'GET',
    searchParams: new URL(request.url).searchParams,
  });
}
