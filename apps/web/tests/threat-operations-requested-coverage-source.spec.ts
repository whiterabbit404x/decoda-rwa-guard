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
  expect(threat).toContain('<strong>SIMULATED</strong> non-live action');
  expect(chainPanel).toContain('Open evidence');
  expect(chainPanel).toContain("label: 'Detection'");
  expect(chainPanel).toContain("label: 'Incident'");

  expect(alerts).toContain("? 'SIMULATED'");
  expect(incidents).toContain("? 'SIMULATED'");
  expect(alerts).toContain('Recommended mode (SIMULATED)');
  expect(incidents).toContain('Recommended mode (SIMULATED)');
  expect(alerts).toContain('Degraded evidence state: LIVE/HYBRID monitoring is active but this alert has no persisted linked evidence yet.');
  expect(incidents).toContain('Degraded evidence state: LIVE/HYBRID monitoring is active but this incident has no persisted linked evidence yet.');
});
