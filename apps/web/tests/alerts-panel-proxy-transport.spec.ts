import { expect, test } from '@playwright/test';
import fs from 'fs';
import path from 'node:path';

const appDir = path.join(__dirname, '..', 'app');
const read = (...segments: string[]) => fs.readFileSync(path.join(appDir, ...segments), 'utf8');

test.describe('Alerts page (Screen 6) same-origin proxy transport', () => {
  test('the alerts screen calls the same-origin /api proxy and never the backend directly', () => {
    const screen = read('alerts-screen.tsx');

    // Every backend call on the page goes through the same-origin Next.js proxy
    // base. A direct backend call via resolveApiUrl() silently fails in production
    // (NEXT_PUBLIC_API_URL unset) and is what made Active Alerts read 0.
    expect(screen).toContain("const API_PROXY_BASE = '/api';");
    expect(screen).toContain('const apiUrl = API_PROXY_BASE;');
    // The composed read model + the Run Triage command are both proxied.
    expect(screen).toContain('`${apiUrl}/alerts/triage/summary?${params.toString()}`');
    expect(screen).toContain('`${apiUrl}/alerts/triage/run`');
    expect(screen).not.toContain('resolveApiUrl');
  });

  test('a same-origin /api/alerts proxy route exists and resolves the backend server-side', () => {
    const route = read('api', 'alerts', '[[...path]]', 'route.ts');
    expect(route).toContain('proxyJsonToBackend');
    expect(route).toContain("return '/alerts';");

    const helper = read('api', '_shared', 'backend-proxy.ts');
    // The backend URL is resolved server-side (API_URL) — never exposed to the browser.
    expect(helper).toContain('getRuntimeConfig');
    expect(helper).toContain('X-Workspace-Id');
    // Backend status code is preserved so 201/409/200/403 paths stay distinguishable.
    expect(helper).toContain('status: response.status');
  });

  test('severity counts + pagination come from the backend read model, not the browser', () => {
    const screen = read('alerts-screen.tsx');
    // Tab counts read the backend-computed totals (the same payload as the rows).
    expect(screen).toContain('summary?.severity_totals');
    expect(screen).toContain('totals[tab.countKey]');
    // Pagination range/total are backend-driven.
    expect(screen).toContain('pagination.total');
    // No independent frontend recomputation of severity counts from the alert page
    // (which would disagree with pagination) — the old metric-card pattern is gone.
    expect(screen).not.toContain('alerts.filter((a) =>');
  });
});
