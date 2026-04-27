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
  expect(threat).toContain('fetch(`${apiUrl}/alerts/${encodeURIComponent(investigationTimeline.chain_linked_ids.alert_id)}/evidence?limit=50`');
  expect(threat).toContain('canonical_collections');
});

test('refresh no longer clears collections and marks stale data on partial failures', () => {
  const threat = appSource('threat-operations-panel.tsx');

  expect(threat).not.toContain('setDetections([])');
  expect(threat).not.toContain('setAlerts([])');
  expect(threat).not.toContain('setIncidents([])');
  expect(threat).not.toContain('setEvidence([])');
  expect(threat).not.toContain('setActionHistory([])');
  expect(threat).toContain('function payloadRows<T>(payload: any, keys: string[]): T[] | null');
  expect(threat).toContain('if (result.status === \'fulfilled\' && result.value.ok && endpointRows) {');
  expect(threat).toContain('if (canonicalRows) {');
  expect(threat).toContain('setSnapshotStaleCollections(staleCollections)');
  expect(threat).toContain('Stale collections ${snapshotStaleCollections.join');
});

test('chain display uses persisted linkage ids and evidence counts from linked records', () => {
  const threat = appSource('threat-operations-panel.tsx');

  expect(threat).toContain('alert.chain_linked_ids?.detection_id');
  expect(threat).toContain('incident.chain_linked_ids?.detection_id');
  expect(threat).toContain('evidence {Number(alert.linked_evidence_count');
  expect(threat).toContain('linkedEvidenceCount: latestDetection?.linked_evidence_count');
  expect(threat).toContain('const completeProofChain = Boolean(row && linkedAlert && linkedIncident);');
  expect(threat).toContain('const hasRealLinkedEvidence = linkedEvidenceCount > 0 && isRealEvidence(linkedEvidence, row);');
  expect(threat).toContain('&& completeProofChain');
  expect(threat).toContain('&& hasRealLinkedEvidence');
});

test('incident timeline and evidence rendering blocks assert populated rows', () => {
  const threat = appSource('threat-operations-panel.tsx');

  expect(threat).toContain('investigationTimelineItems.map((item) => {');
  expect(threat).toContain('Open evidence drawer');
  expect(threat).toContain('id {item.id} · table {String(item.table_name || \'unknown\')} · evidence {sourceLabel}');
  expect(threat).toContain('linked IDs: detection {String(chainIds.detection_id || \'n/a\')}');
  expect(threat).toContain('formatAbsoluteTime(item.timestamp)');
  expect(threat).toContain('!loadingSnapshot && linkedAlertRows.length === 0 ? (');
  expect(threat).toContain('!loadingSnapshot && incidents.length === 0 ? (');
});

test('alerts and incidents tables render non-empty list rows with persisted proof chain fields when data exists', () => {
  const threat = appSource('threat-operations-panel.tsx');

  expect(threat).toContain('linkedAlertRows.map(({ alert, linkedDetection }) => (');
  expect(threat).toContain('incidents.slice(0, 6).map((incident) => (');
  expect(threat).toContain('Chain: detection {alert.chain_linked_ids?.detection_id || alert.detection_id');
  expect(threat).toContain('Chain: detection {incident.chain_linked_ids?.detection_id || incident.linked_detection_id');
  expect(threat).toContain('linked IDs: detection {String(chainIds.detection_id || \'n/a\')}');
});
