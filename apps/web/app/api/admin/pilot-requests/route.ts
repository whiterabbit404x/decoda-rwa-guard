import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for GET /admin/pilot-requests. Performs NO authorization of
// its own: the backend authorizes internal staff server-side and returns 403 to
// a customer, and a pending request is never visible to anyone else.
export async function GET(request: Request): Promise<Response> {
  const url = new URL(request.url);
  const searchParams = new URLSearchParams();
  for (const key of ['status', 'limit', 'offset']) {
    const value = url.searchParams.get(key);
    if (value) {
      searchParams.set(key, value);
    }
  }
  return proxyJsonToBackend(request, {
    backendPath: '/admin/pilot-requests',
    method: 'GET',
    searchParams,
  });
}
