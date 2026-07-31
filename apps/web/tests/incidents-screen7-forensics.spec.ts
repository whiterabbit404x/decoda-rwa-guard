/**
 * Screen 7 — Digital Forensics Investigator contract tests.
 *
 * Two layers (the repo's established frontend test style — no running server):
 *   1. Executable unit tests for the pure presentation helpers.
 *   2. Source-level structural tests that assert the investigator panel renders the
 *      required states / panels / distinctions and does NOT hardcode demo incident data.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import {
  confidenceBandLabel,
  confidenceBandVariant,
  confidencePercent,
  corroborationLabel,
  corroborationVariant,
  coveragePercent,
  evidenceSourceLabel,
  evidenceSourceVariant,
  incidentReference,
  investigationSummaryState,
  isAiActive,
  refKind,
  refKindLabel,
  summaryStateVariant,
  truncateMiddle,
  verificationStateLabel,
  verificationStateVariant,
  workflowStateLabel,
  workflowStateVariant,
} from '../app/forensic-investigation-presentation';

function appSource(fileName: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', fileName), 'utf-8');
}

/* ───────────────────── 1. Pure presentation logic ─────────────────────── */

test('verification states are labelled distinctly (fact / rule / AI / hypothesis)', () => {
  expect(verificationStateLabel('verified_fact')).toBe('Verified fact');
  expect(verificationStateLabel('rule_match')).toBe('Rule match');
  expect(verificationStateLabel('ai_interpretation')).toBe('AI interpretation');
  expect(verificationStateLabel('unverified_hypothesis')).toBe('Hypothesis');
});

test('a hypothesis is never styled like a verified fact', () => {
  // Every verification state maps to a DISTINCT pill variant, so a hypothesis can
  // never look identical to a verified fact.
  const variants = new Set([
    verificationStateVariant('verified_fact'),
    verificationStateVariant('rule_match'),
    verificationStateVariant('ai_interpretation'),
    verificationStateVariant('unverified_hypothesis'),
  ]);
  expect(variants.size).toBe(4);
  expect(verificationStateVariant('verified_fact')).toBe('success');
  expect(verificationStateVariant('unverified_hypothesis')).not.toBe('success');
});

test('confidence band labels + variants are canonical', () => {
  expect(confidenceBandLabel('high')).toBe('High');
  expect(confidenceBandLabel('medium')).toBe('Medium');
  expect(confidenceBandLabel('low')).toBe('Low');
  expect(confidenceBandVariant('high')).toBe('success');
  expect(confidenceBandVariant('medium')).toBe('warning');
  expect(confidenceBandVariant('low')).toBe('danger');
});

test('confidence and coverage percentages clamp to 0..100', () => {
  expect(confidencePercent(0.75)).toBe(75);
  expect(confidencePercent(2)).toBe(100);
  expect(confidencePercent(-1)).toBe(0);
  expect(confidencePercent(undefined)).toBe(0);
  expect(coveragePercent(0.5)).toBe(50);
  expect(coveragePercent(null)).toBe(0);
});

test('workflow stage states map to canonical labels + variants', () => {
  expect(workflowStateLabel('completed')).toBe('Completed');
  expect(workflowStateLabel('in_progress')).toBe('In progress');
  expect(workflowStateLabel('queued')).toBe('Queued');
  expect(workflowStateLabel('failed')).toBe('Failed');
  expect(workflowStateLabel('degraded')).toBe('Degraded');
  expect(workflowStateLabel('skipped')).toBe('Skipped');
  expect(workflowStateLabel('pending')).toBe('Pending');
  expect(workflowStateVariant('completed')).toBe('success');
  expect(workflowStateVariant('failed')).toBe('danger');
  expect(workflowStateVariant('degraded')).toBe('warning');
});

test('AI Investigation Summary state derives from analysis + AI status', () => {
  expect(investigationSummaryState('completed', 'queued')).toBe('Queued');
  expect(investigationSummaryState('completed', 'running')).toBe('Investigating');
  expect(investigationSummaryState('completed', 'completed')).toBe('Completed');
  expect(investigationSummaryState('degraded', 'not_requested')).toBe('Degraded');
  expect(investigationSummaryState('completed', 'failed')).toBe('Failed');
  expect(summaryStateVariant('Completed')).toBe('success');
  expect(summaryStateVariant('Failed')).toBe('danger');
});

test('only queued/running AI status is active (bounded polling gate)', () => {
  expect(isAiActive('queued')).toBe(true);
  expect(isAiActive('running')).toBe(true);
  expect(isAiActive('completed')).toBe(false);
  expect(isAiActive('failed')).toBe(false);
  expect(isAiActive(undefined)).toBe(false);
});

test('long identifiers are middle-truncated so they never overflow', () => {
  const hash = '0x1234567890abcdef1234567890abcdef1234567890abcdef';
  const t = truncateMiddle(hash);
  expect(t.length).toBeLessThan(hash.length);
  expect(t).toContain('…');
  expect(truncateMiddle('')).toBe('—');
  expect(truncateMiddle('0xabc')).toBe('0xabc'); // short values are untouched
});

test('evidence reference kinds are recognised and labelled', () => {
  expect(refKind('telemetry:abc')).toBe('telemetry');
  expect(refKind('alert:1')).toBe('alert');
  expect(refKind('mystery:1')).toBe('unknown');
  expect(refKindLabel('telemetry:abc')).toBe('Telemetry');
  expect(refKindLabel('rule:x')).toBe('Rule');
});

test('simulator evidence is never labelled live_provider', () => {
  expect(evidenceSourceLabel('simulator')).toBe('simulator');
  expect(evidenceSourceLabel('replay')).toBe('simulator');
  expect(evidenceSourceLabel('live_provider')).toBe('live_provider');
  expect(evidenceSourceVariant('simulator')).toBe('info');
  expect(evidenceSourceVariant('live_provider')).toBe('success');
});

test('corroboration state is labelled + coloured truthfully', () => {
  expect(corroborationLabel('corroborated')).toBe('Corroborated');
  expect(corroborationLabel('partial')).toBe('Partial');
  expect(corroborationVariant('corroborated')).toBe('success');
  expect(corroborationVariant('partial')).toBe('warning');
});

test('incident reference falls back to a stable short id, never a fabricated number', () => {
  expect(incidentReference('INC-2026-017', 'uuid')).toBe('INC-2026-017');
  expect(incidentReference(null, 'abcd1234-5678')).toBe('INC-abcd1234');
  expect(incidentReference('', '')).toBe('Incident');
});

/* ───────────────────── 2. Source-level structure ──────────────────────── */

test('investigator panel fetches the real per-incident endpoint (no hardcoded data)', () => {
  const src = appSource('forensic-investigator-panel.tsx');
  expect(src).toContain('/incidents/${encodeURIComponent(incidentId)}/investigation');
  expect(src).toContain('authHeaders()');
  // No demo/mock incident data and no randomness in production UI.
  expect(src).not.toContain('Math.random');
  expect(src).not.toMatch(/INC-2026-017/); // reference-design number must not be hardcoded
});

test('investigator panel renders the four required panels', () => {
  const src = appSource('forensic-investigator-panel.tsx');
  expect(src).toContain('AI Investigation Summary');
  expect(src).toContain('Evidence — Corroborated');
  expect(src).toContain('Investigation Workflow');
  expect(src).toContain('Digital Forensics Investigator');
});

test('investigator panel implements every required UI state', () => {
  const src = appSource('forensic-investigator-panel.tsx');
  expect(src).toContain('LoadingSkeleton');            // loading
  expect(src).toContain('No incident found');          // not found
  expect(src).toContain('Not authorized');             // authorization failure
  expect(src).toContain('Forensic investigation unavailable'); // AI/schema unavailable
  expect(src).toContain('Retry');                      // backend error + retry
  expect(src).toContain('Waiting for linked evidence'); // no-evidence
  expect(src).toContain('Generating report…');         // report in progress
});

test('findings distinguish verified fact / rule match / AI interpretation / hypothesis', () => {
  const src = appSource('forensic-investigator-panel.tsx');
  expect(src).toContain('verificationStateLabel');
  expect(src).toContain('verificationStateVariant');
  // Rule matches are labelled "Potential rule match", never a legal judgment.
  expect(src).toContain('Potential rule match');
  // Verified findings and rule matches are rendered in distinct sections.
  expect(src).toContain('Top verified findings');
  expect(src).toContain('Potential rule matches');
});

test('header renders incident metadata from API data', () => {
  const src = appSource('forensic-investigator-panel.tsx');
  expect(src).toContain('Detection time');
  expect(src).toContain('Last updated');
  expect(src).toContain('Impacted asset');
  expect(src).toContain('Source alerts');
  expect(src).toContain('Risk score');
  expect(src).toContain('incidentReference(incident.reference, incident.incident_id)');
});

test('Generate Report + Re-run Investigation post to the real endpoints and never execute', () => {
  const src = appSource('forensic-investigator-panel.tsx');
  expect(src).toContain('/incidents/${encodeURIComponent(incidentId)}/reports');
  expect(src).toContain('/incidents/${encodeURIComponent(incidentId)}/investigations');
  expect(src).toContain('Generate Report');
  expect(src).toContain('Re-run Investigation');
  // The recommended next step is a recommendation routed to approval — never executed here.
  expect(src).toContain('no action is executed here');
  expect(src).toContain('View response recommendations');
});

test('bounded polling only while the AI narrative is active, cleaned up on unmount', () => {
  const src = appSource('forensic-investigator-panel.tsx');
  expect(src).toContain('isAiActive(data.ai_triage?.status)');
  expect(src).toContain('setInterval');
  expect(src).toContain('clearInterval');
});

test('evidence identifiers use copy/truncate so they do not overflow the layout', () => {
  const src = appSource('forensic-investigator-panel.tsx');
  expect(src).toContain('CopyableId');
  expect(src).toContain('truncateMiddle');
  expect(src).toContain('overflowX: \'auto\'');
});

test('layout is responsive (readable two-column investigation layout)', () => {
  const src = appSource('forensic-investigator-panel.tsx');
  // The four regions are no longer flattened into one auto-fit row of equally
  // narrow columns. Main investigation content (AI Summary + Workflow, then
  // Evidence at full width) sits in .forensicMain; the agent panel is the
  // second child of .forensicLayout (its right column on desktop).
  expect(src).toContain('className="forensicLayout"');
  expect(src).toContain('className="forensicMain"');
  expect(src).toContain('className="forensicMainTop"');
  expect(src).not.toContain('repeat(auto-fit, minmax(300px, 1fr))');
});

test('mandatory generated-content + non-execution disclaimer is shown', () => {
  const src = appSource('forensic-investigator-panel.tsx');
  expect(src).toContain('Evidence-grounded forensic analysis.');
  expect(src).toContain('No fund-moving, contract-changing');
});

test('Workflow tab is wired into the incidents drawer and reads canonical backend state', () => {
  const panel = appSource('incidents-panel.tsx');
  expect(panel).toContain("{ key: 'workflow',          label: 'Workflow' }");
  expect(panel).toContain("activeTab === 'workflow'");
  // The drawer now sources workflow stages from the SAME canonical investigation payload
  // the full incident page uses (fetched once per selected incident), not a separate
  // /workflow request — so the drawer and full page can never disagree.
  expect(panel).toContain('/incidents/${encodeURIComponent(selectedId)}/investigation');
  expect(panel).toContain('<WorkflowTab stages={workflowStages} load={investigationLoad} />');
  expect(panel).toContain('workflowStateLabel(s.state)');
  // Existing tabs are preserved (regression).
  expect(panel).toContain("{ key: 'overview',          label: 'Overview' }");
  expect(panel).toContain("{ key: 'ai-investigation',  label: 'AI Investigation' }");
});

test('detail route renders the investigator hero and preserves the drawer', () => {
  const detail = appSource('(product)/incidents/[incidentId]/page.tsx');
  expect(detail).toContain('<ForensicInvestigatorPanel incidentId={incidentId} />');
  expect(detail).toContain('<IncidentsPanel initialSelectedId={incidentId} />');
});

test('forensic proxy routes exist and target the backend', () => {
  const base = path.join(__dirname, '..', 'app', 'api', 'incidents', '[incidentId]');
  const investigation = fs.readFileSync(path.join(base, 'investigation', 'route.ts'), 'utf-8');
  const workflow = fs.readFileSync(path.join(base, 'workflow', 'route.ts'), 'utf-8');
  const investigations = fs.readFileSync(path.join(base, 'investigations', 'route.ts'), 'utf-8');
  const reports = fs.readFileSync(path.join(base, 'reports', 'route.ts'), 'utf-8');
  expect(investigation).toContain('/investigation');
  expect(workflow).toContain('/workflow');
  expect(investigations).toContain('/investigations');
  expect(reports).toContain('/reports');
});
