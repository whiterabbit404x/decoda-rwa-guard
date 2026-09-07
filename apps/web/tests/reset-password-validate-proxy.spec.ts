/**
 * The same-origin proxy for the reset-link check.
 *
 * The browser never talks to the API directly, so this route is the one place the
 * token leaves the page. It must reach the backend unchanged, relay the answer
 * verbatim, and add nothing of its own — a proxy that invented a "valid" would open
 * the password form for a link the backend rejected.
 */
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { expect, test } from '@playwright/test';

import { POST as postValidateRoute } from '../app/api/auth/reset-password/validate/route';

type FetchMock = typeof fetch;

function withEnv(overrides: Record<string, string | undefined>, run: () => Promise<void>) {
  const original = new Map<string, string | undefined>();
  for (const [key, value] of Object.entries(overrides)) {
    original.set(key, process.env[key]);
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }
  return run().finally(() => {
    for (const [key, value] of original.entries()) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  });
}

async function withMockFetch(implementation: FetchMock, run: () => Promise<void>) {
  const originalFetch = global.fetch;
  global.fetch = implementation;
  try {
    await run();
  } finally {
    global.fetch = originalFetch;
  }
}

function validateRequest(body: unknown) {
  return new Request('http://localhost/api/auth/reset-password/validate', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

test.describe('reset-password validate proxy', () => {
  test('forwards the token to the backend validate endpoint', async () => {
    await withEnv({ NODE_ENV: 'production', API_URL: 'https://railway.decoda.example' }, async () => {
      await withMockFetch(async (input, init) => {
        expect(input).toBe('https://railway.decoda.example/auth/reset-password/validate');
        expect(init?.method).toBe('POST');
        expect(init?.body).toBe(JSON.stringify({ token: 'reset-token-123' }));

        return new Response(JSON.stringify({ status: 'valid', email: 'thanhdat852@gmail.com' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }, async () => {
        const response = await postValidateRoute(validateRequest({ token: 'reset-token-123' }));

        expect(response.status).toBe(200);
        expect(response.headers.get('Cache-Control')).toBe('no-store');
        await expect(response.json()).resolves.toEqual({
          status: 'valid',
          email: 'thanhdat852@gmail.com',
        });
      });
    });
  });

  test('relays a rejected link verbatim instead of softening it', async () => {
    for (const reported of ['expired', 'used', 'invalid'] as const) {
      await withEnv({ NODE_ENV: 'production', API_URL: 'https://railway.decoda.example' }, async () => {
        await withMockFetch(async () => new Response(JSON.stringify({ status: reported, email: null }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        }), async () => {
          const response = await postValidateRoute(validateRequest({ token: 'spent-token' }));

          await expect(response.json()).resolves.toEqual({ status: reported, email: null });
        });
      });
    }
  });

  test('a backend failure is surfaced as a failure, not as a usable link', async () => {
    await withEnv({ NODE_ENV: 'production', API_URL: 'https://railway.decoda.example' }, async () => {
      await withMockFetch(async () => new Response(JSON.stringify({ detail: 'Service unavailable.' }), {
        status: 503,
        headers: { 'Content-Type': 'application/json' },
      }), async () => {
        const response = await postValidateRoute(validateRequest({ token: 'reset-token-123' }));

        expect(response.status).toBe(503);
        const body = await response.json();
        expect(body.status).toBeUndefined();
      });
    });
  });

  test('an unconfigured deployment refuses rather than guessing a backend', async () => {
    await withEnv({ NODE_ENV: 'production', API_URL: undefined, NEXT_PUBLIC_API_URL: undefined }, async () => {
      const response = await postValidateRoute(validateRequest({ token: 'reset-token-123' }));

      expect(response.status).toBe(500);
      const body = await response.json();
      expect(body.configured).toBe(false);
    });
  });

  test('the route is a thin proxy with no logic of its own', () => {
    const source = readFileSync(
      path.resolve(__dirname, '../app/api/auth/reset-password/validate/route.ts'),
      'utf8',
    );

    expect(source).toContain("proxyAuthRequest(request, '/auth/reset-password/validate', 'POST')");
    // No caching: a link's state changes the moment it is used.
    expect(source).toContain("export const dynamic = 'force-dynamic'");
    expect(source).toContain('export const revalidate = 0');
    // The proxy must not consume, rewrite, or persist the token.
    expect(source).not.toMatch(/localStorage|cookies\(\)|console\.log/);
  });
});
