import { expect, test } from '@playwright/test';
import fs from 'node:fs';

test('auth proxy sets secure httponly session cookie options', async () => {
  const source = fs.readFileSync('apps/web/app/api/auth/_shared/proxy.ts', 'utf8');
  expect(source.includes('httpOnly: true')).toBeTruthy();
  expect(source.includes("sameSite: 'lax'")).toBeTruthy();
  expect(source.includes('secure: isProd')).toBeTruthy();
  expect(source.includes("const ACCESS_TOKEN_COOKIE_NAME = 'decoda_access_token';")).toBeTruthy();
  expect(source.includes('proxyResponse.cookies.set(ACCESS_TOKEN_COOKIE_NAME, sessionToken, accessTokenCookieOptions());')).toBeTruthy();
});

test('pilot auth context persists bearer token and includes it in auth headers', async () => {
  const source = fs.readFileSync('apps/web/app/pilot-auth-context.tsx', 'utf8');
  expect(source.includes("const ACCESS_TOKEN_STORAGE_KEY = 'decoda.accessToken';")).toBeTruthy();
  expect(source.includes('window.localStorage.setItem(ACCESS_TOKEN_STORAGE_KEY, token);')).toBeTruthy();
  expect(source.includes('headers.Authorization = `Bearer ${token}`;')).toBeTruthy();
  expect(source.includes('window.localStorage.removeItem(ACCESS_TOKEN_STORAGE_KEY);')).toBeTruthy();
});
