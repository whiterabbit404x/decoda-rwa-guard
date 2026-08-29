/**
 * Screen 3 — NOT_APPLICABLE is a valid TERMINAL state, and the actions must say so.
 *
 * The reported symptom: for a plain Wallet the tab correctly returned
 * NOT_APPLICABLE / SUPPLY_RECONCILIATION_NOT_APPLICABLE, and then offered "Run
 * reconciliation" beside the drawer's "Run again" — two run controls for a
 * workflow that cannot run — while the Authoritative card told the operator four
 * fields were "Not configured", implying a transfer agent they should go and set
 * up. Neither is true of an asset supply reconciliation does not apply to.
 *
 * The contract asserted here:
 *   * NOT_APPLICABLE is neutral — never green, never red, never a warning amber,
 *     and never "healthy": other controls may still detect risk on the asset,
 *   * no reconciliation action is offered as live for it, and the two run
 *     controls can never appear together,
 *   * "not required" is distinguished from "not configured" everywhere,
 *   * and every OTHER domain state (A-E below) keeps its own distinct verdict,
 *     variance and CTA — the renderer is driven by asset type + deterministic
 *     backend status, never by a check for one particular asset.
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
  authoritativeCardState,
  authoritativeRequirement,
  freshnessLabel,
  isAnomalyStatus,
  isHealthyStatus,
  reconcileAction,
  reconciliationResultTone,
  reconciliationStatusVariant,
  reconciliationView,
  riskImpactAbsentLabel,
} from '../app/asset-integrity-presentation';

const read = (...segments: string[]) => fs.readFileSync(path.join(__dirname, '..', ...segments), 'utf-8');
const panelSrc = read('app', 'asset-integrity-panel.tsx');
const presentationSrc = read('app', 'asset-integrity-presentation.ts');
const managerSrc = read('app', 'assets-manager.tsx');
const stylesSrc = read('app', 'styles.css');

/* ── The five domain states (item 9) ──────────────────────────────── */

/** A. WALLET — supply reconciliation does not apply. */
const walletPayload = {
  state: 'not_configured',
  reconcile_enabled: true,
  asset: { id: 'a1', name: 'Test MetaMask Wallet', asset_type: 'wallet', chain_network: 'base-mainnet' },
  onchain_state: {
    availability: 'NOT_CONFIGURED',
    total_supply_applicability: 'NOT_APPLICABLE',
    asset_type: 'wallet',
    asset_chain_network: 'base-mainnet',
    asset_address: '0xc0ffee0000000000000000000000000000000001',
    available: false, total_supply: null, observed_at: null,
  },
  authoritative_state: {
    requirement: 'NOT_REQUIRED', availability: 'NOT_APPLICABLE',
    source_status: 'missing', available: false,
  },
  reconciliation: null,
  reconciliation_view: {
    evaluated: false, status: 'NOT_APPLICABLE', reason_code: 'SUPPLY_RECONCILIATION_NOT_APPLICABLE',
    variance_units: null, severity: null, rule_id: null, rule_version: null,
    evaluated_at: null, evidence_count: 0,
  },
  investigation: { available: false },
};

/** B. RWA TOKEN + missing authoritative source. */
const sourceMissingPayload = {
  state: 'not_evaluated',
  reconcile_enabled: true,
  asset: { id: 'b1', name: 'Tokenized Treasury 2028', asset_type: 'contract', rwa_asset_type: 'tokenized_treasury' },
  onchain_state: {
    availability: 'AVAILABLE', total_supply_applicability: 'APPLICABLE',
    available: true, total_supply: '5000000', observed_at: '2026-08-28T11:59:00Z',
  },
  authoritative_state: { requirement: 'REQUIRED', availability: 'NOT_CONFIGURED', source_status: 'missing', available: false },
  reconciliation: null,
  reconciliation_view: {
    evaluated: false, status: 'MISSING_AUTHORITATIVE_DATA', reason_code: 'AUTHORITATIVE_SOURCE_MISSING',
    variance_units: null, severity: null, evidence_count: 0,
  },
  investigation: { available: false },
};

/** C. RWA TOKEN + no usable on-chain observation. */
const insufficientPayload = {
  state: 'not_configured',
  reconcile_enabled: true,
  asset: { id: 'c1', name: 'Tokenized Treasury 2028', asset_type: 'contract', rwa_asset_type: 'tokenized_treasury' },
  onchain_state: {
    availability: 'NOT_CONFIGURED', total_supply_applicability: 'APPLICABLE',
    available: false, total_supply: null, observed_at: null,
  },
  authoritative_state: { requirement: 'REQUIRED', availability: 'NOT_CONFIGURED', source_status: 'missing', available: false },
  reconciliation: null,
  reconciliation_view: {
    evaluated: false, status: 'INSUFFICIENT_EVIDENCE', reason_code: 'ONCHAIN_OBSERVATION_MISSING',
    variance_units: null, severity: null, evidence_count: 0,
  },
  investigation: { available: false },
};

/** D. RWA TOKEN + matched state. */
const reconciledPayload = {
  state: 'evaluated',
  reconcile_enabled: true,
  asset: { id: 'd1', name: 'Tokenized Treasury 2028', asset_type: 'contract' },
  onchain_state: { availability: 'AVAILABLE', total_supply_applicability: 'APPLICABLE', available: true, total_supply: '4500000' },
  authoritative_state: {
    requirement: 'REQUIRED', availability: 'AVAILABLE', source_status: 'reported',
    available: true, expected_total_supply: '4500000', stale: false,
  },
  reconciliation: {
    status: 'RECONCILED', reason_code: 'SUPPLY_MATCHES_AUTHORITATIVE_STATE', variance_units: '0',
    severity: 'low', rule_id: 'RP-17', rule_version: 4, evaluated_at: '2026-08-28T12:00:00Z', evidence_count: 4,
  },
  reconciliation_view: {
    evaluated: true, status: 'RECONCILED', reason_code: 'SUPPLY_MATCHES_AUTHORITATIVE_STATE',
    variance_units: '0', severity: 'low', rule_id: 'RP-17', rule_version: 4,
    evaluated_at: '2026-08-28T12:00:00Z', evidence_count: 4,
  },
  investigation: { available: false },
};

/** E. RWA TOKEN + unauthorized issuance. */
const anomalyPayload = {
  state: 'evaluated',
  reconcile_enabled: true,
  asset: { id: 'e1', name: 'Demo Seed Tokenized Bond', asset_type: 'contract' },
  onchain_state: { availability: 'AVAILABLE', total_supply_applicability: 'APPLICABLE', available: true, total_supply: '5000000' },
  authoritative_state: {
    requirement: 'REQUIRED', availability: 'AVAILABLE', source_status: 'reported',
    available: true, expected_total_supply: '4500000', stale: false,
  },
  reconciliation: {
    status: 'UNEXPLAINED_VARIANCE', reason_code: 'NO_MATCHING_AUTHORIZED_ISSUANCE', variance_units: '500000',
    severity: 'critical', rule_id: 'RP-17', rule_version: 4,
    evaluated_at: '2026-08-28T12:00:00Z', evidence_count: 6, canonical_event_id: 'evt-1',
  },
  reconciliation_view: {
    evaluated: true, status: 'UNEXPLAINED_VARIANCE', reason_code: 'NO_MATCHING_AUTHORIZED_ISSUANCE',
    variance_units: '500000', severity: 'critical', rule_id: 'RP-17', rule_version: 4,
    evaluated_at: '2026-08-28T12:00:00Z', evidence_count: 6, canonical_event_id: 'evt-1',
  },
  investigation: { available: true, incident_id: null, destination: '/alerts?alertId=al-1' },
};

/* ── 1. NOT_APPLICABLE is a valid terminal state ──────────────────── */
test('A: a wallet resolves to NOT_APPLICABLE with no variance and no severity', () => {
  const view = reconciliationView(walletPayload);
  expect(view.status).toBe('NOT_APPLICABLE');
  expect(view.reason_code).toBe('SUPPLY_RECONCILIATION_NOT_APPLICABLE');
  expect(view.variance_units).toBeNull();
  expect(view.severity).toBeNull();
  // Not an error, not missing evidence, not stale, not an anomaly, not healthy.
  expect(isAnomalyStatus(view.status)).toBe(false);
  expect(isHealthyStatus(view.status)).toBe(false);
  expect(view.reason_code).not.toBe('MISSING_AUTHORITATIVE_DATA');
  expect(view.reason_code).not.toBe('INSUFFICIENT_EVIDENCE');
});

test('a terminal not-applicable result is neither Complete nor Limited', () => {
  // "Limited" promises a verdict once the missing evidence arrives; nothing is
  // owed here, so the assessment is its own answer.
  const assessment = assessorView(walletPayload, reconciliationView(walletPayload));
  expect(assessment.assessment).toBe('Not applicable');
  expect(assessment.assessment_reason).toBe('SUPPLY_RECONCILIATION_NOT_APPLICABLE');
  expect(assessment.risk_impact).toBeNull();
  expect(riskImpactAbsentLabel('NOT_APPLICABLE')).toBe('Not applicable');
});

test('NOT_APPLICABLE never borrows a severity-derived risk impact', () => {
  // The engine scores a non-applicable dimension 'low' because it is not a
  // data-quality gap to chase. Rendering that as "Low" would read as a clean
  // bill of health this control never gave.
  const snapshot = {
    status: 'NOT_APPLICABLE', reason_code: 'SUPPLY_RECONCILIATION_NOT_APPLICABLE',
    severity: 'low', variance_units: null, rule_id: 'RP-17', rule_version: 4,
    evaluated_at: '2026-08-28T12:00:00Z', evidence_count: 0,
  };
  const persisted = {
    ...walletPayload,
    reconciliation: snapshot,
    reconciliation_view: { ...snapshot, evaluated: true },
    ai_assessment_view: { explanation: 'Does not apply.', risk_impact: 'Low', next_steps: [], source: 'deterministic' },
  };
  const view = reconciliationView(persisted);
  expect(view.evaluated).toBe(true);
  const assessment = assessorView(persisted, view);
  expect(assessment.risk_impact).toBeNull();
  expect(assessment.assessment).toBe('Not applicable');
});

/* ── 2. Actions for NOT_APPLICABLE ────────────────────────────────── */
test('Run reconciliation is not offered as a live action for NOT_APPLICABLE', () => {
  const action = reconcileAction(walletPayload, reconciliationView(walletPayload));
  expect(action.enabled).toBe(false);
  expect(action.hint).toBe('Supply reconciliation does not apply to this asset type.');
  // Stated beside the control, not hidden in a tooltip, and the control is not
  // silently removed (a vanishing button reads as a bug).
  expect(panelSrc).toContain('{runReconciliation.visible && !runReconciliation.enabled ? (');
  expect(panelSrc).toContain('<span className="integrityFooterNote">{runReconciliation.hint}</span>');
  expect(panelSrc).toContain('disabled={reconciling || !runReconciliation.enabled}');
});

test('the reconcile handler refuses to start a run the state cannot produce', () => {
  // Guarded in the handler as well as on the button, so no code path can POST it.
  expect(panelSrc).toContain('if (reconciling || !runReconciliation.enabled) return;');
});

test('every other state keeps Run reconciliation live', () => {
  for (const payload of [sourceMissingPayload, insufficientPayload, reconciledPayload, anomalyPayload]) {
    const action = reconcileAction(payload, reconciliationView(payload));
    expect(action.visible).toBe(true);
    expect(action.enabled).toBe(true);
  }
  // ...and it disappears entirely where the workspace disables on-demand runs.
  expect(reconcileAction({ ...anomalyPayload, reconcile_enabled: false }, reconciliationView(anomalyPayload)).visible).toBe(false);
});

test('"Run reconciliation" and "Run again" can never appear together', () => {
  // They are DIFFERENT runs — one re-scores asset risk, the other reconciles
  // supply — so the risk-assessment button is scoped to the tabs whose content
  // it belongs to, rather than sitting under the Integrity workspace.
  expect(managerSrc).toContain('{ASSESSMENT_BACKED_TABS.includes(tab) ? (');
  expect(managerSrc).toContain('{assessDisplay.actionLabel}');
  // The reconciliation control lives in the Integrity panel, and only there.
  expect(managerSrc).not.toContain('Run reconciliation');
  expect(panelSrc).toContain('runReconciliation.label');
  // Integrity is not an assessment-backed tab, so the two never coexist.
  const tabs = managerSrc.match(/export const ASSESSMENT_BACKED_TABS[^;]+;/);
  expect(tabs).not.toBeNull();
  expect((tabs as RegExpMatchArray)[0]).not.toContain("'integrity'");
});

/* ── 3. Authoritative state: not required vs not configured ───────── */
test('a wallet needs no authoritative ledger, so nothing is reported as unconfigured', () => {
  const card = authoritativeCardState(walletPayload);
  expect(card.requirement).toBe('NOT_REQUIRED');
  expect(card.availability).toBe('NOT_APPLICABLE');
  // The rows read "Not applicable", never "Not configured" — the latter is an
  // instruction to go and configure a transfer agent that changes nothing here.
  expect(absentValueLabel(card.availability)).toBe('Not applicable');
  expect(absentValueLabel(card.availability)).not.toBe('Not configured');
  // The summary row states applicability rather than availability.
  expect(authoritativeApplicabilityRow(card)).toEqual({ label: 'Applicability', value: 'Not required', variant: 'neutral' });
  // Freshness is a dash: nothing is required, so nothing can be out of date.
  expect(freshnessLabel(walletPayload.authoritative_state, card.availability).label).toBe('—');
  // ...and the card says why, without naming a setup step.
  expect(panelSrc).toContain('No authoritative supply ledger is required because supply reconciliation does not apply');
});

test('a token asset with no system of record is still Not configured', () => {
  const card = authoritativeCardState(sourceMissingPayload);
  expect(card.requirement).toBe('REQUIRED');
  expect(card.availability).toBe('NOT_CONFIGURED');
  expect(authoritativeApplicabilityRow(card)).toEqual({ label: 'Availability', value: 'Not configured', variant: 'neutral' });
  // The existing explanation of why nothing can be reconciled is kept.
  expect(panelSrc).toContain('nothing to reconcile the chain against');
});

test('requirement falls back to the canonical applicability fact for an older API', () => {
  // A frontend deployed ahead of the API still distinguishes the two states.
  const legacyWallet = { ...walletPayload, authoritative_state: { source_status: 'missing', available: false } };
  expect(authoritativeRequirement(legacyWallet)).toBe('NOT_REQUIRED');
  expect(authoritativeCardState(legacyWallet).availability).toBe('NOT_APPLICABLE');

  const legacyToken = { ...sourceMissingPayload, authoritative_state: { source_status: 'missing', available: false } };
  expect(authoritativeRequirement(legacyToken)).toBe('REQUIRED');
  expect(authoritativeCardState(legacyToken).availability).toBe('NOT_CONFIGURED');
});

test('applicability never hides a source that actually reported', () => {
  // Recorded state is always described on its own terms, whatever the asset type
  // says — "not applicable" must never suppress a real value.
  const walletWithLedger = {
    ...walletPayload,
    authoritative_state: { source_status: 'reported', available: true, expected_total_supply: '10', stale: false },
  };
  expect(authoritativeCardState(walletWithLedger).availability).toBe('AVAILABLE');
});

/* ── 5. The assessor narrative ────────────────────────────────────── */
test('the not-applicable narrative explains, and never warns', () => {
  const view = reconciliationView(walletPayload);
  const assessment = assessorView({ ...walletPayload, ai_assessment_view: null }, view);
  expect(assessment.explanation).toContain('does not apply');
  expect(assessment.explanation).toContain('no token total supply');
  // NOT_APPLICABLE is not HEALTHY: other controls may still detect risk.
  expect(assessment.explanation).toContain('not a clean bill of health');
  // ...and it never describes a gap someone could close.
  expect(assessment.explanation).not.toContain('has not been collected');
  expect(assessment.explanation).not.toContain('stale');
});

/* ── 6. The next action ───────────────────────────────────────────── */
test('the not-applicable state offers no CTA that duplicates the drawer', () => {
  const cta = assessorCta(walletPayload, reconciliationView(walletPayload));
  // No button at all: "Configure Monitoring Source" would be a dead end, and a
  // second Monitoring Sources link would duplicate the one the asset drawer
  // already carries under every tab — the very duplication being fixed here.
  expect(cta.kind).toBe('none');
  expect(cta.destination).toBeNull();
  expect(cta.label).not.toContain('Configure');
  // The drawer already carries that link under every tab, which is why the card
  // must not add a second one.
  expect(managerSrc).toContain('href="/monitoring-sources"');
  // It still says why, and that this is not a clean result for the asset.
  expect(cta.hint).toContain('nothing to configure for it here');
  expect(cta.hint).toContain('Other monitoring controls still apply');
  expect(cta.hint).toContain('not a clean bill of health');
  // Registering a token contract is the ONLY thing that would make supply
  // reconciliation apply, and it is named on the On-Chain card — where the
  // absent field actually is — rather than as a button to an editor that this
  // build does not have.
  expect(panelSrc).toContain('Register a\n          token contract if it should be reconciled against one.');
  // The configure CTA still exists where configuring IS the fix.
  expect(assessorCta(sourceMissingPayload, reconciliationView(sourceMissingPayload)).destination)
    .toBe(MONITORING_SOURCES_ROUTE);
});

/* ── 7. Colour semantics ──────────────────────────────────────────── */
test('NOT_APPLICABLE is neutral — never red, green, or a warning amber', () => {
  expect(reconciliationStatusVariant('NOT_APPLICABLE')).toBe('neutral');
  expect(reconciliationResultTone(reconciliationView(walletPayload))).toBe('integrityResultNeutral');
  expect(stylesSrc).toContain('.integrityResultNeutral');
  // The neutral tone borrows the plain card surface, not a status colour.
  const rule = (stylesSrc.match(/\.integrityResultNeutral\s*\{[^}]*\}/) as RegExpMatchArray)[0];
  for (const token of ['--danger', '--success', '--warning']) {
    expect(rule).not.toContain(token);
  }
});

test('each status keeps its own reserved colour', () => {
  expect(reconciliationResultTone(reconciliationView(anomalyPayload))).toBe('integrityResultCritical');
  expect(reconciliationResultTone(reconciliationView(reconciledPayload))).toBe('integrityResultOk');
  for (const payload of [sourceMissingPayload, insufficientPayload]) {
    expect(reconciliationResultTone(reconciliationView(payload))).toBe('integrityResultWarning');
  }
  // Green stays reserved for a RECORDED verdict that says the asset reconciles.
  const projectedHealthy = { ...reconciliationView(sourceMissingPayload), status: 'RECONCILED' };
  expect(reconciliationResultTone(projectedHealthy)).not.toBe('integrityResultOk');
});

/* ── 8. Typography ────────────────────────────────────────────────── */
test('the values, verdict and narrative are readable without changing the layout', () => {
  const size = (selector: string, property = 'font-size') => {
    const block = stylesSrc.match(new RegExp(`\\${selector}\\s*(?:,[^{]*)?\\{[^}]*\\}`)) as RegExpMatchArray;
    const found = block[0].match(new RegExp(`${property}:\\s*([\\d.]+)rem`)) as RegExpMatchArray;
    return Number(found[1]);
  };
  expect(size('.integrityKvRow')).toBeGreaterThanOrEqual(0.86);
  expect(size('.integrityKvLabel')).toBeGreaterThanOrEqual(0.7);
  expect(size('.integrityMono')).toBeGreaterThanOrEqual(0.78);
  expect(size('.integrityResultVariance')).toBeGreaterThanOrEqual(1.25);
  expect(size('.integrityResultMeaning')).toBeGreaterThanOrEqual(0.8);
  expect(size('.integrityAiText')).toBeGreaterThanOrEqual(0.88);
  expect(size('.integrityFooterNote')).toBeGreaterThanOrEqual(0.76);
  // The verdict pill itself is scaled up; it is the first thing read.
  expect(stylesSrc).toContain('.integrityResultStatus .sharedStatusPill');
  // Enterprise density is preserved: the drawer and the cards are untouched.
  expect(stylesSrc).toContain('.drawerCardWide { width: min(clamp(1080px, 76vw, 1600px), 100%); }');
  expect(stylesSrc).toContain('padding: 0.9rem 1rem;');
});

/* ── 9. The five states stay distinct ─────────────────────────────── */
test('A-E resolve to five distinct verdicts, variances and CTAs', () => {
  const cases = [
    { name: 'A wallet', payload: walletPayload, status: 'NOT_APPLICABLE',
      reason: 'SUPPLY_RECONCILIATION_NOT_APPLICABLE', variance: null, cta: 'none' },
    { name: 'B token, no source', payload: sourceMissingPayload, status: 'MISSING_AUTHORITATIVE_DATA',
      reason: 'AUTHORITATIVE_SOURCE_MISSING', variance: null, cta: 'configure' },
    { name: 'C token, no observation', payload: insufficientPayload, status: 'INSUFFICIENT_EVIDENCE',
      reason: 'ONCHAIN_OBSERVATION_MISSING', variance: null, cta: 'configure' },
    { name: 'D token, matched', payload: reconciledPayload, status: 'RECONCILED',
      reason: 'SUPPLY_MATCHES_AUTHORITATIVE_STATE', variance: '0', cta: 'none' },
    { name: 'E token, unauthorized issuance', payload: anomalyPayload, status: 'UNEXPLAINED_VARIANCE',
      reason: 'NO_MATCHING_AUTHORIZED_ISSUANCE', variance: '500000', cta: 'investigate' },
  ];
  for (const c of cases) {
    const view = reconciliationView(c.payload);
    expect(view.status, c.name).toBe(c.status);
    expect(view.reason_code, c.name).toBe(c.reason);
    expect(view.variance_units, c.name).toBe(c.variance);
    expect(assessorCta(c.payload, view).kind, c.name).toBe(c.cta);
  }
  // Five inputs, five different verdicts — none collapses into another.
  expect(new Set(cases.map((c) => c.status)).size).toBe(5);
});

test('E still reaches the investigation workflow with its evidenced variance', () => {
  const view = reconciliationView(anomalyPayload);
  const cta = assessorCta(anomalyPayload, view);
  expect(cta.kind).toBe('investigate');
  expect(cta.enabled).toBe(true);
  expect(cta.label).toBe('Investigate Variance');
  expect(view.variance_units).toBe('500000');
  expect(view.severity).toBe('critical');
});

test('D reports a real zero variance rather than an absent one', () => {
  const view = reconciliationView(reconciledPayload);
  expect(view.evaluated).toBe(true);
  expect(view.variance_units).toBe('0');
  expect(isHealthyStatus(view.status)).toBe(true);
  expect(assessorView(reconciledPayload, view).assessment).toBe('Complete');
});

/* ── 10. Driven by asset type + backend status, not by one asset ──── */
test('the Integrity renderer names no particular asset', () => {
  // A future tokenized treasury must render the reconciliation workflow with no
  // further UI work, so nothing here may branch on an asset name or address.
  for (const source of [panelSrc, presentationSrc]) {
    expect(source.toLowerCase()).not.toContain('metamask');
    expect(source).not.toMatch(/asset(_|\.)?name\s*===/);
    expect(source).not.toMatch(/asset_type\s*===\s*'wallet'/);
  }
  // Applicability is a BACKEND fact the frontend only reads.
  expect(presentationSrc).toContain('total_supply_applicability');
  expect(presentationSrc).toContain("view.status === 'NOT_APPLICABLE'");
});

test('a tokenized treasury reaches the full reconciliation workflow unchanged', () => {
  // Same code path, different backend status: the workflow appears by itself.
  const treasury = reconciliationView(anomalyPayload);
  expect(reconcileAction(anomalyPayload, treasury).enabled).toBe(true);
  expect(authoritativeCardState(anomalyPayload).requirement).toBe('REQUIRED');
  expect(assessorCta(anomalyPayload, treasury).kind).toBe('investigate');
});
