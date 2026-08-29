/**
 * Screen 3 — the Integrity workspace must ALWAYS render its four panels.
 *
 * The regression this file exists for: production showed the whole tab as one
 * sentence, "Asset integrity state is unavailable right now.", because a non-2xx
 * from /assets/{id}/integrity replaced the entire workspace.
 *
 * The contract asserted here:
 *   * ON-CHAIN STATE / AUTHORITATIVE STATE / RECONCILIATION RESULT /
 *     AI ASSET RISK ASSESSOR render for EVERY state — healthy, anomalous,
 *     missing, stale, unavailable, not-applicable, never-evaluated, and a hard
 *     API failure,
 *   * a domain state is never rendered as an error, and an error never as a
 *     domain state,
 *   * no variance is ever shown without a persisted evaluation that produced one,
 *   * "Not applicable" (a wallet has no token supply) is never shown as
 *     "Unavailable".
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import {
  MONITORING_SOURCES_ROUTE,
  absentValueLabel,
  assessorCta,
  assessorView,
  authoritativeApplicabilityRow,
  authoritativeAvailability,
  authoritativeCardState,
  authoritativeRequirement,
  availabilityLabel,
  freshnessLabel,
  integrityBanner,
  isAnomalyStatus,
  isHealthyStatus,
  onchainAvailability,
  reconcileAction,
  reconciliationResultTone,
  reconciliationStatusLabel,
  reconciliationStatusVariant,
  reconciliationMeaning,
  reconciliationView,
  riskImpactAbsentLabel,
  tokenSupplyApplicability,
} from '../app/asset-integrity-presentation';

const read = (...segments: string[]) => fs.readFileSync(path.join(__dirname, '..', ...segments), 'utf-8');
const panelSrc = read('app', 'asset-integrity-panel.tsx');
const stylesSrc = read('app', 'styles.css');

/* ── Payload fixtures ─────────────────────────────────────────────── */

/** The production asset in the report: a Wallet on base-mainnet with nothing configured. */
const walletPayload = {
  state: 'not_configured',
  asset: { id: 'a1', name: 'Test MetaMask Wallet', asset_type: 'wallet', chain_network: 'base-mainnet' },
  onchain_state: {
    availability: 'NOT_CONFIGURED',
    total_supply_applicability: 'NOT_APPLICABLE',
    asset_type: 'wallet',
    asset_chain_network: 'base-mainnet',
    asset_address: '0xc0ffee0000000000000000000000000000000001',
    available: false,
    total_supply: null,
    observed_at: null,
  },
  authoritative_state: { availability: 'NOT_CONFIGURED', source_status: 'missing', available: false },
  reconciliation: null,
  reconciliation_view: {
    evaluated: false,
    // A wallet address has no token total supply, so the blocker is applicability
    // — not an observation someone could go and collect.
    status: 'NOT_APPLICABLE',
    reason_code: 'SUPPLY_RECONCILIATION_NOT_APPLICABLE',
    variance_units: null,
    severity: null,
    rule_id: null,
    rule_version: null,
    evaluated_at: null,
    evidence_count: 0,
  },
  ai_assessment_view: {
    explanation: 'Supply reconciliation does not apply to Test MetaMask Wallet: this asset has no token total supply to reconcile.',
    risk_impact: null,
    next_steps: [],
    source: 'deterministic',
    assessment: 'Limited',
  },
  investigation: { available: false },
};

/**
 * A token asset that IS reconcilable, observed on-chain, with no system of record
 * configured — the honest MISSING_AUTHORITATIVE_DATA case. Kept separate from the
 * wallet: for the wallet, configuring a source would change nothing.
 */
const sourceMissingPayload = {
  state: 'not_evaluated',
  asset: { id: 'a3', name: 'Demo Seed Tokenized Bond', asset_type: 'contract' },
  onchain_state: {
    availability: 'AVAILABLE',
    total_supply_applicability: 'APPLICABLE',
    available: true,
    total_supply: '5000000',
    observed_at: '2026-08-28T11:59:00Z',
  },
  authoritative_state: { availability: 'NOT_CONFIGURED', source_status: 'missing', available: false },
  reconciliation: null,
  reconciliation_view: {
    evaluated: false,
    status: 'MISSING_AUTHORITATIVE_DATA',
    reason_code: 'AUTHORITATIVE_SOURCE_MISSING',
    variance_units: null,
    severity: null,
    rule_id: null,
    rule_version: null,
    evaluated_at: null,
    evidence_count: 0,
  },
  ai_assessment_view: {
    explanation: 'Asset integrity reconciliation cannot be completed because no authoritative operational source is configured for this asset.',
    risk_impact: null,
    next_steps: [],
    source: 'deterministic',
    assessment: 'Limited',
  },
  investigation: { available: false },
};

/** The demo anomaly fixture: observed 5,000,000 vs expected 4,500,000, +500,000 unmatched. */
const anomalyPayload = {
  state: 'evaluated',
  asset: { id: 'a2', name: 'Demo Seed Tokenized Bond', asset_type: 'contract' },
  onchain_state: {
    availability: 'AVAILABLE',
    total_supply_applicability: 'APPLICABLE',
    available: true,
    total_supply: '5000000',
    last_delta: '500000',
    last_delta_operation: 'mint',
    observed_at: '2026-08-28T11:59:00Z',
  },
  authoritative_state: {
    availability: 'AVAILABLE',
    source_status: 'reported',
    available: true,
    expected_total_supply: '4500000',
    stale: false,
  },
  reconciliation: {
    status: 'UNEXPLAINED_VARIANCE',
    reason_code: 'NO_MATCHING_AUTHORIZED_ISSUANCE',
    variance_units: '500000',
    severity: 'critical',
    rule_id: 'RP-17',
    rule_version: 4,
    evaluated_at: '2026-08-28T12:00:00Z',
    evidence_count: 6,
    canonical_event_id: 'evt-1',
  },
  reconciliation_view: {
    evaluated: true,
    status: 'UNEXPLAINED_VARIANCE',
    reason_code: 'NO_MATCHING_AUTHORIZED_ISSUANCE',
    variance_units: '500000',
    severity: 'critical',
    rule_id: 'RP-17',
    rule_version: 4,
    evaluated_at: '2026-08-28T12:00:00Z',
    evidence_count: 6,
    canonical_event_id: 'evt-1',
  },
  investigation: { available: true, incident_id: null, destination: '/alerts?alertId=al-1' },
};

/* ── The shell always renders ─────────────────────────────────────── */
test('the four panels are rendered unconditionally, not behind a state branch', () => {
  // Every card sits in the returned tree with no guard around the grids.
  expect(panelSrc).toContain('<div className="integrityStateGrid">');
  expect(panelSrc).toContain('<div className="integrityResultGridOuter">');
  for (const card of ['<OnChainStateCard', '<AuthoritativeStateCard', '<ReconciliationResultCard', '<AiAssessorCard']) {
    expect(panelSrc).toContain(card);
  }
  // The regression: no early return that replaces the workspace with a sentence.
  expect(panelSrc).not.toMatch(/if \(panelState === 'error'\)\s*\{?\s*return\s*</);
  expect(panelSrc).not.toMatch(/if \(!\w*[Ss]tate\)\s*\{\s*return\s*<p/);
  // The four panels are never swapped out for a paragraph.
  expect(panelSrc).not.toContain("panelState === 'not_configured' ? (");
});

test('a null payload still produces a complete, truthful four-card model', () => {
  const view = reconciliationView(null);
  expect(view.evaluated).toBe(false);
  expect(view.status).toBe('INSUFFICIENT_EVIDENCE');
  expect(view.variance_units).toBeNull();
  expect(view.severity).toBeNull();
  // An unknown evidence count is never a confident zero.
  expect(view.evidence_count).toBeNull();

  const assessment = assessorView(null, view);
  expect(assessment.explanation.length).toBeGreaterThan(0);
  expect(assessment.assessment).toBe('Limited');
  expect(assessment.risk_impact).toBeNull();

  expect(onchainAvailability(null)).toBe('UNKNOWN');
  expect(authoritativeAvailability(null)).toBe('UNKNOWN');
  // Unknown is never styled as healthy.
  expect(availabilityLabel('UNKNOWN').variant).toBe('neutral');
});

/* ── Error vs domain state ────────────────────────────────────────── */
test('only a real request failure produces a banner, and it never hides the panels', () => {
  expect(integrityBanner({ loading: false, error: '' })).toBeNull();
  expect(integrityBanner({ loading: true, error: 'boom' })).toBeNull();

  const banner = integrityBanner({ loading: false, error: 'Asset integrity state is unavailable right now.', httpStatus: 500 });
  expect(banner).not.toBeNull();
  expect(banner!.message).toContain('HTTP 500');
  expect(banner!.detail).toContain('Nothing here asserts that the asset is healthy.');

  // Rendered ABOVE the grids, inside the same layout — never instead of them.
  const gridAt = panelSrc.indexOf('<div className="integrityStateGrid">');
  const bannerAt = panelSrc.lastIndexOf('<ErrorBanner banner={banner}', gridAt);
  expect(bannerAt).toBeGreaterThan(-1);
  expect(gridAt).toBeGreaterThan(bannerAt);
  // The banner returns null when there is nothing to report, so a healthy state
  // never renders an empty alert box.
  expect(panelSrc).toContain('if (!banner) return null;');
  expect(stylesSrc).toContain('.integrityErrorBanner');
});

test('a domain state never renders as an error', () => {
  // Not configured / unavailable / stale are facts about the asset, so no banner.
  expect(integrityBanner({ loading: false, error: null })).toBeNull();
  for (const availability of ['NOT_CONFIGURED', 'SOURCE_UNAVAILABLE', 'STALE', 'NOT_APPLICABLE']) {
    expect(availabilityLabel(availability).variant).not.toBe('danger');
  }
});

/* ── Availability mapping (item 8) ────────────────────────────────── */
test('each availability reason maps to its own distinct label', () => {
  expect(absentValueLabel('NOT_APPLICABLE')).toBe('Not applicable');
  expect(absentValueLabel('NOT_CONFIGURED')).toBe('Not configured');
  expect(absentValueLabel('SOURCE_UNAVAILABLE')).toBe('Source unavailable');
  expect(absentValueLabel('STALE')).toBe('Stale');
  expect(absentValueLabel('UNKNOWN')).toBe('Unknown');
  // They are genuinely distinct — never collapsed into one generic word.
  const labels = ['NOT_APPLICABLE', 'NOT_CONFIGURED', 'SOURCE_UNAVAILABLE', 'STALE'].map(absentValueLabel);
  expect(new Set(labels).size).toBe(4);
});

test('a missing authoritative source is NOT_CONFIGURED, not an outage', () => {
  expect(authoritativeAvailability({ source_status: 'missing' })).toBe('NOT_CONFIGURED');
  expect(authoritativeAvailability({})).toBe('NOT_CONFIGURED');
});

test('a source that failed is SOURCE_UNAVAILABLE, not "not configured"', () => {
  expect(authoritativeAvailability({ source_status: 'unavailable', available: false })).toBe('SOURCE_UNAVAILABLE');
  expect(authoritativeAvailability({ source_status: 'error', available: false })).toBe('SOURCE_UNAVAILABLE');
  expect(availabilityLabel('SOURCE_UNAVAILABLE').variant).toBe('warning');
});

test('a stale source is STALE — never current, never an anomaly', () => {
  expect(authoritativeAvailability({ availability: 'STALE' })).toBe('STALE');
  expect(authoritativeAvailability({ source_status: 'reported', available: true, stale: true })).toBe('STALE');
  expect(availabilityLabel('STALE').variant).toBe('warning');
  expect(availabilityLabel('STALE').variant).not.toBe('success');
});

test('a fresh reported source is AVAILABLE', () => {
  expect(authoritativeAvailability({ source_status: 'reported', available: true, stale: false })).toBe('AVAILABLE');
  expect(onchainAvailability({ available: true, stale: false })).toBe('AVAILABLE');
  expect(onchainAvailability({ available: true, stale: true })).toBe('STALE');
  expect(onchainAvailability({ available: false })).toBe('NOT_CONFIGURED');
});

/* ── Wallet: NOT_APPLICABLE, not "Unavailable" (items 3 & 4) ──────── */
test('wallet token fields are Not applicable, never Unavailable', () => {
  expect(tokenSupplyApplicability(walletPayload.onchain_state)).toBe('NOT_APPLICABLE');
  expect(absentValueLabel('NOT_APPLICABLE')).toBe('Not applicable');
  expect(absentValueLabel('NOT_APPLICABLE')).not.toBe('Unavailable');
  // Both token rows are gated on applicability, so neither can print a supply.
  expect(panelSrc).toContain('const supplyApplies = tokenSupplyApplicability(state)');
  expect(panelSrc).toContain('<Absent availability="NOT_APPLICABLE" />');
});

test('the on-chain card still identifies a wallet with no observation', () => {
  // Asset type / network / address come from the registry and are always shown.
  for (const row of ['<Row label="Asset Type">', '<Row label="Network">', '<Row label="Address"', '<Row label="Observation">']) {
    expect(panelSrc).toContain(row);
  }
  expect(panelSrc).toContain('state?.asset_address');
});

test('a token-bearing asset keeps its supply rows applicable', () => {
  expect(tokenSupplyApplicability(anomalyPayload.onchain_state)).toBe('APPLICABLE');
  expect(tokenSupplyApplicability({})).toBe('APPLICABLE');
});

/* ── No fabricated variance (items 6 & 12) ────────────────────────── */
test('a missing authoritative source yields MISSING_AUTHORITATIVE_DATA and no variance', () => {
  const view = reconciliationView(sourceMissingPayload);
  expect(view.evaluated).toBe(false);
  expect(view.status).toBe('MISSING_AUTHORITATIVE_DATA');
  expect(view.reason_code).toBe('AUTHORITATIVE_SOURCE_MISSING');
  // Never UNEXPLAINED_VARIANCE — there is no baseline to compare against.
  expect(view.status).not.toBe('UNEXPLAINED_VARIANCE');
  expect(view.variance_units).toBeNull();
  expect(view.rule_id).toBeNull();
  expect(view.evaluated_at).toBeNull();
  expect(view.evidence_count).toBe(0);
});

test('an unevaluated view can never smuggle a variance, severity or rule through', () => {
  // Even if a payload claims them, an unevaluated view reports none of them: a
  // variance is only ever rendered from a persisted evaluation.
  const view = reconciliationView({
    reconciliation_view: {
      evaluated: false, status: 'MISSING_AUTHORITATIVE_DATA', reason_code: 'AUTHORITATIVE_SOURCE_MISSING',
      variance_units: '500000', severity: 'critical', rule_id: 'RP-17', rule_version: 4,
      evaluated_at: '2026-08-28T12:00:00Z', evidence_count: 0,
    },
  });
  expect(view.variance_units).toBeNull();
  expect(view.severity).toBeNull();
  expect(view.rule_id).toBeNull();
  expect(view.evaluated_at).toBeNull();
});

test('an unevaluated result card is never styled as a healthy one', () => {
  // Green is reserved for a recorded RECONCILED verdict — a card that was never
  // evaluated cannot borrow it, whatever status a malformed payload claims.
  const claimsHealth = { ...reconciliationView(sourceMissingPayload), status: 'RECONCILED' };
  expect(claimsHealth.evaluated).toBe(false);
  expect(reconciliationResultTone(claimsHealth)).not.toBe('integrityResultOk');
  expect(reconciliationResultTone(reconciliationView(sourceMissingPayload))).toBe('integrityResultWarning');
  expect(panelSrc).toContain('const tone = reconciliationResultTone(view);');
  expect(panelSrc).toContain('<StatusPill label="Not evaluated" variant="neutral" />');
});

test('the result card prints "Not calculated" rather than a number when nothing was evaluated', () => {
  expect(panelSrc).toContain("view.evaluated ? formatVarianceUnits(view.variance_units) : 'Not calculated'");
  expect(panelSrc).toContain("view.evaluated ? formatRule(view.rule_id, view.rule_version) : 'Not evaluated'");
});

test('a configured-but-never-evaluated asset gets its own sentence, not "no evidence"', () => {
  const notEvaluated = reconciliationView({
    reconciliation_view: { evaluated: false, status: 'INSUFFICIENT_EVIDENCE', reason_code: 'RECONCILIATION_NOT_EVALUATED', evidence_count: 0 },
  });
  const meaning = reconciliationMeaning(notEvaluated);
  expect(meaning).toContain('No reconciliation has been recorded for this asset yet');
  // Still explicitly neither an anomaly nor a clean result.
  expect(meaning).toContain('not an anomaly');
  expect(meaning).toContain('not a clean bill of health');

  // Other statuses keep their existing sentence.
  const missing = reconciliationView(sourceMissingPayload);
  expect(reconciliationMeaning(missing)).toContain('No authoritative state is recorded');
});

/* ── Real anomaly still works (item 13) ───────────────────────────── */
test('the same four panels render a real reconciliation anomaly', () => {
  const view = reconciliationView(anomalyPayload);
  expect(view.evaluated).toBe(true);
  expect(view.status).toBe('UNEXPLAINED_VARIANCE');
  expect(view.reason_code).toBe('NO_MATCHING_AUTHORIZED_ISSUANCE');
  expect(view.variance_units).toBe('500000');
  expect(view.severity).toBe('critical');
  expect(view.evidence_count).toBe(6);

  const assessment = assessorView(anomalyPayload, view);
  expect(assessment.assessment).toBe('Complete');
  expect(assessment.assessment_reason).toBeNull();
});

/* ── CTA (items 5 & 7) ────────────────────────────────────────────── */
test('Investigate Variance is offered only for an evidenced, persisted variance', () => {
  const cta = assessorCta(anomalyPayload, reconciliationView(anomalyPayload));
  expect(cta.kind).toBe('investigate');
  expect(cta.enabled).toBe(true);
  expect(cta.label).toBe('Investigate Variance');
});

test('no Investigate Variance CTA exists when no variance was established', () => {
  const cta = assessorCta(sourceMissingPayload, reconciliationView(sourceMissingPayload));
  expect(cta.kind).toBe('configure');
  expect(cta.label).not.toContain('Investigate');
  expect(cta.label).toBe('Configure Monitoring Source');
});

test('the configure CTA reuses the EXISTING Monitoring Sources workflow', () => {
  const cta = assessorCta(sourceMissingPayload, reconciliationView(sourceMissingPayload));
  expect(cta.destination).toBe(MONITORING_SOURCES_ROUTE);
  expect(MONITORING_SOURCES_ROUTE).toBe('/monitoring-sources');
  // No second configuration surface is introduced.
  expect(panelSrc).not.toContain('/integrity/configure');
  expect(panelSrc).not.toContain('authoritative-sources');
});

test('a projected anomaly can never enable an investigation', () => {
  // An unevaluated view claiming an anomaly still offers no investigation: only a
  // persisted snapshot can evidence one.
  const projected = {
    reconciliation: null,
    reconciliation_view: { evaluated: false, status: 'UNEXPLAINED_VARIANCE', variance_units: '500000' },
    investigation: { available: true, destination: '/alerts?alertId=x' },
  };
  const cta = assessorCta(projected, reconciliationView(projected));
  expect(cta.kind).toBe('configure');
});

test('a healthy result offers no CTA at all', () => {
  const healthy = {
    reconciliation: { status: 'RECONCILED', evidence_count: 3 },
    reconciliation_view: { evaluated: true, status: 'RECONCILED', reason_code: 'SUPPLY_MATCHES_AUTHORITATIVE_STATE', evidence_count: 3 },
  };
  const cta = assessorCta(healthy, reconciliationView(healthy));
  expect(cta.kind).toBe('none');
  expect(cta.enabled).toBe(false);
});

/* ── AI availability (item 7) ─────────────────────────────────────── */
test('the assessor card works with no AI and asks it to infer nothing', () => {
  const view = reconciliationView(sourceMissingPayload);
  const withoutAi = assessorView({ ...sourceMissingPayload, ai_assessment_view: null, ai_assessment: null }, view);
  expect(withoutAi.source).toBe('deterministic');
  expect(withoutAi.explanation).toContain('no authoritative operational source is configured');
  expect(withoutAi.risk_impact).toBeNull();
  expect(withoutAi.assessment).toBe('Limited');
});

test('risk impact is Not determined when no severity was ever computed', () => {
  // A default of "Low" would read as a clean result the engine never produced.
  const view = reconciliationView(walletPayload);
  expect(assessorView(walletPayload, view).risk_impact).toBeNull();
  expect(riskImpactAbsentLabel('INSUFFICIENT_EVIDENCE')).toBe('Not determined');
  expect(riskImpactAbsentLabel('MISSING_AUTHORITATIVE_DATA')).toBe('Not determined');
  // ...and a check that does not apply has no risk impact to determine at all.
  expect(riskImpactAbsentLabel('NOT_APPLICABLE')).toBe('Not applicable');
  expect(riskImpactAbsentLabel('NOT_APPLICABLE')).not.toBe('Low');
  expect(panelSrc).toContain('riskImpactAbsentLabel(status)');
});

test('an AI narrative is labelled as one, and cannot change the verdict', () => {
  const view = reconciliationView(anomalyPayload);
  const withAi = assessorView(
    { ...anomalyPayload, ai_assessment_view: { explanation: 'Reworded.', source: 'ai', risk_impact: 'Critical', next_steps: [] } },
    view,
  );
  expect(withAi.source).toBe('ai');
  expect(withAi.explanation).toBe('Reworded.');
  // The verdict itself still comes from the persisted snapshot.
  expect(view.status).toBe('UNEXPLAINED_VARIANCE');
  expect(view.variance_units).toBe('500000');
});

/* ── Backwards compatibility with an older API ────────────────────── */
test('a payload without the new view still renders truthfully', () => {
  // A frontend deployed ahead of the API must not blank the workspace.
  const legacyMissing = {
    onchain_state: { available: true, stale: false },
    authoritative_state: { source_status: 'missing', available: false },
    reconciliation: null,
  };
  const view = reconciliationView(legacyMissing);
  expect(view.evaluated).toBe(false);
  expect(view.status).toBe('MISSING_AUTHORITATIVE_DATA');
  expect(view.reason_code).toBe('AUTHORITATIVE_SOURCE_MISSING');
  expect(view.variance_units).toBeNull();

  const legacyEvaluated = { reconciliation: { status: 'RECONCILED', reason_code: 'SUPPLY_MATCHES_AUTHORITATIVE_STATE', evidence_count: 2 } };
  expect(reconciliationView(legacyEvaluated).evaluated).toBe(true);
  expect(reconciliationView(legacyEvaluated).status).toBe('RECONCILED');
});

test('the legacy fallback follows the engine precedence: on-chain gaps resolve first', () => {
  const noObservation = {
    onchain_state: { available: false },
    authoritative_state: { source_status: 'missing', available: false },
    reconciliation: null,
  };
  // An unusable observation is named before any authoritative gap, exactly as the
  // backend engine resolves it.
  expect(reconciliationView(noObservation).status).toBe('INSUFFICIENT_EVIDENCE');
  expect(reconciliationView(noObservation).reason_code).toBe('ONCHAIN_OBSERVATION_MISSING');

  const staleSource = {
    onchain_state: { available: true, stale: false },
    authoritative_state: { source_status: 'reported', available: true, stale: true },
    reconciliation: null,
  };
  expect(reconciliationView(staleSource).status).toBe('STALE_AUTHORITATIVE_DATA');
  expect(reconciliationView(staleSource).reason_code).toBe('AUTHORITATIVE_SOURCE_STALE');
});

/* ── Not applicable is not a missing observation (items 3, 4, 8, 14) ── */
/**
 * The production asset. A wallet address has no token total supply, so the tab
 * must not report a gap the operator can never close, and must not contradict
 * its own On-Chain card, which already reads "Not applicable".
 */
test('a wallet reports NOT_APPLICABLE, never a missing on-chain observation', () => {
  const view = reconciliationView(walletPayload);
  expect(view.status).toBe('NOT_APPLICABLE');
  expect(view.reason_code).toBe('SUPPLY_RECONCILIATION_NOT_APPLICABLE');
  expect(view.reason_code).not.toBe('ONCHAIN_OBSERVATION_MISSING');
  // The result card and the on-chain card agree.
  expect(tokenSupplyApplicability(walletPayload.onchain_state)).toBe('NOT_APPLICABLE');
});

test('a not-applicable result is neither healthy nor an anomaly', () => {
  const view = reconciliationView(walletPayload);
  expect(isHealthyStatus(view.status)).toBe(false);
  expect(isAnomalyStatus(view.status)).toBe(false);
  expect(view.evaluated).toBe(false);
  expect(reconciliationStatusVariant(view.status)).not.toBe('success');
  expect(reconciliationStatusVariant(view.status)).not.toBe('danger');
});

test('a not-applicable result carries no variance, severity or rule', () => {
  const view = reconciliationView(walletPayload);
  expect(view.variance_units).toBeNull();
  expect(view.severity).toBeNull();
  expect(view.rule_id).toBeNull();
  expect(view.evaluated_at).toBeNull();
});

test('NOT_APPLICABLE is distinguished from every other absent-data state', () => {
  // Item 8: these must not collapse into one another.
  const statuses = ['NOT_APPLICABLE', 'MISSING_AUTHORITATIVE_DATA', 'SOURCE_UNAVAILABLE',
                    'STALE_AUTHORITATIVE_DATA', 'INSUFFICIENT_EVIDENCE'];
  const labels = statuses.map(reconciliationStatusLabel);
  expect(new Set(labels).size).toBe(statuses.length);
  const meanings = statuses.map((status) => reconciliationMeaning({ ...reconciliationView(walletPayload), status }));
  expect(new Set(meanings).size).toBe(statuses.length);
});

test('a not-applicable asset is offered no dead-end Configure CTA', () => {
  // No monitoring source gives a wallet address a token total supply, so
  // offering to configure one would send the operator after a dead end.
  const cta = assessorCta(walletPayload, reconciliationView(walletPayload));
  expect(cta.kind).toBe('none');
  expect(cta.destination).toBeNull();
  expect(cta.label).not.toContain('Investigate');
  expect(cta.label).not.toContain('Configure');
  expect(cta.hint).toContain('does not apply');
  // ...and the state is still not reported as a clean result.
  expect(cta.hint).toContain('not a clean bill of health');
  expect(cta.hint).toContain('Other monitoring controls still apply');
});

test('the not-applicable narrative never claims data is missing', () => {
  const view = reconciliationView(walletPayload);
  const withoutAi = assessorView({ ...walletPayload, ai_assessment_view: null, ai_assessment: null }, view);
  expect(withoutAi.source).toBe('deterministic');
  expect(withoutAi.explanation).toContain('does not apply');
  expect(withoutAi.explanation).not.toContain('has not been collected');
  expect(withoutAi.explanation).not.toContain('no authoritative operational source is configured');
  // Still never a clean bill of health for the asset itself.
  expect(withoutAi.explanation).toContain('not a clean bill of health');
  expect(withoutAi.risk_impact).toBeNull();
  // Not "Limited": nothing further is owed, so the assessment is terminal.
  expect(withoutAi.assessment).toBe('Not applicable');
  expect(withoutAi.assessment).not.toBe('Complete');
  expect(withoutAi.assessment_reason).toBe('SUPPLY_RECONCILIATION_NOT_APPLICABLE');
});

test('the on-chain card does not promise a fix that cannot exist', () => {
  // The "link a monitoring target so supply is observed" note is gated on
  // applicability — it must not be shown for an asset that has no supply.
  expect(panelSrc).toContain('{!supplyApplies ? (');
  expect(panelSrc).toContain('supply reconciliation does not apply to it');
});

test('the four panels still render for a not-applicable asset', () => {
  // The shell invariant holds for this state like every other.
  const view = reconciliationView(walletPayload);
  expect(reconciliationStatusLabel(view.status)).toBe('Not Applicable');
  expect(integrityBanner({ loading: false, error: '', httpStatus: null })).toBeNull();
  for (const card of ['aria-label="On-chain state"', 'aria-label="Authoritative state"',
                      'aria-label="Reconciliation result"', 'aria-label="AI Asset Risk Assessor"']) {
    expect(panelSrc).toContain(card);
  }
});

test('the legacy fallback also resolves applicability before any evidence gap', () => {
  // An older API that sends no reconciliation_view must reach the same verdict.
  const legacy = { ...walletPayload, reconciliation_view: undefined, reconciliation: null };
  const view = reconciliationView(legacy);
  expect(view.status).toBe('NOT_APPLICABLE');
  expect(view.reason_code).toBe('SUPPLY_RECONCILIATION_NOT_APPLICABLE');
  expect(view.evaluated).toBe(false);
});
