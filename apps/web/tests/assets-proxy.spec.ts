import { expect, test } from '@playwright/test';

import { GET as getAssetsRoute, POST as postAssetsRoute } from '../app/api/assets/route';

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

test.describe('assets GET proxy', () => {
  test('forwards GET request with Authorization and X-Workspace-Id to backend', async () => {
    await withEnv({ NODE_ENV: 'production', API_URL: 'https://railway.decoda.example' }, async () => {
      await withMockFetch(async (input, init) => {
        expect(input).toBe('https://railway.decoda.example/assets');
        expect(init?.method).toBe('GET');
        const headers = new Headers(init?.headers);
        expect(headers.get('Authorization')).toBe('Bearer test-token');
        expect(headers.get('X-Workspace-Id')).toBe('11111111-1111-4111-8111-111111111111');
        return new Response(JSON.stringify({ assets: [{ id: 'asset-1', name: 'Treasury wallet' }] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }, async () => {
        const response = await getAssetsRoute(new Request('http://localhost/api/assets', {
          headers: {
            Authorization: 'Bearer test-token',
            'X-Workspace-Id': '11111111-1111-4111-8111-111111111111',
          },
        }));
        expect(response.status).toBe(200);
        await expect(response.json()).resolves.toEqual({ assets: [{ id: 'asset-1', name: 'Treasury wallet' }] });
      });
    });
  });

  test('returns 401 when Authorization header is missing', async () => {
    await withEnv({ NODE_ENV: 'production', API_URL: 'https://railway.decoda.example' }, async () => {
      await withMockFetch(async () => {
        throw new Error('fetch should not be called without Authorization');
      }, async () => {
        const response = await getAssetsRoute(new Request('http://localhost/api/assets'));
        expect(response.status).toBe(401);
        const body = await response.json();
        expect(body.code).toBe('missing_authorization');
      });
    });
  });

  test('returns 502 when backend is unreachable', async () => {
    await withEnv({ NODE_ENV: 'production', API_URL: 'https://railway.decoda.example' }, async () => {
      await withMockFetch(async () => {
        throw new Error('connect ECONNREFUSED');
      }, async () => {
        const response = await getAssetsRoute(new Request('http://localhost/api/assets', {
          headers: { Authorization: 'Bearer test-token' },
        }));
        expect(response.status).toBe(502);
        const body = await response.json();
        expect(body.code).toBe('backend_unreachable');
        expect(body.transport).toBe('same-origin proxy');
      });
    });
  });
});

test.describe('assets POST proxy (create asset)', () => {
  test('forwards POST with Authorization, X-Workspace-Id, X-CSRF-Token and body to backend', async () => {
    await withEnv({ NODE_ENV: 'production', API_URL: 'https://railway.decoda.example' }, async () => {
      await withMockFetch(async (input, init) => {
        expect(input).toBe('https://railway.decoda.example/assets');
        expect(init?.method).toBe('POST');
        const headers = new Headers(init?.headers);
        expect(headers.get('Authorization')).toBe('Bearer web-token');
        expect(headers.get('X-Workspace-Id')).toBe('11111111-1111-4111-8111-111111111111');
        expect(headers.get('X-CSRF-Token')).toBe('csrf-uuid-token');
        expect(headers.get('Content-Type')).toBe('application/json');
        const body = JSON.parse(init?.body as string);
        expect(body.name).toBe('Treasury wallet');
        expect(body.asset_type).toBe('wallet');
        return new Response(JSON.stringify({ id: 'new-asset', verification_status: 'pending' }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }, async () => {
        const response = await postAssetsRoute(new Request('http://localhost/api/assets', {
          method: 'POST',
          headers: {
            Authorization: 'Bearer web-token',
            'X-Workspace-Id': '11111111-1111-4111-8111-111111111111, 22222222-2222-4222-8222-222222222222',
            'X-CSRF-Token': 'csrf-uuid-token',
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({ name: 'Treasury wallet', asset_type: 'wallet', chain_network: 'ethereum-mainnet', identifier: '0x1234567890123456789012345678901234567890' }),
        }));

        expect(response.status).toBe(200);
        await expect(response.json()).resolves.toEqual({ id: 'new-asset', verification_status: 'pending' });
      });
    });
  });

  test('returns 401 when Authorization header is missing', async () => {
    await withEnv({ NODE_ENV: 'production', API_URL: 'https://railway.decoda.example' }, async () => {
      await withMockFetch(async () => {
        throw new Error('fetch should not be called without Authorization');
      }, async () => {
        const response = await postAssetsRoute(new Request('http://localhost/api/assets', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: 'test' }),
        }));
        expect(response.status).toBe(401);
        const body = await response.json();
        expect(body.code).toBe('missing_authorization');
        expect(body.transport).toBe('same-origin proxy');
      });
    });
  });

  test('returns 502 when backend is unreachable', async () => {
    await withEnv({ NODE_ENV: 'production', API_URL: 'https://railway.decoda.example' }, async () => {
      await withMockFetch(async () => {
        throw new Error('connect ECONNREFUSED');
      }, async () => {
        const response = await postAssetsRoute(new Request('http://localhost/api/assets', {
          method: 'POST',
          headers: { Authorization: 'Bearer web-token', 'Content-Type': 'application/json' },
          body: JSON.stringify({}),
        }));
        expect(response.status).toBe(502);
        const body = await response.json();
        expect(body.code).toBe('backend_unreachable');
        expect(body.transport).toBe('same-origin proxy');
      });
    });
  });

  test('returns 504 when backend create times out', async () => {
    await withEnv({ NODE_ENV: 'production', API_URL: 'https://railway.decoda.example' }, async () => {
      await withMockFetch(async () => {
        throw new DOMException('Aborted', 'AbortError');
      }, async () => {
        const response = await postAssetsRoute(new Request('http://localhost/api/assets', {
          method: 'POST',
          headers: { Authorization: 'Bearer web-token', 'Content-Type': 'application/json' },
          body: JSON.stringify({}),
        }));
        expect(response.status).toBe(504);
        const body = await response.json();
        expect(body.code).toBe('backend_timeout');
        expect(body.transport).toBe('same-origin proxy');
      });
    });
  });

  test('passes backend 403 CSRF_INVALID response through to the client', async () => {
    await withEnv({ NODE_ENV: 'production', API_URL: 'https://railway.decoda.example' }, async () => {
      await withMockFetch(async () => {
        return new Response(JSON.stringify({ detail: 'CSRF token missing or invalid.', code: 'CSRF_INVALID' }), {
          status: 403,
          headers: { 'Content-Type': 'application/json' },
        });
      }, async () => {
        const response = await postAssetsRoute(new Request('http://localhost/api/assets', {
          method: 'POST',
          headers: { Authorization: 'Bearer web-token', 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: 'test' }),
        }));
        expect(response.status).toBe(403);
        const body = await response.json();
        expect(body.code).toBe('CSRF_INVALID');
        expect(body.detail).toContain('CSRF');
      });
    });
  });

  test('passes backend 400 with field_errors through to the client', async () => {
    await withEnv({ NODE_ENV: 'production', API_URL: 'https://railway.decoda.example' }, async () => {
      await withMockFetch(async () => {
        return new Response(JSON.stringify({ detail: { message: 'Validation failed.', field_errors: { name: 'Name too long.' } } }), {
          status: 400,
          headers: { 'Content-Type': 'application/json' },
        });
      }, async () => {
        const response = await postAssetsRoute(new Request('http://localhost/api/assets', {
          method: 'POST',
          headers: { Authorization: 'Bearer web-token', 'Content-Type': 'application/json' },
          body: JSON.stringify({ name: 'x'.repeat(200) }),
        }));
        expect(response.status).toBe(400);
        const body = await response.json();
        expect(body.detail.field_errors.name).toBe('Name too long.');
      });
    });
  });

  test('returns 500 when runtime config is missing', async () => {
    await withEnv({ NODE_ENV: 'production', API_URL: undefined, NEXT_PUBLIC_API_URL: undefined }, async () => {
      const response = await postAssetsRoute(new Request('http://localhost/api/assets', {
        method: 'POST',
        headers: { Authorization: 'Bearer web-token' },
        body: JSON.stringify({}),
      }));
      expect(response.status).toBe(500);
      const body = await response.json();
      expect(body.code).toBe('invalid_runtime_config');
    });
  });
});

test.describe('assets-manager create form source contract', () => {
  test('create form sends POST to /api/assets proxy (not direct to backend)', () => {
    const fs = require('node:fs') as typeof import('node:fs');
    const path = require('node:path') as typeof import('node:path');
    const source = fs.readFileSync(path.join(__dirname, '..', 'app', 'assets-manager.tsx'), 'utf-8');

    expect(source).toContain("fetch('/api/assets'");
    expect(source).not.toContain('fetch(`${apiUrl}/assets`');
  });

  test('create form distinguishes 401 auth failure from 403 CSRF failure', () => {
    const fs = require('node:fs') as typeof import('node:fs');
    const path = require('node:path') as typeof import('node:path');
    const source = fs.readFileSync(path.join(__dirname, '..', 'app', 'assets-manager.tsx'), 'utf-8');

    expect(source).toContain('response.status === 401');
    expect(source).toContain('response.status === 403');
    expect(source).toContain('CSRF_INVALID');
    expect(source).toContain('security token is invalid or expired');
  });

  test('create form handles 404 endpoint missing with specific message', () => {
    const fs = require('node:fs') as typeof import('node:fs');
    const path = require('node:path') as typeof import('node:path');
    const source = fs.readFileSync(path.join(__dirname, '..', 'app', 'assets-manager.tsx'), 'utf-8');

    expect(source).toContain('response.status === 404');
    expect(source).toContain('HTTP 404');
  });

  test('successful POST shows verification status in success message', () => {
    const fs = require('node:fs') as typeof import('node:fs');
    const path = require('node:path') as typeof import('node:path');
    const source = fs.readFileSync(path.join(__dirname, '..', 'app', 'assets-manager.tsx'), 'utf-8');

    expect(source).toContain('Asset created successfully.');
    expect(source).toContain('verification_status');
    expect(source).toContain('await load()');
  });

  test('failed POST with field errors extracts and displays backend field errors', () => {
    const fs = require('node:fs') as typeof import('node:fs');
    const path = require('node:path') as typeof import('node:path');
    const source = fs.readFileSync(path.join(__dirname, '..', 'app', 'assets-manager.tsx'), 'utf-8');

    expect(source).toContain('field_errors');
    expect(source).toContain('setFieldErrors');
    expect(source).toContain('focusFirstInvalid(responseFieldErrors');
  });
});
