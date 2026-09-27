import { expect, test } from '@playwright/test';
import fs from 'node:fs';

// The session design: the RWA Guard session lives ONLY in the HttpOnly
// `decoda_session` cookie. No page script can read it — it is never stored in
// localStorage, never mirrored into a readable cookie, never returned in a JSON
// body — and the server (proxy.ts) attaches it to every backend call.

test('auth proxy keeps the session in an HttpOnly cookie and out of responses', async () => {
  const source = fs.readFileSync('apps/web/app/api/auth/_shared/proxy.ts', 'utf8');
  expect(source.includes('httpOnly: true')).toBeTruthy();
  expect(source.includes("sameSite: 'lax'")).toBeTruthy();
  expect(source.includes('secure: isProd')).toBeTruthy();
  // The readable copy of earlier releases is only ever cleared, never set with a token.
  expect(source.includes("const RETIRED_ACCESS_TOKEN_COOKIE_NAME = 'decoda_access_token';")).toBeTruthy();
  expect(/cookies\.set\(RETIRED_ACCESS_TOKEN_COOKIE_NAME, sessionToken/.test(source)).toBeFalsy();
  expect(source.includes('NextResponse.json(withoutSessionToken(responseBody)')).toBeTruthy();
});

test('pilot auth context never stores, reads or sends a bearer token', async () => {
  const source = fs.readFileSync('apps/web/app/pilot-auth-context.tsx', 'utf8');
  expect(source.includes('localStorage')).toBeFalsy();
  expect(source.includes('decoda_access_token')).toBeFalsy();
  expect(source.includes('headers.Authorization')).toBeFalsy();
  expect(source.includes('Bearer ')).toBeFalsy();
  // Earlier copies are removed on load.
  expect(source.includes('clearRetiredBrowserToken();')).toBeTruthy();
});

test('credentials are attached by the server, and browsers cannot supply them', async () => {
  const proxy = fs.readFileSync('apps/web/proxy.ts', 'utf8');
  expect(proxy.includes('stripBrowserCredentials(requestHeaders);')).toBeTruthy();
  expect(proxy.includes('sessionToken: request.cookies.get(SESSION_COOKIE)?.value')).toBeTruthy();
  const credentials = fs.readFileSync('apps/web/app/server-credentials.ts', 'utf8');
  expect(credentials.includes("headers.set('authorization', `Bearer ${credentials.sessionToken}`);")).toBeTruthy();
  const runtimeRoute = fs.readFileSync('apps/web/app/api/runtime-config/route.ts', 'utf8');
  expect(runtimeRoute.includes('browserApiUrl(serverConfig.apiUrl)')).toBeTruthy();
});
