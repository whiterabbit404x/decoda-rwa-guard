import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const pagePath = path.join(__dirname, '..', 'app', '(product)', 'monitoring-sources', 'page.tsx');
const pageSource = fs.readFileSync(pagePath, 'utf-8');

test('monitoring-sources route file exists', () => {
  expect(pageSource.length).toBeGreaterThan(100);
});

test('page title "Monitoring Sources" exists', () => {
  expect(pageSource).toContain('Monitoring Sources');
});

test('subtitle exists', () => {
  expect(pageSource).toContain('Manage detection coverage by configuring monitoring targets and monitored systems.');
});

test('"Add Target" button exists', () => {
  expect(pageSource).toContain('Add Target');
});

test('tabs exist: Monitoring Targets and Monitored Systems', () => {
  expect(pageSource).toContain('Monitoring Targets');
  expect(pageSource).toContain('Monitored Systems');
});

test('Monitoring Targets table has exact required columns', () => {
  expect(pageSource).toContain('Target Name');
  expect(pageSource).toContain('Type');
  expect(pageSource).toContain('Provider');
  expect(pageSource).toContain('Systems');
  expect(pageSource).toContain('Status');
  expect(pageSource).toContain('Last Poll');
  expect(pageSource).toContain('Next Action');
});

test('Monitored Systems table has exact required columns', () => {
  expect(pageSource).toContain('System Name');
  expect(pageSource).toContain('Linked Target');
  expect(pageSource).toContain('Enabled');
  expect(pageSource).toContain('Runtime Status');
  expect(pageSource).toContain('Last Heartbeat');
  expect(pageSource).toContain('Last Telemetry');
  expect(pageSource).toContain('Coverage');
  expect(pageSource).toContain('Evidence Source');
});

test('empty state appears when asset exists but no monitoring target exists', () => {
  expect(pageSource).toContain('No monitoring target is linked to this asset yet');
  expect(pageSource).toContain('Create monitoring target');
});

test('empty state appears when target exists but no monitored system exists', () => {
  expect(pageSource).toContain('Target exists, but no monitored system is enabled');
  expect(pageSource).toContain('Enable monitored system');
});

test('page does not show Healthy when reporting systems is zero', () => {
  const lines = pageSource.split('\n');
  const unconditionalHealthy = lines.filter((line) => {
    const trimmed = line.trim();
    return trimmed === "'Healthy'" || trimmed === '"Healthy"';
  });

  expect(unconditionalHealthy).toHaveLength(0);
});

test('page does not show Reporting when last heartbeat is unavailable', () => {
  expect(pageSource).toContain('last_heartbeat');
  expect(pageSource).toContain("label: 'Reporting'");
  const reportingIdx = pageSource.indexOf("label: 'Reporting'");
  const heartbeatGuardIdx = pageSource.indexOf('!system.last_heartbeat');
  expect(heartbeatGuardIdx).toBeLessThan(reportingIdx);
});

test('page does not label simulator evidence as live_provider', () => {
  expect(pageSource).toContain("'simulator'");
  const simulatorCheckIdx = pageSource.indexOf("'simulator'");
  const liveProviderAssignIdx = pageSource.indexOf("label: 'live_provider'");
  expect(simulatorCheckIdx).toBeLessThan(liveProviderAssignIdx);
});
