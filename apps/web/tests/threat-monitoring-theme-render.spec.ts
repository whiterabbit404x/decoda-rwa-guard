/**
 * Whole-screen theme readability — Threat Monitoring, rendered.
 *
 * The shell spec proves the chrome and the primitives. This proves a real
 * SCREEN: the unmodified ThreatMonitoringScreen, mounted against the same
 * operational-integrity fixture the Screen 5 spec uses, swept for WCAG AA on
 * every rendered text node in BOTH themes.
 *
 * Sweeping rather than listing selectors is the point. A hand-written list
 * only finds the elements someone predicted; this finds the severity chip, the
 * stale-telemetry notice or the filter label that a token flip strands,
 * without anyone having guessed it would be that one.
 */
import { expect, test, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import { resolveChromium, startRenderHarness, type Harness } from './support/render-harness';
import { expectReadableInBothThemes } from './support/contrast-sweep';

const chromiumExecutable = resolveChromium();
if (chromiumExecutable) test.use({ launchOptions: { executablePath: chromiumExecutable } });

const FIXTURE = JSON.parse(
  fs.readFileSync(
    path.join(__dirname, '..', '..', '..', 'services', 'api', 'tests', 'fixtures', 'operational_integrity_demo.json'),
    'utf-8',
  ),
);

const UNMATCHED_ID = 'fixture0-0000-0000-0000-000000000001';
const SETTLEMENT_ID = 'fixture0-0000-0000-0000-000000000002';

const unmatched = FIXTURE.detections.find((d: any) => d.id === UNMATCHED_ID);
const settlement = FIXTURE.detections.find((d: any) => d.id === SETTLEMENT_ID);

/** The Screen 5 summary payload, with a LIVE operational-integrity coverage block. */
function summaryPayload(overrides: Record<string, unknown> = {}) {
  return {
    summary: {
      window: '24h',
      data_freshness: 'fresh',
      degraded_reasons: [],
      next_action: 'diagnose_ingestion',
      detections_by_type: [],
      detection_categories: [
        { value: 'CYBER_SECURITY', label: 'Cyber Security' },
        { value: 'OPERATIONAL_INTEGRITY', label: 'Operational Integrity' },
      ],
      operational_integrity: {
        category: 'OPERATIONAL_INTEGRITY',
        label: 'Operational Integrity',
        detection_count: 2,
        matcher_version: 'op-integrity-v1',
        by_type: [
          { type: 'unmatched_issuance', label: 'Unmatched Issuance', count: 1, supported: true, unsupported_reason: null },
          { type: 'settlement_timeout', label: 'Settlement Timeout', count: 1, supported: true, unsupported_reason: null },
          {
            type: 'nav_valuation_drift', label: 'NAV / Valuation Drift', count: 0, supported: false,
            unsupported_reason: 'Requires an authoritative NAV / valuation feed for the asset. No such source is collected, so valuation drift is not evaluated.',
          },
        ],
        coverage: {
          state: 'LIVE',
          telemetry_source: 'rpc_polling',
          telemetry_stage: 'FINALIZED',
          last_issuance_telemetry_at: '2026-08-29T11:55:00+00:00',
          authoritative_sources: 1,
          authorized_records: 1,
          preconfirmation_available: false,
          reasons: [],
        },
      },
      ...overrides,
    },
  };
}

/**
 * Mount the real screen with a routed fetch stub.
 *
 * `detections` is what /threat-monitoring/detections returns; everything else
 * is the minimum an authenticated session needs. The stub records every request
 * on `window.__requests` so a spec can assert what the UI actually asked the
 * backend for.
 */
async function mountScreen(
  page: Page,
  harness: Harness,
  options: { detections?: unknown[]; summary?: Record<string, unknown> } = {},
) {
  const detections = options.detections ?? FIXTURE.detections;
  await page.addInitScript(
    ([detectionRows, details, summary]) => {
      const w = window as any;
      w.__requests = [];
      w.localStorage.setItem('decoda.accessToken', 'fixture-token');
      w.__nav = { pushes: [], replaces: [], search: 'tab=detections&window=24h', pathname: '/threat' };

      const json = (body: unknown, status = 200) =>
        Promise.resolve(new Response(JSON.stringify(body), {
          status, headers: { 'content-type': 'application/json' },
        }));

      w.fetch = (input: any, init?: any) => {
        const url = String(typeof input === 'string' ? input : input?.url ?? '');
        w.__requests.push({ url, method: (init?.method ?? 'GET').toUpperCase(), headers: init?.headers ?? {} });
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
              id: 'fixture-user', email: 'operator@example.test', full_name: 'Fixture Operator',
              current_workspace_id: 'fixture-ws',
              current_workspace: { id: 'fixture-ws', name: 'Fixture Workspace', slug: 'fixture' },
            },
          });
        }
        if (p === '/api/ops/monitoring/runtime-status') {
          return json({
            loop_running: true,
            realtime_ingestion: {
              streams_enabled: true, status: 'healthy', healthy: true,
              live_evidence_fresh: true, live_coverage_fresh: true,
              live_security_telemetry_fresh: true, live_evidence_kind: 'coverage',
              lane_state: 'live', reason: null,
            },
          });
        }
        if (p === '/api/threat-monitoring/summary') return json(summary);
        if (p === '/api/threat-monitoring/detections') {
          return json({ detections: detectionRows, total: (detectionRows as unknown[]).length, limit: 50, offset: 0, degraded: false });
        }
        if (p.startsWith('/api/threat-monitoring/detections/')) {
          const id = decodeURIComponent(p.split('/').pop() || '');
          const detail = (details as Record<string, unknown>)[id];
          return detail ? json(detail) : json({ detail: 'Detection not found.' }, 404);
        }
        if (p === '/api/threat-monitoring/telemetry') return json({ telemetry: [], total: 0, limit: 50, offset: 0, degraded: false });
        if (p === '/api/threat-monitoring/anomalies') return json({ anomalies: [], total: 0, limit: 50, offset: 0, degraded: false });
        return json({}, 404);
      };
    },
    [detections, FIXTURE.details, options.summary ?? summaryPayload()] as any,
  );

  await page.goto(harness.url);
  await page.waitForFunction(() => !!document.querySelector('[role="tabpanel"], .statusLine'), null, { timeout: 15_000 });
  // A module/render failure must fail the test, never render as an empty screen.
  expect(await page.evaluate(() => (window as any).__renderError)).toBeNull();
}


const BOOTSTRAP = `
import React from '/vendor/react.js';
import { createRoot } from '/vendor/react-dom-client.js';
import { PilotAuthProvider } from '/app/pilot-auth-context.tsx';
import { RuntimeSummaryProvider } from '/app/runtime-summary-context.tsx';
import ThreatMonitoringScreen from '/app/threat-monitoring/threat-monitoring-screen.tsx';

createRoot(document.getElementById('root')).render(
  React.createElement(PilotAuthProvider, null,
    React.createElement(RuntimeSummaryProvider, null,
      React.createElement(ThreatMonitoringScreen, null))));
`;

let harness: Harness;
test.beforeAll(async () => { harness = await startRenderHarness({ bootstrap: BOOTSTRAP }); });
test.afterAll(async () => { await harness?.close(); });

test('every rendered word on Threat Monitoring clears WCAG AA in both themes', async ({ page }) => {
  await mountScreen(page, harness);
  await expect(page.getByTestId(`detection-row-${UNMATCHED_ID}`)).toBeVisible();
  await expectReadableInBothThemes(page, 'Threat Monitoring');
});

test('the detections table stays readable once a row is opened', async ({ page }) => {
  await mountScreen(page, harness);
  await page.getByTestId(`detection-row-${UNMATCHED_ID}`).click();
  // The detail surface is where a dark-only panel would survive unnoticed:
  // it is behind a click, so a static read of the screen never renders it.
  await page.waitForTimeout(400);
  await expectReadableInBothThemes(page, 'Threat Monitoring (detection open)');
});

test('severity is never carried by colour alone', async ({ page }) => {
  await mountScreen(page, harness);
  const row = page.getByTestId(`detection-row-${UNMATCHED_ID}`);
  // The severity cell spells the level out; an analyst who cannot separate
  // red from amber still reads "Critical".
  await expect(row.locator('td').first()).toContainText(/Critical/i);
});
