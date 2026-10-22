import { expect, test, type Page } from '@playwright/test';
import * as fs from 'node:fs';
import * as path from 'node:path';
import { pathToFileURL } from 'node:url';

import { GET as getRuntimeConfigRoute } from '../app/api/runtime-config/route';
import { GET as getRetentionPolicies } from '../app/api/workspace/retention-policies/route';
import { alertStreamReconnectDelayMs } from '../app/alert-stream-client';
import { breadcrumbsFor } from '../app/shell-breadcrumbs';
import { getBrowserRuntimeConfig } from '../app/runtime-config';
import { resolveChromium, startRenderHarness, type Harness } from './support/render-harness';

/**
 * Navigation performance: each change that shortened the critical path or
 * removed a duplicate request, pinned by behaviour rather than by source text.
 *
 * The before/after numbers live in artifacts/navigation-timing/ (captured with
 * scripts/capture-navigation-timings.mjs). These tests make sure the request
 * shapes those numbers depend on cannot quietly come back.
 */

const chromiumExecutable = resolveChromium();
if (chromiumExecutable) test.use({ launchOptions: { executablePath: chromiumExecutable } });

function withEnv(overrides: Record<string, string | undefined>, run: () => Promise<void> | void) {
  const original = new Map<string, string | undefined>();
  for (const [key, value] of Object.entries(overrides)) {
    original.set(key, process.env[key]);
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }
  const restore = () => {
    for (const [key, value] of original.entries()) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  };
  return Promise.resolve().then(run).finally(restore);
}

// ── runtime config arrives with the document ─────────────────────────────────

test.describe('runtime config is resolved on the server', () => {
  test('the browser config carries the same-origin proxy, never the API itself', async () => {
    await withEnv({ API_URL: 'https://api.decoda.example', LIVE_MODE_ENABLED: 'true' }, () => {
      const config = getBrowserRuntimeConfig();
      expect(config.apiUrl).toBe('/api/backend');
      expect(JSON.stringify(config)).not.toContain('api.decoda.example');
      expect(config.configured).toBe(true);
      expect(config.liveModeEnabled).toBe(true);
    });
  });

  test('/api/runtime-config answers with exactly what the layout inlines', async () => {
    await withEnv({ API_URL: 'https://api.decoda.example', LIVE_MODE_ENABLED: 'true' }, async () => {
      const response = await getRuntimeConfigRoute();
      expect(await response.json()).toEqual(getBrowserRuntimeConfig());
    });
  });

  test('an unconfigured deployment still reads as unconfigured', async () => {
    await withEnv({ API_URL: undefined, NEXT_PUBLIC_API_URL: undefined, NODE_ENV: 'production' }, () => {
      const config = getBrowserRuntimeConfig();
      expect(config.apiUrl).toBeNull();
      expect(config.configured).toBe(false);
    });
  });
});

// ── the product layout never streams a screen ahead of its gate ──────────────

test.describe('the auth gate in the server stream', () => {
  test('the layout fallback is the skeleton, never the screen rendered outside the gate', () => {
    const layout = fs.readFileSync(path.join(__dirname, '..', 'app', '(product)', 'layout.tsx'), 'utf8');
    expect(layout).toContain('<Suspense fallback={<ProductLayoutLoading />}>');
    expect(layout).not.toMatch(/fallback=\{[^}]*\{children\}/);
  });

});

const SKELETON_BOOTSTRAP = `
import React from '/vendor/react.js';
import { createRoot } from '/vendor/react-dom-client.js';
import { ScreenSkeleton } from '/app/components/screen-states.tsx';

createRoot(document.getElementById('root')).render(
  React.createElement(ScreenSkeleton, { label: 'Validating your session and loading the active workspace…' }));
`;

test.describe('the gate skeleton', () => {
  let harness: Harness;
  test.beforeAll(async () => { harness = await startRenderHarness({ bootstrap: SKELETON_BOOTSTRAP }); });
  test.afterAll(async () => { await harness?.close(); });

  test('stays small enough for React to stream it inline', async ({ page }) => {
    // React outlines a completed Suspense boundary (fallback first, content
    // swapped in by script) once its HTML passes 500 bytes in a response that is
    // already past 12.8KB -- which the shell always is. The gate's loading state
    // is that boundary's content on every product document load.
    await page.goto(harness.url);
    const skeleton = page.getByTestId('screen-skeleton');
    await expect(skeleton).toHaveAttribute('role', 'status');
    await expect(skeleton).toHaveAttribute('aria-busy', 'true');
    const bytes = await page.evaluate(() => new TextEncoder().encode(document.getElementById('root')!.innerHTML).length);
    expect(bytes).toBeLessThan(500);
    // The blocks are decoration only: hidden from assistive tech, label kept.
    await expect(page.locator('.screenSkeletonShape')).toHaveAttribute('aria-hidden', 'true');
    await expect(page.locator('.sr-only')).toHaveText('Validating your session and loading the active workspace…');
  });
});

// ── the header trail ─────────────────────────────────────────────────────────

test.describe('breadcrumb trail', () => {
  test('a sidebar screen reads group › screen, with the screen current', () => {
    expect(breadcrumbsFor('/alerts')).toEqual([{ label: 'Monitor' }, { label: 'Alerts' }]);
    expect(breadcrumbsFor('/dashboard')).toEqual([{ label: 'Overview' }, { label: 'Dashboard' }]);
  });

  test('a page below a screen links back to it', () => {
    expect(breadcrumbsFor('/incidents/inc-1')).toEqual([
      { label: 'Investigate & respond' },
      { label: 'Incidents', href: '/incidents' },
      { label: 'Incident' },
    ]);
    expect(breadcrumbsFor('/settings/security')).toEqual([
      { label: 'Govern & prove' },
      { label: 'Settings', href: '/settings' },
      { label: 'Security' },
    ]);
  });

  test('the group is never a link, and an unlisted route gets no guessed trail', () => {
    for (const href of ['/alerts', '/incidents/inc-1', '/system-health/readiness']) {
      expect(breadcrumbsFor(href)[0].href).toBeUndefined();
    }
    expect(breadcrumbsFor('/workspaces')).toEqual([]);
    // "/dashboard-x" must not borrow the Dashboard crumb by prefix.
    expect(breadcrumbsFor('/dashboard-x')).toEqual([]);
  });
});

// ── the alert stream backs off ───────────────────────────────────────────────

test('alert stream reconnects back off to a one-minute ceiling', () => {
  expect([0, 1, 2, 3, 4, 5, 6, 12].map(alertStreamReconnectDelayMs)).toEqual([
    3000, 6000, 12000, 24000, 48000, 60000, 60000, 60000,
  ]);
});

// ── the Settings retention read has a route ──────────────────────────────────

test.describe('GET /api/workspace/retention-policies', () => {
  const BACKEND = 'https://railway.decoda.example';
  const WORKSPACE = '61dc1921-a481-4d5c-93f4-8687f981111a';

  async function call(status: number, body: unknown) {
    const originalFetch = global.fetch;
    let seen: { url: string; method: string; auth: string | null; workspace: string | null } | null = null;
    global.fetch = (async (input: string | URL | Request, init?: RequestInit) => {
      const headers = new Headers(init?.headers);
      seen = {
        url: String(input),
        method: String(init?.method ?? 'GET'),
        auth: headers.get('authorization'),
        workspace: headers.get('x-workspace-id'),
      };
      return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
    }) as typeof fetch;
    try {
      let response: Response | null = null;
      await withEnv({ NODE_ENV: 'production', API_URL: BACKEND }, async () => {
        response = await getRetentionPolicies(new Request('http://localhost/api/workspace/retention-policies', {
          headers: { Authorization: 'Bearer test-access-token', 'X-Workspace-Id': WORKSPACE },
        }));
      });
      return { response: response as unknown as Response, seen: seen as typeof seen };
    } finally {
      global.fetch = originalFetch;
    }
  }

  test('forwards the read, scoped to the workspace, to the backend route', async () => {
    const { response, seen } = await call(200, { policies: [], workspace_id: WORKSPACE });
    expect(response.status).toBe(200);
    expect(seen?.url).toBe(`${BACKEND}/workspace/retention-policies`);
    expect(seen?.method).toBe('GET');
    expect(seen?.auth).toBe('Bearer test-access-token');
    expect(seen?.workspace).toBe(WORKSPACE);
  });

  test('a refusal stays a refusal, so the card can say "denied" rather than "none"', async () => {
    const { response } = await call(403, { detail: 'Forbidden' });
    expect(response.status).toBe(403);
  });
});

// ── the capture harness counts duplicates the way the report says ────────────

test('the navigation capture reports a repeated identical request as a duplicate', async () => {
  const harnessPath = path.join(__dirname, '../../../scripts/capture-navigation-timings.mjs');
  const { summarise } = (await import(pathToFileURL(harnessPath).href)) as {
    summarise: (window: unknown) => { duplicateCount: number; apiCount: number; settledMs: number };
  };
  const row = summarise({
    resources: [
      { path: '/api/dashboard/executive-summary', queryKey: '', start: 10, end: 60 },
      { path: '/api/dashboard/executive-summary', queryKey: '', start: 500, end: 560 },
      { path: '/api/incidents', queryKey: '1:9', start: 10, end: 40 },
      { path: '/api/incidents', queryKey: '1:12', start: 10, end: 45 },
      // A stream never ends by design; it must not define "settled".
      { path: '/api/stream/alerts', queryKey: '', start: 20, end: 9000 },
    ],
    marks: {},
    paint: {},
    lastLoadingMs: null,
  });
  expect(row.apiCount).toBe(5);
  expect(row.duplicateCount).toBe(1);
  expect(row.settledMs).toBe(560);
});

// ── real components: what the browser actually requests ──────────────────────

type BackendOptions = {
  memberships?: boolean;
};

/**
 * A session backend that answers the auth leg and records every request. Data
 * endpoints answer with an empty 200 — nothing is invented for a screen to show.
 */
async function installRecordingBackend(page: Page, pathname: string, options: BackendOptions = {}) {
  await page.addInitScript(([route, withMembership]) => {
    const w = window as any;
    w.__nav = { pathname: route, search: '', pushes: [], replaces: [] };
    w.__requests = [];
    const json = (body: unknown, status = 200) =>
      Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } }));
    w.fetch = (input: any) => {
      const p = String(typeof input === 'string' ? input : input?.url ?? '').split('?')[0];
      w.__requests.push(p);
      if (p === '/api/runtime-config') {
        return json({
          apiUrl: '/api/backend', liveModeEnabled: true, apiTimeoutMs: null, configured: true, diagnostic: null,
          source: { apiUrl: 'API_URL', liveModeEnabled: 'LIVE_MODE_ENABLED', apiTimeoutMs: 'default' },
        });
      }
      if (p === '/api/auth/csrf') return json({ csrfToken: 'fixture-csrf' });
      if (p === '/api/auth/me') {
        return json({
          user: {
            id: 'fixture-user', email: 'analyst@acme.test', full_name: 'Acme Analyst',
            current_workspace_id: withMembership ? 'fixture-ws' : null,
            current_workspace: withMembership ? { id: 'fixture-ws', name: 'Acme Capital', slug: 'acme' } : null,
            memberships: withMembership
              ? [{ workspace_id: 'fixture-ws', role: 'admin', workspace: { id: 'fixture-ws', name: 'Acme Capital', slug: 'acme' } }]
              : [],
          },
        });
      }
      if (p === '/api/account/pilot-access') return json({ has_access: false, state: 'none' });
      if (p === '/api/stream/alerts') return json({ detail: 'stream unavailable' }, 503);
      return json({});
    };
  }, [pathname, options.memberships !== false] as const);
}

const SERVER_CONFIG = `{
  apiUrl: '/api/backend', liveModeEnabled: true, apiTimeoutMs: null, configured: true, diagnostic: null,
  source: { apiUrl: 'API_URL', liveModeEnabled: 'LIVE_MODE_ENABLED', apiTimeoutMs: 'default' },
}`;

const AUTH_BOOTSTRAP = `
import React from '/vendor/react.js';
import { createRoot } from '/vendor/react-dom-client.js';
import { PilotAuthProvider, usePilotAuth } from '/app/pilot-auth-context.tsx';

function Probe() {
  const { loading, user } = usePilotAuth();
  window.__auth = { loading, email: user ? user.email : null };
  return null;
}
const props = window.__withServerConfig ? { initialRuntimeConfig: ${SERVER_CONFIG} } : null;
createRoot(document.getElementById('root')).render(
  React.createElement(PilotAuthProvider, props, React.createElement(Probe)));
`;

test.describe('the auth bootstrap', () => {
  let harness: Harness;
  test.beforeAll(async () => { harness = await startRenderHarness({ bootstrap: AUTH_BOOTSTRAP, css: false }); });
  test.afterAll(async () => { await harness?.close(); });

  test('with the config from the server, the session is asked for at once', async ({ page }) => {
    await installRecordingBackend(page, '/dashboard');
    await page.addInitScript(() => { (window as any).__withServerConfig = true; });
    await page.goto(harness.url);
    await expect.poll(() => page.evaluate(() => (window as any).__auth?.email)).toBe('analyst@acme.test');
    const requests: string[] = await page.evaluate(() => (window as any).__requests);
    expect(requests).not.toContain('/api/runtime-config');
    expect(requests).toContain('/api/auth/me');
    expect(requests).toContain('/api/auth/csrf');
  });

  test('without it, the provider still fetches the config first (fallback kept)', async ({ page }) => {
    await installRecordingBackend(page, '/dashboard');
    await page.goto(harness.url);
    await expect.poll(() => page.evaluate(() => (window as any).__auth?.email)).toBe('analyst@acme.test');
    const requests: string[] = await page.evaluate(() => (window as any).__requests);
    expect(requests[0]).toBe('/api/runtime-config');
    expect(requests.indexOf('/api/auth/me')).toBeGreaterThan(0);
  });
});

const GATE_BOOTSTRAP = `
import React from '/vendor/react.js';
import { createRoot } from '/vendor/react-dom-client.js';
import { PilotAuthProvider } from '/app/pilot-auth-context.tsx';
import AuthenticatedRoute from '/app/authenticated-route.tsx';

createRoot(document.getElementById('root')).render(
  React.createElement(PilotAuthProvider, { initialRuntimeConfig: ${SERVER_CONFIG} },
    React.createElement(AuthenticatedRoute, null,
      React.createElement('p', { id: 'product' }, 'Product content'))));
`;

test.describe('the Pilot-access read', () => {
  let harness: Harness;
  test.beforeAll(async () => { harness = await startRenderHarness({ bootstrap: GATE_BOOTSTRAP }); });
  test.afterAll(async () => { await harness?.close(); });

  test('is not made for a member, who never sees that gate', async ({ page }) => {
    await installRecordingBackend(page, '/dashboard', { memberships: true });
    await page.goto(harness.url);
    await expect(page.locator('#product')).toHaveText('Product content');
    await page.waitForTimeout(300);
    const requests: string[] = await page.evaluate(() => (window as any).__requests);
    expect(requests).not.toContain('/api/account/pilot-access');
  });

  test('is still made, and the gate shown, for an account with no membership', async ({ page }) => {
    await installRecordingBackend(page, '/dashboard', { memberships: false });
    await page.goto(harness.url);
    await expect(page.getByText('Pilot access required').first()).toBeVisible();
    await expect(page.locator('#product')).toHaveCount(0);
    const requests: string[] = await page.evaluate(() => (window as any).__requests);
    expect(requests).toContain('/api/account/pilot-access');
  });

  test('while the session resolves, the gate shows the shared skeleton, not product content', async ({ page }) => {
    await installRecordingBackend(page, '/dashboard', { memberships: true });
    // Hold /api/auth/me so the loading state can be observed.
    await page.addInitScript(() => {
      const w = window as any;
      const original = w.fetch;
      w.fetch = (input: any) => {
        const p = String(typeof input === 'string' ? input : input?.url ?? '').split('?')[0];
        if (p === '/api/auth/me') return new Promise(() => undefined);
        return original(input);
      };
    });
    await page.goto(harness.url);
    const skeleton = page.getByTestId('screen-skeleton');
    await expect(skeleton).toHaveAttribute('aria-busy', 'true');
    await expect(page.locator('#product')).toHaveCount(0);
  });
});

const RUNTIME_SUMMARY_BOOTSTRAP = `
import React from '/vendor/react.js';
import { createRoot } from '/vendor/react-dom-client.js';
import { PilotAuthProvider } from '/app/pilot-auth-context.tsx';
import { RuntimeSummaryProvider, useRuntimeSummary } from '/app/runtime-summary-context.tsx';

window.__loadingLog = [];
window.__effectRuns = 0;
function Consumer() {
  const { loading, refresh } = useRuntimeSummary();
  window.__refresh = refresh;
  if (window.__loadingLog[window.__loadingLog.length - 1] !== loading) window.__loadingLog.push(loading);
  // The shape screens use: a read keyed on the summary's loading flag.
  React.useEffect(() => { if (!loading) window.__effectRuns += 1; }, [loading]);
  return React.createElement('p', { id: 'state' }, loading ? 'loading' : 'ready');
}
createRoot(document.getElementById('root')).render(
  React.createElement(PilotAuthProvider, { initialRuntimeConfig: ${SERVER_CONFIG} },
    React.createElement(RuntimeSummaryProvider, null, React.createElement(Consumer))));
`;

test.describe('the runtime summary', () => {
  let harness: Harness;
  test.beforeAll(async () => { harness = await startRenderHarness({ bootstrap: RUNTIME_SUMMARY_BOOTSTRAP, css: false }); });
  test.afterAll(async () => { await harness?.close(); });

  test('a refresh replaces the answer in place instead of reading as loading again', async ({ page }) => {
    await installRecordingBackend(page, '/dashboard');
    await page.goto(harness.url);
    await expect(page.locator('#state')).toHaveText('ready');
    await page.evaluate(() => (window as any).__refresh());
    await expect.poll(async () => (await page.evaluate(() => (window as any).__requests as string[]))
      .filter((p) => p === '/api/ops/monitoring/runtime-status').length).toBe(2);
    await page.waitForTimeout(200);
    expect(await page.evaluate(() => (window as any).__loadingLog)).toEqual([true, false]);
    // A screen effect keyed on `loading` ran once, not once per refresh.
    expect(await page.evaluate(() => (window as any).__effectRuns)).toBe(1);
  });
});

const DASHBOARD_BOOTSTRAP = `
import React from '/vendor/react.js';
import { createRoot } from '/vendor/react-dom-client.js';
import { PilotAuthProvider } from '/app/pilot-auth-context.tsx';
import DashboardLiveHydrator from '/app/dashboard-live-hydrator.tsx';

createRoot(document.getElementById('root')).render(
  React.createElement(PilotAuthProvider, { initialRuntimeConfig: ${SERVER_CONFIG} },
    React.createElement(DashboardLiveHydrator)));
`;

test.describe('the dashboard', () => {
  let harness: Harness;
  test.beforeAll(async () => { harness = await startRenderHarness({ bootstrap: DASHBOARD_BOOTSTRAP }); });
  test.afterAll(async () => { await harness?.close(); });

  test('asks for the executive summary once on load, not again when the live feed first lands', async ({ page }) => {
    await installRecordingBackend(page, '/dashboard');
    await page.goto(harness.url);
    // The live feed's first snapshot has landed...
    await expect.poll(async () => (await page.evaluate(() => (window as any).__requests as string[]))
      .includes('/api/ops/monitoring/runtime-status')).toBe(true);
    // ...and the 400ms debounce that used to refetch on it has long passed.
    await page.waitForTimeout(1200);
    const requests: string[] = await page.evaluate(() => (window as any).__requests);
    expect(requests.filter((p) => p === '/api/dashboard/executive-summary')).toHaveLength(1);
  });
});
