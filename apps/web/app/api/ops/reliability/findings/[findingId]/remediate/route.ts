import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for POST /ops/reliability/findings/{id}/remediate (Screen 12
// policy-gated remediation). Forwards Authorization + X-Workspace-Id + CSRF so
// the backend enforces security.manage + reauthentication. The frontend never
// authorizes a restart — authorization is decided server-side.
export async function POST(
  request: Request,
  { params }: { params: Promise<{ findingId: string }> },
): Promise<Response> {
  const { findingId } = await params;
  if (!findingId || typeof findingId !== 'string') {
    return Response.json(
      { detail: 'Missing findingId.', code: 'invalid_params' },
      { status: 400, headers: { 'Cache-Control': 'no-store' } },
    );
  }
  return proxyJsonToBackend(request, {
    backendPath: `/ops/reliability/findings/${encodeURIComponent(findingId)}/remediate`,
    method: 'POST',
  });
}
