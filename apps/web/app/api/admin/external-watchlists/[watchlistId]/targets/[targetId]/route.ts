import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

type Params = { params: Promise<{ watchlistId: string; targetId: string }> };

export async function PATCH(request: Request, { params }: Params): Promise<Response> {
  const { watchlistId, targetId } = await params;
  return proxyJsonToBackend(request, {
    backendPath: `/admin/external-watchlists/${encodeURIComponent(watchlistId)}/targets/${encodeURIComponent(targetId)}`,
    method: 'PATCH',
    forwardBody: true,
  });
}

export async function DELETE(request: Request, { params }: Params): Promise<Response> {
  const { watchlistId, targetId } = await params;
  return proxyJsonToBackend(request, {
    backendPath: `/admin/external-watchlists/${encodeURIComponent(watchlistId)}/targets/${encodeURIComponent(targetId)}`,
    method: 'DELETE',
  });
}
