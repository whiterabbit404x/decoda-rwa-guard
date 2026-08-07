import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for POST /exports/proof-bundle (Create Evidence Package).
// Routing the create through the server-resolved backend URL (API_URL) — exactly
// like every other Screen 9 read/write (list, detail, verify, download, manifest)
// — guarantees the newly created package is persisted in the SAME backend and
// workspace the list GET reads from. Calling the backend directly with the
// browser-exposed NEXT_PUBLIC_API_URL (which may be unset or point at a different
// deployment in production) is why a "queued" toast could appear with no row.
export async function POST(request: Request): Promise<Response> {
  return proxyJsonToBackend(request, {
    backendPath: '/exports/proof-bundle',
    method: 'POST',
    forwardBody: true,
  });
}
