import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Proxy POST /api/exports/[packageId]/regenerate → backend POST /exports/{packageId}/regenerate.
// The Screen 9 "Regenerate Package" recovery fallback: when a Manifest-Missing
// package cannot safely receive an in-place manifest, the backend creates a NEW
// superseding proof bundle for the same incident and leaves the original untouched.
// Regeneration is authoritative on the backend — the browser only triggers it and
// displays the resulting package. Never exposes the backend URL or credentials.
export async function POST(
  request: Request,
  { params }: { params: Promise<{ packageId: string }> },
): Promise<Response> {
  const { packageId } = await params;
  return proxyJsonToBackend(request, {
    backendPath: `/exports/${encodeURIComponent(packageId)}/regenerate`,
    method: 'POST',
  });
}
