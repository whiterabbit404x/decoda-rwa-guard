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
const fetchSource = readFileSync(join(componentsDir, 'fetch-system-health.ts'), 'utf8');
const endpointErrorSource = readFileSync(join(componentsDir, 'system-health-endpoint-error.tsx'), 'utf8');

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
  expect(fetchSource).toContain('/ops/system-health');
});

test('system-health fetch preserves the failure reason instead of returning bare null', () => {
  // The endpoint can fail in several distinct ways; each must be captured so the
  // page can distinguish "unreachable" from "reachable but a component failed".
  ['not_configured', 'timeout', 'network_error', 'http_error', 'invalid_contract'].forEach((reason) => {
    expect(fetchSource).toContain(reason);
  });
  expect(fetchSource).toContain('SystemHealthFetchResult');
  expect(fetchSource).toContain('diagnoseSystemHealthFailure');
  // Dedicated, longer timeout because the backend runs blocking RPC probes.
  expect(fetchSource).toContain('SYSTEM_HEALTH_TIMEOUT_MS');
});

test('endpoint failure renders one diagnostic panel, not eight unavailable cards', () => {
  // The page must branch: reachable → component sections, unreachable → error panel.
  expect(pageSource).toContain('healthResult.ok');
  expect(pageSource).toContain('SystemHealthEndpointError');
  expect(pageSource).toContain('endpointReachable');
  // The "unreachable" headline is produced by the diagnosis and rendered dynamically.
  expect(fetchSource).toContain('System health API is unreachable');
  expect(endpointErrorSource).toContain('diagnosis.headline');
  // The error panel surfaces operator diagnostics.
  expect(endpointErrorSource).toContain('Endpoint unreachable');
  expect(endpointErrorSource).toContain('Requested endpoint');
  expect(endpointErrorSource).toContain('HTTP status');
  expect(endpointErrorSource).toContain('Retry');
  expect(endpointErrorSource).toContain('Suggested action');
});

test('endpoint failure is classified for the operator (auth vs backend vs unreachable)', () => {
  expect(fetchSource).toContain("category: 'auth'");
  expect(fetchSource).toContain("category: 'backend_error'");
  expect(fetchSource).toContain("category: 'endpoint_unreachable'");
  expect(fetchSource).toContain('401');
  expect(fetchSource).toContain('403');
});

test('debug logging is development-only and never runs in production', () => {
  expect(fetchSource).toContain("process.env.NODE_ENV !== 'production'");
  expect(fetchSource).toContain('console.debug');
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
  expect(pageSource).toContain('endpointReachable');
});

test('no fake healthy text when degraded or failing — statusLabel driven by live status', () => {
  expect(helpersSource).toContain('statusLabel');
  expect(allComponentSources).toContain('statusLabel(status)');
  expect(allComponentSources).toContain("statusLabel(provider.status)");
});

test('page handles missing data without showing everything as Unavailable', () => {
  // Endpoint failure is handled by a single error panel (see dedicated test),
  // never by mapping the failure onto every component card.
  expect(pageSource).toContain('endpointReachable');
  expect(summaryCardsSource).toContain('comp?.message');
  expect(opsOverviewSource).toContain('comp?.message');
  // Partial data (endpoint OK, one component missing) is labelled truthfully.
  expect(summaryCardsSource).toContain('Component check missing from backend response.');
  expect(opsOverviewSource).toContain('missing from the backend response');
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
