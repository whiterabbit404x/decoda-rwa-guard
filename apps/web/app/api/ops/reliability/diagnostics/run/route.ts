import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for POST /ops/reliability/diagnostics/run (Screen 12 Run
// Diagnostic). Forwards Authorization + X-Workspace-Id + X-CSRF-Token so the
// backend enforces CSRF + RBAC (monitoring.configure). No body required.
export async function POST(request: Request): Promise<Response> {
  return proxyJsonToBackend(request, { backendPath: '/ops/reliability/diagnostics/run', method: 'POST' });
}
