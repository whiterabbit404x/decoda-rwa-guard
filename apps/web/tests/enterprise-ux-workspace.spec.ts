import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function read(relativePath: string) {
  return fs.readFileSync(path.join(__dirname, '..', relativePath), 'utf-8');
}

test('dashboard and shared shell expose truthful live workspace state', async () => {
  const hydrator = read('app/dashboard-live-hydrator.tsx');
  const shell = read('app/workspace-ownership-bar.tsx');
  expect(hydrator).toContain('useLiveWorkspaceFeed');
  expect(shell).toContain('Monitoring state');
  expect(shell).toContain('Evidence is stale');
  expect(shell).toContain('Open alerts');
  expect(shell).toContain('Open incidents');
});

test('primary product pages do not expose demo selectors or run-once controls', async () => {
  const alerts = read('app/(product)/alerts-page-client.tsx');
  const targets = read('app/targets-manager.tsx');
  const threat = read('app/threat-operations-panel.tsx');
  expect(alerts).not.toContain('source=demo');
  expect(targets).not.toContain('Demo scenario');
  expect(threat).not.toContain('Run once now');
  expect(threat).not.toContain('demo scenario');
});

test('alerts and incidents include operator decisions with role-gated governance actions', async () => {
  const alerts = read('app/(product)/alerts-page-client.tsx');
  const incidents = read('app/(product)/incidents-page-client.tsx');
  expect(alerts).toContain('Block transaction');
  expect(alerts).toContain('Freeze wallet');
  expect(alerts).toContain('Pause asset');
  expect(alerts).toContain('Apply compliance rule');
  expect(alerts).toContain('Only admin/owner can apply governance actions');
  expect(incidents).toContain('Escalate incident');
  expect(incidents).toContain('Create remediation task');
});

test('history includes first-class tabs and persisted filters', async () => {
  const history = read('app/history-records-view.tsx');
  const alerts = read('app/(product)/alerts-page-client.tsx');
  const incidents = read('app/(product)/incidents-page-client.tsx');
  expect(history).toContain('Alerts timeline');
  expect(history).toContain('Governance actions');
  expect(history).toContain('Audit logs');
  expect(history).toContain('window.localStorage.setItem(HISTORY_TAB_KEY');
  expect(alerts).toContain('window.localStorage.setItem(FILTER_KEY');
  expect(incidents).toContain('window.localStorage.setItem(FILTER_KEY');
});

