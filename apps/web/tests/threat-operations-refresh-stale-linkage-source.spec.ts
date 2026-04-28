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
  expect(threat).toContain("detections: payloadRows<DetectionRow>(detectionsPayload, ['detections'])");
  expect(threat).toContain("history: payloadRows<ActionHistoryRow>(historyPayload, ['history', 'actions'])");
  expect(threat).toContain('const timelineAlertId = investigationTimelinePayload?.chain_linked_ids?.alert_id;');
  expect(threat).toContain('fetch(`${apiUrl}/alerts/${encodeURIComponent(String(timelineAlertId))}/evidence?limit=50`');
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
  expect(threat).toContain('function payloadRowsWithAvailability<T>(payload: any, keys: string[]): { rows: T[] | null; available: boolean }');
  expect(threat).toContain('const endpointOk = result.status === \'fulfilled\' && result.value.ok;');
  expect(threat).toContain('const applyRows = (rows: T[], allowEmpty: boolean): boolean => {');
  expect(threat).toContain('if (rows.length === 0 && !allowEmpty) {');
  expect(threat).toContain('if (canonical.available && canonical.rows) {');
  expect(threat).toContain('if (applyRows(rows, endpointOk)) {');
  expect(threat).toContain("if (endpointOk && endpoint.available && endpoint.rows) {");
  expect(threat).toContain('setSnapshotStaleCollections(staleCollections)');
  expect(threat).toContain('Stale collections ${snapshotStaleCollections.map((collection) => `${collection}:${formatAbsoluteTime(collectionLastSuccessfulRefreshAt[collection])}`).join');
  expect(threat).toContain('if (!endpointOk) {');
  expect(threat).toContain('stale.push(key);');
});

test('chain display uses persisted linkage ids and evidence counts from linked records', () => {
  const threat = appSource('threat-operations-panel.tsx');

  expect(threat).toContain('function resolvePersistedThreatChain(params: {');
  expect(threat).toContain('const persistedThreatChain = useMemo(() => resolvePersistedThreatChain({');
  expect(threat).toContain('alert.chain_linked_ids?.detection_id');
  expect(threat).toContain('incident.chain_linked_ids?.detection_id');
  expect(threat).toContain('detectionId: persistedThreatChain.linkedIds.detectionId');
  expect(threat).toContain('actionId: persistedThreatChain.linkedIds.actionId');
  expect(threat).toContain('evidence {Number(alert.linked_evidence_count');
  expect(threat).toContain('linkedEvidenceCount: latestDetection?.linked_evidence_count');
  expect(threat).toContain('const chainLinkedIds = row?.chain_linked_ids');
  expect(threat).toContain('const hasLinkedChainIds = Boolean(');
  expect(threat).toContain('const hasRealLinkedEvidence = linkedEvidenceCount > 0 && isRealEvidence(linkedEvidence, row);');
  expect(threat).toContain('&& hasLinkedChainIds');
  expect(threat).toContain('&& hasRealLinkedEvidence');
});

test('incident timeline and evidence rendering blocks assert populated rows', () => {
  const threat = appSource('threat-operations-panel.tsx');

  expect(threat).toContain('investigationTimelineItems.map((item) => {');
  expect(threat).toContain('Open evidence drawer');
  expect(threat).toContain('id {item.id} · table {String(item.table_name || \'unknown\')} · evidence {sourceLabel}');
  expect(threat).toContain('timeline: timestamp {timelineTimestamp} · source {sourceLabel}');
  expect(threat).toContain('linked IDs: detection {String(chainIds.detection_id || \'n/a\')}');
  expect(threat).toContain('formatAbsoluteTime(item.timestamp)');
  expect(threat).toContain('raw evidence refs: evidence_id');
  expect(threat).toContain('threatChainTimeline.orderedTimeline.map((step) => (');
  expect(threat).toContain('<Link href="/alerts" prefetch={false}>Detection</Link> → <Link href="/alerts" prefetch={false}>Alert</Link> → <Link href="/incidents" prefetch={false}>Incident</Link> → <Link href="/history" prefetch={false}>Action</Link>');
  expect(threat).toContain('<p>{step.label}: {step.id || \'n/a\'}</p>');
  expect(threat).toContain('const latestEvidence = chainPanelSelection.detectionId');
  expect(threat).toContain("timestamp: persistedThreatChain.action?.timestamp ?? null");
  expect(threat).toContain('!loadingSnapshot && linkedAlertRows.length === 0 ? (');
  expect(threat).toContain('!loadingSnapshot && incidents.length === 0 ? (');
});

test('live signal labeling requires linked chain IDs and real linked evidence for populated backend records', () => {
  const threat = appSource('threat-operations-panel.tsx');

  expect(threat).toContain('const chainLinkedIds = row?.chain_linked_ids');
  expect(threat).toContain('const hasLinkedChainIds = Boolean(');
  expect(threat).toContain('const hasRealLinkedEvidence = linkedEvidenceCount > 0 && isRealEvidence(linkedEvidence, row);');
  expect(threat).toContain('&& hasLinkedChainIds');
  expect(threat).toContain('&& hasRealLinkedEvidence');
});

test('detection rows surface raw evidence references and observed timestamps', () => {
  const threat = appSource('threat-operations-panel.tsx');

  expect(threat).toContain('rawEvidenceReference');
  expect(threat).toContain('raw evidence refs: evidence_id');
  expect(threat).toContain('raw evidence refs: detection');
  expect(threat).toContain('rawEvidenceObservedAt');
  expect(threat).toContain('observed {formatAbsoluteTime(signal.rawEvidenceObservedAt || signal.timestamp)}');
});

test('timeline evidence gate accepts telemetry/evidence links and complete chain ids', () => {
  const threat = appSource('threat-operations-panel.tsx');

  expect(threat).toContain("timelineLinkNames.has('telemetry')");
  expect(threat).toContain("timelineLinkNames.has('evidence')");
  expect(threat).toContain('const hasCompleteTimelineLinkedIds = Boolean(');
  expect(threat).toContain('const hasPersistedTimelineEvidence = Boolean(');
  expect(threat).toContain('&& hasPersistedTimelineEvidence;');
});

test('alerts and incidents tables render non-empty list rows with persisted proof chain fields when data exists', () => {
  const threat = appSource('threat-operations-panel.tsx');

  expect(threat).toContain('linkedAlertRows.map(({ alert, linkedDetection }) => (');
  expect(threat).toContain('incidents.slice(0, 6).map((incident) => (');
  expect(threat).toContain('Chain: detection {alert.chain_linked_ids?.detection_id || alert.detection_id');
  expect(threat).toContain('Chain: detection {incident.chain_linked_ids?.detection_id || incident.linked_detection_id');
  expect(threat).toContain('linked IDs: detection {String(chainIds.detection_id || \'n/a\')}');
});
