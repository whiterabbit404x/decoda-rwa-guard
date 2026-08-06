import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for POST /exports/{packageId}/verify. The backend
// recalculates every file's SHA-256 and the manifest hash, compares them to the
// stored manifest, persists the integrity result, and records an audit event.
// Verification is authoritative on the backend — the browser only displays it.
export async function POST(
  request: Request,
  { params }: { params: Promise<{ packageId: string }> },
): Promise<Response> {
  const { packageId } = await params;
  return proxyJsonToBackend(request, {
    backendPath: `/exports/${encodeURIComponent(packageId)}/verify`,
    method: 'POST',
  });
}
