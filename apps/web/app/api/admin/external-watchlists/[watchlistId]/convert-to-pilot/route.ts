import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Explicit founder action. The backend refuses it without `confirm: true`, creates
// one Pilot workspace, copies targets as inactive drafts, and sends no invitation.

type Params = { params: Promise<{ watchlistId: string }> };

export async function POST(request: Request, { params }: Params): Promise<Response> {
  const { watchlistId } = await params;
  return proxyJsonToBackend(request, {
    backendPath: `/admin/external-watchlists/${encodeURIComponent(watchlistId)}/convert-to-pilot`,
    method: 'POST',
    forwardBody: true,
  });
}
