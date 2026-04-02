import { dynamic, proxyAuthRequest, revalidate } from 'app/api/auth/_shared/proxy';
import { clearSessionCookies } from 'app/api/auth/_shared/session';

export { dynamic, revalidate };

export async function POST(request: Request) {
  const response = await proxyAuthRequest(request, '/auth/signout-all', 'POST', { requireAuth: true, requireCsrf: true });
  if (response.ok) {
    await clearSessionCookies();
  }
  const payload = await response.json().catch(() => ({ ok: response.ok }));
  return Response.json(payload, { status: response.status, headers: { 'Cache-Control': 'no-store' } });
}
