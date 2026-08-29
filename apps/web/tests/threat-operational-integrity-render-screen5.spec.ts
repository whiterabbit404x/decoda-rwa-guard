/**
 * Screen 5 — Operational Integrity: RENDERED, in a real browser.
 *
 * The sibling spec (threat-operational-integrity-screen5.spec.ts) covers the
 * presentation logic and pins the component wiring with source assertions.
 * Neither can prove the thing a customer actually depends on: that the real
 * React tree renders, that clicking a detection row opens the two panels, and
 * that a backend PASS / FAIL / UNKNOWN reaches the DOM as three visibly
 * different things.
 *
 * So this spec mounts the UNMODIFIED ThreatMonitoringScreen — through the real
 * PilotAuthProvider and RuntimeSummaryProvider, with the real ui-primitives,
 * the real presentation module and the real detail panels — in Chromium, and
 * serves it a KNOWN API payload.
 *
 * That payload is not invented here. It is
 * services/api/tests/fixtures/operational_integrity_demo.json, produced by
 * running the actual deterministic engine over seeded telemetry in a disposable
 * database and serializing the stored rows through the actual Screen 5
 * endpoint serializers. test_operational_integrity_fixture.py re-derives it from
 * the matcher on every backend run, so the DOM asserted below is the DOM a real
 * detection produces — and the fixture cannot drift from the engine without a
 * backend test failing first.
 *
 * The fixture is TEST DATA. It reaches the browser only through the stubbed
 * fetch installed inside this spec; no production code path can read it.
 */
import { expect, test, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import { metricCount } from '../app/threat-monitoring/presentation';
import { resolveChromium, startRenderHarness, type Harness } from './support/render-harness';

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

/**
 * The toolbar filters are the app's own ARIA combobox (a trigger button plus a
 * portalled listbox), not native <select> elements — so drive them the way a
 * user does rather than through selectOption.
 */
async function chooseFilter(page: Page, ariaLabel: string, optionLabel: string) {
  await page.getByRole('combobox', { name: ariaLabel }).click();
  await page.getByRole('option', { name: optionLabel, exact: true }).click();
}

async function filterOptions(page: Page, ariaLabel: string): Promise<string[]> {
  await page.getByRole('combobox', { name: ariaLabel }).click();
  const labels = await page.getByRole('option').allInnerTexts();
  await page.keyboard.press('Escape');
  return labels.map((l) => l.replace('\u2713', '').trim());
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

/* ── 4. The detection row renders the reference columns ──────────────────── */
test('an operational integrity detection renders as a row with its business amounts', async ({ page }) => {
  await mountScreen(page, harness);

  const row = page.getByTestId(`detection-row-${UNMATCHED_ID}`);
  await expect(row).toBeVisible();
  await expect(row).toHaveAttribute('data-category', 'OPERATIONAL_INTEGRITY');

  // Severity | Detection | Asset | Observed | Expected — read off the rendered cells.
  const cells = await row.locator('td').allInnerTexts();
  expect(cells[0]).toContain('Critical');
  expect(cells[1]).toContain('Unmatched Issuance');
  expect(cells[2]).toContain('US Treasury Bond #013');
  expect(cells[3]).toBe('+5,000,000 USTB');
  expect(cells[4]).toBe('0 USTB');

  // Status, confidence and first-seen come from the same stored record. The
  // shared table formatter rounds confidence to whole percent (0.991 -> "99%"),
  // so this asserts what the column actually renders rather than the raw value.
  await expect(row).toContainText('Open');
  await expect(row).toContainText('99% (High)');
  await expect(row).toContainText('ago');

  // The reason code is shown under the detection name — the deterministic
  // verdict, not a restatement of the category.
  await expect(row).toContainText('No matching authorized issuance');
});

test('the observed and expected columns are headed as such', async ({ page }) => {
  await mountScreen(page, harness);
  // The table styles headers upper-case, so compare case-insensitively.
  const headers = (await page.locator('table thead th').allInnerTexts()).map((h) => h.toLowerCase());
  expect(headers).toEqual(
    ['severity', 'detection', 'asset', 'observed', 'expected', 'status', 'confidence', 'first seen', 'action'],
  );
});

test('a base-unit amount is never rendered through a float', async ({ page }) => {
  // 39 digits — a JS number would silently round this.
  const huge = { ...unmatched, id: 'fixture0-0000-0000-0000-000000000009', observed_amount: '123456789012345678901234567890123456789' };
  await mountScreen(page, harness, { detections: [huge] });
  await expect(page.getByTestId('detection-row-fixture0-0000-0000-0000-000000000009'))
    .toContainText('+123,456,789,012,345,678,901,234,567,890,123,456,789');
});

/* ── 5. Row selection opens both panels ──────────────────────────────────── */
test('clicking the row renders Detection Details and Operational Integrity Analysis', async ({ page }) => {
  await mountScreen(page, harness);
  await expect(page.getByTestId('detection-detail-panels')).toHaveCount(0);

  await page.getByTestId(`detection-row-${UNMATCHED_ID}`).click();

  const panels = page.getByTestId('detection-detail-panels');
  await expect(panels).toBeVisible();
  await expect(panels.getByText('Detection Details')).toBeVisible();
  await expect(page.getByTestId('operational-integrity-analysis')).toBeVisible();

  // The left panel's reference fields, rendered from the stored record.
  const details = page.getByTestId('detection-details-fields');
  await expect(details).toContainText('Unmatched Issuance');
  await expect(details).toContainText('US Treasury Bond #013');
  await expect(details).toContainText('MINT');
  await expect(details).toContainText('+5,000,000');   // Observed
  await expect(details).toContainText('5,000,000');    // Variance
  await expect(details).toContainText('RPC polling');  // telemetry source
  await expect(details).toContainText('Finalized block');
  await expect(details).toContainText('0x7a71dccc'); // transaction hash (shortened, not dropped)
  await expect(details).toContainText('Expected Amount');
  await expect(details).toContainText('Variance');
});

test('selecting a row is a read — it never POSTs', async ({ page }) => {
  await mountScreen(page, harness);
  await page.getByTestId(`detection-row-${UNMATCHED_ID}`).click();
  await expect(page.getByTestId('detection-detail-panels')).toBeVisible();

  const requests = await page.evaluate(() => (window as any).__requests);
  const detailRequests = requests.filter((r: any) => r.url.includes(`/detections/${UNMATCHED_ID}`));
  expect(detailRequests.length).toBeGreaterThan(0);
  expect(detailRequests.every((r: any) => r.method === 'GET')).toBe(true);
});

test('clicking the selected row again closes the panels', async ({ page }) => {
  await mountScreen(page, harness);
  const row = page.getByTestId(`detection-row-${UNMATCHED_ID}`);
  await row.click();
  await expect(page.getByTestId('detection-detail-panels')).toBeVisible();
  await row.click();
  await expect(page.getByTestId('detection-detail-panels')).toHaveCount(0);
});

/* ── 5/6. PASS, FAIL and UNKNOWN are three different things on screen ─────── */
test('the deterministic checks render with the status the backend decided', async ({ page }) => {
  await mountScreen(page, harness);
  await page.getByTestId(`detection-row-${UNMATCHED_ID}`).click();
  await expect(page.getByTestId('operational-checks')).toBeVisible();

  const expected = unmatched.operational_checks;
  for (const key of ['on_chain_event', 'transfer_agent_match', 'settlement_match', 'signer_validity']) {
    const check = page.getByTestId(`operational-check-${key}`);
    await expect(check).toHaveAttribute('data-status', expected[key].status);
    await expect(check).toContainText(expected[key].reason);
  }

  // The screen's whole argument, visible in one panel: the chain accepted it,
  // the business did not authorize it.
  await expect(page.getByTestId('operational-check-signer_validity')).toContainText('Cryptographically valid');
  await expect(page.getByTestId('operational-check-transfer_agent_match')).toContainText('No authorized issuance');
});

test('the conclusion is the backend verdict, styled as critical', async ({ page }) => {
  await mountScreen(page, harness);
  await page.getByTestId(`detection-row-${UNMATCHED_ID}`).click();

  const conclusion = page.getByTestId('operational-conclusion');
  await expect(conclusion).toHaveAttribute('data-conclusion', 'CRITICAL_OPERATIONAL_ANOMALY');
  await expect(conclusion).toHaveText('CRITICAL OPERATIONAL ANOMALY');
  await expect(page.getByTestId('operational-integrity-analysis')).toContainText('No matching authorized issuance');
  await expect(page.getByTestId('operational-integrity-analysis')).toContainText('op-integrity-v1');
});

test('an UNKNOWN check renders as not-evaluated, visibly distinct from a FAIL', async ({ page }) => {
  await mountScreen(page, harness);
  await page.getByTestId(`detection-row-${SETTLEMENT_ID}`).click();

  const unknown = page.getByTestId('operational-check-signer_validity');
  await expect(unknown).toHaveAttribute('data-status', 'UNKNOWN');
  // A check that could not run says so — it is never worded as a violation.
  await expect(unknown).toContainText('Not applicable');
  await expect(unknown).toContainText('Not evaluated');   // screen-reader status
  await expect(unknown).not.toContainText('No authorized issuance');

  const failed = page.getByTestId('operational-check-settlement_match');
  await expect(failed).toHaveAttribute('data-status', 'FAIL');

  // Three states, three glyphs and three colours — never collapsed into two.
  const styles = await page.evaluate(() =>
    ['on_chain_event', 'settlement_match', 'signer_validity'].map((key) => {
      const el = document.querySelector(`[data-testid="operational-check-${key}"] span[aria-hidden="true"]`) as HTMLElement;
      return { glyph: el.textContent, color: getComputedStyle(el).color };
    }),
  );
  expect(styles.map((s) => s.glyph)).toEqual(['✓', '✕', '?']);
  expect(new Set(styles.map((s) => s.color)).size).toBe(3);
});

test('a settlement timeout does not claim to be a critical anomaly', async ({ page }) => {
  await mountScreen(page, harness);
  await page.getByTestId(`detection-row-${SETTLEMENT_ID}`).click();
  await expect(page.getByTestId('operational-conclusion')).toHaveAttribute('data-conclusion', 'OPERATIONAL_ANOMALY');
  await expect(page.getByTestId('operational-conclusion')).toHaveText('OPERATIONAL ANOMALY');
});

test('the AI narrative is labelled as explanation only', async ({ page }) => {
  await mountScreen(page, harness);
  await page.getByTestId(`detection-row-${UNMATCHED_ID}`).click();
  await expect(page.getByTestId('ai-authority-label')).toContainText('Explanation only');
});

/* ── 1. The Category filter narrows real backend records ─────────────────── */
test('choosing Operational Integrity sends the category to the API', async ({ page }) => {
  await mountScreen(page, harness);
  await chooseFilter(page, 'Filter by detection category', 'Operational Integrity');

  await expect
    .poll(async () => {
      const requests = await page.evaluate(() => (window as any).__requests);
      return requests.some((r: any) => r.url.includes('/api/threat-monitoring/detections?') && r.url.includes('category=OPERATIONAL_INTEGRITY'));
    })
    .toBe(true);
});

test('the category options come from the backend summary', async ({ page }) => {
  await mountScreen(page, harness);
  expect(await filterOptions(page, 'Filter by detection category')).toEqual(
    ['All categories', 'Cyber Security', 'Operational Integrity'],
  );
});

/* ── 6/8. Nothing found is never rendered as nothing wrong ───────────────── */
test('zero detections under Operational Integrity states the period, not an all-clear', async ({ page }) => {
  await mountScreen(page, harness, { detections: [] });
  await chooseFilter(page, 'Filter by detection category', 'Operational Integrity');

  // Changing the category refetches, so wait for the SETTLED empty state rather
  // than snapshotting a panel that may still read "Loading detections…".
  const panel = page.locator('[role="tabpanel"]');
  await expect(panel).toContainText('No operational integrity detections in the selected period.');
  await expect(page.getByRole('heading', { name: 'No operational integrity detections' })).toBeVisible();

  // Zero findings is stated as a period with nothing in it, never as an all-clear.
  const body = (await panel.innerText()).toLowerCase();
  for (const reassurance of ['healthy', 'all clear', 'no issues', 'secure', 'safe', 'protected']) {
    expect(body).not.toContain(reassurance);
  }
});

test('a workspace with no authoritative source says absence is not evidence', async ({ page }) => {
  const degraded = summaryPayload();
  (degraded.summary as any).operational_integrity.coverage = {
    state: 'UNAVAILABLE', telemetry_source: null, telemetry_stage: 'UNKNOWN',
    last_issuance_telemetry_at: null, authoritative_sources: 0, authorized_records: 0,
    preconfirmation_available: false, reasons: ['no_authoritative_source'],
  };
  await mountScreen(page, harness, { detections: [], summary: degraded });
  await chooseFilter(page, 'Filter by detection category', 'Operational Integrity');

  const notice = page.getByTestId('operational-coverage-notice');
  await expect(notice).toBeVisible();
  await expect(notice).toHaveAttribute('data-tone', 'warning');
  await expect(notice).toContainText('no authoritative business source is configured');
  await expect(notice).toContainText('Absence of detections is not evidence that issuance is authorized.');
});

/* ── 7. No hardcoded latency or preconfirmation claim ────────────────────── */
test('the rendered screen claims no preconfirmation the ingestion path did not deliver', async ({ page }) => {
  await mountScreen(page, harness);
  await page.getByTestId(`detection-row-${UNMATCHED_ID}`).click();
  // Wait for the panel CONTENT, not just its frame — the fields arrive from a
  // second request, and reading the page before they land would prove nothing.
  await expect(page.getByTestId('detection-details-fields')).toBeVisible();
  await expect(page.getByTestId('operational-checks')).toBeVisible();

  const body = (await page.locator('body').innerText()).toLowerCase();
  for (const claim of ['flashblock', 'preconfirmed', 'preconfirmation', '200 ms', '380 ms']) {
    expect(body).not.toContain(claim);
  }
  // What it says instead is what actually delivered the event.
  expect(body).toContain('rpc polling');
  expect(body).toContain('finalized block');
});

/* ── 7. A count the backend did not send is not a value ──────────────────── */
test('metricCount renders a missing count as unavailable, never as text or zero', () => {
  expect(metricCount(0)).toBe('0');
  expect(metricCount(42)).toBe('42');
  for (const missing of [undefined, null, Number.NaN, Number.POSITIVE_INFINITY]) {
    expect(metricCount(missing as number)).toBe('\u2014');
  }
});

test('a summary missing its counts shows em dashes, not the word "undefined"', async ({ page }) => {
  // The backend always sends these counts today; this pins the fail-closed
  // behaviour so a partial payload can never render "undefined" as a KPI value.
  const partial = summaryPayload();
  delete (partial.summary as any).telemetry_events_count;
  delete (partial.summary as any).detection_count;
  delete (partial.summary as any).anomaly_count;

  await mountScreen(page, harness, { summary: partial });
  const kpis = page.getByTestId('kpi-row');
  await expect(kpis).toBeVisible();
  expect(await kpis.innerText()).not.toContain('undefined');
  for (const metric of ['telemetry', 'detections', 'anomalies']) {
    await expect(page.getByTestId(`kpi-${metric}`).locator('.metricValue')).toHaveText('\u2014');
  }
});

/* ── 10. The existing screen is intact ───────────────────────────────────── */
test('the four existing tabs are unchanged', async ({ page }) => {
  await mountScreen(page, harness);
  const tabs = await page.locator('[role="tab"]').allInnerTexts();
  expect(tabs).toEqual(['Overview', 'Telemetry', 'Detections', 'Anomalies']);
});

test('the existing severity, type and status filters still work alongside Category', async ({ page }) => {
  await mountScreen(page, harness);
  for (const label of [
    'Filter by detection category', 'Filter by severity',
    'Filter by detection type', 'Filter by investigation status',
  ]) {
    await expect(page.getByRole('combobox', { name: label })).toBeVisible();
  }
  await expect(page.getByTestId('detection-search')).toBeVisible();
  await chooseFilter(page, 'Filter by severity', 'Critical');
  await expect
    .poll(async () => {
      const requests = await page.evaluate(() => (window as any).__requests);
      return requests.some((r: any) => r.url.includes('severity=critical'));
    })
    .toBe(true);
});

test('a cyber-security detection keeps its own lane and gets no operational analysis', async ({ page }) => {
  const cyber = {
    ...unmatched,
    id: 'fixture0-0000-0000-0000-000000000010',
    category: 'CYBER_SECURITY',
    detection_type: 'unusual_transfer',
    detection_type_label: 'Unusual Transfer',
    deterministic_reason_code: null,
    operational_checks: {},
    observed_amount: null,
    expected_amount: null,
  };
  await page.addInitScript(([id, row]) => {
    (window as any).__extraDetail = { [id as string]: { detection: row, evidence: [] } };
  }, ['fixture0-0000-0000-0000-000000000010', cyber] as any);

  await mountScreen(page, harness, { detections: [cyber, ...FIXTURE.detections] });
  const row = page.getByTestId('detection-row-fixture0-0000-0000-0000-000000000010');
  await expect(row).toHaveAttribute('data-category', 'CYBER_SECURITY');
  await expect(row).toContainText('Unusual Transfer');
  await expect(row).toContainText('Cyber Security');
  // Its Observed/Expected cells are blank rather than a fabricated zero.
  const cells = await row.locator('td').allInnerTexts();
  expect(cells[3]).toBe('\u2014');
  expect(cells[4]).toBe('\u2014');
});
