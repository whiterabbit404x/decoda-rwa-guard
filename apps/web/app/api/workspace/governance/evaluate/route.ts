import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for POST /workspace/governance/evaluate (Screen 11 run evaluation). Forwards the JSON body, Authorization,
// X-Workspace-Id and the X-CSRF-Token header so the backend enforces CSRF + RBAC.
export async function POST(request: Request): Promise<Response> {
  return proxyJsonToBackend(request, { backendPath: '/workspace/governance/evaluate', method: 'POST', forwardBody: true });
}
