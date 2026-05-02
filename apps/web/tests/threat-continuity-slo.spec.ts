import { expect, test } from '@playwright/test';

import { continuityFailedChecks, evaluateContinuitySlo, pageStatePrimaryCopy } from '../app/threat-operations-panel';

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
  expect(copy).toContain('Monitoring continuity needs attention:');
  expect(copy).toContain('Worker heartbeat: 3m 1s exceeds 3m');
  expect(copy).toContain('Telemetry ingestion: 2m 1s exceeds 2m');
  expect(copy).toContain('Detection evaluation: 5m 1s exceeds 5m');
});

test('configured-no-signals copy includes remediation links when explicit failed checks are provided', () => {
  const copy = pageStatePrimaryCopy(
    'configured_no_signals',
    null,
    'continuous_no_evidence',
    undefined,
    [
      { code: 'event_ingestion_stale', label: 'Telemetry ingestion' },
      { code: 'heartbeat_stale', label: 'Worker heartbeat' },
    ],
    {
      event_ingestion_stale: '/threat#telemetry-freshness',
      heartbeat_stale: '/threat#continuity-slo',
    },
  );
  expect(copy).toContain('Monitoring continuity needs attention: Telemetry ingestion, Worker heartbeat.');
  expect(copy).toContain('Remediation: /threat#telemetry-freshness · /threat#continuity-slo.');
});

test('continuity SLO evaluator reads runtime continuity payload for stale/offline transitions', () => {
  const stale = evaluateContinuitySlo(
    undefined,
    {
      pass: false,
      heartbeat_age_seconds: 181,
      telemetry_age_seconds: 121,
      detection_age_seconds: 301,
      thresholds_seconds: { heartbeat: 180, telemetry: 120, detection_eval: 300 },
    },
  );
  const offline = evaluateContinuitySlo(
    undefined,
    {
      pass: false,
      heartbeat_age_seconds: null,
      telemetry_age_seconds: null,
      detection_age_seconds: null,
      thresholds_seconds: { heartbeat: 180, telemetry: 120, detection_eval: 300 },
    },
  );

  expect(stale.statusLabel).toBe('FAIL');
  expect(stale.dimensions.map((dimension) => dimension.pass)).toEqual([false, false, false]);
  expect(offline.statusLabel).toBe('FAIL');
  expect(offline.dimensions.map((dimension) => dimension.reason)).toEqual([
    'timestamp missing',
    'timestamp missing',
    'timestamp missing',
  ]);
});

test('continuity SLO evaluator uses worker heartbeat age when provided in continuity payload', () => {
  const result = evaluateContinuitySlo(
    undefined,
    {
      pass: false,
      worker_heartbeat_age_seconds: 301,
      heartbeat_age_seconds: 10,
      telemetry_age_seconds: 30,
      detection_age_seconds: 30,
      thresholds_seconds: { heartbeat: 300, telemetry: 120, detection_eval: 300 },
    },
  );

  expect(result.statusLabel).toBe('FAIL');
  expect(result.dimensions[0]).toMatchObject({
    key: 'heartbeat',
    pass: false,
    reason: '5m 1s exceeds 5m',
  });
});

test('continuity failed checks expose explicit codes for live/stale/offline/degraded transitions', () => {
  const live = continuityFailedChecks(
    { continuity_reason_codes: [] } as any,
    { pass: true, reason_codes: [] },
    { pass: true, statusLabel: 'PASS', dimensions: [] },
  );
  const stale = continuityFailedChecks(
    { continuity_reason_codes: ['event_ingestion_stale'] } as any,
    { pass: false, reason_codes: ['event_ingestion_stale'] },
  );
  const offline = continuityFailedChecks(
    { continuity_reason_codes: ['worker_not_live', 'heartbeat_offline'] } as any,
    { pass: false, reason_codes: ['worker_not_live', 'heartbeat_offline'] },
  );
  const degraded = continuityFailedChecks(
    { continuity_reason_codes: ['detection_pipeline_stale'] } as any,
    { pass: false, reason_codes: ['detection_pipeline_stale'] },
  );

  expect(live).toEqual([]);
  expect(stale).toEqual([{ code: 'event_ingestion_stale', label: 'Telemetry ingestion' }]);
  expect(offline).toEqual([
    { code: 'worker_not_live', label: 'worker not live' },
    { code: 'heartbeat_offline', label: 'Worker heartbeat' },
  ]);
  expect(degraded).toEqual([{ code: 'detection_pipeline_stale', label: 'Detection evaluation' }]);
});

test('continuity failed checks include concrete breach details when runtime payload provides them', () => {
  const failed = continuityFailedChecks(
    {
      continuity_breach_reasons: [
        {
          code: 'event_ingestion_stale',
          check: 'telemetry_freshness',
          state: 'stale',
          age_seconds: 901,
          threshold_seconds: 300,
        },
      ],
    } as any,
    {
      pass: false,
      breach_reasons: [
        {
          code: 'event_ingestion_stale',
          check: 'telemetry_freshness',
          state: 'stale',
          age_seconds: 901,
          threshold_seconds: 300,
        },
      ],
    },
  );

  expect(failed).toEqual([
    {
      code: 'event_ingestion_stale',
      label: 'Telemetry ingestion',
      detail: 'age 15m 1s · threshold 5m · state stale',
    },
  ]);
});
