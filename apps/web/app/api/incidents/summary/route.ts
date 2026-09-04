import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for the Screen 7 incident-queue KPI counters.
// GET /incidents/summary — the workspace-wide Open / Critical / In Investigation /
// Awaiting Response counts, computed by the backend over every incident in the
// workspace (never the page or filter the list happens to be showing).
//
// A static segment outranks the sibling [incidentId] route in the App Router, so
// this never resolves as an incident whose id is literally "summary".
export async function GET(request: Request): Promise<Response> {
  return proxyJsonToBackend(request, {
    backendPath: '/incidents/summary',
    method: 'GET',
  });
}
