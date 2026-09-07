import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for GET /admin/customers. This proxy performs NO
// authorization of its own — the backend authorizes internal staff server-side
// and returns 403 to a customer. Proxy-side checks would be a second, weaker
// copy of a rule that must have exactly one home.
export async function GET(request: Request): Promise<Response> {
  const url = new URL(request.url);
  const searchParams = new URLSearchParams();
  for (const key of ['limit', 'offset']) {
    const value = url.searchParams.get(key);
    if (value) {
      searchParams.set(key, value);
    }
  }
  return proxyJsonToBackend(request, { backendPath: '/admin/customers', method: 'GET', searchParams });
}
