import { proxyAuthRequest } from 'app/api/auth/_shared/proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Same-origin proxy for POST /pilot-invitations/signup — the account-creation
// half of invitation acceptance.
//
// Unauthenticated, like /api/auth/signup: the approved applicant this route
// exists for has no Decoda account yet. It goes through the auth proxy rather
// than the generic one so the backend's `access_token` becomes the HttpOnly
// session cookie by the SAME code path sign-in uses; there is no second
// cookie-writing convention to keep in step.
//
// The body reaches the backend verbatim, and the backend reads exactly two
// fields from it plus the invitation token. The approved email, the company, the
// plan, and the role are all read from the invitation row the token resolves to,
// so nothing a browser puts in this body can choose them.
export async function POST(request: Request) {
  return proxyAuthRequest(request, '/pilot-invitations/signup', 'POST', { cookieAction: 'set-session' });
}
