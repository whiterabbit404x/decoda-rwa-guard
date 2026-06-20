import { expect, test } from '@playwright/test';
import fs from 'fs';
import path from 'node:path';

const appDir = path.join(__dirname, '..', 'app');
const read = (...segments: string[]) => fs.readFileSync(path.join(appDir, ...segments), 'utf8');

test.describe('Incidents page same-origin proxy transport', () => {
  test('incidents panel calls the same-origin /api proxy, never the backend directly', () => {
    const panel = read('incidents-panel.tsx');

    // A direct backend call via resolveApiUrl() silently fails in production (NEXT_PUBLIC_API_URL
    // unset), which is exactly why /incidents rendered "No incidents yet" while the Alerts page
    // reported a linked incident. The Incidents list must go through the same-origin proxy.
    expect(panel).toContain("const API_PROXY_BASE = '/api';");
    expect(panel).toContain('const apiUrl = API_PROXY_BASE;');
    expect(panel).toContain('`${apiUrl}/incidents?${params.toString()}`');
    expect(panel).not.toContain('resolveApiUrl');
  });

  test('same-origin /api/incidents proxy routes exist (list + detail)', () => {
    const list = read('api', 'incidents', 'route.ts');
    expect(list).toContain('proxyJsonToBackend');
    expect(list).toContain("backendPath: '/incidents'");

    const detail = read('api', 'incidents', '[incidentId]', 'route.ts');
    expect(detail).toContain('proxyJsonToBackend');
    expect(detail).toContain('/incidents/${encodeURIComponent(incidentId)}');

    const actions = read('api', 'response', 'actions', 'route.ts');
    expect(actions).toContain("backendPath: '/response/actions'");
  });

  test('an incident detail route page exists so /incidents/{id} loads', () => {
    const page = read('(product)', 'incidents', '[incidentId]', 'page.tsx');
    expect(page).toContain('IncidentsPanel');
    expect(page).toContain('initialSelectedId={incidentId}');
  });

  test('Alerts page View/Open Incident routes to the specific incident, not just /incidents', () => {
    const alerts = read('alerts-panel.tsx');
    // Table row + detail-panel "View Incident" links deep-link to the persisted incident id.
    expect(alerts).toContain('href={`/incidents/${alert.incident_id}`}');
    // Open Incident escalates then navigates to the incident the backend created/linked.
    expect(alerts).toContain('window.location.href = `/incidents/${result.incident_id}`;');
    // It never falls back to the bare list route for a linked alert.
    expect(alerts).not.toContain('href="/incidents"');
  });

  test('empty-state copy is truthful: alerts-exist message takes precedence', () => {
    const panel = read('incidents-panel.tsx');
    // When alerts exist the page says incidents have not been opened — never the detection-stage
    // line, which would falsely claim no alert exists.
    expect(panel).toContain('const anyAlerts = alertsExist || activeAlerts > 0;');
    expect(panel).toContain('Alerts exist, but no incident has been opened yet.');
    // alertsExist comes from a real /alerts probe, not frontend-only runtime counters alone.
    expect(panel).toContain('`${apiUrl}/alerts?limit=1`');
  });

  test('read-path diagnostics and a bug-visible filtered-out guard are logged', () => {
    const panel = read('incidents-panel.tsx');
    expect(panel).toContain("console.log('frontend_incidents_fetch_response_count'");
    // If the API returns incidents but none render, surface it loudly in dev.
    expect(panel).toContain("console.error('incidents_list_bug_filtered_out'");
  });
});
