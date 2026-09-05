/**
 * Screen 7 — case-state consistency audit (presentation layer).
 *
 * Companion to `services/api/tests/test_incident_case_state_consistency.py`. The
 * backend suite pins what the API may CLAIM; this one pins the words an operator
 * actually reads, because a truthful payload rendered with the wrong noun is not
 * a truthful screen.
 *
 * Every test here exists because a real Case File showed a state combination that
 * could not be explained from the UI alone:
 *
 *   * "No linked detection" beside "Chain event recorded / Observed";
 *   * "Policy Not Found +1 more" beside an authoritative DENY;
 *   * "Not collected" beside "13 artifacts";
 *   * "Awaiting approval (2)" beside "5 recommended" — 2 of what?
 *   * "4 / 7 complete" with no visible stage model.
 *
 * Two layers, matching the repo's established frontend test style:
 *   1. Executable unit tests for the pure presentation helpers.
 *   2. Source-level structural tests asserting the components read canonical
 *      backend facts and fail closed.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import {
  buildIntegritySummary,
  evaluatedPolicyReference,
  evidenceDomainBreakdown,
  failClosedReason,
  incidentOriginLabel,
  investigationCoverage,
  missingDetectionExplanation,
  operationalOutcome,
  operationalOutcomeDetail,
  operationalOutcomeLabel,
  operationalOutcomeVariant,
  policyDecisionSourceDetail,
  policyDecisionSourceLabel,
  resolvePolicyDecisionSource,
  sectionCollected,
  summarizeResponseState,
  approvalQuorumLabel,
  type CaseResponseAction,
  type ForensicLoadState,
  type IncidentCaseSummary,
} from '../app/incident-forensics-presentation';

function appSource(fileName: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', fileName), 'utf-8');
}

function rowsFor(
  summary: IncidentCaseSummary | null,
  summaryLoad: ForensicLoadState,
  actions: CaseResponseAction[] = [],
  responseLoad: ForensicLoadState = 'ready',
) {
  const rows = buildIntegritySummary({
    summary,
    summaryLoad,
    response: summarizeResponseState(actions),
    responseLoad,
  });
  return Object.fromEntries(rows.map((row) => [row.key, row])) as Record<
    string,
    (typeof rows)[number]
  >;
}

/* ═════════ 1. No linked detection + an observed chain event ═════════ */

test('a chain observation without a detection is explained by the incident origin', () => {
  // The combination is legitimate: an alert-escalated case never had a Screen 5
  // detection, and its chain evidence comes from the evidence snapshot. The row
  // says which, so an empty Detection cannot read as a broken relationship.
  const summary: IncidentCaseSummary = {
    origin: { origin: 'alert', detection_linked: false, alert_linked: true },
    on_chain: { state: 'observed', collection_state: 'collected', fact_source: 'evidence_snapshot' },
  };
  const rows = rowsFor(summary, 'ready');
  expect(rows.detection.value).toBe('No linked detection');
  expect(rows.detection.detail).toBe('Origin: Alert');
  expect(rows.detection.recorded).toBe(false);
  // Absence still earns no badge — no data must not be shown as a verdict.
  expect(rows.detection.badge).toBeNull();
  expect(rows.on_chain.badge).toEqual({ label: 'Observed', variant: 'info' });
});

test('the missing-detection sentence names the origin instead of implying a broken link', () => {
  expect(missingDetectionExplanation({ origin: 'alert', detection_linked: false }))
    .toContain('raised from an alert');
  expect(missingDetectionExplanation({ origin: 'manual', detection_linked: false }))
    .toContain('opened directly');
  expect(missingDetectionExplanation({ origin: 'system', detection_linked: false }))
    .toContain('automated workflow');
  // Nothing recorded means nothing is asserted about how it began.
  expect(missingDetectionExplanation({ origin: 'unknown', detection_linked: false }))
    .toContain('no origin is recorded');
  // A linked detection has nothing to explain.
  expect(missingDetectionExplanation({ origin: 'detection', detection_linked: true })).toBe('');
});

test('origin labels cover every persisted value and never guess', () => {
  expect(incidentOriginLabel('detection')).toBe('Detection');
  expect(incidentOriginLabel('alert')).toBe('Alert');
  expect(incidentOriginLabel('manual')).toBe('Manual');
  expect(incidentOriginLabel('system')).toBe('System');
  expect(incidentOriginLabel('unknown')).toBe('Unknown');
  expect(incidentOriginLabel(null)).toBe('Unknown');
  expect(incidentOriginLabel('something-new')).toBe('Unknown');
});

test('no detection is fabricated for a case that has none', () => {
  const src = appSource('incident-case-overview.tsx');
  // The section renders the origin explanation, never a synthesised detection.
  expect(src).toContain('missingDetectionExplanation(origin)');
  expect(src).not.toContain('detection_type ??');
  expect(src).not.toContain("'Unknown detection'");
});

/* ═════════ 2. "Policy Not Found" + DENY ═════════════════════════════ */

test('a fail-closed DENY names its source and its reason, never a missing policy record', () => {
  const summary: IncidentCaseSummary = {
    policy: {
      state: 'decided', decision: 'DENY', decision_source: 'fail_closed_default',
      policy_key: null, policy_version: null,
      reason_codes: ['POLICY_NOT_FOUND', 'OPERATION_NOT_ESTABLISHED'],
    },
  };
  const row = rowsFor(summary, 'ready').policy;
  expect(row.badge).toEqual({ label: 'DENY', variant: 'danger' });
  // "No applicable policy" — a statement about governance, not about a lookup.
  expect(row.value).toBe('No applicable policy');
  expect(row.detail).toBe('Default fail-closed rule');
  // No policy identity is invented to fill the code slot.
  expect(row.code).toBeNull();
});

test('the fail-closed reason distinguishes an unmatched policy from an unestablished operation', () => {
  expect(failClosedReason({
    decision: 'DENY', decision_source: 'fail_closed_default',
    reason_codes: ['POLICY_NOT_FOUND'],
  })).toContain('No policy governs this operation');
  expect(failClosedReason({
    decision: 'DENY', decision_source: 'fail_closed_default',
    reason_codes: ['POLICY_NOT_FOUND', 'OPERATION_NOT_ESTABLISHED'],
  })).toContain('governed operation could not be established');
  // A policy's own verdict is explained by its identity, not by this sentence.
  expect(failClosedReason({
    decision: 'DENY', decision_source: 'matched_policy', policy_key: 'issuance-authorization',
  })).toBeNull();
});

test('a matched-policy decision is attributed to the policy that made it', () => {
  const summary: IncidentCaseSummary = {
    policy: {
      state: 'decided', decision: 'DENY', decision_source: 'matched_policy',
      policy_key: 'issuance-authorization', policy_version: 7,
      reason_codes: ['COMPLIANCE_APPROVAL_MISSING'],
    },
  };
  const row = rowsFor(summary, 'ready').policy;
  expect(row.detail).toBe('Matched policy');
  expect(row.code).toBe('issuance-authorization v7');
});

test('no policy evaluation is never rendered as a denial', () => {
  const row = rowsFor({ policy: { state: 'not_recorded' } }, 'ready').policy;
  expect(row.value).toBe('Not evaluated');
  expect(row.badge).toBeNull();
  expect(row.recorded).toBe(false);
  expect(policyDecisionSourceLabel(resolvePolicyDecisionSource({ state: 'not_recorded' })))
    .toBe('No evaluation recorded');
});

test('an unattributed decision is reported as unattributed, not as fail-closed', () => {
  // The fail-closed branch only ever produces DENY, so no other decision may
  // borrow that mechanism as its explanation.
  expect(resolvePolicyDecisionSource({ decision: 'ALLOW' })).toBe('unattributed');
  expect(resolvePolicyDecisionSource({ decision: 'DENY' })).toBe('fail_closed_default');
  expect(resolvePolicyDecisionSource({ decision: 'DENY', policy_key: 'p' })).toBe('matched_policy');
  expect(policyDecisionSourceLabel('unattributed')).toBe('Decision source not recorded');
  expect(policyDecisionSourceDetail('unattributed')).toContain('cannot be established');
});

test('the backend decision_source always wins over the local fallback', () => {
  // The fallback exists only for a payload that predates the field. A declared
  // source is never second-guessed.
  expect(resolvePolicyDecisionSource({
    decision: 'DENY', policy_key: 'issuance-authorization',
    decision_source: 'fail_closed_default',
  })).toBe('fail_closed_default');
});

/* ═════════ 3. Historical policy truth survives policy deletion ══════ */

test('the policy reference is read from the evaluation, so it survives a deleted policy', () => {
  // No current-policy lookup exists in this module: the key and version rendered
  // are the ones the evaluation row persisted.
  expect(evaluatedPolicyReference({ policy_key: 'issuance-authorization', policy_version: 7 }))
    .toBe('issuance-authorization v7');
  expect(evaluatedPolicyReference({ policy_key: 'issuance-authorization', policy_version: null }))
    .toBe('issuance-authorization');
  // A fail-closed refusal names no policy — inventing one would fabricate a link.
  expect(evaluatedPolicyReference({ policy_key: null, policy_version: null })).toBeNull();
});

test('the Overview renders the evaluation-time identity, not the current policy record', () => {
  const src = appSource('incident-case-overview.tsx');
  expect(src).toContain('Policy at evaluation');
  expect(src).toContain('evaluatedPolicyReference(policy)');
  expect(src).toContain('Decision source');
  // The engine version travels with the decision, so a re-run under a newer
  // engine cannot be mistaken for the verdict that actually gated the response.
  expect(src).toContain('policy.engine_version');
  // Screen 7 never fetches a policy to resolve a decision.
  expect(src).not.toContain('/policies');
});

/* ═════════ 4. NOT COLLECTED is not NOT MATCHED ═════════════════════ */

test('the five operational outcomes stay semantically separate', () => {
  expect(operationalOutcome({ state: 'not_recorded' })).toBe('not_collected');
  expect(operationalOutcome({ state: 'anomaly' })).toBe('not_matched');
  expect(operationalOutcome({ state: 'reconciled' })).toBe('matched');
  expect(operationalOutcome({ state: 'indeterminate' })).toBe('indeterminate');
  expect(operationalOutcome({ collection_state: 'error' })).toBe('error');
  expect(operationalOutcome({ state: 'decided' })).toBe('unknown');
});

test('not collected is neutral, never danger, and never worded as a mismatch', () => {
  expect(operationalOutcomeLabel('not_collected')).toBe('Not collected');
  expect(operationalOutcomeVariant('not_collected')).toBe('neutral');
  expect(operationalOutcomeDetail('not_collected')).toContain('nothing was compared');
  // Only a real failed comparison is danger.
  expect(operationalOutcomeVariant('not_matched')).toBe('danger');
  expect(operationalOutcomeDetail('not_matched')).toContain('did not match');
  // "Could not establish truth" is its own answer — never agreement.
  expect(operationalOutcomeVariant('indeterminate')).toBe('neutral');
  expect(operationalOutcomeLabel('indeterminate')).toBe('Could not be established');
});

test('an uncollected operational half carries no verdict badge in the Case File', () => {
  const rows = rowsFor({ operational: { state: 'not_recorded', collection_state: 'not_collected' } }, 'ready');
  expect(rows.operational.value).toBe('Not collected');
  expect(rows.operational.detail).toBe('Nothing was compared for this event');
  expect(rows.operational.badge).toBeNull();
  expect(rows.operational.recorded).toBe(false);
});

test('collection state prefers the backend field over the verdict state', () => {
  expect(sectionCollected({ state: 'anomaly', collection_state: 'not_collected' })).toBe(false);
  expect(sectionCollected({ state: 'not_recorded', collection_state: 'collected' })).toBe(true);
  // With no explicit field, the verdict state decides — the same rule the backend uses.
  expect(sectionCollected({ state: 'anomaly' })).toBe(true);
  expect(sectionCollected({ state: 'not_recorded' })).toBe(false);
  expect(sectionCollected(null)).toBe(false);
});

test('the reconciliation band says Unavailable, not Not matched, when nothing was collected', () => {
  const src = appSource('incident-case-overview.tsx');
  expect(src).toContain('No operational data was collected for this event');
  expect(src).toContain('operationalOutcomeVariant(outcome)');
});

/* ═════════ 5. Evidence counts grouped by domain ════════════════════ */

test('an artifact total is broken down by domain so it cannot imply operational evidence', () => {
  const breakdown = evidenceDomainBreakdown({
    on_chain: 4, operational: 0, policy: 3, human_actions: 6, total: 13,
  });
  // The zero domain is omitted rather than listed — and crucially, the total is
  // never presented as evidence that the operational half was collected.
  expect(breakdown).toBe('4 on-chain · 3 policy · 6 human actions');
  expect(breakdown).not.toContain('operational');
});

test('the evidence row states the domain split beside the total', () => {
  const summary: IncidentCaseSummary = {
    operational: { state: 'not_recorded', collection_state: 'not_collected' },
    evidence: {
      artifact_count: 13,
      counts: { on_chain: 4, operational: 0, policy: 3, human_actions: 6, total: 13 },
      snapshot_status: 'ready',
    },
  };
  const rows = rowsFor(summary, 'ready');
  expect(rows.evidence.value).toBe('13 artifacts');
  expect(rows.evidence.detail).toBe('4 on-chain · 3 policy · 6 human actions');
  // Both facts stand together without contradiction, because the split says so.
  expect(rows.operational.value).toBe('Not collected');
});

test('a domain the backend did not report is omitted, never rendered as zero', () => {
  expect(evidenceDomainBreakdown({ on_chain: 2 })).toBe('2 on-chain');
  expect(evidenceDomainBreakdown({})).toBeNull();
  expect(evidenceDomainBreakdown(null)).toBeNull();
});

test('the Evidence directory classifies by the persisted domain, never by AI', () => {
  const src = appSource('incident-evidence-tab.tsx');
  // Filtering and counting both read the backend's own domain field / count keys.
  expect(src).toContain('domainCount(counts, domain)');
  expect(src).toContain('filterArtifacts(artifacts, filter)');
  const presentation = appSource('incident-forensics-presentation.ts');
  expect(presentation).toContain('artifact.domain === filter');
  // Hashes are backend-derived: the UI shortens a digest, it never computes one.
  expect(presentation).toContain('export function shortDigest');
  expect(presentation).not.toContain('crypto.subtle');
  expect(presentation).not.toContain('createHash');
});

/* ═════════ 6. Response approval wording ════════════════════════════ */

test('every response count names its unit, so a bare number cannot be misread', () => {
  const twoPending: CaseResponseAction[] = [
    { id: 'a', approval_status: 'pending' },
    { id: 'b', approval_status: 'pending' },
    { id: 'c', approval_status: 'approved' },
    { id: 'd' },
    { id: 'e' },
  ];
  const state = summarizeResponseState(twoPending);
  // The observed ambiguity was "Awaiting approval (2)" beside "5 recommended".
  // These count ACTIONS, and now say so.
  expect(state.label).toBe('2 actions awaiting approval');
  expect(state.awaitingApproval).toBe(2);
  expect(state.total).toBe(5);
  const rows = rowsFor({}, 'empty', twoPending, 'ready');
  expect(rows.response.value).toBe('2 actions awaiting approval');
  expect(rows.response.detail).toBe('5 response actions recommended in total');
});

test('response labels are singular for one action', () => {
  expect(summarizeResponseState([{ id: 'a', approval_status: 'pending' }]).label)
    .toBe('1 action awaiting approval');
  expect(summarizeResponseState([{ id: 'a', execution_status: 'executed' }]).label)
    .toBe('1 action executed');
  expect(summarizeResponseState([{ id: 'a', execution_status: 'failed' }]).label)
    .toBe('1 action failed to execute');
  expect(summarizeResponseState([{ id: 'a' }]).label).toBe('1 action recommended');
});

test('a per-action approval quorum is shown only when the gate reported one', () => {
  expect(approvalQuorumLabel({
    id: 'a', approval_gate: { required_approval_count: 2, current_approval_count: 1 },
  })).toBe('1 of 2 approvals received');
  expect(approvalQuorumLabel({
    id: 'a', approval_gate: { required_approval_count: 1, current_approval_count: 0 },
  })).toBe('0 of 1 approval received');
  // No quorum reported → nothing claimed, rather than implying one approval suffices.
  expect(approvalQuorumLabel({ id: 'a' })).toBeNull();
  expect(approvalQuorumLabel({ id: 'a', approval_gate: { required_approval_count: 0 } })).toBeNull();
});

test('the Overview labels each response count with its unit and states the authority boundary', () => {
  const src = appSource('incident-case-overview.tsx');
  expect(src).toContain('Actions recommended');
  expect(src).toContain('Actions awaiting approval');
  expect(src).toContain('Approval quorum');
  // AI recommends; the deterministic engine plus a human authorizes. Screen 7
  // reports this, and never approves or executes anything itself.
  expect(src).toContain('AI authority: recommend only');
  expect(src).toContain('deterministic policy engine plus required human authorization');
});

/* ═════════ 7. Investigation progress + coverage ════════════════════ */

test('investigation coverage answers availability, never completion', () => {
  const summary: IncidentCaseSummary = {
    origin: { origin: 'alert', detection_linked: false, alert_linked: true },
    on_chain: { state: 'observed', collection_state: 'collected' },
    operational: { state: 'not_recorded', collection_state: 'not_collected' },
    policy: { state: 'not_recorded' },
    evidence: { artifact_count: 13 },
  };
  const coverage = investigationCoverage({
    summary, summaryLoad: 'ready', responseTotal: 2, responseLoad: 'ready',
  });
  const byKey = Object.fromEntries(coverage.map((row) => [row.key, row.state]));
  expect(byKey).toEqual({
    on_chain: 'available',
    operational: 'missing',
    // An alert-escalated case never had a detection to collect, so its absence is
    // not a gap in coverage — reporting it as one would invent an expectation.
    detection: 'not_applicable',
    policy: 'missing',
    response: 'available',
    evidence: 'available',
  });
});

test('a detection-originated case reports its detection as available, not applicable', () => {
  const coverage = investigationCoverage({
    summary: { origin: { origin: 'detection', detection_linked: true } },
    summaryLoad: 'ready', responseTotal: 0, responseLoad: 'empty',
  });
  expect(coverage.find((row) => row.key === 'detection')?.state).toBe('available');
  // No response actions → missing, never "not applicable": one could still exist.
  expect(coverage.find((row) => row.key === 'response')?.state).toBe('missing');
});

test('coverage is missing while the record has not been read', () => {
  const coverage = investigationCoverage({
    summary: null, summaryLoad: 'loading', responseTotal: 0, responseLoad: 'loading',
  });
  // Nothing is claimed available from an unread record.
  expect(coverage.every((row) => row.state === 'missing')).toBe(true);
});

test('the Workflow tab reads the canonical stage endpoint and defines no stages of its own', () => {
  const src = appSource('incident-workflow-tab.tsx');
  expect(src).toContain('/workflow');
  expect(src).toContain('summarizeWorkflowProgress(stages)');
  expect(src).toContain('investigationCoverage(');
  // The denominator is the length of the backend's list. No stage names are
  // hard-coded here, so the browser can never define a seventh stage of its own.
  for (const invented of ['Evidence Collection', 'Correlation', 'Report Generated']) {
    expect(src, invented).not.toContain(`'${invented}'`);
  }
  // Fails closed on every non-ready state rather than implying progress.
  for (const state of ['loading', 'unauthorized', 'not_found', 'error']) {
    expect(src, state).toContain(`'${state}'`);
  }
  expect(src).toContain('not enabled for this deployment');
});

test('the Workflow tab is reachable from the full investigation workspace', () => {
  const tabs = appSource('incident-case-file-tabs.tsx');
  expect(tabs).toContain("{ key: 'workflow',         label: 'Workflow' }");
  expect(tabs).toContain('IncidentWorkflowTab');
  // The existing tabs are preserved — nothing functioning was removed.
  for (const key of ['overview', 'timeline', 'alerts', 'evidence', 'response-actions', 'ai-investigation']) {
    expect(tabs, key).toContain(`key: '${key}'`);
  }
});

/* ═════════ 8. Timeline contains only placeable, persisted events ═══ */

test('the timeline reports records it could not place rather than dropping them', () => {
  const src = appSource('incident-forensic-timeline.tsx');
  expect(src).toContain('undatedEvents');
  expect(src).toContain('no canonical');
  expect(src).toContain('not placed on this chronology');
  // Ordering is by canonical timestamp, never by array position.
  expect(src).toContain('sortTimelineEvents(events)');
});

test('the timeline renders backend events only and fabricates no stage', () => {
  const src = appSource('incident-forensic-timeline.tsx');
  expect(src).toContain('No lifecycle events have been recorded for this incident.');
  // No stage list, no placeholder event, no synthesised time.
  expect(src).not.toContain('Date.now()');
  expect(src).not.toContain('new Date()');
  expect(src).not.toContain('placeholderEvents');
});

/* ═════════ 9. Workspace isolation + backend-derived integrity ══════ */

test('every Screen 7 read goes through the workspace-scoped proxy', () => {
  for (const file of ['use-incident-forensics.ts', 'incident-workflow-tab.tsx']) {
    const src = appSource(file);
    expect(src, file).toContain("const API_PROXY_BASE = '/api'");
    // Authenticated, workspace-resolving headers on every call; never a direct
    // backend URL, which would bypass the proxy's workspace resolution.
    expect(src, file).toContain('authHeaders()');
    expect(src, file).not.toContain('process.env.NEXT_PUBLIC_API_URL');
    expect(src, file).not.toContain('http://');
    expect(src, file).not.toContain('https://');
  }
});

test('integrity and verification states are backend-derived, never asserted by the UI', () => {
  const src = appSource('incident-evidence-tab.tsx');
  expect(src).toContain('integrityVariant');
  expect(src).toContain('showsImmutableMark');
  const presentation = appSource('incident-forensics-presentation.ts');
  // Only a snapshot-sealed artifact the backend marked immutable earns the mark.
  expect(presentation).toContain("artifact.immutable === true && artifact.integrity_status === 'snapshot_sealed'");
  // A package is "verified" only where Screen 9 said so.
  expect(presentation).toContain('export function hasEvidencePackage');
  expect(presentation).toContain('pkg?.available && pkg?.package_id');
});
