import { proxyAuthRequest } from 'app/api/auth/_shared/proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

export async function GET(request: Request) {
  return proxyAuthRequest(request, '/auth/me', 'GET', { requireAuth: true });
}
