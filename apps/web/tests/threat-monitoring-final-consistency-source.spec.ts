/**
 * Screen 5 — Threat Monitoring: final consistency pass regression guards.
 *
 * Asserts on the ACTUAL routed component tree only —
 * app/(product)/threat/page.tsx -> threat-monitoring/threat-monitoring-screen.tsx
 * (+ threat-detection-engineer-panel.tsx, presentation.ts) — plus the shared global
 * RuntimeBanner. Pure-logic helper tests + source-contract assertions; no browser.
 *
 * Covers: stale-state → Diagnose ingestion (never Open incident), one canonical
 * freshness object surfaced in the agent panel, honest empty-state language,
 * canonical detector display names, watchlist stale warning, aligned header, and
 * the read-only / diagnostic-only guarantees.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import {
  SOURCE_DIAGNOSTIC_HREF,
  degradedReasonCopy,
  detectionTypeLabel,
  emptyStateCopy,
  evidenceCoverageLabel,
  formatDate,
  nextActionLabel,
} from '../app/threat-monitoring/presentation';

const APP = path.join(__dirname, '..', 'app');
const read = (rel: string) => fs.readFileSync(path.join(APP, rel), 'utf8');
const SCREEN = () => read('threat-monitoring/threat-monitoring-screen.tsx');
const PANEL = () => read('threat-monitoring/threat-detection-engineer-panel.tsx');
const BANNER = () => read('components/runtime-banner.tsx');
const CONTEXT = () => read('runtime-summary-context.tsx');
const PAGE = () => read('(product)/threat/page.tsx');

// 1-2. The shared global strip can show "Diagnose ingestion"; the stale-priority
//      selector lives in the backend, and Screen 5 never hardcodes an incident action.
test('global runtime strip maps diagnose_ingestion and never hardcodes Open incident in Screen 5', () => {
  expect(BANNER()).toContain("diagnose_ingestion: 'Diagnose ingestion'");
  expect(CONTEXT()).toContain("diagnose_ingestion: 'Diagnose ingestion'");
  // Screen 5 must not independently choose an incident action.
  expect(SCREEN()).not.toContain('open_incident');
  expect(PANEL()).not.toContain('open_incident');
  expect(SCREEN().toLowerCase()).not.toContain('open incident');
});

// 3-4. The agent panel surfaces the canonical latest-telemetry date + heartbeat age
//      from the same summary fields (never a bare "—" when the summary carries them).
test('agent panel renders canonical latest-telemetry date and worker heartbeat', () => {
  const panel = PANEL();
  expect(panel).toContain('data-testid="engine-latest-telemetry"');
  expect(panel).toContain('formatDate(fields.last_security_telemetry_at)');
  expect(panel).toContain('data-testid="engine-worker-heartbeat"');
  expect(panel).toContain('relativeTime(fields.worker_heartbeat_at)');
});

// formatDate renders an absolute calendar date (not a decaying "N days ago").
test('formatDate renders an absolute month-day-year date', () => {
  expect(formatDate('2026-07-11T00:00:00Z')).toBe('July 11, 2026');
  expect(formatDate(null)).toBe('—');
  expect(formatDate('not-a-date')).toBe('—');
});

// 5-6. Empty-state language: "No telemetry has arrived yet" only when nothing ever
//      arrived; "No security telemetry in this period" when older telemetry exists.
test('empty-state language distinguishes never-arrived from none-in-window', () => {
  expect(emptyStateCopy('no_telemetry_ever').title).toBe('No telemetry has arrived yet');
  const inWindow = emptyStateCopy('no_security_telemetry_in_window', {
    windowText: 'Last 7 days',
    latestEverAt: '2026-07-11T00:00:00Z',
  });
  expect(inWindow.title).toBe('No security telemetry in this period');
  // The historical fact is rendered as a fixed date, not "N days ago".
  expect(inWindow.body).toContain('July 11, 2026');
  expect(inWindow.title).not.toBe('No telemetry has arrived yet');
  // The page-level degraded copy for the in-window reason never claims "arrived yet".
  expect(degradedReasonCopy('no_security_telemetry_in_window').toLowerCase()).not.toContain('arrived yet');
});

// 7. Evidence coverage never serializes as a misleading "0/0".
test('evidence coverage never renders a bare 0/0', () => {
  expect(evidenceCoverageLabel({ active_detections: 0, live_backed: 0, non_live_backed: 0 })).toBe('No qualifying events');
  expect(evidenceCoverageLabel({ active_detections: 3, live_backed: 2, non_live_backed: 1 })).toBe('2/3 live-backed');
  expect(evidenceCoverageLabel(null)).toBe('Unavailable');
  const panel = PANEL();
  expect(panel).toContain('evidenceCoverageLabel(fields.evidence_coverage)');
  expect(panel).not.toContain('live_backed}/{fields.evidence_coverage.active_detections}');
});

// 8. Canonical detector display names (persisted keys unchanged).
test('detector display names are canonical', () => {
  expect(detectionTypeLabel('unusual_transfer')).toBe('Unusual Transfer');
  expect(detectionTypeLabel('reentrancy_pattern')).toBe('Possible Reentrancy');
  expect(detectionTypeLabel('flash_loan_pattern')).toBe('Flash-Loan-Assisted Sequence');
  expect(detectionTypeLabel('other')).toBe('Other');
});

// 9. Watchlist empty state warns when ingestion coverage may be incomplete.
test('watchlist empty state includes a stale-coverage warning', () => {
  const src = SCREEN();
  expect(src).toContain('data-testid="watchlist-empty-state"');
  expect(src).toContain('data-testid="watchlist-stale-warning"');
  expect(src).toContain('Results may be incomplete until telemetry ingestion resumes.');
  expect(src).toContain('No monitored addresses matched active watchlists during the selected period.');
});

// 10. Header: title (page <h1>) + subtitle + controls share one aligned block; the
//     content is no longer auto-centered away from the full-width title's left edge.
test('header aligns title, subtitle, and controls to the same left edge', () => {
  expect(PAGE()).toContain('<h1>Threat Monitoring</h1>');
  const src = SCREEN();
  // Subtitle sits in the routed screen exactly once, with its testid.
  expect(src).toContain('data-testid="threat-subtitle"');
  // The overview container keeps a readable max-width but is NOT auto-centered.
  const m = src.match(/maxWidth: '(\d+)px', width: '100%'/);
  expect(m).not.toBeNull();
  expect(Number(m![1])).toBeGreaterThanOrEqual(1500);
  expect(src).not.toContain("maxWidth: '1680px', width: '100%', margin: '0 auto'");
});

// 11. Run Source Diagnostic reuses the existing Monitoring Sources flow (no Screen-5
//     re-implementation of provider diagnostics).
test('Run Source Diagnostic routes to the existing Monitoring Sources flow', () => {
  expect(SOURCE_DIAGNOSTIC_HREF).toBe('/monitoring-sources');
  expect(nextActionLabel('diagnose_ingestion')).toBe('Run Source Diagnostic');
  const panel = PANEL();
  expect(panel).toContain('data-testid="run-source-diagnostic"');
  expect(panel).toContain('href={SOURCE_DIAGNOSTIC_HREF}');
});

// 12. No Screen 5 read/interaction creates an incident (GET summary; investigate is a
//     POST to the detection investigate proxy, never an incident-create endpoint).
test('Screen 5 never creates an incident', () => {
  const src = SCREEN();
  expect(src).toContain("fetch(`/api/threat-monitoring/summary?window=${windowKey}`");
  expect(src).not.toContain('/incidents');
  // Investigate targets the detection investigate route, not an incident writer.
  expect(src).toContain('/investigate');
});
