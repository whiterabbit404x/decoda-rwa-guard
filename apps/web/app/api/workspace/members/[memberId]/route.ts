import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for updating a member role / removing a member (Settings Team tab).
export async function PATCH(request: Request, { params }: { params: Promise<{ memberId: string }> }): Promise<Response> {
  const { memberId } = await params;
  return proxyJsonToBackend(request, { backendPath: `/workspace/members/${encodeURIComponent(memberId)}`, method: 'PATCH', forwardBody: true });
}

export async function DELETE(request: Request, { params }: { params: Promise<{ memberId: string }> }): Promise<Response> {
  const { memberId } = await params;
  return proxyJsonToBackend(request, { backendPath: `/workspace/members/${encodeURIComponent(memberId)}`, method: 'DELETE' });
}
