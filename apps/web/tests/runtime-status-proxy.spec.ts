import { expect, test } from '@playwright/test';

import { GET as getRuntimeStatusRoute } from '../app/api/ops/monitoring/runtime-status/route';

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

test.describe('same-origin runtime-status proxy route', () => {
  test('forwards auth, workspace, csrf, and cookie headers with no-store fetch settings', async () => {
    await withEnv({ NODE_ENV: 'production', API_URL: 'https://railway.decoda.example/' }, async () => {
      await withMockFetch(async (input, init) => {
        expect(input).toBe('https://railway.decoda.example/ops/monitoring/runtime-status');
        expect(init?.method).toBe('GET');
        expect(init?.cache).toBe('no-store');
        expect((init as RequestInit & { next?: { revalidate?: number } })?.next?.revalidate).toBe(0);

        const headers = new Headers(init?.headers);
        expect(headers.get('Accept')).toBe('application/json');
        expect(headers.get('Authorization')).toBe('Bearer web-token');
        expect(headers.get('X-Workspace-Id')).toBe('workspace-123');
        expect(headers.get('X-CSRF-Token')).toBe('csrf-123');
        expect(headers.get('Cookie')).toContain('decoda_session=abc123');

        return new Response(JSON.stringify({ workspace_id: 'workspace-123', mode: 'live' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }, async () => {
        const response = await getRuntimeStatusRoute(new Request('http://localhost/api/ops/monitoring/runtime-status', {
          method: 'GET',
          headers: {
            Authorization: 'Bearer web-token',
            'X-Workspace-Id': 'workspace-123',
            'X-CSRF-Token': 'csrf-123',
            Cookie: 'decoda_session=abc123; theme=dark',
          },
        }));

        expect(response.status).toBe(200);
        expect(response.headers.get('Cache-Control')).toBe('no-store');
        expect(response.headers.get('Vary')).toBe('Authorization, X-Workspace-Id, X-CSRF-Token, Cookie');
        await expect(response.json()).resolves.toEqual({ workspace_id: 'workspace-123', mode: 'live' });
      });
    });
  });

  test('returns invalid_runtime_config when API URL is unavailable', async () => {
    await withEnv({ NODE_ENV: 'production', API_URL: undefined, NEXT_PUBLIC_API_URL: undefined }, async () => {
      const response = await getRuntimeStatusRoute(new Request('http://localhost/api/ops/monitoring/runtime-status'));

      expect(response.status).toBe(500);
      await expect(response.json()).resolves.toEqual({
        detail: 'API URL source: missing. API_URL or NEXT_PUBLIC_API_URL is required. Local fallback is disabled unless ALLOW_LOCAL_API_FALLBACK=true.',
        code: 'invalid_runtime_config',
        transport: 'same-origin proxy',
        configured: false,
      });
    });
  });
});
