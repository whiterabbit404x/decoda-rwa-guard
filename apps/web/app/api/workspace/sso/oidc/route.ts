import { proxyAuthRequest } from 'app/api/auth/_shared/proxy';
export const dynamic = 'force-dynamic';
export async function GET(request: Request) { return proxyAuthRequest(request, '/workspace/sso/oidc', 'GET', { requireAuth: true }); }
export async function PUT(request: Request) { return proxyAuthRequest(request, '/workspace/sso/oidc', 'PUT', { requireAuth: true }); }
export async function DELETE(request: Request) { return proxyAuthRequest(request, '/workspace/sso/oidc', 'DELETE', { requireAuth: true }); }
