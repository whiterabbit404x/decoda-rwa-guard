import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for one governance policy (Screen 11). GET reads; PATCH is the
// versioned edit, which the BACKEND gates on security.manage — this proxy only relays
// the request and preserves the backend's status, it never authorizes anything.
export async function GET(request: Request, { params }: { params: Promise<{ policyRef: string }> }): Promise<Response> {
  const { policyRef } = await params;
  return proxyJsonToBackend(request, { backendPath: `/workspace/governance/policies/${encodeURIComponent(policyRef)}`, method: 'GET' });
}

export async function PATCH(request: Request, { params }: { params: Promise<{ policyRef: string }> }): Promise<Response> {
  const { policyRef } = await params;
  return proxyJsonToBackend(request, { backendPath: `/workspace/governance/policies/${encodeURIComponent(policyRef)}`, method: 'PATCH', forwardBody: true });
}
