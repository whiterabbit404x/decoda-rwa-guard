import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for POST /admin/pilot-requests/{id}/approve. Internal-admin
// authorization happens once, server-side, before any request data is read.
type Params = { params: Promise<{ requestId: string }> };

export async function POST(request: Request, { params }: Params): Promise<Response> {
  const { requestId } = await params;
  return proxyJsonToBackend(request, {
    backendPath: `/admin/pilot-requests/${encodeURIComponent(requestId)}/approve`,
    method: 'POST',
    forwardBody: true,
  });
}
