import { proxyAuthRequest } from 'app/api/auth/_shared/proxy';
export const dynamic = 'force-dynamic';
export async function GET(request: Request) { return proxyAuthRequest(request, '/workspace/scim/tokens', 'GET', { requireAuth: true }); }
export async function POST(request: Request) { return proxyAuthRequest(request, '/workspace/scim/tokens', 'POST', { requireAuth: true }); }
