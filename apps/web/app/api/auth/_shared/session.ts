import { cookies } from 'next/headers';

export const AUTH_COOKIE_NAME = 'decoda-pilot-access-token';
export const CSRF_COOKIE_NAME = 'decoda-csrf-token';
const COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24;

function secureCookie() {
  return process.env.NODE_ENV === 'production';
}

export async function setSessionCookies(token: string) {
  const cookieStore = await cookies();
  cookieStore.set(AUTH_COOKIE_NAME, token, {
    httpOnly: true,
    sameSite: 'lax',
    secure: secureCookie(),
    path: '/',
    maxAge: COOKIE_MAX_AGE_SECONDS,
  });
}

export async function clearSessionCookies() {
  const cookieStore = await cookies();
  cookieStore.set(AUTH_COOKIE_NAME, '', {
    httpOnly: true,
    sameSite: 'lax',
    secure: secureCookie(),
    path: '/',
    maxAge: 0,
  });
  cookieStore.set(CSRF_COOKIE_NAME, '', {
    httpOnly: false,
    sameSite: 'strict',
    secure: secureCookie(),
    path: '/',
    maxAge: 0,
  });
}

export async function ensureCsrfCookie() {
  const cookieStore = await cookies();
  let csrf = cookieStore.get(CSRF_COOKIE_NAME)?.value;
  if (!csrf) {
    csrf = crypto.randomUUID().replace(/-/g, '');
    cookieStore.set(CSRF_COOKIE_NAME, csrf, {
      httpOnly: false,
      sameSite: 'strict',
      secure: secureCookie(),
      path: '/',
      maxAge: COOKIE_MAX_AGE_SECONDS,
    });
  }
  return csrf;
}
