/**
 * Screen 5 — Threat Monitoring: Operational Integrity Detections.
 *
 * The screen's whole argument is that a transaction can be cryptographically
 * valid while still being operationally unauthorized. These tests hold the
 * rendering of that argument to the same standard as the backend that produces
 * it: a check that could not run must never read as a pass, an amount must never
 * pass through a float, and a provider outage must never render as "no threats".
 *
 * Pure-logic tests over the presentation module, plus source-contract assertions
 * on the screen/panel components. No browser required.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import {
  CATEGORY_OPERATIONAL_INTEGRITY,
  EMPTY_STATE_BODY,
  categoryLabel,
  categoryOptions,
  checkGlyph,
  checkStatusColor,
  checkStatusLabel,
  checkStatusVariant,
  conclusionLabel,
  conclusionVariant,
  coverageNotice,
  coverageSourceLine,
  formatAmount,
  isOperationalIntegrity,
  isPreconfirmed,
  preconfirmationAge,
  reasonCodeLabel,
  telemetrySourceLabel,
  telemetryStageLabel,
  telemetryStageVariant,
  type OperationalCoverage,
} from '../app/threat-monitoring/operational-integrity';
import { detectionTypeLabel } from '../app/threat-monitoring/presentation';

function appSource(fileName: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', fileName), 'utf-8');
}

const screen = () => appSource('threat-monitoring/threat-monitoring-screen.tsx');
const panels = () => appSource('threat-monitoring/detection-detail-panels.tsx');

// -- 11. Operational Integrity appears as a first-class category --------------
test('Operational Integrity is offered as a detection category', () => {
  const options = categoryOptions(null).map((o) => o.value);
  expect(options).toContain(CATEGORY_OPERATIONAL_INTEGRITY);
  expect(options).toContain('CYBER_SECURITY');
  expect(options[0]).toBe(''); // "All categories" stays first
  expect(categoryLabel(CATEGORY_OPERATIONAL_INTEGRITY)).toBe('Operational Integrity');
});

test('backend-supplied categories win over the static fallback', () => {
  const options = categoryOptions([{ value: 'CYBER_SECURITY', label: 'Cyber Security' }]);
  expect(options.map((o) => o.value)).toEqual(['', 'CYBER_SECURITY']);
});

test('adding the category never removes the existing cyber lane', () => {
  expect(categoryOptions(null).map((o) => o.label)).toContain('Cyber Security');
});

test('operational integrity detection types have real labels', () => {
  expect(detectionTypeLabel('unmatched_issuance')).toBe('Unmatched Issuance');
  expect(detectionTypeLabel('settlement_timeout')).toBe('Settlement Timeout');
  expect(detectionTypeLabel('nav_valuation_drift')).toBe('NAV / Valuation Drift');
  expect(detectionTypeLabel('transfer_agent_mismatch')).toBe('Transfer-Agent Mismatch');
  expect(detectionTypeLabel('unauthorized_admin_change')).toBe('Unauthorized Admin Change');
  // The existing cyber labels are untouched.
  expect(detectionTypeLabel('unusual_transfer')).toBe('Unusual Transfer');
});

// -- 12. The category filter reaches the backend -----------------------------
test('the category filter is sent to the backend as a real query parameter', () => {
  const src = screen();
  expect(src).toContain("params.set('category', category)");
  expect(src).toContain("params.set('search', search)");
  // A filter change resets pagination so page 3 of the old lane is never shown.
  expect(src).toContain('onCategory={(v) => { setCategory(v); setType(\'\'); setOffset(0); setSelectedId(null); }}');
});

test('the Category control exists in the detections toolbar', () => {
  const src = screen();
  expect(src).toContain('testId="category-filter"');
  expect(src).toContain('ariaLabel="Filter by detection category"');
  expect(src).toContain('<FilterField label="Category">');
});

test('the toolbar keeps severity, type, status and adds search', () => {
  const src = screen();
  expect(src).toContain('<FilterField label="Severity">');
  expect(src).toContain('<FilterField label="Type">');
  expect(src).toContain('<FilterField label="Status">');
  expect(src).toContain("placeholder=\"Search detections...\"");
  expect(src).toContain('data-testid="detection-search"');
});

test('search is debounced rather than fired per keystroke', () => {
  expect(screen()).toContain('window.setTimeout(() => { setSearch(searchInput.trim()); setOffset(0); }, 350)');
});

test('the type options follow the selected lane', () => {
  const src = screen();
  expect(src).toContain("const operational = category === CATEGORY_OPERATIONAL_INTEGRITY;");
  expect(src).toContain("{ value: 'unmatched_issuance', label: 'Unmatched Issuance' }");
  expect(src).toContain("{ value: 'unusual_transfer', label: 'Unusual Transfer' }");
});

// -- 13. Selecting a detection opens the details -----------------------------
test('clicking a detection row opens the detail panels', () => {
  const src = screen();
  expect(src).toContain('onClick={() => setSelectedId(selected ? null : d.id)}');
  expect(src).toContain('<DetectionDetailPanels');
  expect(src).toContain('detectionId={selectedId}');
});

test('the Investigate button does not also select the row', () => {
  // The action cell stops propagation, so pressing Investigate never opens the
  // panel underneath it.
  expect(screen()).toContain('<td onClick={(e) => e.stopPropagation()}>');
});

test('a selection that leaves the result set is cleared', () => {
  expect(screen()).toContain('if (selectedId && !rows.some((r) => r.id === selectedId)) setSelectedId(null);');
});

test('the detail panel loads from the same-origin proxy with GET only', () => {
  const src = panels();
  expect(src).toContain('/api/threat-monitoring/detections/${encodeURIComponent(detectionId)}');
  expect(src).not.toContain("method: 'POST'");
  expect(src).toContain("cache: 'no-store'");
});

test('the Detection Details panel shows the reference fields', () => {
  const src = panels();
  for (const label of [
    'Detection', 'Asset', 'Operation', 'Observed Amount', 'Expected Amount',
    'Source', 'Transaction Hash', 'First Seen',
  ]) {
    expect(src).toContain(`label="${label}"`);
  }
});

// -- 14. PASS / FAIL / UNKNOWN render correctly -------------------------------
test('check glyphs map to the deterministic status', () => {
  expect(checkGlyph('PASS')).toBe('✓');
  expect(checkGlyph('FAIL')).toBe('✕');
  expect(checkGlyph('UNKNOWN')).toBe('?');
  expect(checkGlyph(null)).toBe('?');
});

test('an UNKNOWN check is never styled as a pass', () => {
  expect(checkStatusVariant('PASS')).toBe('success');
  expect(checkStatusVariant('FAIL')).toBe('danger');
  // A check that could not run has established nothing. Colouring it green
  // would turn a source outage into a clean bill of health.
  expect(checkStatusVariant('UNKNOWN')).toBe('warning');
  expect(checkStatusVariant(undefined)).toBe('warning');
  expect(checkStatusColor('UNKNOWN')).not.toBe(checkStatusColor('PASS'));
});

test('check status labels are explicit about "not evaluated"', () => {
  expect(checkStatusLabel('PASS')).toBe('Pass');
  expect(checkStatusLabel('FAIL')).toBe('Fail');
  expect(checkStatusLabel('UNKNOWN')).toBe('Not evaluated');
});

test('the analysis panel renders the stored checks and their status', () => {
  const src = panels();
  expect(src).toContain('data-testid="operational-checks"');
  expect(src).toContain('data-testid={`operational-check-${check.key}`}');
  expect(src).toContain('data-status={String(check.status ?? \'\').toUpperCase()}');
  expect(src).toContain('checkGlyph(check.status)');
});

test('an analysis with no recorded checks says so instead of rendering an empty list', () => {
  const src = panels();
  expect(src).toContain('data-testid="checks-unavailable"');
  expect(src).toContain('CHECKS_UNAVAILABLE_COPY');
});

// -- 15. Critical anomaly styling --------------------------------------------
test('a critical operational anomaly is labelled and styled as one', () => {
  expect(conclusionLabel('CRITICAL_OPERATIONAL_ANOMALY')).toBe('CRITICAL OPERATIONAL ANOMALY');
  expect(conclusionVariant('CRITICAL_OPERATIONAL_ANOMALY')).toBe('danger');
});

test('an indeterminate conclusion is never rendered as authorized or as an anomaly', () => {
  expect(conclusionLabel('INDETERMINATE')).toBe('NOT ESTABLISHED');
  expect(conclusionVariant('INDETERMINATE')).toBe('warning');
  // An unknown/absent conclusion falls back to "not established", never to a
  // reassuring default.
  expect(conclusionLabel(null)).toBe('NOT ESTABLISHED');
  expect(conclusionLabel('SOMETHING_NEW')).toBe('NOT ESTABLISHED');
  expect(conclusionVariant(undefined)).toBe('warning');
});

test('an authorized conclusion is the only success styling', () => {
  expect(conclusionVariant('OPERATIONALLY_AUTHORIZED')).toBe('success');
  expect(conclusionLabel('OPERATIONALLY_AUTHORIZED')).toBe('OPERATIONALLY AUTHORIZED');
});

test('the conclusion is rendered from the backend value', () => {
  const src = panels();
  expect(src).toContain('data-testid="operational-conclusion"');
  expect(src).toContain('conclusionLabel(analysis.conclusion)');
  expect(src).toContain('data-conclusion=');
});

test('reason codes render as sentences without inventing meaning', () => {
  expect(reasonCodeLabel('NO_MATCHING_AUTHORIZED_ISSUANCE')).toBe('No matching authorized issuance');
  expect(reasonCodeLabel('SETTLEMENT_DEADLINE_EXCEEDED')).toBe('Settlement deadline exceeded');
  expect(reasonCodeLabel(null)).toBe('—');
  // An unknown code is shown, not swallowed.
  expect(reasonCodeLabel('SOME_NEW_CODE')).toBe('some new code');
});

// -- Amounts ------------------------------------------------------------------
test('base-unit amounts are formatted without losing precision', () => {
  expect(formatAmount('5000000', 0, { signed: true })).toBe('+5,000,000');
  expect(formatAmount('0', 0)).toBe('0');
  // A uint256-range amount keeps every digit — a Number() round-trip would not.
  const huge = '123456789012345678901234567890123456789';
  expect(formatAmount(huge, 0)).toBe('123,456,789,012,345,678,901,234,567,890,123,456,789');
});

test('token decimals scale the digit string rather than dividing a float', () => {
  expect(formatAmount('5000000', 6)).toBe('5');
  expect(formatAmount('5000001', 6)).toBe('5.000001');
  expect(formatAmount('1', 6)).toBe('0.000001');
});

test('a missing amount reads as unavailable, never as zero', () => {
  expect(formatAmount(null, 0)).toBe('—');
  expect(formatAmount(undefined, 0)).toBe('—');
  expect(formatAmount('', 0)).toBe('—');
});

test('the unit is appended only when the backend recorded one', () => {
  expect(formatAmount('100', 0, { unit: 'USTB' })).toBe('100 USTB');
  expect(formatAmount('100', 0, { unit: null })).toBe('100');
});

// -- Telemetry truthfulness ---------------------------------------------------
test('telemetry sources are labelled as what they actually are', () => {
  expect(telemetrySourceLabel('rpc_polling')).toBe('RPC polling');
  expect(telemetrySourceLabel('realtime_websocket')).toBe('WebSocket');
  expect(telemetrySourceLabel('quicknode_stream')).toBe('Streams');
  expect(telemetrySourceLabel(null)).toBe('Not recorded');
});

test('"Preconfirmed" is only ever shown for a real preconfirmation stage', () => {
  expect(telemetryStageLabel('FINALIZED')).toBe('Finalized block');
  expect(telemetryStageLabel('CONFIRMED')).toBe('Confirmed block');
  expect(telemetryStageLabel('PRECONFIRMATION')).toBe('Preconfirmation');
  expect(telemetryStageLabel(null)).toBe('Stage not recorded');
  expect(isPreconfirmed('FINALIZED')).toBe(false);
  expect(isPreconfirmed('PRECONFIRMATION')).toBe(true);
  expect(isPreconfirmed(null)).toBe(false);
});

test('an unrecorded stage is a warning, not a neutral shrug', () => {
  expect(telemetryStageVariant('FINALIZED')).toBe('neutral');
  expect(telemetryStageVariant(null)).toBe('warning');
});

test('the preconfirmation age is only rendered beside a real timestamp', () => {
  expect(preconfirmationAge(null)).toBeNull();
  expect(preconfirmationAge('not-a-date')).toBeNull();
  const now = Date.parse('2026-08-29T12:00:00.000Z');
  expect(preconfirmationAge('2026-08-29T11:59:59.620Z', now)).toBe('380 ms ago');
  // The panel gates the whole row on the stage, so no age can appear without one.
  expect(panels()).toContain("isPreconfirmed(detail.telemetry_stage) && detail.preconfirmation_received_at");
});

/** Strip comments so the assertion is about what SHIPS, not about prose that
 *  explains why a claim is absent. */
function withoutComments(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
}

test('the screen contains no hardcoded latency or Flashblocks claim', () => {
  const src = withoutComments(
    `${screen()}\n${panels()}\n${appSource('threat-monitoring/operational-integrity.ts')}`,
  );
  expect(src).not.toContain('Flashblocks');
  expect(src).not.toContain('200 ms live');
  expect(src).not.toMatch(/>\s*Live\s*·\s*\d+\s*ms/);
  // The only latency string the UI can produce comes from a real timestamp.
  expect(src).toContain('preconfirmationAge(detail.preconfirmation_received_at)');
});

// -- 16 + 17. Empty and degraded states are different -------------------------
test('the empty state states the period, not a clean bill of health', () => {
  expect(EMPTY_STATE_BODY).toBe('No operational integrity detections in the selected period.');
  const src = screen();
  expect(src).toContain('EMPTY_STATE_BODY');
  expect(src).toContain("title={operationalSelected ? 'No operational integrity detections' : 'No detections'}");
});

function coverage(overrides: Partial<OperationalCoverage> = {}): OperationalCoverage {
  return {
    state: 'LIVE',
    telemetry_source: 'rpc_polling',
    telemetry_stage: 'FINALIZED',
    last_issuance_telemetry_at: '2026-08-29T11:59:00.000Z',
    authoritative_sources: 1,
    authorized_records: 3,
    preconfirmation_available: false,
    reasons: [],
    ...overrides,
  };
}

test('live coverage shows no banner', () => {
  expect(coverageNotice(coverage())).toBeNull();
});

test('degraded coverage says coverage is limited and why', () => {
  const notice = coverageNotice(coverage({ state: 'DEGRADED', reasons: ['issuance_telemetry_stale'] }));
  expect(notice).not.toBeNull();
  expect(notice!.tone).toBe('warning');
  expect(notice!.text).toContain('limited telemetry coverage');
  expect(notice!.text).toContain('issuance telemetry is stale');
});

test('a provider outage never reads as "no threats detected"', () => {
  const notice = coverageNotice(coverage({ state: 'UNAVAILABLE', reasons: ['no_authoritative_source'] }));
  expect(notice!.text).toContain('not evaluating this workspace');
  expect(notice!.text).toContain('Absence of detections is not evidence');
  expect(notice!.text).not.toContain('No threats');
});

test('the telemetry source line reports the real lane', () => {
  expect(coverageSourceLine(coverage())).toBe('Telemetry Source: RPC polling · Finalized block');
  expect(coverageSourceLine(coverage({ telemetry_source: null }))).toContain('none recorded');
  expect(coverageSourceLine(null)).toBe('Telemetry Source: not recorded');
});

test('unsupported detectors are named rather than counted as zero', () => {
  const src = screen();
  expect(src).toContain('data-testid="operational-unsupported-note"');
  expect(src).toContain('a count of zero would not mean they ran');
});

// -- AI authority -------------------------------------------------------------
test('AI text carries an explanation-only authority label', () => {
  const src = panels();
  expect(src).toContain('data-testid="ai-authority-label"');
  expect(src).toContain('AI Analysis: Explanation only');
});

test('the panel renders the deterministic conclusion outside the AI block', () => {
  const src = panels();
  const conclusionAt = src.indexOf('data-testid="operational-conclusion"');
  const aiAt = src.indexOf('data-testid="ai-authority-label"');
  expect(conclusionAt).toBeGreaterThan(-1);
  expect(aiAt).toBeGreaterThan(conclusionAt);
});

// -- 18. The existing Screen 5 keeps working ----------------------------------
test('all four tabs are still present', () => {
  const src = screen();
  expect(src).toContain("{ key: 'overview', label: 'Overview' }");
  expect(src).toContain("{ key: 'telemetry', label: 'Telemetry' }");
  expect(src).toContain("{ key: 'detections', label: 'Detections' }");
  expect(src).toContain("{ key: 'anomalies', label: 'Anomalies' }");
});

test('the screen is still one route with no second navigation system', () => {
  const page = appSource('(product)/threat/page.tsx');
  expect(page).toContain('export default function ThreatPage');
  expect(page).toContain('<ThreatMonitoringScreen />');
  expect(screen()).toContain("router.push(`/threat?${params.toString()}`");
});

test('the existing Investigate action is preserved', () => {
  const src = screen();
  expect(src).toContain("onInvestigate(d.id)");
  expect(src).toContain("{investigatingId === d.id ? 'Opening…' : 'Investigate'}");
});

test('the Telemetry and Anomalies tabs are untouched by the operational lane', () => {
  const src = screen();
  expect(src).toContain('function TelemetryTab(');
  expect(src).toContain('function AnomaliesTab(');
  expect(src).toContain("category: 'security'");
});

test('a cyber detection is identified as such in the table', () => {
  expect(isOperationalIntegrity('CYBER_SECURITY')).toBe(false);
  expect(isOperationalIntegrity('OPERATIONAL_INTEGRITY')).toBe(true);
  expect(isOperationalIntegrity(null)).toBe(false);
});

test('observed/expected cells stay empty for a cyber row rather than showing 0', () => {
  const src = screen();
  expect(src).toContain("? formatAmount(d.observed_amount, d.amount_decimals, { signed: true, unit: d.amount_unit })\n                          : '—'");
});

// -- Responsive ---------------------------------------------------------------
test('the detail panels use an auto-fit grid so they stack on narrow layouts', () => {
  const src = panels();
  expect(src).toContain("gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))'");
});

test('the screen reuses the shared design-system primitives', () => {
  const src = panels();
  expect(src).toContain("from '../components/ui-primitives'");
  expect(src).toContain('className="dataCard"');
  expect(src).toContain('<StatusPill');
  // No new colour system: every colour is a design token with a fallback.
  expect(src).not.toMatch(/color:\s*'#[0-9a-f]{3,8}'/i);
});
