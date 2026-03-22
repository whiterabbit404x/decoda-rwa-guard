import { readFileSync } from 'node:fs';
import path from 'node:path';

import { expect, test } from '@playwright/test';

import { GET as getAuthMeRoute } from '../app/api/auth/me/route';
import { POST as postAuthSigninRoute } from '../app/api/auth/signin/route';
import { POST as postAuthSignupRoute } from '../app/api/auth/signup/route';

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

test.describe('same-origin auth proxy routes', () => {
  test('sign-in and sign-up client code use same-origin auth proxy routes', async () => {
    const source = readFileSync(path.join(process.cwd(), 'apps/web/app/pilot-auth-context.tsx'), 'utf8');

    expect(source).toContain("const proxyUrl = '/api/auth/signin';");
    expect(source).toContain("const proxyUrl = '/api/auth/signup';");
    expect(source).toContain("fetch('/api/auth/me'");
    expect(source).toContain("fetch('/api/auth/signout'");
    expect(source).toContain("fetch('/api/auth/select-workspace'");
    expect(source).not.toContain('fetch(`${apiUrl}/auth/signin`');
    expect(source).not.toContain('fetch(`${apiUrl}/auth/signup`');
    expect(source).not.toContain('fetch(`${runtimeConfig.apiUrl}/auth/me`');
  });

  test('signin proxy forwards backend JSON and status transparently', async () => {
    await withEnv({ NODE_ENV: 'production', API_URL: 'https://railway.decoda.example' }, async () => {
      await withMockFetch(async (input, init) => {
        expect(input).toBe('https://railway.decoda.example/auth/signin');
        expect(init?.method).toBe('POST');
        expect(init?.headers).toBeTruthy();
        const headers = new Headers(init?.headers);
        expect(headers.get('Content-Type')).toBe('application/json');
        expect(init?.body).toBe(JSON.stringify({ email: 'pilot@example.com', password: 'hunter2-hunter2' }));

        return new Response(JSON.stringify({ detail: 'Invalid email or password.' }), {
          status: 401,
          headers: { 'Content-Type': 'application/json' },
        });
      }, async () => {
        const response = await postAuthSigninRoute(new Request('http://localhost/api/auth/signin', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email: 'pilot@example.com', password: 'hunter2-hunter2' }),
        }));

        expect(response.status).toBe(401);
        expect(response.headers.get('Cache-Control')).toBe('no-store');
        await expect(response.json()).resolves.toEqual({ detail: 'Invalid email or password.' });
      });
    });
  });

  test('protected proxy routes fall back to the token cookie when Authorization is absent', async () => {
    await withEnv({ NODE_ENV: 'production', API_URL: 'https://railway.decoda.example/' }, async () => {
      await withMockFetch(async (input, init) => {
        expect(input).toBe('https://railway.decoda.example/auth/me');
        const headers = new Headers(init?.headers);
        expect(headers.get('Authorization')).toBe('Bearer cookie-token');
        return new Response(JSON.stringify({ user: { id: 'user-1' } }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }, async () => {
        const response = await getAuthMeRoute(new Request('http://localhost/api/auth/me', {
          headers: {
            cookie: 'decoda-pilot-access-token=cookie-token; theme=dark',
          },
        }));

        expect(response.status).toBe(200);
        await expect(response.json()).resolves.toEqual({ user: { id: 'user-1' } });
      });
    });
  });

  test('proxy routes return a clear JSON error when runtime config is invalid', async () => {
    await withEnv({ NODE_ENV: 'production', API_URL: undefined, NEXT_PUBLIC_API_URL: undefined }, async () => {
      await withMockFetch(async () => {
        throw new Error('fetch should not be called when runtime config is invalid');
      }, async () => {
        const response = await postAuthSignupRoute(new Request('http://localhost/api/auth/signup', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email: 'pilot@example.com' }),
        }));
        const payload = await response.json();

        expect(response.status).toBe(500);
        expect(payload).toEqual({
          detail: 'API_URL or NEXT_PUBLIC_API_URL is required in production.',
          code: 'invalid_runtime_config',
          authTransport: 'same-origin proxy',
          backendApiUrl: null,
          configured: false,
        });
      });
    });
  });
});
