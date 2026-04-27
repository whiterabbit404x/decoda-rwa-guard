import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function appSource(fileName: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', fileName), 'utf-8');
}

test('live signal badges require complete linked ids and persisted linked evidence', () => {
  const threat = appSource('threat-operations-panel.tsx');

  expect(threat).toContain('const hasCompleteTimelineLinkedIds = Boolean(');
  expect(threat).toContain('const hasTimelineLinkedEvidence = Number(investigationTimeline?.linked_evidence_count ?? 0) > 0;');
  expect(threat).toContain('const showEvidenceLinkedSignals = hasDetectionTimelineLink && hasEvidenceTimelineLink && hasCompleteTimelineLinkedIds && hasTimelineLinkedEvidence;');
  expect(threat).toContain('const completeProofChain = Boolean(');
  expect(threat).toContain('&& linkedActionId');
  expect(threat).toContain('const hasRealLinkedEvidence = linkedEvidenceCount > 0 && isRealEvidence(linkedEvidence, row);');
});

test('refresh prefers canonical collections and keeps stale fallback markers when collection endpoints fail', () => {
  const threat = appSource('threat-operations-panel.tsx');

  expect(threat).toContain('function updateCollection<T>({');
  expect(threat).toContain('if (canonicalRows) {');
  expect(threat).toContain('if (!(result.status === \'fulfilled\' && result.value.ok)) {');
  expect(threat).toContain('stale.push(key);');
  expect(threat).toContain('setSnapshotStaleCollections(staleCollections);');
});
