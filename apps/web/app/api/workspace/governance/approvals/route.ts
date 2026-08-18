import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for GET /workspace/governance/approvals (Screen 11 approvals dialog). Read-only: forwards Authorization +
// X-Workspace-Id to the backend and preserves its status. No CSRF required for GET.
export async function GET(request: Request): Promise<Response> {
  return proxyJsonToBackend(request, { backendPath: '/workspace/governance/approvals', method: 'GET' });
}
