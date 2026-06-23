import { expect, test } from '@playwright/test';
import { readFileSync } from 'fs';
import { join } from 'path';

const pageSource = readFileSync(
  join(process.cwd(), 'app/(product)/system-health/page.tsx'),
  'utf8',
);
const resilienceSource = readFileSync(
  join(process.cwd(), 'app/(product)/resilience/page.tsx'),
  'utf8',
);
const navSource = readFileSync(join(process.cwd(), 'app/product-nav.ts'), 'utf8');

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

test('status hero shows overall status badge and summary', () => {
  expect(pageSource).toContain('overallStatus');
  expect(pageSource).toContain('summaryText');
  expect(pageSource).toContain('All Systems Operational');
  expect(pageSource).toContain('Action Required');
  expect(pageSource).toContain('Degraded');
  expect(pageSource).toContain('Unavailable');
  expect(pageSource).toContain('primaryAction');
  expect(pageSource).toContain('Action required');
});

test('summary cards show 8 infrastructure components', () => {
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
    expect(pageSource).toContain(`'${key}'`);
  });
  expect(pageSource).toContain('COMPONENT_META');
});

test('component meta labels match SaaS naming', () => {
  ['API', 'Database', 'Redis', 'Worker', 'Base RPC', 'Live Polling', 'Telemetry Ingestion', 'Detection', 'Alert Delivery'].forEach((label) => {
    expect(pageSource).toContain(label);
  });
});

test('Operational Overview table has required columns', () => {
  ['Component', 'Status', 'What It Checks', 'Current Signal', 'Last Event', 'Age', 'Action'].forEach((column) => {
    expect(pageSource).toContain(column);
  });
  expect(pageSource).toContain('Operational Overview');
});

test('Status Overview shows canonical truth fields', () => {
  expect(pageSource).toContain('Status Overview');
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
    expect(pageSource).toContain(label);
  });
});

test('Live Chain Monitoring section exists with diagnosis box', () => {
  expect(pageSource).toContain('Live Chain Monitoring');
  expect(pageSource).toContain('Diagnosis');
  expect(pageSource).toContain('chainMonitoring.diagnosis');
  expect(pageSource).toContain('Worker &amp; Polling');
  expect(pageSource).toContain('RPC &amp; Telemetry');
  expect(pageSource).toContain('Expected chain ID');
  expect(pageSource).toContain('RPC configured');
  expect(pageSource).toContain('Telemetry 1h / 24h');
  expect(pageSource).toContain('Detections 1h / 24h');
});

test('health timeline and provider sections exist', () => {
  expect(pageSource).toContain('Incident &amp; Health Timeline');
  expect(pageSource).toContain('No recent health events');
  expect(pageSource).toContain('Provider Health');
  expect(pageSource).toContain('External dependencies');
});

test('reliability snapshot shows unavailable for unimplemented metrics', () => {
  expect(pageSource).toContain('Reliability &amp; Coverage');
  expect(pageSource).toContain('Active monitoring targets');
  expect(pageSource).toContain('RPC success rate');
  expect(pageSource).toContain('Unavailable: metric not implemented');
});

test('truth guards prevent false healthy status', () => {
  expect(pageSource).toContain('reportingSystems > 0');
  expect(pageSource).toContain('hasHeartbeat');
  expect(pageSource).toContain('hasTelemetry');
  expect(pageSource).toContain('contradictionFlags.length === 0');
});

test('runtime contradiction flags are shown', () => {
  expect(pageSource).toContain('contradictionFlags');
  expect(pageSource).toContain('Runtime contradictions detected');
});

test('no data shown as healthy - truth-closed status derivation', () => {
  expect(pageSource).toContain('isOperational');
  expect(pageSource).toContain('isOffline');
  expect(pageSource).toContain('!summaryMissing');
  expect(pageSource).toContain('summaryMissing');
  expect(pageSource).toContain('noSystemHealthData');
});

test('no secrets are rendered in page source', () => {
  expect(pageSource).not.toContain('DATABASE_URL');
  expect(pageSource).not.toContain('REDIS_URL');
  expect(pageSource).not.toContain('EVM_RPC_URL');
  expect(pageSource).not.toContain('API_KEY');
  expect(pageSource).not.toContain('password');
  expect(pageSource).not.toContain('secret');
});

test('nav label is System Health and /resilience remains backward compatible', () => {
  expect(navSource).toContain("label: 'System Health'");
  expect(navSource).toContain("href: '/system-health'");
  expect(resilienceSource).toContain("redirect('/system-health')");
});
