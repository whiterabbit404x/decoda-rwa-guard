import { dynamic, proxyAuthRequest, revalidate } from 'app/api/auth/_shared/proxy';

export { dynamic, revalidate };

export async function POST(request: Request) {
  return proxyAuthRequest(request, '/auth/mfa/confirm', 'POST', { requireAuth: true });
}
