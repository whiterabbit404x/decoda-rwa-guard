import { expect, test } from '@playwright/test';

import { GET as getCsrfRoute } from '../app/api/auth/csrf/route';

type FetchMock = typeof fetch;

function withEnv(overrides: Record<string, string | undefined>, run: () => Promise<void> | void) {
  const originalValues = new Map<string, string | undefined>();

  for (const [key, value] of Object.entries(overrides)) {
    originalValues.set(key, process.env[key]);
    if (value === undefined) {
      delete process.env[key];
    } else {
      process.env[key] = value;
    }
  }

  const restore = () => {
    for (const [key, value] of originalValues.entries()) {
      if (value === undefined) {
        delete process.env[key];
      } else {
        process.env[key] = value;
      }
    }
  };

  try {
    const result = run();
    if (result instanceof Promise) {
      return result.finally(restore);
    }
    restore();
    return result;
  } catch (error) {
    restore();
    throw error;
  }
}

async function withMockFetch(implementation: FetchMock, run: () => Promise<void> | void) {
  const originalFetch = global.fetch;
  global.fetch = implementation;
  try {
    await run();
  } finally {
    global.fetch = originalFetch;
  }
}

test.describe('/api/auth/csrf proxy route', () => {
  test('proxies backend HMAC csrf_token and returns csrfToken to client', async () => {
    await withEnv({ NODE_ENV: 'production', API_URL: 'https://railway.decoda.example' }, async () => {
      const hmacToken = 'abc123nonce0000000000000000000000.deadbeef0000000000000000000000000000000000000000000000000000000000000000';
      await withMockFetch(async (input) => {
        expect(input).toBe('https://railway.decoda.example/auth/csrf-token');
        return new Response(JSON.stringify({ csrf_token: hmacToken }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }, async () => {
        const response = await getCsrfRoute();
        expect(response.status).toBe(200);
        const body = await response.json();
        expect(body.csrfToken).toBe(hmacToken);
        // Sets decoda_csrf cookie so double-submit pattern works for proxy-layer mutations
        const setCookie = response.headers.get('set-cookie');
        expect(setCookie).toContain('decoda_csrf=');
        expect(setCookie).toContain(hmacToken);
      });
    });
  });

  test('returns csrfToken: null when runtime config is not set', async () => {
    await withEnv({ NODE_ENV: 'production', API_URL: undefined, NEXT_PUBLIC_API_URL: undefined }, async () => {
      const response = await getCsrfRoute();
      expect(response.status).toBe(200);
      const body = await response.json();
      expect(body.csrfToken).toBeNull();
    });
  });

  test('returns csrfToken: null when backend csrf-token endpoint fails', async () => {
    await withEnv({ NODE_ENV: 'production', API_URL: 'https://railway.decoda.example' }, async () => {
      await withMockFetch(async () => {
        return new Response('', { status: 500 });
      }, async () => {
        const response = await getCsrfRoute();
        expect(response.status).toBe(200);
        const body = await response.json();
        expect(body.csrfToken).toBeNull();
      });
    });
  });

  test('returns csrfToken: null when backend is unreachable', async () => {
    await withEnv({ NODE_ENV: 'production', API_URL: 'https://railway.decoda.example' }, async () => {
      await withMockFetch(async () => {
        throw new Error('connect ECONNREFUSED');
      }, async () => {
        const response = await getCsrfRoute();
        expect(response.status).toBe(200);
        const body = await response.json();
        expect(body.csrfToken).toBeNull();
      });
    });
  });
});

test.describe('CSRF token source contract', () => {
  test('/api/auth/csrf route proxies backend csrf-token endpoint (not UUID cookie)', () => {
    const fs = require('node:fs') as typeof import('node:fs');
    const path = require('node:path') as typeof import('node:path');
    const source = fs.readFileSync(path.join(__dirname, '..', 'app', 'api', 'auth', 'csrf', 'route.ts'), 'utf-8');

    expect(source).toContain('/auth/csrf-token');
    expect(source).toContain('csrf_token');
    expect(source).not.toContain('crypto.randomUUID');
    expect(source).not.toContain('getCsrfToken()');
  });

  test('auth context exposes refreshCsrfToken for components to call on token expiry', () => {
    const fs = require('node:fs') as typeof import('node:fs');
    const path = require('node:path') as typeof import('node:path');
    const source = fs.readFileSync(path.join(__dirname, '..', 'app', 'pilot-auth-context.tsx'), 'utf-8');

    expect(source).toContain('refreshCsrfToken: () => Promise<string | null>');
    expect(source).toContain('refreshCsrfToken: fetchAndStoreCsrfToken');
  });
});
