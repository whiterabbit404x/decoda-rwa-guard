import fs from 'fs';
import path from 'path';
import { test, expect } from '@playwright/test';

function readThreatPanelSource() {
  return fs.readFileSync(path.join(__dirname, '..', 'app', 'threat-operations-panel.tsx'), 'utf-8');
}

test('threat operations panel fetches and renders recent monitoring runs', async () => {
  const source = readThreatPanelSource();
  expect(source).toContain("fetch(`${apiUrl}/monitoring/runs?limit=20`, { headers: authHeaders(), cache: 'no-store' })");
  expect(source).toContain("setMonitoringRuns(payloadRows<MonitoringRunRow>(monitoringRunsPayload, 'runs'))");
  expect(source).toContain("setDetections(payloadRows<DetectionRow>(detectionsPayload, 'detections'))");
  expect(source).toContain("setAlerts(payloadRows<AlertRow>(alertsPayload, 'alerts'))");
  expect(source).toContain("setIncidents(payloadRows<IncidentRow>(incidentsPayload, 'incidents'))");
  expect(source).toContain("setActionHistory(payloadRows<ActionHistoryRow>(historyPayload, 'history'))");
  expect(source).toContain("setEvidence(payloadRows<EvidenceRow>(evidencePayload, 'evidence'))");
  expect(source).toContain('Recent Monitoring Runs');
  expect(source).toContain('Workspace cycle persistence');
  expect(source).toContain('monitoringRuns.slice(0, 8).map((run) => (');
  expect(source).toContain('Recent Detections');
  expect(source).toContain('detectionsToRender.map((signal) => (');
  expect(source).toContain('Alerts');
  expect(source).toContain('linkedAlertRows.map(({ alert, linkedDetection }) => (');
  expect(source).toContain('Incidents');
  expect(source).toContain('incidents.slice(0, 6).map((incident) => (');
  expect(source).toContain('Response Actions');
  expect(source).toContain('Open evidence drawer');
  expect(source).toContain('Category: Telemetry Events');
  expect(source).toContain('Category: Detections');
  expect(source).toContain('Category: Alerts');
  expect(source).toContain('Category: Incidents');
  expect(source).toContain('Category: Actions');
});
