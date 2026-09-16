/**
 * The authenticated shell — RENDERED, in Chromium, in both themes.
 *
 * The sibling spec (theme-system.spec.ts) pins the architecture by reading
 * source. It cannot prove the thing a redesign is actually judged on: whether
 * an analyst can READ the screen. A token can be wired correctly everywhere
 * and still resolve to grey-on-grey.
 *
 * So this spec mounts the UNMODIFIED AppShell — real AppNavigation, real
 * ThemeToggle, real styles.css — switches the theme the way the product does
 * (stamping <html data-theme>), and MEASURES what is painted: WCAG contrast on
 * the sidebar, the header and table text, in Light and in Dark.
 *
 * The specific regressions it exists to catch:
 *   • white-on-white — the sidebar reading the workspace text token, so dark
 *     ink lands on navy the moment the workspace goes light;
 *   • a card, table or header that stays dark while the workspace turns light,
 *     or vice versa;
 *   • an active nav item distinguishable only by hue.
 */
import { expect, test, type Page } from '@playwright/test';

import { resolveChromium, startRenderHarness, type Harness } from './support/render-harness';

const chromiumExecutable = resolveChromium();
if (chromiumExecutable) test.use({ launchOptions: { executablePath: chromiumExecutable } });

/**
 * The same provider stack the real app mounts: RootLayout wraps
 * ThemeProvider → PilotAuthProvider, and AppShell supplies the runtime and
 * plan providers itself. Mounting AppShell bare throws ("usePilotAuth must be
 * used within PilotAuthProvider"), which is the point — the shell genuinely
 * depends on session context, so the harness supplies it rather than stubbing
 * the hook.
 */
const BOOTSTRAP = `
import React from '/vendor/react.js';
import { createRoot } from '/vendor/react-dom-client.js';
import { PilotAuthProvider } from '/app/pilot-auth-context.tsx';
import { ThemeProvider } from '/app/theme-context.tsx';
import AppShell from '/app/app-shell.tsx';

createRoot(document.getElementById('root')).render(
  React.createElement(ThemeProvider, null,
  React.createElement(PilotAuthProvider, null,
  React.createElement(AppShell, null,
    React.createElement('article', { className: 'dataCard' },
      React.createElement('h3', null, 'Monitoring coverage'),
      React.createElement('p', { className: 'muted' }, 'Telemetry has not arrived for this workspace.'),

      // The button system, including the states a theme flip most easily
      // strands: disabled, and the destructive-confirmation fill.
      React.createElement('div', { className: 'buttonRow' },
        React.createElement('button', { type: 'button', className: 'btn btn-primary' }, 'Run diagnostic'),
        React.createElement('button', { type: 'button', className: 'btn btn-secondary' }, 'Export'),
        React.createElement('button', { type: 'button', className: 'btn btn-ghost' }, 'Cancel'),
        React.createElement('button', { type: 'button', className: 'btn btn-danger' }, 'Block address'),
        React.createElement('button', { type: 'button', className: 'btn btn-destructive' }, 'Confirm freeze'),
        React.createElement('button', { type: 'button', className: 'btn btn-secondary', disabled: true }, 'Approve')),

      // Every status the product can report, in one row, so no screen can
      // quietly disagree about what HEALTHY or UNKNOWN look like.
      React.createElement('div', { className: 'chipRow' },
        React.createElement('span', { className: 'ruleChip pill-danger' }, 'CRITICAL'),
        React.createElement('span', { className: 'ruleChip pill-warning' }, 'DEGRADED'),
        React.createElement('span', { className: 'ruleChip pill-success' }, 'VERIFIED'),
        React.createElement('span', { className: 'ruleChip pill-info' }, 'INVESTIGATING'),
        React.createElement('span', { className: 'ruleChip pill-neutral' }, 'UNKNOWN'),
        React.createElement('span', { className: 'ruleChip pill-violet' }, 'GOVERNANCE')),

      // Labelled form controls, including disabled and invalid.
      React.createElement('div', { className: 'formField' },
        React.createElement('label', { htmlFor: 'qa-input' }, 'Contract address'),
        React.createElement('input', { id: 'qa-input', defaultValue: '0xabc', placeholder: '0x…' }),
        React.createElement('p', { className: 'inputHint' }, 'Checksummed address of the monitored contract.'),
        React.createElement('p', { className: 'fieldError' }, 'Address failed checksum validation.')),
      React.createElement('div', { className: 'formField' },
        React.createElement('label', { htmlFor: 'qa-disabled' }, 'Workspace (read only)'),
        React.createElement('input', { id: 'qa-disabled', defaultValue: 'Acme Capital', disabled: true })),

      React.createElement('div', { className: 'underlineTabs', role: 'tablist' },
        React.createElement('button', { type: 'button', role: 'tab', className: 'underlineTab', 'aria-selected': true }, 'Overview'),
        React.createElement('button', { type: 'button', role: 'tab', className: 'underlineTab', 'aria-selected': false }, 'Findings')),

      React.createElement('div', { className: 'tableWrap' },
        React.createElement('table', null,
          React.createElement('thead', null,
            React.createElement('tr', null,
              React.createElement('th', null, 'Asset'),
              React.createElement('th', null, 'Status'))),
          React.createElement('tbody', null,
            React.createElement('tr', null,
              React.createElement('td', null, 'Reserve contract'),
              React.createElement('td', null,
                React.createElement('span', { className: 'ruleChip pill-neutral' }, 'UNKNOWN')))))))))));
`;

let harness: Harness;
test.beforeAll(async () => { harness = await startRenderHarness({ bootstrap: BOOTSTRAP }); });
test.afterAll(async () => { await harness?.close(); });

/* ── Contrast maths (WCAG 2.1 relative luminance) ─────────────────── */
function channel(v: number): number {
  const s = v / 255;
  return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}
function luminance([r, g, b]: number[]): number {
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}
function parseRgb(value: string): number[] {
  const parts = value.match(/[\d.]+/g);
  if (!parts) throw new Error(`unparseable colour: ${value}`);
  return [Number(parts[0]), Number(parts[1]), Number(parts[2])];
}
function ratio(fg: number[], bg: number[]): number {
  const a = luminance(fg);
  const b = luminance(bg);
  const [hi, lo] = a > b ? [a, b] : [b, a];
  return (hi + 0.05) / (lo + 0.05);
}

/**
 * The colour actually painted behind an element: walk up until an ancestor
 * declares a non-transparent background, compositing any translucent layers
 * on the way. Reading `backgroundColor` alone reports `rgba(0,0,0,0)` for most
 * text nodes and would make every check pass vacuously.
 */
async function paintedPair(page: Page, selector: string): Promise<{ fg: number[]; bg: number[] }> {
  const pair = await page.locator(selector).first().evaluate((el) => {
    const stack: string[] = [];
    let node: HTMLElement | null = el as HTMLElement;
    const colour = getComputedStyle(el).color;
    while (node) {
      const bg = getComputedStyle(node).backgroundColor;
      const parts = bg.match(/[\d.]+/g)?.map(Number) ?? [];
      const alpha = parts.length > 3 ? parts[3] : 1;
      if (alpha > 0) stack.push(bg);
      if (alpha === 1) break;
      node = node.parentElement;
    }
    return { colour, stack };
  });

  // Composite from the opaque base upward.
  let bg = [255, 255, 255];
  for (const layer of [...pair.stack].reverse()) {
    const parts = layer.match(/[\d.]+/g)!.map(Number);
    const alpha = parts.length > 3 ? parts[3] : 1;
    bg = [0, 1, 2].map((i) => alpha * parts[i] + (1 - alpha) * bg[i]);
  }
  return { fg: parseRgb(pair.colour), bg };
}

async function mountShell(page: Page, theme: 'light' | 'dark' | 'system') {
  await page.addInitScript((value) => { (window as any).__qaTheme = value; }, theme);
  await page.addInitScript(() => {
    const w = window as any;
    w.localStorage.setItem('decoda.accessToken', 'fixture-token');
    // A real route, so the active nav item is decided by the component's own
    // isActive logic rather than by the test reaching into the DOM.
    w.__nav = { pathname: '/dashboard', search: '', pushes: [], replaces: [] };
    // Seed the stored preference BEFORE the app boots, exactly as a returning
    // analyst's browser would. The provider then resolves it on mount; the
    // test never reaches in and stamps data-theme itself.
    w.localStorage.setItem('decoda.theme', (window as any).__qaTheme ?? 'light');
    const json = (body: unknown, status = 200) =>
      Promise.resolve(new Response(JSON.stringify(body), {
        status, headers: { 'content-type': 'application/json' },
      }));
    w.fetch = (input: any) => {
      const p = String(typeof input === 'string' ? input : input?.url ?? '').split('?')[0];
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
            id: 'f1x7u4e0-0000-0000-0000-0000000000u1',
            email: 'analyst@acme.test', full_name: 'Acme Analyst',
            current_workspace_id: 'fixture-ws',
            current_workspace: { id: 'fixture-ws', name: 'Acme Capital', slug: 'acme' },
            memberships: [{ workspace: { id: 'fixture-ws', name: 'Acme Capital' }, role: 'admin' }],
          },
        });
      }
      return json({}, 404);
    };
  });

  await page.goto(harness.url);
  await expect(page.locator('.appSidebar')).toBeVisible();
  expect(await page.evaluate(() => (window as any).__renderError)).toBeNull();

  // The provider stamps the resolved theme on mount. Wait for that rather
  // than racing it — and for 'system' this is the assertion's whole point:
  // whatever lands here was resolved from the OS, not written by the test.
  await expect
    .poll(async () => page.evaluate(() => document.documentElement.getAttribute('data-theme')))
    .toMatch(/^(light|dark)$/);
}

for (const theme of ['light', 'dark'] as const) {
  test.describe(`${theme} theme`, () => {
    test('the sidebar stays dark navy and its text stays readable on it', async ({ page }) => {
      await mountShell(page, theme);

      const sidebarBg = parseRgb(
        await page.locator('.appSidebar').evaluate((el) => getComputedStyle(el).backgroundColor),
      );
      // Dark navy in BOTH themes — that is the product's identity, and the
      // reason the sidebar reads --sidebar-* rather than the workspace tokens.
      expect(luminance(sidebarBg), `${theme} sidebar luminance`).toBeLessThan(0.05);

      // Every nav label against it. This is the white-on-white regression:
      // if --sidebar-text ever followed --text-secondary, light mode would
      // put slate ink on navy and this drops to ~1.5.
      const labels = page.locator('.appNav a');
      const count = await labels.count();
      expect(count).toBeGreaterThan(5);
      for (let i = 0; i < count; i++) {
        const colour = parseRgb(await labels.nth(i).evaluate((el) => getComputedStyle(el).color));
        expect(ratio(colour, sidebarBg), `nav item ${i} on sidebar`).toBeGreaterThanOrEqual(4.5);
      }

      // Group headings are smaller and dimmer; hold them to large-text AA.
      const headings = page.locator('.appNavSection');
      for (let i = 0; i < await headings.count(); i++) {
        const { fg } = await paintedPair(page, `.appNavSection >> nth=${i}`);
        expect(ratio(fg, sidebarBg), `nav heading ${i}`).toBeGreaterThanOrEqual(3);
      }
    });

    test('the workspace matches the theme while the sidebar does not', async ({ page }) => {
      await mountShell(page, theme);

      const contentBg = parseRgb(
        await page.locator('.appShellContent').evaluate((el) => getComputedStyle(el).backgroundColor),
      );
      const cardBg = parseRgb(
        await page.locator('.dataCard').first().evaluate((el) => getComputedStyle(el).backgroundColor),
      );

      if (theme === 'light') {
        expect(luminance(contentBg), 'light workspace').toBeGreaterThan(0.8);
        expect(luminance(cardBg), 'light card').toBeGreaterThan(0.9);
      } else {
        expect(luminance(contentBg), 'dark workspace').toBeLessThan(0.05);
        expect(luminance(cardBg), 'dark card').toBeLessThan(0.08);
      }
    });

    test('header, card, table and status text all clear WCAG AA', async ({ page }) => {
      await mountShell(page, theme);

      for (const selector of [
        '.shellWorkspaceSelector',
        '.shellUserChip',
        '.dataCard h3',
        '.dataCard .muted',
        '.tableWrap th',
        '.tableWrap td',
        '.pill-neutral',
      ]) {
        const { fg, bg } = await paintedPair(page, selector);
        // 4.5 for body text; the uppercase table head and the pill are large
        // or bold enough to sit at the AA large-text threshold.
        const required = selector === '.tableWrap th' || selector === '.pill-neutral' ? 3 : 4.5;
        expect(ratio(fg, bg), `${selector} in ${theme}`).toBeGreaterThanOrEqual(required);
      }
    });

    test('every status pill is readable, and UNKNOWN never reads as healthy', async ({ page }) => {
      await mountShell(page, theme);

      for (const pill of ['danger', 'warning', 'success', 'info', 'neutral', 'violet']) {
        const { fg, bg } = await paintedPair(page, `.pill-${pill}`);
        expect(ratio(fg, bg), `pill-${pill} in ${theme}`).toBeGreaterThanOrEqual(3);
      }

      // The truthfulness rule as a pixel assertion: absence of data must not
      // borrow the colour that means "healthy". A grey and a green that
      // resolved to the same hue would pass every source-level check.
      const unknown = await paintedPair(page, '.pill-neutral');
      const healthy = await paintedPair(page, '.pill-success');
      expect(unknown.fg.join(','), 'UNKNOWN must not reuse the healthy colour')
        .not.toBe(healthy.fg.join(','));
      // Grey means grey: near-equal channels, no green cast.
      const [r, g, b] = unknown.fg;
      expect(Math.max(r, g, b) - Math.min(r, g, b), 'UNKNOWN should be neutral').toBeLessThan(40);
    });

    test('the button system is readable in every variant, disabled included', async ({ page }) => {
      await mountShell(page, theme);

      for (const variant of ['primary', 'secondary', 'ghost', 'danger', 'destructive']) {
        const { fg, bg } = await paintedPair(page, `.btn-${variant}`);
        expect(ratio(fg, bg), `btn-${variant} in ${theme}`).toBeGreaterThanOrEqual(4.5);
      }

      // Disabled must read as disabled without becoming invisible: dimmed,
      // but still legible enough to know what the blocked action was.
      const disabled = page.locator('.btn:disabled').first();
      const opacity = Number(await disabled.evaluate((el) => getComputedStyle(el).opacity));
      expect(opacity).toBeLessThan(1);
      expect(opacity).toBeGreaterThanOrEqual(0.5);
      expect(await disabled.evaluate((el) => getComputedStyle(el).cursor)).toBe('not-allowed');

      const { fg, bg } = await paintedPair(page, '.btn:disabled');
      const effective = [0, 1, 2].map((i) => opacity * fg[i] + (1 - opacity) * bg[i]);
      expect(ratio(effective, bg), `disabled button in ${theme}`).toBeGreaterThanOrEqual(2.5);
    });

    test('form labels, hints, errors and disabled inputs stay legible', async ({ page }) => {
      await mountShell(page, theme);

      for (const [selector, required] of [
        ['.formField label', 4.5],
        ['.inputHint', 4.5],
        ['.fieldError', 4.5],
      ] as const) {
        const { fg, bg } = await paintedPair(page, selector);
        expect(ratio(fg, bg), `${selector} in ${theme}`).toBeGreaterThanOrEqual(required);
      }

      // The input surface must belong to the theme, not fight it: a white
      // field on a navy card (or a navy field on a white card) is the
      // classic half-migrated-theme artefact.
      const fieldBg = parseRgb(
        await page.locator('#qa-input').evaluate((el) => getComputedStyle(el).backgroundColor),
      );
      const cardBg = parseRgb(
        await page.locator('.dataCard').first().evaluate((el) => getComputedStyle(el).backgroundColor),
      );
      expect(Math.abs(luminance(fieldBg) - luminance(cardBg)), `input vs card in ${theme}`).toBeLessThan(0.2);

      // Typed text in the field, and the disabled field's value, both readable.
      for (const selector of ['#qa-input', '#qa-disabled']) {
        const { fg, bg } = await paintedPair(page, selector);
        expect(ratio(fg, bg), `${selector} value in ${theme}`).toBeGreaterThanOrEqual(3);
      }
    });

    test('the selected tab is distinguishable from the unselected one', async ({ page }) => {
      await mountShell(page, theme);

      const selected = page.locator('.underlineTab[aria-selected="true"]');
      const idle = page.locator('.underlineTab[aria-selected="false"]');

      const marks = await selected.evaluate((el) => {
        const style = getComputedStyle(el);
        return { colour: style.color, border: style.borderBottomColor, weight: Number(style.fontWeight) };
      });
      const idleMarks = await idle.evaluate((el) => {
        const style = getComputedStyle(el);
        return { colour: style.color, border: style.borderBottomColor, weight: Number(style.fontWeight) };
      });

      // Underline + weight, not colour alone.
      expect(marks.border).not.toBe(idleMarks.border);
      expect(marks.weight).toBeGreaterThan(idleMarks.weight);

      for (const selector of ['.underlineTab[aria-selected="true"]', '.underlineTab[aria-selected="false"]']) {
        const { fg, bg } = await paintedPair(page, selector);
        expect(ratio(fg, bg), `${selector} in ${theme}`).toBeGreaterThanOrEqual(4.5);
      }
    });

    test('the account menu popover is opaque and readable', async ({ page }) => {
      await mountShell(page, theme);
      await page.locator('.shellUserChip').click();

      const menu = page.locator('.shellUserMenu');
      await expect(menu).toBeVisible();

      // Opaque: a translucent popover over a table would make its items
      // unreadable wherever a row happened to sit behind them.
      const menuBg = await menu.evaluate((el) => getComputedStyle(el).backgroundColor);
      const parts = menuBg.match(/[\d.]+/g)!.map(Number);
      expect(parts.length > 3 ? parts[3] : 1, `${theme} menu opacity`).toBe(1);

      for (const selector of [
        '.shellUserMenuEmail',
        '.shellUserMenuRole',
        '.shellUserMenuLabel',
        '.shellUserMenuItem',
        '.shellUserMenuItem--danger',
        '.themeToggleOption[aria-checked="true"]',
        '.themeToggleOption[aria-checked="false"]',
      ]) {
        const { fg, bg } = await paintedPair(page, selector);
        const required = selector === '.shellUserMenuLabel' ? 3 : 4.5;
        expect(ratio(fg, bg), `${selector} in ${theme}`).toBeGreaterThanOrEqual(required);
      }
    });

    test('the active nav item is marked by more than hue', async ({ page }) => {
      await mountShell(page, theme);

      // /dashboard is the mounted route, so this is the component's own
      // active state, aria-current included.
      const active = page.locator('.appNav a.active').first();
      await expect(active).toHaveAttribute('aria-current', 'page');
      await expect(active).toContainText('Dashboard');

      // `.appNav a` carries `transition: background 0.12s`, so a computed
      // style read immediately after the class lands returns the START of the
      // transition — transparent — not the active surface. Poll until the
      // transition settles rather than asserting on a frame mid-animation.
      await expect.poll(
        async () => active.evaluate((el) => getComputedStyle(el).backgroundColor),
        { message: 'active nav surface should settle to a filled tint' },
      ).not.toBe('rgba(0, 0, 0, 0)');

      const marks = await active.evaluate((el) => {
        const style = getComputedStyle(el);
        const rail = getComputedStyle(el, '::before');
        return {
          weight: Number(style.fontWeight),
          background: style.backgroundColor,
          colour: style.color,
          railWidth: rail.width,
          railContent: rail.content,
        };
      });

      // A filled surface, a left rail, AND a weight change — so the state
      // survives greyscale and colour-vision differences.
      expect(marks.weight).toBeGreaterThanOrEqual(600);
      expect(marks.background).not.toBe('rgba(0, 0, 0, 0)');
      expect(marks.railContent).not.toBe('none');
      expect(parseFloat(marks.railWidth)).toBeGreaterThan(0);

      // And it is still readable against the surface it just gained.
      const sidebarBg = parseRgb(
        await page.locator('.appSidebar').evaluate((el) => getComputedStyle(el).backgroundColor),
      );
      const parts = marks.background.match(/[\d.]+/g)!.map(Number);
      const alpha = parts.length > 3 ? parts[3] : 1;
      const painted = [0, 1, 2].map((i) => alpha * parts[i] + (1 - alpha) * sidebarBg[i]);
      expect(ratio(parseRgb(marks.colour), painted), 'active nav label').toBeGreaterThanOrEqual(4.5);
    });
  });
}

/* ── System theme ─────────────────────────────────────────────────────────
   The default preference. No data-theme is stamped, so the CSS media query
   and the provider's own resolution are the only things deciding — and they
   have to agree, or the analyst gets one theme's tokens under the other
   theme's colour-scheme. Chromium's emulated colour scheme stands in for the
   OS setting. */
for (const [emulated, expected] of [['light', 'light'], ['dark', 'dark']] as const) {
  test(`System follows the OS when it reports ${emulated}`, async ({ page }) => {
    // Set before boot: the provider reads the media query on mount.
    await page.emulateMedia({ colorScheme: emulated });
    await mountShell(page, 'system');

    // The provider resolves it and stamps the attribute…
    await expect.poll(async () =>
      page.evaluate(() => document.documentElement.getAttribute('data-theme')),
    ).toBe(expected);
    await expect.poll(async () =>
      page.evaluate(() => document.documentElement.getAttribute('data-theme-preference')),
    ).toBe('system');

    // …and what is painted matches, rather than merely being labelled.
    const contentBg = parseRgb(
      await page.locator('.appShellContent').evaluate((el) => getComputedStyle(el).backgroundColor),
    );
    if (expected === 'light') expect(luminance(contentBg)).toBeGreaterThan(0.8);
    else expect(luminance(contentBg)).toBeLessThan(0.05);

    // Body text is readable either way — the case where the media query and
    // the provider disagree would land here as grey-on-grey.
    const { fg, bg } = await paintedPair(page, '.dataCard h3');
    expect(ratio(fg, bg), `System→${expected} card heading`).toBeGreaterThanOrEqual(4.5);
  });
}

test('a live OS change is followed while the preference is System, and ignored after an explicit choice', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'light' });
  await mountShell(page, 'system');
  await expect.poll(async () =>
    page.evaluate(() => document.documentElement.getAttribute('data-theme')),
  ).toBe('light');

  await page.emulateMedia({ colorScheme: 'dark' });
  await expect.poll(async () =>
    page.evaluate(() => document.documentElement.getAttribute('data-theme')),
  ).toBe('dark');

  // Choose Light explicitly: the OS must stop driving the theme.
  await page.locator('.shellUserChip').click();
  await page.getByRole('radiogroup', { name: 'Appearance' }).getByRole('radio', { name: 'Light' }).click();
  await expect.poll(async () =>
    page.evaluate(() => document.documentElement.getAttribute('data-theme')),
  ).toBe('light');

  await page.emulateMedia({ colorScheme: 'dark' });
  await page.waitForTimeout(150);
  expect(
    await page.evaluate(() => document.documentElement.getAttribute('data-theme')),
    'an explicit Light must survive the OS going dark',
  ).toBe('light');
});

/* ── Responsive ───────────────────────────────────────────────────────────
   Desktop stays the priority; the point here is that nothing becomes
   unusable or unreadable on the way down. */
for (const width of [390, 768, 1024, 1280, 1440]) {
  test(`the shell stays usable and readable at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await mountShell(page, 'light');

    // Navigation is reachable at every width — it collapses to a horizontal
    // strip below 1100px rather than disappearing.
    const navLinks = page.locator('.appNav a');
    expect(await navLinks.count()).toBeGreaterThan(5);
    await expect(navLinks.first()).toBeVisible();

    // The page never scrolls sideways as a whole …
    const overflow = await page.evaluate(() =>
      document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(overflow, `horizontal overflow at ${width}px`).toBeLessThanOrEqual(1);

    // … but a wide security table is allowed to scroll inside its own box,
    // because squeezing incident columns into 390px is worse than scrolling.
    expect(
      await page.locator('.tableWrap').first().evaluate((el) => getComputedStyle(el).overflowX),
    ).toBe('auto');

    // The account menu stays reachable and on-screen at every width.
    await page.locator('.shellUserChip').click();
    const menu = page.locator('.shellUserMenu');
    await expect(menu).toBeVisible();
    const box = (await menu.boundingBox())!;
    expect(box.x, `menu clipped left at ${width}px`).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width, `menu clipped right at ${width}px`).toBeLessThanOrEqual(width + 1);
  });
}

test('the account menu opens instead of signing the analyst out', async ({ page }) => {
  // The shipped chip called signOut() on click while rendering a chevron that
  // promised a menu: one mis-click ended a session mid-investigation.
  await mountShell(page, 'light');

  const chip = page.locator('.shellUserChip');
  await expect(chip).toHaveAttribute('aria-expanded', 'false');
  await chip.click();

  await expect(page.locator('.shellUserMenu')).toBeVisible();
  await expect(chip).toHaveAttribute('aria-expanded', 'true');
  await expect(page.getByRole('menuitem', { name: 'Sign out' })).toBeVisible();
  // Still on the page: nothing navigated.
  await expect(page.locator('.appSidebar')).toBeVisible();

  // Escape closes it and returns focus to the chip.
  await page.keyboard.press('Escape');
  await expect(page.locator('.shellUserMenu')).toHaveCount(0);
  await expect(chip).toBeFocused();
});

test('the theme control switches the workspace and reports the current choice', async ({ page }) => {
  await mountShell(page, 'light');
  await page.locator('.shellUserChip').click();

  const group = page.getByRole('radiogroup', { name: 'Appearance' });
  await expect(group).toBeVisible();

  await group.getByRole('radio', { name: 'Dark' }).click();
  await expect.poll(async () =>
    page.evaluate(() => document.documentElement.getAttribute('data-theme')),
  ).toBe('dark');
  await expect(group.getByRole('radio', { name: 'Dark' })).toHaveAttribute('aria-checked', 'true');

  // …and the choice is persisted for the next visit.
  expect(await page.evaluate(() => window.localStorage.getItem('decoda.theme'))).toBe('dark');

  await group.getByRole('radio', { name: 'Light' }).click();
  await expect.poll(async () =>
    page.evaluate(() => document.documentElement.getAttribute('data-theme')),
  ).toBe('light');
  expect(await page.evaluate(() => window.localStorage.getItem('decoda.theme'))).toBe('light');
});
