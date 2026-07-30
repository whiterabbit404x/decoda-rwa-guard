/**
 * Screen 6 — Active Alerts desktop table layout (UI-only follow-up).
 *
 * Two layers:
 *   1. Real-browser layout tests. A faithful slice of the app shell
 *      (sidebar + content + .alertsSplit + the .alertsTableShell table)
 *      is rendered with the *actual* app CSS injected, then measured at
 *      several viewport widths. These assert the behaviour the follow-up
 *      is about: no horizontal scrollbar at desktop widths, the Cluster
 *      column stays visible, long titles / asset names / cluster ids do
 *      not break the layout, and horizontal scrolling is preserved only
 *      on genuinely narrow (mobile/tablet) widths.
 *   2. Source-level structural tests that pin the fix in place so the
 *      wiring cannot silently regress (mirrors the repo's existing
 *      source-level test convention).
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

/* ── Resolve the Chromium binary present in this environment ──────────────
 * The pinned Playwright build number can differ from the browser build that
 * is pre-installed here, so point the launcher at whichever chromium-<rev>
 * is actually on disk. When nothing matches we leave the default in place
 * (e.g. CI with a matching install). */
function resolveChromium(): string | undefined {
  const base = process.env.PLAYWRIGHT_BROWSERS_PATH || '/opt/pw-browsers';
  try {
    const dir = fs
      .readdirSync(base)
      .find((d) => d.startsWith('chromium-') && fs.existsSync(path.join(base, d, 'chrome-linux', 'chrome')));
    if (dir) return path.join(base, dir, 'chrome-linux', 'chrome');
  } catch {
    /* fall through to the Playwright default */
  }
  return undefined;
}
const chromiumExecutable = resolveChromium();
if (chromiumExecutable) test.use({ launchOptions: { executablePath: chromiumExecutable } });

const APP_CSS = fs.readFileSync(path.join(__dirname, '..', 'app', 'styles.css'), 'utf-8');

/* ── Faithful DOM slice of the Alerts screen ─────────────────────────────── */
type Row = {
  severity: string; sevVariant: string;
  title: string; detection?: string;
  asset: string; txHash?: string;
  status: string; statusVariant: string;
  time: string;
  cluster?: string;
};

// Mirrors alerts-screen.tsx AlertsTableRegion row markup (incl. the cluster
// button's wrap overrides) so the measured layout matches production.
function rowHtml(r: Row): string {
  const detection = r.detection ? `<div class="muted" style="font-size:0.7rem">${r.detection}</div>` : '';
  const tx = r.txHash ? `<div style="font-family:monospace;font-size:0.7rem;color:#94a3b8">${r.txHash}</div>` : '';
  const cluster = r.cluster
    ? `<button type="button" class="btn btn-ghost" style="padding:0.15rem 0.45rem;font-size:0.72rem;white-space:normal;overflow-wrap:anywhere;text-align:left;max-width:100%;height:auto;line-height:1.3">${r.cluster}</button>`
    : `<span class="muted">Unclustered</span>`;
  return `<tr>
    <td><span class="ruleChip sharedStatusPill pill-${r.sevVariant}">${r.severity}</span></td>
    <td>${r.title}${detection}</td>
    <td style="font-size:0.82rem"><span>${r.asset}</span>${tx}</td>
    <td><span class="ruleChip sharedStatusPill pill-${r.statusVariant}">${r.status}</span></td>
    <td style="font-size:0.78rem;white-space:nowrap">${r.time}</td>
    <td style="font-size:0.78rem">${cluster}</td>
  </tr>`;
}

function pageHtml(rows: Row[]): string {
  const body = rows.map(rowHtml).join('');
  return `<!DOCTYPE html><html><head><meta charset="utf-8"><style>${APP_CSS}</style></head>
  <body>
    <div class="appShellFrame">
      <aside class="appSidebar">
        <div class="brandBlock">Decoda</div>
        <nav class="appNav"><a href="#">Dashboard</a><a href="#">Alerts</a><a href="#">Assets</a></nav>
      </aside>
      <div class="appShellContent">
        <div class="appShellTop"><div style="height:56px"></div></div>
        <div class="productShellContent">
          <main class="productPage">
            <div style="max-width:1680px;width:100%">
              <div class="alertsSplit">
                <main style="min-width:0">
                  <div class="tableWrap sharedTableShell tableCompact alertsTableShell">
                    <table>
                      <thead><tr>
                        <th>Severity</th><th>Alert Title</th><th>Asset</th><th>Status</th><th>Time</th><th>Cluster</th>
                      </tr></thead>
                      <tbody>${body}</tbody>
                    </table>
                  </div>
                </main>
                <aside class="dataCard sharedSurfaceCard" style="padding:1.15rem">
                  <p class="eyebrow">Autonomous Agent</p>
                  <h3>AI Alert Triage Agent</h3>
                  <p>Grouped 12 related alerts into 3 clusters.</p>
                </aside>
              </div>
            </div>
          </main>
        </div>
      </div>
    </div>
  </body></html>`;
}

// A realistic active-alert set, including the genuinely long enum labels
// ("Linked to Incident", "Investigating", "Acknowledged") that stress the
// compact Status column.
const REALISTIC_ROWS: Row[] = [
  { severity: 'Critical', sevVariant: 'danger', title: 'Reserve backing ratio dropped below policy threshold', detection: 'Reserve integrity', asset: 'USDC Treasury Reserve Pool', txHash: '0x9a3f…c21b', status: 'Investigating', statusVariant: 'info', time: '3m ago', cluster: 'Reserve drawdown cluster' },
  { severity: 'High', sevVariant: 'danger', title: 'Oracle price deviation exceeded tolerance band', detection: 'Oracle deviation', asset: 'Tokenized T-Bill Fund', txHash: '0x1b2c…9f0a', status: 'Linked to Incident', statusVariant: 'warning', time: '1h ago', cluster: 'Oracle anomaly' },
  { severity: 'Medium', sevVariant: 'warning', title: 'Unusual redemption velocity for settlement contract', asset: 'Money Market Note', status: 'Acknowledged', statusVariant: 'info', time: '6h ago' },
  { severity: 'Low', sevVariant: 'success', title: 'Heartbeat latency briefly elevated', asset: 'Custody Attestation Feed', status: 'Resolved', statusVariant: 'success', time: '2d ago', cluster: 'Latency blip' },
];

// Long / adversarial content. The cluster label is capped at the same 22
// characters the screen renders via truncate(title, 22).
const LONG_TITLE_NO_SPACES = 'Suspicious-large-transfer-detected-on-tokenized-real-world-asset-settlement-contract-without-corresponding-attestation-or-reserve-update-immediate-review-now';
const LONG_TITLE_WORDS = 'Immediate review required: an unusually large redemption was settled against the tokenized treasury reserve pool without a corresponding attestation or reserve update event being observed on chain';
const LONG_ADDRESS = '0xA1b2C3d4E5f6A7b8C9d0E1f2A3b4C5d6E7f8A9b0C1d2E3f4A5b6C7d8E9f0A1b2';
const LONG_CLUSTER_ID = 'clstr-9f8e7d6c5b4a3f21'; // 22 chars, as truncate(…, 22) would emit

/** Core geometry probe shared by the layout assertions. */
async function measure(page: import('@playwright/test').Page) {
  return page.evaluate(() => {
    const de = document.documentElement;
    const wrap = document.querySelector('.tableWrap') as HTMLElement;
    const table = document.querySelector('.alertsTableShell table') as HTMLElement;
    const split = document.querySelector('.alertsSplit') as HTMLElement;
    const main = split.querySelector(':scope > main') as HTMLElement;
    const panel = split.querySelector(':scope > aside') as HTMLElement;
    const headers = Array.from(document.querySelectorAll('.alertsTableShell thead th')) as HTMLElement[];
    const clusterHeader = headers[headers.length - 1];
    const wrapRect = wrap.getBoundingClientRect();
    const mainRect = main.getBoundingClientRect();
    const panelRect = panel.getBoundingClientRect();
    const clusterRect = clusterHeader.getBoundingClientRect();
    return {
      pageOverflow: de.scrollWidth - de.clientWidth,
      wrapOverflow: wrap.scrollWidth - wrap.clientWidth,
      wrapClientWidth: wrap.clientWidth,
      wrapScrollWidth: wrap.scrollWidth,
      headerCount: headers.length,
      clusterVisible: clusterRect.right <= wrapRect.right + 1 && clusterRect.width > 0,
      sideBySide: panelRect.left >= mainRect.right - 2,
      stacked: panelRect.top >= mainRect.bottom - 2,
    };
  });
}

/* ───────────────────────────── 1. Layout (browser) ─────────────────────── */

test('desktop: no horizontal overflow at the side-by-side breakpoint (1360px)', async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 900 });
  await page.setContent(pageHtml(REALISTIC_ROWS));
  const m = await measure(page);
  expect(m.headerCount).toBe(6);
  expect(m.sideBySide).toBe(true);              // table sits beside the AI panel
  expect(m.pageOverflow).toBeLessThanOrEqual(1); // no page-level horizontal scrollbar
  expect(m.wrapOverflow).toBeLessThanOrEqual(1); // the table fits — no inner scrollbar
  expect(m.clusterVisible).toBe(true);           // Cluster column fully visible
});

test('desktop: no horizontal overflow at a common laptop width (1440px)', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.setContent(pageHtml(REALISTIC_ROWS));
  const m = await measure(page);
  expect(m.sideBySide).toBe(true);
  expect(m.pageOverflow).toBeLessThanOrEqual(1);
  expect(m.wrapOverflow).toBeLessThanOrEqual(1);
  expect(m.clusterVisible).toBe(true);
});

test('desktop: table keeps full width and does not overflow when the panel stacks (1280px)', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.setContent(pageHtml(REALISTIC_ROWS));
  const m = await measure(page);
  expect(m.stacked).toBe(true);                  // panel drops below the table
  expect(m.pageOverflow).toBeLessThanOrEqual(1);
  expect(m.wrapOverflow).toBeLessThanOrEqual(1);
  expect(m.clusterVisible).toBe(true);
});

test('long alert titles do not break the desktop layout', async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 900 });
  await page.setContent(pageHtml([
    { severity: 'Critical', sevVariant: 'danger', title: LONG_TITLE_NO_SPACES, detection: 'Anomalous transfer', asset: 'USDC Treasury Reserve Pool', status: 'Investigating', statusVariant: 'info', time: '2m ago', cluster: 'Reserve drawdown cluster' },
    { severity: 'High', sevVariant: 'danger', title: LONG_TITLE_WORDS, asset: 'Tokenized T-Bill Fund', status: 'Linked to Incident', statusVariant: 'warning', time: '9m ago', cluster: 'Oracle anomaly' },
    ...REALISTIC_ROWS,
  ]));
  const m = await measure(page);
  expect(m.pageOverflow).toBeLessThanOrEqual(1);
  expect(m.wrapOverflow).toBeLessThanOrEqual(1);
  expect(m.clusterVisible).toBe(true);
});

test('long asset names / addresses do not break the desktop layout', async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 900 });
  await page.setContent(pageHtml([
    { severity: 'Medium', sevVariant: 'warning', title: 'Unknown counterparty interacted with the settlement contract', asset: LONG_ADDRESS, txHash: '0x1b2c…9f0a', status: 'Acknowledged', statusVariant: 'info', time: '4h ago', cluster: 'Counterparty review' },
    ...REALISTIC_ROWS,
  ]));
  const m = await measure(page);
  expect(m.pageOverflow).toBeLessThanOrEqual(1);
  expect(m.wrapOverflow).toBeLessThanOrEqual(1);
  expect(m.clusterVisible).toBe(true);
});

test('long cluster identifiers do not break the desktop layout', async ({ page }) => {
  await page.setViewportSize({ width: 1360, height: 900 });
  await page.setContent(pageHtml([
    { severity: 'Critical', sevVariant: 'danger', title: 'Reserve backing ratio dropped below policy threshold', asset: 'USDC Treasury Reserve Pool', status: 'Investigating', statusVariant: 'info', time: '1m ago', cluster: LONG_CLUSTER_ID },
    ...REALISTIC_ROWS,
  ]));
  const m = await measure(page);
  expect(m.pageOverflow).toBeLessThanOrEqual(1);
  expect(m.wrapOverflow).toBeLessThanOrEqual(1);
  expect(m.clusterVisible).toBe(true);
});

test('narrow screens preserve horizontal scrolling for the table only', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 800 });
  await page.setContent(pageHtml([
    { severity: 'Critical', sevVariant: 'danger', title: LONG_TITLE_WORDS, asset: LONG_ADDRESS, status: 'Linked to Incident', statusVariant: 'warning', time: '3m ago', cluster: LONG_CLUSTER_ID },
    ...REALISTIC_ROWS,
  ]));
  const m = await measure(page);
  // The page itself must not overflow — the scroll is contained in the table.
  expect(m.pageOverflow).toBeLessThanOrEqual(1);
  // The table region keeps a readable min-width, so it scrolls horizontally.
  expect(m.wrapScrollWidth).toBeGreaterThan(m.wrapClientWidth + 1);
});

/* ───────────────────────── 2. Source-level wiring guards ───────────────── */

function read(rel: string): string {
  return fs.readFileSync(path.join(__dirname, '..', rel), 'utf-8');
}

test('styles.css defines the responsive split and the fixed-layout alerts table', () => {
  const css = read('app/styles.css');
  expect(css).toContain('.alertsSplit');
  // Stacks by default; side-by-side only above the derived desktop breakpoint.
  expect(css).toContain('@media (min-width: 1360px)');
  expect(css).toContain('grid-template-columns: minmax(0, 3fr) minmax(300px, 1fr)');
  // Fixed table sizing with a min-width floor that preserves narrow scrolling.
  expect(css).toMatch(/\.alertsTableShell table\s*\{[^}]*table-layout:\s*fixed/);
  expect(css).toMatch(/\.alertsTableShell table\s*\{[^}]*min-width:\s*720px/);
  // Long content wraps instead of forcing the table wider.
  expect(css).toContain('overflow-wrap: anywhere');
});

test('alerts-screen wires the responsive split + fixed-layout table and wraps free text', () => {
  const screen = read('app/alerts-screen.tsx');
  expect(screen).toContain('className="alertsSplit"');
  expect(screen).toContain('className="alertsTableShell"');
  // The old fixed-width nowrap/ellipsis title clamp is gone (title now wraps).
  expect(screen).not.toContain("maxWidth: '320px'");
  expect(screen).not.toContain('textOverflow');
  // The cluster button opts out of the .btn nowrap so long ids wrap in-cell.
  expect(screen).toContain("whiteSpace: 'normal'");
  // Columns are unchanged (operational fields preserved, none hidden).
  expect(screen).toContain("['Severity', 'Alert Title', 'Asset', 'Status', 'Time', 'Cluster']");
});

test('TableShell accepts an optional className for opt-in table sizing', () => {
  const ui = read('app/components/ui-primitives.tsx');
  expect(ui).toContain('className = \'\'');
  expect(ui).toContain('export function TableShell');
});
