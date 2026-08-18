import { expect, test } from '@playwright/test';

import { GET as getWorkspaceSettings, PATCH as patchWorkspaceSettings } from '../app/api/workspace/settings/route';
import { GET as getGovernanceSummary } from '../app/api/workspace/governance/summary/route';
import { POST as postSecurityChange } from '../app/api/workspace/security-settings/change/route';

// These are the same-origin proxy routes that were MISSING for Screen 11 — the settings page
// was calling the backend directly from the browser (via NEXT_PUBLIC_API_URL), so nothing
// reached Railway. Every customer-facing read/write must instead route through /api/* which
// resolves the backend base URL server-side (API_URL) — the canonical transport /auth/me uses.

const BACKEND = 'https://railway.decoda.example';
const TOKEN = 'Bearer test-access-token';
const WORKSPACE = '61dc1921-a481-4d5c-93f4-8687f981111a';
const CSRF = 'a1b2c3d4e5f6.deadbeefcafebabe';

type Seen = { url: string; method: string; auth: string | null; csrf: string | null; workspace: string | null; body: string | undefined };

function withEnv(overrides: Record<string, string | undefined>, run: () => Promise<void>) {
  const original = new Map<string, string | undefined>();
  for (const [k, v] of Object.entries(overrides)) {
    original.set(k, process.env[k]);
    if (v === undefined) delete process.env[k];
    else process.env[k] = v;
  }
  const restore = () => {
    for (const [k, v] of original.entries()) {
      if (v === undefined) delete process.env[k];
      else process.env[k] = v;
    }
  };
  return run().finally(restore);
}

async function withMockBackend(
  status: number,
  body: unknown,
  run: (getSeen: () => Seen | null) => Promise<void>,
) {
  const originalFetch = global.fetch;
  let seen: Seen | null = null;
  global.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
    const headers = new Headers(init?.headers);
    seen = {
      url: String(input),
      method: String(init?.method ?? 'GET'),
      auth: headers.get('authorization'),
      csrf: headers.get('x-csrf-token'),
      workspace: headers.get('x-workspace-id'),
      body: typeof init?.body === 'string' ? init.body : undefined,
    };
    return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
  }) as typeof fetch;
  try {
    await run(() => seen);
  } finally {
    global.fetch = originalFetch;
  }
}

// 1. A GET read forwards auth + workspace to the backend and needs NO CSRF token.
test('GET /api/workspace/settings forwards to the backend and requires no CSRF', async () => {
  await withEnv({ NODE_ENV: 'production', API_URL: BACKEND }, async () => {
    await withMockBackend(200, { workspace_id: WORKSPACE, name: 'Rabbit', can_manage: false }, async (getSeen) => {
      const res = await getWorkspaceSettings(new Request('http://localhost/api/workspace/settings', {
        method: 'GET',
        headers: { Authorization: TOKEN, 'X-Workspace-Id': WORKSPACE }, // deliberately NO X-CSRF-Token
      }));

      expect(res.status).toBe(200);
      const seen = getSeen();
      expect(seen?.url).toBe(`${BACKEND}/workspace/settings`);
      expect(seen?.method).toBe('GET');
      expect(seen?.auth).toBe(TOKEN);
      expect(seen?.workspace).toBe(WORKSPACE);
      expect(seen?.csrf).toBeNull(); // GET never requires or sends CSRF
      await expect(res.json()).resolves.toMatchObject({ can_manage: false });
    });
  });
});

// 2. The governance summary read is served the same way (backend status preserved).
test('GET /api/workspace/governance/summary preserves the backend status code', async () => {
  await withEnv({ NODE_ENV: 'production', API_URL: BACKEND }, async () => {
    await withMockBackend(200, { anomalies: { evaluated: true, open_total: 0, by_severity: {} } }, async (getSeen) => {
      const res = await getGovernanceSummary(new Request('http://localhost/api/workspace/governance/summary', {
        method: 'GET',
        headers: { Authorization: TOKEN, 'X-Workspace-Id': WORKSPACE },
      }));
      expect(res.status).toBe(200);
      expect(getSeen()?.url).toBe(`${BACKEND}/workspace/governance/summary`);
    });
  });
});

// 3. A read with no Authorization is rejected by the proxy (401) and never hits the backend.
test('GET proxy rejects an unauthenticated read with 401 and does not call the backend', async () => {
  await withEnv({ NODE_ENV: 'production', API_URL: BACKEND }, async () => {
    await withMockBackend(200, {}, async (getSeen) => {
      const res = await getGovernanceSummary(new Request('http://localhost/api/workspace/governance/summary', {
        method: 'GET',
      }));
      expect(res.status).toBe(401);
      expect(getSeen()).toBeNull(); // backend never contacted
    });
  });
});

// 4. A mutation forwards the X-CSRF-Token header to the backend so the backend enforces CSRF.
test('POST /api/workspace/security-settings/change forwards CSRF + body to the backend', async () => {
  await withEnv({ NODE_ENV: 'production', API_URL: BACKEND }, async () => {
    await withMockBackend(200, { status: 'pending_approval', risk_level: 'high' }, async (getSeen) => {
      const res = await postSecurityChange(new Request('http://localhost/api/workspace/security-settings/change', {
        method: 'POST',
        headers: { Authorization: TOKEN, 'X-Workspace-Id': WORKSPACE, 'X-CSRF-Token': CSRF, 'Content-Type': 'application/json' },
        body: JSON.stringify({ mfa_enforcement: 'optional', reauthentication_minutes: 120 }),
      }));

      expect(res.status).toBe(200);
      const seen = getSeen();
      expect(seen?.url).toBe(`${BACKEND}/workspace/security-settings/change`);
      expect(seen?.method).toBe('POST');
      expect(seen?.csrf).toBe(CSRF); // relayed so the backend can enforce CSRF on the write
      expect(seen?.body).toContain('reauthentication_minutes');
    });
  });
});

// 5. The version-guarded settings save (PATCH) is proxied and preserves a 409 conflict.
test('PATCH /api/workspace/settings preserves a 409 stale-version conflict', async () => {
  await withEnv({ NODE_ENV: 'production', API_URL: BACKEND }, async () => {
    await withMockBackend(409, { detail: { code: 'STALE_VERSION' } }, async (getSeen) => {
      const res = await patchWorkspaceSettings(new Request('http://localhost/api/workspace/settings', {
        method: 'PATCH',
        headers: { Authorization: TOKEN, 'X-Workspace-Id': WORKSPACE, 'X-CSRF-Token': CSRF, 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: 'Rabbit', timezone: 'UTC', currency: 'USD', expected_version: 1 }),
      }));
      expect(res.status).toBe(409);
      expect(getSeen()?.method).toBe('PATCH');
    });
  });
});
