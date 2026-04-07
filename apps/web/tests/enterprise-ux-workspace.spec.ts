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
  expect(shell).toContain('This workspace');
  expect(shell).toContain('Protected assets');
  expect(shell).toContain('Monitored systems');
  expect(shell).toContain('Alerts for this workspace');
  expect(shell).toContain('Incidents affecting this workspace');
  expect(shell).toContain('coverage freshness');
});

test('primary threat page uses always-on monitoring language and removes scenario-run flow', async () => {
  const threatPage = read('app/(product)/threat/page.tsx');
  const threatPanel = read('app/threat-operations-panel.tsx');

  expect(threatPage).toContain('Live monitoring for this workspace');
  expect(threatPanel).toContain('This workspace is under continuous monitoring');
  expect(threatPanel).toContain('Monitored systems');
  expect(threatPanel).toContain('Recent activity');
  expect(threatPanel).toContain('Investigate and act from live workspace monitoring');

  expect(threatPanel).not.toContain('scenario presets');
  expect(threatPanel).not.toContain('Run analysis');
  expect(threatPanel).not.toContain('Run once now');
  expect(threatPanel).not.toContain('Advanced policy configuration (JSON)');
  expect(threatPanel.toLowerCase()).not.toContain('demo');
  expect(threatPanel).not.toContain('synthetic_leak');
  expect(threatPanel).not.toContain('monitoring_scenario');
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
