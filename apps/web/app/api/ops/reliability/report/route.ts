import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for GET /ops/reliability/report (Screen 12 Full Health
// Report). Read-only; forwards Authorization + X-Workspace-Id. No CSRF for GET.
export async function GET(request: Request): Promise<Response> {
  return proxyJsonToBackend(request, { backendPath: '/ops/reliability/report', method: 'GET' });
}
