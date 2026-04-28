import { expect, test } from '@playwright/test';

import { collectMonitoringContradictions } from '../app/threat-operations-panel';

type Input = Parameters<typeof collectMonitoringContradictions>[0];

function model(overrides: Partial<Input>): Input {
  return {
    provenanceLabel: 'degraded',
    telemetryState: 'fresh',
    pollState: 'fresh',
    heartbeatState: 'fresh',
    endpointProvenance: {
      runtimeStatus: 'degraded',
      investigationTimeline: 'degraded',
    },
    presentationStatus: 'degraded',
    ...overrides,
  };
}

test.describe('threat operations monitoring contradiction guards', () => {
  test('flags contradictory live provenance combinations', async () => {
    const contradictions = collectMonitoringContradictions(model({
      provenanceLabel: 'live',
      telemetryState: 'stale',
      pollState: 'fresh',
      heartbeatState: 'fresh',
      endpointProvenance: {
        runtimeStatus: 'degraded',
        investigationTimeline: 'live',
      },
      presentationStatus: 'degraded',
    }));

    expect(contradictions).toContain('Live provenance cannot be shown while telemetry, poll, or heartbeat freshness is stale/unavailable.');
    expect(contradictions).toContain('Live provenance requires runtime endpoint provenance to be live.');
    expect(contradictions).toContain('Live provenance requires presentation status to be live.');
  });

  test('flags invalid partial failure provenance without failed endpoints', async () => {
    const contradictions = collectMonitoringContradictions(model({
      provenanceLabel: 'partial_failure',
      endpointProvenance: {
        runtimeStatus: 'degraded',
        investigationTimeline: 'degraded',
      },
    }));

    expect(contradictions).toEqual([
      'Partial failure provenance requires at least one endpoint to report partial_failure.',
    ]);
  });

  test('flags stale snapshot provenance without stale freshness or endpoint state', async () => {
    const contradictions = collectMonitoringContradictions(model({
      provenanceLabel: 'stale_snapshot',
      telemetryState: 'fresh',
      pollState: 'fresh',
      heartbeatState: 'fresh',
      endpointProvenance: {
        runtimeStatus: 'degraded',
        investigationTimeline: 'degraded',
      },
    }));

    expect(contradictions).toEqual([
      'stale_snapshot provenance requires stale freshness telemetry or an endpoint stale_snapshot marker.',
    ]);
  });

  test('accepts coherent live provenance combinations', async () => {
    const contradictions = collectMonitoringContradictions(model({
      provenanceLabel: 'live',
      telemetryState: 'fresh',
      pollState: 'fresh',
      heartbeatState: 'fresh',
      endpointProvenance: {
        runtimeStatus: 'live',
        investigationTimeline: 'live',
      },
      presentationStatus: 'live',
    }));

    expect(contradictions).toEqual([]);
  });
});
