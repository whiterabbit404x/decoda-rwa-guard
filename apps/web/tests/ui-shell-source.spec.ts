/**
 * Source-level (static-analysis) tests for the shared UI shell.
 *
 * These run without a browser — they read source files and assert structural
 * contracts: sidebar order, banner presence, component exports, and that no
 * page claims healthy/live status without backend truth.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const appDir = path.join(__dirname, '..', 'app');

function read(rel: string): string {
  return fs.readFileSync(path.join(appDir, rel), 'utf-8');
}

// ── Sidebar order ────────────────────────────────────────────────
test('sidebar nav order matches exact spec', () => {
  const src = read('product-nav.ts');

  const labelMatches = [...src.matchAll(/label:\s*'([^']+)'/g)].map((m) => m[1]);
  expect(labelMatches).toEqual([
    'Getting Started',
    'Dashboard',
    'Assets',
    'Monitoring Sources',
    'Threat Monitoring',
    'Alerts',
    'Incidents',
    'Response Actions',
    'Evidence & Audit',
    'Integrations',
    'Settings',
    'System Health',
  ]);
});

test('sidebar nav hrefs match spec', () => {
  const src = read('product-nav.ts');

  const hrefMatches = [...src.matchAll(/href:\s*'([^']+)'/g)].map((m) => m[1]);
  expect(hrefMatches).toEqual([
    '/onboarding',
    '/dashboard',
    '/assets',
    '/monitoring-sources',
    '/threat',
    '/alerts',
    '/incidents',
    '/response-actions',
    '/evidence',
    '/integrations',
    '/settings',
    '/system-health',
  ]);
});

test('sidebar has exactly 12 nav items', () => {
  const src = read('product-nav.ts');
  const count = [...src.matchAll(/href:/g)].length;
  expect(count).toBe(12);
});

// ── Runtime banner on protected pages ───────────────────────────
test('app-shell renders RuntimeBanner component', () => {
  const shell = read('app-shell.tsx');
  expect(shell).toContain('RuntimeBanner');
  expect(shell).toContain("import RuntimeBanner from './components/runtime-banner'");
});

test('runtime banner shows all required fields', () => {
  const banner = read('components/runtime-banner.tsx');

  // All 7 required labels must appear as string literals in Field calls
  expect(banner).toContain('Monitoring');
  expect(banner).toContain('Freshness');
  expect(banner).toContain('Confidence');
  expect(banner).toContain('Telemetry');
  expect(banner).toContain('Heartbeat');
  expect(banner).toContain('Poll');
  expect(banner).toContain('Next action');
});

test('runtime banner guards against false healthy claims', () => {
  const banner = read('components/runtime-banner.tsx');

  // Must check all conditions before claiming healthy/live
  expect(banner).toContain('hasLiveTelemetry(summary)');
  expect(banner).toContain('hasRealTelemetryBackedChain(summary)');
  expect(banner).toContain('healthProvable');
  // Must show a warning when health cannot be proven
  expect(banner).toContain('Live/healthy display disabled until telemetry verified');
});

test('runtime banner is placed inside the header in app-shell', () => {
  const shell = read('app-shell.tsx');
  // RuntimeBanner must appear after the <header> tag open
  const headerStart = shell.indexOf('<header');
  const headerEnd = shell.indexOf('</header>');
  const bannerIdx = shell.indexOf('RuntimeBanner', headerStart);
  expect(headerStart).toBeGreaterThan(-1);
  expect(bannerIdx).toBeGreaterThan(headerStart);
  expect(bannerIdx).toBeLessThan(headerEnd);
});

// ── Shared components exports ────────────────────────────────────
test('ui-primitives exports all required primitives', () => {
  const src = read('components/ui-primitives.tsx');

  expect(src).toContain('export function SurfaceCard');
  expect(src).toContain('export function MetricTile');
  expect(src).toContain('export function StatusPill');
  expect(src).toContain('export function TableShell');
  expect(src).toContain('export function EmptyStateBlocker');
  expect(src).toContain('export function TabStrip');
  expect(src).toContain('export function Button');
  expect(src).toContain('export function LinkButton');
  expect(src).toContain('export function StepRail');
});

test('StatusPill supports variant prop for semantic colours', () => {
  const src = read('components/ui-primitives.tsx');
  expect(src).toContain("'success'");
  expect(src).toContain("'warning'");
  expect(src).toContain("'danger'");
  expect(src).toContain("'info'");
  expect(src).toContain("'neutral'");
});

test('Button component uses variant-based CSS classes', () => {
  const src = read('components/ui-primitives.tsx');
  // Button uses template literal to compose class names from variant
  expect(src).toContain('btn-${variant}');
  // And the supported variant union is declared
  expect(src).toContain("'primary'");
  expect(src).toContain("'secondary'");
  expect(src).toContain("'ghost'");
  expect(src).toContain("'danger'");
});

// ── Nav icons ────────────────────────────────────────────────────
test('nav-icons defines an icon export for every nav href', () => {
  const src = read('nav-icons.tsx');

  const hrefs = [
    '/onboarding',
    '/dashboard',
    '/assets',
    '/monitoring-sources',
    '/threat',
    '/alerts',
    '/incidents',
    '/response-actions',
    '/evidence',
    '/integrations',
    '/settings',
    '/system-health',
  ];

  for (const href of hrefs) {
    expect(src, `Missing icon mapping for ${href}`).toContain(`'${href}'`);
  }
});

test('app-navigation imports NAV_ICONS and renders SVG icons', () => {
  const src = read('app-navigation.tsx');
  expect(src).toContain("import { NAV_ICONS } from './nav-icons'");
  expect(src).toContain('NavIcon');
  expect(src).toContain('appNavIcon');
});

// ── Dark theme tokens ─────────────────────────────────────────────
test('styles.css defines required CSS custom properties', () => {
  const css = read('styles.css');

  expect(css).toContain('--bg-base');
  expect(css).toContain('--bg-sidebar');
  expect(css).toContain('--bg-card');
  expect(css).toContain('--border');
  expect(css).toContain('--accent-blue');
  expect(css).toContain('--success-fg');
  expect(css).toContain('--warning-fg');
  expect(css).toContain('--danger-fg');
  expect(css).toContain('--sidebar-w');
});

test('styles.css defines .btn-primary and .btn-secondary', () => {
  const css = read('styles.css');
  expect(css).toContain('.btn-primary');
  expect(css).toContain('.btn-secondary');
  expect(css).toContain('.btn-ghost');
  expect(css).toContain('.btn-danger');
});

test('styles.css defines status pill colour variants', () => {
  const css = read('styles.css');
  expect(css).toContain('.pill-success');
  expect(css).toContain('.pill-warning');
  expect(css).toContain('.pill-danger');
});

test('styles.css defines compact table class', () => {
  const css = read('styles.css');
  expect(css).toContain('.tableCompact');
});

test('styles.css defines shell header bar classes', () => {
  const css = read('styles.css');
  expect(css).toContain('.shellHeaderBar');
  expect(css).toContain('.shellWorkspaceSelector');
  expect(css).toContain('.shellUserChip');
  expect(css).toContain('.shellAvatar');
});

// ── No fake healthy states ───────────────────────────────────────
test('app-shell does not hard-code healthy or live status strings', () => {
  const shell = read('app-shell.tsx');
  // Must not contain literal hardcoded status messages
  expect(shell).not.toMatch(/>\s*(Healthy|System Healthy|All systems operational)\s*</);
  expect(shell).not.toMatch(/status[=:]\s*['"]live['"]/);
});

test('runtime banner only claims live when healthProvable is true', () => {
  const banner = read('components/runtime-banner.tsx');
  // "Live" must only appear as a conditional value guarded by healthProvable
  const healthIdx = banner.indexOf('healthProvable');
  const liveIdx = banner.indexOf("'Live'");
  expect(healthIdx).toBeGreaterThan(-1);
  expect(liveIdx).toBeGreaterThan(-1);
  // healthProvable check must precede the 'Live' value assignment
  expect(healthIdx).toBeLessThan(liveIdx);
});
