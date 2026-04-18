import { expect, test } from '@playwright/test';

import { GET as getMonitoringSystemsRoute } from '../app/api/monitoring/systems/route';

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

test.describe('same-origin monitoring systems proxy', () => {
  test('retries once on timeout and succeeds on second attempt', async () => {
    let callCount = 0;

    await withEnv({ NODE_ENV: 'production', API_URL: 'https://railway.decoda.example' }, async () => {
      await withMockFetch(async () => {
        callCount += 1;
        if (callCount === 1) {
          throw new DOMException('Aborted', 'AbortError');
        }

        return new Response(JSON.stringify({ systems: [{ id: 'sys-1' }] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }, async () => {
        const response = await getMonitoringSystemsRoute(new Request('http://localhost/api/monitoring/systems', {
          method: 'GET',
        }));

        expect(response.status).toBe(200);
        expect(callCount).toBe(2);
        expect(response.headers.get('X-Proxy-Attempts')).toBe('2');
        expect(Number(response.headers.get('X-Proxy-Duration-Ms'))).toBeGreaterThanOrEqual(0);
        await expect(response.json()).resolves.toEqual({ systems: [{ id: 'sys-1' }] });
      });
    });
  });

  test('returns 504 after timeout retry is exhausted', async () => {
    await withEnv({ NODE_ENV: 'production', API_URL: 'https://railway.decoda.example' }, async () => {
      await withMockFetch(async () => {
        throw new DOMException('Aborted', 'AbortError');
      }, async () => {
        const response = await getMonitoringSystemsRoute(new Request('http://localhost/api/monitoring/systems', {
          method: 'GET',
        }));

        expect(response.status).toBe(504);
        await expect(response.json()).resolves.toEqual({
          detail: 'Timed out waiting for backend monitored systems list.',
          code: 'backend_timeout',
          transport: 'same-origin proxy',
        });
      });
    });
  });
});
