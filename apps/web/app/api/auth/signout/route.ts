import { dynamic, proxyAuthRequest, revalidate } from 'app/api/auth/_shared/proxy';
import { clearSessionCookies } from 'app/api/auth/_shared/session';

export { dynamic, revalidate };

export async function POST(request: Request) {
  const response = await proxyAuthRequest(request, '/auth/signout', 'POST', { requireAuth: true, requireCsrf: true });
  await clearSessionCookies();
  const payload = await response.json().catch(() => ({ ok: true }));
  return Response.json(payload, { status: response.status, headers: { 'Cache-Control': 'no-store' } });
}
