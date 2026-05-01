import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function appSource(fileName: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', fileName), 'utf-8');
}

test('threat header copy excludes raw internal debug flags', () => {
  const threatPage = appSource('(product)/threat/page.tsx');

  expect(threatPage).toContain('<h1>Threat Monitoring</h1>');
  expect(threatPage).not.toContain('contradiction_flags');
  expect(threatPage).not.toContain('guard_flags');
  expect(threatPage).not.toContain('db_failure_classification');
});

test('technical details disclosure exists and is collapsed by default', () => {
  const technicalRuntime = appSource('threat/technical-runtime-details.tsx');

  expect(technicalRuntime).toContain('<details className="tableMeta">');
  expect(technicalRuntime).toContain('<summary>View technical details</summary>');
});

test('alert -> incident -> response chain and focused posture/action rendering are explicit', () => {
  const chain = appSource('threat/alert-incident-chain.tsx');
  const response = appSource('threat/response-action-panel.tsx');
  const overview = appSource('threat/threat-overview-card.tsx');
  const operations = appSource('threat-operations-panel.tsx');

  expect(chain).toContain('aria-label="Alert Incident Response Chain"');
  expect(response).toContain('aria-label="Response Actions"');
  expect(overview).toContain('aria-label="Security Overview"');

});

test('threat composition components do not independently claim live/healthy status copy', () => {
  const healthCard = appSource('threat/monitoring-health-card.tsx');
  const overview = appSource('threat/threat-overview-card.tsx');
  const emptyState = appSource('threat/threat-empty-state.tsx');

  expect(healthCard.toLowerCase()).not.toContain('monitoring is active and operating normally');
  expect(healthCard.toLowerCase()).not.toContain('live monitoring is healthy');
  expect(overview.toLowerCase()).not.toContain('live monitoring is healthy');
  expect(emptyState.toLowerCase()).not.toContain('live monitoring is healthy');
});
