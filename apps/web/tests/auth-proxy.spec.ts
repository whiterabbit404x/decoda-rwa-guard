import { readFileSync } from 'node:fs';
import path from 'node:path';

import { expect, test } from '@playwright/test';

import { GET as getBackendHealthRoute } from '../app/api/auth/backend-health/route';
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
            cookie: 'decoda_session=cookie-token; theme=dark',
          },
        }));

        expect(response.status).toBe(200);
        await expect(response.json()).resolves.toEqual({ user: { id: 'user-1' } });
      });
    });
  });

  test('signin proxy can establish session cookie from backend Set-Cookie when access_token is absent', async () => {
    await withEnv({ NODE_ENV: 'production', API_URL: 'https://railway.decoda.example' }, async () => {
      await withMockFetch(async () => new Response(JSON.stringify({ user: { id: 'user-1' } }), {
        status: 200,
        headers: {
          'Content-Type': 'application/json',
          'Set-Cookie': 'decoda_session=backend-cookie-token; Path=/; HttpOnly; Secure; SameSite=Lax',
        },
      }), async () => {
        const response = await postAuthSigninRoute(new Request('http://localhost/api/auth/signin', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ email: 'pilot@example.com', password: 'hunter2-hunter2' }),
        }));

        expect(response.status).toBe(200);
        expect(response.headers.get('Set-Cookie')).toContain('decoda_session=backend-cookie-token');
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
          detail: 'API URL source: missing. API_URL or NEXT_PUBLIC_API_URL is required. Local fallback is disabled unless ALLOW_LOCAL_API_FALLBACK=true.',
          code: 'invalid_runtime_config',
          authTransport: 'same-origin proxy',
          backendApiUrl: null,
          configured: false,
        });
      });
    });
  });
});

test.describe('backend-health route', () => {
  test('returns not_configured when API_URL is absent', async () => {
    await withEnv({ NODE_ENV: 'production', API_URL: undefined, NEXT_PUBLIC_API_URL: undefined }, async () => {
      await withMockFetch(async () => {
        throw new Error('fetch should not be called when API_URL is not configured');
      }, async () => {
        const response = await getBackendHealthRoute();
        expect(response.status).toBe(200);
        const payload = await response.json();
        expect(payload.reachable).toBe(false);
        expect(payload.configured).toBe(false);
        expect(payload.reason).toBe('api_url_not_configured');
      });
    });
  });

  test('returns reachable=true when backend /health responds 200', async () => {
    await withEnv({ NODE_ENV: 'production', API_URL: 'https://api.decoda.example' }, async () => {
      await withMockFetch(async (input) => {
        expect(input).toBe('https://api.decoda.example/health');
        return new Response(JSON.stringify({ service: 'decoda-rwa-guard-api', backend_git_commit: 'abc1234' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }, async () => {
        const response = await getBackendHealthRoute();
        expect(response.status).toBe(200);
        const payload = await response.json();
        expect(payload.reachable).toBe(true);
        expect(payload.configured).toBe(true);
        expect(payload.status).toBe(200);
        expect(payload.backend_service).toBe('decoda-rwa-guard-api');
        expect(payload.backend_git_commit).toBe('abc1234');
        expect(payload.api_url).toBe('https://api.decoda.example/[...]');
      });
    });
  });

  test('returns reachable=false when backend /health is unreachable', async () => {
    await withEnv({ NODE_ENV: 'production', API_URL: 'https://api.decoda.example' }, async () => {
      await withMockFetch(async () => {
        throw new TypeError('fetch failed');
      }, async () => {
        const response = await getBackendHealthRoute();
        expect(response.status).toBe(200);
        const payload = await response.json();
        expect(payload.reachable).toBe(false);
        expect(payload.configured).toBe(true);
        expect(payload.reason).toBe('network_error');
        expect(payload.error_type).toBe('TypeError');
        expect(payload.api_url).toBe('https://api.decoda.example/[...]');
      });
    });
  });

  test('proxy backend error log includes request_id and masked auth_request_url', async () => {
    await withEnv({ NODE_ENV: 'production', API_URL: 'https://railway.decoda.example' }, async () => {
      const logged: string[] = [];
      const originalError = console.error;
      console.error = (...args: unknown[]) => { logged.push(String(args[0])); };

      try {
        await withMockFetch(async () => new Response(JSON.stringify({ detail: 'Invalid email or password.' }), {
          status: 401,
          headers: { 'Content-Type': 'application/json' },
        }), async () => {
          await postAuthSigninRoute(new Request('http://localhost/api/auth/signin', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ email: 'pilot@example.com', password: 'hunter2' }),
          }));
        });
      } finally {
        console.error = originalError;
      }

      expect(logged.length).toBeGreaterThan(0);
      const parsed = JSON.parse(logged[0]);
      expect(parsed.event).toBe('auth_proxy_backend_error');
      expect(parsed.status).toBe(401);
      expect(typeof parsed.request_id).toBe('string');
      expect(parsed.auth_request_url).toBe('https://railway.decoda.example/[masked]');
    });
  });
});
