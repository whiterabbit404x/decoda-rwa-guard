import { dynamic, proxyAuthRequest, revalidate } from 'app/api/auth/_shared/proxy';

export { dynamic, revalidate };

export async function GET(request: Request) {
  return proxyAuthRequest(request, '/workspace/members', 'GET', { requireAuth: true });
}
