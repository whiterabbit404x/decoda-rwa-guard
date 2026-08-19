import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for GET /ops/reliability/events (Screen 12 reliability event
// ledger / remediation history). Read-only; forwards Authorization + workspace.
export async function GET(request: Request): Promise<Response> {
  const searchParams = new URL(request.url).searchParams;
  return proxyJsonToBackend(request, { backendPath: '/ops/reliability/events', method: 'GET', searchParams });
}
