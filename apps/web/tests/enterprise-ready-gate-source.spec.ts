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
  expect(threat).toContain("linked_fresh_evidence: 'Linked fresh evidence'");
  expect(threat).toContain("criterion_b_continuity_slos: 'Criterion B · Continuity SLOs'");
  expect(threat).toContain("hidden_architecture: 'Hidden architecture'");
  expect(threat).toContain('Pass is granted only after measurable evidence is present.');
  expect(threat).toContain("'/threat#monitored-system-state'");
  expect(threat).toContain("'/threat#response-actions'");
});

test('dashboard suppresses enterprise-ready claims when gate is false', () => {
  const dashboard = appSource('dashboard-page-content.tsx');
  expect(dashboard).toContain('Enterprise-ready claims:');
  expect(dashboard).toContain('Allowed');
  expect(dashboard).toContain('Blocked by readiness gate');
  expect(dashboard).toContain('Readiness remediation:');
  expect(dashboard).toContain('remediationChecks.map');
  expect(dashboard).toContain("enterpriseReadyPass ? 'PASS' : 'FAIL'");
  expect(dashboard).toContain("enterpriseReadyPass ? ' All enterprise checks are green.' : ' Claims remain blocked until the failed checks are remediated.'");
});
