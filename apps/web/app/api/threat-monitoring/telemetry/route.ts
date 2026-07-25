import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin read-only proxy for the normalized telemetry list (Screen 5,
// Telemetry tab). Filters (event type / asset / source / pagination) forwarded verbatim.
export async function GET(request: Request): Promise<Response> {
  return proxyJsonToBackend(request, {
    backendPath: '/threat-monitoring/telemetry',
    method: 'GET',
    searchParams: new URL(request.url).searchParams,
  });
}
