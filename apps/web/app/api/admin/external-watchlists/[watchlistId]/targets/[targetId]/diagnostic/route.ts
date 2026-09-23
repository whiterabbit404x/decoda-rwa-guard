import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Read-only diagnostic: eth_chainId / eth_blockNumber / eth_getCode on the target's
// network. The backend's RPC gateway refuses every non-read method.

type Params = { params: Promise<{ watchlistId: string; targetId: string }> };

export async function POST(request: Request, { params }: Params): Promise<Response> {
  const { watchlistId, targetId } = await params;
  return proxyJsonToBackend(request, {
    backendPath: `/admin/external-watchlists/${encodeURIComponent(watchlistId)}/targets/${encodeURIComponent(targetId)}/diagnostic`,
    method: 'POST',
    forwardBody: true,
  });
}
