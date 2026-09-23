import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Queues a historical backfill. The scan runs in the external watchlist worker,
// never inside this request.

type Params = { params: Promise<{ watchlistId: string }> };

export async function POST(request: Request, { params }: Params): Promise<Response> {
  const { watchlistId } = await params;
  return proxyJsonToBackend(request, {
    backendPath: `/admin/external-watchlists/${encodeURIComponent(watchlistId)}/backfill`,
    method: 'POST',
    forwardBody: true,
  });
}
