import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin GET-only read model for Screen 10 (spec §24). A browser refresh reads
// canonical provider health + persisted recommendations; it never triggers a scan.
export async function GET(request: Request): Promise<Response> {
  return proxyJsonToBackend(request, {
    backendPath: '/integrations/control-plane',
    method: 'GET',
  });
}
