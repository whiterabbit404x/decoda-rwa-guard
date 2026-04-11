import { expect, test } from '@playwright/test';

import { POST as postMonitoringSystemsReconcileRoute } from '../app/api/monitoring/systems/reconcile/route';

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

test.describe('same-origin monitoring reconcile proxy', () => {
  test('forwards POST and auth/workspace/csrf headers to backend', async () => {
    await withEnv({ NODE_ENV: 'production', API_URL: 'https://railway.decoda.example/' }, async () => {
      await withMockFetch(async (input, init) => {
        expect(input).toBe('https://railway.decoda.example/monitoring/systems/reconcile');
        expect(init?.method).toBe('POST');

        const headers = new Headers(init?.headers);
        expect(headers.get('Authorization')).toBe('Bearer web-token');
        expect(headers.get('X-Workspace-Id')).toBe('workspace-123');
        expect(headers.get('X-CSRF-Token')).toBe('csrf-123');
        expect(init?.body).toBe(JSON.stringify({ dry_run: true }));

        return new Response(JSON.stringify({ reconcile: { targets_scanned: 1 }, systems: [] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }, async () => {
        const response = await postMonitoringSystemsReconcileRoute(new Request('http://localhost/api/monitoring/systems/reconcile', {
          method: 'POST',
          headers: {
            Authorization: 'Bearer web-token',
            'X-Workspace-Id': 'workspace-123',
            'X-CSRF-Token': 'csrf-123',
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ dry_run: true }),
        }));

        expect(response.status).toBe(200);
        await expect(response.json()).resolves.toEqual({ reconcile: { targets_scanned: 1 }, systems: [] });
      });
    });
  });


  test('returns 401 JSON when Authorization header is missing', async () => {
    await withEnv({ NODE_ENV: 'production', API_URL: 'https://railway.decoda.example' }, async () => {
      await withMockFetch(async () => {
        throw new Error('fetch should not be called without Authorization');
      }, async () => {
        const response = await postMonitoringSystemsReconcileRoute(new Request('http://localhost/api/monitoring/systems/reconcile', {
          method: 'POST',
        }));

        expect(response.status).toBe(401);
        await expect(response.json()).resolves.toEqual({
          detail: 'Authorization is required.',
          code: 'missing_authorization',
          transport: 'same-origin proxy',
          backendApiUrl: 'https://railway.decoda.example',
          configured: true,
        });
      });
    });
  });

  test('returns structured 502 when backend is unreachable', async () => {
    await withEnv({ NODE_ENV: 'production', API_URL: 'https://railway.decoda.example' }, async () => {
      await withMockFetch(async () => {
        throw new Error('connect ECONNREFUSED');
      }, async () => {
        const response = await postMonitoringSystemsReconcileRoute(new Request('http://localhost/api/monitoring/systems/reconcile', {
          method: 'POST',
          headers: {
            Authorization: 'Bearer web-token',
          },
        }));

        expect(response.status).toBe(502);
        await expect(response.json()).resolves.toEqual({
          detail: 'Backend unreachable.',
          code: 'backend_unreachable',
          transport: 'same-origin proxy',
          backendApiUrl: 'https://railway.decoda.example',
          configured: true,
        });
      });
    });
  });

  test('returns structured 504 when backend reconcile times out', async () => {
    await withEnv({ NODE_ENV: 'production', API_URL: 'https://railway.decoda.example' }, async () => {
      await withMockFetch(async () => {
        throw new DOMException('Aborted', 'AbortError');
      }, async () => {
        const response = await postMonitoringSystemsReconcileRoute(new Request('http://localhost/api/monitoring/systems/reconcile', {
          method: 'POST',
          headers: {
            Authorization: 'Bearer web-token',
          },
        }));

        expect(response.status).toBe(504);
        await expect(response.json()).resolves.toEqual({
          detail: 'Timed out waiting for backend reconcile response.',
          code: 'backend_timeout',
          transport: 'same-origin proxy',
          backendApiUrl: 'https://railway.decoda.example',
          configured: true,
        });
      });
    });
  });
  test('flattens nested FastAPI HTTPException detail payloads from backend', async () => {
    await withEnv({ NODE_ENV: 'production', API_URL: 'https://railway.decoda.example' }, async () => {
      await withMockFetch(async () => new Response(JSON.stringify({
        detail: {
          code: 'monitoring_reconcile_failed',
          detail: 'Unexpected backend error during monitored systems reconcile.',
          stage: 'reconcile_targets',
        },
      }), {
        status: 500,
        headers: { 'Content-Type': 'application/json' },
      }), async () => {
        const response = await postMonitoringSystemsReconcileRoute(new Request('http://localhost/api/monitoring/systems/reconcile', {
          method: 'POST',
          headers: {
            Authorization: 'Bearer web-token',
          },
        }));

        expect(response.status).toBe(500);
        await expect(response.json()).resolves.toEqual({
          code: 'monitoring_reconcile_failed',
          detail: 'Unexpected backend error during monitored systems reconcile.',
          stage: 'reconcile_targets',
        });
      });
    });
  });

  test('preserves flat backend error payloads', async () => {
    await withEnv({ NODE_ENV: 'production', API_URL: 'https://railway.decoda.example' }, async () => {
      await withMockFetch(async () => new Response(JSON.stringify({
        detail: 'Authorization is required.',
      }), {
        status: 401,
        headers: { 'Content-Type': 'application/json' },
      }), async () => {
        const response = await postMonitoringSystemsReconcileRoute(new Request('http://localhost/api/monitoring/systems/reconcile', {
          method: 'POST',
          headers: {
            Authorization: 'Bearer web-token',
          },
        }));

        expect(response.status).toBe(401);
        await expect(response.json()).resolves.toEqual({
          detail: 'Authorization is required.',
        });
      });
    });
  });

});
