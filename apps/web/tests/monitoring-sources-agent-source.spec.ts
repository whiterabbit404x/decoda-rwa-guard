import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const appDir = path.join(__dirname, '..', 'app');
const dir = path.join(appDir, '(product)', 'monitoring-sources');
const read = (p: string) => fs.readFileSync(p, 'utf-8');

const pageSource = read(path.join(dir, 'page.tsx'));
const agentSource = read(path.join(dir, 'agent-panel.tsx'));
const drawerSource = read(path.join(dir, 'detail-drawer.tsx'));

test('agent panel renders all Screen-4 sections', () => {
  for (const section of [
    'Source Optimization Agent', 'Current Agent Assessment', 'AI Recommendations',
    'Auto-Routing', 'Recent Agent Decisions',
  ]) {
    expect(agentSource).toContain(section);
  }
});

test('agent panel surfaces provider-health counts', () => {
  expect(agentSource).toContain('Healthy');
  expect(agentSource).toContain('Degraded');
  expect(agentSource).toContain('Ingestion health');
});

test('AI-unavailable state is honest and does not fake healthy data', () => {
  expect(agentSource).toContain('AI explanation is unavailable. Deterministic monitoring remains active.');
});

test('Auto-Routing toggle is a persisted switch wired to the settings endpoint', () => {
  expect(agentSource).toContain("role=\"switch\"");
  expect(agentSource).toContain('aria-checked={autoRouting}');
  expect(pageSource).toContain('/api/monitoring/sources/settings');
  expect(pageSource).toContain('handleToggleAutoRouting');
});

test('no approved fallback state is labeled honestly', () => {
  expect(agentSource).toContain('Primary monitoring is active, but no approved fallback provider is available.');
});

test('Run Health Check calls the backend health-check endpoint', () => {
  expect(pageSource).toContain('/api/monitoring/sources/health-check');
  expect(pageSource).toContain('handleRunHealthCheck');
});

test('recent agent decisions open evidence and cover the decision vocabulary', () => {
  expect(pageSource).toContain('DecisionEvidenceDrawer');
  for (const label of [
    'No action required', 'Failover initiated', 'Failover completed', 'Failover failed',
    'Route restored', 'Engineering escalation', 'Degradation detected',
  ]) {
    expect(agentSource).toContain(label);
  }
});

test('decision evidence drawer exposes supporting record ids', () => {
  expect(drawerSource).toContain('Correlation ID');
  expect(drawerSource).toContain('Decision ID');
  expect(drawerSource).toContain('Triggered rule');
});

test('all Screen-4 proxy routes exist and forward CSRF for mutations', () => {
  const settingsProxy = read(path.join(appDir, 'api', 'monitoring', 'sources', 'settings', 'route.ts'));
  const healthProxy = read(path.join(appDir, 'api', 'monitoring', 'sources', 'health-check', 'route.ts'));
  const decisionsProxy = read(path.join(appDir, 'api', 'monitoring', 'sources', 'decisions', 'route.ts'));
  const streamProxy = read(path.join(appDir, 'api', 'stream', 'sources', 'route.ts'));

  // Mutations forward the CSRF token; GET-only proxies need not.
  expect(settingsProxy).toContain('X-CSRF-Token');
  expect(settingsProxy).toContain('export async function PUT');
  expect(settingsProxy).toContain('export async function GET');
  expect(healthProxy).toContain('X-CSRF-Token');
  expect(healthProxy).toContain('export async function POST');
  expect(decisionsProxy).toContain('export async function GET');
  expect(streamProxy).toContain('/stream/sources');
  // Timeouts on every backend call.
  expect(settingsProxy).toContain('fetchWithTimeout');
  expect(healthProxy).toContain('fetchWithTimeout');
});

test('diagnostics that require a live worker are disabled, not faked', () => {
  expect(drawerSource).toContain('Run Connectivity Test');
  expect(drawerSource).toContain('disabled');
  expect(drawerSource).toContain('Live per-endpoint probes require the monitoring worker.');
});
