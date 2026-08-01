import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for the deterministic, read-only safety checks of one response
// action. GET-only: never generates or executes anything.
export async function GET(
  request: Request,
  { params }: { params: Promise<{ actionId: string }> },
): Promise<Response> {
  const { actionId } = await params;
  return proxyJsonToBackend(request, {
    backendPath: `/response/actions/${encodeURIComponent(actionId)}/safety-checks`,
    method: 'GET',
  });
}
