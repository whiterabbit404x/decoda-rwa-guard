import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for POST /admin/pilot-requests/{id}/resend-invitation. Used
// after a delivery failure; the backend mints a FRESH token, so the retry does
// not resend a link that may have leaked from the failed attempt.
type Params = { params: Promise<{ requestId: string }> };

export async function POST(request: Request, { params }: Params): Promise<Response> {
  const { requestId } = await params;
  return proxyJsonToBackend(request, {
    backendPath: `/admin/pilot-requests/${encodeURIComponent(requestId)}/resend-invitation`,
    method: 'POST',
    forwardBody: true,
  });
}
