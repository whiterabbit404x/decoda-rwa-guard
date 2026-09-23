import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

type Params = { params: Promise<{ watchlistId: string }> };

export async function GET(request: Request, { params }: Params): Promise<Response> {
  const { watchlistId } = await params;
  const url = new URL(request.url);
  const searchParams = new URLSearchParams();
  for (const key of ['limit', 'offset']) {
    const value = url.searchParams.get(key);
    if (value) {
      searchParams.set(key, value);
    }
  }
  return proxyJsonToBackend(request, {
    backendPath: `/admin/external-watchlists/${encodeURIComponent(watchlistId)}/evidence`,
    method: 'GET',
    searchParams,
  });
}
