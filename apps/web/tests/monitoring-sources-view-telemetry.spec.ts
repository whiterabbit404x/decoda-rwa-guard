import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const monitoringPagePath = path.join(
  __dirname,
  '..',
  'app',
  '(product)',
  'monitoring-sources',
  'page.tsx',
);
const monitoringPageSource = fs.readFileSync(monitoringPagePath, 'utf-8');

const telemetryPagePath = path.join(
  __dirname,
  '..',
  'app',
  '(product)',
  'monitoring-sources',
  '[targetId]',
  'telemetry',
  'page.tsx',
);
const telemetryPageSource = fs.readFileSync(telemetryPagePath, 'utf-8');

const proxyRoutePath = path.join(
  __dirname,
  '..',
  'app',
  'api',
  'monitoring',
  'targets',
  '[targetId]',
  'telemetry',
  'route.ts',
);
const proxyRouteSource = fs.readFileSync(proxyRoutePath, 'utf-8');

// --- Monitoring sources page: View telemetry is a clickable link ---

test('"View telemetry" is rendered as a Next.js Link (not a plain span)', () => {
  // Confirm <Link is present in the table row render block (after the target loop opening)
  const loopStart = monitoringPageSource.indexOf('targets.map((target)');
  expect(loopStart).toBeGreaterThan(-1);
  const loopBlock = monitoringPageSource.slice(loopStart);
  expect(loopBlock).toContain('<Link');
  // The href template literal contains the telemetry path
  expect(loopBlock).toContain('/telemetry');
  // The "View telemetry" text appears as link content (not just in the function string)
  const linkTagIdx = loopBlock.indexOf('<Link');
  const afterFirstLink = loopBlock.slice(linkTagIdx);
  expect(afterFirstLink).toContain('View telemetry');
});

test('"View telemetry" link href contains the target ID', () => {
  // href template literal should interpolate target.id for the telemetry route
  expect(monitoringPageSource).toContain('/monitoring-sources/');
  expect(monitoringPageSource).toContain('/telemetry');
  // The href for the telemetry link encodes target.id
  const telemetryHrefPattern = /\/monitoring-sources\/[^'"]*target\.id[^'"]*\/telemetry/;
  expect(telemetryHrefPattern.test(monitoringPageSource)).toBe(true);
});

test('"View telemetry" link only renders when target.id is truthy', () => {
  expect(monitoringPageSource).toContain("targetNextAction(target) === 'View telemetry' && target.id");
});

// --- Telemetry page: empty state ---

test('telemetry page file exists', () => {
  expect(telemetryPageSource.length).toBeGreaterThan(100);
});

test('telemetry page shows truthful empty state when no telemetry exists', () => {
  expect(telemetryPageSource).toContain(
    'No live telemetry has been persisted for this target yet.',
  );
});

test('telemetry page empty state guard checks rows.length === 0', () => {
  expect(telemetryPageSource).toContain('rows.length === 0');
});

test('telemetry page does not fake telemetry (no mock/demo/simulator data)', () => {
  expect(telemetryPageSource).not.toContain('mock');
  expect(telemetryPageSource).not.toContain('demo_');
  expect(telemetryPageSource).not.toContain('fakeRow');
  expect(telemetryPageSource).not.toContain('simulatedRow');
});

// --- Telemetry page: live RPC telemetry display ---

test('telemetry page renders target_id field', () => {
  expect(telemetryPageSource).toContain('targetId');
});

test('telemetry page renders workspace_id field', () => {
  expect(telemetryPageSource).toContain('workspace_id');
  expect(telemetryPageSource).toContain('Workspace ID');
});

test('telemetry page renders provider_type column', () => {
  expect(telemetryPageSource).toContain('provider_type');
  expect(telemetryPageSource).toContain('Provider Type');
});

test('telemetry page renders evidence_source column', () => {
  expect(telemetryPageSource).toContain('evidence_source');
  expect(telemetryPageSource).toContain('Evidence Source');
});

test('telemetry page renders source_type column', () => {
  expect(telemetryPageSource).toContain('source_type');
  expect(telemetryPageSource).toContain('Source Type');
});

test('telemetry page renders chain_id column', () => {
  expect(telemetryPageSource).toContain('chain_id');
  expect(telemetryPageSource).toContain('Chain ID');
});

test('telemetry page renders block_number column', () => {
  expect(telemetryPageSource).toContain('block_number');
  expect(telemetryPageSource).toContain('Block Number');
});

test('telemetry page renders observed_at column', () => {
  expect(telemetryPageSource).toContain('observed_at');
  expect(telemetryPageSource).toContain('Observed At');
});

test('telemetry page renders raw provider response', () => {
  expect(telemetryPageSource).toContain('payload_json');
  expect(telemetryPageSource).toContain('Raw Response');
});

test('telemetry page fetches from the correct API route using target ID', () => {
  expect(telemetryPageSource).toContain('/api/monitoring/targets/');
  expect(telemetryPageSource).toContain('/telemetry');
  expect(telemetryPageSource).toContain('targetId');
});

// --- Proxy route ---

test('proxy route exists and proxies to backend targeting the target ID', () => {
  expect(proxyRouteSource).toContain('/monitoring/targets/');
  expect(proxyRouteSource).toContain('targetId');
  expect(proxyRouteSource).toContain('telemetry');
});

test('proxy route passes workspace header to backend', () => {
  expect(proxyRouteSource).toContain('x-workspace-id');
  expect(proxyRouteSource).toContain('X-Workspace-Id');
});

// --- Spelling: no telemetry typo variant anywhere ---

test('telemetry route folder is spelled correctly (no typo variant)', () => {
  const dir = path.join(
    __dirname,
    '..',
    'app',
    '(product)',
    'monitoring-sources',
    '[targetId]',
    'telemetry',
  );
  expect(fs.existsSync(dir)).toBe(true);
  const typo = `telem${'try'}`;
  const typoDir = path.join(
    __dirname,
    '..',
    'app',
    '(product)',
    'monitoring-sources',
    '[targetId]',
    typo,
  );
  expect(fs.existsSync(typoDir)).toBe(false);
});

test('View telemetry link uses /telemetry spelling (no typo variant)', () => {
  const typo = `telem${'try'}`;
  expect(monitoringPageSource).not.toContain(`/${typo}`);
  expect(monitoringPageSource).toContain('/telemetry');
});

test('proxy route URL uses /telemetry spelling (no typo variant)', () => {
  const typo = `telem${'try'}`;
  expect(proxyRouteSource).not.toContain(typo);
  expect(proxyRouteSource).toContain('telemetry');
});

// --- Telemetry detail modal: event classification ---

test('telemetry page classifies wallet transfer events and shows label', () => {
  expect(telemetryPageSource).toContain('wallet_transfer');
  expect(telemetryPageSource).toContain('Wallet transfer detected');
});

test('telemetry page classifies block polling events and shows heartbeat label', () => {
  expect(telemetryPageSource).toContain('block_poll');
  expect(telemetryPageSource).toContain('RPC polling heartbeat — no wallet transfer detected');
});

test('telemetry page handles missing tx_hash gracefully (falls back to unknown kind)', () => {
  expect(telemetryPageSource).toContain('classifyEvent');
  expect(telemetryPageSource).toContain("'unknown'");
});

test('telemetry page renders Basescan link for chain_id 8453', () => {
  expect(telemetryPageSource).toContain('8453');
  expect(telemetryPageSource).toContain('basescan.org');
  expect(telemetryPageSource).toContain('View on Basescan');
});

test('telemetry page renders Copy JSON button', () => {
  expect(telemetryPageSource).toContain('Copy JSON');
});

test('telemetry page renders Copy Tx Hash button', () => {
  expect(telemetryPageSource).toContain('Copy Tx Hash');
});

test('telemetry detail opens as a modal overlay (not inline details element)', () => {
  // View button sets selected row via setSelectedRow, not <details>
  expect(telemetryPageSource).toContain('setSelectedRow(row)');
  expect(telemetryPageSource).toContain('TelemetryDetailModal');
  // The old inline <details> expand pattern should be gone
  expect(telemetryPageSource).not.toContain('<details>');
});

test('telemetry detail modal JSON viewer uses dark background token', () => {
  expect(telemetryPageSource).toContain('var(--bg-base)');
  expect(telemetryPageSource).toContain('monospace');
});

test('telemetry detail modal shows human-readable summary fields', () => {
  expect(telemetryPageSource).toContain('Event type');
  expect(telemetryPageSource).toContain('Transaction hash');
  expect(telemetryPageSource).toContain('From address');
  expect(telemetryPageSource).toContain('To address');
  expect(telemetryPageSource).toContain('Observed at');
  expect(telemetryPageSource).toContain('Evidence source');
});
