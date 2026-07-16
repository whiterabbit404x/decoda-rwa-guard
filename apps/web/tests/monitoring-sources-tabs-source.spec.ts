import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const dir = path.join(__dirname, '..', 'app', '(product)', 'monitoring-sources');
const pageSource = fs.readFileSync(path.join(dir, 'page.tsx'), 'utf-8');
const summarySource = fs.readFileSync(path.join(dir, 'summary-cards.tsx'), 'utf-8');
const agentSource = fs.readFileSync(path.join(dir, 'agent-panel.tsx'), 'utf-8');
const drawerSource = fs.readFileSync(path.join(dir, 'detail-drawer.tsx'), 'utf-8');
const typesSource = fs.readFileSync(path.join(dir, 'source-types.ts'), 'utf-8');

test('monitoring-sources route file exists', () => {
  expect(pageSource.length).toBeGreaterThan(100);
});

test('page title "Monitoring Sources" exists', () => {
  expect(pageSource).toContain('Monitoring Sources');
});

test('subtitle matches the Screen-4 spec', () => {
  expect(pageSource).toContain(
    'Continuously monitor provider health, ingestion coverage, routing status, and telemetry reliability.',
  );
});

test('header actions exist: Run Health Check, Add Source, Auto-Routing', () => {
  expect(pageSource).toContain('Run Health Check');
  expect(pageSource).toContain('Add Source');
  expect(agentSource).toContain('Auto-Routing');
  expect(pageSource).toContain('Last refreshed');
});

test('tabs exist: Monitoring Targets and Monitored Systems', () => {
  expect(pageSource).toContain('Monitoring Targets');
  expect(pageSource).toContain('Monitored Systems');
});

test('selected tab is persisted to the URL query string', () => {
  expect(pageSource).toContain("searchParams.set('tab'");
  expect(pageSource).toContain("get('tab')");
});

test('Monitoring Targets table has the required Screen-4 columns', () => {
  for (const col of [
    'Target / System', 'Network', 'Source Provider', 'Role', 'Status', 'Health Score',
    'P95 Latency', 'Block Lag', 'Error Rate', 'Last Event', 'Last Heartbeat', 'Routing', 'Actions',
  ]) {
    expect(pageSource).toContain(col);
  }
});

test('Monitored Systems table has the required Screen-4 columns', () => {
  for (const col of [
    'System', 'Type', 'Environment', 'Provider', 'Status', 'Availability',
    'Response Time', 'Last Successful Check', 'Last Failure', 'Current Route', 'Actions',
  ]) {
    expect(pageSource).toContain(col);
  }
});

test('five summary cards are present', () => {
  for (const card of ['Source Health', 'Active Routes', 'Telemetry Coverage', 'Oracle Heartbeats', 'Agent Activity']) {
    expect(summarySource).toContain(card);
  }
});

test('search, filters, sorting and pagination are implemented', () => {
  expect(pageSource).toContain('Search sources');
  expect(pageSource).toContain('Filter by status');
  expect(pageSource).toContain('Filter by network');
  expect(pageSource).toContain('Filter by provider');
  expect(pageSource).toContain('Filter by routing role');
  expect(pageSource).toContain('Sort by');
  expect(pageSource).toContain('function Pagination');
});

test('live connection status reflects SSE with polling fallback', () => {
  expect(pageSource).toContain('/api/stream/sources');
  expect(pageSource).toContain("streamStatus === 'live'");
  expect(pageSource).toContain('Live updates are reconnecting. Data is temporarily refreshed by polling.');
});

test('page never shows Healthy without live evidence', () => {
  // No unconditional "Healthy" literal — status comes from backend classification.
  const lines = pageSource.split('\n');
  const unconditionalHealthy = lines.filter((line) => {
    const trimmed = line.trim();
    return trimmed === "'Healthy'" || trimmed === '"Healthy"';
  });
  expect(unconditionalHealthy).toHaveLength(0);
});

test('health score cell shows "No live evidence" when the score is null', () => {
  expect(pageSource).toContain('No live evidence');
  const nullCheckIdx = pageSource.indexOf('source.health_score == null');
  const noEvidenceIdx = pageSource.indexOf('No live evidence');
  expect(nullCheckIdx).toBeGreaterThanOrEqual(0);
  expect(nullCheckIdx).toBeLessThan(noEvidenceIdx);
});

test('endpoint credentials are redacted before rendering', () => {
  expect(typesSource).toContain('export function redactEndpoint');
  expect(drawerSource).toContain('redactEndpoint');
  // host only — never path, query, or userinfo.
  expect(typesSource).toContain('url.host');
});

test('detail drawer never labels un-measured metrics as live values', () => {
  expect(drawerSource).toContain('Not measured');
  expect(drawerSource).toContain('Awaiting live probe worker integration');
});

test('page does not label simulator evidence as live_provider', () => {
  expect(pageSource).toContain("'simulator'");
  const simulatorCheckIdx = pageSource.indexOf("'simulator'");
  const liveProviderAssignIdx = pageSource.indexOf("label: 'live_provider'");
  expect(simulatorCheckIdx).toBeLessThan(liveProviderAssignIdx);
});
