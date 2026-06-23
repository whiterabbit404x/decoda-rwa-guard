import { expect, test } from '@playwright/test';
import { readFileSync } from 'fs';
import { join } from 'path';

const baseDir = join(process.cwd(), 'app/(product)/system-health');
const componentsDir = join(baseDir, '_components');

const pageSource = readFileSync(join(baseDir, 'page.tsx'), 'utf8');
const heroSource = readFileSync(join(componentsDir, 'system-health-hero.tsx'), 'utf8');
const summaryCardsSource = readFileSync(join(componentsDir, 'health-summary-cards.tsx'), 'utf8');
const opsOverviewSource = readFileSync(join(componentsDir, 'operational-overview.tsx'), 'utf8');
const chainMonitoringSource = readFileSync(
  join(componentsDir, 'live-chain-monitoring-panel.tsx'),
  'utf8',
);
const timelineSource = readFileSync(join(componentsDir, 'health-timeline.tsx'), 'utf8');
const providerSource = readFileSync(join(componentsDir, 'provider-health-cards.tsx'), 'utf8');
const reliabilitySource = readFileSync(join(componentsDir, 'reliability-snapshot.tsx'), 'utf8');
const statusOverviewSource = readFileSync(join(componentsDir, 'status-overview-panel.tsx'), 'utf8');
const typesSource = readFileSync(join(componentsDir, 'types.ts'), 'utf8');
const helpersSource = readFileSync(join(componentsDir, 'helpers.ts'), 'utf8');

const resilienceSource = readFileSync(
  join(process.cwd(), 'app/(product)/resilience/page.tsx'),
  'utf8',
);
const navSource = readFileSync(join(process.cwd(), 'app/product-nav.ts'), 'utf8');

const allComponentSources = [
  pageSource,
  heroSource,
  summaryCardsSource,
  opsOverviewSource,
  chainMonitoringSource,
  timelineSource,
  providerSource,
  reliabilitySource,
  statusOverviewSource,
  typesSource,
  helpersSource,
].join('\n');

// ── Page shell ──────────────────────────────────────────────────────────────

test('/system-health renders the System Health screen', () => {
  expect(pageSource).toContain('<h1>System Health</h1>');
  expect(pageSource).toContain(
    'Live operational status for Decoda RWA Guard infrastructure, monitoring workers,',
  );
  expect(pageSource).toContain('Refresh Health');
});

test('page fetches both dashboard data and system health endpoint', () => {
  expect(pageSource).toContain('fetchDashboardPageData');
  expect(pageSource).toContain('fetchSystemHealth');
  expect(pageSource).toContain('/ops/system-health');
});

// ── Hero ────────────────────────────────────────────────────────────────────

test('hero renders overall status and primary action', () => {
  expect(allComponentSources).toContain('overallStatus');
  expect(allComponentSources).toContain('summaryText');
  expect(allComponentSources).toContain('All Systems Operational');
  expect(allComponentSources).toContain('Action Required');
  expect(allComponentSources).toContain('Degraded');
  expect(allComponentSources).toContain('Unavailable');
  expect(allComponentSources).toContain('primaryAction');
  expect(heroSource).toContain('Action required');
});

test('hero shows environment badge, last checked, and git commit', () => {
  expect(heroSource).toContain('environment');
  expect(heroSource).toContain('generatedAt');
  expect(heroSource).toContain('gitCommit');
  expect(heroSource).toContain('Last checked');
});

// ── Summary cards ───────────────────────────────────────────────────────────

test('status cards render the 8 infrastructure components', () => {
  const expectedComponents = [
    'api',
    'database',
    'redis',
    'worker',
    'base_rpc',
    'telemetry',
    'detection',
    'alert_delivery',
  ];
  expectedComponents.forEach((key) => {
    expect(allComponentSources).toContain(`'${key}'`);
  });
  expect(allComponentSources).toContain('COMPONENT_META');
});

test('component meta labels match SaaS naming', () => {
  [
    'API',
    'Database',
    'Redis',
    'Worker',
    'Base RPC',
    'Live Polling',
    'Telemetry Ingestion',
    'Detection',
    'Alert Delivery',
  ].forEach((label) => {
    expect(allComponentSources).toContain(label);
  });
});

// ── Operational Overview ────────────────────────────────────────────────────

test('Operational Overview table has required columns', () => {
  ['Component', 'Status', 'Signal', 'Last Event', 'Age', 'Action'].forEach((column) => {
    expect(opsOverviewSource).toContain(column);
  });
  expect(opsOverviewSource).toContain('Operational Overview');
});

test('"what it checks" appears as sub-label under component name', () => {
  expect(opsOverviewSource).toContain('shOpsWhat');
  expect(opsOverviewSource).toContain('meta.what');
});

// ── Status Overview ─────────────────────────────────────────────────────────

test('Status Overview shows canonical truth fields', () => {
  expect(statusOverviewSource).toContain('Status Overview');
  [
    'Overall status',
    'Monitoring status',
    'Freshness status',
    'Confidence status',
    'Last heartbeat',
    'Last poll',
    'Last telemetry',
    'Last detection',
  ].forEach((label) => {
    expect(statusOverviewSource).toContain(label);
  });
});

// ── Live Chain Monitoring ───────────────────────────────────────────────────

test('Live Chain Monitoring section exists with diagnosis card', () => {
  expect(chainMonitoringSource).toContain('Live Chain Monitoring');
  expect(chainMonitoringSource).toContain('Diagnosis');
  expect(chainMonitoringSource).toContain('chainMonitoring.diagnosis');
  expect(chainMonitoringSource).toContain('Expected chain ID');
  expect(chainMonitoringSource).toContain('RPC configured');
  expect(chainMonitoringSource).toContain('Telemetry 1h / 24h');
  expect(chainMonitoringSource).toContain('Detections 1h / 24h');
});

test('Base RPC failing shows action path', () => {
  expect(opsOverviewSource).toContain('comp?.action');
  expect(opsOverviewSource).toContain('shOpsAction');
});

test('telemetry stale renders degraded state via diagnosisVariant', () => {
  expect(helpersSource).toContain('diagnosisVariant');
  expect(helpersSource).toContain("'degraded'");
  expect(chainMonitoringSource).toContain('diagnosisVariant');
});

// ── Health timeline ─────────────────────────────────────────────────────────

test('empty health events show "No recent health events."', () => {
  expect(timelineSource).toContain('No recent health events.');
  expect(timelineSource).toContain('shEmptyState');
  expect(timelineSource).toContain('shEmptyText');
});

test('health timeline and provider sections exist', () => {
  expect(timelineSource).toContain('Incident &amp; Health Timeline');
  expect(providerSource).toContain('Provider Health');
  expect(providerSource).toContain('External dependencies');
});

// ── Reliability ─────────────────────────────────────────────────────────────

test('reliability snapshot shows metric not implemented for unimplemented metrics', () => {
  expect(reliabilitySource).toContain('Reliability &amp; Coverage');
  expect(reliabilitySource).toContain('Active Monitoring Targets');
  expect(reliabilitySource).toContain('RPC Success Rate');
  expect(reliabilitySource).toContain('Metric not implemented');
});

// ── Truth guards ────────────────────────────────────────────────────────────

test('truth guards prevent false healthy status', () => {
  expect(pageSource).toContain('reportingSystems > 0');
  expect(pageSource).toContain('hasHeartbeat');
  expect(pageSource).toContain('hasTelemetry');
  expect(pageSource).toContain('contradictionFlags.length === 0');
});

test('runtime contradiction flags are shown', () => {
  expect(pageSource).toContain('contradictionFlags');
  expect(heroSource).toContain('contradictionFlags');
  expect(heroSource).toContain('Runtime contradictions detected');
});

test('no data shown as healthy — truth-closed status derivation', () => {
  expect(pageSource).toContain('isOperational');
  expect(pageSource).toContain('isOffline');
  expect(pageSource).toContain('!summaryMissing');
  expect(pageSource).toContain('summaryMissing');
  expect(pageSource).toContain('noSystemHealthData');
});

test('no fake healthy text when degraded or failing — statusLabel driven by live status', () => {
  expect(helpersSource).toContain('statusLabel');
  expect(allComponentSources).toContain('statusLabel(status)');
  expect(allComponentSources).toContain("statusLabel(provider.status)");
});

test('page handles missing data without showing everything as Unavailable', () => {
  expect(pageSource).toContain('noSystemHealthData');
  expect(summaryCardsSource).toContain('comp?.message');
  expect(opsOverviewSource).toContain('comp?.message');
  expect(summaryCardsSource).toContain('noSystemHealthData ?');
});

// ── Secrets / security ──────────────────────────────────────────────────────

test('no secrets or RPC URLs are rendered in any source file', () => {
  expect(allComponentSources).not.toContain('DATABASE_URL');
  expect(allComponentSources).not.toContain('REDIS_URL');
  expect(allComponentSources).not.toContain('EVM_RPC_URL');
  expect(allComponentSources).not.toContain('API_KEY');
  expect(allComponentSources).not.toContain('password');
  expect(allComponentSources).not.toContain('secret');
});

// ── Navigation backward compat ──────────────────────────────────────────────

test('nav label is System Health and /resilience remains backward compatible', () => {
  expect(navSource).toContain("label: 'System Health'");
  expect(navSource).toContain("href: '/system-health'");
  expect(resilienceSource).toContain("redirect('/system-health')");
});
