/**
 * Whole-screen theme readability — every remaining authenticated screen.
 *
 * Threat Monitoring and Settings have their own sweeps, driven by their own
 * rich fixtures. The rest of the product is covered here, mounted against an
 * authenticated session whose backend returns nothing.
 *
 * That is not a shortcut around fixtures — it is the state these screens must
 * get right most. "No data", "unavailable", "not configured" and "monitoring
 * is not live yet" are the surfaces the product's truthfulness rules govern,
 * they are what a new workspace actually sees, and they are the ones a theme
 * flip most easily strands because a reviewer looking at a populated demo
 * never renders them.
 *
 * Each screen is swept for WCAG AA on every rendered text node, in both
 * themes. A screen that throws on mount fails here rather than rendering as a
 * blank page.
 */
import { expect, test, type Page } from '@playwright/test';

import { resolveChromium, startRenderHarness, type Harness } from './support/render-harness';
import { expectReadableInBothThemes } from './support/contrast-sweep';

const chromiumExecutable = resolveChromium();
if (chromiumExecutable) test.use({ launchOptions: { executablePath: chromiumExecutable } });

/** The screens, and the module + component each page route renders. */
const SCREENS: Array<{ name: string; module: string; export: string; pathname: string; wrap?: string[] }> = [
  { name: 'Onboarding', module: '/app/(product)/onboarding-page-client.tsx', export: 'default', pathname: '/onboarding' },
  { name: 'Asset Risk', module: '/app/assets-manager.tsx', export: 'default', pathname: '/assets' },
  { name: 'Monitoring Sources', module: '/app/(product)/monitoring-sources/page.tsx', export: 'default', pathname: '/monitoring-sources' },
  { name: 'Alerts', module: '/app/alerts-screen.tsx', export: 'default', pathname: '/alerts' },
  { name: 'Incidents', module: '/app/incidents-panel.tsx', export: 'default', pathname: '/incidents' },
  { name: 'Response Actions', module: '/app/(product)/response-actions-page-client.tsx', export: 'default', pathname: '/response-actions' },
  { name: 'Evidence', module: '/app/evidence-audit-panel.tsx', export: 'default', pathname: '/evidence' },
  { name: 'Integrations', module: '/app/(product)/integrations-page-client.tsx', export: 'default', pathname: '/integrations' },
];

function bootstrapFor(screen: (typeof SCREENS)[number]): string {
  return `
import React from '/vendor/react.js';
import { createRoot } from '/vendor/react-dom-client.js';
import { PilotAuthProvider } from '/app/pilot-auth-context.tsx';
import { ThemeProvider } from '/app/theme-context.tsx';
import { RuntimeSummaryProvider } from '/app/runtime-summary-context.tsx';
import Screen from '${screen.module}';

// An error boundary so a screen that throws is REPORTED rather than silently
// rendering an empty document that would sweep clean.
class Boundary extends React.Component {
  constructor(p) { super(p); this.state = { error: null }; }
  static getDerivedStateFromError(error) { return { error }; }
  componentDidCatch(error) { window.__renderError = String(error && error.stack || error); }
  render() {
    if (this.state.error) return React.createElement('pre', null, String(this.state.error));
    return this.props.children;
  }
}

createRoot(document.getElementById('root')).render(
  React.createElement(ThemeProvider, null,
    React.createElement(PilotAuthProvider, null,
      React.createElement(RuntimeSummaryProvider, null,
        React.createElement(Boundary, null,
          React.createElement(Screen, null))))));
`;
}

/**
 * An authenticated session against a backend that has nothing to report.
 *
 * Only the session and runtime-config endpoints answer; everything else
 * returns an empty collection with a 200, which is what a freshly created
 * workspace genuinely gets. Nothing is invented — no fake alerts, no fake
 * coverage — so whatever the screen renders is its real empty state.
 */
async function installEmptyBackend(page: Page, pathname: string) {
  await page.addInitScript((route) => {
    const w = window as any;
    w.localStorage.setItem('decoda.accessToken', 'fixture-token');
    w.localStorage.setItem('decoda.theme', 'light');
    w.__nav = { pathname: route, search: '', pushes: [], replaces: [] };

    const json = (body: unknown, status = 200) =>
      Promise.resolve(new Response(JSON.stringify(body), {
        status, headers: { 'content-type': 'application/json' },
      }));

    w.fetch = (input: any) => {
      const url = String(typeof input === 'string' ? input : input?.url ?? '');
      const p = url.split('?')[0];

      if (p === '/api/runtime-config') {
        return json({
          apiUrl: 'https://api.example.test', liveModeEnabled: true, apiTimeoutMs: 15000,
          configured: true, diagnostic: null,
          source: { apiUrl: 'API_URL', liveModeEnabled: 'LIVE_MODE_ENABLED', apiTimeoutMs: 'default' },
        });
      }
      if (p === '/api/auth/csrf') return json({ csrfToken: 'fixture-csrf' });
      if (p === '/api/auth/me') {
        return json({
          user: {
            id: 'fixture-user', email: 'analyst@acme.test', full_name: 'Acme Analyst',
            current_workspace_id: 'fixture-ws',
            current_workspace: { id: 'fixture-ws', name: 'Acme Capital', slug: 'acme' },
            memberships: [{ workspace: { id: 'fixture-ws', name: 'Acme Capital' }, role: 'admin' }],
          },
        });
      }

      // Every collection endpoint: present, empty, and honest about it.
      return json({
        items: [], results: [], data: [], total: 0, limit: 50, offset: 0,
        alerts: [], incidents: [], assets: [], targets: [], detections: [],
        telemetry: [], anomalies: [], actions: [], packages: [], providers: [],
        integrations: [], monitored_systems: [], runs: [], events: [],
        degraded: false,
      });
    };
  }, pathname);
}

for (const screen of SCREENS) {
  test.describe(screen.name, () => {
    let harness: Harness;
    test.beforeAll(async () => { harness = await startRenderHarness({ bootstrap: bootstrapFor(screen) }); });
    test.afterAll(async () => { await harness?.close(); });

    test(`renders its empty state and stays readable in both themes`, async ({ page }) => {
      await installEmptyBackend(page, screen.pathname);
      await page.goto(harness.url);

      // Something must render: a screen that mounts to nothing would sweep
      // clean and prove nothing at all.
      await page.waitForFunction(
        () => (document.getElementById('root')?.textContent ?? '').trim().length > 0,
        null,
        { timeout: 15_000 },
      );
      expect(await page.evaluate(() => (window as any).__renderError), `${screen.name} threw on mount`).toBeNull();

      await expectReadableInBothThemes(page, screen.name);
    });
  });
}
