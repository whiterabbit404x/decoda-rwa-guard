import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// POST-only (spec §24): Run Health Check is an explicit operator mutation that runs a
// real backend workflow (bounded provider probe + recommendation refresh + scan record).
// The backend re-checks the monitoring.configure permission — button state is never authz.
export async function POST(request: Request): Promise<Response> {
  return proxyJsonToBackend(request, {
    backendPath: '/integrations/health-check',
    method: 'POST',
  });
}
