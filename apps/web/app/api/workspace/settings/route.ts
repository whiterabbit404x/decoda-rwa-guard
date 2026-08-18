import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for the Screen 11 Workspace Settings card.
// GET is read-only (any workspace member); PATCH is the version-guarded save and
// forwards the X-CSRF-Token header so the backend enforces CSRF + the write permission.
export async function GET(request: Request): Promise<Response> {
  return proxyJsonToBackend(request, { backendPath: '/workspace/settings', method: 'GET' });
}

export async function PATCH(request: Request): Promise<Response> {
  return proxyJsonToBackend(request, { backendPath: '/workspace/settings', method: 'PATCH', forwardBody: true });
}
