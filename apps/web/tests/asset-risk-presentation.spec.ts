/**
 * Screen 3 — risk badge / monitoring-health / formatting helpers (pure logic).
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import {
  assessmentActionLabel,
  assessmentStatusLabel,
  assessmentStatusVariant,
  getAssetAssessmentDisplayState,
  formatPercent,
  formatUsd,
  isReserveBackedRwaType,
  monitoringHealthLabel,
  monitoringHealthVariant,
  relativeTime,
  reserveCoverageMessage,
  reserveStatusLabel,
  reserveStatusVariant,
  riskLevelForScore,
  riskLevelLabel,
  riskLevelVariant,
  rwaTypeLabel,
  workspaceAssessmentAction,
  RISK_SCORE_TOOLTIP,
  type AssessmentCapability,
} from '../app/asset-risk-presentation';

function capability(overrides: Partial<AssessmentCapability> = {}): AssessmentCapability {
  return {
    background_enabled: false,
    on_demand_enabled: true,
    worker_healthy: false,
    last_heartbeat_at: null,
    execution_mode: 'on_demand',
    ...overrides,
  };
}

test('risk level thresholds match the canonical 0-100 scale (higher = riskier)', () => {
  expect(riskLevelForScore(0)).toBe('low');
  expect(riskLevelForScore(29)).toBe('low');
  expect(riskLevelForScore(30)).toBe('medium');
  expect(riskLevelForScore(59)).toBe('medium');
  expect(riskLevelForScore(60)).toBe('high');
  expect(riskLevelForScore(79)).toBe('high');
  expect(riskLevelForScore(80)).toBe('critical');
  expect(riskLevelForScore(100)).toBe('critical');
  expect(riskLevelForScore(null)).toBe('unassessed');
  expect(riskLevelForScore(undefined)).toBe('unassessed');
});

test('risk badge variants: low green, medium warning, high/critical danger', () => {
  expect(riskLevelVariant('low')).toBe('success');
  expect(riskLevelVariant('medium')).toBe('warning');
  expect(riskLevelVariant('high')).toBe('danger');
  expect(riskLevelVariant('critical')).toBe('danger');
  expect(riskLevelLabel('unassessed')).toBe('Not assessed');
});

test('risk score tooltip explains higher = greater risk', () => {
  expect(RISK_SCORE_TOOLTIP.toLowerCase()).toContain('higher');
  expect(RISK_SCORE_TOOLTIP.toLowerCase()).toContain('risk');
});

test('monitoring health never turns unknown/missing into healthy', () => {
  expect(monitoringHealthLabel('healthy')).toBe('Healthy');
  expect(monitoringHealthVariant('healthy')).toBe('success');
  expect(monitoringHealthVariant('critical')).toBe('danger');
  expect(monitoringHealthVariant('not_configured')).toBe('neutral');
  expect(monitoringHealthLabel('')).toBe('Unknown');
  expect(monitoringHealthVariant('')).toBe('neutral');
});

test('reserve status labels are truthful (insufficient evidence surfaced)', () => {
  expect(reserveStatusLabel('insufficient_evidence')).toBe('Insufficient evidence');
  expect(reserveStatusLabel('over_collateralized')).toBe('Over-collateralized');
});

test('reserve status distinguishes not applicable, not configured, insufficient evidence', () => {
  // Wallet / non-reserve asset — never "missing evidence".
  expect(reserveStatusLabel('not_applicable')).toBe('Not applicable');
  expect(reserveStatusLabel('not_required')).toBe('Not applicable');
  expect(reserveStatusVariant('not_applicable')).toBe('info');
  // No reserve-backed assets configured — distinct from insufficient evidence.
  expect(reserveStatusLabel('not_configured')).toBe('Not configured');
  expect(reserveStatusVariant('not_configured')).toBe('neutral');
});

test('reserve coverage message is generated from structured state only', () => {
  expect(reserveCoverageMessage('not_configured', 0)).toBe('No reserve-backed assets are configured.');
  expect(reserveCoverageMessage('not_configured', 2)).toContain('cannot be verified');
  expect(reserveCoverageMessage('insufficient_evidence')).toContain('no verified reserve evidence');
  expect(reserveCoverageMessage('not_applicable')).toContain('does not apply');
  expect(reserveCoverageMessage('healthy')).toBe('');
});

test('assessment status labels + variants cover the lifecycle', () => {
  expect(assessmentStatusLabel('not_started')).toBe('Not started');
  expect(assessmentStatusLabel('not_assessed')).toBe('Not started');
  expect(assessmentStatusLabel('queued')).toBe('Queued');
  expect(assessmentStatusLabel('running')).toBe('Running');
  expect(assessmentStatusLabel('complete')).toBe('Complete');
  expect(assessmentStatusLabel('partial')).toBe('Partial');
  expect(assessmentStatusLabel('failed')).toBe('Failed');
  expect(assessmentStatusLabel('blocked')).toBe('Blocked');
  expect(assessmentStatusVariant('complete')).toBe('success');
  expect(assessmentStatusVariant('running')).toBe('info');
  expect(assessmentStatusVariant('partial')).toBe('warning');
  expect(assessmentStatusVariant('failed')).toBe('danger');
  expect(assessmentStatusVariant('blocked')).toBe('danger');
});

test('per-asset Run button renders canonical state, never ambiguous "pending"', () => {
  // No prior assessment, background worker down but on-demand available -> the
  // bounded on-demand affordance (same canonical selector as the workspace panel).
  expect(assessmentActionLabel('not_started', capability())).toEqual({ label: 'Run limited assessment', disabled: false });
  expect(assessmentActionLabel('not_assessed', capability())).toEqual({ label: 'Run limited assessment', disabled: false });
  // With a healthy background worker -> plain "Run assessment".
  const bg = capability({ background_enabled: true, worker_healthy: true, execution_mode: 'background' });
  expect(assessmentActionLabel('not_started', bg)).toEqual({ label: 'Run assessment', disabled: false });
  // A persisted queued/running job -> that exact state, and the button is disabled.
  expect(assessmentActionLabel('queued', capability())).toEqual({ label: 'Assessment queued', disabled: true });
  expect(assessmentActionLabel('running', capability())).toEqual({ label: 'Assessment running', disabled: true });
  // After a completed / partial assessment -> "Run again".
  expect(assessmentActionLabel('completed', capability()).label).toBe('Run again');
  expect(assessmentActionLabel('partial', capability()).label).toBe('Run again');
  expect(assessmentActionLabel('stale', capability()).label).toBe('Run again');
  // After a failure / block -> "Retry assessment".
  expect(assessmentActionLabel('failed', capability()).label).toBe('Retry assessment');
  expect(assessmentActionLabel('blocked', capability()).label).toBe('Retry assessment');
});

test('per-asset Run button is disabled "Worker unavailable" when no execution path exists', () => {
  const cap = capability({ on_demand_enabled: false, execution_mode: 'unavailable' });
  const action = assessmentActionLabel('not_started', cap);
  expect(action.label).toBe('Worker unavailable');
  expect(action.disabled).toBe(true);
  expect(action.hint).toBeTruthy();
  // Even a previously-failed asset cannot be retried without an execution path.
  expect(assessmentActionLabel('failed', cap).label).toBe('Worker unavailable');
});

test('workspace Run button reflects capability, never an unassessed-count "N pending"', () => {
  // Healthy background worker -> plain "Run assessment".
  expect(workspaceAssessmentAction({
    assessmentStatus: 'not_started', capability: capability({ background_enabled: true, worker_healthy: true, execution_mode: 'background' }), hasAssets: true,
  })).toEqual({ label: 'Run assessment', disabled: false });
  // Background worker down but on-demand available -> "Run limited assessment".
  expect(workspaceAssessmentAction({
    assessmentStatus: 'not_started', capability: capability(), running: false, hasAssets: true,
  }).label).toBe('Run limited assessment');
  // No execution path -> disabled "Worker unavailable".
  const unavailable = workspaceAssessmentAction({
    assessmentStatus: 'not_started', capability: capability({ on_demand_enabled: false, execution_mode: 'unavailable' }), hasAssets: true,
  });
  expect(unavailable).toMatchObject({ label: 'Worker unavailable', disabled: true });
  // A genuinely queued job with a healthy worker -> "Assessment queued".
  expect(workspaceAssessmentAction({
    assessmentStatus: 'queued', capability: capability({ background_enabled: true, worker_healthy: true, execution_mode: 'background' }), hasAssets: true,
  })).toMatchObject({ label: 'Assessment queued', disabled: true });
});

// ── Canonical assessment display-state selector (the single source of truth) ──
// These are the invariants that fix the impossible "Not started + Run assessment
// (pending)" state. The button label must never be "pending" after the POST settles.

test('fresh page with no job and no assessment shows "Run assessment", never pending', () => {
  const bg = capability({ background_enabled: true, worker_healthy: true, execution_mode: 'background' });
  const s = getAssetAssessmentDisplayState({ assessmentStatus: 'not_started', activeJob: null, capability: bg });
  expect(s.statusLabel).toBe('Not started');
  expect(s.actionLabel).toBe('Run assessment');
  expect(s.actionDisabled).toBe(false);
  expect(s.actionBusy).toBe(false);
  expect(s.actionLabel).not.toContain('pending');
});

test('disabled background worker + on-demand enabled shows "Run limited assessment"', () => {
  const s = getAssetAssessmentDisplayState({
    assessmentStatus: 'not_started', activeJob: null, capability: capability({ execution_mode: 'on_demand' }),
  });
  expect(s.actionLabel).toBe('Run limited assessment');
  expect(s.actionDisabled).toBe(false);
});

test('disabled background worker + on-demand disabled shows disabled "Worker unavailable"', () => {
  const s = getAssetAssessmentDisplayState({
    assessmentStatus: 'not_started', activeJob: null,
    capability: capability({ on_demand_enabled: false, execution_mode: 'unavailable' }),
  });
  expect(s.actionLabel).toBe('Worker unavailable');
  expect(s.actionDisabled).toBe(true);
  expect(s.hint).toBeTruthy();
});

test('mutation in flight shows "Starting assessment…" and marks the button busy', () => {
  const s = getAssetAssessmentDisplayState({
    assessmentStatus: 'not_started', activeJob: null,
    capability: capability({ execution_mode: 'on_demand' }), mutationInFlight: true,
  });
  expect(s.actionLabel).toBe('Starting assessment…');
  expect(s.actionDisabled).toBe(true);
  expect(s.actionBusy).toBe(true);
  // The pill does NOT move to "queued/running" — no job has been persisted yet.
  expect(s.statusLabel).toBe('Not started');
});

test('mutation SUCCESS clears pending: label returns to the persisted backend state', () => {
  // In flight -> "Starting assessment…"
  const inFlight = getAssetAssessmentDisplayState({
    assessmentStatus: 'not_started', capability: capability({ execution_mode: 'on_demand' }), mutationInFlight: true,
  });
  expect(inFlight.actionLabel).toBe('Starting assessment…');
  // After a successful on-demand assessment the backend state is partial/completed
  // and the request has settled (mutationInFlight=false) -> "Run again", not pending.
  const settled = getAssetAssessmentDisplayState({
    assessmentStatus: 'partial', capability: capability({ execution_mode: 'on_demand' }), mutationInFlight: false,
  });
  expect(settled.actionLabel).toBe('Run again');
  expect(settled.actionBusy).toBe(false);
  expect(settled.actionLabel).not.toContain('pending');
});

test('mutation FAILURE clears pending: label returns to a retryable backend state', () => {
  const settled = getAssetAssessmentDisplayState({
    assessmentStatus: 'failed', capability: capability({ execution_mode: 'on_demand' }), mutationInFlight: false,
  });
  expect(settled.actionLabel).toBe('Retry assessment');
  expect(settled.actionBusy).toBe(false);
  // A failed POST with no execution path falls closed to disabled, still not pending.
  const noPath = getAssetAssessmentDisplayState({
    assessmentStatus: 'not_started', capability: capability({ on_demand_enabled: false, execution_mode: 'unavailable' }), mutationInFlight: false,
  });
  expect(noPath.actionLabel).toBe('Worker unavailable');
  expect(noPath.actionLabel).not.toContain('pending');
});

test('"Assessment queued" requires a PERSISTED queued job, never a capability queue-depth', () => {
  // A capability that reports a non-zero queue depth but NO persisted active job
  // must NOT show "queued" — that was the old, misleading inference.
  const cap = capability({ background_enabled: true, worker_healthy: true, execution_mode: 'background', queue_depth: 5, active_job_count: 5 });
  const noJob = getAssetAssessmentDisplayState({ assessmentStatus: 'not_started', activeJob: null, capability: cap });
  expect(noJob.statusLabel).toBe('Not started');
  expect(noJob.actionLabel).toBe('Run assessment');
  // With an actual persisted queued job it DOES show queued (disabled).
  const withJob = getAssetAssessmentDisplayState({
    assessmentStatus: 'not_started', activeJob: { status: 'queued', job_id: 'j1' }, capability: cap,
  });
  expect(withJob.statusLabel).toBe('Queued');
  expect(withJob.actionLabel).toBe('Assessment queued');
  expect(withJob.actionDisabled).toBe(true);
  // A persisted running job -> running (disabled, busy).
  const running = getAssetAssessmentDisplayState({
    assessmentStatus: 'partial', activeJob: { status: 'running', job_id: 'j2' }, capability: cap,
  });
  expect(running.statusLabel).toBe('Running');
  expect(running.actionLabel).toBe('Assessment running');
  expect(running.actionBusy).toBe(true);
});

test('table and panel show IDENTICAL canonical status for the same backend facts', () => {
  const args = {
    assessmentStatus: 'partial',
    activeJob: null,
    capability: capability({ execution_mode: 'on_demand' }),
  } as const;
  // The table cell derives the pill; the panel derives pill + button. Same selector,
  // same inputs -> identical status pill. This is the "one canonical source" rule.
  const tableView = getAssetAssessmentDisplayState(args);
  const panelView = getAssetAssessmentDisplayState(args);
  expect(tableView.statusLabel).toBe(panelView.statusLabel);
  expect(tableView.statusVariant).toBe(panelView.statusVariant);
  expect(tableView.statusLabel).toBe('Partial');
});

test('page refresh does not restore mutation pending state (mutationInFlight defaults false)', () => {
  // After a reload, React state is fresh: mutationInFlight is false. The selector
  // must render the persisted backend state, never a lingering "Starting…" label.
  const afterReload = getAssetAssessmentDisplayState({
    assessmentStatus: 'not_started', activeJob: null, capability: capability({ execution_mode: 'on_demand' }),
    // mutationInFlight omitted == false
  });
  expect(afterReload.actionLabel).toBe('Run limited assessment');
  expect(afterReload.actionBusy).toBe(false);
  expect(afterReload.actionLabel).not.toContain('Starting');
});

test('configuration warnings do not alter assessment job/display state', () => {
  // A global monitoring contradiction flag (e.g. proof_chain_link_missing) is not an
  // input to the assessment selector at all. Same typed inputs -> same output,
  // regardless of any unrelated config warning elsewhere in the workspace.
  const cap = capability({ background_enabled: true, worker_healthy: true, execution_mode: 'background' });
  const base = getAssetAssessmentDisplayState({ assessmentStatus: 'not_started', activeJob: null, capability: cap });
  // Extra/unknown fields are ignored; the selector reads only its typed contract.
  const withNoise = getAssetAssessmentDisplayState({
    assessmentStatus: 'not_started', activeJob: null, capability: { ...cap, ...( { contradiction_flags: ['proof_chain_link_missing'] } as any) },
  });
  expect(withNoise.actionLabel).toBe(base.actionLabel);
  expect(withNoise.statusLabel).toBe(base.statusLabel);
  expect(base.actionLabel).toBe('Run assessment');
});

test('workspace with no assets disables the Run button truthfully', () => {
  const s = getAssetAssessmentDisplayState({
    assessmentStatus: 'not_started', activeJob: null,
    capability: capability({ execution_mode: 'on_demand' }), hasAssets: false,
  });
  expect(s.actionDisabled).toBe(true);
  expect(s.actionLabel).toBe('Run assessment');
});

test('reserve-backed RWA types match the backend taxonomy', () => {
  expect(isReserveBackedRwaType('tokenized_treasury')).toBe(true);
  expect(isReserveBackedRwaType('stablecoin')).toBe(true);
  expect(isReserveBackedRwaType('real_estate')).toBe(false);
  expect(isReserveBackedRwaType('other')).toBe(false);
  expect(isReserveBackedRwaType(null)).toBe(false);
});

test('currency + percent formatting', () => {
  expect(formatUsd(991320000)).toBe('$991.32M');
  expect(formatUsd(3420000000)).toBe('$3.42B');
  expect(formatUsd(null)).toBe('--');
  expect(formatUsd('')).toBe('--');
  expect(formatPercent(128, 0)).toBe('128%');
  expect(formatPercent(null)).toBe('--');
});

test('rwa type labels map canonical keys and fall back gracefully', () => {
  expect(rwaTypeLabel('tokenized_treasury')).toBe('Tokenized Treasury');
  expect(rwaTypeLabel('real_estate')).toBe('Real Estate');
  expect(rwaTypeLabel('', 'contract')).toBe('Contract');
  expect(rwaTypeLabel(null)).toBe('Unclassified');
});

test('relative time is truthful about missing timestamps', () => {
  expect(relativeTime(null)).toBe('never');
  expect(relativeTime('not-a-date')).toBe('never');
  const now = Date.parse('2026-07-24T12:00:00Z');
  expect(relativeTime('2026-07-24T11:59:30Z', now)).toBe('30s ago');
  expect(relativeTime('2026-07-24T11:30:00Z', now)).toBe('30m ago');
  expect(relativeTime('2026-07-24T09:00:00Z', now)).toBe('3h ago');
});

// AI Asset Risk Assessor panel source contract.
const panelSrc = fs.readFileSync(path.join(__dirname, '..', 'app', 'asset-risk-assessor-panel.tsx'), 'utf-8');

test('AI panel is the canonical assessor surface (not a chatbot) and consumes the summary API', () => {
  expect(panelSrc).toContain('AI Asset Risk Assessor');
  expect(panelSrc).toContain('Continuous reserve and exposure analysis');
  expect(panelSrc).toContain('Reserve Coverage');
  expect(panelSrc).toContain('Anomaly Warnings');
  expect(panelSrc).toContain('Monitoring Gaps');
  expect(panelSrc).toContain('View full risk report');
  expect(panelSrc).toContain('/api/assets/risk-summary');
  // No free-text chat surface.
  expect(panelSrc).not.toContain('chatbot');
  expect(panelSrc).not.toContain('textarea');
});

test('AI panel surfaces an operational Run assessment + assessment status + worker health', () => {
  // On-demand assessment button, gated by permission/running state.
  expect(panelSrc).toContain('onRunAssessment');
  // The status pill comes from the canonical selector's statusLabel/statusVariant.
  expect(panelSrc).toContain('display.statusLabel');
  expect(panelSrc).toContain('reserveCoverageMessage');
  // The button label + status pill both come from the ONE canonical selector — not
  // from an ambiguous unassessed-asset count and not from a separate derivation.
  expect(panelSrc).toContain('getAssetAssessmentDisplayState');
  expect(panelSrc).toContain('assessment_capability');
  // The active job (persisted) is a distinct input from the in-flight request.
  expect(panelSrc).toContain('active_job');
  expect(panelSrc).toContain('mutationInFlight');
  // The old ambiguous "(N pending)" label must be gone.
  expect(panelSrc).not.toContain('pending)');
});
