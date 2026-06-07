import { proxyAuthRequest } from 'app/api/auth/_shared/proxy';
export const dynamic = 'force-dynamic';
export async function DELETE(request: Request, context: { params: Promise<{ tokenId: string }> }) {
  const { tokenId } = await context.params;
  return proxyAuthRequest(request, `/workspace/scim/tokens/${encodeURIComponent(tokenId)}`, 'DELETE', { requireAuth: true });
}
