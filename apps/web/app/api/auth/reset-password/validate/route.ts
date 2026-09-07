import { proxyAuthRequest } from 'app/api/auth/_shared/proxy';

export const dynamic = 'force-dynamic';
export const revalidate = 0;

// Read-only check of a reset link: it reports whether the token can still be used
// and, for a usable one, which account it belongs to. It never consumes the token,
// so reloading the reset page is safe.
export async function POST(request: Request) {
  return proxyAuthRequest(request, '/auth/reset-password/validate', 'POST');
}
