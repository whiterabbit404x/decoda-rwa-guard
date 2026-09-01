import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for GET /workspace/governance/policies (Screen 11 Policies tab).
// Read-only: forwards Authorization + X-Workspace-Id to the backend and preserves its
// status. No CSRF required for GET.
export async function GET(request: Request): Promise<Response> {
  return proxyJsonToBackend(request, { backendPath: '/workspace/governance/policies', method: 'GET' });
}

// Create a governance policy. The BACKEND gates this on security.manage and binds
// the row to the workspace the session resolves to — this proxy only relays the
// request (with Authorization, X-Workspace-Id and X-CSRF-Token) and preserves the
// backend's status, so the UI can tell 201 (created) from 409 (already exists)
// and 403 (not permitted). It authorizes nothing of its own.
export async function POST(request: Request): Promise<Response> {
  return proxyJsonToBackend(request, { backendPath: '/workspace/governance/policies', method: 'POST', forwardBody: true });
}
