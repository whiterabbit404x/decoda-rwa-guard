import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for approving/rejecting a governance change request (Screen 11).
export async function POST(request: Request, { params }: { params: Promise<{ requestId: string }> }): Promise<Response> {
  const { requestId } = await params;
  return proxyJsonToBackend(request, { backendPath: `/workspace/governance/approvals/${encodeURIComponent(requestId)}/decision`, method: 'POST', forwardBody: true });
}
