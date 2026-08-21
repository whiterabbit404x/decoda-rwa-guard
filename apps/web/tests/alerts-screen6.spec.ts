/**
 * Screen 6 — Alerts / Active Alerts contract tests.
 *
 * Two layers:
 *   1. Executable unit tests for the canonical presentation helpers (pure logic).
 *   2. Source-level structural tests that assert the screen renders the required
 *      states/columns/actions and does NOT hardcode the reference-design numbers.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import {
  confidenceText,
  getTriageDisplayState,
  recommendationLabel,
  severityLabel,
  severityVariant,
  statusLabel,
  statusVariant,
  triageModeLabel,
  triageModeVariant,
  triageSummaryText,
  type TriageState,
} from '../app/alerts-triage-presentation';

function appSource(fileName: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', fileName), 'utf-8');
}

function triageState(overrides: Partial<TriageState> = {}): TriageState {
  return {
    schema_ready: true,
    ai_available: false,
    mode: 'rules_based',
    processing: false,
    last_run: null,
    last_success_at: null,
    cluster_count: 0,
    unclustered_count: null,
    top_recommendation: null,
    clusters_preview: [],
    state: 'not_run',
    ...overrides,
  };
}

/* ───────────────────────── 1. Pure presentation logic ─────────────────── */

test('severity labels + variants are canonical (Critical/High/Medium/Low)', () => {
  expect(severityLabel('critical')).toBe('Critical');
  expect(severityLabel('high')).toBe('High');
  expect(severityLabel('medium')).toBe('Medium');
  expect(severityLabel('low')).toBe('Low');
  expect(severityVariant('critical')).toBe('danger');
  expect(severityVariant('high')).toBe('danger');
  expect(severityVariant('medium')).toBe('warning');
  expect(severityVariant('low')).toBe('success');
});

test('status labels use canonical lifecycle names, not invented ones', () => {
  expect(statusLabel('open')).toBe('Open');
  expect(statusLabel('investigating')).toBe('Investigating');
  expect(statusLabel('acknowledged')).toBe('Acknowledged');
  expect(statusLabel('linked_to_incident')).toBe('Linked to Incident');
  expect(statusLabel('resolved')).toBe('Resolved');
  expect(statusVariant('open')).toBe('danger');
  expect(statusVariant('resolved')).toBe('success');
});

test('triage mode label: rules_based -> Rules-based, ai_assisted -> AI-assisted', () => {
  expect(triageModeLabel('rules_based')).toBe('Rules-based');
  expect(triageModeLabel('ai_assisted')).toBe('AI-assisted');
  expect(triageModeLabel('unavailable')).toBe('Unavailable');
  // Fail-closed default is rules-based (never claims AI).
  expect(triageModeLabel(undefined)).toBe('Rules-based');
  expect(triageModeVariant('ai_assisted')).toBe('info');
});

test('confidence: missing reads "Not calculated", present is a real percent + band', () => {
  expect(confidenceText(null)).toBe('Not calculated');
  expect(confidenceText(undefined)).toBe('Not calculated');
  expect(confidenceText(0.87, 'high')).toBe('87% (High)');
  expect(confidenceText(0.5, 'medium')).toBe('50% (Medium)');
  expect(confidenceText(0.9)).toBe('90%');
});

test('recommendation labels map the canonical categories', () => {
  expect(recommendationLabel('escalate_to_incident')).toBe('Escalate to incident');
  expect(recommendationLabel('validate_oracle_source')).toBe('Validate oracle source');
  expect(recommendationLabel('review_immediately')).toBe('Review immediately');
  expect(recommendationLabel(null)).toBe('Continue monitoring');
});

test('Run Triage is disabled while a run is processing (never optimistic)', () => {
  const processing = getTriageDisplayState({ triage: triageState({ processing: true, state: 'processing' }) });
  expect(processing.runDisabled).toBe(true);
  expect(processing.runBusy).toBe(true);
  expect(processing.statusLabel).toBe('Running…');
});

test('Run Triage is disabled while the mutation is in flight', () => {
  const inFlight = getTriageDisplayState({ triage: triageState(), mutationInFlight: true });
  expect(inFlight.runDisabled).toBe(true);
  expect(inFlight.runBusy).toBe(true);
});

test('triage display: not-run is runnable, degraded retries, provisioning is blocked', () => {
  const notRun = getTriageDisplayState({ triage: triageState({ state: 'not_run' }) });
  expect(notRun.runLabel).toBe('Run Triage');
  expect(notRun.runDisabled).toBe(false);
  expect(notRun.statusLabel).toBe('Triage not run');

  const degraded = getTriageDisplayState({ triage: triageState({ state: 'degraded' }) });
  expect(degraded.runLabel).toBe('Retry Triage');
  expect(degraded.statusLabel).toBe('Last run failed');

  const provisioning = getTriageDisplayState({ triage: triageState({ schema_ready: false }) });
  expect(provisioning.runDisabled).toBe(true);
  expect(provisioning.statusLabel).toBe('Provisioning');

  const noPerm = getTriageDisplayState({ triage: triageState({ state: 'not_run' }), canRun: false });
  expect(noPerm.runDisabled).toBe(true);
});

test('triage summary sentence is DERIVED from run counts, never hardcoded', () => {
  expect(triageSummaryText(null)).toBe('Triage not run.');
  expect(triageSummaryText(triageState({ schema_ready: false }))).toContain('provisioning');
  const grouped = triageState({
    state: 'ready',
    last_run: {
      id: 'r1', status: 'completed', mode: 'rules_based', alerts_considered: 23,
      clusters_total: 5, clusters_created: 5, unclustered_count: 0,
    },
  });
  // Reference wording, but computed from the run (23 considered, 0 unclustered, 5 clusters).
  expect(triageSummaryText(grouped)).toBe('Grouped 23 related alerts into 5 clusters.');
  const none = triageState({
    state: 'ready',
    last_run: { id: 'r2', status: 'completed', mode: 'rules_based', alerts_considered: 4, clusters_total: 0, clusters_created: 0, unclustered_count: 4 },
  });
  expect(triageSummaryText(none)).toContain('No related-alert clusters');
});

/* ───────────────────────── 2. Page + screen structure ─────────────────── */

test('/alerts page renders the h1, the screen, and a triage-focused subtitle', () => {
  const src = appSource('(product)/alerts/page.tsx');
  expect(src).toContain('export default function AlertsPage');
  expect(src).toContain('<h1>Active Alerts</h1>');
  // The large runtime/coverage diagnostics panel no longer sits above Active Alerts —
  // detailed monitoring diagnostics live on /system-health, and a degraded runtime is
  // surfaced by the compact global health warning in the app shell. Active Alerts is the
  // primary content near the top of the page.
  expect(src).not.toContain('RuntimeSummaryPanel');
  expect(src).toContain('AlertsScreen');
  expect(src.toLowerCase()).toContain('cluster');
});

test('severity tabs All/Critical/High/Medium/Low render real counts from the read model', () => {
  const screen = appSource('alerts-screen.tsx');
  for (const label of ['All', 'Critical', 'High', 'Medium', 'Low']) {
    expect(screen).toContain(`label: '${label}'`);
  }
  // Counts come from severity_totals in the same summary payload as the rows.
  expect(screen).toContain('summary?.severity_totals');
  expect(screen).toContain('totals[tab.countKey]');
  expect(screen).toContain('data-testid={`severity-tab-');
});

test('table columns are exactly Severity/Alert Title/Asset/Status/Time/Cluster', () => {
  const screen = appSource('alerts-screen.tsx');
  expect(screen).toContain("['Severity', 'Alert Title', 'Asset', 'Status', 'Time', 'Cluster']");
});

test('all required states are implemented and testable', () => {
  const screen = appSource('alerts-screen.tsx');
  expect(screen).toContain('data-testid="alerts-loading"');
  expect(screen).toContain('data-testid="alerts-error"');
  expect(screen).toContain('data-testid="alerts-empty"');          // no active alerts
  expect(screen).toContain('data-testid="alerts-empty-filters"');  // no results for filters
  expect(screen).toContain('data-testid="triage-provisioning"');   // schema provisioning
  expect(screen).toContain('data-testid="unclustered"');           // Unclustered cell
});

test('unclustered alerts show "Unclustered" (never a made-up cluster)', () => {
  const screen = appSource('alerts-screen.tsx');
  expect(screen).toContain('>Unclustered</span>');
});

test('AI Alert Triage Agent panel + Review Clusters + Run Triage actions exist', () => {
  const screen = appSource('alerts-screen.tsx');
  expect(screen).toContain('AI Alert Triage Agent');
  expect(screen).toContain('data-testid="run-triage"');
  expect(screen).toContain('data-testid="review-clusters"');
  expect(screen).toContain('data-testid="cluster-drawer"');
  expect(screen).toContain('data-testid="cluster-item"');
  // Response actions are explicitly NOT executed from Screen 6.
  expect(screen).toContain('Response actions are not run from this screen.');
});

test('AI-unavailable + rules-based states are surfaced truthfully', () => {
  const screen = appSource('alerts-screen.tsx');
  expect(screen).toContain('!triage.ai_available');
  expect(screen).toContain('deterministic rules only');
  // AI narrative is rendered as plain text, never dangerouslySetInnerHTML.
  expect(screen).not.toContain('dangerouslySetInnerHTML');
});

test('pagination is backend-driven with an accurate range + total', () => {
  const screen = appSource('alerts-screen.tsx');
  expect(screen).toContain('data-testid="pagination-range"');
  expect(screen).toContain('Showing {pagination.showing_from}–{pagination.showing_to} of {pagination.total}');
  expect(screen).toContain('data-testid="page-prev"');
  expect(screen).toContain('data-testid="page-next"');
});

test('changing filters resets pagination to page 1', () => {
  const screen = appSource('alerts-screen.tsx');
  // Every filter setter passes resetPage.
  expect(screen).toContain('resetPage: true');
  expect(screen).toContain("params.delete('page')");
});

test('screen does NOT hardcode the reference-design numbers or labels', () => {
  const screen = appSource('alerts-screen.tsx');
  // None of the reference mock values may be baked into the UI.
  for (const forbidden of ['87%', '23 related alerts into 5', 'Cluster A', 'Cluster B', 'Oracle Price Deviation', '$3.42B', '28 alerts']) {
    expect(screen).not.toContain(forbidden);
  }
});
