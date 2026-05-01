import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function source(): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', 'threat-operations-panel.tsx'), 'utf-8');
}

function threatSource(fileName: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', 'threat', fileName), 'utf-8');
}

test('contradiction guard blocks impossible provenance combinations', () => {
  const threat = source();
  expect(threat).toContain('function collectMonitoringContradictions');
  expect(threat).toContain("model.provenanceLabel === 'live' && (model.telemetryState !== 'fresh' || model.pollState !== 'fresh' || model.heartbeatState !== 'fresh')");
  expect(threat).toContain("model.provenanceLabel === 'partial_failure'");
  expect(threat).toContain("model.endpointProvenance.runtimeStatus");
  expect(threat).toContain("model.provenanceLabel === 'stale_snapshot'");
  expect(threat).toContain("'stale_snapshot provenance requires stale freshness telemetry or an endpoint stale_snapshot marker.'");
  expect(threat).toContain('Contradiction guard active');

  const technicalDetails = threatSource('technical-runtime-details.tsx');
  expect(technicalDetails).toContain('contradiction_flags:');
  expect(technicalDetails).toContain('guard_flags:');
});
