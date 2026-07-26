import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin read-only proxy for the promoted threat detections list (Screen 5,
// Detections tab). Filters (type / severity / status / asset / confidence / window /
// pagination) are forwarded verbatim to the backend.
export async function GET(request: Request): Promise<Response> {
  return proxyJsonToBackend(request, {
    backendPath: '/threat-monitoring/detections',
    method: 'GET',
    searchParams: new URL(request.url).searchParams,
  });
}
