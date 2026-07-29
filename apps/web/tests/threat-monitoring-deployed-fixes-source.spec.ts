/**
 * Screen 5 — Threat Monitoring: regression guards for the deployed empty/runtime
 * state fixes.
 *
 * These assert on the ACTUAL routed component only —
 * app/(product)/threat/page.tsx -> threat-monitoring/threat-monitoring-screen.tsx
 * (+ threat-detection-engineer-panel.tsx, presentation.ts) — never a legacy/unused
 * panel. Pure-logic helper tests + source-contract assertions; no browser required.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import { emptyStateCopy, ingestionBadge } from '../app/threat-monitoring/presentation';

const APP = path.join(__dirname, '..', 'app');
const read = (rel: string) => fs.readFileSync(path.join(APP, rel), 'utf8');
const PAGE = () => read('(product)/threat/page.tsx');
const SCREEN = () => read('threat-monitoring/threat-monitoring-screen.tsx');
const PANEL = () => read('threat-monitoring/threat-detection-engineer-panel.tsx');
const CSS = () => read('styles.css');

// 1. The routed page renders a VISIBLE <h1> (never a screen-reader-only substitute).
test('the /threat route renders a visible Threat Monitoring heading', () => {
  expect(PAGE()).toContain('<h1>Threat Monitoring</h1>');
  expect(PAGE()).toContain('ThreatMonitoringScreen');
  const css = CSS();
  const start = css.indexOf('.productPage > h1');
  expect(start).toBeGreaterThan(-1);
  const rule = css.slice(start, start + 300);
  // The heading must not be clipped off-screen the way a sr-only utility would.
  expect(rule).not.toContain('clip: rect(0, 0, 0, 0)');
  expect(rule).not.toContain('width: 1px');
  expect(rule).toContain('font-size');
});

// 2. Subtitle renders exactly once, in the routed screen.
test('subtitle renders exactly once in the routed screen', () => {
  const src = SCREEN();
  const matches = src.match(/Correlated telemetry, behavioral anomalies, and exploit detections\./g) ?? [];
  expect(matches.length).toBe(1);
  expect(src).toContain('data-testid="threat-subtitle"');
});

// 3. Overview uses the available width (no restrictive 1440 max) with an 8/4 split.
test('overview layout uses the available width', () => {
  const src = SCREEN();
  expect(src).not.toContain("maxWidth: '1440px'");
  const m = src.match(/maxWidth: '(\d+)px'/);
  expect(m).not.toBeNull();
  expect(Number(m![1])).toBeGreaterThanOrEqual(1500);
  // ~8/4 columns (2fr main content / 1fr agent panel).
  expect(src).toContain("gridTemplateColumns: 'minmax(0, 2fr) minmax(0, 1fr)'");
});

// 4. Every detection category is listed; unsupported detectors are a SEPARATE note.
test('detections-by-type shows every category and separates capability', () => {
  const src = SCREEN();
  // Renders ALL rows, not only the supported ones.
  expect(src).toContain('{rows.map((r) => (');
  expect(src).toContain('data-testid="detections-by-type"');
  // Unsupported detectors are a compact capability note, not hidden in a <details>.
  expect(src).toContain('data-testid="detector-capability-note"');
  expect(src).not.toContain('detector(s) not evaluated (evidence unavailable)');
  // Unsupported rows read "Not evaluated", never a misleading "0".
  expect(src).toContain('Not evaluated');
});

// 5. Engineer panel is compact, window-aware, and non-repetitive.
test('engineer panel is compact, window-aware, and non-repetitive', () => {
  const panel = PANEL();
  expect(panel).toContain('Selected window');
  expect(panel).toContain('Threat Detection Engineer');
  // The generic descriptor is present but demoted (muted), never the state finding.
  expect(panel).toContain('Correlated exploit &amp; anomaly detection');
  // Badge is derived from canonical ingestion status ("Ingestion stale"), not a
  // generic eyebrow, for the no-detection states.
  expect(panel).toContain('ingestionBadge(fields.worker_status, fields.ingestion_status)');
  // Reasons already stated by the finding are filtered so no sentence repeats.
  expect(panel).toContain('REDUNDANT_REASONS');
});

// 6. Empty-state copy distinguishes "no data in window" from "no data ever".
test('emptyStateCopy distinguishes in-window from ever', () => {
  const ever = emptyStateCopy('no_telemetry_ever');
  expect(ever.title).toBe('No telemetry has arrived yet');

  const inWindow = emptyStateCopy('no_security_telemetry_in_window', {
    windowText: 'Last 7 days',
    latestEverAt: new Date(Date.now() - 40 * 864e5).toISOString(),
  });
  expect(inWindow.title).toBe('No security telemetry in this period');
  expect(inWindow.body).toContain('Last 7 days');
  expect(inWindow.body).toContain('Latest security telemetry');
  expect(inWindow.title).not.toBe(ever.title);

  expect(emptyStateCopy('runtime_only_telemetry').title).toBe('No security telemetry yet');
  // Stale ingestion surfaces an incompleteness warning.
  expect(emptyStateCopy('telemetry_stale').staleWarning).toContain('stale');
});

// 7. ingestionBadge reads "Ingestion stale" for a stale worker (never "Stable").
test('ingestionBadge reflects stale ingestion truthfully', () => {
  expect(ingestionBadge('stale', 'no_telemetry').label).toBe('Ingestion stale');
  expect(ingestionBadge('offline', 'no_telemetry').label).toBe('Ingestion offline');
  expect(ingestionBadge('healthy', 'fresh').label).toBe('Ingestion healthy');
  // Never a "Stable"-branded label that reads as a contradiction next to "stale".
  for (const ws of ['stale', 'offline', 'degraded']) {
    expect(ingestionBadge(ws, 'no_telemetry').label.toLowerCase()).not.toContain('stable');
  }
});

// 8. The chart empty state derives from the canonical reason (not a hardcoded line).
test('chart empty state derives from the canonical empty-state reason', () => {
  const src = SCREEN();
  expect(src).toContain('data-testid="chart-empty-state"');
  expect(src).toContain('emptyStateCopy(summary?.empty_state_reason');
});
