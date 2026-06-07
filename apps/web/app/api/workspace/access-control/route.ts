import { proxyAuthRequest } from 'app/api/auth/_shared/proxy';
export const dynamic = 'force-dynamic';
export async function GET(request: Request) { return proxyAuthRequest(request, '/workspace/access-control', 'GET', { requireAuth: true }); }
