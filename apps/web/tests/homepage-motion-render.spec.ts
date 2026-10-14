/**
 * Public homepage motion — MEASURED, in a real browser.
 *
 * Why this spec exists
 * --------------------
 * The sibling spec (homepage-redesign.spec.ts) reads the stylesheets and the
 * components as text. Motion bugs are rarely expressible as a string: an
 * IntersectionObserver that fires while a section is still below the fold
 * passes every source assertion and still plays the whole entrance off
 * screen. So this spec mounts the UNMODIFIED homepage sections in Chromium
 * with the real public stylesheets applied, scrolls the page the way a reader
 * does, and measures what actually moves and when. Nothing here reimplements
 * the components or the CSS.
 *
 * What it pins:
 *   • a block is mostly ON SCREEN when its entrance starts, and nothing below
 *     the fold is revealed early
 *   • the hero console advances one stage at a time, holds on the sealed
 *     evidence, loops, and pauses while it is off screen
 *   • the "How Guard works" story follows the step the reader is on
 *   • the product tour is operable from the keyboard
 *   • reduced motion renders the finished page: nothing staged, the console
 *     on its completed state
 */
import fs from 'node:fs';
import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';

import { resolveChromium, startRenderHarness, type Harness } from './support/render-harness';

const chromiumExecutable = resolveChromium();
if (chromiumExecutable) test.use({ launchOptions: { executablePath: chromiumExecutable } });

const VIEWPORT = { width: 1440, height: 900 };
test.use({ viewport: VIEWPORT });

const HOME_DIR = path.join(__dirname, '..', 'app', 'components', 'home');
/**
 * The public stylesheets, raw. The harness maps every CSS-module class to its
 * own name, so `.reveal`, `.panel` and the rest match exactly as written.
 * (`:global(...)` rules are not valid outside the bundler and drop out, which
 * only removes the html/body light lock and smooth scrolling.)
 */
const PUBLIC_CSS = ['site', 'home', 'console', 'story', 'showcase']
  .map((name) => fs.readFileSync(path.join(HOME_DIR, `${name}.module.css`), 'utf-8'))
  .join('\n');

/** Step timing of the hero console (guard-console.tsx). */
const STEP_MS = 2800;
const HOLD_MS = 4600;

/**
 * The homepage sections that carry motion, under the `.site` root that holds
 * every design token, in the order the page renders them.
 */
const BOOTSTRAP = `
import React from '/vendor/react.js';
import { createRoot } from '/vendor/react-dom-client.js';
import { HeroSection } from '/app/components/home/hero-section.tsx';
import { ProblemSection } from '/app/components/home/problem-section.tsx';
import { PlatformSection } from '/app/components/home/platform-section.tsx';
import { HowItWorksSection } from '/app/components/home/how-it-works-section.tsx';
import { ProductSection } from '/app/components/home/product-section.tsx';

createRoot(document.getElementById('root')).render(
  React.createElement('div', { className: 'site' },
    React.createElement('main', null,
      React.createElement(HeroSection),
      React.createElement(ProblemSection),
      React.createElement(PlatformSection),
      React.createElement(HowItWorksSection),
      React.createElement(ProductSection))));
`;

let harness: Harness;
test.beforeAll(async () => { harness = await startRenderHarness({ bootstrap: BOOTSTRAP }); });
test.afterAll(async () => { await harness?.close(); });

async function mount(page: Page, options: { reducedMotion?: boolean; clock?: boolean } = {}) {
  if (options.reducedMotion) await page.emulateMedia({ reducedMotion: 'reduce' });
  if (options.clock) await page.clock.install();
  // The stylesheets must be in the document BEFORE React mounts: the reveal
  // decides "already on screen" against the real layout at mount time.
  await page.route(harness.url, async (route) => {
    const response = await route.fetch();
    const html = (await response.text()).replace('</head>', `<style>${PUBLIC_CSS}</style></head>`);
    await route.fulfill({ response, body: html });
  });
  await page.goto(harness.url);
  await page.waitForSelector('#hero-title');
  expect(await page.evaluate(() => (window as unknown as { __renderError?: string }).__renderError ?? null)).toBeNull();
}

/** Opacity of each element matching `selector`, rounded to two places. */
function opacities(page: Page, selector: string): Promise<number[]> {
  return page.evaluate(
    (sel) => Array.from(document.querySelectorAll(sel))
      .map((el) => Math.round(Number(getComputedStyle(el).opacity) * 100) / 100),
    selector,
  );
}

/** The console's state: its step, which stage is active, which panel shows. */
function consoleState(page: Page) {
  return page.evaluate(() => {
    const figure = document.querySelector('.figure')!;
    const stages = Array.from(figure.querySelectorAll('.stages > li')).map((li) => li.getAttribute('data-state'));
    const panels = Array.from(figure.querySelectorAll('.panel')).map((panel) => panel.hasAttribute('data-active'));
    return {
      step: figure.getAttribute('data-step'),
      live: figure.getAttribute('data-live'),
      stages,
      activePanels: panels.filter(Boolean).length,
      activePanel: panels.indexOf(true),
    };
  });
}

// ── 1. Reveals start where the reader is looking ──────────────────────
test.describe('the entrance starts where the reader is looking', () => {
  test('every revealed block is mostly on screen when it flips to "in"', async ({ page }) => {
    await mount(page);
    await page.evaluate(() => {
      (window as any).__flips = [];
      const observer = new MutationObserver((records) => {
        for (const record of records) {
          const el = record.target as HTMLElement;
          if (record.attributeName !== 'data-reveal' || el.getAttribute('data-reveal') !== 'in') continue;
          const rect = el.getBoundingClientRect();
          const onScreen = Math.max(0, Math.min(rect.bottom, window.innerHeight) - Math.max(rect.top, 0));
          (window as any).__flips.push({ cls: el.className, share: rect.height > 0 ? onScreen / rect.height : 0, onScreen });
        }
      });
      for (const el of Array.from(document.querySelectorAll('[data-reveal]'))) {
        observer.observe(el, { attributes: true, attributeFilter: ['data-reveal'] });
      }
    });

    // A reader's scroll, not a jump: 60px a frame down the whole page.
    const bottom = await page.evaluate(() => document.body.scrollHeight);
    for (let y = 0; y < bottom; y += 60) {
      await page.evaluate((to) => window.scrollTo({ top: to, behavior: 'instant' }), y);
      await page.waitForTimeout(16);
    }

    const flips: { cls: string; share: number; onScreen: number }[] = await page.evaluate(() => (window as any).__flips);
    expect(flips.length, 'no block ever revealed on scroll').toBeGreaterThan(3);
    expect(flips.map((f) => f.cls).join(' ')).toContain('riskGrid');
    expect(flips.map((f) => f.cls).join(' ')).toContain('capGrid');
    for (const flip of flips) {
      // The bug class this guards: an observer firing while a section is
      // still mostly below the fold (it once fired with 63% of a section
      // unseen). A block starts with at least 40% of itself on screen — or,
      // for one taller than the screen, a third of the screen's worth of it.
      const fair = flip.share >= 0.4 || flip.onScreen >= VIEWPORT.height * 0.35;
      expect(fair, `${flip.cls} started with only ${Math.round(flip.share * 100)}% on screen`).toBe(true);
    }
    // Every staged block was eventually revealed.
    expect(await page.evaluate(() => document.querySelectorAll('[data-reveal="out"]').length)).toBe(0);
  });

  test('nothing below the fold is revealed before the reader gets there', async ({ page }) => {
    await mount(page);
    const staged = await page.evaluate(() => document.querySelectorAll('[data-reveal="out"]').length);
    expect(staged, 'the sections below the hero must start staged').toBeGreaterThan(0);
    // …and they are genuinely hidden, not "staged" in name only.
    const risks = await opacities(page, '.riskGrid > li');
    expect(risks.length).toBe(5);
    expect(risks.every((o) => o === 0)).toBe(true);
    // The hero itself never waits: headline and calls to action are visible.
    expect(await opacities(page, '#hero-title')).toEqual([1]);
  });
});

// ── 2. The hero console ───────────────────────────────────────────────
test.describe('the hero console', () => {
  test('advances one stage at a time, holds on the sealed evidence, then loops', async ({ page }) => {
    await mount(page, { clock: true });

    // After mount the loop restarts from Detect.
    let state = await consoleState(page);
    expect(state.step).toBe('0');
    expect(state.live).toBe('true');

    for (let step = 0; step <= 4; step += 1) {
      state = await consoleState(page);
      expect(state.step).toBe(String(step));
      // Exactly one stage is current and exactly one panel shows: the earlier
      // stages read as done, the later ones as still to come.
      expect(state.stages.filter((s) => s === 'active')).toHaveLength(1);
      expect(state.stages.indexOf('active')).toBe(step);
      expect(state.stages.slice(0, step).every((s) => s === 'done')).toBe(true);
      expect(state.stages.slice(step + 1).every((s) => s === 'todo')).toBe(true);
      expect(state.activePanels).toBe(1);
      expect(state.activePanel).toBe(step);
      if (step < 4) await page.clock.runFor(STEP_MS);
    }

    // The sealed evidence is held longer than a step before the loop resets.
    await page.clock.runFor(STEP_MS);
    expect((await consoleState(page)).step).toBe('4');
    await page.clock.runFor(HOLD_MS - STEP_MS);
    expect((await consoleState(page)).step).toBe('0');
  });

  test('pauses while it is off screen', async ({ page }) => {
    await mount(page, { clock: true });
    await page.clock.runFor(STEP_MS);
    expect((await consoleState(page)).step).toBe('1');

    // Scroll the console out of view; the observer reports it hidden.
    await page.evaluate(() => window.scrollTo({ top: 4000, behavior: 'instant' }));
    await page.waitForFunction(() => document.querySelector('.figure')!.getBoundingClientRect().bottom < 0);
    await page.waitForTimeout(100);
    await page.clock.runFor(STEP_MS * 3);
    expect((await consoleState(page)).step, 'the loop kept running off screen').toBe('1');

    // Back in view, it carries on from where it stopped.
    await page.evaluate(() => window.scrollTo({ top: 0, behavior: 'instant' }));
    await page.waitForTimeout(100);
    await page.clock.runFor(STEP_MS);
    expect((await consoleState(page)).step).toBe('2');
  });
});

// ── 3. The lifecycle story follows the reader ─────────────────────────
test('the "How Guard works" story activates the step the reader is on', async ({ page }) => {
  await mount(page);
  const steps = await page.evaluate(() => document.querySelectorAll('.story .steps > li').length);
  expect(steps).toBe(5);

  for (const index of [0, 2, 4, 1]) {
    await page.evaluate((i) => {
      document.querySelectorAll('.story .steps > li')[i]!.scrollIntoView({ block: 'center', behavior: 'instant' });
    }, index);
    await expect
      .poll(() => page.evaluate(() => document.querySelector('.story')!.getAttribute('data-progress')))
      .toBe(String(index));
    const states = await page.evaluate(() => Array.from(document.querySelectorAll('.story .steps > li')).map((li) => li.getAttribute('data-state')));
    expect(states[index]).toBe('active');
    expect(states.filter((s) => s === 'active')).toHaveLength(1);
    // The sticky illustration shows the same step, and only that one.
    const panels = await page.evaluate(() => Array.from(document.querySelectorAll('.stagePanel')).map((p) => p.hasAttribute('data-active')));
    expect(panels.indexOf(true)).toBe(index);
    expect(panels.filter(Boolean)).toHaveLength(1);
  }

  // Inactive steps change colour, never opacity: every paragraph stays legible.
  expect((await opacities(page, '.story .stepBody')).every((o) => o === 1)).toBe(true);
});

// ── 4. The product tour is operable from the keyboard ─────────────────
test('the product tour moves with the arrow keys, Home and End', async ({ page }) => {
  await mount(page);
  const selected = () => page.evaluate(() => {
    const tab = document.querySelector('[role="tab"][aria-selected="true"]');
    return { label: tab?.textContent?.trim(), focused: document.activeElement === tab };
  });

  await page.locator('[role="tab"]').first().focus();
  expect((await selected()).label).toBe('Dashboard');

  await page.keyboard.press('ArrowDown');
  expect(await selected()).toEqual({ label: 'Asset Risk', focused: true });
  await expect(page.locator('[role="tabpanel"]')).toContainText('Asset Risk');

  await page.keyboard.press('End');
  expect(await selected()).toEqual({ label: 'System Health', focused: true });

  await page.keyboard.press('ArrowDown');
  expect((await selected()).label, 'the tab list wraps around').toBe('Dashboard');

  await page.keyboard.press('ArrowUp');
  expect((await selected()).label).toBe('System Health');

  await page.keyboard.press('Home');
  expect(await selected()).toEqual({ label: 'Dashboard', focused: true });

  // Only the selected tab is in the tab order.
  const tabIndexes = await page.evaluate(() => Array.from(document.querySelectorAll('[role="tab"]')).map((t) => t.getAttribute('tabindex')));
  expect(tabIndexes.filter((t) => t === '0')).toHaveLength(1);
});

// ── 5. Reduced motion: finished, never blank ──────────────────────────
test.describe('reduced motion', () => {
  test('nothing is staged and the console shows its completed workflow', async ({ page }) => {
    await mount(page, { reducedMotion: true, clock: true });
    expect(await page.evaluate(() => window.matchMedia('(prefers-reduced-motion: reduce)').matches)).toBe(true);

    expect(await page.evaluate(() => document.querySelectorAll('[data-reveal]').length),
      'reduced motion must never stage an element').toBe(0);
    for (const selector of ['.riskGrid > li', '.capGrid > *', '.principles > li']) {
      const values = await opacities(page, selector);
      expect(values.length, selector).toBeGreaterThan(0);
      expect(values.every((o) => o === 1), `${selector} is hidden under reduced motion`).toBe(true);
    }

    // The console never starts its loop: it stays on the sealed evidence.
    await page.clock.runFor(STEP_MS * 4);
    const state = await consoleState(page);
    expect(state.step).toBe('4');
    expect(state.live).toBeNull();
    expect(state.stages).toEqual(['done', 'done', 'done', 'done', 'active']);
    expect(await opacities(page, '.float')).toEqual([1, 1]);
  });

  test('a visitor with no preference gets the entrances', async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    await mount(page);
    expect(await page.evaluate(() => document.querySelectorAll('[data-reveal="out"]').length)).toBeGreaterThan(0);
  });
});
