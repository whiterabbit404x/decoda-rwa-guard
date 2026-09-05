/**
 * Screen 7 — Case File / Full Investigation information architecture.
 *
 * The forensic LOGIC is unchanged; this pins where it is presented. The narrow
 * Case File beside the incident queue is a compact summary — case metadata, the
 * six operational-integrity states, a compact progress line, and the
 * full-investigation hand-off. The forensic record itself (reason-code lists,
 * reconciliation detail, the four evidence domains and their artifact directory,
 * the lifecycle chronology, the response authorization trail, the AI
 * investigation) belongs to Open Full Investigation, across the main content
 * width.
 *
 * Three layers:
 *   1. Executable unit tests for the pure fold (`buildIntegritySummary`,
 *      `summarizeWorkflowProgress`) — this is where the truthfulness rules live:
 *      absence, an unreadable read and an in-flight read are three different
 *      answers, and none of them earns a state badge.
 *   2. Real-browser layout tests with the ACTUAL app CSS injected, measuring the
 *      Case File column width, its stacking, the wide-overview split, and the
 *      rendered type scale.
 *   3. Source-level structural guards so the placement cannot silently regress.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import {
  buildIntegritySummary,
  summarizeResponseState,
  summarizeWorkflowProgress,
  type IncidentCaseSummary,
} from '../app/incident-forensics-presentation';
import type { WorkflowStage } from '../app/forensic-investigation-presentation';

function appSource(...segments: string[]): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', ...segments), 'utf-8');
}

// The stylesheet is byte-order-marked on disk. Injected verbatim into a <style>
// element the BOM invalidates the first rule — the :root design tokens — so every
// var() below would silently fall back. Strip it so these measurements are of the
// real, tokenised styles.
const APP_CSS = fs.readFileSync(path.join(__dirname, '..', 'app', 'styles.css'), 'utf-8').replace(/^\uFEFF/, '');

/* ── Resolve the Chromium binary present in this environment (mirrors the
 * Screen 7 layout spec): the pinned Playwright build can differ from the
 * browser build on disk here. */
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

/* ═════════════════ 1. The fold (pure, executable) ═══════════════════════ */

/** A fully recorded case, in the exact shape the backend sends. */
const RECORDED: IncidentCaseSummary = {
  event_id: 'evt-1',
  detection: {
    category: 'OPERATIONAL_INTEGRITY',
    detection_type: 'unmatched_issuance',
    title: 'Unmatched issuance',
    severity: 'critical',
    reason_code: 'ISSUANCE_NOT_AUTHORIZED',
  },
  on_chain: {
    state: 'observed',
    operation: 'mint',
    observed_amount: { value: '500000', unit: 'units' },
    block_number: '21451233',
  },
  operational: {
    state: 'anomaly',
    reconciliation_status: 'not_matched',
    reason_code: 'NO_AUTHORIZED_ISSUANCE',
    variance_amount: { value: '500000', unit: 'units' },
  },
  policy: {
    state: 'decided',
    decision: 'DENY',
    policy_key: 'issuance-authorization',
    policy_version: 3,
    reason_codes: ['COMPLIANCE_APPROVAL_MISSING', 'AUTHORIZATION_ABSENT', 'VARIANCE_UNEXPLAINED'],
  },
  evidence: { artifact_count: 11, snapshot_status: 'ready' },
};

function rowsFor(
  summary: IncidentCaseSummary | null,
  summaryLoad: Parameters<typeof buildIntegritySummary>[0]['summaryLoad'],
  actions: Parameters<typeof summarizeResponseState>[0] = [],
  responseLoad: Parameters<typeof buildIntegritySummary>[0]['responseLoad'] = 'empty',
) {
  const rows = buildIntegritySummary({
    summary,
    summaryLoad,
    response: summarizeResponseState(actions),
    responseLoad,
  });
  return Object.fromEntries(rows.map((r) => [r.key, r]));
}

test('the six integrity domains are reported in lifecycle order', () => {
  const rows = buildIntegritySummary({
    summary: RECORDED,
    summaryLoad: 'ready',
    response: summarizeResponseState([]),
    responseLoad: 'empty',
  });
  expect(rows.map((r) => r.key)).toEqual([
    'detection', 'on_chain', 'operational', 'policy', 'response', 'evidence',
  ]);
  expect(rows.map((r) => r.label)).toEqual([
    'Detection', 'On-Chain', 'Operational', 'Policy', 'Response', 'Evidence',
  ]);
});

test('a recorded case states each backend fact, with its state badge', () => {
  const rows = rowsFor(RECORDED, 'ready');
  expect(rows.detection.value).toBe('Unmatched issuance');
  expect(rows.detection.badge).toEqual({ label: 'Critical', variant: 'danger' });

  expect(rows.on_chain.value).toBe('Mint 500000 units');
  expect(rows.on_chain.badge).toEqual({ label: 'Observed', variant: 'info' });

  // The badge carries the five-word outcome vocabulary, which keeps "collected and
  // did not match" apart from "nothing was collected". Here the engine's own status
  // token says the same thing, so the value blanks rather than printing the fact
  // twice in a 360px column.
  expect(rows.operational.value).toBe('');
  expect(rows.operational.badge).toEqual({ label: 'Not matched', variant: 'danger' });
  expect(rows.operational.recorded).toBe(true);

  // The badge carries the verdict; the value carries why, in one phrase; the detail
  // states WHERE the verdict came from, so an authoritative DENY is never unexplained.
  expect(rows.policy.value).toBe('Compliance Approval Missing +2 more');
  expect(rows.policy.badge).toEqual({ label: 'DENY', variant: 'danger' });
  expect(rows.policy.detail).toBe('Matched policy');
  expect(rows.policy.code).toBe('issuance-authorization v3');

  expect(rows.evidence.value).toBe('11 artifacts');
  // Short enough to sit beside the value; the same four states, never a new one.
  expect(rows.evidence.badge).toEqual({ label: 'Ready', variant: 'info' });
});

test('a row never prints its own badge twice', () => {
  // A value identical to its badge wastes the one line the column has. The badge keeps
  // the state; the value goes blank and the renderer skips it.
  const noReasons: IncidentCaseSummary = { policy: { state: 'decided', decision: 'ALLOW' } };
  const policy = rowsFor(noReasons, 'ready').policy;
  expect(policy.badge).toEqual({ label: 'ALLOW', variant: 'success' });
  expect(policy.value).toBe('');
  expect(policy.recorded).toBe(true);
  const noStatus: IncidentCaseSummary = { operational: { state: 'anomaly' } };
  const operational = rowsFor(noStatus, 'ready').operational;
  expect(operational.badge).toEqual({ label: 'Not matched', variant: 'danger' });
  expect(operational.value).toBe('');
  // The two strings come from different vocabularies — the engine's status token and
  // the outcome word — so the comparison ignores case and surrounding space.
  const differentCase: IncidentCaseSummary = {
    operational: { state: 'anomaly', reconciliation_status: 'NOT_MATCHED' },
  };
  expect(rowsFor(differentCase, 'ready').operational.value).toBe('');
  // A status that genuinely says something else is kept.
  const distinct: IncidentCaseSummary = {
    operational: { state: 'anomaly', reconciliation_status: 'UNEXPLAINED_VARIANCE' },
  };
  expect(rowsFor(distinct, 'ready').operational.value).toBe('Unexplained Variance');
});

test('a stack of reason codes becomes one short line, not a column of pills', () => {
  // Three codes stacked as pills is what made the narrow panel unreadable. The full
  // list stays on the full investigation; the summary states the first and the rest.
  expect(rowsFor(RECORDED, 'ready').policy.value).toBe('Compliance Approval Missing +2 more');
  const single = { ...RECORDED, policy: { ...RECORDED.policy, reason_codes: ['AUTH_MISSING'] } };
  expect(rowsFor(single, 'ready').policy.value).toBe('Auth Missing');
});

test('an absent record says so and earns NO badge', () => {
  // No data must not be shown as safe — and it must not be shown as a verdict either.
  const rows = rowsFor({}, 'empty');
  expect(rows.detection.value).toBe('No linked detection');
  expect(rows.on_chain.value).toBe('Not available');
  expect(rows.operational.value).toBe('Not collected');
  expect(rows.policy.value).toBe('Not evaluated');
  expect(rows.response.value).toBe('No response action');
  expect(rows.evidence.value).toBe('No snapshot');
  for (const row of Object.values(rows)) {
    expect(row.badge, row.key).toBeNull();
    expect(row.recorded, row.key).toBe(false);
  }
});

test('a not_recorded section is never rendered as Observed / Not matched / DENY', () => {
  const empty: IncidentCaseSummary = {
    on_chain: { state: 'not_recorded' },
    operational: { state: 'not_recorded' },
    policy: { state: 'not_recorded' },
  };
  const rows = rowsFor(empty, 'ready');
  for (const key of ['on_chain', 'operational', 'policy'] as const) {
    expect(rows[key].badge, key).toBeNull();
    expect(rows[key].recorded, key).toBe(false);
  }
  expect(rows.on_chain.value).toBe('Not available');
  expect(rows.policy.value).toBe('Not evaluated');
});

test('loading, unreadable and absent are three different answers', () => {
  expect(rowsFor(null, 'loading').policy.value).toBe('Loading…');
  expect(rowsFor(null, 'unauthorized').policy.value).toBe('Not permitted in this workspace');
  expect(rowsFor(null, 'not_found').policy.value).toBe('Incident not found');
  expect(rowsFor(null, 'error').policy.value).toBe('Unavailable');
  expect(rowsFor({}, 'empty').policy.value).toBe('Not evaluated');
  // None of them is a state.
  for (const load of ['loading', 'unauthorized', 'not_found', 'error'] as const) {
    expect(rowsFor(RECORDED, load).policy.badge, load).toBeNull();
  }
});

test('the response row waits for Screen 8 to actually be read', () => {
  // An empty action list mid-fetch is not "no response action".
  expect(rowsFor(RECORDED, 'ready', [], 'loading').response.value).toBe('Loading…');
  expect(rowsFor(RECORDED, 'ready', [], 'error').response.value).toBe('Unavailable');
  expect(rowsFor(RECORDED, 'ready', [], 'empty').response.value).toBe('No response action');
  const pending = rowsFor(RECORDED, 'ready', [{ id: 'a', approval_status: 'pending' }], 'ready').response;
  // The value carries Screen 8's canonical sentence (with its count); the badge names
  // the state alone, so a 360px column is not asked to render the count twice. Both
  // numbers name their unit: these count ACTIONS, never approvals.
  expect(pending.value).toBe('1 action awaiting approval');
  expect(pending.badge).toEqual({ label: 'Awaiting', variant: 'warning' });
  expect(pending.detail).toBe('1 response action recommended in total');
  const failed = rowsFor(RECORDED, 'ready', [{ id: 'a', execution_status: 'failed' }], 'ready').response;
  expect(failed.badge).toEqual({ label: 'Failed', variant: 'danger' });
});

test('a snapshot badge is shown only for a state the backend recorded', () => {
  const noStatus = { ...RECORDED, evidence: { artifact_count: 4, snapshot_status: null } };
  const row = rowsFor(noStatus, 'ready').evidence;
  expect(row.value).toBe('4 artifacts');
  expect(row.badge).toBeNull();          // never a green "ready" the backend did not assert
  expect(row.detail).toBe('Snapshot state unknown');
});

test('the fold is deterministic', () => {
  const a = buildIntegritySummary({ summary: RECORDED, summaryLoad: 'ready', response: summarizeResponseState([]), responseLoad: 'empty' });
  const b = buildIntegritySummary({ summary: RECORDED, summaryLoad: 'ready', response: summarizeResponseState([]), responseLoad: 'empty' });
  expect(a).toEqual(b);
});

/* ── Compact investigation progress ─────────────────────────────────────── */

const STAGES: WorkflowStage[] = [
  { stage: 'detection', label: 'Detection', state: 'completed' },
  { stage: 'scope', label: 'Scope', state: 'completed' },
  { stage: 'evidence', label: 'Evidence Collection', state: 'completed' },
  { stage: 'correlation', label: 'Correlation', state: 'in_progress' },
  { stage: 'analysis', label: 'Analysis', state: 'pending' },
  { stage: 'recommendations', label: 'Recommendations', state: 'pending' },
  { stage: 'report', label: 'Report Generated', state: 'pending' },
];

test('progress counts the canonical stages and names the current one', () => {
  const progress = summarizeWorkflowProgress(STAGES);
  expect(progress).toEqual({ total: 7, completed: 3, failed: 0, percent: 42, current: 'Correlation' });
});

test('a queued stage is the current one when nothing is in progress', () => {
  const progress = summarizeWorkflowProgress([
    { stage: 'a', label: 'A', state: 'completed' },
    { stage: 'b', label: 'B', state: 'queued' },
  ]);
  expect(progress?.current).toBe('B');
  expect(progress?.percent).toBe(50);
});

test('a failed stage is counted and never hidden behind the completed count', () => {
  const progress = summarizeWorkflowProgress([
    { stage: 'a', label: 'A', state: 'completed' },
    { stage: 'b', label: 'B', state: 'failed' },
  ]);
  expect(progress?.failed).toBe(1);
  expect(progress?.completed).toBe(1);
});

test('no recorded stages yields null — "0 / 0" is a claim the data does not support', () => {
  expect(summarizeWorkflowProgress([])).toBeNull();
  expect(summarizeWorkflowProgress(null)).toBeNull();
});

/* ═════════════════ 2. Layout (real browser, real CSS) ═══════════════════ */

/**
 * Faithful DOM slice of /incidents: the KPI row, the filter bar, the queue table
 * and the Case File panel inside the app shell — with the real class names the
 * panel renders, so the measurements are of the shipped CSS.
 */
function queuePageHtml(): string {
  const rows = Array.from({ length: 4 }, (_, i) => `
    <tr>
      <td style="font-family:monospace;font-size:0.75rem">inc-${i}-9f8e7d6c5b4a3f2e1d0c</td>
      <td><span class="ruleChip sharedStatusPill pill-danger">Critical</span></td>
      <td>Unmatched issuance against the tokenized treasury reserve</td>
      <td style="font-size:0.8rem">RWA Treasury Note</td>
      <td><span class="ruleChip sharedStatusPill pill-info">Investigating</span></td>
      <td style="font-size:0.78rem">3m ago</td>
      <td><a class="btn btn-secondary" href="#">View Incident</a></td>
    </tr>`).join('');

  const integrityRow = (label: string, value: string, badge: string | null, detail: string | null, code: string | null) => `
    <div class="caseFileIntegrityRow">
      <span class="caseFileIntegrityLabel">${label}</span>
      <div class="caseFileIntegrityBody">
        <div class="caseFileIntegrityHead">
          <span class="caseFileIntegrityValue">${value}</span>
          ${badge ?? ''}
        </div>
        ${detail ? `<span class="caseFileIntegrityDetail">${detail}</span>` : ''}
        ${code ? `<span class="caseFileIntegrityCode">${code}</span>` : ''}
      </div>
    </div>`;

  const metaField = (label: string, value: string, wide = false) => `
    <div class="caseFileMetaField${wide ? ' caseFileMetaField-wide' : ''}">
      <p class="caseFileMetaLabel">${label}</p>
      <div class="caseFileMetaValue">${value}</div>
    </div>`;

  const caseFile = `
  <aside class="dataCard sharedSurfaceCard caseFilePanel" aria-label="Incident detail">
    <div class="caseFileHeader">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:0.5rem">
        <p class="caseFileEyebrow">Case File</p>
        <span class="ruleChip sharedStatusPill pill-danger">Critical</span>
      </div>
      <h4 class="caseFileTitle">Unmatched issuance against the tokenized treasury reserve</h4>
      <div class="caseFileMeta">
        ${metaField('Incident ID', '<span class="caseFileMonoValue">inc-0-9f8e7d6c5b4a3f2e1d0c</span>', true)}
        ${metaField('Status', '<span class="ruleChip sharedStatusPill pill-info">Investigating</span>')}
        ${metaField('Evidence Source', '<span class="ruleChip sharedStatusPill pill-success">live_provider</span>')}
        ${metaField('Created', '7/30/2026, 09:14:22')}
        ${metaField('Updated', '7/30/2026, 09:21:04')}
        ${metaField('Asset', 'RWA Treasury Note')}
        ${metaField('Assigned', 'Unassigned')}
        ${metaField('Linked Alert', '<span class="caseFileMonoValue">alr-7f6e5d4c3b2a</span>')}
        ${metaField('Linked Detection', '<span class="caseFileMonoValue">det-5d4c3b2a1f0e</span>')}
        ${metaField('Canonical Event', '<span class="caseFileMonoValue">evt-9f8e7d6c5b4a3f2e1d0c9b8a7f6e5d4c</span>', true)}
      </div>
      <a class="btn btn-primary caseFileCta" href="#">Open Full Investigation</a>
    </div>
    <div class="caseFileBody">
      <section class="caseFileSection" aria-label="Integrity summary">
        <p class="caseFileSectionTitle">Integrity Summary</p>
        <div class="caseFileIntegrity">
          ${integrityRow('Detection', 'Unmatched issuance', '<span class="ruleChip sharedStatusPill pill-danger">Critical</span>', 'Operational Integrity', null)}
          ${integrityRow('On-Chain', 'Mint 500000 units', '<span class="ruleChip sharedStatusPill pill-info">Observed</span>', 'Block 21451233', null)}
          ${integrityRow('Operational', 'Not Matched', '<span class="ruleChip sharedStatusPill pill-danger">Mismatch</span>', 'Variance 500000 units', null)}
          ${integrityRow('Policy', 'Compliance Approval Missing +2 more', '<span class="ruleChip sharedStatusPill pill-danger">DENY</span>', null, 'issuance-authorization v3')}
          ${integrityRow('Response', 'Awaiting approval (2)', '<span class="ruleChip sharedStatusPill pill-warning">Awaiting</span>', '3 recommended', null)}
          ${integrityRow('Evidence', '11 artifacts', '<span class="ruleChip sharedStatusPill pill-info">Ready</span>', null, 'EV-0007')}
        </div>
        <a class="btn btn-secondary" href="#" style="font-size:0.75rem;padding:0.2rem 0.55rem;align-self:flex-start">Open in Response Actions</a>
      </section>
      <section class="caseFileSection" aria-label="Investigation progress">
        <div style="display:flex;align-items:center;justify-content:space-between;gap:0.5rem">
          <p class="caseFileSectionTitle">Investigation</p>
          <span class="ruleChip sharedStatusPill pill-info">Investigating</span>
        </div>
        <div class="caseFileProgressHead"><span class="caseFileProgressCount">3 / 7 complete</span></div>
        <div class="caseFileProgressTrack"><div class="caseFileProgressFill" style="width:42%"></div></div>
        <span class="caseFileProgressCurrent">Current: Correlation</span>
        <p class="caseFileIntegrityDetail" style="margin:0">Next: Review Findings</p>
      </section>
    </div>
  </aside>`;

  return `<!DOCTYPE html><html><head><meta charset="utf-8"><style>${APP_CSS}</style></head>
  <body>
    <div class="appShellFrame">
      <aside class="appSidebar">
        <div class="brandBlock">Decoda</div>
        <nav class="appNav"><a href="#">Dashboard</a><a href="#">Alerts</a><a href="#">Incidents</a></nav>
      </aside>
      <div class="appShellContent">
        <header class="appShellTop"><div style="height:56px"></div></header>
        <main class="appShellPage">
          <main class="productPage">
            <section class="featureSection">
              <div class="incidentsQueueCounters">
                <article class="metricCard sharedMetricTile"><p class="metricLabel">Open Incidents</p><p class="metricValue">4</p></article>
                <article class="metricCard sharedMetricTile"><p class="metricLabel">Critical Incidents</p><p class="metricValue">1</p></article>
                <article class="metricCard sharedMetricTile"><p class="metricLabel">In Investigation</p><p class="metricValue">2</p></article>
                <article class="metricCard sharedMetricTile"><p class="metricLabel">Awaiting Response</p><p class="metricValue">1</p></article>
              </div>
              <div class="incidentsQueueLayout incidentsQueueLayout-withCase">
                <div>
                  <div class="tableWrap sharedTableShell tableCompact">
                    <table>
                      <thead><tr><th>Incident ID</th><th>Severity</th><th>Title</th><th>Asset</th><th>Status</th><th>Created</th><th>Action</th></tr></thead>
                      <tbody>${rows}</tbody>
                    </table>
                  </div>
                </div>
                ${caseFile}
              </div>
            </section>
          </main>
        </main>
      </div>
    </div>
  </body></html>`;
}

async function measureQueue(page: import('@playwright/test').Page) {
  return page.evaluate(() => {
    const de = document.documentElement;
    const layout = document.querySelector('.incidentsQueueLayout') as HTMLElement;
    const queue = layout.children[0] as HTMLElement;
    const caseFile = document.querySelector('.caseFilePanel') as HTMLElement;
    const q = queue.getBoundingClientRect();
    const c = caseFile.getBoundingClientRect();
    const px = (sel: string) => {
      const el = document.querySelector(sel) as HTMLElement | null;
      return el ? parseFloat(getComputedStyle(el).fontSize) : 0;
    };
    return {
      pageOverflow: de.scrollWidth - de.clientWidth,
      queueWidth: q.width,
      caseFileWidth: c.width,
      sideBySide: c.left >= q.right - 2,
      stacked: c.top >= q.bottom - 2,
      caseFileHeight: c.height,
      viewportHeight: window.innerHeight,
      // Where the compact progress and the last integrity row sit, relative to the
      // top of the Case File — the "pushed below the fold" complaint, measured.
      progressOffset: Math.round(
        (document.querySelector('[aria-label="Investigation progress"]') as HTMLElement).getBoundingClientRect().top - c.top,
      ),
      integrityBottomOffset: Math.round(
        (Array.from(document.querySelectorAll('.caseFileIntegrityRow')).pop() as HTMLElement).getBoundingClientRect().bottom - c.top,
      ),
      titleFont: px('.caseFileTitle'),
      valueFont: px('.caseFileIntegrityValue'),
      metaValueFont: px('.caseFileMetaValue'),
      sectionTitleFont: px('.caseFileSectionTitle'),
      detailFont: px('.caseFileIntegrityDetail'),
      metaLabelFont: px('.caseFileMetaLabel'),
      // Every text node the Case File renders, at its computed size.
      smallestTextPx: Array.from(document.querySelectorAll('.caseFilePanel *'))
        .filter((el) => Array.from(el.childNodes).some((n) => n.nodeType === 3 && (n.textContent ?? '').trim()))
        .reduce((min, el) => Math.min(min, parseFloat(getComputedStyle(el as HTMLElement).fontSize)), 99),
    };
  });
}

test('1440px: the Case File is a compact 320–380px column beside the queue', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.setContent(queuePageHtml());
  const m = await measureQueue(page);
  expect(m.sideBySide).toBe(true);
  // A moderate preview column — never half the page, never a 250px squeeze.
  expect(m.caseFileWidth).toBeGreaterThanOrEqual(320);
  expect(m.caseFileWidth).toBeLessThanOrEqual(380);
  // The queue keeps the main content area.
  expect(m.queueWidth).toBeGreaterThan(m.caseFileWidth * 1.5);
  expect(m.pageOverflow).toBeLessThanOrEqual(1);
});

test('1280px (standard desktop low end): the Case File keeps its width, the queue keeps the page', async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.setContent(queuePageHtml());
  const m = await measureQueue(page);
  expect(m.sideBySide).toBe(true);
  expect(m.caseFileWidth).toBeGreaterThanOrEqual(320);
  expect(m.caseFileWidth).toBeLessThanOrEqual(380);
  expect(m.queueWidth).toBeGreaterThan(m.caseFileWidth);
  expect(m.pageOverflow).toBeLessThanOrEqual(1);
});

test('tablet (1024px) and mobile (375px): the Case File stacks instead of crushing the queue', async ({ page }) => {
  for (const width of [1024, 375]) {
    await page.setViewportSize({ width, height: 900 });
    await page.setContent(queuePageHtml());
    const m = await measureQueue(page);
    expect(m.stacked, `${width}px`).toBe(true);
    expect(m.pageOverflow, `${width}px`).toBeLessThanOrEqual(1);
  }
});

test('the Case File type scale is readable — nothing important below 11px', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.setContent(queuePageHtml());
  const m = await measureQueue(page);
  expect(m.titleFont).toBeGreaterThanOrEqual(13.5);        // panel heading ~14px
  expect(m.valueFont).toBeGreaterThanOrEqual(12.5);        // primary values ~13px
  expect(m.metaValueFont).toBeGreaterThanOrEqual(12.5);
  expect(m.sectionTitleFont).toBeGreaterThanOrEqual(10.5); // section headings ~11px
  expect(m.detailFont).toBeGreaterThanOrEqual(10.5);       // secondary metadata ~11px
  expect(m.metaLabelFont).toBeGreaterThanOrEqual(10.5);
  // No 7–9px text anywhere in the Case File.
  expect(m.smallestTextPx).toBeGreaterThanOrEqual(10.5);
});

test('the integrity summary and the progress line are within one screen of the Case File', async ({ page }) => {
  // Investigation progress used to sit far below the fold, underneath a tab strip, a
  // six-section forensic overview and a seven-item checklist. All six integrity states
  // AND the progress line now read within a single viewport of the panel's top.
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.setContent(queuePageHtml());
  const m = await measureQueue(page);
  expect(m.integrityBottomOffset).toBeLessThanOrEqual(m.viewportHeight);
  expect(m.progressOffset).toBeLessThanOrEqual(m.viewportHeight);
});

test('the queue KPI row wraps instead of forcing the page wider than the viewport', async ({ page }) => {
  // A fixed four-column counter grid made the whole incidents page overflow a phone
  // viewport, which pushed the queue table and the Case File off-screen with it.
  await page.setViewportSize({ width: 375, height: 800 });
  await page.setContent(queuePageHtml());
  const m = await page.evaluate(() => {
    const de = document.documentElement;
    const tiles = Array.from(document.querySelectorAll('.incidentsQueueCounters > *')) as HTMLElement[];
    return {
      pageOverflow: de.scrollWidth - de.clientWidth,
      widest: Math.max(...tiles.map((t) => t.getBoundingClientRect().right)),
      clientWidth: de.clientWidth,
    };
  });
  expect(m.pageOverflow).toBeLessThanOrEqual(1);
  expect(m.widest).toBeLessThanOrEqual(m.clientWidth + 1);
});

/* ── The wide full-investigation overview ───────────────────────────────── */

function wideOverviewHtml(): string {
  const section = (title: string, pill: string, lines: string) => `
    <section class="incidentCaseSection">
      <div class="incidentCaseSectionHead"><p class="sectionEyebrow" style="margin:0">${title}</p>${pill}</div>
      <div style="display:flex;flex-direction:column;gap:0.25rem">${lines}</div>
    </section>`;
  const line = (label: string, value: string) =>
    `<div class="incidentCaseLine"><span class="incidentCaseLineLabel">${label}</span><span class="incidentCaseLineValue">${value}</span></div>`;

  return `<!DOCTYPE html><html><head><meta charset="utf-8"><style>${APP_CSS}</style></head>
  <body>
    <div class="appShellFrame">
      <aside class="appSidebar"><div class="brandBlock">Decoda</div></aside>
      <div class="appShellContent">
        <main class="appShellPage">
          <main class="productPage">
            <section class="featureSection">
              <div class="dataCard sharedSurfaceCard" style="padding:1.15rem">
                <div class="incidentCaseOverview incidentCaseOverview-wide" aria-label="Incident case summary">
                  <div class="incidentCaseAnalysis">
                    <p class="incidentCaseColumnTitle">Operational integrity analysis</p>
                    ${section('Detection', '', line('Category', 'Operational Integrity') + line('Type', 'Unmatched issuance'))}
                    <div class="incidentCaseCompare">
                      ${section('On-chain state', '<span class="ruleChip sharedStatusPill pill-info">Observed</span>', line('Observed', 'Mint 500000 units') + line('Transaction', '<span class="incidentMonoValue">0xA1b2C3d4E5f6A7b8C9d0E1f2A3b4C5d6E7f8A9b0</span>'))}
                      ${section('Operational state', '<span class="ruleChip sharedStatusPill pill-danger">Mismatch</span>', line('Reconciliation', 'Not Matched') + line('Variance', '500000 units'))}
                    </div>
                    <div class="incidentCaseReconciliation" aria-label="Reconciliation result">
                      <span class="incidentCaseReconciliationLabel">Reconciliation result</span>
                      <span class="ruleChip sharedStatusPill pill-danger">Not Matched</span>
                    </div>
                    ${section('Policy', '<span class="ruleChip sharedStatusPill pill-danger">DENY</span>', line('Decision', 'DENY') + line('Reason codes', '<span class="incidentReasonCode">COMPLIANCE_APPROVAL_MISSING</span> <span class="incidentReasonCode">AUTHORIZATION_ABSENT</span> <span class="incidentReasonCode">VARIANCE_UNEXPLAINED</span>'))}
                  </div>
                  <div class="incidentCaseStateColumn">
                    <p class="incidentCaseColumnTitle">Case state</p>
                    ${section('Response', '<span class="ruleChip sharedStatusPill pill-warning">Awaiting approval (2)</span>', line('Recommended', '3'))}
                    ${section('Evidence', '<span class="ruleChip sharedStatusPill pill-info">Evidence snapshot ready</span>', line('Collected', '11 artifacts'))}
                  </div>
                </div>
              </div>
            </section>
          </main>
        </main>
      </div>
    </div>
  </body></html>`;
}

test('the full investigation overview uses the main width: analysis 2fr beside case state 1fr', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.setContent(wideOverviewHtml());
  const m = await page.evaluate(() => {
    const de = document.documentElement;
    const analysis = document.querySelector('.incidentCaseAnalysis') as HTMLElement;
    const state = document.querySelector('.incidentCaseStateColumn') as HTMLElement;
    const compare = Array.from(document.querySelectorAll('.incidentCaseCompare > .incidentCaseSection')) as HTMLElement[];
    const a = analysis.getBoundingClientRect();
    const s = state.getBoundingClientRect();
    const c0 = compare[0].getBoundingClientRect();
    const c1 = compare[1].getBoundingClientRect();
    return {
      pageOverflow: de.scrollWidth - de.clientWidth,
      analysisWidth: a.width,
      stateWidth: s.width,
      stateBeside: s.left >= a.right - 2,
      chainBesideOperational: c1.left >= c0.right - 2,
    };
  });
  expect(m.stateBeside).toBe(true);
  // The analysis column carries roughly twice the case-state column.
  expect(m.analysisWidth).toBeGreaterThan(m.stateWidth * 1.5);
  // The two readings whose disagreement is the case sit side by side.
  expect(m.chainBesideOperational).toBe(true);
  // Far more room than the ~360px Case File column it replaced.
  expect(m.analysisWidth).toBeGreaterThan(600);
  expect(m.pageOverflow).toBeLessThanOrEqual(1);
});

test('mobile: the wide overview collapses to one column with no page overflow', async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 800 });
  await page.setContent(wideOverviewHtml());
  const m = await page.evaluate(() => {
    const de = document.documentElement;
    const analysis = document.querySelector('.incidentCaseAnalysis') as HTMLElement;
    const state = document.querySelector('.incidentCaseStateColumn') as HTMLElement;
    return {
      pageOverflow: de.scrollWidth - de.clientWidth,
      stacked: state.getBoundingClientRect().top >= analysis.getBoundingClientRect().bottom - 2,
    };
  });
  expect(m.stacked).toBe(true);
  expect(m.pageOverflow).toBeLessThanOrEqual(1);
});

/* ═════════════════ 3. Structural guards ════════════════════════════════ */

test('the Case File keeps the forensic data connections it summarises', () => {
  const panel = appSource('incidents-panel.tsx');
  // The forensic logic is NOT removed: the canonical case record, the canonical
  // investigation and Screen 8's action rows are still read for the selected incident.
  expect(panel).toContain('useIncidentEvidence(');
  expect(panel).toContain('/incidents/${encodeURIComponent(selectedId)}/investigation');
  expect(panel).toContain('`${apiUrl}/response/actions?incident_id=${selectedId}`');
  expect(panel).toContain('buildIntegritySummary(');
  expect(panel).toContain('summarizeWorkflowProgress(');
  expect(panel).toContain('investigationNextAction(investigation, { awaitingResponse })');
  expect(panel).toContain('linkedDetectionRef(investigation)');
});

test('the Case File does not render the detailed record it hands off', () => {
  const panel = appSource('incidents-panel.tsx');
  // The evidence directory, the lifecycle chronology and the AI triage panel are the
  // three things a ~360px column cannot render; they live on the full investigation.
  for (const heavy of ['IncidentEvidenceTab', 'IncidentForensicTimeline', 'AiInvestigationPanel', 'IncidentCaseOverview']) {
    expect(panel, heavy).not.toContain(heavy);
  }
  // …and it no longer fetches what it does not render.
  expect(panel).not.toContain('/timeline`');
  expect(panel).not.toContain('setForensicTimeline');
});

test('the tab bodies remain a single source, exported to the full investigation', () => {
  const panel = appSource('incidents-panel.tsx');
  const tabs = appSource('incident-case-file-tabs.tsx');
  for (const body of ['TimelineTab', 'AlertsTab', 'EvidenceTab', 'ResponseActionsTab']) {
    expect(panel, body).toContain(body);
    expect(tabs, body).toContain(body);
  }
  expect(tabs).toContain("from './incidents-panel'");
});

test('the Case File column width and stacking live in CSS, not an inline pixel value', () => {
  const panel = appSource('incidents-panel.tsx');
  const css = fs.readFileSync(path.join(__dirname, '..', 'app', 'styles.css'), 'utf-8');
  expect(panel).toContain('incidentsQueueLayout');
  expect(panel).not.toContain("'1fr 400px'");
  expect(css).toContain('.incidentsQueueLayout');
  expect(css).toContain('.incidentsQueueLayout-withCase');
  // A single column by default; two only once there is room for both.
  expect(css).toMatch(/\.incidentsQueueLayout\s*\{[^}]*grid-template-columns:\s*minmax\(0, 1fr\)/);
  expect(css).toMatch(/grid-template-columns:\s*minmax\(0, 1fr\) 3[2-7]\dpx/);
});

test('the Case File styles add no gradients, glows or animated states', () => {
  const css = fs.readFileSync(path.join(__dirname, '..', 'app', 'styles.css'), 'utf-8');
  const block = css.slice(css.indexOf('.incidentsQueueLayout'), css.indexOf('.incidentCaseOverview'));
  expect(block.length).toBeGreaterThan(0);
  for (const banned of ['gradient', 'box-shadow', 'animation', '@keyframes', 'filter:']) {
    expect(block, banned).not.toContain(banned);
  }
});

test('no reference-design value is hard-coded into the refined Screen 7 code', () => {
  const sources = ['incidents-panel.tsx', 'incident-case-overview.tsx', 'incident-case-file-tabs.tsx',
    'incident-forensics-presentation.ts'].map((f) => appSource(f)).join('\n');
  for (const literal of ['INC-2026-017', 'POL-MINT-007', '+500,000', 'RWA-004', '11 artifacts',
    '14 artifacts', '4 / 7 complete']) {
    expect(sources, literal).not.toContain(literal);
  }
});
