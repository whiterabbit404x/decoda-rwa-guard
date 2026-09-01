/**
 * Screen 11 — the same-origin proxy routes for Governance & Policy.
 *
 * Every customer-facing policy read/write goes through /api/*, which resolves
 * the backend base URL SERVER-SIDE from API_URL. The browser never holds it.
 *
 * What these tests pin:
 *   * the four routes forward to the right backend paths,
 *   * auth + workspace are relayed so the BACKEND enforces tenancy and RBAC,
 *   * a mutation relays the CSRF token so the backend can enforce CSRF,
 *   * an unauthenticated request never reaches the backend at all,
 *   * the backend's status is PRESERVED — a 403, a 409 or a 503 arrives at the
 *     UI as itself, which is what lets the panel fail closed instead of
 *     rendering a verdict it never received.
 */
import { expect, test } from '@playwright/test';

import { GET as listPolicies } from '../app/api/workspace/governance/policies/route';
import { GET as getPolicy, PATCH as patchPolicy } from '../app/api/workspace/governance/policies/[policyRef]/route';
import { GET as getHistory } from '../app/api/workspace/governance/policies/[policyRef]/history/route';
import { POST as simulate } from '../app/api/workspace/governance/policies/[policyRef]/simulate/route';

const BACKEND = 'https://railway.decoda.example';
const TOKEN = 'Bearer test-access-token';
const WORKSPACE = '61dc1921-a481-4d5c-93f4-8687f981111a';
const CSRF = 'a1b2c3d4e5f6.deadbeefcafebabe';
const POLICY = 'POL-MINT-007';

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

async function withMockBackend(status: number, body: unknown, run: (getSeen: () => Seen | null) => Promise<void>) {
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

const authed = (method = 'GET', body?: unknown, csrf = false) => new Request(`http://localhost/api/x`, {
  method,
  headers: {
    Authorization: TOKEN,
    'X-Workspace-Id': WORKSPACE,
    ...(csrf ? { 'X-CSRF-Token': CSRF } : {}),
    ...(body ? { 'Content-Type': 'application/json' } : {}),
  },
  ...(body ? { body: JSON.stringify(body) } : {}),
});

const params = Promise.resolve({ policyRef: POLICY });

test('GET /api/workspace/governance/policies forwards to the backend list route', async () => {
  await withEnv({ NODE_ENV: 'production', API_URL: BACKEND }, async () => {
    await withMockBackend(200, { policies: [], can_manage: false }, async (getSeen) => {
      const res = await listPolicies(authed());
      expect(res.status).toBe(200);
      const seen = getSeen();
      expect(seen?.url).toBe(`${BACKEND}/workspace/governance/policies`);
      expect(seen?.method).toBe('GET');
      expect(seen?.auth).toBe(TOKEN);
      expect(seen?.workspace).toBe(WORKSPACE);
      expect(seen?.csrf).toBeNull(); // GET never requires or sends CSRF
      await expect(res.json()).resolves.toMatchObject({ can_manage: false });
    });
  });
});

test('GET one policy and its history forward to the per-policy backend routes', async () => {
  await withEnv({ NODE_ENV: 'production', API_URL: BACKEND }, async () => {
    await withMockBackend(200, { policy: {} }, async (getSeen) => {
      await getPolicy(authed(), { params });
      expect(getSeen()?.url).toBe(`${BACKEND}/workspace/governance/policies/POL-MINT-007`);
    });
    await withMockBackend(200, { versions: [] }, async (getSeen) => {
      await getHistory(authed(), { params });
      expect(getSeen()?.url).toBe(`${BACKEND}/workspace/governance/policies/POL-MINT-007/history`);
    });
  });
});

test('a policy reference is URL-encoded, never interpolated raw into the backend path', async () => {
  await withEnv({ NODE_ENV: 'production', API_URL: BACKEND }, async () => {
    await withMockBackend(404, { detail: 'nope' }, async (getSeen) => {
      await getPolicy(authed(), { params: Promise.resolve({ policyRef: '../../workspace/members' }) });
      expect(getSeen()?.url).toBe(`${BACKEND}/workspace/governance/policies/..%2F..%2Fworkspace%2Fmembers`);
    });
  });
});

test('POST …/simulate forwards CSRF + body to the backend', async () => {
  await withEnv({ NODE_ENV: 'production', API_URL: BACKEND }, async () => {
    await withMockBackend(200, { decision: 'DENY', reason_codes: ['COMPLIANCE_APPROVAL_MISSING'] }, async (getSeen) => {
      const res = await simulate(
        authed('POST', { operation: 'MINT', amount_usd: '5000000', compliance_approval: false }, true),
        { params },
      );
      expect(res.status).toBe(200);
      const seen = getSeen();
      expect(seen?.url).toBe(`${BACKEND}/workspace/governance/policies/POL-MINT-007/simulate`);
      expect(seen?.method).toBe('POST');
      expect(seen?.csrf).toBe(CSRF); // relayed so the backend can enforce CSRF on the write
      expect(seen?.body).toContain('compliance_approval');
      await expect(res.json()).resolves.toMatchObject({ decision: 'DENY' });
    });
  });
});

test('PATCH on a policy forwards CSRF + body so the backend enforces security.manage', async () => {
  await withEnv({ NODE_ENV: 'production', API_URL: BACKEND }, async () => {
    await withMockBackend(200, { status: 'updated' }, async (getSeen) => {
      await patchPolicy(authed('PATCH', { status: 'DISABLED', expected_version: 7 }, true), { params });
      const seen = getSeen();
      expect(seen?.method).toBe('PATCH');
      expect(seen?.url).toBe(`${BACKEND}/workspace/governance/policies/POL-MINT-007`);
      expect(seen?.csrf).toBe(CSRF);
      expect(seen?.body).toContain('expected_version');
    });
  });
});

test('an unauthenticated policy request is rejected and never reaches the backend', async () => {
  await withEnv({ NODE_ENV: 'production', API_URL: BACKEND }, async () => {
    await withMockBackend(200, {}, async (getSeen) => {
      const res = await listPolicies(new Request('http://localhost/api/workspace/governance/policies'));
      expect(res.status).toBe(401);
      expect(getSeen()).toBeNull();
    });
    await withMockBackend(200, {}, async (getSeen) => {
      const res = await simulate(new Request('http://localhost/api/x', { method: 'POST', body: '{}' }), { params });
      expect(res.status).toBe(401);
      expect(getSeen()).toBeNull(); // an unauthenticated simulation never evaluates anything
    });
  });
});

test('the backend status is preserved so the panel can fail closed', async () => {
  await withEnv({ NODE_ENV: 'production', API_URL: BACKEND }, async () => {
    for (const status of [400, 403, 404, 409, 503]) {
      await withMockBackend(status, { detail: { code: 'x', message: 'y' } }, async () => {
        const res = await simulate(authed('POST', { operation: 'MINT' }, true), { params });
        expect(res.status).toBe(status);
      });
    }
  });
});

test('an unconfigured backend URL is a proxy error, never a fabricated success', async () => {
  await withEnv({ NODE_ENV: 'production', API_URL: undefined, NEXT_PUBLIC_API_URL: undefined }, async () => {
    await withMockBackend(200, { decision: 'ALLOW' }, async (getSeen) => {
      const res = await simulate(authed('POST', { operation: 'MINT' }, true), { params });
      expect(res.status).toBe(500);
      expect(getSeen()).toBeNull();
      const body = await res.json();
      expect(body.code).toBe('invalid_runtime_config');
      // Critically: no decision field at all, so nothing downstream can read ALLOW.
      expect(body.decision).toBeUndefined();
    });
  });
});
