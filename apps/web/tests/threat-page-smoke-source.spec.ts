import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function appSource(fileName: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', fileName), 'utf-8');
}

test('threat page keeps all major operations panels and simulator/live labels', () => {
  const threat = appSource('threat-operations-panel.tsx');
  const alertsPage = appSource('(product)/alerts-page-client.tsx');
  const incidentsPage = appSource('(product)/incidents-page-client.tsx');

  expect(threat).toContain('Recent Monitoring Runs');
  expect(threat).toContain('Recent Detections');
  expect(threat).toContain('Alerts');
  expect(threat).toContain('Incidents');
  expect(threat).toContain('Response Actions');
  expect(threat).toContain('Evidence source {monitoringPresentation.evidenceSourceLabel}');

  expect(threat).toContain('simulatorMode ?');
  expect(threat).toContain('Simulator evidence');
  expect(alertsPage).toContain("? 'SIMULATED'");
  expect(incidentsPage).toContain("? 'SIMULATED'");
});

test('evidence drawer keeps summary and raw evidence rendering', () => {
  const threat = appSource('threat-operations-panel.tsx');

  expect(threat).toContain('setEvidenceDrawer({');
  expect(threat).toContain('Open evidence');
  expect(threat).toContain('role="dialog" aria-label="Evidence details"');
  expect(threat).toContain('Summary: {evidenceDrawer.summary || \'No evidence summary available.\'}');
  expect(threat).toContain("JSON.stringify(evidenceDrawer.raw ?? { message: 'No raw evidence found.' }, null, 2)");
});

test('dashboard and threat page share the same workspace monitoring truth object', () => {
  const dashboard = appSource('dashboard-page-content.tsx');
  const threat = appSource('threat-operations-panel.tsx');

  expect(dashboard).toContain('const monitoringTruth = liveFeed?.monitoring.truth');
  expect(dashboard).toContain('const monitoringPresentation = liveFeed?.monitoring.presentation');

  expect(threat).toContain('const truth = feed.monitoring.truth;');
  expect(threat).toContain('const canonicalPresentation = feed.monitoring.presentation;');
});
