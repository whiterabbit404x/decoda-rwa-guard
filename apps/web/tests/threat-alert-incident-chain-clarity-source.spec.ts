import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function appSource(fileName: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', fileName), 'utf-8');
}

test('alert and incident rows explicitly render alert, incident, and response action linkage details', () => {
  const threat = appSource('threat-operations-panel.tsx');

  expect(threat).toContain('Alert: id {alert.chain_linked_ids?.alert_id || alert.id} · status {alert.status || \'Needs review\'}');
  expect(threat).toContain("Linked incident: id {alert.chain_linked_ids?.incident_id || alert.incident_id || 'No incident linked yet'}");
  expect(threat).toContain("Linked response action: id {alert.chain_linked_ids?.action_id || alert.linked_action_id || 'No response action linked yet'}");
  expect(threat).toContain("· mode {alert.response_action_mode || 'Mode not set'}");

  expect(threat).toContain("Alert: id {incident.chain_linked_ids?.alert_id || incident.source_alert_id || 'No alert linked yet'}");
  expect(threat).toContain('Linked incident: id {incident.chain_linked_ids?.incident_id || incident.id} · status {incident.status || \'Needs review\'}');
  expect(threat).toContain("Linked response action: id {incident.chain_linked_ids?.action_id || incident.linked_action_id || 'No response action linked yet'}");
  expect(threat).toContain("· mode {incident.response_action_mode || 'Mode not set'}");
});

test('linkage fallbacks use customer-safe empty-state language', () => {
  const threat = appSource('threat-operations-panel.tsx');

  expect(threat).toContain('No incident linked yet');
  expect(threat).toContain('No alert linked yet');
  expect(threat).toContain('No response action linked yet');
  expect(threat).toContain('Pending assignment');
});

test('data stitching uses canonical threat operations endpoints', () => {
  const threat = appSource('threat-operations-panel.tsx');

  expect(threat).toContain("fetch(`${apiUrl}/alerts?limit=50`");
  expect(threat).toContain("fetch(`${apiUrl}/incidents?limit=50`");
  expect(threat).toContain("fetch(`${apiUrl}/response/action-capabilities`");
  expect(threat).toContain("fetch(`${apiUrl}/monitoring/runs?limit=20`");
});
