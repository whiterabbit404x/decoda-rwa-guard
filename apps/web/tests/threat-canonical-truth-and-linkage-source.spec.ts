import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function appSource(fileName: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', fileName), 'utf-8');
}

test('dashboard and threat pages consume the canonical monitoring truth object from workspace feed', () => {
  const dashboard = appSource('dashboard-page-content.tsx');
  const threat = appSource('threat-operations-panel.tsx');

  expect(dashboard).toContain('const monitoringTruth = liveFeed?.monitoring.truth');
  expect(dashboard).toContain('const monitoringPresentation = liveFeed?.monitoring.presentation');

  expect(threat).toContain('const truth = feed.monitoring.truth;');
  expect(threat).toContain('const canonicalPresentation = feed.monitoring.presentation;');
  expect(threat).toContain('const monitoringPresentation = {');
  expect(threat).toContain('hasRuntimeQueryFailureMarker({');
});

test('threat page source renders persisted detections with linked alerts, incidents, and response actions', () => {
  const threat = appSource('threat-operations-panel.tsx');

  expect(threat).toContain('fetch(`${apiUrl}/detections?limit=50`');
  expect(threat).toContain('fetch(`${apiUrl}/detections/${detectionId}/evidence`');
  expect(threat).toContain('const targetById = useMemo(() => {');
  expect(threat).toContain('const monitoredSystemById = useMemo(() => {');
  expect(threat).toContain("const evidenceSourceLabel = simulatorEvidence ? 'Simulator evidence' : 'Live evidence';");
  expect(threat).toContain("const normalizedDetectionEvidenceSource = simulatorEvidence ? (replayEvidence ? 'replay' : 'simulator') : 'live';");
  expect(threat).not.toContain('live-like');
  expect(threat).toContain('fetch(`${apiUrl}/alerts?limit=50`');
  expect(threat).toContain('fetch(`${apiUrl}/incidents?limit=50`');
  expect(threat).toContain('fetch(`${apiUrl}/ops/monitoring/evidence?limit=50`');
  expect(threat).toContain('fetch(`${apiUrl}/history/actions?limit=50`');
  expect(threat).toContain('fetch(`${apiUrl}/monitoring/runs?limit=20`');
  expect(threat).toContain('Linked detection: {linkedDetection?.title || linkedDetection?.id || \'Not linked\'}');
  expect(threat).toContain('Active incidents with timeline and run evidence');
  expect(threat).toContain('Detection created → Alert created → Incident opened → Action logged');
  expect(threat).toContain('actionHistory.slice(0, 4).map((entry) => {');
  expect(threat).toContain('Response Actions');
});

test('simulated actions remain visibly labeled SIMULATED in operator pages', () => {
  const alertsPage = appSource('(product)/alerts-page-client.tsx');
  const incidentsPage = appSource('(product)/incidents-page-client.tsx');

  expect(alertsPage).toContain("? 'SIMULATED'");
  expect(incidentsPage).toContain("? 'SIMULATED'");
});
