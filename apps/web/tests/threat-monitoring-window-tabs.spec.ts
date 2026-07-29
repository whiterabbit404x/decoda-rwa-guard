/**
 * Screen 5 — Threat Monitoring: canonical window, URL-addressable tabs, and
 * telemetry-truthfulness (source vs freshness, human-readable labels, stale-state
 * actions). Pure-logic helpers + source-contract guards; no browser required.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import {
  DEFAULT_TAB,
  DEFAULT_WINDOW,
  WINDOW_OPTIONS,
  eventTypeLabel,
  ingestionSourceLabel,
  nextActionLabel,
  resolveTab,
  resolveWindow,
  rowFreshnessLabel,
  windowLabel,
  workerStatusLabel,
} from '../app/threat-monitoring/presentation';

const APP = path.join(__dirname, '..', 'app');
const read = (rel: string) => fs.readFileSync(path.join(APP, rel), 'utf8');
const SCREEN = () => read('threat-monitoring/threat-monitoring-screen.tsx');
const PANEL = () => read('threat-monitoring/threat-detection-engineer-panel.tsx');
const PAGE = () => read('(product)/threat/page.tsx');

// --------------------------------------------------------------------------
// Canonical window (24h / 7d / 30d + safe fallback), shared by every section
// --------------------------------------------------------------------------
test('window presets are 24h / 7d / 30d with a 7d default', () => {
  expect(WINDOW_OPTIONS.map((o) => o.value)).toEqual(['24h', '7d', '30d']);
  expect(DEFAULT_WINDOW).toBe('7d');
  expect(resolveWindow('24h')).toBe('24h');
  expect(resolveWindow('30d')).toBe('30d');
});

test('invalid window falls back to 7d (never an arbitrary period)', () => {
  expect(resolveWindow('bogus')).toBe('7d');
  expect(resolveWindow(null)).toBe('7d');
  expect(resolveWindow('')).toBe('7d');
  expect(windowLabel('24h')).toBe('Last 24 hours');
  expect(windowLabel('bogus')).toBe('Last 7 days');
});

// 3. Window selector rendered near Refresh, using the accessible Decoda Select.
test('window selector is rendered near Refresh with the shared window', () => {
  const src = SCREEN();
  // The accessible Decoda Select maps testId -> data-testid at runtime.
  expect(src).toContain('testId="window-selector"');
  expect(src).toContain('WINDOW_OPTIONS');
  expect(src).toContain('Select time window');
  expect(src).toContain('Refresh');
});

// 4 + 16. The SAME selected window is passed to every section (no KPI/table mismatch).
test('the selected window is passed through every BFF request', () => {
  const src = SCREEN();
  expect(src).toContain('`/api/threat-monitoring/summary?window=${windowKey}`');
  expect(src).toContain('window: windowKey'); // detections + telemetry URLSearchParams
  expect(src).toContain('&window=${windowKey}'); // anomalies
  // The KPI period label derives from the same windowKey the tables use.
  expect(src).toContain('windowLabel(windowKey)');
  expect(src).toContain('windowText={windowText}');
});

// --------------------------------------------------------------------------
// URL-addressable tabs
// --------------------------------------------------------------------------
test('invalid tab falls back to overview; valid tabs pass through', () => {
  expect(DEFAULT_TAB).toBe('overview');
  expect(resolveTab('telemetry')).toBe('telemetry');
  expect(resolveTab('detections')).toBe('detections');
  expect(resolveTab('anomalies')).toBe('anomalies');
  expect(resolveTab('bogus')).toBe('overview');
  expect(resolveTab(null)).toBe('overview');
  expect(resolveTab('')).toBe('overview');
});

test('tab + window are URL-backed so refresh + Back/Forward work', () => {
  const src = SCREEN();
  expect(src).toContain('useSearchParams');
  expect(src).toContain("resolveTab(searchParams.get('tab'))");
  expect(src).toContain("resolveWindow(searchParams.get('window'))");
  // Tab/window changes navigate (history), never write to the DB.
  expect(src).toContain('router.push(`/threat?${params.toString()}`');
  expect(src).toContain("params.set('tab'");
  expect(src).toContain("params.set('window'");
});

// 19. No write request during tab navigation — the only POST is Start Investigation.
test('tab navigation performs no writes (only investigate is a POST)', () => {
  const src = SCREEN();
  const posts = src.match(/method: 'POST'/g) ?? [];
  expect(posts.length).toBe(1);
  expect(src).toContain('/investigate');
  // Every list/summary fetch is a read (cache: 'no-store', GET default).
  expect(src).toContain("cache: 'no-store'");
});

// --------------------------------------------------------------------------
// Page header (title owned by the page <h1>, subtitle in the screen)
// --------------------------------------------------------------------------
test('page renders a visible Threat Monitoring title + subtitle', () => {
  expect(PAGE()).toContain('<h1>Threat Monitoring</h1>');
  const src = SCREEN();
  expect(src).toContain('data-testid="threat-subtitle"');
  expect(src).toContain('Correlated telemetry, behavioral anomalies, and exploit detections.');
});

// --------------------------------------------------------------------------
// Telemetry table — security only, human-readable labels, source != freshness
// --------------------------------------------------------------------------
test('event type labels are human-readable, never raw snake_case', () => {
  expect(eventTypeLabel('native_transfer')).toBe('Native Transfer');
  expect(eventTypeLabel('wallet_transfer_detected')).toBe('Wallet Transfer Detected');
  expect(eventTypeLabel('privileged_action')).toBe('Privileged Action');
  expect(eventTypeLabel('erc20_transfer')).toBe('Token Transfer');
  // Unknown types are title-cased, never left as raw snake_case.
  expect(eventTypeLabel('some_new_event')).toBe('Some New Event');
  expect(eventTypeLabel('some_new_event')).not.toContain('_');
});

test('telemetry table separates ingestion source, evidence mode, and freshness', () => {
  const src = SCREEN();
  // Distinct columns for how it was captured, evidence quality, and freshness.
  for (const header of ['Event Type', 'Asset', 'Transaction', 'Block', 'Ingestion Source', 'Evidence Quality', 'Freshness', 'Observed At']) {
    expect(src).toContain(`'${header}'`);
  }
  // Source and freshness are rendered from separate canonical fields.
  expect(src).toContain('ingestionSourceLabel(e.ingestion_source)');
  expect(src).toContain('rowFreshnessLabel(e.freshness)');
  expect(src).toContain('eventTypeLabel(e.event_type)');
  // A single green "live" badge must NOT stand in for freshness.
  expect(src).not.toContain('label={evidenceSourceLabel(e.evidence_source)} variant={evidenceSourceVariant(e.evidence_source)}');
});

test('ingestion source + freshness + worker labels are canonical', () => {
  expect(ingestionSourceLabel('rpc_polling')).toBe('RPC polling');
  expect(ingestionSourceLabel('quicknode_stream')).toBe('QuickNode stream');
  expect(rowFreshnessLabel('stale')).toBe('Stale');
  expect(rowFreshnessLabel('fresh')).toBe('Fresh');
  expect(workerStatusLabel('stale')).toBe('Stale');
});

// 9. The default telemetry view requests canonical security telemetry only, so
//    rpc_polling ingestion heartbeats never appear in the security table.
test('telemetry tab requests the security category only', () => {
  const src = SCREEN();
  expect(src).toContain("category: 'security'");
});

// 8. Filters use the existing Decoda Select, not native browser selects.
test('detections + telemetry filters use the Decoda Select component', () => {
  const src = SCREEN();
  expect(src).toContain('Select,'); // imported from ui-primitives
  expect(src).toContain('onValueChange');
  // No native <select>/<option> markup in the screen.
  expect(src).not.toContain('<select');
  expect(src).not.toContain('<option');
});

// --------------------------------------------------------------------------
// Stale-state empty states + one canonical action (Run Source Diagnostic)
// --------------------------------------------------------------------------
test('detections stale empty state uses the exact spec copy + diagnostic action', () => {
  const src = SCREEN();
  expect(src).toContain('No promoted threat detections match the current filters during this period.');
  expect(src).toContain('Detection coverage may be incomplete because fresh telemetry is unavailable.');
  expect(src).toContain('data-testid="run-source-diagnostic"');
});

test('anomalies stale empty state uses the exact spec copy + diagnostic action', () => {
  const src = SCREEN();
  expect(src).toContain('No anomalies tracked');
  expect(src).toContain('No sub-threshold deviations match the selected period.');
  expect(src).toContain('Results may be incomplete because telemetry ingestion is stale.');
});

test('the canonical stale action label is "Run Source Diagnostic" -> Monitoring Sources', () => {
  expect(nextActionLabel('diagnose_ingestion')).toBe('Run Source Diagnostic');
  const src = SCREEN();
  expect(src).toContain('SOURCE_DIAGNOSTIC_HREF');
  // Empty states no longer say the generic "Review monitoring coverage".
  expect(src).not.toContain('Review monitoring coverage');
});

// --------------------------------------------------------------------------
// Engineer panel — stale fields + no Start Investigation with zero detections
// --------------------------------------------------------------------------
test('engineer panel renders the canonical stale fields', () => {
  const panel = PANEL();
  for (const label of ['Latest security telemetry', 'Worker heartbeat', 'Ingestion status', 'Evidence coverage', 'Detection confidence']) {
    expect(panel).toContain(label);
  }
  expect(panel).toContain('data-testid="engine-finding"');
  expect(panel).toContain('data-testid="engine-explanation"');
});

test('engineer panel shows Run Source Diagnostic (not Start Investigation) with no detection', () => {
  const panel = PANEL();
  // Start Investigation only renders when a canonical detection exists.
  expect(panel).toContain('const showInvestigate = detection != null');
  expect(panel).toContain('data-testid="run-source-diagnostic"');
  expect(panel).toContain('Restore telemetry ingestion before beginning a new investigation.');
  // The Start button stays gated on the backend can_investigate flag.
  expect(panel).toContain('disabled={!panel.can_investigate');
});

// --------------------------------------------------------------------------
// 20. Shared RuntimeBanner regression: worker/heartbeat/poll stay separate fields
// --------------------------------------------------------------------------
test('shared RuntimeBanner keeps heartbeat, poll, and worker as distinct fields', () => {
  const banner = fs.readFileSync(path.join(APP, 'components', 'runtime-banner.tsx'), 'utf8');
  expect(banner).toContain('label="Heartbeat"');
  expect(banner).toContain('label="Poll"');
  expect(banner).toContain('label="Next action"');
  // Heartbeat / poll / telemetry ages are shown separately (CLAUDE.md rule 3).
  expect(banner).toContain('summary.last_heartbeat_at');
  expect(banner).toContain('summary.last_poll_at');
  expect(banner).toContain('summary.last_telemetry_at');
});
