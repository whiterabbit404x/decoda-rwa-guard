import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for POST /workspace/security-settings/change (Screen 11 security-policy change). Forwards the JSON body, Authorization,
// X-Workspace-Id and the X-CSRF-Token header so the backend enforces CSRF + RBAC.
export async function POST(request: Request): Promise<Response> {
  return proxyJsonToBackend(request, { backendPath: '/workspace/security-settings/change', method: 'POST', forwardBody: true });
}
