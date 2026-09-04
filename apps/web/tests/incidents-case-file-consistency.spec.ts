/**
 * Screen 7 — Case File drawer ↔ full incident page state consistency.
 *
 * The /incidents list-page Case File drawer and the canonical full incident page at
 * /incidents/[incidentId] MUST derive workflow state, the next action, the linked
 * detection, and evidence state from the SAME persisted investigation payload and the
 * SAME canonical selectors. These tests pin that contract so the two views can never
 * disagree again (the observed production bug: the drawer showed "Investigation
 * Started: Pending / Evidence Collected: Completed / Linked Detection: none / Start
 * Investigation" while the full page showed a degraded investigation with findings, an
 * originating detection rule, and "Re-run Investigation").
 *
 * Two layers (the repo's established frontend test style — no running server):
 *   1. Executable unit tests for the pure canonical selectors.
 *   2. Source-level structural tests over the drawer + full page.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import {
  investigationHasFindings,
  investigationNextAction,
  investigationSummaryState,
  isAwaitingResponseStatus,
  isInInvestigationStatus,
  linkedDetectionRef,
  workflowStateLabel,
  workflowStateVariant,
  type ForensicInvestigation,
  type WorkflowStage,
} from '../app/forensic-investigation-presentation';

function appSource(fileName: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', fileName), 'utf-8');
}

/* ── Canonical fixtures (never hardcoded into the UI) ───────────────────────── */

// The seven canonical stages, mirroring the backend's derive_workflow_stages for a
// degraded incident whose forensic evidence has not yet been collected: Detection and
// Triage are complete (a source alert exists), Evidence Collection is still Pending.
const DEGRADED_STAGES: WorkflowStage[] = [
  { stage: 'detection', label: 'Detection', state: 'completed' },
  { stage: 'triage', label: 'Triage', state: 'completed' },
  { stage: 'evidence_collection', label: 'Evidence Collection', state: 'pending' },
  { stage: 'correlation', label: 'Correlation', state: 'pending' },
  { stage: 'analysis', label: 'Analysis', state: 'pending' },
  { stage: 'recommendation', label: 'Recommendation', state: 'pending' },
  { stage: 'report', label: 'Report Generated', state: 'pending' },
];

// A degraded investigation that HAS deterministic findings and an originating detection
// rule — exactly the production case where the drawer wrongly said "Start Investigation".
const degradedWithFindings: ForensicInvestigation = {
  status: 'degraded',
  schema_ready: true,
  analysis: {
    status: 'degraded',
    workflow_stages: DEGRADED_STAGES,
    findings: [
      { finding_id: 'F-01', finding_type: 'source_alert', verification_state: 'verified_fact', evidence_refs: ['alert:a1'] },
      { finding_id: 'F-02', finding_type: 'detection_rule', verification_state: 'verified_fact', evidence_refs: ['rule:RWA-FR-002'] },
    ],
    counts: { findings: 2, verified_facts: 2, rule_matches: 1, evidence_gaps: 0 },
  },
  evidence: {
    rows: [
      { kind: 'alert', type: 'Alert record', reference: 'alert:a1', corroboration: 'corroborated' },
      { kind: 'rule', type: 'Detection rule', title: 'Large value transfer', reference: 'rule:RWA-FR-002', corroboration: 'corroborated' },
    ],
    total: 2,
  },
  ai_triage: { status: 'not_requested', recommendation_count: 0, report_available: false },
  linked: { alerts: 1, evidence_records: 2, recommendations: 0 },
};

/* ───────────────────── 1. Canonical selectors (executable) ─────────────────── */

test('a degraded investigation WITH findings never shows "Start Investigation"', () => {
  const action = investigationNextAction(degradedWithFindings, { awaitingResponse: false });
  expect(action.label).toBe('Review Findings');
  expect(action.label).not.toBe('Start Investigation');
  // The manual re-run stays available as a secondary action (completed/degraded).
  expect(action.secondary?.label).toBe('Re-run Investigation');
});

test('next action is derived from persisted investigation state (all required states)', () => {
  // No investigation / unavailable → Start Investigation.
  expect(investigationNextAction(null).label).toBe('Start Investigation');
  expect(investigationNextAction({ status: 'unavailable' }).label).toBe('Start Investigation');
  expect(investigationNextAction({ status: 'completed' /* no analysis */ }).label).toBe('Start Investigation');

  // Queued / running → View Investigation Progress.
  expect(investigationNextAction({
    status: 'completed', analysis: { status: 'completed', workflow_stages: [] }, ai_triage: { status: 'queued' },
  }).label).toBe('View Investigation Progress');
  expect(investigationNextAction({
    status: 'completed', analysis: { status: 'completed', workflow_stages: [] }, ai_triage: { status: 'running' },
  }).label).toBe('View Investigation Progress');

  // Failed → Retry Investigation.
  expect(investigationNextAction({
    status: 'completed', analysis: { status: 'completed', workflow_stages: [] }, ai_triage: { status: 'failed' },
  }).label).toBe('Retry Investigation');

  // Completed/degraded with findings → Review Findings (+ Re-run secondary).
  expect(investigationNextAction(degradedWithFindings).label).toBe('Review Findings');
});

test('an approval-required recommendation routes the reviewer to the response (Screen 8 boundary)', () => {
  // Terminal investigation + canonical awaiting-response status → Review Response
  // Recommendation. The drawer only routes to review; it never approves.
  const action = investigationNextAction(degradedWithFindings, { awaitingResponse: true });
  expect(action.label).toBe('Review Response Recommendation');
  expect(action.kind).toBe('review-response');
});

test('investigationHasFindings distinguishes real findings from evidence-gap-only analyses', () => {
  expect(investigationHasFindings(degradedWithFindings.analysis)).toBe(true);
  // Counts path: findings minus evidence gaps.
  expect(investigationHasFindings({ status: 'degraded', counts: { findings: 3, evidence_gaps: 3 } })).toBe(false);
  expect(investigationHasFindings({ status: 'degraded', counts: { findings: 4, evidence_gaps: 1 } })).toBe(true);
  // Array fallback when counts are absent.
  expect(investigationHasFindings({ status: 'degraded', findings: [{ finding_type: 'evidence_gap' }] })).toBe(false);
  expect(investigationHasFindings({ status: 'degraded', findings: [{ finding_type: 'source_alert' }] })).toBe(true);
  expect(investigationHasFindings(null)).toBe(false);
});

test('linked detection renders the canonical originating rule when one exists', () => {
  // From the evidence rows (kind: 'rule').
  const fromEvidence = linkedDetectionRef(degradedWithFindings);
  expect(fromEvidence?.reference).toBe('rule:RWA-FR-002');
  expect(fromEvidence?.label).toBe('Rule');
  expect(fromEvidence?.title).toBe('Large value transfer');

  // From the deterministic 'detection_rule' finding when no evidence row carries it.
  const fromFinding = linkedDetectionRef({
    status: 'degraded',
    analysis: {
      status: 'degraded',
      workflow_stages: [],
      findings: [{ finding_id: 'F-02', finding_type: 'detection_rule', evidence_refs: ['rule:RWA-FR-004'] }],
    },
    evidence: { rows: [] },
  });
  expect(fromFinding?.reference).toBe('rule:RWA-FR-004');

  // Null only when there truly is no originating detection reference.
  expect(linkedDetectionRef({ status: 'completed', analysis: { status: 'completed', workflow_stages: [], findings: [] }, evidence: { rows: [] } })).toBeNull();
  expect(linkedDetectionRef(null)).toBeNull();
});

test('Evidence Collection cannot disagree: both views map the same stage identically', () => {
  // Both the drawer and the full page render `analysis.workflow_stages` through the same
  // pure label/variant functions, so a Pending Evidence Collection stage is rendered
  // identically in both — it can never read "Completed" in one view and "Pending" in the
  // other. (Requirement 6: initial alert evidence is not mislabeled as forensic
  // Evidence Collection.)
  const evidenceStage = DEGRADED_STAGES.find((s) => s.stage === 'evidence_collection')!;
  expect(evidenceStage.state).toBe('pending');
  expect(workflowStateLabel(evidenceStage.state)).toBe('Pending');
  expect(workflowStateVariant(evidenceStage.state)).toBe('neutral');
  // Completed stages are equally unambiguous across views.
  expect(workflowStateLabel('completed')).toBe('Completed');
  expect(workflowStateVariant('completed')).toBe('success');
});

test('canonical investigation summary state matches the full page (Degraded case)', () => {
  // The Overview pill uses this exact selector, identical to the full page's AI
  // Investigation Summary — the observed "Investigation state: Degraded".
  expect(investigationSummaryState(
    degradedWithFindings.analysis?.status,
    degradedWithFindings.ai_triage?.status,
  )).toBe('Degraded');
});

test('KPI predicates count from canonical persisted incident status', () => {
  expect(isInInvestigationStatus('investigating')).toBe(true);
  expect(isInInvestigationStatus('open')).toBe(false);
  expect(isAwaitingResponseStatus('awaiting_response')).toBe(true);
  expect(isAwaitingResponseStatus('contained')).toBe(true); // legacy persisted equivalent
  expect(isAwaitingResponseStatus('investigating')).toBe(false);
});

/* ───────────────────── 2. Source-level structure ──────────────────────────── */

test('drawer fetches the canonical investigation payload for the SELECTED incident only', () => {
  const panel = appSource('incidents-panel.tsx');
  // Same canonical endpoint the full page uses, keyed on the selected incident.
  expect(panel).toContain('`${apiUrl}/incidents/${encodeURIComponent(selectedId)}/investigation`');
  // It is fetched in an effect keyed on selectedId — NOT once per table row.
  expect(panel).toContain('}, [apiUrl, authHeaders, selectedId]);');
  // The investigation fetch lives in a selection-keyed effect ABOVE the table render;
  // it is not issued inside the per-row `filteredIncidents.map(...)`.
  const fetchIdx = panel.indexOf('/investigation`, {');
  const rowMapIdx = panel.indexOf('filteredIncidents.map');
  expect(fetchIdx).toBeGreaterThan(-1);
  expect(rowMapIdx).toBeGreaterThan(fetchIdx);
});

test('loading a selected incident fetches its investigation data once and cancels stale data', () => {
  const panel = appSource('incidents-panel.tsx');
  // A cancelled guard + AbortController ensures a response for a previously selected or
  // closed incident can never overwrite the current drawer's state.
  expect(panel).toContain('const controller = new AbortController();');
  expect(panel).toContain('signal: controller.signal,');
  expect(panel).toContain('if (cancelled) return;');
  expect(panel).toContain('return () => { cancelled = true; controller.abort(); };');
  // Clearing the selection tears the investigation state down (no stale carry-over).
  expect(panel).toContain('setInvestigation(null);');
});

test('drawer next action + linked detection use the canonical selectors (no legacy inference)', () => {
  const panel = appSource('incidents-panel.tsx');
  expect(panel).toContain('investigationNextAction(investigation, { awaitingResponse })');
  expect(panel).toContain('const awaitingResponse = isAwaitingResponseStatus(incidentStatus(incident));');
  expect(panel).toContain('const detectionRef = linkedDetectionRef(investigation);');
  // The legacy, browser-inferred next-action ladder is gone.
  expect(panel).not.toContain("return 'Collect Evidence'");
  expect(panel).not.toContain("return 'Recommend Response'");
  expect(panel).not.toContain("return 'Resolve Incident'");
  expect(panel).not.toContain('function recommendedNextAction');
});

test('linked detection does not read "none" when a canonical detection reference exists', () => {
  const panel = appSource('incidents-panel.tsx');
  // Fallback chain: incident row detection id → canonical investigation detection ref →
  // only then "No linked detection".
  expect(panel).toContain('rowDetectionId');
  expect(panel).toContain('detectionRef');
  expect(panel).toContain('No linked detection');
  // The "none" branch is reachable only after BOTH canonical sources are checked.
  const noneIdx = panel.indexOf('No linked detection');
  const refIdx = panel.indexOf('const detectionRef = linkedDetectionRef(investigation);');
  expect(refIdx).toBeGreaterThan(-1);
  expect(noneIdx).toBeGreaterThan(refIdx);
});

test('KPIs read canonical backend counters, never a fold of the loaded page', () => {
  const panel = appSource('incidents-panel.tsx');
  // The counters come from the workspace-wide summary endpoint...
  expect(panel).toContain("`${apiUrl}/incidents/summary`");
  expect(panel).toContain('parseIncidentQueueCounts');
  expect(panel).toContain('queueCounts?.open_incidents');
  expect(panel).toContain('queueCounts?.critical_incidents');
  expect(panel).toContain('queueCounts?.in_investigation');
  expect(panel).toContain('queueCounts?.awaiting_response');
  // ...and never from folding `incidents`, which is one capped, filtered page.
  expect(panel).not.toContain('incidents.filter((i) =>');
  expect(panel).not.toContain('isInInvestigationStatus(incidentStatus(i))');
  expect(panel).not.toContain('isAwaitingResponseStatus(incidentStatus(i))');
});

test('drawer and full page share one workflow-stage source + renderer (identical states)', () => {
  const panel = appSource('incidents-panel.tsx');
  const full = appSource('forensic-investigator-panel.tsx');
  // Both import the SAME canonical presentation selectors.
  for (const src of [panel, full]) {
    expect(src).toContain("from './forensic-investigation-presentation'");
    expect(src).toContain('workflowStateLabel');
    expect(src).toContain('workflowStateVariant');
  }
  // Drawer stages come from the canonical analysis payload (not a second definition).
  expect(panel).toContain('const workflowStages = analysis?.workflow_stages ?? [];');
  // Full page renders the same analysis.workflow_stages.
  expect(full).toContain('stages={analysis.workflow_stages ?? []}');
  // Both render each stage's canonical label + state pill.
  expect(panel).toContain('workflowStateLabel(s.state)');
  expect(full).toContain('workflowStateLabel(s.state)');
});

test('no hardcoded incident workflow data is introduced in the drawer', () => {
  const panel = appSource('incidents-panel.tsx');
  // No randomness, no fabricated reference numbers, no hand-set stage completion.
  expect(panel).not.toContain('Math.random');
  expect(panel).not.toMatch(/INC-20\d\d-\d/);
  // Workflow stage state is never asserted "completed" as a literal in the drawer — it
  // comes from the backend payload only.
  expect(panel).not.toMatch(/state:\s*'completed'/);
  expect(panel).not.toContain("label: 'Alert Received'");
});
