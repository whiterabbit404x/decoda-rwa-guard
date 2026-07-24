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

import { monitoringLinkStatusLabel, getMonitoringStatus, buildAssetsQuery, MONITORING_GAP_FILTER } from '../app/assets-manager';

const BASE_FILTERS = {
  search: '', asset_type: 'all', network: 'all', risk_level: 'all', monitoring_health: 'all',
  custodian: 'all', sort: 'risk', dir: 'desc', page: 1,
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

// 11. The tall global monitoring panel is collapsed into a compact strip on Screen 3.
test('assets page uses the compact runtime status strip (not the full-height panel)', () => {
  expect(pageSrc).toContain('<RuntimeSummaryPanel compact />');
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

// 13b. "View assets with gaps" applies the canonical monitoring-gap filter and
// round-trips through the URL query (so it survives refresh / back-forward).
test('monitoring-gap filter builds a canonical not_configured query preserved in the URL', () => {
  expect(MONITORING_GAP_FILTER).toBe('not_configured');
  const gapQuery = buildAssetsQuery({ ...BASE_FILTERS, monitoring_health: MONITORING_GAP_FILTER });
  const params = new URLSearchParams(gapQuery);
  expect(params.get('monitoring_health')).toBe('not_configured');
  expect(params.get('page')).toBe('1');
  expect(params.get('page_size')).toBe('25');
  // Without the gap filter, monitoring_health is omitted (never the literal "all").
  const noFilter = new URLSearchParams(buildAssetsQuery(BASE_FILTERS));
  expect(noFilter.has('monitoring_health')).toBe(false);
  // The panel wires the gap link to this exact canonical filter value.
  expect(managerSrc).toContain('onFilterGaps={() => updateFilter({ monitoring_health: MONITORING_GAP_FILTER })}');
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
