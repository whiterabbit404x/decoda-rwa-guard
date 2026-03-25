import { dynamic, proxyAuthRequest, revalidate } from '../../auth/_shared/proxy';
export { dynamic, revalidate };
export async function POST(request: Request) { return proxyAuthRequest(request, '/billing/portal', 'POST', { requireAuth: true }); }
