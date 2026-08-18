import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for resending a pending invitation (Settings Team tab).
export async function POST(request: Request, { params }: { params: Promise<{ invitationId: string }> }): Promise<Response> {
  const { invitationId } = await params;
  return proxyJsonToBackend(request, { backendPath: `/workspace/invitations/${encodeURIComponent(invitationId)}/resend`, method: 'POST', forwardBody: true });
}
