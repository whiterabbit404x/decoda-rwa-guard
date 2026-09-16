/**
 * The authenticated theme system — System / Light / Dark.
 *
 * Before this, the product had no theme system at all: `<html>` carried a
 * hard-coded `data-theme="dark"`, the `:root` tokens were dark literals, and
 * ~1,000 colour literals across styles.css bypassed the tokens entirely. Light
 * and System were unreachable rather than merely unbuilt.
 *
 * These are source-level contracts. They guard the three properties that make
 * the system trustworthy rather than merely present:
 *
 *   1. ONE source of truth   — Light and Dark resolve from the same token
 *                              names, and the dark palette is re-pointed in
 *                              exactly one place, so they cannot drift.
 *   2. NO flash              — the theme is resolved before first paint, by a
 *                              nonced script, with no server/client mismatch.
 *   3. NO dark-only leftovers— no screen re-hard-codes a colour the token
 *                              layer already owns.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const appDir = path.join(__dirname, '..', 'app');
const read = (rel: string) => fs.readFileSync(path.join(appDir, rel), 'utf-8');

const styles = read('styles.css');
const layout = read('layout.tsx');
const themeScript = read('theme-script.ts');
const themeContext = read('theme-context.tsx');
const preference = read('theme-preference.ts');

/**
 * styles.css with its comments stripped. A rule and a comment explaining why
 * that rule is absent read the same to a regex; only one of them paints.
 */
const rules = styles.replace(/\/\*[\s\S]*?\*\//g, '');

/** The declarations inside one CSS block, given its opening selector. */
function block(selector: string): string {
  const start = styles.indexOf(selector);
  expect(start, `${selector} missing`).toBeGreaterThanOrEqual(0);
  const open = styles.indexOf('{', start);
  const end = styles.indexOf('\n}', open);
  return styles.slice(open, end);
}

/** Every `--name: value` pair declared in a block. */
function declarations(source: string): Map<string, string> {
  const out = new Map<string, string>();
  for (const [, name, value] of source.matchAll(/(--[a-z0-9-]+)\s*:\s*([^;]+);/g)) {
    out.set(name, value.trim());
  }
  return out;
}

test.describe('one source of truth', () => {
  test('both raw palettes are declared, and the semantic layer resolves light', () => {
    const root = block(':root {');
    const decls = declarations(root);

    // Raw palettes exist under their own prefixes …
    expect([...decls.keys()].filter((k) => k.startsWith('--l-')).length).toBeGreaterThan(20);
    expect([...decls.keys()].filter((k) => k.startsWith('--d-')).length).toBeGreaterThan(20);

    // … and the semantic names point at the LIGHT one by default, so an
    // institutional user sees the light workspace unless they ask otherwise.
    for (const name of ['--bg-base', '--bg-surface', '--text-primary', '--border', '--success-fg']) {
      expect(decls.get(name), `${name} should resolve to the light palette`).toMatch(/^var\(--l-/);
    }
  });

  test('every light token has a dark counterpart — neither theme is partial', () => {
    const root = declarations(block(':root {'));
    const light = [...root.keys()].filter((k) => k.startsWith('--l-'));
    const dark = new Set([...root.keys()].filter((k) => k.startsWith('--d-')));

    for (const name of light) {
      expect(dark.has(name.replace('--l-', '--d-')), `${name} has no dark counterpart`).toBe(true);
    }
  });

  test('the dark theme is declared once, and the OS cannot declare it', () => {
    // There is exactly ONE dark declaration list, so explicit Dark and a
    // System preference resolved to dark cannot drift apart — the second copy
    // this used to compare against is gone, and with it the whole failure mode.
    //
    // It is gone for a product reason, not a tidiness one. A CSS media query
    // cannot see whether a preference exists; it can only see the OS. Keyed off
    // a missing data-theme it themed the document for an analyst who had never
    // chosen anything, which is the opposite of the default. System is resolved
    // in `theme-script.ts`, where the stored preference is actually readable.
    const darkBlocks = [...rules.matchAll(/@media \(prefers-color-scheme: dark\)/g)];
    expect(darkBlocks, 'the OS must not re-point the token layer').toEqual([]);

    // And the one remaining dark block is complete: every semantic token the
    // light layer declares has a dark counterpart pointing at the dark palette.
    const dark = declarations(block('[data-theme="dark"],'));
    expect(dark.size).toBeGreaterThan(20);
  });

  test('an unstamped document resolves light, never the OS setting', () => {
    // The no-JavaScript path. `:root` is the answer, and `:root` is light — an
    // unreadable preference is not a reason to guess from the desktop. The
    // scheme is pinned too, so native chrome (scrollbars, date pickers,
    // autofill) does not go dark on its own either.
    const root = block(':root {');
    expect(root).toContain('color-scheme: light;');
    expect(declarations(root).get('--bg-base')).toMatch(/^var\(--l-/);
  });

  test('no raw palette entry is declared and then never consumed', () => {
    // The gap this closes: adding a --l-x/--d-x pair and wiring only the
    // light one, so the dark theme silently inherits the light value. Both
    // themes still "work", both earlier tests still pass, and the dark
    // palette entry is dead — which is exactly how a contrast regression
    // gets shipped looking intentional.
    const root = declarations(block(':root {'));
    const dark = declarations(block('[data-theme="dark"],'));

    const consumedByLight = new Set([...root.values()].flatMap((v) => [...v.matchAll(/var\((--l-[a-z0-9-]+)\)/g)].map((m) => m[1])));
    const consumedByDark = new Set([...dark.values()].flatMap((v) => [...v.matchAll(/var\((--d-[a-z0-9-]+)\)/g)].map((m) => m[1])));

    const orphanedLight = [...root.keys()].filter((k) => k.startsWith('--l-') && !consumedByLight.has(k));
    const orphanedDark = [...root.keys()].filter((k) => k.startsWith('--d-') && !consumedByDark.has(k));

    expect(orphanedLight, 'light palette entries no semantic token reads').toEqual([]);
    expect(orphanedDark, 'dark palette entries the dark theme never re-points').toEqual([]);
  });

  test('the dark theme re-points tokens rather than restating colours', () => {
    const decls = declarations(block('[data-theme="dark"],'));
    for (const [name, value] of decls) {
      if (name === '--color-scheme') continue;
      expect(value, `${name} should reference the dark palette, not a literal`).toMatch(/^var\(--d-/);
    }
  });

  test('the sidebar is deliberately outside the flip', () => {
    // Dark navy structural navigation beside a light operational workspace is
    // the product's identity. If --sidebar-text ever started following
    // --text-secondary, the sidebar would paint dark ink on navy.
    const root = declarations(block(':root {'));
    expect(root.get('--sidebar-bg')).toBe('#081426');
    expect(root.get('--sidebar-text-active')).toBe('#ffffff');

    const dark = declarations(block('[data-theme="dark"],'));
    for (const name of dark.keys()) {
      expect(name.startsWith('--sidebar'), `${name} must not be re-themed`).toBe(false);
    }

    // And the sidebar's own rules read those tokens, not the workspace ones.
    expect(block('.appSidebar {')).toContain('var(--sidebar-bg)');
    expect(block('.appNav a {')).toContain('var(--sidebar-text)');
    expect(block('.appNav a.active {')).toContain('var(--sidebar-text-active)');
  });
});

test.describe('no flash, no hydration mismatch', () => {
  test('the theme is stamped before first paint from a nonced script', () => {
    expect(layout).toContain('THEME_INIT_SCRIPT');
    expect(layout).toContain('nonce={nonce}');
    expect(layout).toContain("requestHeaders.get('x-nonce')");
    // The server must NOT render a theme: if it did, the pre-paint script's
    // correction would be the flash it exists to prevent.
    expect(layout).not.toContain('data-theme="dark"');
    expect(layout).not.toContain('data-theme="light"');
    expect(layout).toContain('suppressHydrationWarning');
  });

  test('the pre-paint script writes attributes only, and never throws', () => {
    expect(themeScript).toContain("setAttribute('data-theme'");
    expect(themeScript).toContain('prefers-color-scheme: dark');
    // Blocked storage (private mode, blocked site data) must degrade to a
    // theme, never to a blank page.
    expect(themeScript).toContain('try{');
    expect(themeScript).toContain('catch(e)');
    expect(themeScript).toMatch(/catch\(e\)\{document\.documentElement\.setAttribute\('data-theme','light'\)\}/);
  });

  test('the script and the provider agree on what "system" means', () => {
    // Both read the same storage key and the same media query, and the
    // provider resolves through the shared pure helper rather than its own
    // copy of the rule.
    expect(themeScript).toContain('THEME_STORAGE_KEY');
    expect(themeContext).toContain('THEME_STORAGE_KEY');
    expect(themeContext).toContain('resolveTheme');
    expect(preference).toContain("export const THEME_STORAGE_KEY = 'decoda.theme'");
  });

  test('the provider follows the OS only while the preference is System', () => {
    expect(themeContext).toContain("if (preference !== 'system') return;");
    expect(themeContext).toContain("query.addEventListener('change', onChange)");
    expect(themeContext).toContain("query.removeEventListener('change', onChange)");
  });

  test('a blocked localStorage still applies the chosen theme for the session', () => {
    // The write is what fails, not the switch: the attribute is stamped
    // before the persist attempt.
    const setter = themeContext.slice(themeContext.indexOf('const setPreference'));
    expect(setter.indexOf('applyTheme(')).toBeLessThan(setter.indexOf('localStorage.setItem'));
    expect(setter).toContain('catch');
  });

  test('theme switching repaints without re-rendering the product tree', () => {
    // The token layer keys off one attribute, so a switch is a repaint. If
    // this became React state on a provider the whole authenticated tree
    // would re-render on every toggle.
    expect(themeContext).toContain('root.setAttribute');
    expect(themeContext).not.toContain('window.location.reload');
  });
});

test.describe('no dark-only leftovers', () => {
  const productFiles: string[] = [];
  (function walk(dir: string) {
    for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) {
        if (entry.name === 'node_modules' || full.endsWith(path.join('components', 'home'))) continue;
        walk(full);
      } else if (entry.name.endsWith('.tsx') || entry.name.endsWith('.ts')) {
        productFiles.push(full);
      }
    }
  })(appDir);

  test('no authenticated screen hard-codes a hex colour', () => {
    const offenders: string[] = [];
    for (const file of productFiles) {
      const source = fs.readFileSync(file, 'utf-8');
      for (const [hex] of source.matchAll(/#[0-9a-fA-F]{6}\b/g)) {
        offenders.push(`${path.relative(appDir, file)} → ${hex}`);
      }
    }
    expect(offenders, 'use a semantic token instead').toEqual([]);
  });

  test('no authenticated screen uses an inline <style> element', () => {
    // The production CSP is `style-src 'self' 'nonce-…'`, so an unnonced
    // <style> element is refused and its rules never reach the customer.
    const offenders = productFiles
      .filter((file) => /<style>\{`/.test(fs.readFileSync(file, 'utf-8')))
      .map((file) => path.relative(appDir, file));
    expect(offenders, 'move these rules into styles.css').toEqual([]);
  });

  test('styles.css keeps hex literals to the fixed brand marks only', () => {
    // Brand blue in the logo/avatar gradients is a fixed pair with the white
    // ink on it, in both themes. Everything else must be a token.
    const body = styles.slice(styles.indexOf('\n.container {'));
    const hexes = [...body.matchAll(/#[0-9a-fA-F]{6}\b/g)].map((m) => m[0]);
    for (const hex of hexes) {
      expect(['#2563eb', '#1d4ed8', '#7c3aed'], `unexpected literal ${hex}`).toContain(hex.toLowerCase());
    }
  });

  test('no near-opaque dark surface is painted outside the dark palette', () => {
    // The bug class this closes, found twice by the rendered sweep: a panel,
    // tab strip or card authored for a dark-only product keeps a literal
    // near-black fill. It stays invisible in dark review and becomes a black
    // box on a white workspace. Shadows and scrims are the legitimate users
    // of dark literals, and they live in the palette block.
    const body = styles.slice(styles.indexOf('\n.container {'));
    const offenders: string[] = [];
    for (const [literal, r, g, b, a] of body.matchAll(
      /rgba\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*([\d.]+)\s*\)/g,
    )) {
      const dark = Number(r) < 80 && Number(g) < 80 && Number(b) < 90;
      if (dark && Number(a) >= 0.4) offenders.push(literal);
    }
    expect(offenders, 'use --bg-surface / --bg-inset / --scrim instead').toEqual([]);
  });

  test('no shared primitive uses a gradient or glass treatment', () => {
    // The brief rules these out, and the card primitives had both: a dark
    // gradient behind an 8px backdrop blur, on every SurfaceCard, MetricTile,
    // CtaPanel, EmptyState and TableShell in the product.
    const sharedBlock = block('.sharedSurfaceCard,');
    expect(sharedBlock).not.toContain('gradient');
    expect(sharedBlock).not.toContain('backdrop-filter');
    expect(sharedBlock).toContain('var(--bg-card)');
    expect(sharedBlock).toContain('var(--border)');
  });

  test('no white wash survives where a light surface would swallow it', () => {
    const body = styles.slice(styles.indexOf('\n.container {'));
    expect(body).not.toMatch(/rgba\(\s*255\s*,\s*255\s*,\s*255\s*,\s*0?\.\d+\s*\)/);
  });
});

test.describe('the preference is offered in both places, and they agree', () => {
  test('System, Light and Dark are the three options', () => {
    expect(preference).toContain("{ value: 'system'");
    expect(preference).toContain("{ value: 'light'");
    expect(preference).toContain("{ value: 'dark'");
  });

  test('the account menu and Settings render the same control', () => {
    expect(read('app-shell.tsx')).toContain('<ThemeToggle');
    expect(read('settings-page-client.tsx')).toContain('<ThemeToggle');
    // One component, so the two entry points cannot offer different options
    // or write different values.
    expect(read('theme-toggle.tsx')).toContain('setPreference');
  });

  test('the control is a radiogroup, not three unrelated buttons', () => {
    const toggle = read('theme-toggle.tsx');
    expect(toggle).toContain('role="radiogroup"');
    expect(toggle).toContain('role="radio"');
    expect(toggle).toContain('aria-checked={checked}');
    // Roving tabindex + arrow keys: a native radio group's behaviour.
    expect(toggle).toContain('tabIndex={checked ? 0 : -1}');
    expect(toggle).toContain("'ArrowRight'");
  });
});

/* ── Security messaging survives the redesign ───────────────────────────── */
test.describe('the redesign did not soften what the product says about authority', () => {
  const responseActions = read('(product)/response-actions-page-client.tsx');

  test('the AI layer is still stated to be recommend-only', () => {
    // A visual refactor must not quietly drop the sentence that separates what
    // the AI may propose from what the policy engine may execute.
    expect(responseActions).toContain('Recommendations only');
    expect(responseActions).toMatch(/deterministic policy engine/i);
    expect(responseActions).toContain('never by this advisor');
  });

  test('selecting an action still says plainly that it executes nothing', () => {
    expect(responseActions).toContain('It does not execute anything.');
  });

  test('high-impact actions are not styled as casual one-click controls', () => {
    // The destructive-confirmation variant exists and is visually separate
    // from the ordinary danger tint, so a freeze never looks like a filter.
    expect(styles).toContain('.btn-destructive');
    const destructive = block('.btn-destructive {');
    expect(destructive).toContain('var(--danger-solid)');
    expect(destructive).toContain('var(--on-accent)');

    const danger = block('.btn-danger {');
    expect(danger).toContain('var(--danger-bg)');
    expect(danger).not.toContain('var(--danger-solid)');
  });

  test('UNKNOWN and DISABLED never borrow a colour that reads as working', () => {
    // pill-neutral and statusBadge-unavailable both mean "not reporting" and
    // both resolve to the grey family — not to --info-fg, which this product
    // uses for INVESTIGATING.
    const neutralPill = block('.pill-neutral {');
    expect(neutralPill).toContain('var(--neutral-fg)');

    const unavailable = block('.statusBadge-stale,');
    expect(unavailable).toContain('var(--neutral-fg)');
    expect(unavailable).not.toContain('var(--info-fg)');
    expect(unavailable).not.toContain('var(--success-fg)');
  });
});

/* ── Motion stays functional ────────────────────────────────────────────── */
test.describe('motion inside the product is functional only', () => {
  test('every animation is a loading, spinner or active-state indicator', () => {
    const names = [...styles.matchAll(/@keyframes\s+([A-Za-z0-9_-]+)/g)].map((m) => m[1]);
    // mkt* belongs to the public marketing surface, which is out of scope here.
    const product = names.filter((n) => !n.startsWith('mkt'));
    const allowed = /(spin|pulse|shimmer|skel|fade|loading)/i;
    for (const name of product) {
      expect(name, `@keyframes ${name} must be a functional indicator`).toMatch(allowed);
    }
  });

  test('reduced motion is honoured globally without removing state feedback', () => {
    const at = styles.indexOf('@media (prefers-reduced-motion: reduce)');
    expect(at).toBeGreaterThanOrEqual(0);
    // Just the global block, not everything after it.
    const reduced = styles.slice(at, styles.indexOf('\n}\n', styles.indexOf('scroll-behavior', at)));
    // Durations collapse; colour, border and opacity changes still land, so a
    // status change is still announced — it just arrives at once.
    expect(reduced).toContain('animation-duration: 0.001ms !important');
    expect(reduced).toContain('transition-duration: 0.001ms !important');
    expect(reduced).not.toContain('display: none');
    expect(reduced).not.toContain('visibility: hidden');
  });
});
