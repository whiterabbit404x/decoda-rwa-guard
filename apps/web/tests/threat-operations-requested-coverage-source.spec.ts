import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function appSource(fileName: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', fileName), 'utf-8');
}

test('uses shared monitoring truth object across dashboard and threat page', () => {
  const dashboard = appSource('dashboard-page-content.tsx');
  const threat = appSource('threat-operations-panel.tsx');

  expect(dashboard).toContain('const monitoringTruth = liveFeed?.monitoring.truth');
  expect(dashboard).toContain('const monitoringPresentation = liveFeed?.monitoring.presentation');
  expect(threat).toContain('const truth = feed.monitoring.truth;');
  expect(threat).toContain('const canonicalPresentation = feed.monitoring.presentation;');
});

test('renders threat operations panels for runs detections alerts incidents and actions', () => {
  const threat = appSource('threat-operations-panel.tsx');

  expect(threat).toContain('Recent Monitoring Runs');
  expect(threat).toContain('Recent Detections');
  expect(threat).toContain('Alerts');
  expect(threat).toContain('Incidents');
  expect(threat).toContain('Response Actions');
});

test('renders evidence drawer and keeps SIMULATED labels explicit', () => {
  const threat = appSource('threat-operations-panel.tsx');
  const alerts = appSource('(product)/alerts-page-client.tsx');
  const incidents = appSource('(product)/incidents-page-client.tsx');
  const chainPanel = appSource('threat-chain-panel.tsx');

  expect(threat).toContain('role="dialog" aria-label="Evidence details"');
  expect(threat).toContain('<p className="sectionEyebrow">Evidence</p>');
  expect(threat).toContain('<span className="ruleChip">SIMULATED</span>');
  expect(chainPanel).toContain('Open evidence drawer');
  expect(chainPanel).toContain("label: 'Detection'");
  expect(chainPanel).toContain("label: 'Incident'");

  expect(alerts).toContain("? 'SIMULATED'");
  expect(incidents).toContain("? 'SIMULATED'");
  expect(alerts).toContain('Recommended mode (SIMULATED)');
  expect(incidents).toContain('Recommended mode (SIMULATED)');
  expect(chainPanel).toContain('Degraded evidence state: LIVE/HYBRID monitoring is active but this chain has no persisted evidence yet.');
  expect(alerts).not.toContain('this alert has no persisted linked evidence yet');
  expect(incidents).not.toContain('this incident has no persisted linked evidence yet');
});

test('threat quick actions require explicit linked context and block unrelated fallback rows', () => {
  const threat = appSource('threat-operations-panel.tsx');

  expect(threat).not.toContain('alerts[0]');
  expect(threat).not.toContain('incidents[0]');
  expect(threat).not.toContain('incident_id: incidents[0]?.id');
  expect(threat).not.toContain('alert_id: alerts[0]?.id');
  expect(threat).toContain('const shouldBlockThreatActionCreation = noLinkedActionContextAvailable || !selectedThreatActionContext;');
  expect(threat).toContain('if (shouldBlockThreatActionCreation) {');
  expect(threat).toContain("setResponseToast('No linked alert/incident context available.');");
  expect(threat).toContain("if (mode === 'live' && !selectedThreatActionContext?.incidentId) {");
  expect(threat).toContain("setResponseToast('LIVE actions require linked incident context.');");
  expect(threat).toContain('incident_id: selectedThreatActionContext.incidentId');
  expect(threat).toContain('alert_id: selectedThreatActionContext.alertId');
  expect(threat).toContain('<option value="" disabled>Select linked detection/alert/incident context</option>');
  expect(threat).toContain('role="dialog" aria-label="Confirm live action"');
  expect(threat).toContain('LIVE action confirmation');
  expect(threat).toContain('Confirm LIVE action');
  expect(threat).not.toContain('Unlinked action (manual follow-up required)');
  expect(threat).toContain('No linked alert/incident context available.');
});
