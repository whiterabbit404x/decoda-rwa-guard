/**
 * What theme an analyst actually BOOTS into — measured in Chromium.
 *
 * The sibling specs cover the parts around this one: theme-system.spec.ts pins
 * the architecture by reading source, and app-shell-theme-render.spec.ts proves
 * the shell is readable once a preference is already stored. Neither ran the
 * pre-paint script, so neither could catch the bug this spec exists for: the
 * default preference was 'system', so an analyst who had never opened the
 * Appearance control at all got whatever their desktop was set to — a dark
 * product nobody chose, with the Appearance control claiming a "System" choice
 * they never made.
 *
 * So this spec boots the REAL `THEME_INIT_SCRIPT` from <head>, ahead of the
 * stylesheet, exactly where layout.tsx puts it, and then mounts the REAL
 * ThemeProvider and ThemeToggle on top of it. It asserts three things per case:
 *
 *   1. the theme resolved BEFORE any stylesheet was reached (so no flash),
 *   2. React did not move it afterwards (so no hydration overwrite),
 *   3. the Appearance control shows the preference actually in force.
 */
import { expect, test, type Page } from '@playwright/test';

import { THEME_STORAGE_KEY, type ThemePreference } from '../app/theme-preference';
import { THEME_INIT_SCRIPT } from '../app/theme-script';
import { resolveChromium, startRenderHarness, type Harness } from './support/render-harness';

const chromiumExecutable = resolveChromium();
if (chromiumExecutable) test.use({ launchOptions: { executablePath: chromiumExecutable } });

/**
 * The real pre-paint script, then a probe that records what it left on <html>.
 *
 * Both run inside <head> before the <style> element, so whatever the probe
 * reads was true before the browser had a stylesheet to paint with. That is a
 * stronger no-flash proof than sampling a pixel after load, and it cannot flake.
 */
const HEAD = `<script>${THEME_INIT_SCRIPT}</script>
<script>
window.__prePaint = {
  theme: document.documentElement.getAttribute('data-theme'),
  preference: document.documentElement.getAttribute('data-theme-preference'),
};
</script>`;

/** The provider and the control the product actually ships, over a themed surface. */
const BOOTSTRAP = `
import React from '/vendor/react.js';
import { createRoot } from '/vendor/react-dom-client.js';
import { ThemeProvider } from '/app/theme-context.tsx';
import ThemeToggle from '/app/theme-toggle.tsx';

createRoot(document.getElementById('root')).render(
  React.createElement(ThemeProvider, null,
    React.createElement('div', { className: 'appShellContent' },
      React.createElement(ThemeToggle, null),
      React.createElement('article', { className: 'dataCard' },
        React.createElement('h3', null, 'Monitoring coverage')))));
`;

let harness: Harness;
test.beforeAll(async () => { harness = await startRenderHarness({ bootstrap: BOOTSTRAP, head: HEAD }); });
test.afterAll(async () => { await harness?.close(); });

function channel(v: number): number {
  const s = v / 255;
  return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}
function luminance(value: string): number {
  const [r, g, b] = value.match(/[\d.]+/g)!.map(Number);
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

/** Seed storage the way a returning browser would, then load the document. */
async function boot(page: Page, stored: string | null) {
  if (stored !== null) {
    await page.addInitScript(
      ([key, value]) => { try { window.localStorage.setItem(key, value); } catch { /* ignore */ } },
      [THEME_STORAGE_KEY, stored] as const,
    );
  }
  await page.goto(harness.url);
  await expect(page.locator('.themeToggle')).toBeVisible();
  expect(await page.evaluate(() => (window as any).__renderError)).toBeNull();
}

const prePaint = (page: Page) => page.evaluate(() => (window as any).__prePaint);
const stamped = (page: Page) =>
  page.evaluate(() => ({
    theme: document.documentElement.getAttribute('data-theme'),
    preference: document.documentElement.getAttribute('data-theme-preference'),
  }));

/* ── The matrix ───────────────────────────────────────────────────────────
   Six cases, the two that matter first: a NEW analyst gets Light whatever
   their desktop says. System is honoured only once it has been chosen. */
const MATRIX: ReadonlyArray<{
  stored: string | null;
  os: 'light' | 'dark';
  expected: 'light' | 'dark';
  preference: ThemePreference;
}> = [
  { stored: null, os: 'light', expected: 'light', preference: 'light' },
  { stored: null, os: 'dark', expected: 'light', preference: 'light' },
  { stored: 'light', os: 'dark', expected: 'light', preference: 'light' },
  { stored: 'dark', os: 'light', expected: 'dark', preference: 'dark' },
  { stored: 'system', os: 'light', expected: 'light', preference: 'system' },
  { stored: 'system', os: 'dark', expected: 'dark', preference: 'system' },
];

for (const { stored, os, expected, preference } of MATRIX) {
  const label = stored === null ? 'no stored preference' : `stored ${stored}`;

  test(`${label} + OS ${os} boots ${expected}`, async ({ page }) => {
    await page.emulateMedia({ colorScheme: os });
    await boot(page, stored);

    // 1. Resolved before the stylesheet: there is no frame to flash.
    expect(await prePaint(page), 'resolved before first paint').toEqual({ theme: expected, preference });

    // 2. And React left it alone. The provider reads the same storage through
    //    the same helper, so a disagreement here is a hydration overwrite.
    await expect.poll(() => stamped(page)).toEqual({ theme: expected, preference });

    // 3. What is painted matches the label, rather than merely being labelled.
    const painted = await page.locator('.appShellContent').evaluate((el) => getComputedStyle(el).backgroundColor);
    if (expected === 'light') expect(luminance(painted), painted).toBeGreaterThan(0.8);
    else expect(luminance(painted), painted).toBeLessThan(0.05);

    // 4. Appearance reports the preference in force, not a different one.
    const checked = page.getByRole('radiogroup', { name: 'Appearance' }).getByRole('radio', { checked: true });
    await expect(checked).toHaveCount(1);
    await expect(checked).toHaveAccessibleName(new RegExp(preference, 'i'));
  });

  test(`${label} + OS ${os} survives a hard refresh`, async ({ page }) => {
    // The full-document-load path: a hard refresh, and the sign-in redirect,
    // which lands on a new document rather than a client-side navigation.
    await page.emulateMedia({ colorScheme: os });
    await boot(page, stored);
    await page.reload();
    await expect(page.locator('.themeToggle')).toBeVisible();

    expect(await prePaint(page), 'still resolved before first paint').toEqual({ theme: expected, preference });
    await expect.poll(() => stamped(page)).toEqual({ theme: expected, preference });
  });
}

/* ── Legacy and storage hygiene ───────────────────────────────────────────
   The shell before this one hard-coded <html data-theme="dark"> and offered no
   control, so that dark was markup, never a stored choice. Nothing may quietly
   turn it back into one. */
test.describe('a preference exists only when the analyst made one', () => {
  test('booting does not write a preference into an untouched browser', async ({ page }) => {
    // If boot persisted its own default, every analyst would look like they had
    // chosen — and a later change to the default could never reach them.
    await page.emulateMedia({ colorScheme: 'dark' });
    await boot(page, null);

    const afterBoot = await page.evaluate(
      (key) => window.localStorage.getItem(key),
      THEME_STORAGE_KEY,
    );
    expect(afterBoot, 'boot must not populate the key').toBeNull();

    // An actual choice does persist, and is honoured on the next document.
    await page.getByRole('radiogroup', { name: 'Appearance' }).getByRole('radio', { name: 'Dark' }).click();
    await expect.poll(() => stamped(page)).toEqual({ theme: 'dark', preference: 'dark' });
    expect(await page.evaluate((key) => window.localStorage.getItem(key), THEME_STORAGE_KEY)).toBe('dark');

    await page.reload();
    expect(await prePaint(page)).toEqual({ theme: 'dark', preference: 'dark' });
  });

  test('a value this control cannot have written is not honoured as a choice', async ({ page }) => {
    // A legacy key, a half-written value, a leftover from a development build:
    // none of those are an explicit choice, so they must fail closed to the
    // default rather than being read as "dark".
    await page.emulateMedia({ colorScheme: 'dark' });
    await boot(page, 'DARK');

    expect(await prePaint(page)).toEqual({ theme: 'light', preference: 'light' });
    await expect.poll(() => stamped(page)).toEqual({ theme: 'light', preference: 'light' });
  });

  test('blocked storage still boots light rather than blank', async ({ page }) => {
    await page.emulateMedia({ colorScheme: 'dark' });
    await page.addInitScript(() => {
      Object.defineProperty(window, 'localStorage', {
        get() { throw new Error('storage blocked'); },
      });
    });
    await page.goto(harness.url);

    expect(await prePaint(page)).toEqual({ theme: 'light', preference: 'light' });
  });
});

/* ── The no-JavaScript path ───────────────────────────────────────────────
   With no script there is no way to read a preference, so the honest answer is
   the default. The CSS must not reach past that to the OS. */
test('a dark OS cannot theme the document without a stored choice', async ({ browser }) => {
  const context = await browser.newContext({ colorScheme: 'dark', javaScriptEnabled: false });
  try {
    const page = await context.newPage();
    await page.goto(harness.url);

    // `body` carries --bg-base; <html> itself paints nothing of its own.
    expect(await page.$eval('html', (el) => el.getAttribute('data-theme'))).toBeNull();
    const painted = await page.$eval('body', (el) => getComputedStyle(el).backgroundColor);
    const scheme = await page.$eval('html', (el) => getComputedStyle(el).colorScheme);
    expect(luminance(painted), `body background ${painted}`).toBeGreaterThan(0.8);
    expect(scheme).toBe('light');
  } finally {
    await context.close();
  }
});
