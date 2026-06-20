import { expect, test } from '@playwright/test';
import fs from 'fs';
import path from 'node:path';

const appDir = path.join(__dirname, '..', 'app');
const read = (...segments: string[]) => fs.readFileSync(path.join(appDir, ...segments), 'utf8');

test.describe('Alerts page same-origin proxy transport', () => {
  test('alerts panel calls the same-origin /api proxy and never the backend directly', () => {
    const panel = read('alerts-panel.tsx');

    // The list (and every other backend call on the page) goes through the same-origin
    // Next.js proxy base. A direct backend call via resolveApiUrl() silently fails in
    // production (NEXT_PUBLIC_API_URL unset) and is what made Active Alerts read 0.
    expect(panel).toContain("const API_PROXY_BASE = '/api';");
    expect(panel).toContain('const apiUrl = API_PROXY_BASE;');
    expect(panel).toContain('`${apiUrl}/alerts?${params.toString()}`');
    expect(panel).not.toContain('resolveApiUrl');
  });

  test('a same-origin /api/alerts proxy route exists and resolves the backend server-side', () => {
    const route = read('api', 'alerts', '[[...path]]', 'route.ts');
    expect(route).toContain('proxyJsonToBackend');
    expect(route).toContain("return '/alerts';");

    const helper = read('api', '_shared', 'backend-proxy.ts');
    // The backend URL is resolved server-side (API_URL) — never exposed to the browser.
    expect(helper).toContain('getRuntimeConfig');
    expect(helper).toContain('X-Workspace-Id');
    // Backend status code is preserved so 201/409/200 Open-Alert paths stay distinguishable.
    expect(helper).toContain('status: response.status');
  });

  test('read-path diagnostics are logged on the frontend', () => {
    const panel = read('alerts-panel.tsx');
    expect(panel).toContain("console.log('frontend_alerts_fetch_started'");
    expect(panel).toContain("console.log('frontend_alerts_fetch_response_count'");
    expect(panel).toContain("console.log('frontend_alerts_render_count'");
  });

  test('Open Alert navigates to the existing alert instead of only showing a toast', () => {
    const panel = read('alerts-panel.tsx');
    // 201 / 409 / any alert_id => select + refresh + (re)select the named alert.
    expect(panel).toContain('res.status === 201 || res.status === 409 || namedAlertId');
    expect(panel).toContain('if (namedAlertId) setSelectedId(namedAlertId);');
    expect(panel).toContain('const rows = await fetchAlerts(noop);');
    // If the alert is still not visible after refresh, the exact diagnostic is logged.
    expect(panel).toContain("console.log('existing_alert_not_visible_after_refresh'");
    expect(panel).toContain('returned_ids: rows.map((row) => row.id)');
  });

  test('count cards derive from the same normalised list as the rendered rows', () => {
    const panel = read('alerts-panel.tsx');
    // criticalCount / highConfidenceCount / linkedIncidentCount all read from `alerts`,
    // the same array filteredAlerts (the rendered rows) is derived from.
    expect(panel).toContain('const criticalCount = alerts.filter(');
    expect(panel).toContain('const highConfidenceCount = alerts.filter(isHighConfidence).length;');
    expect(panel).toContain('const linkedIncidentCount = alerts.filter((a) => !!a.incident_id).length;');
    // Active Alerts counts open rows actually returned, maxed with the runtime counter.
    expect(panel).toContain('const activeAlerts: number = Math.max(runtimeActiveAlerts, openAlertsInList);');
  });
});
