import { expect, test } from '@playwright/test';

import { evaluateContinuitySlo } from '../app/threat-operations-panel';

test('continuity SLO passes when all dimensions are within thresholds', () => {
  const result = evaluateContinuitySlo({
    workspace_configured: true,
    runtime_status: 'live',
    monitoring_status: 'live',
    last_poll_at: null,
    last_heartbeat_at: null,
    last_telemetry_at: null,
    telemetry_freshness: 'fresh',
    confidence: 'high',
    reporting_systems_count: 1,
    monitored_systems_count: 1,
    protected_assets_count: 1,
    active_alerts_count: 0,
    active_incidents_count: 0,
    evidence_source_summary: 'live',
    contradiction_flags: [],
    guard_flags: [],
    status_reason: null,
    continuity_slo_pass: true,
    heartbeat_age_seconds: 120,
    event_ingestion_age_seconds: 90,
    detection_eval_age_seconds: 240,
    required_thresholds_seconds: {
      heartbeat: 180,
      event_ingestion: 120,
      detection_eval: 300,
    },
  });

  expect(result.statusLabel).toBe('PASS');
  expect(result.pass).toBeTruthy();
  expect(result.dimensions.every((dimension) => dimension.pass)).toBeTruthy();
});

test('continuity SLO fails when controlled timestamp ages breach thresholds', () => {
  const result = evaluateContinuitySlo({
    workspace_configured: true,
    runtime_status: 'degraded',
    monitoring_status: 'limited',
    last_poll_at: null,
    last_heartbeat_at: null,
    last_telemetry_at: null,
    telemetry_freshness: 'stale',
    confidence: 'low',
    reporting_systems_count: 1,
    monitored_systems_count: 1,
    protected_assets_count: 1,
    active_alerts_count: 0,
    active_incidents_count: 0,
    evidence_source_summary: 'live',
    contradiction_flags: [],
    guard_flags: [],
    status_reason: 'runtime_status_degraded:timestamp_stale',
    continuity_slo_pass: false,
    heartbeat_age_seconds: 181,
    event_ingestion_age_seconds: 121,
    detection_eval_age_seconds: 301,
    required_thresholds_seconds: {
      heartbeat: 180,
      event_ingestion: 120,
      detection_eval: 300,
    },
  });

  expect(result.statusLabel).toBe('FAIL');
  expect(result.pass).toBeFalsy();
  expect(result.dimensions.map((dimension) => dimension.pass)).toEqual([false, false, false]);
  expect(result.dimensions.map((dimension) => dimension.reason)).toEqual([
    '3m 1s exceeds 3m',
    '2m 1s exceeds 2m',
    '5m 1s exceeds 5m',
  ]);
});
