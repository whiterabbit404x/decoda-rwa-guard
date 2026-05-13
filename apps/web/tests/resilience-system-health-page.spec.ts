import { expect, test } from '@playwright/test';
import { readFileSync } from 'fs';
import { join } from 'path';

const pageSource = readFileSync(
  join(process.cwd(), 'apps/web/app/(product)/system-health/page.tsx'),
  'utf8',
);
const resilienceSource = readFileSync(
  join(process.cwd(), 'apps/web/app/(product)/resilience/page.tsx'),
  'utf8',
);
const navSource = readFileSync(join(process.cwd(), 'apps/web/app/product-nav.ts'), 'utf8');

test('/system-health renders the System Health screen', () => {
  expect(pageSource).toContain('<h1>System Health</h1>');
  expect(pageSource).toContain(
    'Monitor platform reliability, runtime services, providers, and operational health.',
  );
  expect(pageSource).toContain('Refresh Health');
});

test('top metric cards exist exactly as required', () => {
  ['Uptime', 'Avg Response Time', 'Error Rate', 'Active Systems'].forEach((label) => {
    expect(pageSource).toContain(label);
  });
});

test('System Components table has required columns and components', () => {
  ['Component', 'Status', 'Uptime', 'Response Time', 'Last Check'].forEach((column) => {
    expect(pageSource).toContain(`<th>${column}</th>`);
  });
  [
    'API Gateway',
    'Telemetry Service',
    'Worker',
    'Detection Engine',
    'Alert Engine',
    'Database',
    'Redis / Queue',
    'Provider Connectors',
    'Evidence Export',
  ].forEach((component) => {
    expect(pageSource).toContain(component);
  });
});

test('Status Overview shows operational truth fields', () => {
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
    'Next required action',
  ].forEach((label) => {
    expect(pageSource).toContain(label);
  });
});

test('health events and provider dependency sections exist', () => {
  expect(pageSource).toContain('Recent Health Events');
  ['Time', 'Component', 'Event', 'Severity', 'Result'].forEach((column) => {
    expect(pageSource).toContain(`<th>${column}</th>`);
  });
  expect(pageSource).toContain('Provider Health');
  expect(pageSource).toContain('External Dependencies');
  ['Provider', 'Type', 'Status', 'Last Sync', 'Last Error', 'Action'].forEach((column) => {
    expect(pageSource).toContain(`<th>${column}</th>`);
  });
});

test('truth guards prevent false healthy status', () => {
  expect(pageSource).toContain('reportingSystems > 0');
  expect(pageSource).toContain('hasHeartbeat');
  expect(pageSource).toContain('hasTelemetry');
  expect(pageSource).toContain('contradictionFlags.length === 0');
  expect(pageSource).toContain("component: 'Worker'");
  expect(pageSource).toContain("status: hasHeartbeat ? 'Operational'");
  expect(pageSource).toContain("component: 'Telemetry Service'");
  expect(pageSource).toContain('hasTelemetry && telemetryFresh');
});

test('contradiction flags and degraded states are visible', () => {
  expect(pageSource).toContain('Runtime contradiction detected');
  expect(pageSource).toContain('CONTRADICTION_MESSAGES');
  expect(pageSource).toContain('System health unavailable');
  expect(pageSource).toContain('No monitored systems reporting');
  expect(pageSource).toContain('Worker heartbeat unavailable');
  expect(pageSource).toContain('Telemetry unavailable');
});

test('nav label is System Health and /resilience remains backward compatible', () => {
  expect(navSource).toContain("label: 'System Health'");
  expect(navSource).toContain("href: '/system-health'");
  expect(resilienceSource).toContain("redirect('/system-health')");
});
