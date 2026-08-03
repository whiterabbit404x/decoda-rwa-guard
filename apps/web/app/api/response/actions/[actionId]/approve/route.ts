import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for the Screen 8 "Approve" command. The backend re-checks
// role (owner/admin) and separation of duties (the proposer cannot approve) —
// button visibility is never authorization.
export async function POST(
  request: Request,
  { params }: { params: Promise<{ actionId: string }> },
): Promise<Response> {
  const { actionId } = await params;
  return proxyJsonToBackend(request, {
    backendPath: `/response/actions/${actionId}/approve`,
    method: 'POST',
    forwardBody: true,
  });
}
