import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function source(): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', 'threat-operations-panel.tsx'), 'utf-8');
}

test('contradiction guard blocks impossible provenance combinations', () => {
  const threat = source();
  expect(threat).toContain('function collectMonitoringContradictions');
  expect(threat).toContain("model.provenanceLabel === 'live' && (model.telemetryState !== 'fresh' || model.pollState !== 'fresh' || model.heartbeatState !== 'fresh')");
  expect(threat).toContain("model.provenanceLabel === 'partial_failure' && ![model.endpointProvenance.runtimeStatus, model.endpointProvenance.investigationTimeline].includes('partial_failure')");
  expect(threat).toContain("model.provenanceLabel === 'stale_snapshot'");
  expect(threat).toContain("includes('stale_snapshot')");
  expect(threat).toContain('Contradiction guard active');
});

