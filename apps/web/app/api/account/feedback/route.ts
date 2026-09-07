import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for POST /account/feedback. Forwards the CSRF token; the
// organization, workspace, and user stamped on the row are resolved server-side
// from the session, not from this body.
export async function POST(request: Request): Promise<Response> {
  return proxyJsonToBackend(request, { backendPath: '/account/feedback', method: 'POST', forwardBody: true });
}
