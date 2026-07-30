/**
 * Screen 7 — Incident forensic investigation layout (UI-only follow-up).
 *
 * The Screen 7 investigation logic is unchanged; this pins the *responsive
 * presentation* of the Digital Forensics Investigator so it can no longer be
 * crushed into four equally narrow columns beside the app sidebar.
 *
 * Two layers (mirrors the Screen 6 layout convention):
 *   1. Real-browser layout tests. A faithful slice of the app shell
 *      (sidebar + content + the .forensicLayout / .forensicMain /
 *      .forensicMainTop grid + the .forensicEvidenceTable) is rendered with
 *      the *actual* app CSS injected, then measured at several viewport
 *      widths. These assert the behaviour the follow-up is about: a readable
 *      two-column layout at standard desktop widths, an agent panel with a
 *      useful minimum width, no page-level horizontal overflow from the
 *      evidence table, at most three practical columns on large screens, and
 *      a single stacked column on mobile.
 *   2. Source-level structural tests that pin the fix in place so the wiring
 *      cannot silently regress.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

/* ── Resolve the Chromium binary present in this environment ──────────────
 * The pinned Playwright build number can differ from the browser build that
 * is pre-installed here, so point the launcher at whichever chromium-<rev>
 * is actually on disk. When nothing matches we leave the default in place. */
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

/* ── Faithful DOM slice of the Screen 7 forensic investigator ─────────────
 * Mirrors forensic-investigator-panel.tsx: the disclaimer + HeaderCard, then
 * the .forensicLayout with a .forensicMain column (.forensicMainTop holding
 * the AI Summary + Workflow cards, then the Evidence table at full width) and
 * the Investigator agent panel as the second child (its right column on
 * desktop). Long / adversarial identifiers are included to stress wrapping. */
type EvidenceRow = {
  type: string;
  title: string;
  source: string;
  sourceVariant: string;
  txHash?: string;
  block?: string;
  observed: string;
  state: string;
  stateVariant: string;
};

function copyId(value: string): string {
  return `<button type="button" title="${value}" style="font-family:monospace;font-size:0.78rem;background:rgba(148,163,184,0.1);border:1px solid rgba(148,163,184,0.2);border-radius:6px;padding:0.15rem 0.45rem;color:#8b949e;cursor:pointer;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:inline-block">${value}</button>`;
}

function evidenceRowHtml(r: EvidenceRow): string {
  const tx = r.txHash
    ? `<div style="display:flex;flex-direction:column;gap:0.2rem">${copyId(r.txHash)}${r.block ? `<span class="muted" style="font-size:0.75rem">Block ${r.block}</span>` : ''}</div>`
    : `<span class="muted" style="font-size:0.8rem">—</span>`;
  return `<tr>
    <td style="font-size:0.82rem">${r.type}</td>
    <td style="font-size:0.82rem" title="${r.title}">${r.title}</td>
    <td><span class="ruleChip sharedStatusPill pill-${r.sourceVariant}">${r.source}</span></td>
    <td>${tx}</td>
    <td style="font-size:0.8rem">${r.observed}</td>
    <td><span class="ruleChip sharedStatusPill pill-${r.stateVariant}">${r.state}</span></td>
  </tr>`;
}

function card(inner: string): string {
  return `<section class="dataCard sharedSurfaceCard" style="padding:1.15rem;min-width:0">${inner}</section>`;
}

function pageHtml(rows: EvidenceRow[]): string {
  const evidenceBody = rows.map(evidenceRowHtml).join('');

  const aiCard = card(`
    <p class="sectionEyebrow" style="margin:0">Digital Forensics Investigator</p>
    <h3 style="margin:0.15rem 0 0;font-size:1rem">AI Investigation Summary</h3>
    <p style="font-size:0.85rem;color:#8b949e">Deterministic analysis derived from the immutable evidence snapshot.</p>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:0.5rem 1rem">
      <div><p class="tableMeta" style="font-size:0.75rem">Detection time</p><div style="font-size:0.85rem">7/30/2026, 09:14:22</div></div>
      <div><p class="tableMeta" style="font-size:0.75rem">Impacted asset</p><div style="font-size:0.85rem">${copyId('tgt-9f8e7d6c5b4a')}</div></div>
      <div><p class="tableMeta" style="font-size:0.75rem">Confidence</p><div style="font-size:0.85rem">High 82%</div></div>
      <div><p class="tableMeta" style="font-size:0.75rem">Evidence coverage</p><div style="font-size:0.85rem">67%</div></div>
    </div>`);

  const workflowCard = card(`
    <p class="sectionEyebrow" style="margin:0">Workflow</p>
    <h3 style="margin:0.15rem 0 0;font-size:1rem">Investigation Workflow</h3>
    <ol style="list-style:none;margin:0.5rem 0 0;padding:0;display:flex;flex-direction:column;gap:0.5rem">
      <li style="font-size:0.83rem">1. Snapshot evidence</li>
      <li style="font-size:0.83rem">2. Correlate telemetry</li>
      <li style="font-size:0.83rem">3. Match rules</li>
      <li style="font-size:0.83rem">4. Assemble findings</li>
    </ol>`);

  const evidenceCard = card(`
    <p class="sectionEyebrow" style="margin:0">Evidence</p>
    <h3 style="margin:0.15rem 0 0;font-size:1rem">Evidence — Corroborated</h3>
    <div style="overflow-x:auto">
      <div class="tableWrap sharedTableShell tableCompact forensicEvidenceTable">
        <table>
          <thead><tr>
            <th>Type</th><th>Title</th><th>Source</th><th>Tx / Block</th><th>Observed</th><th>State</th>
          </tr></thead>
          <tbody>${evidenceBody}</tbody>
        </table>
      </div>
    </div>`);

  const agentCard = card(`
    <p class="sectionEyebrow" style="margin:0">Agent</p>
    <h3 style="margin:0.15rem 0 0;font-size:1rem">Digital Forensics Investigator</h3>
    <p class="sectionEyebrow" style="margin-top:0.5rem">Top verified findings</p>
    <p style="font-size:0.82rem;color:#8b949e">Reserve backing ratio dropped below the configured policy threshold, corroborated by two independent telemetry observations.</p>
    <p style="font-size:0.82rem;color:#8b949e">Redemption velocity exceeded the rolling tolerance band for the settlement contract.</p>
    <p class="sectionEyebrow" style="margin-top:0.5rem">Potential rule matches</p>
    <p style="font-size:0.82rem;color:#8b949e">Reserve integrity rule matched against the corroborated telemetry evidence set.</p>
    <div style="display:flex;gap:1rem;margin:0.75rem 0;flex-wrap:wrap">
      <div style="min-width:120px"><p style="font-size:0.85rem;color:#8b949e">Coverage</p><p style="font-size:2rem;font-weight:700">67%</p></div>
      <div style="min-width:120px"><p style="font-size:0.85rem;color:#8b949e">Confidence</p><p style="font-size:2rem;font-weight:700">82%</p></div>
    </div>
    <div style="display:flex;gap:0.5rem;flex-wrap:wrap">
      <button type="button" class="btn btn-primary">Generate Report</button>
      <button type="button" class="btn btn-secondary">Re-run Investigation</button>
    </div>`);

  const headerCard = `<section class="dataCard sharedSurfaceCard" style="padding:1.25rem;background:rgba(59,130,246,0.06)">
    <div style="display:flex;align-items:center;gap:0.5rem;flex-wrap:wrap">
      <span style="font-family:monospace;font-weight:700;font-size:0.9rem">INC-2026-042</span>
      <span class="ruleChip sharedStatusPill pill-danger">critical</span>
      <span class="ruleChip sharedStatusPill pill-info">investigating</span>
    </div>
    <h2 style="margin:0.45rem 0 0;font-size:1.1rem">Reserve backing ratio dropped below policy threshold</h2>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:0.9rem 1.25rem;margin-top:1.1rem">
      <div><p class="tableMeta" style="font-size:0.75rem">Detection time</p><div style="font-size:0.85rem">7/30/2026, 09:14:22</div></div>
      <div><p class="tableMeta" style="font-size:0.75rem">Last updated</p><div style="font-size:0.85rem">3m ago</div></div>
      <div><p class="tableMeta" style="font-size:0.75rem">Impacted asset</p><div style="font-size:0.85rem">${copyId('tgt-9f8e7d6c5b4a')}</div></div>
      <div><p class="tableMeta" style="font-size:0.75rem">Source alerts</p><div style="font-size:0.85rem">3</div></div>
      <div><p class="tableMeta" style="font-size:0.75rem">Risk score</p><div style="font-size:0.85rem">78 / 100</div></div>
      <div><p class="tableMeta" style="font-size:0.75rem">Confidence</p><div style="font-size:0.85rem">High 82%</div></div>
    </div>
  </section>`;

  return `<!DOCTYPE html><html><head><meta charset="utf-8"><style>${APP_CSS}</style></head>
  <body>
    <div class="appShellFrame">
      <aside class="appSidebar">
        <div class="brandBlock">Decoda</div>
        <nav class="appNav"><a href="#">Dashboard</a><a href="#">Alerts</a><a href="#">Incidents</a></nav>
      </aside>
      <div class="appShellContent">
        <header class="appShellTop"><div style="height:56px"></div><div class="runtimeBanner runtimeBannerStale"><span class="runtimeBannerField"><span class="runtimeBannerLabel">Monitoring</span><span class="runtimeBannerValue">Limited coverage</span></span></div></header>
        <main class="appShellPage">
          <main class="productPage">
            <div class="forensicInvestigator" style="display:flex;flex-direction:column;gap:1rem">
              <p class="statusLine" style="margin:0;font-size:0.82rem"><strong>Evidence-grounded forensic analysis.</strong> No fund-moving action is executed here.</p>
              ${headerCard}
              <div class="forensicLayout">
                <div class="forensicMain">
                  <div class="forensicMainTop">
                    ${aiCard}
                    ${workflowCard}
                  </div>
                  ${evidenceCard}
                </div>
                ${agentCard}
              </div>
            </div>
          </main>
        </main>
      </div>
    </div>
  </body></html>`;
}

/* A realistic corroborated-evidence set for the incident. */
const REALISTIC_ROWS: EvidenceRow[] = [
  { type: 'Telemetry', title: 'Reserve backing ratio observation', source: 'live_provider', sourceVariant: 'success', txHash: '0x9a3f01…c21b', block: '21451233', observed: '3m ago', state: 'Corroborated', stateVariant: 'success' },
  { type: 'Telemetry', title: 'Redemption velocity sample', source: 'simulator', sourceVariant: 'info', txHash: '0x1b2c88…9f0a', block: '21451190', observed: '9m ago', state: 'Partial', stateVariant: 'warning' },
  { type: 'Alert', title: 'Oracle price deviation exceeded tolerance band', source: 'reserve-integrity', sourceVariant: 'neutral', observed: '1h ago', state: 'Corroborated', stateVariant: 'success' },
  { type: 'Signal', title: 'Custody attestation feed heartbeat', source: 'live_provider', sourceVariant: 'success', txHash: '0x77aa10…40de', block: '21450980', observed: '2h ago', state: 'Corroborated', stateVariant: 'success' },
];

// Adversarially long identifiers that must wrap / truncate rather than force
// the table (or the page) wider.
const LONG_HASH = '0xA1b2C3d4E5f6A7b8C9d0E1f2A3b4C5d6E7f8A9b0C1d2E3f4A5b6C7d8E9f0A1b2';
const LONG_TITLE = 'Immediate-review-required-unusually-large-redemption-settled-against-the-tokenized-treasury-reserve-pool-without-a-corresponding-attestation-event';
const LONG_ROWS: EvidenceRow[] = [
  { type: 'Telemetry', title: LONG_TITLE, source: 'live_provider', sourceVariant: 'success', txHash: LONG_HASH, block: '21451233', observed: '2m ago', state: 'Corroborated', stateVariant: 'success' },
  ...REALISTIC_ROWS,
];

/** Core geometry probe shared by the layout assertions. */
async function measure(page: import('@playwright/test').Page) {
  return page.evaluate(() => {
    const de = document.documentElement;
    const layout = document.querySelector('.forensicLayout') as HTMLElement;
    const main = layout.querySelector(':scope > .forensicMain') as HTMLElement;
    const agent = layout.querySelector(':scope > section.dataCard') as HTMLElement;
    const top = document.querySelector('.forensicMainTop') as HTMLElement;
    const topCards = Array.from(top.querySelectorAll(':scope > section.dataCard')) as HTMLElement[];
    const evidenceWrap = document.querySelector('.forensicEvidenceTable') as HTMLElement;
    const txCells = Array.from(document.querySelectorAll('.forensicEvidenceTable tbody td:nth-child(4)')) as HTMLElement[];

    const mainRect = main.getBoundingClientRect();
    const agentRect = agent.getBoundingClientRect();
    const c0 = topCards[0].getBoundingClientRect();
    const c1 = topCards[1].getBoundingClientRect();

    return {
      pageOverflow: de.scrollWidth - de.clientWidth,
      mainWidth: mainRect.width,
      agentWidth: agentRect.width,
      // agent to the right of the main column (desktop two-column) …
      agentSideBySide: agentRect.left >= mainRect.right - 2,
      // … or dropped below it (tablet / mobile stack).
      agentStacked: agentRect.top >= mainRect.bottom - 2,
      topSideBySide: c1.left >= c0.right - 2,
      topStacked: c1.top >= c0.bottom - 2,
      evidenceWrapOverflow: evidenceWrap.scrollWidth - evidenceWrap.clientWidth,
      evidenceWrapClientWidth: evidenceWrap.clientWidth,
      evidenceWrapScrollWidth: evidenceWrap.scrollWidth,
      txColumnVisible: txCells.length > 0 && txCells.every((c) => c.getBoundingClientRect().width > 0),
    };
  });
}

/* ───────────────────────────── 1. Layout (browser) ─────────────────────── */

test('1366px: readable two-column layout, agent panel beside main with a useful min width', async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 900 });
  await page.setContent(pageHtml(REALISTIC_ROWS));
  const m = await measure(page);
  expect(m.agentSideBySide).toBe(true);                 // agent stays in the right column
  expect(m.topSideBySide).toBe(true);                   // AI Summary + Workflow side by side
  expect(m.agentWidth).toBeGreaterThanOrEqual(300);     // agent panel has a useful minimum width
  // Main investigation content is ~2fr against the agent's ~1fr.
  expect(m.mainWidth).toBeGreaterThan(m.agentWidth * 1.6);
  expect(m.mainWidth).toBeLessThan(m.agentWidth * 2.4);
  expect(m.pageOverflow).toBeLessThanOrEqual(1);        // no page-level horizontal scrollbar
  expect(m.evidenceWrapOverflow).toBeLessThanOrEqual(1); // evidence table fits — no inner scrollbar
  expect(m.txColumnVisible).toBe(true);                 // all six columns present at desktop
});

test('1366px: long hashes / titles do not produce page-level horizontal overflow', async ({ page }) => {
  await page.setViewportSize({ width: 1366, height: 900 });
  await page.setContent(pageHtml(LONG_ROWS));
  const m = await measure(page);
  expect(m.pageOverflow).toBeLessThanOrEqual(1);
  expect(m.evidenceWrapOverflow).toBeLessThanOrEqual(1);
  expect(m.agentSideBySide).toBe(true);
});

test('1280px (standard desktop low end): two columns, agent min width preserved', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.setContent(pageHtml(REALISTIC_ROWS));
  const m = await measure(page);
  expect(m.agentSideBySide).toBe(true);
  expect(m.agentWidth).toBeGreaterThanOrEqual(300);
  expect(m.pageOverflow).toBeLessThanOrEqual(1);
  expect(m.evidenceWrapOverflow).toBeLessThanOrEqual(1);
});

test('1440px (large): at most three practical columns, evidence + agent readable', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.setContent(pageHtml(REALISTIC_ROWS));
  const m = await measure(page);
  // Three practical columns: AI Summary | Workflow (main) beside the agent —
  // never a fourth equally narrow column.
  expect(m.agentSideBySide).toBe(true);
  expect(m.topSideBySide).toBe(true);
  expect(m.agentWidth).toBeGreaterThanOrEqual(340);     // agent has room for readable content
  expect(m.mainWidth).toBeGreaterThan(m.agentWidth * 1.6);
  expect(m.pageOverflow).toBeLessThanOrEqual(1);
  expect(m.evidenceWrapOverflow).toBeLessThanOrEqual(1);
});

test('tablet (1100px): agent panel stacks below the investigation content', async ({ page }) => {
  await page.setViewportSize({ width: 1100, height: 900 });
  await page.setContent(pageHtml(REALISTIC_ROWS));
  const m = await measure(page);
  expect(m.agentStacked).toBe(true);                    // agent drops below the main content
  expect(m.pageOverflow).toBeLessThanOrEqual(1);
  expect(m.evidenceWrapOverflow).toBeLessThanOrEqual(1);
});

test('mobile (375px): single stacked column, no page-level horizontal overflow', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 800 });
  await page.setContent(pageHtml(LONG_ROWS));
  const m = await measure(page);
  expect(m.agentStacked).toBe(true);                    // agent stacked …
  expect(m.topStacked).toBe(true);                      // … AI Summary + Workflow stacked (one column)
  expect(m.txColumnVisible).toBe(false);                // lower-priority Tx / Block column hidden
  // The page itself never overflows — any evidence scroll is contained in the wrapper.
  expect(m.pageOverflow).toBeLessThanOrEqual(1);
  expect(m.evidenceWrapScrollWidth).toBeGreaterThan(m.evidenceWrapClientWidth + 1);
});

/* ───────────────────────── 2. Source-level wiring guards ───────────────── */

function read(rel: string): string {
  return fs.readFileSync(path.join(__dirname, '..', rel), 'utf-8');
}

test('styles.css defines the Screen 7 responsive layout + fixed-layout evidence table', () => {
  const css = read('app/styles.css');
  expect(css).toContain('.forensicLayout');
  expect(css).toContain('.forensicMain');
  expect(css).toContain('.forensicMainTop');
  // Single column by default; AI Summary + Workflow pair up at tablet …
  expect(css).toContain('@media (min-width: 768px)');
  // … and the page becomes a 2fr / 1fr (main / agent) split at standard desktop.
  expect(css).toContain('@media (min-width: 1280px)');
  expect(css).toContain('grid-template-columns: minmax(0, 2fr) minmax(320px, 1fr)');
  // Evidence table: fixed layout with a readable min-width floor; long content wraps.
  expect(css).toMatch(/\.forensicEvidenceTable table\s*\{[^}]*table-layout:\s*fixed/);
  expect(css).toMatch(/\.forensicEvidenceTable table\s*\{[^}]*min-width:/);
  expect(css).toContain('overflow-wrap: anywhere');
  // Lower-priority Tx / Block column is dropped on narrow screens.
  expect(css).toContain('@media (max-width: 640px)');
  // Deep-linked / re-focused content clears the sticky header + banner.
  expect(css).toContain('scroll-margin-top');
});

test('forensic panel wires the two-column layout and the fixed-layout evidence table', () => {
  const src = read('app/forensic-investigator-panel.tsx');
  expect(src).toContain('className="forensicLayout"');
  expect(src).toContain('className="forensicMain"');
  expect(src).toContain('className="forensicMainTop"');
  // Evidence uses the opt-in fixed-layout table class and keeps a contained scroll wrapper.
  expect(src).toContain('className="forensicEvidenceTable"');
  expect(src).toContain("overflowX: 'auto'");
  // The old flat four-column auto-fit grid is gone.
  expect(src).not.toContain('repeat(auto-fit, minmax(300px, 1fr))');
  // Evidence identifiers still truncate + copy (access to the full value preserved).
  expect(src).toContain('CopyableId');
  expect(src).toContain('View all evidence');
});

test('TableShell forwards an optional className for opt-in table sizing', () => {
  const ui = read('app/components/ui-primitives.tsx');
  expect(ui).toContain('export function TableShell');
  expect(ui).toContain('className');
});
