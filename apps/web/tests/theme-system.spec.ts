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
 *                              names, and System-dark consumes the same raw
 *                              palette as explicit Dark, so they cannot drift.
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

  test('explicit Dark and System Dark set an identical declaration list', () => {
    // The failure this prevents is the classic one: a token added to
    // [data-theme="dark"] and forgotten in the media query, so the theme is
    // right when chosen and subtly wrong when inherited from the OS.
    const explicit = declarations(block('[data-theme="dark"],'));
    const system = declarations(
      block('@media (prefers-color-scheme: dark) {\n  :root:not([data-theme="light"]):not([data-theme="dark"]) {'),
    );

    expect([...system.keys()].sort()).toEqual([...explicit.keys()].sort());
    for (const [name, value] of explicit) {
      expect(system.get(name), `${name} differs between explicit and system dark`).toBe(value);
    }
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
