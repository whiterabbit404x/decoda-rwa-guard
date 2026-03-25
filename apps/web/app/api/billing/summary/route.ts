import { dynamic, proxyAuthRequest, revalidate } from '../../auth/_shared/proxy';

export { dynamic, revalidate };

export async function GET(request: Request) {
  return proxyAuthRequest(request, '/billing/summary', 'GET', { requireAuth: true });
}
