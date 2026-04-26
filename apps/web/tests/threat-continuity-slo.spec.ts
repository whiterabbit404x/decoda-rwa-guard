import { expect, test } from '@playwright/test';

import { evaluateContinuitySlo, pageStatePrimaryCopy } from '../app/threat-operations-panel';

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
    telemetry_age_seconds: 90,
    detection_eval_age_seconds: 240,
    thresholds_seconds: {
      heartbeat: 180,
      telemetry: 120,
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
    telemetry_age_seconds: 121,
    detection_eval_age_seconds: 301,
    thresholds_seconds: {
      heartbeat: 180,
      telemetry: 120,
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

test('configured-no-signals copy explicitly reports SLO FAIL reasons', () => {
  const continuity = evaluateContinuitySlo({
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
    status_reason: 'runtime_status_degraded:continuity_slo_failed:heartbeat_stale',
    continuity_slo_pass: false,
    heartbeat_age_seconds: 181,
    telemetry_age_seconds: 121,
    detection_eval_age_seconds: 301,
    thresholds_seconds: {
      heartbeat: 180,
      telemetry: 120,
      detection_eval: 300,
    },
  });
  const copy = pageStatePrimaryCopy('configured_no_signals', null, 'continuous_no_evidence', continuity);
  expect(copy).toContain('Continuity SLO FAIL.');
  expect(copy).toContain('Worker heartbeat');
  expect(copy).toContain('Telemetry ingestion');
  expect(copy).toContain('Detection evaluation');
});
