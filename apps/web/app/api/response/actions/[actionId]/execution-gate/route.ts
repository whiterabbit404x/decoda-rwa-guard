import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for the Screen 8 deterministic execution gate. GET-only: it
// reads the composed gate (policy verdict + role-scoped human quorum + RBAC +
// expiry + incident state) and never generates or executes anything. The gate the
// UI renders is informational — POST /execute re-evaluates the same gate
// server-side, so a client that ignored this response still hits the lock.
export async function GET(
  request: Request,
  { params }: { params: Promise<{ actionId: string }> },
): Promise<Response> {
  const { actionId } = await params;
  return proxyJsonToBackend(request, {
    backendPath: `/response/actions/${encodeURIComponent(actionId)}/execution-gate`,
    method: 'GET',
  });
}
