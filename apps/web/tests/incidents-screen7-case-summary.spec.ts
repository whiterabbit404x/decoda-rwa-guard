/**
 * Screen 7 — case summary, Case File snapshot and queue counters.
 *
 * Two layers (the repo's established frontend test style — no running server):
 *   1. Executable unit tests for the pure presentation helpers.
 *   2. Source-level structural tests asserting the KPI row, the Case File panel
 *      and the Overview read canonical backend facts and fail closed.
 *
 * The truthfulness invariants pinned here (see CLAUDE.md):
 *   * a KPI tile with no data shows "—", never a 0 an operator reads as "nothing
 *     is open";
 *   * absence and "could not establish truth" are neutral, never success — no data
 *     must not be shown as safe;
 *   * the counters come from the workspace-wide backend summary, never from the
 *     capped, filtered page of rows the list happens to hold;
 *   * response state is folded from Screen 8's own action records; Screen 7 reports
 *     it and never claims an execution or an approval of its own.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import {
  buildIntegritySummary,
  caseSectionRecorded,
  operationalOutcome,
  operationalOutcomeDetail,
  operationalOutcomeLabel,
  operationalOutcomeVariant,
  caseStateLabel,
  caseStateVariant,
  formatCaseAmount,
  humanizeToken,
  parseIncidentQueueCounts,
  responseStateVariant,
  summarizeResponseState,
  type CaseResponseAction,
} from '../app/incident-forensics-presentation';

function appSource(fileName: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', fileName), 'utf-8');
}

/* ───────────────── 1. Queue counters (KPI row) ─────────────────────────── */

test('queue counters are read from the backend payload', () => {
  const counts = parseIncidentQueueCounts({
    counts: { open_incidents: 4, critical_incidents: 1, in_investigation: 2, awaiting_response: 1, total: 9 },
  });
  expect(counts).toEqual({
    open_incidents: 4, critical_incidents: 1, in_investigation: 2, awaiting_response: 1, total: 9,
  });
});

test('a flat payload without the counts envelope still resolves', () => {
  const counts = parseIncidentQueueCounts({
    open_incidents: 0, critical_incidents: 0, in_investigation: 0, awaiting_response: 0,
  });
  expect(counts?.open_incidents).toBe(0);
  // A REAL zero from the backend is a fact and is kept.
  expect(counts?.total).toBe(0);
});

test('a payload missing a counter yields null rather than a fabricated zero', () => {
  // A tile showing 0 claims "nothing is open". Missing data does not support it.
  expect(parseIncidentQueueCounts({ counts: { open_incidents: 3 } })).toBeNull();
  expect(parseIncidentQueueCounts({ counts: { open_incidents: 3, critical_incidents: 1, in_investigation: 0 } })).toBeNull();
  expect(parseIncidentQueueCounts(null)).toBeNull();
  expect(parseIncidentQueueCounts('nope')).toBeNull();
  expect(parseIncidentQueueCounts({ detail: 'permission denied' })).toBeNull();
});

test('a non-numeric counter is rejected, never coerced into a number', () => {
  expect(parseIncidentQueueCounts({
    counts: { open_incidents: 'many', critical_incidents: 1, in_investigation: 1, awaiting_response: 1 },
  })).toBeNull();
});

test('the KPI row renders backend counters and fails closed when they are missing', () => {
  const panel = appSource('incidents-panel.tsx');
  expect(panel).toContain("`${apiUrl}/incidents/summary`");
  expect(panel).toContain('QueueCounterTile');
  // The tile shows an em dash + a reason when the count is not known.
  expect(panel).toContain("value={known ? value : '—'}");
  expect(panel).toContain("'Count unavailable'");
  // Every tile carries the definition behind its number.
  for (const meta of ['Not resolved or closed', 'Critical severity, still open',
    'Status: investigating', 'Awaiting a response decision']) {
    expect(panel, meta).toContain(meta);
  }
});

test('the queue-summary proxy route targets the backend endpoint', () => {
  const route = fs.readFileSync(
    path.join(__dirname, '..', 'app', 'api', 'incidents', 'summary', 'route.ts'), 'utf-8');
  expect(route).toContain("backendPath: '/incidents/summary'");
  expect(route).toContain('proxyJsonToBackend');
});

/* ───────────────── 2. Case section states ──────────────────────────────── */

test('every case section state has operator wording', () => {
  expect(caseStateLabel('not_recorded')).toBe('Not recorded');
  expect(caseStateLabel('observed')).toBe('Observed');
  expect(caseStateLabel('anomaly')).toBe('Mismatch');
  expect(caseStateLabel('indeterminate')).toBe('Could not be established');
  expect(caseStateLabel('reconciled')).toBe('Reconciled');
  expect(caseStateLabel('decided')).toBe('Decision recorded');
});

test('an unknown state is reported as unknown, never guessed into a good one', () => {
  expect(caseStateLabel('brand_new_state')).toBe('Unknown');
  expect(caseStateLabel(null)).toBe('Unknown');
  expect(caseStateVariant('brand_new_state')).toBe('neutral');
});

test('absence and unestablished truth are never styled as success', () => {
  // No data must not be shown as safe.
  expect(caseStateVariant('not_recorded')).toBe('neutral');
  expect(caseStateVariant('indeterminate')).toBe('neutral');
  expect(caseStateVariant('anomaly')).toBe('danger');
  expect(caseStateVariant('reconciled')).toBe('success');
});

test('only a recorded section renders its facts', () => {
  expect(caseSectionRecorded('not_recorded')).toBe(false);
  expect(caseSectionRecorded(undefined)).toBe(false);
  expect(caseSectionRecorded('observed')).toBe(true);
});

/* ───────────────── 3. Amounts + tokens ─────────────────────────────────── */

test('an amount is rendered exactly as recorded, with its unit', () => {
  expect(formatCaseAmount({ value: '500000', decimals: 0, unit: 'units' })).toBe('500000 units');
  expect(formatCaseAmount({ value: '12.5', unit: null })).toBe('12.5');
});

test('an unrecorded amount renders nothing rather than a zero', () => {
  expect(formatCaseAmount(null)).toBeNull();
  expect(formatCaseAmount({ value: null })).toBeNull();
  expect(formatCaseAmount({ value: '  ' })).toBeNull();
});

test('backend tokens become operator labels without inventing meaning', () => {
  expect(humanizeToken('OPERATIONAL_INTEGRITY')).toBe('Operational Integrity');
  expect(humanizeToken('unmatched_issuance')).toBe('Unmatched Issuance');
  expect(humanizeToken('mint')).toBe('Mint');
  expect(humanizeToken(null)).toBeNull();
  expect(humanizeToken('')).toBeNull();
});

/* ───────────────── 4. Response state (Screen 8's facts) ────────────────── */

const pending: CaseResponseAction = { id: 'a', approval_status: 'pending' };
const approved: CaseResponseAction = { id: 'b', approval_status: 'approved' };
const executed: CaseResponseAction = { id: 'c', approval_status: 'approved', execution_status: 'executed' };
const failed: CaseResponseAction = { id: 'd', execution_status: 'failed' };

test('no recommended action says so — it never reads as "nothing to do"', () => {
  const state = summarizeResponseState([]);
  expect(state.state).toBe('none');
  expect(state.label).toBe('No response action recommended yet');
  expect(responseStateVariant(state.state)).toBe('neutral');
});

test('a failed execution outranks everything else', () => {
  const state = summarizeResponseState([pending, executed, failed]);
  expect(state.state).toBe('failed');
  expect(responseStateVariant(state.state)).toBe('danger');
});

test('a pending approval outranks an execution that already happened', () => {
  const state = summarizeResponseState([executed, pending]);
  expect(state.state).toBe('awaiting_approval');
  expect(state.awaitingApproval).toBe(1);
  expect(responseStateVariant(state.state)).toBe('warning');
});

test('approved-but-not-executed is never reported as executed', () => {
  const state = summarizeResponseState([approved]);
  expect(state.state).toBe('approved');
  expect(state.executed).toBe(0);
  expect(state.label).toContain('not executed');
});

test('an execution is claimed only from the canonical execution_status', () => {
  expect(summarizeResponseState([executed]).state).toBe('executed');
  // A lifecycle label alone never promotes an action to executed.
  expect(summarizeResponseState([{ id: 'x', lifecycle_label: 'Executed' }]).state).toBe('recommended');
});

test('the fold is deterministic', () => {
  const rows = [pending, approved, executed];
  expect(summarizeResponseState(rows)).toEqual(summarizeResponseState(rows));
});

/* ───────────────── 5. Case File snapshot fields ────────────────────────── */

test('the Case File panel shows the case metadata and the six integrity states', () => {
  const panel = appSource('incidents-panel.tsx');
  // Case metadata stays in the header, as labelled fields.
  for (const label of ['Incident ID', 'Status', 'Created', 'Updated', 'Asset', 'Assigned',
    'Linked Alert', 'Linked Detection', 'Canonical Event', 'Evidence Source']) {
    expect(panel, label).toContain(`label="${label}"`);
  }
  // The operational-integrity state is one compact row per domain, folded by the shared
  // pure builder — the panel labels nothing itself, so the summary can only say what the
  // canonical record says.
  expect(panel).toContain('Integrity Summary');
  expect(panel).toContain('buildIntegritySummary(');
  expect(panel).toContain('<IntegritySummaryLine key={row.key} row={row} />');
  const builder = appSource('incident-forensics-presentation.ts');
  for (const label of ['Detection', 'On-Chain', 'Operational', 'Policy', 'Response', 'Evidence']) {
    expect(builder, label).toContain(`label: '${label}'`);
  }
  // Sourced from the incident's own forensic record + Screen 8's action rows.
  expect(panel).toContain('incidentEvidence?.case_summary');
  expect(panel).toContain('summarizeResponseState(responseActions)');
});

test('a case-file field distinguishes loading, unreadable and genuinely absent', () => {
  const panel = appSource('incidents-panel.tsx');
  const builder = appSource('incident-forensics-presentation.ts');
  expect(panel).toContain('ForensicFieldFallback');
  for (const text of ['Loading…', 'Not permitted in this workspace', 'Incident not found', 'Unavailable']) {
    expect(panel, text).toContain(text);
  }
  expect(panel).toContain('No canonical event linked');
  // Every integrity row makes the same three-way distinction, in one place.
  for (const text of ['Loading…', 'Not permitted in this workspace', 'Incident not found', 'Unavailable']) {
    expect(builder, text).toContain(text);
  }
  for (const absent of ['No linked detection', 'Not available', 'Not collected', 'Not evaluated',
    'No response action', 'No snapshot']) {
    expect(builder, absent).toContain(absent);
  }
});

/* ───────────────── 6. Overview (executive forensic summary) ────────────── */

test('the Overview answers the case questions in order, from records only', () => {
  const src = appSource('incident-case-overview.tsx');
  for (const section of ['Detection', 'On-chain state', 'Operational state', 'Policy', 'Response', 'Evidence']) {
    expect(src, section).toContain(`title="${section}"`);
  }
});

test('the Overview renders every load state and never a partial summary as complete', () => {
  const src = appSource('incident-case-overview.tsx');
  for (const state of ['loading', 'unauthorized', 'not_found', 'error']) {
    expect(src, state).toContain(`'${state}'`);
  }
  expect(src).toContain('No partial summary is shown as the complete case.');
});

test('each Overview section states its own absence rather than borrowing a verdict', () => {
  const src = appSource('incident-case-overview.tsx');
  for (const copy of [
    'No chain observation is recorded for this incident.',
    'No policy evaluation is recorded for this incident.',
    'No response action has been recommended for this incident yet.',
    'No evidence package has been created for this incident yet.',
  ]) {
    expect(src, copy).toContain(copy);
  }
  // Detection and operational absence are worded by the presentation module, which
  // states WHY the record is missing rather than only that it is: the incident's
  // origin for a detection, and "nothing was compared" for the operational half.
  expect(src).toContain('missingDetectionExplanation(origin)');
  expect(src).toContain('operationalOutcomeDetail(outcome)');
});

test('an absent operational record is never worded as a failed comparison', () => {
  // NOT COLLECTED and NOT MATCHED are opposite claims about a customer's books.
  expect(operationalOutcome({ state: 'not_recorded' })).toBe('not_collected');
  expect(operationalOutcome({ state: 'anomaly' })).toBe('not_matched');
  expect(operationalOutcomeLabel('not_collected')).toBe('Not collected');
  expect(operationalOutcomeLabel('not_matched')).toBe('Not matched');
  // Absence is neutral; only a real failed match is danger.
  expect(operationalOutcomeVariant('not_collected')).toBe('neutral');
  expect(operationalOutcomeVariant('not_matched')).toBe('danger');
  expect(operationalOutcomeDetail('not_collected')).toContain('not a mismatch');
});

test('the policy verdict is the deterministic engine\'s, and Screen 8 still owns the response', () => {
  const src = appSource('incident-case-overview.tsx');
  expect(src).toContain('Decided by the deterministic policy engine.');
  expect(src).toContain('policyDecisionVariant');
  // Screen 7 links to Screen 8; it never approves or executes. The Overview is
  // read-only by construction: it renders links and text, no controls and no
  // mutating request of any kind.
  expect(src).toContain('Open in Response Actions');
  expect(src).not.toContain('<button');
  expect(src).not.toContain('onClick');
  expect(src).not.toContain("method: 'POST'");
  expect(src).not.toContain('fetch(');
});

test('both Screen 7 surfaces read the same shared case summary', () => {
  // The full investigation renders the complete Overview across the main content width …
  const tabs = appSource('incident-case-file-tabs.tsx');
  expect(tabs).toContain('IncidentCaseOverview');
  expect(tabs).toContain('case_summary');
  expect(tabs).toContain('layout="wide"');
  // … and the Case File folds the SAME record into its compact integrity summary, from
  // the same shared fetch — never a second reading of it.
  const panel = appSource('incidents-panel.tsx');
  expect(panel).toContain('useIncidentEvidence(');
  expect(panel).toContain('incidentEvidence?.case_summary');
  expect(panel).toContain('buildIntegritySummary(');
});

test('the wide Overview lays the record out in the order the investigation ran', () => {
  const src = appSource('incident-case-overview.tsx');
  // Chain reading beside operational reading, their reconciliation result, then the
  // policy verdict — with the case state (response, evidence) in its own column.
  expect(src).toContain('incidentCaseOverview-wide');
  expect(src).toContain('incidentCaseAnalysis');
  expect(src).toContain('incidentCaseCompare');
  expect(src).toContain('incidentCaseStateColumn');
  expect(src).toContain('Operational integrity analysis');
  expect(src).toContain('Case state');
  // The reconciliation band restates the operational record's own fields; it never
  // computes a second verdict, and an unreconciled case stays neutral.
  expect(src).toContain('aria-label="Reconciliation result"');
  // An uncollected operational half reads "Unavailable", never "Not matched": the
  // reconciliation band must not turn a gap in coverage into a failed comparison.
  expect(src).toContain('Unavailable');
  expect(src).toContain('No operational data was collected for this event');
  expect(src).toContain('operationalOutcomeVariant(outcome)');
});

/* ───────────────── 7. AI authority ─────────────────────────────────────── */

test('the AI investigation panel states its authority next to its output', () => {
  const src = appSource('ai-investigation-panel.tsx');
  expect(src).toContain('aiAuthorityNotice');
  expect(src).toContain('Analysis &amp; recommendation only — never approval or execution');
  expect(src).toContain("This incident&apos;s collected evidence record");
  expect(src).toContain('Policy decisions, integrity states and timestamps are unchanged by AI');
});

/* ───────────────── 8. No reference-design values ───────────────────────── */

test('no reference-design value is hard-coded into the new Screen 7 code', () => {
  const sources = ['incident-case-overview.tsx', 'incidents-panel.tsx', 'use-incident-forensics.ts']
    .map(appSource).join('\n');
  for (const literal of ['INC-2026-017', 'POL-MINT-007', '+500,000', 'Transfer Agent', 'RWA-004']) {
    expect(sources, literal).not.toContain(literal);
  }
});

test('the case summary layout adds no gradients, glows or animated states', () => {
  const css = fs.readFileSync(path.join(__dirname, '..', 'app', 'styles.css'), 'utf-8');
  const block = css.slice(css.indexOf('.incidentCaseOverview'), css.indexOf('.aiAuthorityNotice'));
  expect(block.length).toBeGreaterThan(0);
  for (const banned of ['gradient', 'box-shadow', 'animation', '@keyframes', 'filter:']) {
    expect(block, banned).not.toContain(banned);
  }
});

/* ───────────────── 9. Response state waits for Screen 8's records ──────── */

test('"no response action" is only claimed once Screen 8 has actually been read', () => {
  // An empty array mid-fetch (or after a failed read) must not render as "no
  // response action recommended" — that is a claim about Screen 8's data.
  for (const file of ['incidents-panel.tsx', 'incident-case-file-tabs.tsx']) {
    const src = appSource(file);
    expect(src, file).toContain('setResponseLoad');
    expect(src, file).toContain("setResponseLoad('loading')");
    expect(src, file).toContain('loadStateFor(r.status, rows.length > 0)');
    expect(src, file).toContain("setResponseLoad('error')");
  }
  const overview = appSource('incident-case-overview.tsx');
  expect(overview).toContain('responseKnown');
  expect(overview).toContain('No response state is shown rather than an unverified one.');
});

test('an evidence count the backend did not report is never rendered as zero', () => {
  const builder = appSource('incident-forensics-presentation.ts');
  expect(builder).toContain("typeof evidence.artifact_count === 'number' ? evidence.artifact_count : null");
  expect(builder).not.toContain('evidence.artifact_count ?? 0');
  // Executable proof: an evidence section with no reported count says "No snapshot"
  // and carries no badge, rather than claiming zero artifacts.
  const row = buildIntegritySummary({
    summary: { evidence: { snapshot_status: null } },
    summaryLoad: 'ready',
    response: summarizeResponseState([]),
    responseLoad: 'empty',
  }).find((r) => r.key === 'evidence');
  expect(row?.value).toBe('No snapshot');
  expect(row?.badge).toBeNull();
  expect(row?.recorded).toBe(false);
});
