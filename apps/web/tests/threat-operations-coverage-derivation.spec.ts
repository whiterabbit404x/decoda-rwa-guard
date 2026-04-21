import { expect, test } from '@playwright/test';

import {
  buildCoverageIndexes,
  destinationForLinked,
  evidenceStatusCopy,
  linkedRiskLabel,
  resolveLinkedCoverageForTarget,
} from '../app/threat-operations-panel';

test('derives target-linked coverage from linked entities only', () => {
  const indexes = buildCoverageIndexes({
    alerts: [{
      id: 'alert-1',
      title: 'Linked alert',
      severity: 'high',
      target_id: 'target-1',
      created_at: '2026-04-21T10:00:00.000Z',
      incident_id: 'incident-1',
    }],
    incidents: [{
      id: 'incident-1',
      title: 'Linked incident',
      severity: 'critical',
      source_alert_id: 'alert-1',
      created_at: '2026-04-21T10:05:00.000Z',
    }],
    detections: [{
      id: 'detection-1',
      monitored_system_id: 'system-1',
      linked_alert_id: 'alert-1',
      severity: 'medium',
      detected_at: '2026-04-21T09:59:00.000Z',
      evidence_source: 'chain-indexer',
    }],
    evidenceRows: [{
      id: 'evidence-1',
      target_id: 'target-1',
      detection_id: 'detection-1',
      observed_at: '2026-04-21T10:06:00.000Z',
      source_provider: 'chain-indexer',
      summary: 'Observed onchain transfer',
    }],
  });

  const linked = resolveLinkedCoverageForTarget({
    target: { id: 'target-1', name: 'Treasury Wallet', monitoring_enabled: true },
    systemIds: ['system-1'],
    indexes,
  });

  expect(linked.latestDetection?.id).toBe('detection-1');
  expect(linked.latestAlert?.id).toBe('alert-1');
  expect(linked.latestIncident?.id).toBe('incident-1');
  expect(linkedRiskLabel(linked)).toEqual({ label: 'Critical', tone: 'critical' });
  expect(destinationForLinked(linked)).toBe('/incidents');
  expect(evidenceStatusCopy({ resolution: linked, fallback: 'fallback' })).toBe('Observed onchain transfer');
});

test('marks missing or degraded evidence explicitly', () => {
  const indexes = buildCoverageIndexes({
    alerts: [],
    incidents: [],
    detections: [{
      id: 'detection-2',
      monitored_system_id: 'system-2',
      severity: 'low',
      detected_at: '2026-04-21T11:00:00.000Z',
      evidence_source: 'simulator',
    }],
    evidenceRows: [],
  });

  const linked = resolveLinkedCoverageForTarget({
    target: { id: 'target-2', name: 'Noisy system', monitoring_enabled: true },
    systemIds: ['system-2'],
    indexes,
  });

  expect(destinationForLinked(linked)).toBe('/detections');
  expect(evidenceStatusCopy({ resolution: linked, fallback: 'fallback' })).toBe('Degraded evidence');
});
