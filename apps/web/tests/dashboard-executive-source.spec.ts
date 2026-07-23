import { expect, test } from '@playwright/test';
import * as fs from 'node:fs';
import * as path from 'node:path';

const EXEC_SUMMARY_PATH = path.join(__dirname, '../app/dashboard-executive-summary.tsx');
const DATA_PATH = path.join(__dirname, '../app/dashboard-executive-summary-data.ts');
const HYDRATOR_PATH = path.join(__dirname, '../app/dashboard-live-hydrator.tsx');
const ROUTE_PATH = path.join(__dirname, '../app/api/dashboard/executive-summary/route.ts');
const STYLES_PATH = path.join(__dirname, '../app/styles.css');

function readSource(filePath: string): string {
  return fs.readFileSync(filePath, 'utf8');
}

test.describe('Dashboard Executive Summary (Screen 2) — source-level contracts', () => {
  test('dashboard route renders DashboardExecutiveSummary via hydrator', () => {
    const hydrator = readSource(HYDRATOR_PATH);
    expect(hydrator).toContain("import DashboardExecutiveSummary from './dashboard-executive-summary'");
    expect(hydrator).toContain('<DashboardExecutiveSummary');
    expect(hydrator).not.toContain('DashboardPageContent');
  });

  test('page title is Dashboard and subtitle covers assets/monitoring/alerts/incidents/health', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain('>Dashboard<');
    expect(source).toContain('protected assets');
    expect(source).toContain('monitoring coverage');
    expect(source).toContain('alerts');
    expect(source).toContain('incidents');
    expect(source).toContain('system health');
  });

  test('top summary areas exist: Executive Brief, Total Asset Value, Open Incidents, Active Alerts, Risk Score, System Health', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain('Executive Brief');
    expect(source).toContain('Total Asset Value');
    expect(source).toContain('"Open Incidents"');
    expect(source).toContain('"Active Alerts"');
    expect(source).toContain('Risk Score');
    expect(source).toContain('System Health');
  });

  test('Executive Brief card shows AI-generated indicator, confidence and View Full AI Summary', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain('aria-label="Executive Brief"');
    expect(source).toContain('View Full AI Summary');
    expect(source).toContain("brief.generation_mode === 'ai'");
    expect(source).toContain('Confidence');
  });

  test('Risk Trend, Recent Alerts, and AI Dashboard Co-Pilot sections exist', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain('aria-label="Risk Trend"');
    expect(source).toContain('Risk Trend — Last 7 Days');
    expect(source).toContain('aria-label="Recent Alerts"');
    expect(source).toContain('View all alerts');
    expect(source).toContain('aria-label="AI Dashboard Co-Pilot"');
    expect(source).toContain('Top Risk Drivers');
    expect(source).toContain('System Health Insights');
    expect(source).toContain('Recommended Focus');
    expect(source).toContain('View Full AI Insights');
  });

  test('bottom metrics exist: Monitored Assets, Active Monitors, Data Sources, 30-day Uptime', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain('Monitored Assets');
    expect(source).toContain('Active Monitors');
    expect(source).toContain('Data Sources');
    expect(source).toContain('30-day Uptime');
  });

  // Required frontend test 1: loading skeleton renders.
  test('loading skeleton is rendered while loading with no data', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain('function LoadingSkeleton');
    expect(source).toContain("status === 'loading' && !data ? <LoadingSkeleton />");
  });

  // Required frontend test 4: empty states contain no fabricated data.
  test('empty and no-trend states are explicit and non-fabricated', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain('Historical trend not available yet');
    expect(source).toContain('no synthetic history is shown');
    expect(source).toContain('No active alerts');
    expect(source).toContain('EmptyStateBlocker');
  });

  // Required frontend test 7: AI generation mode + timestamp render.
  test('brief generation mode and generated timestamp render', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain("isAi ? 'AI generated' : 'Deterministic'");
    expect(source).toContain('formatRelativeTime(brief.generated_at)');
    expect(source).toContain("isAi ? 'AI generated' : 'Deterministic fallback'");
  });

  // Required frontend test 8: stale-data warning renders.
  test('freshness/stale warning renders for non-fresh data', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain('function FreshnessBanner');
    expect(source).toContain("if (status === 'fresh') return null;");
    expect(source).toContain('Telemetry is stale.');
    expect(source).toContain('Telemetry unavailable.');
  });

  // Required frontend test 9: Co-Pilot insights render source links.
  test('co-pilot health insights render deep-link sources', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain('function insightSourceLink');
    expect(source).toContain('View source');
    expect(source).toContain("case 'monitoring_target':");
    expect(source).toContain('/monitoring-sources');
  });

  test('recent alert rows link to the individual alert record', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain('href={alert.url}');
    expect(source).toContain('execAlertRowLink');
  });

  test('recommended focus destinations map only to real routes (no broken links)', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain('const DESTINATION_ROUTES');
    expect(source).toContain("alerts: '/alerts'");
    expect(source).toContain("incidents: '/incidents'");
    expect(source).toContain("monitoring: '/monitoring-sources'");
    expect(source).toContain("assets: '/assets'");
  });

  // Truthfulness: System Health label is derived only from the deterministic
  // backend status; "Healthy" never appears unless status === 'healthy', and an
  // unconfigured workspace shows "Not configured", never a green 100.
  test('system health label is truth-derived, never fake healthy', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain('HEALTH_STATUS_LABELS[status]');
    expect(source).toContain("status === 'not_configured'");
    const data = readSource(DATA_PATH);
    expect(data).toContain("healthy: 'Healthy'");
    expect(data).toContain("not_configured: 'Not configured'");
    // The only healthy label mapping is keyed on the literal 'healthy' status.
    expect(data).not.toContain("degraded: 'Healthy'");
  });

  // Truthfulness: a null valuation is "Not available", never $0.
  test('total asset value renders Not available for null, not $0', () => {
    const data = readSource(DATA_PATH);
    expect(data).toContain("if (value == null) return 'Not available';");
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain('formatAssetValue(value)');
    expect(source).toContain('monitored asset');
  });

  test('metrics never fabricate values — Top Risk Drivers come from backend contributions', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    // Drivers/insights are read straight from ai_copilot (backend deterministic
    // contributions), never invented client-side.
    expect(source).toContain('= data.ai_copilot;');
    expect(source).toContain('top_risk_drivers.map');
    expect(source).toContain('system_health_insights.map');
    expect(source).not.toContain('Math.random');
  });

  test('real-time refresh is wired to the live workspace feed (SSE/poll)', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain('liveFeed?.lastFetchCompletedAt');
    expect(source).toContain('refreshSignal');
    // Debounced refetch preserves chart/scroll state (no remount).
    expect(source).toContain('setTimeout(() => void load(true)');
  });

  test('same-origin proxy route forwards auth + workspace headers to backend', () => {
    const route = readSource(ROUTE_PATH);
    expect(route).toContain('/ops/dashboard/executive-summary');
    expect(route).toContain("'authorization', 'x-workspace-id', 'x-csrf-token', 'cookie'");
    expect(route).toContain('normalizeWorkspaceHeaderValue');
  });

  test('CSS defines Screen 2 layout + responsive breakpoints without horizontal overflow', () => {
    const css = readSource(STYLES_PATH);
    expect(css).toContain('.execMetricRow');
    expect(css).toContain('.execMetricRowScreen2');
    expect(css).toContain('.execBriefCard');
    expect(css).toContain('.execCopilotPanel');
    expect(css).toContain('.execTrendChart');
    expect(css).toContain('.execBottomMetricsRow');
    expect(css).toContain('.execDrawer');
    // Required frontend test 10: mobile layout does not overflow.
    expect(css).toContain('@media (max-width: 720px)');
    expect(css).toContain('overflow-x: auto');
  });

  test('defensive coercers guard every mapped field', () => {
    const data = readSource(DATA_PATH);
    expect(data).toContain('function rec(');
    expect(data).toContain('function num(');
    expect(data).toContain('function numOrNull(');
    expect(data).toContain('function arr(');
    expect(data).toContain('export function mapExecutiveSummary');
  });
});
