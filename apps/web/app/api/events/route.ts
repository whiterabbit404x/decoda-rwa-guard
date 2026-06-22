import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for GET /events (audit logs) so the Evidence & Audit page
// reaches the backend through the server-resolved URL when NEXT_PUBLIC_API_URL is unset.
export async function GET(request: Request): Promise<Response> {
  return proxyJsonToBackend(request, {
    backendPath: '/events',
    method: 'GET',
  });
}
