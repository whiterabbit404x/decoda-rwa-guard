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
  formatEvidenceCount,
  formatRule,
  formatSupply,
  formatUnits,
  formatVariance,
  formatVarianceUnits,
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
  expect(panelSrc).toContain('formatEvidenceCount(reconciliation?.evidence_count)');
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
  for (const status of INDETERMINATE_STATUSES) {
    expect(reconciliationStatusVariant(status)).toBe('warning');
  }
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
  expect(panelSrc).toContain('Nothing here asserts that the asset is healthy.');
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
  expect(panelSrc).toContain('state.stale ?');
  expect(panelSrc).toContain('label="Stale"');
});

/* ── AI availability ──────────────────────────────────────────────── */
test('the AI panel works when AI is unavailable', () => {
  expect(panelSrc).toContain("assessment?.source === 'ai' ? 'AI narrative' : 'Deterministic narrative'");
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
  expect(panelSrc).toContain('if (investigating || !cta.enabled) return;');
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
