import { proxyAuthRequest } from 'app/api/auth/_shared/proxy';
export const dynamic = 'force-dynamic';
// Screen 8 response-action approval step-up. requireAuth forwards the session token
// AND enforces/relays the anti-CSRF token, so the backend re-validates CSRF on this
// dedicated session verification endpoint like every other authenticated mutation.
export async function POST(request: Request) { return proxyAuthRequest(request, '/auth/session/step-up', 'POST', { requireAuth: true }); }
