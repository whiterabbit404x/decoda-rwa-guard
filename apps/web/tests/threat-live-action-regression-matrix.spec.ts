import { readFileSync } from 'node:fs';
import path from 'node:path';

import { expect, test } from '@playwright/test';

import { buildCoverageIndexes, resolveLinkedCoverageForTarget } from '../app/threat-operations-panel';

const cwd = process.cwd();
const appRoot = cwd.endsWith(path.join('apps', 'web')) ? path.join(cwd, 'app') : path.join(cwd, 'apps/web/app');

function read(relativePath: string) {
  return readFileSync(path.join(appRoot, relativePath), 'utf8');
}

test('target linkage remains isolated from unrelated workspace-level alerts and incidents', () => {
  const indexes = buildCoverageIndexes({
    alerts: [{
      id: 'alert-workspace-global',
      title: 'Global alert',
      severity: 'critical',
      target_id: null,
      created_at: '2026-04-21T12:00:00.000Z',
      incident_id: 'incident-workspace-global',
    }],
    incidents: [{
      id: 'incident-workspace-global',
      title: 'Global incident',
      severity: 'critical',
      source_alert_id: 'alert-workspace-global',
      created_at: '2026-04-21T12:01:00.000Z',
    }],
    detections: [{
      id: 'det-target',
      monitored_system_id: 'sys-target',
      linked_alert_id: null,
      severity: 'high',
      detected_at: '2026-04-21T11:59:00.000Z',
      evidence_source: 'chain-indexer',
    }],
    evidenceRows: [],
  });

  const linked = resolveLinkedCoverageForTarget({
    target: { id: 'target-1', name: 'Treasury Wallet', monitoring_enabled: true },
    systemIds: ['sys-target'],
    indexes,
  });

  expect(linked.latestDetection?.id).toBe('det-target');
  expect(linked.latestAlert).toBeNull();
  expect(linked.latestIncident).toBeNull();
});

test('LIVE/HYBRID no-evidence wording stays degraded and does not imply healthy all-clear state', () => {
  const chainPanel = read('threat-chain-panel.tsx').toLowerCase();
  const alertsPage = read('(product)/alerts-page-client.tsx').toLowerCase();
  const incidentsPage = read('(product)/incidents-page-client.tsx').toLowerCase();
  const threatOperationsPanel = read('threat-operations-panel.tsx').toLowerCase();

  expect(chainPanel).toContain('degraded evidence state: live/hybrid monitoring is active but this chain has no persisted evidence yet.');
  expect(threatOperationsPanel).toContain('live polling active. no recent anomaly evidence.');
  expect(threatOperationsPanel).not.toContain('live polling active. telemetry continuity is healthy');
  expect(alertsPage).not.toContain('all clear');
  expect(incidentsPage).not.toContain('all clear');
  expect(alertsPage).not.toContain('healthy');
  expect(incidentsPage).not.toContain('healthy');
});

test('unsupported live actions stay disabled with explicit reason text in all action surfaces', () => {
  const threatPage = read('threat-operations-panel.tsx');
  const alertsPage = read('(product)/alerts-page-client.tsx');
  const incidentsPage = read('(product)/incidents-page-client.tsx');

  expect(threatPage).toContain("title={actionDisabledReason(actionCapabilities.block_transaction, 'simulated') || ''}");
  expect(alertsPage).toContain("title={actionDisabledReason(actionCapabilities.block_transaction, actionMode) || ''}");
  expect(incidentsPage).toContain("title={actionDisabledReason(actionCapabilities.block_transaction, actionMode) || ''}");
  expect(alertsPage).toContain('Unsupported live action');
  expect(incidentsPage).toContain('Unsupported live action');
});

test('repair state rendering remains deterministic across pending and terminal stages', () => {
  const source = read('monitored-systems-manager.tsx');
  const order = [
    "setRepairState('pending_request')",
    "setRepairState('pending_parse')",
    "setRepairState('pending_refresh')",
    "setRepairState('success')",
    "setRepairState('failure')",
  ];

  for (const marker of order) {
    expect(source).toContain(marker);
  }
  expect(source.indexOf(order[0])).toBeLessThan(source.indexOf(order[1]));
  expect(source.indexOf(order[1])).toBeLessThan(source.indexOf(order[2]));
  expect(source).toContain('Sending repair request…');
  expect(source).toContain('Parsing repair response…');
  expect(source).toContain('Refreshing monitored systems from workspace truth…');
});
