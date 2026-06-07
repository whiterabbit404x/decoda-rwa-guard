import { proxyAuthRequest } from 'app/api/auth/_shared/proxy';
export const dynamic = 'force-dynamic';
export async function PUT(request: Request) { return proxyAuthRequest(request, '/workspace/auth-policy', 'PUT', { requireAuth: true }); }
