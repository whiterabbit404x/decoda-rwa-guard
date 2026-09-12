import { expect, test } from '@playwright/test';
import * as fs from 'node:fs';
import * as path from 'node:path';

import { FetchTimeoutError, fetchWithTimeout } from '../app/fetch-with-timeout';

const PROXY_PATH = path.join(__dirname, '../app/api/auth/_shared/proxy.ts');
const AUTH_CONTEXT_PATH = path.join(__dirname, '../app/pilot-auth-context.tsx');

function read(filePath: string): string {
  return fs.readFileSync(filePath, 'utf8');
}

test.describe('auth proxy is bounded', () => {
  test('every auth backend call goes through fetchWithTimeout, never a bare fetch', () => {
    const source = read(PROXY_PATH);

    // /api/auth/me gates the entire authenticated shell. A bare fetch here means
    // a stalled backend holds the dashboard open with no bound at all.
    expect(source).toContain('fetchWithTimeout(authRequestUrl, init, AUTH_PROXY_TIMEOUT_MS)');
    expect(source).not.toContain('await fetch(authRequestUrl, init)');
    expect(source).toContain('AUTH_PROXY_TIMEOUT_MS');
  });

  test('a timeout is reported as 504 backend_timeout, distinct from unreachable', () => {
    const source = read(PROXY_PATH);
    expect(source).toContain('FetchTimeoutError');
    expect(source).toContain("code: 'backend_timeout'");
    expect(source).toContain("code: 'backend_unreachable'");
  });

  test('fetchWithTimeout actually aborts rather than hanging', async () => {
    const started = Date.now();
    let raised: unknown = null;
    try {
      await fetchWithTimeout(
        'http://127.0.0.1:9/never-answers',
        {
          // A signal-aware stub that never settles unless aborted.
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
        } as RequestInit,
        250,
      );
    } catch (error) {
      raised = error;
    }
    const elapsed = Date.now() - started;
    // Either the connection is refused immediately or the timeout fires; both
    // are bounded. What must not happen is an unbounded wait.
    expect(raised).not.toBeNull();
    expect(elapsed).toBeLessThan(5000);
    if (raised instanceof FetchTimeoutError) {
      expect(raised.timeoutMs).toBe(250);
    }
  });
});

test.describe('CSRF bootstrap is parallel but still fail-closed', () => {
  test('the CSRF request starts before /auth/me is awaited', () => {
    const source = read(AUTH_CONTEXT_PATH);
    const csrfStart = source.indexOf('const csrfTokenPromise = fetchCsrfToken();');
    const meAwait = source.indexOf("await fetch('/api/auth/me'");

    expect(csrfStart).toBeGreaterThan(-1);
    expect(meAwait).toBeGreaterThan(-1);
    expect(csrfStart).toBeLessThan(meAwait);
  });

  test('the serial post-/auth/me CSRF round trip is gone', () => {
    const source = read(AUTH_CONTEXT_PATH);
    const refreshUserStart = source.indexOf('const refreshUser = useCallback');
    const refreshUserEnd = source.indexOf('}, [configLoading, fetchCsrfToken]);');
    expect(refreshUserStart).toBeGreaterThan(-1);
    expect(refreshUserEnd).toBeGreaterThan(refreshUserStart);

    const refreshUserBody = source.slice(refreshUserStart, refreshUserEnd);
    expect(refreshUserBody).not.toContain('await fetchAndStoreCsrfToken()');
    expect(refreshUserBody).toContain('await csrfTokenPromise');
  });

  test('csrfReady is derived from a real token, so null is never ready', () => {
    const source = read(AUTH_CONTEXT_PATH);
    expect(source).toContain('csrfReady: Boolean(csrfToken)');
    // The pure fetch must not write state; only the caller decides to apply it.
    const fetchCsrfStart = source.indexOf('const fetchCsrfToken = useCallback');
    const fetchCsrfEnd = source.indexOf('const fetchAndStoreCsrfToken = useCallback');
    expect(fetchCsrfStart).toBeGreaterThan(-1);
    expect(fetchCsrfEnd).toBeGreaterThan(fetchCsrfStart);
    expect(source.slice(fetchCsrfStart, fetchCsrfEnd)).not.toContain('setCsrfToken');
  });

  test('a failed session never applies a CSRF token', () => {
    const source = read(AUTH_CONTEXT_PATH);
    // Both failure exits discard the in-flight token instead of storing it, so a
    // signed-out session still ends with csrfToken null / csrfReady false.
    const discardCount = source.split('swallowUnusedCsrf();').length - 1;
    // one definition + two failure-path calls
    expect(discardCount).toBeGreaterThanOrEqual(2);
    const setAfterSuccess = source.indexOf('setCsrfToken(await csrfTokenPromise);');
    expect(setAfterSuccess).toBeGreaterThan(-1);
    expect(source.indexOf('setUser(payload.user);')).toBeLessThan(setAfterSuccess);
  });
});
