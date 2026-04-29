import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const threatPanelPath = path.join(__dirname, '..', 'app', 'threat-operations-panel.tsx');

function readThreatPanel(): string {
  return fs.readFileSync(threatPanelPath, 'utf-8');
}

test('partial endpoint failures are normalized into failedEndpoints and provenance fallback', () => {
  const threat = readThreatPanel();

  expect(threat).toContain('const failedEndpoints = responseEntries');
  expect(threat).toContain("setSnapshotFailedEndpoints(failedEndpoints);");
  expect(threat).toContain("const derivedProvenanceLabel: MonitoringProvenanceLabel = snapshotFailedEndpoints.length > 0");
  expect(threat).toContain("? 'partial_failure'");
  expect(threat).toContain('Monitoring snapshot fallback is active because');
});

test('stale snapshot retention keeps cached collections and records refresh timestamps', () => {
  const threat = readThreatPanel();

  expect(threat).toContain('const cachedRows = collectionCacheRef.current[cacheKey] as T[];');
  expect(threat).toContain('if (cachedRows.length > 0) {');
  expect(threat).toContain('setter(cachedRows);');
  expect(threat).toContain('stale.push(key);');
  expect(threat).toContain('setCollectionLastSuccessfulRefreshAt((current) => ({');
  expect(threat).toContain('setSnapshotStaleCollections(staleCollections);');
  expect(threat).toContain('const canonicalCollections: CanonicalCollectionPayload = {');
  expect(threat).toContain('...(runtimeStatusPayload?.canonical_collections ?? {}),');
  expect(threat).toContain('...(investigationTimelinePayload?.canonical_collections ?? {}),');
  expect(threat).toContain('if (cachedRows.length > 0) {');
  expect(threat).not.toContain('setter([]);');
});

test('live candidate and timeline rendering require persisted evidence references', () => {
  const threat = readThreatPanel();

  expect(threat).toContain('const hasPersistedEvidenceRecord = Boolean(linkedEvidence?.id);');
  expect(threat).toContain('const hasRealLinkedEvidence = hasPersistedEvidenceRecord && linkedEvidenceCount > 0 && isRealEvidence(linkedEvidence, row);');
  expect(threat).toContain('raw evidence refs: timeline_item {item.id} · link {linkName} · table');
});
