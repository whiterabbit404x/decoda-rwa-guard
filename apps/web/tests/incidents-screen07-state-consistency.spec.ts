/**
 * Screen 7 — forensic state consistency audit.
 *
 * Companion to `incidents-screen7-case-state-audit.spec.ts`. That suite pinned the
 * words used for ONE case record; this one pins the boundaries BETWEEN the states
 * the screen tracks, because every contradiction this pass fixed was two truthful
 * facts sharing one vocabulary:
 *
 *   * "Completed" above "4 / 7 complete"        — analyzer status vs lifecycle;
 *   * "Evidence coverage 100%" above "Operational: not collected"
 *                                               — snapshot dimensions vs domains;
 *   * agent "Active" with no AI job requested   — analyzer status vs agent status;
 *   * "Detection: Not available" beside an originating rule
 *                                               — detection entity vs rule provenance;
 *   * "3 records" beside "13 artifacts"         — two different collections;
 *   * "Snapshot: Verified" beside no export     — snapshot vs package.
 *
 * Two layers, matching the repo's established frontend test style:
 *   1. Executable unit tests over the pure presentation selectors.
 *   2. Source-level structural tests asserting the components read the canonical
 *      backend fact for each state and never derive one state from another.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import {
  aiInvestigatorLabel,
  aiInvestigatorState,
  aiInvestigatorVariant,
  analysisStateLabel,
  confidenceBandCaption,
  CONFIDENCE_BAND_THRESHOLDS,
  detectionProvenance,
  detectionRecordLabel,
  deterministicAnalysisState,
  investigationLifecycle,
  investigationLifecycleLabel,
  investigationLifecycleVariant,
  outstandingStage,
  reportState,
  reportStateLabel,
  snapshotDimensionCoverage,
  verificationStateVariant,
  type ForensicInvestigation,
  type WorkflowStage,
} from '../app/forensic-investigation-presentation';
import {
  evidenceDomainTally,
  evidencePackageStateLabel,
  evidenceSnapshotVerificationLabel,
  investigationCoverage,
  responseLadder,
  summarizeResponseState,
  summarizeWorkflowProgress,
  type CaseResponseAction,
  type IncidentCaseSummary,
} from '../app/incident-forensics-presentation';

function appSource(fileName: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', fileName), 'utf-8');
}

function apiSource(relative: string): string {
  return fs.readFileSync(path.join(__dirname, '..', '..', '..', relative), 'utf-8');
}

/** The canonical seven stages, in the backend's own order. */
function stages(states: Record<string, string> = {}): WorkflowStage[] {
  const canonical: Array<[string, string]> = [
    ['detection', 'Detection'],
    ['triage', 'Triage'],
    ['evidence_collection', 'Evidence Collection'],
    ['correlation', 'Correlation'],
    ['analysis', 'Analysis'],
    ['recommendation', 'Recommendation'],
    ['report', 'Report Generated'],
  ];
  return canonical.map(([stage, label]) => ({ stage, label, state: states[stage] ?? 'pending' }));
}

/** The exact state the screenshots showed: four stages done, three pending. */
const FOUR_OF_SEVEN = stages({
  detection: 'completed',
  triage: 'completed',
  evidence_collection: 'completed',
  correlation: 'completed',
});

/* ═════════ 1. A partial workflow is never a completed investigation ═════════ */

test('1. a 4/7 workflow can never render the investigation as Completed', () => {
  // The analyzer's OWN status is 'completed' here — that is what used to be shown
  // as the page-level verdict, which is how "Completed" sat above "4 / 7".
  expect(investigationLifecycle(FOUR_OF_SEVEN, 'completed')).toBe('awaiting_action');
  expect(investigationLifecycleLabel(investigationLifecycle(FOUR_OF_SEVEN, 'completed')))
    .not.toBe('Completed');
  // Only a genuinely terminal stage list may say Completed.
  const allDone = stages({
    detection: 'completed', triage: 'completed', evidence_collection: 'completed',
    correlation: 'completed', analysis: 'completed', recommendation: 'completed',
    report: 'completed',
  });
  expect(investigationLifecycle(allDone, 'completed')).toBe('completed');
  // A skipped stage is terminal too — a deployment without an AI layer still finishes.
  expect(investigationLifecycle(stages({
    detection: 'completed', triage: 'completed', evidence_collection: 'completed',
    correlation: 'completed', analysis: 'skipped', recommendation: 'completed',
    report: 'completed',
  }), 'completed')).toBe('completed');
});

test('1b. lifecycle states are distinct and only completion is coloured as success', () => {
  expect(investigationLifecycle([], 'completed')).toBe('pending');
  expect(investigationLifecycle(stages(), 'completed')).toBe('pending');
  expect(investigationLifecycle(stages({ detection: 'in_progress' }), 'completed')).toBe('running');
  expect(investigationLifecycle(stages({ detection: 'queued' }), 'completed')).toBe('running');
  expect(investigationLifecycle(stages({ detection: 'failed' }), 'completed')).toBe('failed');
  expect(investigationLifecycle(FOUR_OF_SEVEN, 'failed')).toBe('failed');
  // A degraded stage is terminal-but-incomplete: it must not read as done.
  expect(investigationLifecycle(stages({
    detection: 'completed', triage: 'completed', evidence_collection: 'degraded',
    correlation: 'completed', analysis: 'completed', recommendation: 'completed',
    report: 'completed',
  }), 'degraded')).toBe('awaiting_action');
  expect(investigationLifecycleVariant('completed')).toBe('success');
  for (const state of ['pending', 'running', 'awaiting_action', 'failed'] as const) {
    expect(investigationLifecycleVariant(state), state).not.toBe('success');
  }
});

test('1b2. the outstanding stage is named without claiming it is running', () => {
  // The 4/7 case has nothing in progress. Naming Analysis as the "current" stage
  // would claim work is under way that is not, so the stage is named and the
  // label says which: Next, not Current.
  expect(outstandingStage(FOUR_OF_SEVEN)).toEqual({ label: 'Analysis', running: false });
  expect(outstandingStage(stages({ detection: 'completed', triage: 'in_progress' })))
    .toEqual({ label: 'Triage', running: true });
  // A queued stage is genuinely moving; a skipped one is done with.
  expect(outstandingStage(stages({ detection: 'queued' }))).toEqual({ label: 'Detection', running: true });
  expect(outstandingStage(stages({ detection: 'skipped' }))).toEqual({ label: 'Triage', running: false });
  expect(outstandingStage([])).toBeNull();
  const allDone = stages({
    detection: 'completed', triage: 'completed', evidence_collection: 'completed',
    correlation: 'completed', analysis: 'completed', recommendation: 'completed',
    report: 'completed',
  });
  expect(outstandingStage(allDone)).toBeNull();

  const src = appSource('forensic-investigator-panel.tsx');
  expect(src).toContain("outstanding?.running === false ? 'Next stage' : 'Current stage'");
});

test('1c. the four investigation states are derived from four different facts', () => {
  // One payload, four answers. None of them is computed from another.
  const investigation: ForensicInvestigation = {
    status: 'completed',
    schema_ready: true,
    analysis: { status: 'completed', workflow_stages: FOUR_OF_SEVEN },
    ai_triage: { status: 'not_requested', report_available: false },
  };
  expect(investigationLifecycle(FOUR_OF_SEVEN, 'completed')).toBe('awaiting_action');
  expect(deterministicAnalysisState(investigation)).toBe('complete');
  expect(aiInvestigatorState(investigation.ai_triage?.status)).toBe('idle');
  expect(reportState(investigation.ai_triage)).toBe('not_generated');
});

test('1d. an agent with no requested job is Idle, never Active', () => {
  // The pill used to read `analysis.status`, so an agent that had never been asked
  // to do anything was labelled "Active" on every completed analysis.
  expect(aiInvestigatorLabel(aiInvestigatorState('not_requested'))).toBe('Idle');
  expect(aiInvestigatorLabel(aiInvestigatorState(undefined))).toBe('Idle');
  expect(aiInvestigatorLabel(aiInvestigatorState('running'))).toBe('Running');
  expect(aiInvestigatorLabel(aiInvestigatorState('completed_with_warnings'))).toBe('Completed');
  expect(aiInvestigatorLabel(aiInvestigatorState('validation_failed'))).toBe('Failed');
  expect(aiInvestigatorLabel(aiInvestigatorState('disabled'))).toBe('Unavailable');
  expect(aiInvestigatorLabel(aiInvestigatorState('budget_blocked'))).toBe('Blocked');
  // Idle is never a success colour: nothing succeeded.
  expect(aiInvestigatorVariant('idle')).not.toBe('success');
  // A degraded ANALYSIS says nothing about the agent's job.
  expect(aiInvestigatorState('not_requested')).toBe('idle');
  expect(deterministicAnalysisState({
    status: 'degraded', schema_ready: true, analysis: { status: 'degraded' },
  })).toBe('degraded');
  expect(analysisStateLabel('degraded')).toBe('Degraded');
});

test('1e. the panel derives the agent pill from the AI job, not the analyzer', () => {
  const src = appSource('forensic-investigator-panel.tsx');
  expect(src).toContain('aiInvestigatorState(ai.status)');
  // The old derivation is gone: an analyzer status may not colour the agent.
  expect(src).not.toContain("investigation.status === 'degraded' ? 'Degraded' : 'Active'");
  // Each state row names the fact it reports.
  expect(src).toContain('Deterministic analysis');
  expect(src).toContain('AI investigator');
  expect(src).toContain('Investigation report');
});

/* ═════════ 2. Snapshot dimensions are not domain coverage ═════════ */

test('2. full snapshot dimensions do not imply every forensic domain was collected', () => {
  // The backend's coverage is present/8 SNAPSHOT dimensions. Operational state is
  // not one of them, so 8 of 8 is entirely compatible with "Not collected".
  const full = snapshotDimensionCoverage({
    evidence_coverage: 1,
    coverage_detail: { coverage: 1, present_count: 8, expected_count: 8, missing: [] },
  });
  expect(full).toEqual({ present: 8, expected: 8, percent: 100, missing: [] });

  const summary: IncidentCaseSummary = {
    origin: { origin: 'alert', detection_linked: false, alert_linked: true },
    on_chain: { state: 'observed', collection_state: 'collected' },
    operational: { state: 'not_recorded', collection_state: 'not_collected' },
    policy: { state: 'decided', decision: 'DENY' },
    evidence: { artifact_count: 13, counts: { on_chain: 2, operational: 0, policy: 10, human_actions: 1, total: 13 } },
  };
  const coverage = Object.fromEntries(
    investigationCoverage({ summary, summaryLoad: 'ready', responseTotal: 1, responseLoad: 'ready' })
      .map((row) => [row.key, row.state]),
  );
  // The categorical answer contradicts nothing: it simply reports the gap the
  // percentage was never measuring.
  expect(coverage.operational).toBe('missing');
  expect(coverage.on_chain).toBe('available');
  expect(coverage.policy).toBe('available');
  expect(coverage.human_actions).toBe('available');
  // An alert-escalated case never had a detection to collect.
  expect(coverage.detection).toBe('not_applicable');
});

test('2b. a missing coverage detail reports nothing rather than 0 of 0', () => {
  expect(snapshotDimensionCoverage(undefined)).toBeNull();
  expect(snapshotDimensionCoverage({ evidence_coverage: 0 })).toBeNull();
  expect(snapshotDimensionCoverage({
    evidence_coverage: 0.5,
    coverage_detail: { coverage: 0.5, present_count: 4, expected_count: 8, missing: ['Confirmed block number'] },
  })?.missing).toEqual(['Confirmed block number']);
});

test('2c. the panel never labels the dimension count "Evidence coverage"', () => {
  const src = appSource('forensic-investigator-panel.tsx');
  expect(src).toContain('Snapshot evidence dimensions');
  expect(src).toContain('SNAPSHOT_DIMENSION_CAPTION');
  expect(src).not.toContain('Evidence coverage');
  // Four metrics, four sources — the panel must not fold one into another.
  expect(src).toContain('snapshotDimensionCoverage(analysis)');
  expect(src).toContain('confidenceBandLabel(analysis?.confidence_band)');
});

/* ═════════ 3. Not collected stays distinct from Not matched ═════════ */

test('3. an uncollected operational state is never a contradiction verdict', () => {
  const src = appSource('incident-case-overview.tsx');
  // The six-state vocabulary is unchanged by this pass, and the Overview still
  // routes the absent case through the collection state rather than the verdict.
  expect(src).toContain('sectionCollected(operational)');
  expect(src).toContain('operationalOutcomeDetail(outcome)');
  expect(src).toContain('NOT COLLECTED is not NOT MATCHED');
  // The reconciliation result of an uncollected case is Unavailable, never Not matched.
  expect(src).toContain('label="Unavailable"');
  expect(src).not.toContain("label=\"Not matched\"");
});

/* ═════════ 4-5. Detection record vs originating rule ═════════ */

const ALERT_ORIGIN_INVESTIGATION: ForensicInvestigation = {
  status: 'completed',
  schema_ready: true,
  incident: {
    incident_id: 'inc-1',
    source_alert_id: 'alert-77',
    detection_id: null,
    detection_type: null,
    origin: { origin: 'alert', detection_linked: false, alert_linked: true },
  },
  evidence: {
    rows: [{ kind: 'rule', reference: 'rule:smoke_wallet_transfer', title: 'Smoke Wallet Transfer' }],
    total: 1,
  },
  analysis: { status: 'completed', workflow_stages: FOUR_OF_SEVEN },
};

test('4. no linked detection plus an originating alert rule renders both facts', () => {
  const provenance = detectionProvenance(ALERT_ORIGIN_INVESTIGATION);
  expect(provenance.detectionLinked).toBe(false);
  expect(detectionRecordLabel(provenance)).toBe('None linked');
  expect(provenance.origin).toBe('alert');
  expect(provenance.originatingAlertId).toBe('alert-77');
  expect(provenance.originatingRule).toEqual({
    reference: 'rule:smoke_wallet_transfer',
    ruleId: 'smoke_wallet_transfer',
    name: 'Smoke Wallet Transfer',
  });
});

test('5. a rule reference never fabricates a linked detection record', () => {
  // The rule is present; the detection id is not. `detectionLinked` keys on the
  // detection id ALONE, so provenance can never promote itself into an entity.
  expect(detectionProvenance(ALERT_ORIGIN_INVESTIGATION).detectionLinked).toBe(false);
  // With a real detection, both are true and each keeps its own value.
  const linked = detectionProvenance({
    ...ALERT_ORIGIN_INVESTIGATION,
    incident: { ...ALERT_ORIGIN_INVESTIGATION.incident, detection_id: 'det-9', detection_type: 'unmatched_issuance' },
  });
  expect(linked.detectionLinked).toBe(true);
  expect(linked.detectionId).toBe('det-9');
  expect(linked.originatingRule?.ruleId).toBe('smoke_wallet_transfer');
  // No rule at all is stated as absent, never inferred from the detection.
  expect(detectionProvenance({ status: 'completed', incident: { detection_id: 'det-9' } }).originatingRule)
    .toBeNull();
  expect(detectionProvenance(null).detectionLinked).toBe(false);
});

test('5b. both surfaces render the provenance trio as separate labelled facts', () => {
  const panel = appSource('forensic-investigator-panel.tsx');
  const overview = appSource('incident-case-overview.tsx');
  for (const src of [panel, overview]) {
    expect(src).toContain('Detection record');
    expect(src).toContain('Incident origin');
    expect(src).toContain('Originating alert');
    expect(src).toContain('Originating rule');
  }
  // The backend supplies the Overview's rule from the SAME immutable snapshot the
  // investigation payload cites — it is not resolved a second time in the browser.
  expect(overview).toContain('detection.originating_rule');
  const forensics = apiSource('services/api/app/incident_forensics.py');
  expect(forensics).toContain('def originating_rule(');
  expect(forensics).toContain("'originating_rule': originating_rule(snapshot_payload)");
  // Naming the rule must not change the detection section's own state.
  expect(forensics).toContain("'state': STATE_OBSERVED if detection else STATE_NOT_RECORDED");
});

/* ═════════ 6-7. Fail-closed policy forensics ═════════ */

test('6. a fail-closed DENY states its decision source and deterministic reason', () => {
  const src = appSource('incident-case-overview.tsx');
  expect(src).toContain('resolvePolicyDecisionSource(policy)');
  expect(src).toContain('Decision source');
  expect(src).toContain('failClosedReason(policy)');
  expect(src).toContain('Reason codes');
  expect(src).toContain('Decided by the deterministic policy engine.');
  // The full block is ordered so the verdict, its source and its evidence read in
  // that order, and "Matched policy: None" is stated rather than omitted.
  const order = ['Decision source', 'Matched policy', 'Evaluation ID', 'Engine version', 'Evaluated at', 'Reason codes', 'Explanation'];
  let cursor = 0;
  for (const label of order) {
    const at = src.indexOf(label, cursor);
    expect(at, `${label} out of order`).toBeGreaterThan(-1);
    cursor = at;
  }
});

test('7. a missing matched policy never erases the historical evaluation', () => {
  const src = appSource('incident-case-overview.tsx');
  // The evaluation identity, engine version and reason codes are read from the
  // persisted evaluation row, so they survive the policy being edited or deleted.
  expect(src).toContain('policy.evaluation_id');
  expect(src).toContain('policy.engine_version');
  expect(src).toContain('policy.reason_codes');
  // No policy is fetched to resolve a historical decision.
  expect(src).not.toContain('/policies');
  // A fail-closed refusal shows "None" for the matched policy — it does not drop
  // the line, which would leave a reason code to read as the policy's name.
  expect(src).toContain('Matched policy');
  expect(src).toContain('evaluatedPolicyReference(policy)');
});

/* ═════════ 8. Preview count vs artifact total ═════════ */

test('8. the corroborated preview count is never expressed against the artifact total', () => {
  const src = appSource('forensic-investigator-panel.tsx');
  // The preview counts SNAPSHOT records; the artifact directory is a separate
  // collection, so the preview may not borrow its denominator.
  expect(src).toContain('corroborated snapshot records');
  expect(src).toContain('CORROBORATION_PREVIEW');
  expect(src).toContain('separate, four-domain collection');
  // The old wording read as though the two totals counted the same thing.
  expect(src).not.toContain('of {total} evidence records');
  // The directory is reached by selecting the Evidence tab, not a second page.
  expect(src).toContain('?tab=evidence');
});

/* ═════════ 9. Evidence domain counts reconcile, or say why not ═════════ */

test('9. domain counts sum to the total, and a residual is surfaced not hidden', () => {
  const reconciling = evidenceDomainTally({ on_chain: 2, operational: 0, policy: 10, human_actions: 1, total: 13 });
  expect(reconciling?.classified).toBe(13);
  expect(reconciling?.unclassified).toBe(0);
  expect(reconciling?.reconciles).toBe(true);
  // Every domain is named INCLUDING the zero — an invisible zero is what made the
  // total look like proof that operational evidence existed.
  expect(reconciling?.rows.map((row) => row.count)).toEqual([2, 0, 10, 1]);
  expect(reconciling?.rows.map((row) => row.key)).toEqual(['ON_CHAIN', 'OPERATIONAL', 'POLICY', 'HUMAN_ACTION']);

  // The backend counts an unclassifiable artifact toward the total only. The
  // residual is named rather than folded into a domain to make the sum work.
  const residual = evidenceDomainTally({ on_chain: 2, operational: 0, policy: 10, human_actions: 0, total: 13 });
  expect(residual?.classified).toBe(12);
  expect(residual?.unclassified).toBe(1);
  expect(residual?.reconciles).toBe(false);

  // A total the backend did not report is not zero.
  expect(evidenceDomainTally(undefined)).toBeNull();
  expect(evidenceDomainTally({ on_chain: 2 })).toBeNull();

  const src = appSource('incident-case-overview.tsx');
  expect(src).toContain('evidenceDomainTally(evidence.counts)');
  expect(src).toContain('Unclassified');
});

/* ═════════ 10. Snapshot verified is not package generated ═════════ */

test('10. a verified snapshot never implies a generated evidence package', () => {
  expect(evidenceSnapshotVerificationLabel(true)).toBe('Verified');
  expect(evidenceSnapshotVerificationLabel(false)).toBe('Mismatch');
  expect(evidenceSnapshotVerificationLabel(null)).toBe('Not verified');
  // Package state is read from the package record alone.
  expect(evidencePackageStateLabel({ package_number: null })).toBe('Not generated');
  expect(evidencePackageStateLabel(undefined)).toBe('Not generated');
  expect(evidencePackageStateLabel({ package_number: 'PKG-2026-004' })).toBe('PKG-2026-004');

  const src = appSource('incident-case-overview.tsx');
  expect(src).toContain('Evidence snapshot');
  expect(src).toContain('Evidence package');
  expect(src).toContain('SNAPSHOT_VS_PACKAGE_CAPTION');
  expect(src).toContain('evidenceSnapshotVerificationLabel(evidence.snapshot_hash_verified)');
});

/* ═════════ 11. Recommended is not executed ═════════ */

test('11. a recommended response action never implies approval or execution', () => {
  const actions: CaseResponseAction[] = [
    { id: '1', approval_status: 'pending' },
    { id: '2', approval_status: 'pending' },
    { id: '3' },
    { id: '4' },
    { id: '5' },
  ];
  const ladder = responseLadder(summarizeResponseState(actions));
  expect(ladder.map((row) => [row.label, row.count])).toEqual([
    ['Recommended actions', 5],
    ['Awaiting approval', 2],
    // The zeros are RENDERED. "5 recommended" beside a blank approved line is what
    // made all five look as though they were awaiting approval.
    ['Approved', 0],
    ['Executed', 0],
  ]);
  // A failure is only listed when one exists — zero is not a state an action rests in.
  expect(responseLadder(summarizeResponseState([{ id: '1', execution_status: 'failed' }])).some((r) => r.key === 'failed'))
    .toBe(true);
  // No actions at all renders no ladder, rather than a row of zeros implying a flow ran.
  expect(responseLadder(summarizeResponseState([]))).toEqual([]);

  const src = appSource('incident-case-overview.tsx');
  expect(src).toContain('responseLadder(response)');
  expect(src).toContain('RESPONSE_AUTHORITY_CAPTION');
  expect(src).toContain('AI authority: recommend only');
  // Screen 7 reports the state; it never approves or executes.
  expect(src).not.toContain("method: 'POST'");
});

/* ═════════ 12. A potential match is not a verified fact ═════════ */

test('12. a potential rule match is never rendered as a verified fact', () => {
  expect(verificationStateVariant('verified_fact')).toBe('success');
  expect(verificationStateVariant('rule_match')).not.toBe('success');
  const src = appSource('forensic-investigator-panel.tsx');
  // Rule matches carry their own component and their own always-"Potential" pill;
  // they never route through the verified-finding renderer.
  expect(src).toContain('function RuleMatchItem');
  expect(src).toContain('label="Potential rule match"');
  expect(src).toContain('Potential rule matches');
  expect(src).toContain('Top verified findings');
  // Verified findings are filtered on the backend's verification state.
  expect(src).toContain("f.verification_state === 'verified_fact'");
  expect(src).toContain('Nothing is asserted without a deterministic match.');
});

/* ═════════ 13. One workflow source of truth ═════════ */

test('13. the queue and the full investigation count the same seven stages', () => {
  const progress = summarizeWorkflowProgress(FOUR_OF_SEVEN);
  expect(progress).toEqual({ total: 7, completed: 4, failed: 0, percent: 57, current: null });

  const queue = appSource('incidents-panel.tsx');
  const hero = appSource('forensic-investigator-panel.tsx');
  const tab = appSource('incident-workflow-tab.tsx');
  // All three fold the SAME persisted stage list with the SAME selector.
  for (const src of [queue, hero, tab]) {
    expect(src).toContain('summarizeWorkflowProgress(');
  }
  expect(queue).toContain('analysis?.workflow_stages ?? []');
  expect(hero).toContain('analysis?.workflow_stages ?? []');
  expect(tab).toContain('/workflow');
  // The queue's verdict is now the lifecycle derived from those same stages.
  expect(queue).toContain('investigationLifecycle(workflowStages, analysis.status)');

  // The seven stages are the backend's, named once.
  const backend = apiSource('services/api/app/forensic_investigation.py');
  for (const stage of ['detection', 'triage', 'evidence_collection', 'correlation', 'analysis', 'recommendation', 'report']) {
    expect(backend, stage).toContain(`('${stage}', '`);
  }
});

/* ═════════ 14-15. Report + re-run authority boundary ═════════ */

test('14. generating a report cannot mutate deterministic forensic state', () => {
  const backend = apiSource('services/api/app/forensic_investigation.py');
  const start = backend.indexOf('def generate_report(');
  const end = backend.indexOf('def build_report(', start);
  expect(start).toBeGreaterThan(-1);
  expect(end).toBeGreaterThan(start);
  const body = backend.slice(start, end);
  // It reads the snapshot and persists an analysis keyed by snapshot hash. It
  // never rewrites evidence, policy, timeline or response rows, and it executes
  // nothing.
  for (const forbidden of ['DELETE FROM', 'UPDATE incident_evidence_snapshots', 'UPDATE policy_', 'execute_action', 'DROP ']) {
    expect(body, forbidden).not.toContain(forbidden);
  }
  // The only writes are the append-only timeline event and the audit row.
  expect(body).toContain('append_incident_timeline_event');
  expect(body).toContain('log_audit');

  // The UI reports what the request produced and does not claim a completed stage.
  const src = appSource('forensic-investigator-panel.tsx');
  expect(src).toContain('reportOutcome');
  expect(src).toContain('The Report Generated workflow stage remains pending');
  expect(src).not.toContain('Report generated and persisted.');
});

test('15. re-running the investigation cannot rewrite persisted timeline or evidence facts', () => {
  const backend = apiSource('services/api/app/forensic_investigation.py');
  const start = backend.indexOf('def rerun_investigation(');
  const end = backend.indexOf('def generate_report(', start);
  expect(start).toBeGreaterThan(-1);
  const body = backend.slice(start, end);
  // A re-run builds a NEW snapshot and appends a new timeline event; prior
  // snapshots, artifacts and events are untouched.
  expect(body).toContain('_build_and_store_snapshot');
  expect(body).toContain('append_incident_timeline_event');
  for (const forbidden of ['DELETE FROM', 'UPDATE incident_timeline', 'UPDATE incident_evidence_snapshots', 'execute_action']) {
    expect(body, forbidden).not.toContain(forbidden);
  }
  // The AI hand-off queues an analysis job; it never executes a response action.
  expect(body).toContain('it only queues an analysis job');

  // The banner that states the boundary must survive.
  const src = appSource('forensic-investigator-panel.tsx');
  expect(src).toContain('Evidence-grounded forensic analysis.');
  expect(src).toContain('No fund-moving, contract-changing');
  expect(src).toContain('response steps are recommendations routed to approval');
});

/* ═════════ Confidence provenance ═════════ */

test('confidence is read from the backend band and its factors are shown, never scored here', () => {
  // The thresholds mirror `forensic_investigation.confidence_band` and exist only
  // so the UI can SAY what the bands mean.
  expect(CONFIDENCE_BAND_THRESHOLDS.high).toBe(0.75);
  expect(CONFIDENCE_BAND_THRESHOLDS.medium).toBe(0.4);
  expect(confidenceBandCaption()).toContain('High is 75% or above');
  expect(confidenceBandCaption()).toContain('never computed in the browser');

  const backend = apiSource('services/api/app/forensic_investigation.py');
  expect(backend).toContain('if v >= 0.75:');
  expect(backend).toContain('if v >= 0.40:');

  const src = appSource('forensic-investigator-panel.tsx');
  // The band comes off the payload; the factors and explanation are the backend's.
  expect(src).toContain('analysis?.confidence_band');
  expect(src).toContain('analysis?.confidence_factors');
  expect(src).toContain('confidence_explanation');
  expect(src).toContain('confidenceBandCaption()');
  // No client-side scoring or re-banding.
  expect(src).not.toContain('CONFIDENCE_BAND_THRESHOLDS');
});

/* ═════════ Duplication ═════════ */

test('the hero states each fact once and leaves the directory to the Evidence tab', () => {
  const src = appSource('forensic-investigator-panel.tsx');
  // Confidence and the dimension count moved into the status card; the agent panel
  // no longer repeats them as a second pair of metric tiles.
  expect(src).not.toContain("MetricTile label=\"Evidence coverage\"");
  expect(src).not.toContain("<MetricTile label=\"Confidence\"");
  // The status card no longer restates header facts, nor a permanently-empty field.
  expect(src).not.toContain('Detection time');
  expect(src).not.toContain('Impacted asset');
  expect(src).not.toContain('Estimated loss');
  // The detail route still mounts exactly one investigation payload consumer: the
  // tabs never fetch the investigation endpoint the hero already owns.
  const tabs = appSource('incident-case-file-tabs.tsx');
  expect(tabs).not.toContain('}/investigation`');
});
