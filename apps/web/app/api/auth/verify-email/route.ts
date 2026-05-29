import { proxyAuthRequest } from 'app/api/auth/_shared/proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

export async function POST(request: Request) {
  return proxyAuthRequest(request, '/auth/verify-email', 'POST');
}
