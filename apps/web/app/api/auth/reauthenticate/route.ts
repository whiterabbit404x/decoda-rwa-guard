import { proxyAuthRequest } from 'app/api/auth/_shared/proxy';
export const dynamic = 'force-dynamic';
export async function POST(request: Request) { return proxyAuthRequest(request, '/auth/reauthenticate', 'POST', { requireAuth: true }); }
