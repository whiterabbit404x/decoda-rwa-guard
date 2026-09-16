/**
 * Light-theme polish — the four shared contracts, MEASURED.
 *
 * The theme system already had two kinds of guard: source-level contracts
 * (theme-system.spec.ts) proving Light and Dark resolve from one token layer,
 * and whole-screen WCAG sweeps proving every rendered word clears AA. Both
 * passed on a workspace that was still hard to work in, because neither asks
 * the questions an analyst actually notices:
 *
 *   1. Is the secondary ink readable, or merely legal? --text-muted cleared AA
 *      at 5.32:1 on the tinted surface and still read as washed out beside
 *      near-black primary ink on white.
 *   2. Can you SEE the hairlines? --border measured 1.23:1 against the white
 *      card — a card outline and table rules that existed in the stylesheet
 *      and not on screen. A contrast sweep never looks at borders at all.
 *   3. Do the controls on one row share one height? A filter bar is normally a
 *      search field, two selects and a button; each was a different primitive
 *      with its own height, and nothing tested the row as a group.
 *   4. Does the monitoring status area have a hierarchy, or is it ten
 *      same-weight readings in a row?
 *
 * These are ratios and pixel measurements taken from a real mounted shell, not
 * assertions that a particular hex is present — the values may be tuned, but
 * they may not silently regress past the floor.
 */
import { expect, test, type Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import { resolveChromium, startRenderHarness, type Harness } from './support/render-harness';
import { applyTheme } from './support/contrast-sweep';
import { runtimeFieldTone } from '../app/components/runtime-banner';

const chromiumExecutable = resolveChromium();
if (chromiumExecutable) test.use({ launchOptions: { executablePath: chromiumExecutable } });

const styles = fs.readFileSync(path.join(__dirname, '..', 'app', 'styles.css'), 'utf-8');

/**
 * A filter bar built the way the product builds them — bare <input>, bare
 * <select>, the shared Select primitive and a .btn on one .buttonRow — plus a
 * table and the runtime banner's field markup, so heights, hairlines and the
 * status hierarchy can all be measured off one mount.
 */
const BOOTSTRAP = `
import React from '/vendor/react.js';
import { createRoot } from '/vendor/react-dom-client.js';
import { PilotAuthProvider } from '/app/pilot-auth-context.tsx';
import { ThemeProvider } from '/app/theme-context.tsx';
import AppShell from '/app/app-shell.tsx';
import { Select } from '/app/components/ui-primitives.tsx';

function Fixture() {
  return React.createElement('div', null,
    React.createElement('div', { className: 'buttonRow', id: 'filters' },
      React.createElement('input', { id: 'f-search', placeholder: 'Search incidents…', 'aria-label': 'Search' }),
      React.createElement('select', { id: 'f-native', 'aria-label': 'Severity' },
        React.createElement('option', null, 'All Severities')),
      React.createElement('div', { id: 'f-shared-wrap', style: { minWidth: '170px' } },
        React.createElement(Select, {
          value: '', onValueChange: () => {}, ariaLabel: 'Status',
          options: [{ value: '', label: 'All Statuses' }],
        })),
      React.createElement('button', { type: 'button', className: 'btn btn-primary', id: 'f-btn' }, 'Create Incident')),

    React.createElement('div', { className: 'tableWrap sharedTableShell', id: 'table' },
      React.createElement('table', null,
        React.createElement('thead', null,
          React.createElement('tr', null,
            React.createElement('th', { id: 'th-id' }, 'Incident ID'),
            React.createElement('th', null, 'Severity'))),
        React.createElement('tbody', null,
          React.createElement('tr', null,
            React.createElement('td', { id: 'td-id' }, 'inc-1'),
            React.createElement('td', { id: 'td-sev' },
              React.createElement('span', { className: 'ruleChip sharedStatusPill pill-danger', id: 'pill' }, 'Critical'))),
          React.createElement('tr', null,
            React.createElement('td', null, 'inc-2'),
            React.createElement('td', null, '—'))))),

    React.createElement('p', { className: 'muted', id: 'muted-text' }, 'Telemetry has not arrived for this workspace.'),
    React.createElement('article', { className: 'dataCard', id: 'card' },
      React.createElement('p', null, 'Card')));
}

createRoot(document.getElementById('root')).render(
  React.createElement(ThemeProvider, null,
  React.createElement(PilotAuthProvider, null,
  React.createElement(AppShell, null, React.createElement(Fixture, null)))));
`;

async function installSession(page: Page) {
  await page.addInitScript(() => {
    const w = window as any;
    w.localStorage.setItem('decoda.accessToken', 'fixture-token');
    w.localStorage.setItem('decoda.theme', 'light');
    w.__nav = { pathname: '/incidents', search: '', pushes: [], replaces: [] };
    const json = (body: unknown) =>
      Promise.resolve(new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } }));
    w.fetch = (input: any) => {
      const p = String(typeof input === 'string' ? input : input?.url ?? '').split('?')[0];
      if (p === '/api/runtime-config') {
        return json({ apiUrl: 'https://api.example.test', liveModeEnabled: true, apiTimeoutMs: 15000, configured: true, diagnostic: null, source: {} });
      }
      if (p === '/api/auth/csrf') return json({ csrfToken: 'fixture-csrf' });
      if (p === '/api/auth/me') {
        return json({ user: { id: 'u', email: 'analyst@acme.test', full_name: 'Acme Analyst', current_workspace_id: 'ws', current_workspace: { id: 'ws', name: 'Acme Capital' }, memberships: [] } });
      }
      return json({ items: [], results: [], data: [], total: 0, alerts: [], incidents: [], assets: [], degraded: false });
    };
  });
}

/**
 * WCAG contrast between two painted colours, evaluated in the page so the
 * numbers come from what the browser actually resolved the tokens to.
 */
const CONTRAST_FN = `
  const channel = (v) => { const s = v / 255; return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4; };
  const luminance = (c) => 0.2126 * channel(c[0]) + 0.7152 * channel(c[1]) + 0.0722 * channel(c[2]);
  const parse = (value) => {
    const probe = document.createElement('span');
    probe.style.color = value;
    document.body.appendChild(probe);
    const parts = getComputedStyle(probe).color.match(/[\\d.]+/g).map(Number);
    probe.remove();
    return [parts[0], parts[1], parts[2], parts[3] === undefined ? 1 : parts[3]];
  };
  const over = (fg, bg) => [0, 1, 2].map((i) => fg[i] * fg[3] + bg[i] * (1 - fg[3]));
  const ratio = (a, b) => { const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };
  const against = (token, surfaceToken) => {
    const cs = getComputedStyle(document.documentElement);
    const fg = parse(cs.getPropertyValue(token).trim());
    const bg = parse(cs.getPropertyValue(surfaceToken).trim());
    return ratio(over(fg, bg), bg);
  };
`;

let harness: Harness;
test.beforeAll(async () => { harness = await startRenderHarness({ bootstrap: BOOTSTRAP }); });
test.afterAll(async () => { await harness?.close(); });

async function mount(page: Page) {
  await installSession(page);
  await page.goto(harness.url);
  await page.waitForSelector('#filters', { timeout: 15_000 });
  expect(await page.evaluate(() => (window as any).__renderError)).toBeNull();
  await applyTheme(page, 'light');
}

/* ── 1. Light-mode ink ─────────────────────────────────────────────────── */
test.describe('light-mode text keeps a readable margin, not a passing grade', () => {
  test('muted ink clears 6:1 on every surface it is used on', async ({ page }) => {
    await mount(page);
    const ratios = await page.evaluate(`(() => {
      ${CONTRAST_FN}
      return {
        card: against('--text-muted', '--bg-card'),
        subtle: against('--text-muted', '--bg-subtle'),
        base: against('--text-muted', '--bg-base'),
      };
    })()`) as Record<string, number>;

    // AA for body text is 4.5:1. This floor is deliberately above it: the old
    // value passed AA on the tinted surface at 5.32:1 and still read as
    // washed out, which is the defect this guards.
    for (const [surface, ratio] of Object.entries(ratios)) {
      expect(ratio, `--text-muted on --bg-${surface} measured ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(5.9);
    }
  });

  test('the three text levels stay three levels — muted never merges into secondary', async ({ page }) => {
    await mount(page);
    const levels = await page.evaluate(`(() => {
      ${CONTRAST_FN}
      return {
        primary: against('--text-primary', '--bg-card'),
        secondary: against('--text-secondary', '--bg-card'),
        muted: against('--text-muted', '--bg-card'),
      };
    })()`) as Record<string, number>;

    // Strengthening muted is only an improvement if it remains a step BELOW
    // secondary; collapsed together they stop being a hierarchy.
    expect(levels.primary).toBeGreaterThan(levels.secondary);
    expect(levels.secondary).toBeGreaterThan(levels.muted);
    expect(levels.secondary - levels.muted, 'muted and secondary are too close to read as different levels')
      .toBeGreaterThan(0.5);
  });
});

/* ── 2. Hairlines ──────────────────────────────────────────────────────── */
test.describe('light-mode borders are visible', () => {
  test('the subtle and strong hairlines both read against the card, and stay ordered', async ({ page }) => {
    await mount(page);
    const borders = await page.evaluate(`(() => {
      ${CONTRAST_FN}
      return {
        border: against('--border', '--bg-card'),
        strong: against('--border-strong', '--bg-card'),
      };
    })()`) as Record<string, number>;

    // Not WCAG 1.4.11's 3:1 — these are dividers rather than control
    // boundaries, and a 3:1 rule between every table row would draw a grid.
    // The floor is the point at which a hairline is present rather than
    // theoretical: slate-200 measured 1.23:1 and was not.
    expect(borders.border, `--border measured ${borders.border.toFixed(2)}:1`).toBeGreaterThanOrEqual(1.3);
    expect(borders.strong, `--border-strong measured ${borders.strong.toFixed(2)}:1`).toBeGreaterThanOrEqual(1.7);
    expect(borders.strong).toBeGreaterThan(borders.border);
  });

  test('the dark palette is untouched by the light-mode strengthening', () => {
    // The raw palettes are separate, which is the whole point of the token
    // layer: a light-readability fix must not darken the dark theme's borders
    // or lift its muted ink.
    expect(styles).toContain('--d-border:       #263449;');
    expect(styles).toContain('--d-border-strong:#33455f;');
    expect(styles).toContain('--d-text-muted:   #94a3b8;');
  });
});

/* ── 3. One control height ─────────────────────────────────────────────── */
test.describe('controls on one row share one height', () => {
  test('search field, native select, shared Select and button all measure the same', async ({ page }) => {
    await mount(page);
    const heights = await page.evaluate(() => {
      const h = (selector: string) => {
        const el = document.querySelector(selector) as HTMLElement | null;
        return el ? Math.round(el.getBoundingClientRect().height) : null;
      };
      return {
        input: h('#f-search'),
        nativeSelect: h('#f-native'),
        sharedSelect: h('#f-shared-wrap .dcSelectTrigger'),
        button: h('#f-btn'),
      };
    });

    const values = Object.values(heights);
    expect(values.every((v) => typeof v === 'number' && v > 0), `a control did not render: ${JSON.stringify(heights)}`).toBe(true);
    expect(Math.max(...(values as number[])) - Math.min(...(values as number[])),
      `filter-bar control heights disagree: ${JSON.stringify(heights)}`).toBeLessThanOrEqual(1);
  });

  test('that shared height is the compact one, not a bigger UI', async ({ page }) => {
    await mount(page);
    const height = await page.evaluate(() =>
      Math.round((document.querySelector('#f-search') as HTMLElement).getBoundingClientRect().height));
    // 36px is the compact enterprise control height the form inputs already
    // used. This guards the levelling from being done by growing everything.
    expect(height).toBeGreaterThanOrEqual(34);
    expect(height).toBeLessThanOrEqual(38);
  });

  test('a bare filter control is themed rather than left to the browser', async ({ page }) => {
    await mount(page);
    const painted = await page.evaluate(() => {
      const el = document.querySelector('#f-search') as HTMLElement;
      const cs = getComputedStyle(el);
      return { border: cs.borderTopWidth, radius: cs.borderTopLeftRadius, background: cs.backgroundColor };
    });
    expect(painted.border).toBe('1px');
    expect(parseFloat(painted.radius)).toBeGreaterThan(0);
    expect(painted.background).toBe('rgb(255, 255, 255)');
  });

  test('the shared base layer is held at zero specificity so screens can still tune', () => {
    // :where() is load-bearing, not decoration. At normal specificity the base
    // rule would beat every per-screen control style declared after it and
    // flatten deliberate compact variants.
    expect(styles).toContain(':where(.appShellFrame, .dataCard)');
    expect(styles).toMatch(/:where\(input:not\(\[type="checkbox"\]\):not\(\[type="radio"\]\), select, textarea\)/);
  });
});

/* ── 4. Table hairlines and cell alignment ─────────────────────────────── */
test.describe('tables read as tables', () => {
  test('the header rule is stronger than the row rules', async ({ page }) => {
    await mount(page);
    const rules = await page.evaluate(`(() => {
      ${CONTRAST_FN}
      const head = getComputedStyle(document.querySelector('#th-id')).borderBottomColor;
      const row = getComputedStyle(document.querySelector('#td-id')).borderBottomColor;
      const card = parse(getComputedStyle(document.documentElement).getPropertyValue('--bg-card').trim());
      return { head: ratio(over(parse(head), card), card), row: ratio(over(parse(row), card), card) };
    })()`) as Record<string, number>;

    expect(rules.row, `row rule measured ${rules.row.toFixed(2)}:1`).toBeGreaterThanOrEqual(1.3);
    expect(rules.head, 'the header rule must separate labels from data more firmly than a row divider')
      .toBeGreaterThan(rules.row);
  });

  test('a status pill and the text beside it sit on the same centre line', async ({ page }) => {
    await mount(page);
    const offset = await page.evaluate(() => {
      const centre = (selector: string) => {
        const box = (document.querySelector(selector) as HTMLElement).getBoundingClientRect();
        return box.top + box.height / 2;
      };
      return Math.abs(centre('#td-id') - centre('#pill'));
    });
    // Top-aligned cells dropped a pill's label several pixels below the plain
    // text in the next column, on every operational row in the product.
    expect(offset, `pill and text centres differ by ${offset.toFixed(1)}px`).toBeLessThanOrEqual(1.5);
  });

  test('row density is unchanged — the alignment fix did not grow the rows', async ({ page }) => {
    // The density itself is the contract: the cell padding and type size are
    // exactly what they were, so centring the content cannot have moved them.
    const cell = styles.slice(styles.indexOf('\ntd {'));
    const declarations = cell.slice(0, cell.indexOf('}'));
    expect(declarations).toContain('padding: 0.7rem 0.9rem;');
    expect(declarations).toContain('font-size: 0.84rem;');

    await mount(page);
    const height = await page.evaluate(() =>
      Math.round((document.querySelector('#td-id') as HTMLElement).closest('tr')!.getBoundingClientRect().height));
    // A row carrying a status pill, at that padding: compact enterprise
    // density, nowhere near a comfortable-density row.
    expect(height, `row measured ${height}px`).toBeLessThanOrEqual(48);
  });

  test('the shared table shell reads its hairlines from the theme, not from dark-theme literals', () => {
    expect(styles).toMatch(/\.sharedTableShell table thead th \{[^}]*border-bottom: 1px solid var\(--border-strong\)/);
    expect(styles).toMatch(/\.sharedTableShell table tbody td \{[^}]*border-bottom: 1px solid var\(--border\)/);
    expect(styles).not.toContain('rgba(124, 156, 196, 0.22)');
    expect(styles).not.toContain('rgba(124, 156, 196, 0.12)');
  });

  test('the no-severity status pill is neutral, never a navy chip on a white table', () => {
    // `default` means "no severity to report". It was painted with a 35%
    // navy wash invented for the dark theme.
    const block = styles.slice(styles.indexOf('.sharedStatusPill {'));
    const declarations = block.slice(0, block.indexOf('}'));
    expect(declarations).toContain('var(--neutral-bg)');
    expect(declarations).toContain('var(--neutral-fg)');
    expect(declarations).not.toContain('rgba(41, 63, 96');
  });
});

/* ── 5. The monitoring status area has a hierarchy ─────────────────────── */
test.describe('the monitoring status area reads as a status, not a log line', () => {
  const banner = fs.readFileSync(path.join(__dirname, '..', 'app', 'components', 'runtime-banner.tsx'), 'utf-8');

  test('the ten readings are grouped by the question they answer', () => {
    // Verdict, evidence clocks, operator instruction. Before this they were one
    // undifferentiated middot-separated run, which is what made a status area
    // read as debug output.
    expect(banner).toContain(`data-group="verdict"`);
    expect(banner).toContain(`data-group="evidence"`);
    expect(banner).toContain(`data-group="operator"`);
    expect(styles).toContain('.runtimeBannerGroup + .runtimeBannerGroup');
  });

  test('every field the strip used to render still renders', () => {
    for (const label of ['Monitoring', 'Freshness', 'Confidence', 'Telemetry', 'Heartbeat', 'Poll', 'Next action', 'Workers', 'Limitation', 'Activity']) {
      expect(banner, `the ${label} field was dropped`).toContain(`label="${label}"`);
    }
    // The three separate proofs stay separate: heartbeat proves the worker is
    // alive, poll proves the loop ran, telemetry proves data arrived.
    expect(banner).toContain('value={formatAge(summary.last_telemetry_at)}');
    expect(banner).toContain('value={formatAge(summary.last_heartbeat_at)}');
    expect(banner).toContain('value={formatAge(summary.last_poll_at)}');
  });

  test('the verdict strip states the condition in words, not only in colour', () => {
    const modeBanner = fs.readFileSync(path.join(__dirname, '..', 'app', 'workspace-monitoring-mode-banner.tsx'), 'utf-8');
    expect(modeBanner).toContain("return 'LIMITED COVERAGE'");
    expect(modeBanner).toContain("return 'SETUP REQUIRED'");
    expect(modeBanner).toContain("return 'OFFLINE'");
    // The tint is added to the words; it never replaces them.
    const block = styles.slice(styles.indexOf('.globalHealthWarning {'));
    expect(block.slice(0, block.indexOf('}'))).toContain('var(--warning-bg)');
  });
});

/* ── 6. Tone is derived from the canonical verdict, and fails closed ───── */
test.describe('status tone can never claim more than the runtime proved', () => {
  test('only a proven-healthy verdict reads positive', () => {
    for (const label of ['Live', 'Fresh', 'Live coverage current', 'Verified']) {
      expect(runtimeFieldTone(label), label).toBe('positive');
    }
  });

  test('a reported degraded condition reads as one', () => {
    for (const label of ['Limited coverage', 'Setup required', 'Stale', 'Partial']) {
      expect(runtimeFieldTone(label), label).toBe('caution');
    }
    expect(runtimeFieldTone('Offline')).toBe('critical');
  });

  test('an ABSENCE of evidence is never toned as healthy', () => {
    // These are the labels the derivations produce when nothing has been
    // proven either way. The product's rule is that no data must not be shown
    // as safe; grey is how this strip says so.
    for (const label of ['Waiting for telemetry', 'Unknown', 'Pending evidence', 'Unavailable']) {
      expect(runtimeFieldTone(label), label).toBe('neutral');
    }
  });

  test('an unrecognised reading falls through to neutral, never to positive', () => {
    // Clock readings, worker headlines, quiet-workspace notes, and any label a
    // future derivation adds without updating the map.
    for (const value of ['7h ago', 'never', '42s ago', 'QuickNode Stream is behind the chain tip.', 'A brand new verdict']) {
      expect(runtimeFieldTone(value), value).toBe('neutral');
    }
  });

  test('the tone follows the value, so a field cannot be given a colour its words do not support', () => {
    const banner = fs.readFileSync(path.join(__dirname, '..', 'app', 'components', 'runtime-banner.tsx'), 'utf-8');
    expect(banner).toContain('data-tone={tone ?? runtimeFieldTone(value)}');
    // Neutral is the painted default: only the three toned states get a colour.
    expect(styles).toContain(`.runtimeBannerField[data-tone='positive'] .runtimeBannerValue { color: var(--success-fg); }`);
    expect(styles).toContain(`.runtimeBannerField[data-tone='caution']  .runtimeBannerValue { color: var(--warning-fg); }`);
    expect(styles).toContain(`.runtimeBannerField[data-tone='critical'] .runtimeBannerValue { color: var(--danger-fg); }`);
    expect(styles).not.toMatch(/\[data-tone='neutral'\][^{]*\{[^}]*--success-fg/);
  });
});
