import { readFileSync } from 'node:fs';
import path from 'node:path';

import { expect, test } from '@playwright/test';

import { buildCoverageIndexes, resolveLinkedCoverageForTarget } from '../app/threat-operations-panel';

const appRoot = path.join(process.cwd(), 'apps/web/app');

function read(relativePath: string) {
  return readFileSync(path.join(appRoot, relativePath), 'utf8');
}

test('threat row builder excludes unrelated workspace-global alert and incident rows', () => {
  const indexes = buildCoverageIndexes({
    alerts: [{
      id: 'alert-unrelated',
      title: 'Workspace-global alert',
      severity: 'critical',
      target_id: null,
      created_at: '2026-04-21T12:00:00.000Z',
      incident_id: 'incident-unrelated',
    }],
    incidents: [{
      id: 'incident-unrelated',
      title: 'Workspace-global incident',
      severity: 'critical',
      source_alert_id: 'alert-unrelated',
      created_at: '2026-04-21T12:01:00.000Z',
    }],
    detections: [{
      id: 'detection-target',
      monitored_system_id: 'system-1',
      linked_alert_id: null,
      severity: 'low',
      detected_at: '2026-04-21T11:59:00.000Z',
      evidence_source: 'chain-indexer',
    }],
    evidenceRows: [],
  });

  const linked = resolveLinkedCoverageForTarget({
    target: { id: 'target-1', name: 'Treasury Wallet', monitoring_enabled: true },
    systemIds: ['system-1'],
    indexes,
  });

  expect(linked.latestDetection?.id).toBe('detection-target');
  expect(linked.latestAlert).toBeNull();
  expect(linked.latestIncident).toBeNull();
});

test('LIVE/HYBRID empty states use no-evidence degraded copy instead of healthy all-clear wording', () => {
  const alertsPage = read('(product)/alerts-page-client.tsx');
  const incidentsPage = read('(product)/incidents-page-client.tsx');

  expect(alertsPage).toContain('Degraded evidence state: LIVE/HYBRID monitoring is active but this alert has no persisted linked evidence yet.');
  expect(incidentsPage).toContain('Degraded evidence state: LIVE/HYBRID monitoring is active but this incident has no persisted linked evidence yet.');

  expect(alertsPage.toLowerCase()).not.toContain('all clear');
  expect(incidentsPage.toLowerCase()).not.toContain('all clear');
  expect(alertsPage.toLowerCase()).not.toContain('healthy');
  expect(incidentsPage.toLowerCase()).not.toContain('healthy');
});

test('unsupported live action controls stay disabled with reason text across threat, alerts, and incidents pages', () => {
  const threatPage = read('threat-operations-panel.tsx');
  const alertsPage = read('(product)/alerts-page-client.tsx');
  const incidentsPage = read('(product)/incidents-page-client.tsx');

  expect(threatPage).toContain("title={actionDisabledReason(actionCapabilities.block_transaction, 'simulated') || ''}");
  expect(alertsPage).toContain("title={actionDisabledReason(actionCapabilities.block_transaction, actionMode) || ''}");
  expect(incidentsPage).toContain("title={actionDisabledReason(actionCapabilities.block_transaction, actionMode) || ''}");

  expect(alertsPage).toContain('Unsupported live action');
  expect(incidentsPage).toContain('Unsupported live action');
});

test('repair flow exposes deterministic pending/success/failure transitions', () => {
  const source = read('monitored-systems-manager.tsx');

  expect(source).toContain("setRepairState('pending_request')");
  expect(source).toContain("setRepairState('pending_parse')");
  expect(source).toContain("setRepairState('pending_refresh')");
  expect(source).toContain("setRepairState('success')");
  expect(source).toContain("setRepairState('failure')");

  expect(source).toContain('Sending repair request…');
  expect(source).toContain('Parsing repair response…');
  expect(source).toContain('Refreshing monitored systems from workspace truth…');
  expect(source).toContain("Repair {reconcileSummary.state || 'success'} (reconcile id: {reconcileSummary.reconcile_id || 'unknown'})");
  expect(source).toContain('Repair failed during {repairFailureReason.stage}.');
});
