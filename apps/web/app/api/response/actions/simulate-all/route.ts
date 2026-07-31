import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for the Playbook Execution Agent "Simulate All" command. The
// backend selects and dry-run simulates only ELIGIBLE actions (already-simulated,
// executing, executed, failed, cancelled, and unsupported actions are skipped).
// Optional ?incident_id= scopes it to a single incident.
export async function POST(request: Request): Promise<Response> {
  const url = new URL(request.url);
  return proxyJsonToBackend(request, {
    backendPath: '/response/actions/simulate-all',
    method: 'POST',
    searchParams: url.searchParams,
  });
}
