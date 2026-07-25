/**
 * Screen 5 — Threat Monitoring: presentation logic + source-contract guards.
 *
 * Pure-logic tests for the formatting helpers, plus source assertions that the
 * screen/panel are truthful (canonical summary only, no fabricated screenshot
 * metrics, GET-only load, idempotent investigate via the same-origin proxy).
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import {
  confidenceBand,
  confidencePercent,
  confidenceVariant,
  dataFreshnessLabel,
  dataFreshnessVariant,
  degradedReasonCopy,
  detectionStatusVariant,
  detectionTypeLabel,
  enginePanelPresentation,
  evidenceSourceVariant,
  formatMttd,
  investigateOutcomeMessage,
  severityLabel,
  severityVariant,
  shortHex,
  trend,
} from '../app/threat-monitoring/presentation';

const APP = path.join(__dirname, '..', 'app');
const read = (rel: string) => fs.readFileSync(path.join(APP, rel), 'utf8');

// --------------------------------------------------------------------------
// Severity + confidence are distinct concepts, labelled correctly
// --------------------------------------------------------------------------
test('severity labels and variants (potential impact)', () => {
  expect(severityLabel('critical')).toBe('Critical');
  expect(severityVariant('critical')).toBe('danger');
  expect(severityVariant('high')).toBe('danger');
  expect(severityVariant('medium')).toBe('warning');
  expect(severityVariant('low')).toBe('info');
});

test('confidence is formatted as a percentage + band, separate from severity', () => {
  expect(confidencePercent(0.72)).toBe('72%');
  expect(confidencePercent(null)).toBe('—');
  expect(confidenceBand(0.8)).toBe('High');
  expect(confidenceBand(0.5)).toBe('Medium');
  expect(confidenceBand(0.2)).toBe('Low');
  expect(confidenceBand(null)).toBe('Unknown');
  expect(confidenceVariant(0.8)).toBe('success');
});

// --------------------------------------------------------------------------
// Trend is security-aware and only rendered with a valid comparison base
// --------------------------------------------------------------------------
test('trend returns null without a comparison base (never fabricated)', () => {
  expect(trend('detections', null)).toBeNull();
  expect(trend('telemetry', undefined)).toBeNull();
});

test('an INCREASE in detections/anomalies is a worsening metric (bad tone)', () => {
  expect(trend('detections', 50)!.tone).toBe('bad');
  expect(trend('anomalies', 25)!.tone).toBe('bad');
  // A decrease is an improvement.
  expect(trend('detections', -30)!.tone).toBe('good');
});

test('telemetry-volume change is informational (neutral), MTTD increase is bad', () => {
  expect(trend('telemetry', 200)!.tone).toBe('neutral');
  expect(trend('mttd', 40)!.tone).toBe('bad');
  expect(trend('mttd', -40)!.tone).toBe('good');
});

// --------------------------------------------------------------------------
// MTTD + freshness formatting; zero != unavailable
// --------------------------------------------------------------------------
test('MTTD formats real values and shows a dash when unmeasurable', () => {
  expect(formatMttd(17)).toBe('17s');
  expect(formatMttd(120)).toBe('2m');
  expect(formatMttd(null)).toBe('—'); // unavailable, never a fabricated < 18s
});

test('data freshness distinguishes fresh / stale / no-telemetry / unavailable', () => {
  expect(dataFreshnessLabel('fresh')).toBe('Fresh');
  expect(dataFreshnessLabel('stale')).toBe('Stale');
  expect(dataFreshnessLabel('no_telemetry')).toBe('No telemetry');
  expect(dataFreshnessLabel('unavailable')).toBe('Unavailable');
  expect(dataFreshnessVariant('fresh')).toBe('success');
  expect(dataFreshnessVariant('stale')).toBe('warning');
});

// --------------------------------------------------------------------------
// Engine panel never shows a scary alert on thin evidence
// --------------------------------------------------------------------------
test('engine panel tone: calm when no material threat / anomalies-only', () => {
  expect(enginePanelPresentation('no_material_threat').scary).toBe(false);
  expect(enginePanelPresentation('anomalies_only').scary).toBe(false);
  expect(enginePanelPresentation('telemetry_stale').scary).toBe(false);
  expect(enginePanelPresentation('no_telemetry').scary).toBe(false);
  // Only a real active detection is "scary".
  expect(enginePanelPresentation('active_detection', 'high').scary).toBe(true);
});

test('evidence source variants label live vs simulator vs replay', () => {
  expect(evidenceSourceVariant('live')).toBe('success');
  expect(evidenceSourceVariant('simulator')).toBe('info');
  expect(evidenceSourceVariant('replay')).toBe('warning');
});

test('detection type + status + misc helpers', () => {
  expect(detectionTypeLabel('mint_burn_irregularity')).toBe('Mint/Burn Irregularity');
  expect(detectionStatusVariant('open')).toBe('danger');
  expect(detectionStatusVariant('investigating')).toBe('info');
  expect(detectionStatusVariant('resolved')).toBe('success');
  expect(shortHex('0x1234567890abcdef', 6, 4)).toBe('0x1234…cdef');
  expect(degradedReasonCopy('telemetry_stale')).toContain('stale');
});

// --------------------------------------------------------------------------
// Investigate outcome copy — success + existing + error
// --------------------------------------------------------------------------
test('investigate outcome messages cover created / existing / error', () => {
  expect(investigateOutcomeMessage({ status: 'created' })).toContain('opened');
  expect(investigateOutcomeMessage({ status: 'existing_alert' })).toContain('already open');
  expect(investigateOutcomeMessage({ status: 'existing_incident' })).toContain('incident');
  expect(investigateOutcomeMessage({ detail: 'Insufficient evidence' })).toBe('Insufficient evidence');
});

// --------------------------------------------------------------------------
// Source contract: canonical summary, GET-only load, proxy transport
// --------------------------------------------------------------------------
test('screen loads the canonical summary via the same-origin proxy, GET only', () => {
  const src = read('threat-monitoring/threat-monitoring-screen.tsx');
  expect(src).toContain('/api/threat-monitoring/summary');
  // The summary fetch must be read-only (no-store, GET default) so page load never writes.
  expect(src).toContain("fetch('/api/threat-monitoring/summary?window_days=7', { headers: { ...headers }, cache: 'no-store' })");
  // KPI values derive from the canonical summary — not hardcoded.
  expect(src).toContain('summary.telemetry_events_count');
  expect(src).toContain('summary.detection_count');
  expect(src).toContain('summary.anomaly_count');
  expect(src).toContain('formatMttd(summary.mean_time_to_detect_seconds)');
});

test('no fabricated screenshot metrics are hardcoded in the UI', () => {
  const screen = read('threat-monitoring/threat-monitoring-screen.tsx');
  const panel = read('threat-monitoring/threat-detection-engineer-panel.tsx');
  for (const forbidden of ['12.8M', '341', '< 18s', '<18s']) {
    expect(screen).not.toContain(forbidden);
    expect(panel).not.toContain(forbidden);
  }
});

test('Start Investigation posts to the idempotent proxy and navigates to the destination', () => {
  const src = read('threat-monitoring/threat-monitoring-screen.tsx');
  expect(src).toContain('/api/threat-monitoring/detections/');
  expect(src).toContain('/investigate');
  expect(src).toContain("method: 'POST'");
  expect(src).toContain('router.push(destination)');
  // Actionable error surfaced instead of silently failing.
  expect(src).toContain('setInvestigateError');
});

test('all four tabs are present with Overview default', () => {
  const src = read('threat-monitoring/threat-monitoring-screen.tsx');
  for (const label of ['Overview', 'Telemetry', 'Detections', 'Anomalies']) {
    expect(src).toContain(`label: '${label}'`);
  }
  expect(src).toContain("useState<TabKey>('overview')");
});

test('primary control is accessible (aria-busy, disabled state, testid)', () => {
  const panel = read('threat-monitoring/threat-detection-engineer-panel.tsx');
  expect(panel).toContain('data-testid="start-investigation"');
  expect(panel).toContain('aria-busy={investigating}');
  expect(panel).toContain('disabled={!panel.can_investigate');
});

test('detections tab exposes severity + type filters and pagination', () => {
  const src = read('threat-monitoring/threat-monitoring-screen.tsx');
  expect(src).toContain('Filter by severity');
  expect(src).toContain('Filter by detection type');
  expect(src).toContain('function Pager');
});

test('BFF summary route forwards to the backend threat-monitoring summary, GET only', () => {
  const route = fs.readFileSync(path.join(APP, 'api', 'threat-monitoring', 'summary', 'route.ts'), 'utf8');
  expect(route).toContain("backendPath: '/threat-monitoring/summary'");
  expect(route).toContain("method: 'GET'");
  expect(route).not.toContain('POST');
});

test('BFF investigate route is POST and preserves backend status codes via the shared proxy', () => {
  const route = fs.readFileSync(path.join(APP, 'api', 'threat-monitoring', 'detections', '[detectionId]', 'investigate', 'route.ts'), 'utf8');
  expect(route).toContain('export async function POST');
  expect(route).toContain('/investigate');
  expect(route).toContain('proxyJsonToBackend');
});
