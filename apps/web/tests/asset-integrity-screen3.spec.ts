/**
 * Screen 3 — Asset Integrity / Reconciliation.
 * Pure + source-contract tests (no browser required).
 *
 * The formatting tests exercise the real exported helpers. The source-contract
 * tests assert the truthfulness invariants that must never be relaxed:
 *   * the frontend performs no reconciliation math,
 *   * a missing/stale/unavailable source is never rendered as healthy,
 *   * the evidence count is the backend's count, never a constant,
 *   * the GET proxy has no write handler,
 *   * repeated Investigate clicks cannot open duplicate investigations.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import {
  ANOMALY_STATUSES,
  INDETERMINATE_STATUSES,
  MONITORING_SOURCES_ROUTE,
  absentValueLabel,
  assessorCta,
  assessorView,
  authoritativeAvailability,
  availabilityLabel,
  integrityBanner,
  onchainAvailability,
  reconciliationView,
  tokenSupplyApplicability,
  formatEvidenceCount,
  formatRule,
  formatSupply,
  formatUnits,
  formatVariance,
  formatVarianceUnits,
  freshnessLabel,
  integrityPanelState,
  investigateCta,
  isAnomalyStatus,
  isHealthyStatus,
  isIndeterminateStatus,
  parseUnits,
  reasonCodeLabel,
  reconciliationStatusLabel,
  reconciliationStatusMeaning,
  reconciliationStatusVariant,
  relativeTime,
  severityVariant,
  truncateHex,
  varianceDirection,
} from '../app/asset-integrity-presentation';

const read = (...segments: string[]) => fs.readFileSync(path.join(__dirname, '..', ...segments), 'utf-8');

const panelSrc = read('app', 'asset-integrity-panel.tsx');
const presentationSrc = read('app', 'asset-integrity-presentation.ts');
const managerSrc = read('app', 'assets-manager.tsx');
const proxySrc = read('app', 'api', 'assets', '[assetId]', 'integrity', 'route.ts');
const historyProxySrc = read('app', 'api', 'assets', '[assetId]', 'integrity', 'history', 'route.ts');
const reconcileProxySrc = read('app', 'api', 'assets', '[assetId]', 'integrity', 'reconcile', 'route.ts');
const investigateProxySrc = read('app', 'api', 'assets', '[assetId]', 'integrity', 'investigate', 'route.ts');
const sharedProxySrc = read('app', 'api', 'assets', '[assetId]', 'integrity', '_shared.ts');
const stylesSrc = read('app', 'styles.css');

/* ── Tabs ──────────────────────────────────────────────────────────── */
test('asset detail exposes the Overview / On-Chain / Off-Chain / Integrity / History / AI Risk tabs', () => {
  for (const label of ['Overview', 'On-Chain', 'Off-Chain', 'Integrity', 'History', 'AI Risk']) {
    expect(managerSrc).toContain(`label: '${label}'`);
  }
  expect(managerSrc).toContain('ASSET_DETAIL_TABS');
  expect(managerSrc).toContain('<TabStrip');
});

test('the Integrity tab renders the integrity panel', () => {
  expect(managerSrc).toContain("import AssetIntegrityPanel from './asset-integrity-panel'");
  expect(managerSrc).toContain(`{tab === 'integrity' ? <AssetIntegrityPanel assetId={String(asset.id)} view="integrity" /> : null}`);
  expect(managerSrc).toContain(`view="onchain"`);
  expect(managerSrc).toContain(`view="offchain"`);
  expect(managerSrc).toContain(`view="history"`);
});

test('the existing Overview / History behaviour is preserved, not deleted', () => {
  // Every pre-existing drawer section still exists; it is redistributed across tabs.
  for (const section of ['Configuration', 'Reserve coverage', 'Confidence &amp; completeness', 'Risk score breakdown', 'Active findings', 'Assessment history']) {
    expect(managerSrc).toContain(section);
  }
  expect(managerSrc).toContain('ASSESSMENT_BACKED_TABS');
});

/* ── Panel layout ─────────────────────────────────────────────────── */
test('integrity layout is on-chain + authoritative above, result + AI assessor below', () => {
  expect(panelSrc).toContain('On-Chain State');
  expect(panelSrc).toContain('Authoritative State');
  expect(panelSrc).toContain('Reconciliation Result');
  expect(panelSrc).toContain('AI Asset Risk Assessor');
  expect(panelSrc).toContain('integrityStateGrid');
  expect(panelSrc).toContain('integrityResultGridOuter');
  expect(stylesSrc).toContain('.integrityStateGrid');
  expect(stylesSrc).toContain('.integrityResultGridOuter');
});

test('the panel reuses the existing design system rather than adding a second one', () => {
  expect(panelSrc).toContain("from './components/ui-primitives'");
  expect(panelSrc).toContain('StatusPill');
  expect(panelSrc).toContain('btn btn-primary');
  expect(stylesSrc).toContain('var(--danger-bdr)');
  expect(stylesSrc).toContain('var(--success-bdr)');
  expect(stylesSrc).toContain('var(--warning-bdr)');
});

test('the panel performs no reconciliation math of its own', () => {
  // No supply/variance arithmetic in the component — it formats backend facts.
  expect(panelSrc).not.toMatch(/observed[_\s]*[-+]\s*expected/i);
  expect(panelSrc).not.toContain('variance =');
  expect(panelSrc).toContain('The reconciliation engine computed the supply, variance, reason code and severity');
});

/* ── Number formatting ────────────────────────────────────────────── */
test('positive variance formats with an explicit plus sign', () => {
  expect(formatVariance('500000')).toBe('+500,000');
  expect(formatVarianceUnits('500000')).toBe('+500,000 units');
});

test('negative variance formats with a minus sign', () => {
  expect(formatVariance('-500000')).toBe('-500,000');
  expect(formatVarianceUnits('-500000')).toBe('-500,000 units');
});

test('zero variance is unsigned', () => {
  expect(formatVariance('0')).toBe('0');
  expect(formatVariance(0)).toBe('0');
  expect(formatVarianceUnits('0')).toBe('0 units');
});

test('an unknown variance is never rendered as zero', () => {
  expect(formatVariance(null)).toBeNull();
  expect(formatVariance(undefined)).toBeNull();
  expect(formatVariance('')).toBeNull();
  expect(formatVariance('n/a')).toBeNull();
  expect(formatVarianceUnits(null)).toBe('Unavailable');
});

test('uint256-scale supplies survive without precision loss', () => {
  const huge = '115792089237316195423570985008687907853269984665640564039457584007913129639935';
  expect(parseUnits(huge)).toBe(BigInt(huge));
  expect(formatUnits('5000000000000000000000001')).toBe('5,000,000,000,000,000,000,000,001');
});

test('missing supply renders as Unavailable, never 0', () => {
  expect(formatSupply(null)).toBe('Unavailable');
  expect(formatSupply(undefined)).toBe('Unavailable');
  expect(formatSupply('5000000')).toBe('5,000,000');
});

test('variance direction drives the colour class', () => {
  expect(varianceDirection('500000')).toBe('positive');
  expect(varianceDirection('-500000')).toBe('negative');
  expect(varianceDirection('0')).toBe('zero');
  expect(varianceDirection(null)).toBe('unknown');
});

test('hashes and addresses are middle-truncated', () => {
  expect(truncateHex('0x91d2aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaac09f')).toBe('0x91d2…c09f');
  expect(truncateHex('')).toBe('Unavailable');
});

test('relative time never invents a timestamp', () => {
  expect(relativeTime(null)).toBe('Unavailable');
  expect(relativeTime('not-a-date')).toBe('Unavailable');
  const now = Date.parse('2026-08-28T12:00:00Z');
  expect(relativeTime('2026-08-28T11:59:52Z', now)).toBe('8 sec ago');
});

/* ── Reason codes + rule ──────────────────────────────────────────── */
test('reason codes render verbatim (never AI-reworded)', () => {
  expect(reasonCodeLabel('NO_MATCHING_AUTHORIZED_ISSUANCE')).toBe('NO_MATCHING_AUTHORIZED_ISSUANCE');
  expect(reasonCodeLabel('AMOUNT_MISMATCH')).toBe('AMOUNT_MISMATCH');
  expect(reasonCodeLabel(null)).toBe('—');
});

test('the rule label comes from the backend rule id and version', () => {
  expect(formatRule('RP-17', 4)).toBe('RP-17-v4');
  expect(formatRule('RP-17', null)).toBe('RP-17');
  expect(formatRule(null, 4)).toBe('Unavailable');
  // The reference screenshot's rule is never hardcoded in the component.
  expect(panelSrc).not.toContain('RP-17');
});

test('evidence count is the backend count, never a constant', () => {
  expect(formatEvidenceCount(6)).toBe('6 artifacts');
  expect(formatEvidenceCount(1)).toBe('1 artifact');
  expect(formatEvidenceCount(0)).toBe('0 artifacts');
  // An absent count is NOT a confident zero.
  expect(formatEvidenceCount(null)).toBe('Unknown');
  expect(formatEvidenceCount(undefined)).toBe('Unknown');
  expect(formatEvidenceCount('')).toBe('Unknown');
  expect(panelSrc).toContain('formatEvidenceCount(view.evidence_count)');
  expect(panelSrc).not.toMatch(/6\s*artifacts/);
});

test('no screenshot data is hardcoded in the production UI', () => {
  for (const literal of ['US Treasury Bond', '5,000,000', '4,500,000', '+500,000', 'SUB-81922', 'Demo Transfer Agent']) {
    expect(panelSrc).not.toContain(literal);
    expect(managerSrc).not.toContain(literal);
  }
});

/* ── Status truthfulness ──────────────────────────────────────────── */
test('only a backend RECONCILED/AUTHORIZED result is styled as success', () => {
  expect(reconciliationStatusVariant('RECONCILED')).toBe('success');
  expect(reconciliationStatusVariant('AUTHORIZED_VARIANCE')).toBe('success');
  for (const status of INDETERMINATE_STATUSES) {
    expect(reconciliationStatusVariant(status)).not.toBe('success');
  }
  expect(reconciliationStatusVariant(null)).toBe('neutral');
  expect(reconciliationStatusVariant('SOMETHING_NEW')).toBe('neutral');
});

test('an unexplained variance is the only status styled as a critical anomaly', () => {
  expect(reconciliationStatusVariant('UNEXPLAINED_VARIANCE')).toBe('danger');
  // Nothing we could not establish is ever styled as an anomaly or as healthy.
  for (const status of INDETERMINATE_STATUSES) {
    expect(reconciliationStatusVariant(status)).not.toBe('danger');
    expect(reconciliationStatusVariant(status)).not.toBe('success');
  }
  // An unresolved gap is amber, because it is pending on someone.
  for (const status of ['SOURCE_UNAVAILABLE', 'MISSING_AUTHORITATIVE_DATA',
                        'STALE_AUTHORITATIVE_DATA', 'INSUFFICIENT_EVIDENCE']) {
    expect(reconciliationStatusVariant(status)).toBe('warning');
  }
  // A check that does not apply is pending on nobody, so it is neutral, not amber.
  expect(reconciliationStatusVariant('NOT_APPLICABLE')).toBe('neutral');
  expect(ANOMALY_STATUSES).toEqual(['UNEXPLAINED_VARIANCE']);
});

test('an unavailable or stale source is neither healthy nor an anomaly', () => {
  for (const status of ['SOURCE_UNAVAILABLE', 'MISSING_AUTHORITATIVE_DATA', 'STALE_AUTHORITATIVE_DATA', 'INSUFFICIENT_EVIDENCE']) {
    expect(isHealthyStatus(status)).toBe(false);
    expect(isAnomalyStatus(status)).toBe(false);
    expect(isIndeterminateStatus(status)).toBe(true);
    const meaning = reconciliationStatusMeaning(status);
    expect(meaning).toContain('not an anomaly');
    expect(meaning).toContain('not a clean bill of health');
  }
});

test('an unknown status is never treated as healthy', () => {
  expect(isHealthyStatus('SOMETHING_NEW')).toBe(false);
  expect(isHealthyStatus(null)).toBe(false);
  expect(isHealthyStatus(undefined)).toBe(false);
});

test('status labels are human-readable', () => {
  expect(reconciliationStatusLabel('UNEXPLAINED_VARIANCE')).toBe('Unexplained Variance');
  expect(reconciliationStatusLabel('SOURCE_UNAVAILABLE')).toBe('Source Unavailable');
});

test('severity maps to the shared pill vocabulary', () => {
  expect(severityVariant('critical')).toBe('danger');
  expect(severityVariant('high')).toBe('danger');
  expect(severityVariant('medium')).toBe('warning');
  expect(severityVariant('low')).toBe('success');
  expect(severityVariant(null)).toBe('neutral');
});

/* ── Panel states ─────────────────────────────────────────────────── */
test('loading state', () => {
  expect(integrityPanelState(null, { loading: true })).toBe('loading');
  expect(panelSrc).toContain('aria-busy="true"');
  expect(panelSrc).toContain('skelBlock');
});

test('error state does not fall back to a healthy render', () => {
  expect(integrityPanelState(null, { loading: false, error: 'boom' })).toBe('error');
  expect(integrityPanelState(null, { loading: false })).toBe('error');
  expect(panelSrc).toContain('role="alert"');
});

test('not-configured and not-evaluated states are distinct and never claim health', () => {
  expect(integrityPanelState({ state: 'not_configured' }, { loading: false })).toBe('not_configured');
  expect(integrityPanelState({ state: 'not_evaluated' }, { loading: false })).toBe('not_evaluated');
  // Neither state may read as a clean bill of health, wherever it is surfaced.
  expect(presentationSrc).toContain('Nothing here asserts that the asset is healthy.');
  expect(presentationSrc).toContain('This is not evidence that the asset is healthy.');
});

test('evaluated state renders the persisted backend result', () => {
  expect(integrityPanelState({ state: 'evaluated', reconciliation: { status: 'RECONCILED' } }, { loading: false })).toBe('evaluated');
  // A payload with a result but no declared state still renders it.
  expect(integrityPanelState({ reconciliation: { status: 'RECONCILED' } }, { loading: false })).toBe('evaluated');
});

test('unavailable on-chain / authoritative values render an explicit unavailable state', () => {
  expect(panelSrc).toContain('function Unavailable');
  expect(panelSrc).toContain('No on-chain supply observation is stored for this asset');
  expect(panelSrc).toContain('No authoritative off-chain state is recorded for this asset');
  expect(stylesSrc).toContain('.integrityUnavailable');
});

test('simulator and replay evidence are labelled, never shown as live', () => {
  expect(panelSrc).toContain("state.evidence_source !== 'live'");
  expect(panelSrc).toContain("'Simulator'");
  expect(panelSrc).toContain("'Replay'");
});

test('a stale source is badged as stale', () => {
  expect(panelSrc).toContain('state?.stale ?');
  expect(panelSrc).toContain('label="Stale"');
  expect(availabilityLabel('STALE')).toEqual({ label: 'Stale', variant: 'warning' });
});

/* ── AI availability ──────────────────────────────────────────────── */
test('the AI panel works when AI is unavailable', () => {
  expect(panelSrc).toContain("assessment.source === 'ai' ? 'AI narrative' : 'Deterministic narrative'");
  // The reconciliation result is rendered independently of the AI panel.
  expect(panelSrc).toContain('<ReconciliationResultCard');
  expect(panelSrc).toContain('<AiAssessorCard');
});

/* ── Investigate Variance CTA ─────────────────────────────────────── */
test('CTA is disabled when there is no anomaly', () => {
  const cta = investigateCta({ reconciliation: { status: 'RECONCILED' }, investigation: { available: false } });
  expect(cta.enabled).toBe(false);
  expect(cta.label).toBe('Investigate Variance');
  expect(cta.destination).toBeNull();
});

test('CTA is disabled — with an honest reason — for an indeterminate result', () => {
  const cta = investigateCta({ reconciliation: { status: 'SOURCE_UNAVAILABLE' }, investigation: { available: false } });
  expect(cta.enabled).toBe(false);
  expect(cta.hint).toContain('could not establish a verdict');
});

test('CTA is enabled for an unexplained variance with a canonical event', () => {
  const cta = investigateCta({
    reconciliation: { status: 'UNEXPLAINED_VARIANCE', canonical_event_id: 'evt-1' },
    investigation: { available: true, incident_id: null, destination: '/alerts?alertId=a-1' },
  });
  expect(cta.enabled).toBe(true);
  expect(cta.label).toBe('Investigate Variance');
  expect(cta.destination).toBe('/alerts?alertId=a-1');
  expect(cta.hint).toContain('No response action is executed');
});

test('an existing incident is navigated to, never duplicated', () => {
  const cta = investigateCta({
    reconciliation: { status: 'UNEXPLAINED_VARIANCE', canonical_event_id: 'evt-1' },
    investigation: { available: true, incident_id: 'INC-1', destination: '/incidents/INC-1' },
  });
  expect(cta.enabled).toBe(true);
  expect(cta.label).toBe('View Incident');
  expect(cta.destination).toBe('/incidents/INC-1');
});

test('an anomaly without a canonical event cannot be investigated', () => {
  const cta = investigateCta({
    reconciliation: { status: 'UNEXPLAINED_VARIANCE', canonical_event_id: null },
    investigation: { available: false },
  });
  expect(cta.enabled).toBe(false);
  expect(cta.hint).toContain('No canonical operational-integrity event');
});

test('repeated Investigate clicks are guarded in the component', () => {
  expect(panelSrc).toContain("if (investigating || cta.kind !== 'investigate' || !cta.enabled) return;");
  expect(panelSrc).toContain('setInvestigating(true)');
  expect(panelSrc).toContain('disabled={!cta.enabled || investigating}');
});

test('the CTA never triggers a response action', () => {
  expect(panelSrc).not.toContain('response-action');
  expect(panelSrc).not.toContain('/execute');
});

/* ── Proxy transport ──────────────────────────────────────────────── */
test('the integrity read proxy exposes no write handler', () => {
  expect(proxySrc).toContain('export async function GET');
  expect(proxySrc).not.toContain('export async function POST');
  expect(proxySrc).not.toContain('export async function PATCH');
  expect(proxySrc).not.toContain('export async function DELETE');
  expect(historyProxySrc).toContain('export async function GET');
  expect(historyProxySrc).not.toContain('export async function POST');
});

test('write proxies are POST-only and forward the CSRF token', () => {
  expect(reconcileProxySrc).toContain('export async function POST');
  expect(reconcileProxySrc).not.toContain('export async function GET');
  expect(investigateProxySrc).toContain('export async function POST');
  expect(investigateProxySrc).not.toContain('export async function GET');
  expect(sharedProxySrc).toContain("headers.set('X-CSRF-Token', csrfToken)");
});

test('every proxy call is authorized and workspace-scoped', () => {
  expect(sharedProxySrc).toContain('missing_authorization');
  expect(sharedProxySrc).toContain("headers.set('X-Workspace-Id', workspaceId)");
  expect(sharedProxySrc).toContain('normalizeWorkspaceHeaderValue');
  // No provider secret is ever forwarded to the browser.
  expect(sharedProxySrc).not.toMatch(/api[_-]?key/i);
});

test('reads are never cached, so a reload shows backend truth', () => {
  expect(proxySrc).toContain("export const dynamic = 'force-dynamic'");
  expect(proxySrc).toContain('export const revalidate = 0');
  expect(sharedProxySrc).toContain("cache: 'no-store'");
  expect(panelSrc).toContain("cache: 'no-store'");
});

test('the presentation module documents that it decides nothing', () => {
  expect(presentationSrc).toContain('none of them decides one');
  expect(presentationSrc).toContain('The frontend never computes a supply');
});


/* ── Freshness (item 6) ───────────────────────────────────────────── */
test('freshness reports the backend staleness verdict, never a UI-computed one', () => {
  expect(freshnessLabel({ source_status: 'reported', stale: false })).toEqual({ label: 'Current', variant: 'success' });
  expect(freshnessLabel({ source_status: 'reported', stale: true })).toEqual({ label: 'Stale', variant: 'warning' });
});

test('an unconfigured authoritative source reads as Not configured, never as fresh', () => {
  expect(freshnessLabel({ source_status: 'missing', stale: null })).toEqual({ label: 'Not configured', variant: 'neutral' });
  expect(freshnessLabel(null)).toEqual({ label: 'Unknown', variant: 'neutral' });
  expect(freshnessLabel(undefined)).toEqual({ label: 'Unknown', variant: 'neutral' });
});

test('a source that did not report is never labelled Current, whatever its age', () => {
  // stale=false only means "the stored timestamp is recent" — it cannot make an
  // unavailable/errored source current.
  for (const status of ['unavailable', 'error', 'degraded']) {
    expect(freshnessLabel({ source_status: status, stale: false })).toEqual({ label: 'Not reported', variant: 'warning' });
  }
});

test('an unknown staleness verdict is neutral, never green', () => {
  const unknown = freshnessLabel({ source_status: 'reported', stale: null });
  expect(unknown.label).toBe('Unknown');
  expect(unknown.variant).not.toBe('success');
});

test('freshness is a label chooser, not a staleness calculation', () => {
  // The threshold comparison belongs to the backend; the helper must not
  // re-derive it from an age.
  const helper = presentationSrc.slice(presentationSrc.indexOf('export function freshnessLabel'));
  expect(helper).not.toMatch(/age_seconds\s*[<>]/);
  expect(helper).not.toContain('Date.now()');
});

/* ── Authoritative "Not configured" (item 6) ──────────────────────── */
test('a missing system of record is stated as an explicit labelled field', () => {
  // The card renders ONE unconditional set of rows, so "Not configured" is a
  // labelled value on the Source row rather than a separate collapsed branch.
  expect(authoritativeAvailability({ source_status: 'missing' })).toBe('NOT_CONFIGURED');
  expect(absentValueLabel('NOT_CONFIGURED')).toBe('Not configured');
  expect(panelSrc).toContain('<Absent availability={availability} />');
  // ...and the explanation of why nothing can be reconciled is kept.
  expect(panelSrc).toContain('nothing to reconcile the chain against');
});

test('Freshness renders for every authoritative state, configured or not', () => {
  const occurrences = panelSrc.match(/<Row label="Freshness">/g) || [];
  expect(occurrences.length).toBe(1);
  // One unconditional row, outside any branch — it cannot be skipped.
  expect(panelSrc).toContain('<Row label="Freshness"><StatusPill label={freshness.label} variant={freshness.variant} /></Row>');
  expect(panelSrc).toContain('const freshness = freshnessLabel(state);');
});

test('a missing authoritative source never renders an expected supply', () => {
  // Expected Units is gated on the source having actually REPORTED a value, so a
  // missing or failed source can never print a number (and never a 0).
  expect(panelSrc).toContain('{reported && state?.expected_total_supply != null');
  expect(authoritativeAvailability({ source_status: 'missing' })).toBe('NOT_CONFIGURED');
  expect(authoritativeAvailability({ source_status: 'unavailable' })).toBe('SOURCE_UNAVAILABLE');
  expect(authoritativeAvailability({ source_status: 'reported', available: false })).toBe('SOURCE_UNAVAILABLE');
});

/* ── Drawer width (item 3) ────────────────────────────────────────── */
test('the asset detail drawer is viewport-relative and wide enough for the 2x2 layout', () => {
  const rule = stylesSrc.match(/\.drawerCardWide \{ width: ([^;]+); \}/);
  expect(rule).not.toBeNull();
  const width = (rule as RegExpMatchArray)[1];
  // Viewport-relative, inside the 70-78vw target band.
  const vw = Number((width.match(/(\d+)vw/) as RegExpMatchArray)[1]);
  expect(vw).toBeGreaterThanOrEqual(70);
  expect(vw).toBeLessThanOrEqual(78);
  // A sensible max-width cap, and a floor no narrower than the previous 1080px.
  expect(width).toContain('clamp(1080px');
  expect(width).toContain('1600px');
});

test('the drawer never causes horizontal scrolling and goes full width on small screens', () => {
  // The floor is bounded by the viewport, so it can never overflow.
  expect(stylesSrc).toContain('.drawerCardWide { width: min(clamp(1080px, 76vw, 1600px), 100%); }');
  expect(stylesSrc).toMatch(/@media \(max-width: 900px\) \{[\s\S]*?\.drawerCardWide \{ width: 100%; \}/);
});

test('the drawer stays a drawer over the asset list — no separate detail route', () => {
  expect(managerSrc).toContain('drawerCard drawerCardWide');
  expect(managerSrc).toContain('className="drawerOverlay"');
  expect(stylesSrc).toContain('.drawerOverlay { position: fixed');
  // The Integrity work must not have introduced an /assets/[assetId] page.
  expect(fs.existsSync(path.join(__dirname, '..', 'app', '(product)', 'assets', '[assetId]'))).toBe(false);
});

test('cards stack rather than scroll sideways on a narrow viewport', () => {
  expect(stylesSrc).toMatch(/\.integrityStateGrid,\s*\.integrityResultGridOuter,\s*\.integrityResultGrid \{ grid-template-columns: minmax\(0, 1fr\); \}/);
  expect(stylesSrc).toContain('.integrityCard {');
  expect(stylesSrc).toContain('min-width: 0;');
});
