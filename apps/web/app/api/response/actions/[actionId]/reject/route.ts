import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for the Screen 8 "Reject" command. A rejection reason is
// mandatory and enforced by the backend; the reason body is forwarded verbatim.
// The backend re-checks the approver role — button visibility is not authorization.
export async function POST(
  request: Request,
  { params }: { params: Promise<{ actionId: string }> },
): Promise<Response> {
  const { actionId } = await params;
  return proxyJsonToBackend(request, {
    backendPath: `/response/actions/${actionId}/reject`,
    method: 'POST',
    forwardBody: true,
  });
}
