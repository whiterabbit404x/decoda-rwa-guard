import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function appSource(fileName: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', fileName), 'utf-8');
}

test('threat page keeps all major operations panels and simulator/live labels', () => {
  const threat = appSource('threat-operations-panel.tsx');
  const threatPage = appSource('(product)/threat/page.tsx');
  const overviewCard = appSource('threat/threat-overview-card.tsx');
  const chainSection = appSource('threat/alert-incident-chain.tsx');
  const responseSection = appSource('threat/response-action-panel.tsx');
  const alertsPage = appSource('(product)/alerts-page-client.tsx');
  const incidentsPage = appSource('(product)/incidents-page-client.tsx');

  expect(threatPage).toContain('<h1>Threat Monitoring</h1>');
  expect(threatPage).not.toContain('contradiction_flags');
  expect(overviewCard).toContain('aria-label="Security Overview"');
  expect(chainSection).toContain('aria-label="Alert Incident Response Chain"');
  expect(responseSection).toContain('aria-label="Response Actions"');
  expect(threat).toContain('Recent Monitoring Runs');
  expect(threat).toContain('Recent Detections');
  expect(threat).toContain('Alerts');
  expect(threat).toContain('Incidents');
  expect(threat).toContain('Response Actions');
  expect(threat).toContain('Evidence source: {simulatorMode ?');

  expect(threat).toContain('(SIMULATOR/REPLAY)');
  expect(alertsPage).toContain("? 'SIMULATED'");
  expect(incidentsPage).toContain("? 'SIMULATED'");
  expect(alertsPage).toContain('ThreatChainPanel');
  expect(incidentsPage).toContain('ThreatChainPanel');
});

test('evidence drawer keeps summary and raw evidence rendering', () => {
  const threat = appSource('threat-operations-panel.tsx');
  const chainPanel = appSource('threat-chain-panel.tsx');
  const technicalRuntime = appSource('threat/technical-runtime-details.tsx');

  expect(threat).toContain('setEvidenceDrawer({');
  expect(threat).toContain('Open evidence drawer');
  expect(chainPanel).toContain('Threat chain summary');
  expect(chainPanel).toContain('Degraded evidence state: LIVE/HYBRID monitoring is active but this chain has no persisted evidence yet.');
  expect(chainPanel).toContain('Link href={step.href} prefetch={false}');
  expect(threat).toContain('role="dialog" aria-label="Evidence details"');
  expect(threat).toContain('Summary: {evidenceDrawer.summary || \'No evidence summary available.\'}');
  expect(threat).toContain("JSON.stringify(evidenceDrawer.raw ?? { message: 'No raw evidence found.' }, null, 2)");
  expect(technicalRuntime).toContain('<details className="tableMeta">');
  expect(technicalRuntime).toContain('<summary>View technical details</summary>');
});

test('dashboard and threat page share the same workspace monitoring truth object', () => {
  const dashboard = appSource('dashboard-page-content.tsx');
  const threat = appSource('threat-operations-panel.tsx');

  expect(dashboard).toContain('const monitoringTruth = liveFeed?.monitoring.truth');
  expect(dashboard).toContain('const monitoringPresentation = liveFeed?.monitoring.presentation');

  expect(threat).toContain('const truth = feed.monitoring.truth;');
  expect(threat).toContain('const canonicalPresentation = feed.monitoring.presentation;');
});

test('threat page empty incidents state keeps live continuity semantics', () => {
  const threat = appSource('threat-operations-panel.tsx');

  expect(threat).toContain('Open incident queue');
  expect(threat).toContain('Incident opened');
  expect(threat).toContain("continuityStatus === 'continuous_live'");
  expect(threat).toContain('continuous_live');
});
