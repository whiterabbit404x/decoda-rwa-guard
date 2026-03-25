import { dynamic, proxyAuthRequest, revalidate } from '../_shared/proxy';
export { dynamic, revalidate };
export async function POST(request: Request) { return proxyAuthRequest(request, '/auth/verify-email', 'POST'); }
