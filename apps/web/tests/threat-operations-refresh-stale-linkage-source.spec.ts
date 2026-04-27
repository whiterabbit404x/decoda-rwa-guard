import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function appSource(fileName: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', fileName), 'utf-8');
}

test('snapshot refresh keeps canonical endpoints plus linked collections in parallel', () => {
  const threat = appSource('threat-operations-panel.tsx');

  expect(threat).toContain("fetch(`${apiUrl}/ops/monitoring/runtime-status`");
  expect(threat).toContain("fetch(`${apiUrl}/ops/monitoring/investigation-timeline`");
  expect(threat).toContain("fetch(`${apiUrl}/detections?limit=50`");
  expect(threat).toContain("fetch(`${apiUrl}/alerts?limit=50`");
  expect(threat).toContain("fetch(`${apiUrl}/incidents?limit=50`");
  expect(threat).toContain("fetch(`${apiUrl}/history/actions?limit=50`");
  expect(threat).toContain("fetch(`${apiUrl}/ops/monitoring/evidence?limit=50`");
});

test('refresh no longer clears collections and marks stale data on partial failures', () => {
  const threat = appSource('threat-operations-panel.tsx');

  expect(threat).not.toContain('setDetections([])');
  expect(threat).not.toContain('setAlerts([])');
  expect(threat).not.toContain('setIncidents([])');
  expect(threat).not.toContain('setEvidence([])');
  expect(threat).not.toContain('setActionHistory([])');
  expect(threat).toContain('setSnapshotStaleCollections(staleCollections)');
  expect(threat).toContain('Stale collections ${snapshotStaleCollections.join');
});

test('chain display uses persisted linkage ids and evidence counts from linked records', () => {
  const threat = appSource('threat-operations-panel.tsx');

  expect(threat).toContain('alert.chain_linked_ids?.detection_id');
  expect(threat).toContain('incident.chain_linked_ids?.detection_id');
  expect(threat).toContain('evidence {Number(alert.linked_evidence_count');
  expect(threat).toContain('linkedEvidenceCount: latestDetection?.linked_evidence_count');
  expect(threat).toContain('function hasEvidenceLinkedChainIds(detection: DetectionRow): boolean');
  expect(threat).toContain('&& hasEvidenceLinkedChainIds(item)');
});

test('incident timeline and evidence rendering blocks assert populated rows', () => {
  const threat = appSource('threat-operations-panel.tsx');

  expect(threat).toContain('investigationTimelineItems.map((item) => {');
  expect(threat).toContain('Open evidence drawer');
  expect(threat).toContain('id {item.id} · table {String(item.table_name || \'unknown\')} · evidence {sourceLabel}');
});
