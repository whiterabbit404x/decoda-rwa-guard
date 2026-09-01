import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for the immutable policy version history (Screen 11 "View History").
export async function GET(request: Request, { params }: { params: Promise<{ policyRef: string }> }): Promise<Response> {
  const { policyRef } = await params;
  return proxyJsonToBackend(request, { backendPath: `/workspace/governance/policies/${encodeURIComponent(policyRef)}/history`, method: 'GET' });
}
