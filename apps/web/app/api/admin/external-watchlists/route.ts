import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// External Watchlist directory and "Monitor Public Protocol". The backend authorizes
// internal staff and checks EXTERNAL_WATCHLIST_ENABLED on every request; this proxy
// performs no authorization of its own and forwards only allowlisted query params.

export async function GET(request: Request): Promise<Response> {
  const url = new URL(request.url);
  const searchParams = new URLSearchParams();
  for (const key of ['q', 'network', 'status', 'limit', 'offset']) {
    const value = url.searchParams.get(key);
    if (value) {
      searchParams.set(key, value);
    }
  }
  return proxyJsonToBackend(request, {
    backendPath: `/admin/external-watchlists`,
    method: 'GET',
    searchParams,
  });
}

export async function POST(request: Request): Promise<Response> {
  return proxyJsonToBackend(request, {
    backendPath: `/admin/external-watchlists`,
    method: 'POST',
    forwardBody: true,
  });
}
