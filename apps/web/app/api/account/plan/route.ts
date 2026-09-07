import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for GET /account/plan. Read-only: forwards Authorization +
// X-Workspace-Id so the backend resolves the organization from the session. The
// browser never names the organization it wants.
export async function GET(request: Request): Promise<Response> {
  return proxyJsonToBackend(request, { backendPath: '/account/plan', method: 'GET' });
}
