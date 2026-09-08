/**
 * Pilot "Give feedback" ▸ Feedback type — RENDERED, in a real browser.
 *
 * The sibling spec (plan-feedback-dropdown.spec.ts) pins the wiring by reading
 * the source. It cannot prove the thing the bug was actually about: what a
 * customer SEES when the dropdown is open. A native <select> passes every
 * source assertion and still hands its option list to the OS, which composited
 * `.planFeedbackInput`'s translucent `rgba(255,255,255,0.04)` surface over
 * white and left the dark theme's light option text unreadable.
 *
 * So this spec mounts the UNMODIFIED PlanBadge — through the real
 * PilotAuthProvider and PlanStatusProvider, with the real styles.css — in
 * Chromium, opens the menu, and measures the CONTRAST of every option against
 * the surface it is actually painted on. Nothing here is a hand-written copy of
 * the component; only fetch is stubbed, so the plan the chip reports is a KNOWN
 * API payload rather than invented UI state.
 */
import { expect, test, type Page } from '@playwright/test';

import { resolveChromium, startRenderHarness, type Harness } from './support/render-harness';

const chromiumExecutable = resolveChromium();
if (chromiumExecutable) test.use({ launchOptions: { executablePath: chromiumExecutable } });

const BOOTSTRAP = `
import React from '/vendor/react.js';
import { createRoot } from '/vendor/react-dom-client.js';
import { PilotAuthProvider } from '/app/pilot-auth-context.tsx';
import { PlanStatusProvider } from '/app/plan-status-context.tsx';
import PlanBadge from '/app/plan-badge.tsx';

// The chip lives at the right edge of the app shell header and its panel is
// anchored with right: 0, so the harness reproduces that placement — mounted
// flush against x=0 the panel would open off-screen and nothing would be
// clickable.
createRoot(document.getElementById('root')).render(
  React.createElement('div',
    { style: { display: 'flex', justifyContent: 'flex-end', padding: '1.5rem' } },
    React.createElement(PilotAuthProvider, null,
      React.createElement(PlanStatusProvider, null,
        React.createElement(PlanBadge, null)))));
`;

let harness: Harness;
test.beforeAll(async () => { harness = await startRenderHarness({ bootstrap: BOOTSTRAP }); });
test.afterAll(async () => { await harness?.close(); });

/** Mount the real plan chip with a routed fetch stub reporting an active pilot. */
async function mountFeedbackForm(page: Page) {
  await page.addInitScript(() => {
    const w = window as any;
    w.localStorage.setItem('decoda.accessToken', 'fixture-token');

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
            email: 'admin@acme.test', full_name: 'Acme Admin',
            current_workspace_id: 'fixture-ws',
            current_workspace: { id: 'fixture-ws', name: 'Acme Capital', slug: 'acme' },
            memberships: [{ workspace: { id: 'fixture-ws', name: 'Acme Capital' }, role: 'admin' }],
          },
        });
      }
      if (p === '/api/account/plan') {
        return json({
          state: 'available',
          organization: { id: 'org-1', name: 'Acme Capital', slug: 'acme' },
          plan: 'pilot', plan_label: 'Pilot', status: 'active', lifecycle_state: 'ACTIVE_PILOT',
          evaluation: { started_at: null, expires_at: '2026-12-24T00:00:00Z', days_remaining: 23, expired: false },
          usage: {
            workspaces: { current: 1, limit: 1 },
            monitored_contracts: { current: 3, limit: 5 },
            evidence_packages: { current: 4, limit: 10 },
          },
          entitlements: { automatic_execution: false, ai_investigation: true },
        });
      }
      return json({}, 404);
    };
  });

  await page.goto(harness.url);
  await expect(page.locator('.planBadge')).toBeVisible();
  expect(await page.evaluate(() => (window as any).__renderError)).toBeNull();

  await page.locator('.planBadge').click();
  await page.getByRole('button', { name: 'Give feedback' }).click();
  await expect(page.getByTestId('plan-feedback-type')).toBeVisible();
}

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
function contrast(fg: string, bg: string): number {
  const a = luminance(parseRgb(fg));
  const b = luminance(parseRgb(bg));
  const [hi, lo] = a > b ? [a, b] : [b, a];
  return (hi + 0.05) / (lo + 0.05);
}
/** Alpha-composite a possibly translucent colour over an opaque backdrop. */
function over(colour: string, backdrop: number[]): number[] {
  const parts = colour.match(/[\d.]+/g)!.map(Number);
  const alpha = parts.length > 3 ? parts[3] : 1;
  return [0, 1, 2].map((i) => alpha * parts[i] + (1 - alpha) * backdrop[i]);
}

test.describe('the Feedback type dropdown is readable in dark mode', () => {
  test('no native select hands the option list to the OS', async ({ page }) => {
    await mountFeedbackForm(page);
    expect(await page.locator('.planFeedback select').count()).toBe(0);
    await expect(page.getByTestId('plan-feedback-type')).toHaveAttribute('role', 'combobox');
  });

  test('the opened menu is an opaque dark surface, never a white popup', async ({ page }) => {
    await mountFeedbackForm(page);
    await page.getByTestId('plan-feedback-type').click();

    const menu = page.getByRole('listbox');
    await expect(menu).toBeVisible();

    const background = await menu.evaluate((el) => getComputedStyle(el).backgroundColor);
    const rgb = parseRgb(background);
    // Opaque (an alpha channel would let the page behind bleed through) …
    expect(over(background, [255, 255, 255])).toEqual(rgb);
    // … and dark: the Decoda popover surface, not the OS white the bug showed.
    expect(luminance(rgb)).toBeLessThan(0.05);
  });

  test('every option label clears WCAG AA against the menu it is painted on', async ({ page }) => {
    await mountFeedbackForm(page);
    await page.getByTestId('plan-feedback-type').click();

    const menu = page.getByRole('listbox');
    const menuBg = parseRgb(await menu.evaluate((el) => getComputedStyle(el).backgroundColor));

    const options = page.getByRole('option');
    // The five labels named in the bug report plus Security.
    await expect(options).toHaveCount(6);

    for (const label of ['Security', 'Detection accuracy', 'Usability', 'Missing feature', 'Integration', 'Other']) {
      const option = page.getByRole('option', { name: new RegExp(`^${label}`) });
      const { colour, rowBg } = await option.evaluate((el) => {
        const style = getComputedStyle(el);
        return { colour: style.color, rowBg: style.backgroundColor };
      });
      // A row may tint itself (hover / selected); measure over that tint.
      const painted = over(rowBg, menuBg);
      const ratio = contrast(colour, `rgb(${painted.join(',')})`);
      expect(ratio, `${label} contrast`).toBeGreaterThanOrEqual(4.5);
    }
  });

  test('selected, hovered and focused states are each visibly distinct', async ({ page }) => {
    await mountFeedbackForm(page);
    const trigger = page.getByTestId('plan-feedback-type');

    // Focus ring on the trigger is visible (keyboard users can see where they
    // are). Reached by keyboard — Shift+Tab back from the message field — so
    // :focus-visible genuinely matches, as it would not on a scripted focus().
    await page.locator('#plan-feedback-message').focus();
    await page.keyboard.press('Shift+Tab');
    await expect(trigger).toBeFocused();
    const outline = await trigger.evaluate((el) => {
      const style = getComputedStyle(el);
      return { width: style.outlineWidth, style: style.outlineStyle, colour: style.outlineColor };
    });
    expect(outline.style).not.toBe('none');
    expect(parseFloat(outline.width)).toBeGreaterThan(0);
    // The ring must be visible against the panel it sits on.
    const panelBg = await page.locator('.planPanel').evaluate((el) => getComputedStyle(el).backgroundColor);
    expect(contrast(outline.colour, panelBg)).toBeGreaterThanOrEqual(3);

    await trigger.click();
    const selected = page.locator('[role="option"][aria-selected="true"]');
    await expect(selected).toHaveCount(1);
    await expect(selected).toContainText('Usability');

    const plain = page.getByRole('option', { name: /^Other/ });
    const restingBg = await plain.evaluate((el) => getComputedStyle(el).backgroundColor);
    await plain.hover();
    const hoverBg = await plain.evaluate((el) => getComputedStyle(el).backgroundColor);
    expect(hoverBg).not.toBe(restingBg);
  });

  test('the menu is fully keyboard navigable and commits the backend value', async ({ page }) => {
    await mountFeedbackForm(page);
    const trigger = page.getByTestId('plan-feedback-type');
    await trigger.focus();

    // Opens on ArrowDown, moves, and commits on Enter.
    await page.keyboard.press('ArrowDown');
    await expect(page.getByRole('listbox')).toBeVisible();
    await expect(trigger).toHaveAttribute('aria-expanded', 'true');
    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('Enter');
    await expect(page.getByRole('listbox')).toHaveCount(0);
    await expect(trigger).toContainText('Missing feature');

    // Escape closes without changing the committed value, focus returns.
    await page.keyboard.press('ArrowDown');
    await expect(page.getByRole('listbox')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.getByRole('listbox')).toHaveCount(0);
    await expect(trigger).toContainText('Missing feature');
    await expect(trigger).toBeFocused();

    // Home jumps to the first option; the value posted stays a backend enum.
    await page.keyboard.press('Home');
    await page.keyboard.press('Enter');
    await expect(trigger).toContainText('Security');
  });

  test('the trigger names itself from the visible Feedback type label', async ({ page }) => {
    await mountFeedbackForm(page);
    const trigger = page.getByTestId('plan-feedback-type');
    await expect(trigger).toHaveAttribute('aria-labelledby', 'plan-feedback-type-label');
    await expect(trigger).toHaveAttribute('aria-haspopup', 'listbox');
    await expect(page.locator('#plan-feedback-type-label')).toHaveText('Feedback type');
  });
});
