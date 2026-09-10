import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for GET /exports/history — the Screen 9 Export History tab.
// A static route segment, so Next.js matches it ahead of /api/exports/[packageId].
// Pagination params are forwarded verbatim; the backend clamps them, so a client
// can never request an unbounded history page.
export async function GET(request: Request): Promise<Response> {
  const requested = new URL(request.url).searchParams;
  const searchParams = new URLSearchParams();
  for (const key of ['limit', 'offset'] as const) {
    const value = requested.get(key);
    if (value) searchParams.set(key, value);
  }
  return proxyJsonToBackend(request, {
    backendPath: '/exports/history',
    method: 'GET',
    searchParams,
  });
}
