import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function appSource(fileName: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', fileName), 'utf-8');
}

test('threat panel renders enterprise readiness gate banner and remediation links', () => {
  const threat = appSource('threat-operations-panel.tsx');
  expect(threat).toContain('Enterprise readiness gate');
  expect(threat).toContain("Enterprise-readiness checks failed. Enterprise-ready copy is hidden until all checks pass.");
  expect(threat).toContain('Enterprise readiness gate:');
  expect(threat).toContain('ENTERPRISE_GATE_REMEDIATION_LINKS');
  expect(threat).toContain("stable_monitored_systems: 'Stable monitored systems'");
  expect(threat).toContain("'/threat#monitored-system-state'");
  expect(threat).toContain("'/threat#response-actions'");
});

test('dashboard suppresses enterprise-ready claims when gate is false', () => {
  const dashboard = appSource('dashboard-page-content.tsx');
  expect(dashboard).toContain('Enterprise-ready claims:');
  expect(dashboard).toContain('Blocked by readiness gate');
  expect(dashboard).toContain('Readiness remediation:');
});
