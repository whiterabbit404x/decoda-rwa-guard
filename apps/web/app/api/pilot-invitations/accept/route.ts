import { proxyJsonToBackend } from 'app/api/_shared/backend-proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for POST /pilot-invitations/accept. This one DOES require a
// session: activation binds the approved invitation to an authenticated account,
// and the backend compares that account's address against the approved one. The
// proxy forwards the body verbatim — the only field the backend reads from it is
// the token; plan, organization, and role are server-decided.
export async function POST(request: Request): Promise<Response> {
  return proxyJsonToBackend(request, {
    backendPath: '/pilot-invitations/accept',
    method: 'POST',
    forwardBody: true,
  });
}
