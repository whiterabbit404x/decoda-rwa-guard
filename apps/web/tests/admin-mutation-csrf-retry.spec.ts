/**
 * The transient 403 on internal-admin mutations.
 *
 * REPORTED SYMPTOM
 *   decoda.guard@gmail.com is internal admin, /admin/customers loads fine, and
 *   "Resend invitation" SOMETIMES returns 403. Refreshing the page and clicking
 *   the same button then succeeds.
 *
 * CAUSE
 *   The backend anti-CSRF token is an HMAC over `nonce:window` where `window` is
 *   an absolute hour bucket (CSRF_TOKEN_WINDOW_SECONDS = 3600) and validation
 *   accepts only the current and previous bucket. It therefore dies at a
 *   wall-clock boundary — a token minted at 10:59 is refused from 12:00:00, one
 *   minted at 10:00 is refused at the same instant — not after a fixed elapsed
 *   time. The browser mints it ONCE per PilotAuthProvider mount (sign-in and
 *   session restore); a client-side route transition does not remount the
 *   provider. A tab open across the boundary keeps sending a dead token: the GET
 *   that renders the page is CSRF-exempt and still succeeds, the POST behind the
 *   button is not and is refused. A browser refresh remounts the provider, mints
 *   a token in the current window, and the identical click succeeds.
 *
 * WHAT THESE SPECS PIN DOWN
 *   1  A CSRF-refused mutation heals itself: fresh token, replayed once, no
 *      browser refresh.
 *   2  The healing is scoped to the CSRF rejection ONLY. An internal-admin
 *      refusal is never retried and never re-reported as anything else.
 *   3  The retry is bounded — one replay, never a loop — and fails closed when a
 *      fresh token cannot be minted.
 *   4  All seven internal-admin mutations share the one fix.
 *
 * Run:
 *     npx playwright test tests/admin-mutation-csrf-retry.spec.ts
 */
import fs from 'node:fs';
import path from 'node:path';

import { expect, test } from '@playwright/test';

import { isCsrfRejection, mutateWithCsrfRetry } from '../app/csrf-retry';

const CLIENT_PATH = path.join(__dirname, '..', 'app', 'admin', 'customers', 'admin-customers-client.tsx');
const client = fs.readFileSync(CLIENT_PATH, 'utf-8');

/** What the backend CSRF middleware returns (services/api/app/main.py). */
const BACKEND_CSRF_REFUSAL = { detail: 'CSRF token missing or invalid.', code: 'CSRF_INVALID' };
/** What the Next auth proxy returns (app/api/auth/_shared/proxy.ts). */
const PROXY_CSRF_REFUSAL = { detail: 'CSRF token missing or invalid.', code: 'csrf_invalid' };
/** What require_internal_admin returns (services/api/app/organizations.py). */
const INTERNAL_ADMIN_REFUSAL = {
  detail: { code: 'INTERNAL_ADMIN_REQUIRED', message: 'This area is restricted to Decoda internal staff.' },
};

type Call = { url: string; method: string; headers: Record<string, string>; body: string };

/** Records every request and replies from a scripted queue of responses. */
function recordingFetch(responses: Array<{ status: number; body: unknown }>) {
  const calls: Call[] = [];
  const impl = async (input: RequestInfo | URL, init?: RequestInit) => {
    const headers = { ...((init?.headers ?? {}) as Record<string, string>) };
    calls.push({
      url: String(input),
      method: String(init?.method ?? 'GET'),
      headers,
      body: String(init?.body ?? ''),
    });
    const next = responses[Math.min(calls.length - 1, responses.length - 1)];
    return new Response(JSON.stringify(next.body), {
      status: next.status,
      headers: { 'Content-Type': 'application/json' },
    });
  };
  return { calls, impl: impl as unknown as typeof fetch };
}

async function withMockFetch(implementation: typeof fetch, run: () => Promise<void>) {
  const original = global.fetch;
  global.fetch = implementation;
  try {
    await run();
  } finally {
    global.fetch = original;
  }
}

/** A browser holding the token minted before the hour boundary. */
const STALE_HEADERS = () => ({
  Authorization: 'Bearer session-token',
  'X-CSRF-Token': 'stalenonce.staleoldwindowsignature',
});

test.describe('what counts as a CSRF rejection', () => {
  test('the backend middleware refusal is one', () => {
    expect(isCsrfRejection(403, BACKEND_CSRF_REFUSAL)).toBe(true);
  });

  test('the auth proxy refusal is one, whatever the case of the code', () => {
    expect(isCsrfRejection(403, PROXY_CSRF_REFUSAL)).toBe(true);
    expect(isCsrfRejection(403, { code: 'CSRF_EXPIRED' })).toBe(true);
    expect(isCsrfRejection(403, { code: 'csrf_expired' })).toBe(true);
  });

  test('a refusal that only says so in a string detail is one', () => {
    expect(isCsrfRejection(403, { detail: 'CSRF token missing or invalid.' })).toBe(true);
  });

  test('an internal-admin refusal is NOT one', () => {
    // The single most important assertion here. Treating an authorization
    // denial as a stale token would retry it away and report a permission
    // problem as a transport hiccup.
    expect(isCsrfRejection(403, INTERNAL_ADMIN_REFUSAL)).toBe(false);
  });

  test('nothing but a 403 is one', () => {
    expect(isCsrfRejection(401, BACKEND_CSRF_REFUSAL)).toBe(false);
    expect(isCsrfRejection(200, BACKEND_CSRF_REFUSAL)).toBe(false);
    expect(isCsrfRejection(500, BACKEND_CSRF_REFUSAL)).toBe(false);
  });

  test('an unreadable or empty body is not assumed to be one', () => {
    expect(isCsrfRejection(403, null)).toBe(false);
    expect(isCsrfRejection(403, {})).toBe(false);
    expect(isCsrfRejection(403, 'CSRF')).toBe(false);
    expect(isCsrfRejection(403, { detail: { message: 'csrf' } })).toBe(false);
  });
});

test.describe('a stale token heals itself', () => {
  test('Resend invitation succeeds on the replay, with no browser refresh', async () => {
    const { calls, impl } = recordingFetch([
      { status: 403, body: BACKEND_CSRF_REFUSAL },
      { status: 200, body: { request: { id: 'req-1' }, invitation_sent: true } },
    ]);
    let refreshes = 0;

    await withMockFetch(impl, async () => {
      const result = await mutateWithCsrfRetry({
        url: '/api/admin/pilot-requests/req-1/resend-invitation',
        authHeaders: STALE_HEADERS,
        refreshCsrfToken: async () => {
          refreshes += 1;
          return 'freshnonce.currentwindowsignature';
        },
      });

      expect(result.response.status).toBe(200);
      expect(result.retried).toBe(true);
      expect(result.payload.invitation_sent).toBe(true);
    });

    expect(refreshes).toBe(1);
    expect(calls).toHaveLength(2);
    // The ONLY difference between the refused request and the accepted one.
    expect(calls[0].headers['X-CSRF-Token']).toBe('stalenonce.staleoldwindowsignature');
    expect(calls[1].headers['X-CSRF-Token']).toBe('freshnonce.currentwindowsignature');
    // Everything else is replayed identically: same route, same method, same
    // body, same bearer session.
    expect(calls[1].url).toBe(calls[0].url);
    expect(calls[1].method).toBe('POST');
    expect(calls[0].method).toBe('POST');
    expect(calls[1].body).toBe(calls[0].body);
    expect(calls[1].headers.Authorization).toBe(calls[0].headers.Authorization);
  });

  test('the request body survives the replay byte-for-byte', async () => {
    const { calls, impl } = recordingFetch([
      { status: 403, body: PROXY_CSRF_REFUSAL },
      { status: 200, body: { ok: true } },
    ]);

    await withMockFetch(impl, async () => {
      await mutateWithCsrfRetry({
        url: '/api/admin/customers/org-1/extend-evaluation',
        body: { days: 30 },
        authHeaders: STALE_HEADERS,
        refreshCsrfToken: async () => 'fresh.token',
      });
    });

    expect(calls[0].body).toBe(JSON.stringify({ days: 30 }));
    expect(calls[1].body).toBe(JSON.stringify({ days: 30 }));
  });

  test('a healthy token costs no extra request and mints nothing', async () => {
    const { calls, impl } = recordingFetch([{ status: 200, body: { ok: true } }]);
    let refreshes = 0;

    await withMockFetch(impl, async () => {
      const result = await mutateWithCsrfRetry({
        url: '/api/admin/pilot-requests/req-1/approve',
        authHeaders: STALE_HEADERS,
        refreshCsrfToken: async () => {
          refreshes += 1;
          return 'fresh.token';
        },
      });
      expect(result.retried).toBe(false);
      expect(result.response.status).toBe(200);
    });

    expect(calls).toHaveLength(1);
    expect(refreshes).toBe(0);
  });
});

test.describe('authorization is not weakened', () => {
  test('an internal-admin 403 is reported as itself, never retried', async () => {
    const { calls, impl } = recordingFetch([{ status: 403, body: INTERNAL_ADMIN_REFUSAL }]);
    let refreshes = 0;

    await withMockFetch(impl, async () => {
      const result = await mutateWithCsrfRetry({
        url: '/api/admin/customers/org-1/status',
        body: { status: 'suspended' },
        authHeaders: STALE_HEADERS,
        refreshCsrfToken: async () => {
          refreshes += 1;
          return 'fresh.token';
        },
      });

      expect(result.response.status).toBe(403);
      expect(result.retried).toBe(false);
      expect((result.payload.detail as { code: string }).code).toBe('INTERNAL_ADMIN_REQUIRED');
    });

    expect(calls).toHaveLength(1);
    expect(refreshes).toBe(0);
  });

  test('a 401 is reported as itself: a dead session is not a stale token', async () => {
    const { calls, impl } = recordingFetch([
      { status: 401, body: { detail: 'Authorization is required.', code: 'missing_authorization' } },
    ]);

    await withMockFetch(impl, async () => {
      const result = await mutateWithCsrfRetry({
        url: '/api/admin/customers/org-1/plan',
        body: { plan: 'scale' },
        authHeaders: STALE_HEADERS,
        refreshCsrfToken: async () => 'fresh.token',
      });
      expect(result.response.status).toBe(401);
      expect(result.retried).toBe(false);
    });

    expect(calls).toHaveLength(1);
  });

  test('a CSRF refusal that stands after one replay is reported, not looped', async () => {
    const { calls, impl } = recordingFetch([
      { status: 403, body: BACKEND_CSRF_REFUSAL },
      { status: 403, body: BACKEND_CSRF_REFUSAL },
    ]);

    await withMockFetch(impl, async () => {
      const result = await mutateWithCsrfRetry({
        url: '/api/admin/pilot-requests/req-1/reject',
        authHeaders: STALE_HEADERS,
        refreshCsrfToken: async () => 'fresh.token',
      });
      expect(result.response.status).toBe(403);
      expect(result.retried).toBe(true);
    });

    expect(calls).toHaveLength(2);
  });

  test('when no fresh token can be minted the refusal stands', async () => {
    const { calls, impl } = recordingFetch([{ status: 403, body: BACKEND_CSRF_REFUSAL }]);

    await withMockFetch(impl, async () => {
      const result = await mutateWithCsrfRetry({
        url: '/api/admin/pilot-requests/req-1/resend-invitation',
        authHeaders: STALE_HEADERS,
        // A blip on /api/auth/csrf: fail closed rather than send the mutation
        // without a token the backend will accept.
        refreshCsrfToken: async () => null,
      });
      expect(result.response.status).toBe(403);
      expect(result.retried).toBe(false);
    });

    expect(calls).toHaveLength(1);
  });
});

test.describe('every internal-admin mutation shares the fix', () => {
  // The seven buttons on /admin/customers and the route each one posts to.
  const MUTATIONS: Array<[string, string]> = [
    ['Approve', '/api/admin/pilot-requests/req-1/approve'],
    ['Reject', '/api/admin/pilot-requests/req-1/reject'],
    ['Resend invitation', '/api/admin/pilot-requests/req-1/resend-invitation'],
    ['Extend 30d', '/api/admin/customers/org-1/extend-evaluation'],
    ['Suspend', '/api/admin/customers/org-1/status'],
    ['Reactivate', '/api/admin/customers/org-1/status'],
    ['Upgrade to Scale', '/api/admin/customers/org-1/plan'],
  ];

  for (const [label, url] of MUTATIONS) {
    test(`${label} recovers from a stale token`, async () => {
      const { calls, impl } = recordingFetch([
        { status: 403, body: BACKEND_CSRF_REFUSAL },
        { status: 200, body: { ok: true } },
      ]);

      await withMockFetch(impl, async () => {
        const result = await mutateWithCsrfRetry({
          url,
          authHeaders: STALE_HEADERS,
          refreshCsrfToken: async () => 'fresh.token',
        });
        expect(result.response.status).toBe(200);
        expect(result.retried).toBe(true);
      });

      expect(calls).toHaveLength(2);
      expect(calls[1].url).toBe(url);
      expect(calls[1].headers['X-CSRF-Token']).toBe('fresh.token');
    });
  }

  test('both console helpers route through the shared retry', () => {
    // act() covers Extend 30d / Suspend / Reactivate / Upgrade to Scale,
    // actOnRequest() covers Approve / Reject / Resend invitation. Neither may
    // POST directly again: a bare fetch would reintroduce the bug for whichever
    // buttons it serves.
    expect(client).toContain("import { mutateWithCsrfRetry } from 'app/csrf-retry'");
    expect(client.match(/mutateWithCsrfRetry\(\{/g) ?? []).toHaveLength(2);
    expect(client).not.toContain("method: 'POST'");
  });

  test('the console mints a token when it opens without one', () => {
    // Otherwise a single failed mint at session restore leaves every action
    // disabled on !csrfReady until the founder reloads the page.
    expect(client).toContain('refreshCsrfToken');
    expect(client).toContain('if (!isAuthenticated || csrfReady)');
  });

  test('the buttons still refuse to fire without a token', () => {
    // Fail-closed is preserved: the retry heals a stale token, it does not
    // license sending a mutation with none.
    expect(client.match(/disabled=\{!csrfReady \|\| busyId ===/g) ?? []).toHaveLength(7);
  });
});
