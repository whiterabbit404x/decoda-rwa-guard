import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for POST /response/actions/{id}/execute. The backend
// re-validates every prerequisite (persisted state, approvals, live-execution
// configuration, workspace isolation) and returns the canonical persisted
// state; the client never chooses the final execution status.
export async function POST(
  request: Request,
  { params }: { params: Promise<{ actionId: string }> },
): Promise<Response> {
  const { actionId } = await params;
  return proxyJsonToBackend(request, {
    backendPath: `/response/actions/${actionId}/execute`,
    method: 'POST',
  });
}
