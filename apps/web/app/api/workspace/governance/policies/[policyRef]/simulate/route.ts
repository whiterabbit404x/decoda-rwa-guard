import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for the READ-ONLY policy simulation (Screen 11 "Run Simulation").
// The ALLOW/DENY verdict is produced by the backend's deterministic policy engine; this
// route only relays the request and preserves the backend's status. It has no execution
// path of its own and never calls a response/action endpoint.
export async function POST(request: Request, { params }: { params: Promise<{ policyRef: string }> }): Promise<Response> {
  const { policyRef } = await params;
  return proxyJsonToBackend(request, { backendPath: `/workspace/governance/policies/${encodeURIComponent(policyRef)}/simulate`, method: 'POST', forwardBody: true });
}
