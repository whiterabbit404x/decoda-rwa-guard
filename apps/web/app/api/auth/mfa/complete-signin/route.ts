import { dynamic, proxyAuthRequest, revalidate } from 'app/api/auth/_shared/proxy';
import { ensureCsrfCookie, setSessionCookies } from 'app/api/auth/_shared/session';

export { dynamic, revalidate };

export async function POST(request: Request) {
  const response = await proxyAuthRequest(request, '/auth/mfa/complete-signin', 'POST');
  const payload = await response.json();
  if (response.ok && payload?.access_token) {
    await setSessionCookies(String(payload.access_token));
    await ensureCsrfCookie();
    return Response.json({ ...payload, access_token: undefined }, { status: response.status, headers: { 'Cache-Control': 'no-store' } });
  }
  return Response.json(payload, { status: response.status, headers: { 'Cache-Control': 'no-store' } });
}
