import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for GET /incidents (list + count cards on the Incidents page). The browser
// must NOT call the backend directly: only NEXT_PUBLIC_API_URL is exposed client-side and it is
// often unset in production, so a direct fetch silently renders an empty list ("No incidents
// yet") even when escalated incidents exist. Routing through this proxy is the same transport
// the Alerts list and telemetry/runtime-status already use, so /alerts and /incidents agree.
export async function GET(request: Request): Promise<Response> {
  const url = new URL(request.url);
  return proxyJsonToBackend(request, {
    backendPath: '/incidents',
    method: 'GET',
    searchParams: url.searchParams,
  });
}
