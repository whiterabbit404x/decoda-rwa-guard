/**
 * Landing-page motion — MEASURED, in a real browser.
 *
 * Why this spec exists
 * --------------------
 * The sibling spec (homepage-redesign.spec.ts) reads the stylesheet and the
 * components as text. It passed in full while the deployed page was, to a
 * visitor scrolling it, completely static — because the thing that was wrong
 * was never expressible as a string: the IntersectionObserver fired while the
 * section it belonged to was still two thirds below the fold, so every
 * sequence finished before it was looked at.
 *
 * So this spec mounts the UNMODIFIED landing sections in Chromium with the
 * real home.module.css applied, scrolls the page the way a reader does, and
 * measures what actually moves and when. Nothing here reimplements the
 * components or the CSS.
 *
 * What it pins:
 *   • a section is mostly ON SCREEN when its entrance starts
 *   • the four operating-layer pillars activate one at a time
 *   • the OBSERVE -> PROVE connector draws, and the nodes light in order
 *   • the hero workflow loop keeps exactly one step current, and it advances
 *   • reduced motion lands on the completed state with nothing hidden
 */
import fs from 'node:fs';
import path from 'node:path';
import { expect, test, type Page } from '@playwright/test';

import { resolveChromium, startRenderHarness, type Harness } from './support/render-harness';

const chromiumExecutable = resolveChromium();
if (chromiumExecutable) test.use({ launchOptions: { executablePath: chromiumExecutable } });

const VIEWPORT = { width: 1440, height: 900 };
test.use({ viewport: VIEWPORT });

const HOME_CSS = fs.readFileSync(
  path.join(__dirname, '..', 'app', 'components', 'home', 'home.module.css'),
  'utf-8',
);

/**
 * The landing sections that carry motion, under a `.page` root (the stylesheet
 * hangs every design token off it) and behind a full-viewport spacer, so each
 * section starts below the fold exactly as it does on the real page.
 */
const BOOTSTRAP = `
import React from '/vendor/react.js';
import { createRoot } from '/vendor/react-dom-client.js';
import { OperatingLayerSection } from '/app/components/home/operating-layer-section.tsx';
import { IncidentLifecycleSection } from '/app/components/home/incident-lifecycle-section.tsx';
import { IncidentWorkflowDemo } from '/app/components/home/incident-workflow-demo.tsx';

const spacer = (h) => React.createElement('div', { className: 'harness-spacer-' + h });

createRoot(document.getElementById('root')).render(
  React.createElement('div', { className: 'page' },
    React.createElement('div', { className: 'zoneDark hero' },
      React.createElement(IncidentWorkflowDemo)),
    spacer(1),
    React.createElement(OperatingLayerSection),
    spacer(2),
    React.createElement(IncidentLifecycleSection),
    spacer(3)));
`;

let harness: Harness;
test.beforeAll(async () => { harness = await startRenderHarness({ bootstrap: BOOTSTRAP }); });
test.afterAll(async () => { await harness?.close(); });

/** Tall spacers so every section below the hero starts off-screen. */
const LAYOUT_CSS = '[class^="harness-spacer-"] { height: 900px; } body { margin: 0; }';

async function mount(page: Page, options: { reducedMotion?: boolean } = {}) {
  if (options.reducedMotion) await page.emulateMedia({ reducedMotion: 'reduce' });
  // The stylesheet has to be in the document BEFORE React mounts. Adding it
  // afterwards leaves the spacers at zero height while the reveal effect runs,
  // so every section looks like it is already at the top of the page and is
  // revealed on the spot — which is exactly the state this spec must not be
  // measuring.
  await page.route(harness.url, async (route) => {
    const response = await route.fetch();
    const html = (await response.text())
      .replace('</head>', `<style>${HOME_CSS}</style><style>${LAYOUT_CSS}</style></head>`);
    await route.fulfill({ response, body: html });
  });
  await page.goto(harness.url);
  await page.waitForSelector('.pillar');
  expect(await page.evaluate(() => (window as unknown as { __renderError?: string }).__renderError ?? null)).toBeNull();
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.waitForTimeout(120);
}

/** Opacity of each element matching `selector`, rounded to two places. */
function opacities(page: Page, selector: string): Promise<number[]> {
  return page.evaluate(
    (sel) => Array.from(document.querySelectorAll(sel))
      .map((el) => Math.round(Number(getComputedStyle(el).opacity) * 100) / 100),
    selector,
  );
}

/** Horizontal scale of a connector fill (its `scaleX`, 0 → 1 as it draws). */
function drawn(page: Page, selector: string): Promise<number> {
  return page.evaluate((sel) => {
    const el = document.querySelector(sel);
    if (!el) return -1;
    const t = getComputedStyle(el).transform;
    if (t === 'none') return 1;
    return Number(t.replace(/matrix\(|\)/g, '').split(', ')[0]);
  }, selector);
}

/** Scroll `selector` to the position a reader stops at, and wait for the flip. */
async function scrollToRead(page: Page, selector: string) {
  await page.evaluate((sel) => document.querySelector(sel)!.scrollIntoView({ block: 'center' }), selector);
}

// ── 1. The regression: WHERE the entrance starts ──────────────────────
test.describe('the entrance starts where the reader is looking', () => {
  test('every reveal group is mostly on screen when it flips to "in"', async ({ page }) => {
    await mount(page);

    // Record the geometry of each group at the instant it is revealed.
    await page.evaluate(() => {
      (window as any).__flips = [];
      const observer = new MutationObserver((records) => {
        for (const record of records) {
          const el = record.target as HTMLElement;
          if (record.attributeName !== 'data-reveal' || el.getAttribute('data-reveal') !== 'in') continue;
          const rect = el.getBoundingClientRect();
          const onScreen = Math.max(0, Math.min(rect.bottom, window.innerHeight) - Math.max(rect.top, 0));
          (window as any).__flips.push({
            cls: el.className,
            share: rect.height > 0 ? onScreen / rect.height : 0,
            height: rect.height,
          });
        }
      });
      for (const el of Array.from(document.querySelectorAll('[data-reveal]'))) {
        observer.observe(el, { attributes: true, attributeFilter: ['data-reveal'] });
      }
    });

    // A reader's scroll, not a jump: 60px a frame down the whole page.
    const bottom = await page.evaluate(() => document.body.scrollHeight);
    for (let y = 0; y < bottom; y += 60) {
      await page.evaluate((to) => window.scrollTo(0, to), y);
      await page.waitForTimeout(16);
    }

    const flips: { cls: string; share: number; height: number }[] =
      await page.evaluate(() => (window as any).__flips);
    expect(flips.length, 'no group ever revealed').toBeGreaterThan(0);
    // The group this spec exists for must actually be among them: a section
    // revealed at mount instead of on scroll would otherwise skip the check.
    expect(flips.map((f) => f.cls).join(' '), 'the operating-layer sequence never revealed on scroll')
      .toContain('opStage');

    for (const flip of flips) {
      // The bug: the operating layer used to reveal with 63% of itself below
      // the fold, so its pillars and its lifecycle ribbon animated entirely
      // off-screen. Half of a group must be visible before it starts.
      expect(flip.share, `${flip.cls} started with only ${Math.round(flip.share * 100)}% on screen`)
        .toBeGreaterThanOrEqual(0.5);
    }
  });

  test('nothing below the fold is revealed before the reader gets there', async ({ page }) => {
    await mount(page);
    const staged = await page.evaluate(() => document.querySelectorAll('[data-reveal="out"]').length);
    expect(staged, 'the sections below the hero must start staged').toBeGreaterThan(0);
    // …and they must be genuinely hidden, not "staged" in name only.
    expect(await opacities(page, '.pillar')).toEqual([0, 0, 0, 0]);
  });
});

// ── 2. The operating layer is a sequence, not one fade ────────────────
test.describe('operating layer', () => {
  test('the four pillars activate one at a time', async ({ page }) => {
    await mount(page);
    await scrollToRead(page, '.opStage');

    // Sample across the whole card sequence and keep the frames where the
    // cards disagree: a simultaneous fade produces none.
    const frames: number[][] = [];
    for (let i = 0; i < 10; i += 1) {
      frames.push(await opacities(page, '.pillar'));
      await page.waitForTimeout(120);
    }

    const ordered = frames.filter((f) => f[0] > 0.9 && f[3] < 0.1);
    expect(ordered.length, `cards never separated: ${JSON.stringify(frames)}`).toBeGreaterThan(0);

    // At every sampled instant the cards are in left-to-right order.
    for (const frame of frames) {
      for (let i = 1; i < frame.length; i += 1) {
        expect(frame[i - 1], `card ${i} led card ${i - 1}: ${JSON.stringify(frame)}`)
          .toBeGreaterThanOrEqual(frame[i] - 0.02);
      }
    }
    // …and they all finish.
    await page.waitForTimeout(900);
    expect(await opacities(page, '.pillar')).toEqual([1, 1, 1, 1]);
  });

  test('the lifecycle line draws left to right and the nodes light in order', async ({ page }) => {
    await mount(page);
    await scrollToRead(page, '.opStage');

    const nodeFills = () => page.evaluate(() => Array.from(document.querySelectorAll('.ribbonNode'))
      .map((el) => getComputedStyle(el).backgroundColor));
    const inactive = await nodeFills();
    expect(new Set(inactive).size, 'every node starts in the same inactive state').toBe(1);

    const progress: number[] = [];
    const litAt: (number | null)[] = [null, null, null, null, null];
    for (let i = 0; i < 22; i += 1) {
      progress.push(await drawn(page, '.ribbonTrackFill'));
      const fills = await nodeFills();
      fills.forEach((fill, index) => {
        if (litAt[index] === null && fill !== inactive[index]) litAt[index] = i;
      });
      await page.waitForTimeout(150);
    }

    // The connector draws, monotonically, from nothing to fully drawn.
    expect(progress[0]).toBeLessThan(0.1);
    expect(Math.max(...progress)).toBeGreaterThan(0.98);
    for (let i = 1; i < progress.length; i += 1) {
      expect(progress[i], `the line went backwards at sample ${i}`).toBeGreaterThanOrEqual(progress[i - 1] - 0.01);
    }

    // Every node lights, and strictly in OBSERVE -> PROVE order.
    expect(litAt.every((at) => at !== null), `a node never activated: ${JSON.stringify(litAt)}`).toBe(true);
    for (let i = 1; i < litAt.length; i += 1) {
      expect(litAt[i]!, `node ${i} lit before node ${i - 1}: ${JSON.stringify(litAt)}`)
        .toBeGreaterThan(litAt[i - 1]!);
    }

    // PROVE settles green — the verified terminal state, not another blue dot.
    const settled = await nodeFills();
    expect(settled[4]).not.toBe(settled[0]);
    expect(settled[4]).toBe('rgb(21, 128, 61)');
  });
});

// ── 3. The incident lifecycle progresses stage by stage ───────────────
test('the incident lifecycle activates stage by stage as its connector advances', async ({ page }) => {
  await mount(page);
  await scrollToRead(page, '.lcFlow');

  const frames: { stages: number[]; line: number }[] = [];
  for (let i = 0; i < 16; i += 1) {
    frames.push({ stages: await opacities(page, '.lcStageWrap'), line: await drawn(page, '.lcTrackFill') });
    await page.waitForTimeout(150);
  }

  const separated = frames.filter((f) => f.stages[0] > 0.9 && f.stages[5] < 0.1);
  expect(separated.length, `stages never separated: ${JSON.stringify(frames)}`).toBeGreaterThan(0);
  expect(frames[frames.length - 1].stages).toEqual([1, 1, 1, 1, 1, 1]);
  expect(frames[frames.length - 1].line).toBeGreaterThan(0.98);

  // Each stage's ring takes its own tone only once it has been reached.
  const rings = await page.evaluate(() => Array.from(document.querySelectorAll('.lcStageIcon'))
    .map((el) => getComputedStyle(el).borderTopColor));
  expect(new Set(rings).size, 'every stage ended up the same colour').toBe(6);
  expect(rings).not.toContain('rgb(203, 213, 225)'); // --border-strong, the un-reached state
});

// ── 4. The hero loop keeps exactly one step current ───────────────────
test('the hero workflow loop advances a single current step', async ({ page }) => {
  await mount(page);

  const frames: number[][] = [];
  for (let i = 0; i < 24; i += 1) {
    frames.push(await opacities(page, '.wfRow'));
    await page.waitForTimeout(350);
  }

  // Three readable levels, not two: without a "completed" level every row
  // reaches full opacity and stays there, and the panel stops moving.
  const levels = new Set(frames.flat());
  expect(Math.max(...levels)).toBe(1);
  expect(Math.min(...levels)).toBeLessThan(0.5);
  expect([...levels].filter((l) => l > 0.5 && l < 1).length, 'no completed level').toBeGreaterThan(0);

  // The brightest row moves down the list over the loop.
  const current = frames.map((f) => f.indexOf(Math.max(...f)));
  expect(new Set(current).size, `the current step never moved: ${current.join(',')}`).toBeGreaterThanOrEqual(4);

  // The rail advances with it rather than sitting drawn.
  const rail = await page.evaluate(() => {
    const el = document.querySelector('.wfRailFill')!;
    return getComputedStyle(el).transform;
  });
  expect(rail).not.toBe('none');
});

// ── 5. Reduced motion: completed, never blank ─────────────────────────
test.describe('reduced motion', () => {
  test('no element is staged, and every sequence shows its completed state', async ({ page }) => {
    await mount(page, { reducedMotion: true });
    expect(await page.evaluate(() => window.matchMedia('(prefers-reduced-motion: reduce)').matches)).toBe(true);

    expect(await page.evaluate(() => document.querySelectorAll('[data-reveal]').length),
      'reduced motion must never stage an element').toBe(0);

    for (const selector of ['.pillar', '.lcStageWrap', '.wfRow', '.ribbonLabel']) {
      const values = await opacities(page, selector);
      expect(values.length).toBeGreaterThan(0);
      expect(values.every((o) => o === 1), `${selector} is hidden under reduced motion`).toBe(true);
    }

    // Connectors drawn, hero rail drawn, ribbon nodes all reached.
    expect(await drawn(page, '.lcTrackFill')).toBeGreaterThan(0.98);
    expect(await drawn(page, '.ribbonTrackFill')).toBeGreaterThan(0.98);
    const fills = await page.evaluate(() => Array.from(document.querySelectorAll('.ribbonNode'))
      .map((el) => getComputedStyle(el).backgroundColor));
    expect(fills).not.toContain('rgb(255, 255, 255)');
    expect(fills[4]).toBe('rgb(21, 128, 61)');
  });

  test('a visitor with no preference is never put into the static state', async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'no-preference' });
    await mount(page);
    expect(await page.evaluate(() => window.matchMedia('(prefers-reduced-motion: reduce)').matches)).toBe(false);
    expect(await page.evaluate(() => document.querySelectorAll('[data-reveal="out"]').length))
      .toBeGreaterThan(0);
  });
});
