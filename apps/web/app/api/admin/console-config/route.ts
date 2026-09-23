import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Founder console configuration (feature flags). Internal admin only: the backend
// answers a customer with 403. This proxy performs NO authorization of its own.

export async function GET(request: Request): Promise<Response> {
  return proxyJsonToBackend(request, {
    backendPath: `/admin/console-config`,
    method: 'GET',
  });
}
