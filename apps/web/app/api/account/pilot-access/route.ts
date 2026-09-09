import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for GET /account/pilot-access. Reports whether the CALLER's
// own account has Pilot access; it takes no user or email parameter, so it
// cannot be used to probe whether someone else applied.
export async function GET(request: Request): Promise<Response> {
  return proxyJsonToBackend(request, { backendPath: '/account/pilot-access', method: 'GET' });
}
