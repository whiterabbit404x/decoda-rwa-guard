import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for GET /exports/{packageId} — the full evidence package
// report (files, completeness, chain evidence, integrity, agent findings).
export async function GET(
  request: Request,
  { params }: { params: Promise<{ packageId: string }> },
): Promise<Response> {
  const { packageId } = await params;
  return proxyJsonToBackend(request, {
    backendPath: `/exports/${encodeURIComponent(packageId)}`,
    method: 'GET',
  });
}
