import { dynamic, proxyAuthRequest, revalidate } from '../../../_shared/proxy';

export { dynamic, revalidate };

export async function GET(request: Request) {
  return proxyAuthRequest(request, '/billing/status', 'GET', { requireAuth: true });
}
