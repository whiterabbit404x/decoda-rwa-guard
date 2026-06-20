import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for GET /response/actions (the Incident detail "Response Actions" tab filters
// by ?incident_id=...) so the Incidents page reaches the backend through the server-resolved URL
// like the rest of its reads, instead of silently failing when NEXT_PUBLIC_API_URL is unset.
export async function GET(request: Request): Promise<Response> {
  const url = new URL(request.url);
  return proxyJsonToBackend(request, {
    backendPath: '/response/actions',
    method: 'GET',
    searchParams: url.searchParams,
  });
}
