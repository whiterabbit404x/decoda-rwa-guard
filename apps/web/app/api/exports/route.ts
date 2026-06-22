import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for GET /exports so the Evidence & Audit page reaches the
// backend through the server-resolved URL when NEXT_PUBLIC_API_URL is unset.
// Supports ?package_id, ?action_id, and ?incident_id filter params.
export async function GET(request: Request): Promise<Response> {
  const url = new URL(request.url);
  return proxyJsonToBackend(request, {
    backendPath: '/exports',
    method: 'GET',
    searchParams: url.searchParams,
  });
}
