/**
 * Screen 3 – Assets / Protected Asset Registry
 * Source-level contract tests (no browser required).
 *
 * The design-contract assertions (title, columns, filters) reflect the current
 * Screen 3 (risk registry + AI Asset Risk Assessor). The truthfulness / monitoring
 * fail-closed assertions are INVARIANTS and must never be relaxed.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import { monitoringLinkStatusLabel, getMonitoringStatus, buildAssetsQuery, MONITORING_GAP_FILTER, monitoringGapFilterLabel, reconcileMonitoringGapFilter } from '../app/assets-manager';

const BASE_FILTERS = {
  search: '', asset_type: 'all', network: 'all', risk_level: 'all', monitoring_health: 'all',
  monitoring_gap: 'all', custodian: 'all', sort: 'risk', dir: 'desc', page: 1,
};

const managerSrc = fs.readFileSync(
  path.join(__dirname, '..', 'app', 'assets-manager.tsx'),
  'utf-8',
);

const pageSrc = fs.readFileSync(
  path.join(__dirname, '..', 'app', '(product)', 'assets', 'page.tsx'),
  'utf-8',
);

// 1. Assets route renders (page file imports AssetsManager)
test('assets route imports AssetsManager and resolveApiUrl', () => {
  expect(pageSrc).toContain("import AssetsManager from '../../assets-manager'");
  expect(pageSrc).toContain('resolveApiUrl');
});

// 2. Page title "Protected Assets" exists in source
test('assets manager renders page title "Protected Assets"', () => {
  expect(managerSrc).toContain('Protected Assets');
});

// 2b. Subtitle reflects the risk-scoring registry
test('page subtitle describes AI risk scoring and monitoring coverage', () => {
  expect(managerSrc).toContain('AI risk scoring and monitoring coverage for all protected assets.');
});

// 3. "Add Asset" primary button exists
test('assets manager has "Add Asset" button', () => {
  expect(managerSrc).toContain('Add Asset');
  expect(managerSrc).toContain('btn btn-primary');
});

// 4. Search input exists with placeholder "Search assets..."
test('assets manager has search input with correct placeholder', () => {
  expect(managerSrc).toContain('Search assets...');
  expect(managerSrc).toContain('aria-label="Search assets"');
});

// 5. Asset type filter uses the RWA product taxonomy plus risk/monitoring filters
test('assets manager has RWA asset type filter with "All Types" plus RWA options', () => {
  expect(managerSrc).toContain('All Types');
  expect(managerSrc).toContain('RWA_TYPE_OPTIONS');
  expect(managerSrc).toContain('aria-label="Filter by asset type"');
  expect(managerSrc).toContain('aria-label="Filter by risk level"');
  expect(managerSrc).toContain('aria-label="Filter by monitoring health"');
});

// 6. Table has the Screen 3 registry columns
test('assets table headers are Asset Name, Asset Type, Custodian, Network, Value (USD), Risk Score, Monitoring Health, Assessment', () => {
  expect(managerSrc).toContain("'Asset Name'");
  expect(managerSrc).toContain("'Asset Type'");
  expect(managerSrc).toContain("'Custodian'");
  expect(managerSrc).toContain("'Network'");
  expect(managerSrc).toContain("'Value (USD)'");
  expect(managerSrc).toContain("'Risk Score'");
  expect(managerSrc).toContain("'Monitoring Health'");
  // Assessment status + last-assessed time are surfaced in the table.
  expect(managerSrc).toContain("'Assessment'");
  expect(managerSrc).toContain('AssessmentCell');
});

// 6b. Risk score badge + tooltip + AI Assessor panel are present
test('registry renders a risk badge with tooltip and the AI Asset Risk Assessor panel', () => {
  expect(managerSrc).toContain('RiskBadge');
  expect(managerSrc).toContain('RISK_SCORE_TOOLTIP');
  expect(managerSrc).toContain('AssetRiskAssessorPanel');
});

// 6c. Server-side pagination is wired through the query string
test('registry uses server-side query params and pagination', () => {
  expect(managerSrc).toContain('/api/assets?');
  expect(managerSrc).toContain('page_size');
  expect(managerSrc).toContain('window.history.replaceState');
});

// 7. "Monitoring attached" label never appears anywhere in source
test('source never contains the banned label "Monitoring attached"', () => {
  expect(managerSrc).not.toContain('Monitoring attached');
});

// 7b. getMonitoringStatus never returns "Monitoring" when monitored_systems = 0
test('getMonitoringStatus does not return Monitoring when monitoring_systems_count is 0', () => {
  const result = getMonitoringStatus({ monitoring_link_status: 'attached', monitoring_systems_count: 0 });
  expect(result.label).not.toBe('Monitoring');
  expect(result.label).not.toBe('Monitoring attached');
});

test('getMonitoringStatus does not return Monitoring when has_linked_monitored_system is false', () => {
  const result = getMonitoringStatus({ monitoring_link_status: 'attached', has_linked_monitored_system: false });
  expect(result.label).not.toBe('Monitoring');
  expect(result.label).not.toBe('Monitoring attached');
});

// 8. Shows "Target missing" when no monitoring target
test('monitoringLinkStatusLabel returns "Target missing" for target_missing status', () => {
  expect(monitoringLinkStatusLabel({ monitoring_link_status: 'target_missing' })).toBe('Target missing');
  expect(monitoringLinkStatusLabel({ monitoring_link_status: 'not_configured' })).toBe('Target missing');
  expect(monitoringLinkStatusLabel({})).toBe('Target missing');
});

// 9. Shows "System not enabled" when target exists but no monitored system
test('monitoringLinkStatusLabel returns "System not enabled" for system_missing status', () => {
  expect(monitoringLinkStatusLabel({ monitoring_link_status: 'system_missing' })).toBe('System not enabled');
});

test('getMonitoringStatus returns "System not enabled" when has_linked_monitored_system is explicitly false', () => {
  const result = getMonitoringStatus({
    monitoring_link_status: 'attached',
    has_linked_monitored_system: false,
    monitoring_target_count: 1,
  });
  expect(result.label).toBe('System not enabled');
});

// 10. Simulator data is not labelled as live_provider
test('assets source does not label simulator data as live_provider', () => {
  expect(managerSrc).not.toContain('live_provider');
  expect(managerSrc).not.toContain("source: 'live'");
  expect(managerSrc).not.toContain('isLive: true');
});

// Regression: empty state shows correct copy
test('empty state shows "No protected assets yet" with correct message', () => {
  expect(managerSrc).toContain('No protected assets yet');
  expect(managerSrc).toContain('Add your first wallet, smart contract, treasury vault, or tokenized RWA to begin monitoring.');
});

// 11. Asset risk is this page's job — the global monitoring diagnostics panel is not
// repeated here. Detailed coverage/provider/worker/telemetry diagnostics live on
// /system-health, and a genuinely degraded runtime is surfaced by the compact global
// health warning in the app shell (not a large per-page panel).
test('assets page does not embed the global runtime diagnostics panel', () => {
  expect(pageSrc).not.toContain('RuntimeSummaryPanel');
  expect(pageSrc).toContain('AssetsManager');
});

// 12. Workspace-level Run assessment is wired from the page into the AI panel.
test('assets manager wires an operational workspace assessment into the AI panel', () => {
  expect(managerSrc).toContain('runWorkspaceAssessment');
  expect(managerSrc).toContain('onRunAssessment={runWorkspaceAssessment}');
  expect(managerSrc).toContain('assessmentRunning={workspaceAssessing}');
  // Duplicate concurrent jobs are tolerated (409 => idempotent), never surfaced as failure.
  expect(managerSrc).toContain('response.status === 409');
});

// 13. Add Asset modal has the production fields + progressive disclosure.
test('Add Asset modal has token metadata, reserve interval, and reserve-backed disclosure', () => {
  expect(managerSrc).toContain('Token contract address');
  expect(managerSrc).toContain('Token decimals');
  expect(managerSrc).toContain('Expected update interval (seconds)');
  expect(managerSrc).toContain('isReserveBackedRwaType');
  // Wallet monitoring type hides token-contract fields.
  expect(managerSrc).toContain('isWalletType');
});

// 13b. "View assets with gaps" applies the CANONICAL monitoring-gap filter
// (monitoring_gap=any) — a SEPARATE dimension from monitoring health — and
// round-trips through the URL so it survives refresh / back-forward. Regression
// guard for the bug where the gap link mapped onto monitoring_health=not_configured
// (which returned nothing for a Critical wallet that truly had a missing-target gap).
test('monitoring-gap link is canonical monitoring_gap=any (never a monitoring_health value)', () => {
  expect(MONITORING_GAP_FILTER).toBe('any');
  const gapQuery = buildAssetsQuery({ ...BASE_FILTERS, monitoring_gap: MONITORING_GAP_FILTER });
  const params = new URLSearchParams(gapQuery);
  expect(params.get('monitoring_gap')).toBe('any');
  // It must NOT hijack monitoring_health — that was the production bug.
  expect(params.has('monitoring_health')).toBe(false);
  expect(params.get('sort')).toBe('risk');
  expect(params.get('dir')).toBe('desc');
  expect(params.get('page')).toBe('1');
  expect(params.get('page_size')).toBe('25');
  // Without the gap filter, monitoring_gap is omitted (never the literal "all").
  const noFilter = new URLSearchParams(buildAssetsQuery(BASE_FILTERS));
  expect(noFilter.has('monitoring_gap')).toBe(false);
  // The panel wires the gap link to this exact canonical filter value, and never
  // again onto monitoring_health.
  expect(managerSrc).toContain('onFilterGaps={() => updateFilter({ monitoring_gap: MONITORING_GAP_FILTER })}');
  expect(managerSrc).not.toContain('onFilterGaps={() => updateFilter({ monitoring_health');
});

// 13b-i. Refresh preserves the gap filter: the URL is parsed back into filter state,
// and a specific gap value survives the query -> URL -> query round-trip.
test('refresh preserves the monitoring_gap filter (URL is the source of truth)', () => {
  expect(managerSrc).toContain("monitoring_gap: params.get('monitoring_gap')");
  const q = buildAssetsQuery({ ...BASE_FILTERS, monitoring_gap: 'no_linked_target' });
  expect(new URLSearchParams(q).get('monitoring_gap')).toBe('no_linked_target');
});

// 13b-ii. Clear filters removes monitoring_gap (and every other param) and restores
// the complete list; the chip also offers a targeted clear of just the gap filter.
test('clear filters removes monitoring_gap and restores the complete list', () => {
  const cleared = new URLSearchParams(buildAssetsQuery(BASE_FILTERS));
  expect(cleared.has('monitoring_gap')).toBe(false);
  expect(managerSrc).toContain('onClick={() => setFilters(DEFAULT_FILTERS)}');
  expect(managerSrc).toContain("updateFilter({ monitoring_gap: 'all' })");
});

// 13b-iii. The filter UI truthfully represents the active gap filter as its own chip
// and never mislabels the gap as a monitoring health. The Monitoring dropdown is
// bound to monitoring_health ONLY, so a gap filter never shows "Not configured".
test('filter UI represents the active monitoring-gap filter as its own chip', () => {
  expect(monitoringGapFilterLabel('any')).toBe('Any');
  expect(monitoringGapFilterLabel('no_linked_target')).toBe('No linked target');
  expect(managerSrc).toContain('Monitoring gap: {monitoringGapFilterLabel(filters.monitoring_gap)}');
  expect(managerSrc).toContain('aria-label="Clear monitoring gap filter"');
  // The Monitoring HEALTH dropdown value is bound to monitoring_health ONLY — the
  // gap filter never drives it (so it can't display a misleading "Not configured").
  expect(managerSrc).toContain('value={filters.monitoring_health} onChange={(e) => updateFilter({ monitoring_health: e.target.value })}');
});

// 13b-iv. LEGACY → CANONICAL gap-filter migration. The production bug: "View assets
// with gaps" set the canonical monitoring_gap=any but LEFT the legacy
// monitoring_health=not_configured in the URL. Because the two are AND-ed server-side
// and the affected wallet is Monitoring Health = Critical (not not_configured), the
// stale health constraint excluded it and the table showed no results.
// reconcileMonitoringGapFilter is the single source of truth for dropping the legacy
// constraint whenever the canonical gap filter is active; it is applied at both
// state-merge points (updateFilter for the click, URL parsing for old bookmarks).

const LEGACY_STATE = { ...BASE_FILTERS, monitoring_health: 'not_configured' };
const EXPECTED_GAP_URL = '/assets?monitoring_gap=any&sort=risk&dir=desc&page=1&page_size=25';

// (1) Clicking "View assets with gaps" removes the legacy health filter. This mirrors
// the exact state merge updateFilter performs for onFilterGaps, starting from a state
// that already carries the legacy monitoring_health=not_configured.
test('clicking the gap link drops the legacy monitoring_health=not_configured constraint', () => {
  const merged = reconcileMonitoringGapFilter({ ...LEGACY_STATE, monitoring_gap: MONITORING_GAP_FILTER, page: 1 });
  expect(merged.monitoring_gap).toBe('any');
  expect(merged.monitoring_health).toBe('all'); // dropped, so the dropdown shows "All Monitoring"
  const params = new URLSearchParams(buildAssetsQuery(merged));
  expect(params.get('monitoring_gap')).toBe('any');
  expect(params.has('monitoring_health')).toBe(false);
  // updateFilter (which onFilterGaps calls) runs the merged state through the reconcile.
  expect(managerSrc).toContain('reconcileMonitoringGapFilter({ ...current, ...patch, page: patch.page ?? 1 })');
});

// (2) Old bookmarked URLs containing BOTH parameters normalize to the canonical gap
// only, and the resulting URL is exactly the expected one.
test('old URL with both filters normalizes to canonical monitoring_gap=any only', () => {
  const parsed = reconcileMonitoringGapFilter({ ...BASE_FILTERS, monitoring_health: 'not_configured', monitoring_gap: 'any' });
  expect(parsed.monitoring_health).toBe('all');
  expect(parsed.monitoring_gap).toBe('any');
  expect(`/assets?${buildAssetsQuery(parsed)}`).toBe(EXPECTED_GAP_URL);
  // The parse-on-mount effect feeds the incoming URL params through the same reconcile.
  expect(managerSrc).toContain('setFilters((current) => reconcileMonitoringGapFilter({');
});

// (3) A Critical asset with a monitoring gap remains visible: after reconcile the query
// sent to the backend carries NO monitoring_health constraint, so the server-side gap
// filter (a separate dimension) returns the Critical wallet instead of ANDing it away.
// (The backend counterpart is test_gap_filter_any_returns_critical_asset_with_missing_target.)
test('a Critical asset with a gap is not excluded — no stale health constraint is sent', () => {
  const reconciled = reconcileMonitoringGapFilter({ ...BASE_FILTERS, monitoring_health: 'not_configured', monitoring_gap: 'any' });
  const params = new URLSearchParams(buildAssetsQuery(reconciled));
  expect(params.has('monitoring_health')).toBe(false);
  expect(params.get('monitoring_gap')).toBe('any');
  // A legitimate NON-not_configured health filter is preserved alongside the gap — only
  // the legacy not_configured alias is dropped, so a real Critical health filter still works.
  const withCritical = reconcileMonitoringGapFilter({ ...BASE_FILTERS, monitoring_health: 'critical', monitoring_gap: 'any' });
  expect(withCritical.monitoring_health).toBe('critical');
});

// (4) Refresh preserves ONLY monitoring_gap=any: round-trip state → query → URL → parse
// yields the gap alone; the legacy not_configured never survives the refresh.
test('refresh preserves only monitoring_gap=any (legacy not_configured does not survive)', () => {
  const canonical = reconcileMonitoringGapFilter({ ...LEGACY_STATE, monitoring_gap: 'any' });
  const reparsed = new URLSearchParams(buildAssetsQuery(canonical));
  expect(reparsed.get('monitoring_gap')).toBe('any');
  expect(reparsed.has('monitoring_health')).toBe(false);
  expect(reparsed.get('sort')).toBe('risk');
  expect(reparsed.get('dir')).toBe('desc');
});

// (5) Back/Forward cannot restore the legacy constraint: normalization rewrites the URL
// with history.replaceState (in place) and never history.pushState, so the not_configured
// entry is replaced — not stacked — and no history entry retains it to navigate back to.
test('URL normalization uses replaceState (not pushState) so back/forward cannot restore the legacy constraint', () => {
  expect(managerSrc).toContain('window.history.replaceState');
  // No pushState CALL anywhere — a mention in a comment is fine, an actual
  // history.pushState(...) that would stack the legacy URL as a restorable entry is not.
  expect(managerSrc).not.toContain('.pushState(');
  // The reconcile that strips the legacy constraint runs on the parsed URL, so the state
  // (and therefore every replaced URL) never carries not_configured alongside a gap.
  expect(managerSrc).toContain('reconcileMonitoringGapFilter');
});

// (6) Clear filters removes BOTH parameters (full reset to defaults, gap='all'/health='all').
test('clear filters removes both monitoring_gap and the legacy monitoring_health', () => {
  const cleared = new URLSearchParams(buildAssetsQuery(BASE_FILTERS));
  expect(cleared.has('monitoring_gap')).toBe(false);
  expect(cleared.has('monitoring_health')).toBe(false);
  expect(managerSrc).toContain('onClick={() => setFilters(DEFAULT_FILTERS)}');
});

// 13c. Asset details drawer explains the score: per-dimension weight + weighted
// contribution, applicable/not-applicable rationale, and status-vs-condition copy.
test('asset details drawer explains dimensions (weight, contribution) and status meaning', () => {
  expect(managerSrc).toContain('weighted contribution');
  expect(managerSrc).toContain('effective_weight');
  expect(managerSrc).toContain('DIMENSION_LABELS');
  expect(managerSrc).toContain('DIMENSION_NA_REASON');
  // Not-applicable dimensions (reserve/oracle for a wallet) are excluded, never 0.
  expect(managerSrc).toContain('Not applicable');
  expect(managerSrc).toContain("does not apply");
  // Status vs condition is spelled out and tooltipped.
  expect(managerSrc).toContain('assessmentStatusTooltip');
  expect(managerSrc).toContain('A completed assessment stays Complete even when its risk is high.');
});

// 14. Reserve semantics: the registry never hardcodes a "missing reserve evidence"
// message for a non-reserve asset, and uses the not_applicable path.
test('drawer treats non-reserve assets as not applicable, not missing evidence', () => {
  expect(managerSrc).toContain('reserveApplies');
  expect(managerSrc).toContain('Reserve backing does not apply to this asset type');
  // Data-provenance labels exist for the details drawer.
  expect(managerSrc).toContain('DataLabel');
  expect(managerSrc).toContain("'not_applicable'");
});
