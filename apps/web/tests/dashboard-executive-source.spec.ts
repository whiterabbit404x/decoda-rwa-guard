import { expect, test } from '@playwright/test';
import * as fs from 'node:fs';
import * as path from 'node:path';

const EXEC_SUMMARY_PATH = path.join(__dirname, '../app/dashboard-executive-summary.tsx');
const DATA_PATH = path.join(__dirname, '../app/dashboard-executive-summary-data.ts');
const HYDRATOR_PATH = path.join(__dirname, '../app/dashboard-live-hydrator.tsx');
const ROUTE_PATH = path.join(__dirname, '../app/api/dashboard/executive-summary/route.ts');
const STYLES_PATH = path.join(__dirname, '../app/styles.css');
const PAGE_HEADER_PATH = path.join(__dirname, '../app/components/page-header.tsx');

function readSource(filePath: string): string {
  return fs.readFileSync(filePath, 'utf8');
}

/**
 * The Dashboard follows the shared page hierarchy (Decoda Assets' idiom): a page
 * header with ONE primary action, the monitoring status card, a row of four key
 * figures, then Recent Alerts beside the Executive Brief. The detail that used to
 * sit on the first screen -- risk drivers, health insights, coverage counts,
 * uptime, system health score -- lives in the full-summary drawer. These
 * contracts pin both the hierarchy and every truthfulness rule it carries.
 */
test.describe('Dashboard (Screen 2) — source-level contracts', () => {
  test('dashboard route renders DashboardExecutiveSummary via hydrator', () => {
    const hydrator = readSource(HYDRATOR_PATH);
    expect(hydrator).toContain("import DashboardExecutiveSummary from './dashboard-executive-summary'");
    expect(hydrator).toContain('<DashboardExecutiveSummary');
    expect(hydrator).not.toContain('DashboardPageContent');
  });

  test('page header: title, one line of purpose, Refresh and one primary action', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain('<PageHeader');
    expect(source).toContain('title="Dashboard"');
    expect(source).toContain('Protected assets, monitoring coverage, alerts, incidents and system health');
    expect(source).toContain('execRefreshBtn');
    // The primary action is the backend's own top recommended focus -- never a
    // frontend guess -- and it resolves to a real route.
    expect(source).toContain('data?.executive_brief.recommended_focus[0] ?? data?.ai_copilot.recommended_focus[0] ?? null');
    expect(source).toContain('href={destinationRoute(primaryFocus.destination)}');
    expect(source).toContain('data-testid="dashboard-primary-action"');
    expect(readSource(PAGE_HEADER_PATH)).toContain('<h1 className="pageHeadTitle">{title}</h1>');
  });

  test('first screen holds the status card, four key figures, recent alerts and the brief', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain('<MonitoringStatusCard data={data} streamStatus={streamStatus} />');
    expect(source).toContain('<StatRow label="Key figures" items={keyFigures(data)} />');
    for (const label of ["label: 'Open Incidents'", "label: 'Active Alerts'", "label: 'Monitored Assets'", "label: 'Risk Score'"]) {
      expect(source).toContain(label);
    }
    expect(source).toContain('<RecentAlertsCard alerts={data.recent_alerts} />');
    expect(source).toContain('<ExecutiveBriefCard data={data} onOpen={() => setBriefOpen(true)} />');
    // The removed first-screen panels stay removed.
    for (const removed of ['function SecurityPostureStrip', 'function BottomMetricsRow', 'function CoPilotPanel', 'function MonitoringStatusIndicators', 'execMetricCluster']) {
      expect(source).not.toContain(removed);
    }
  });

  test('figures link to the list they count; a count is never painted healthy', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain("href: '/incidents'");
    expect(source).toContain("href: '/alerts'");
    expect(source).toContain("href: '/assets'");
    // Only "needs someone" tones; no success tone on a figure.
    expect(source).toContain("tone: m.open_incident_count > 0 ? 'warning' : undefined");
    expect(source).toContain("tone: m.active_alert_count > 0 ? 'danger' : undefined");
    expect(source).not.toMatch(/tone: '?success/);
    const header = readSource(PAGE_HEADER_PATH);
    expect(header).toContain("export type StatTone = 'danger' | 'warning' | 'info' | 'neutral';");
    expect(header).toContain("data-tone={item.tone ?? 'neutral'}");
  });

  test('total asset value renders Not available for null, not $0', () => {
    const data = readSource(DATA_PATH);
    expect(data).toContain("if (value == null) return 'Not available';");
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain('formatAssetValue(value)');
    expect(source).toContain('formatAssetValue(data.metrics.total_asset_value_usd)');
  });

  // Monitoring vs telemetry vs heartbeat vs ingestion vs the browser's connection:
  // separate facts, read straight from the backend, never collapsed into one.
  test('status card keeps every evidence axis separate and gates Live on backend state', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain('MONITORING_STATUS_LABELS[monitoring.state]');
    expect(source).toContain('TELEMETRY_FRESHNESS_LABELS[data.data_freshness.status]');
    expect(source).toContain("monitoring.workers_fresh ? 'Heartbeat current' : 'Heartbeat not confirmed'");
    expect(source).toContain("monitoring.ingestion_healthy ? 'Ingestion healthy' : 'Ingestion not confirmed'");
    expect(source).toContain('Live updates: {CONNECTION_STATUS_LABELS[connection]}');
    expect(source).toContain('connectionStatusFromStream(streamStatus)');
    expect(source).toContain('HEALTH_STATUS_LABELS[health]');
    // The SSE transport never drives a monitoring label.
    expect(source).not.toContain("streamStatus === 'live' ? 'Live' : 'Reconnecting'");
    expect(source).not.toContain("streamStatus === 'live' ? 'Live monitoring'");
    // Not live: the way to fix it is offered; live: no setup call-to-action.
    expect(source).toContain('Set up monitoring');
    expect(source).toContain("{live ? 'View system health' : 'Diagnose'}");
    // Period movement only when the backend reported a period.
    expect(source).toContain('const activityReported = activity.period_start != null;');
  });

  test('the status rail fails closed: offline is red, nothing unknown reads fine', () => {
    const css = readSource(STYLES_PATH);
    expect(css).toContain(".dashStatus[data-state='offline'] { border-left-color: var(--danger-fg); }");
    expect(css).toContain(".dashStatus[data-state='live'] { border-left-color: var(--success-fg); }");
    expect(css).toContain(".dashFact[data-ok='false']");
    const data = readSource(DATA_PATH);
    // Anything other than an explicit live/degraded is offline.
    expect(data).toContain("return v === 'live' || v === 'degraded' ? v : 'offline';");
  });

  test('freshness is stated in words for fresh, stale and missing telemetry', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain('function freshnessSentence');
    expect(source).toContain('Telemetry unavailable. No verified telemetry has been received from any monitored system.');
    expect(source).toContain('Telemetry is stale. Last verified event');
  });

  test('zero-alert wording is coverage-aware and drops "All clear"', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).not.toContain('All clear');
    expect(source).toContain('function activeAlertsMeta');
    expect(source).toContain('No active alerts detected');
    expect(source).toContain("'No active alerts'");
    expect(source).toContain('function CoverageCaveatBanner');
    expect(source).toContain('zero alerts does not confirm absence of threats');
    expect(source).toContain('<CoverageCaveatBanner data={data} />');
  });

  test('executive brief: evidence freshness, deterministic confidence, generation mode', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain('aria-label="Executive Brief"');
    expect(source).toContain('View Full AI Summary');
    expect(source).toContain("brief.generation_mode === 'ai'");
    expect(source).toContain("isAi ? 'AI generated' : 'Deterministic'");
    expect(source).toContain("isAi ? 'AI generated' : 'Deterministic fallback'");
    expect(source).toContain('function EvidenceFreshnessLine');
    expect(source).toContain('Data current through');
    expect(source).toContain('formatAgeSeconds(evidence.telemetry_age_seconds)');
    expect(source).toContain('DATA_CONFIDENCE_LABELS[evidence.data_confidence]');
    expect(source).toContain('formatRelativeTime(evidence.generated_at)');
    expect(source).not.toContain("brief.confidence >= 0.7 ? 'High'");
    // Recommended focus sits in the brief, each item a real route.
    expect(source).toContain('Recommended Focus');
    expect(source).toContain('href={destinationRoute(item.destination)}');
  });

  test('recommended focus destinations map only to real routes (no broken links)', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain('const DESTINATION_ROUTES');
    expect(source).toContain("alerts: '/alerts'");
    expect(source).toContain("incidents: '/incidents'");
    expect(source).toContain("monitoring: '/monitoring-sources'");
    expect(source).toContain("assets: '/assets'");
  });

  test('recent alert rows link to the individual alert record', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain('aria-label="Recent Alerts"');
    expect(source).toContain('View all alerts');
    expect(source).toContain('href={alert.url}');
    expect(source).toContain('execAlertRowLink');
    expect(source).toContain('title="No active alerts"');
  });

  test('the full summary drawer carries the co-pilot detail and coverage', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain('aria-label="AI Dashboard Co-Pilot"');
    expect(source).toContain('Top Risk Drivers');
    expect(source).toContain('System Health Insights');
    expect(source).toContain('top_risk_drivers.map');
    expect(source).toContain('system_health_insights.map');
    for (const label of ['Monitored Assets', 'Active Monitors', 'Data Sources', 'Total Asset Value', '30-day Uptime', 'System Health']) {
      expect(source).toContain(`<dt>${label}</dt>`);
    }
    // Insight sources deep-link to the record behind them.
    expect(source).toContain('function insightSourceLink');
    expect(source).toContain('View source');
    expect(source).toContain("case 'monitoring_target':");
  });

  test('system health label is truth-derived, never fake healthy', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain("data.metrics.system_health_status === 'not_configured' ? '—'");
    const data = readSource(DATA_PATH);
    expect(data).toContain("healthy: 'Healthy'");
    expect(data).toContain("not_configured: 'Not configured'");
    expect(data).not.toContain("degraded: 'Healthy'");
  });

  test('risk trend draws only from real snapshots and is omitted until it exists', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain('{data.trend_available ? (');
    expect(source).toContain('const points = trend.filter((p) => p.captured_at);');
    expect(source).toContain('const hasEnough = available && points.length >= 2;');
    expect(source).toContain('{hasEnough && partial ? <StatusPill label="Partial data"');
    // Its absence is still stated, in the drawer, without inventing history.
    expect(source).toContain('Historical trend not available yet');
    expect(source).toContain('no synthetic history is shown');
    const data = readSource(DATA_PATH);
    expect(data).toContain('trend_partial: boolean');
    expect(data).toContain('captured_date: string | null');
  });

  test('loading and error states', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain('function LoadingSkeleton');
    expect(source).toContain("status === 'loading' && !data ? <LoadingSkeleton />");
    expect(source).toContain('Dashboard temporarily unavailable');
    expect(source).toContain('Showing last known data.');
  });

  test('metrics never fabricate values', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).not.toContain('Math.random');
    expect(source).toContain('= data.ai_copilot;');
  });

  test('real-time refresh is wired to the live workspace feed (SSE/poll)', () => {
    const source = readSource(EXEC_SUMMARY_PATH);
    expect(source).toContain('liveFeed?.lastFetchCompletedAt');
    expect(source).toContain('refreshSignal');
    expect(source).toContain('setTimeout(() => void load(true)');
  });

  test('same-origin proxy route forwards auth + workspace headers to backend', () => {
    const route = readSource(ROUTE_PATH);
    expect(route).toContain('/ops/dashboard/executive-summary');
    expect(route).toContain("'authorization', 'x-workspace-id', 'x-csrf-token', 'cookie'");
    expect(route).toContain('normalizeWorkspaceHeaderValue');
  });

  test('defensive coercers guard every mapped field', () => {
    const data = readSource(DATA_PATH);
    expect(data).toContain('function rec(');
    expect(data).toContain('function num(');
    expect(data).toContain('function numOrNull(');
    expect(data).toContain('function arr(');
    expect(data).toContain('export function mapExecutiveSummary');
  });

  test('CSS: one-column stack below 1100px, figures two-up on phones, drawer kept', () => {
    const css = readSource(STYLES_PATH);
    expect(css).toContain('.dashGrid');
    expect(css).toMatch(/@media \(max-width: 1100px\) \{\s*\.dashGrid \{ grid-template-columns: minmax\(0, 1fr\); \}/);
    expect(css).toMatch(/@media \(max-width: 640px\) \{\s*\.statRow \{ grid-template-columns: repeat\(2, minmax\(0, 1fr\)\); \}/);
    expect(css).toContain('.execDrawer');
    expect(css).toContain('.execCoverageCaveat');
    expect(css).toContain('.execBriefEvidence');
  });
});
