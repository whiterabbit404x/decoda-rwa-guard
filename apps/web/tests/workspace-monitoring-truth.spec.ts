import { expect, test } from '@playwright/test';

import type { MonitoringRuntimeStatus } from '../app/monitoring-status-contract';
import {
  hasLiveTelemetry,
  hasRealTelemetryBackedChain,
  monitoringHealthyCopyAllowed,
  resolveWorkspaceMonitoringTruth,
} from '../app/workspace-monitoring-truth';

function runtimeWithSummary(summary: MonitoringRuntimeStatus['workspace_monitoring_summary']): MonitoringRuntimeStatus {
  return {
    mode: 'OFFLINE',
    workspace_monitoring_summary: summary,
  } as MonitoringRuntimeStatus;
}

test.describe('workspace monitoring truth guardrails', () => {
  test('heartbeat without telemetry never yields live telemetry copy', () => {
    const truth = resolveWorkspaceMonitoringTruth(runtimeWithSummary({
      workspace_configured: true,
      monitoring_mode: 'live',
      runtime_status: 'healthy',
      configured_systems: 1,
      reporting_systems: 1,
      protected_assets: 1,
      coverage_state: { configured_systems: 1, reporting_systems: 1, protected_assets: 1 },
      freshness_status: 'fresh',
      confidence_status: 'high',
      last_poll_at: null,
      last_heartbeat_at: '2026-04-15T10:00:00Z',
      last_telemetry_at: null,
      last_detection_at: null,
      evidence_source: 'live',
      status_reason: null,
      contradiction_flags: [],
    }));

    expect(truth.contradiction_flags).toContain('heartbeat_without_telemetry_timestamp');
    expect(hasLiveTelemetry(truth)).toBeFalsy();
  });

  test('poll without telemetry never yields live telemetry copy', () => {
    const truth = resolveWorkspaceMonitoringTruth(runtimeWithSummary({
      workspace_configured: true,
      monitoring_mode: 'live',
      runtime_status: 'healthy',
      configured_systems: 2,
      reporting_systems: 1,
      protected_assets: 2,
      coverage_state: { configured_systems: 2, reporting_systems: 1, protected_assets: 2 },
      freshness_status: 'fresh',
      confidence_status: 'high',
      last_poll_at: '2026-04-15T10:00:00Z',
      last_heartbeat_at: null,
      last_telemetry_at: null,
      last_detection_at: null,
      evidence_source: 'live',
      status_reason: null,
      contradiction_flags: [],
    }));

    expect(truth.contradiction_flags).toContain('poll_without_telemetry_timestamp');
    expect(hasLiveTelemetry(truth)).toBeFalsy();
  });

  test('offline plus telemetry timestamp produces contradiction and blocks live telemetry', () => {
    const truth = resolveWorkspaceMonitoringTruth(runtimeWithSummary({
      workspace_configured: true,
      monitoring_mode: 'live',
      runtime_status: 'offline',
      configured_systems: 2,
      reporting_systems: 1,
      protected_assets: 2,
      coverage_state: { configured_systems: 2, reporting_systems: 1, protected_assets: 2 },
      freshness_status: 'fresh',
      confidence_status: 'high',
      last_poll_at: null,
      last_heartbeat_at: null,
      last_telemetry_at: '2026-04-15T09:59:30Z',
      last_detection_at: null,
      evidence_source: 'live',
      status_reason: null,
      contradiction_flags: [],
    }));

    expect(truth.contradiction_flags).toContain('offline_with_current_telemetry');
    expect(hasLiveTelemetry(truth)).toBeFalsy();
  });

  test('workspace unconfigured plus coverage over zero yields contradiction', () => {
    const truth = resolveWorkspaceMonitoringTruth(runtimeWithSummary({
      workspace_configured: false,
      monitoring_mode: 'offline',
      runtime_status: 'offline',
      configured_systems: 1,
      monitored_systems_count: 1,
      reporting_systems: 0,
      protected_assets: 1,
      protected_assets_count: 1,
      coverage_state: { configured_systems: 1, reporting_systems: 0, protected_assets: 1 },
      freshness_status: 'unavailable',
      confidence_status: 'unavailable',
      last_poll_at: null,
      last_heartbeat_at: null,
      last_telemetry_at: null,
      last_detection_at: null,
      evidence_source: 'none',
      status_reason: 'workspace_not_configured',
      contradiction_flags: [],
    }));

    expect(truth.contradiction_flags).toContain('workspace_unconfigured_with_coverage');
    expect(hasLiveTelemetry(truth)).toBeFalsy();
  });

  test('zero reporting systems cannot render healthy/live copy', () => {
    const truth = resolveWorkspaceMonitoringTruth(runtimeWithSummary({
      workspace_configured: true,
      monitoring_mode: 'live',
      runtime_status: 'healthy',
      configured_systems: 3,
      reporting_systems: 0,
      protected_assets: 3,
      coverage_state: { configured_systems: 3, reporting_systems: 0, protected_assets: 3 },
      freshness_status: 'fresh',
      confidence_status: 'high',
      last_poll_at: '2026-04-15T10:00:00Z',
      last_heartbeat_at: '2026-04-15T10:00:00Z',
      last_telemetry_at: '2026-04-15T09:59:30Z',
      last_detection_at: null,
      evidence_source: 'live',
      status_reason: null,
      contradiction_flags: [],
    }));

    expect(truth.contradiction_flags).toContain('live_monitoring_without_reporting_systems');
    expect(truth.guard_flags).toContain('live_monitoring_without_reporting_systems');
    expect(hasLiveTelemetry(truth)).toBeFalsy();
    expect(monitoringHealthyCopyAllowed(truth)).toBeFalsy();
  });

  test('contradiction flags always suppress healthy monitoring copy', () => {
    const truth = resolveWorkspaceMonitoringTruth(runtimeWithSummary({
      workspace_configured: true,
      monitoring_mode: 'live',
      runtime_status: 'healthy',
      configured_systems: 2,
      reporting_systems: 2,
      protected_assets: 2,
      coverage_state: { configured_systems: 2, reporting_systems: 2, protected_assets: 2 },
      freshness_status: 'fresh',
      confidence_status: 'high',
      last_poll_at: '2026-04-15T10:00:00Z',
      last_heartbeat_at: '2026-04-15T10:00:00Z',
      last_telemetry_at: '2026-04-15T09:59:30Z',
      last_detection_at: null,
      evidence_source: 'live',
      status_reason: null,
      contradiction_flags: ['offline_with_current_telemetry'],
    }));

    expect(truth.contradiction_flags).toContain('offline_with_current_telemetry');
    expect(hasLiveTelemetry(truth)).toBeFalsy();
    expect(monitoringHealthyCopyAllowed(truth)).toBeFalsy();
  });

  test('configured flag with missing required linkage counts is contradicted', () => {
    const truth = resolveWorkspaceMonitoringTruth(runtimeWithSummary({
      workspace_configured: true,
      monitoring_mode: 'live',
      runtime_status: 'healthy',
      configured_systems: 2,
      reporting_systems: 2,
      protected_assets: 2,
      coverage_state: { configured_systems: 2, reporting_systems: 2, protected_assets: 2 },
      freshness_status: 'fresh',
      confidence_status: 'high',
      last_poll_at: '2026-04-15T10:00:00Z',
      last_heartbeat_at: '2026-04-15T10:00:00Z',
      last_telemetry_at: '2026-04-15T09:59:30Z',
      last_detection_at: null,
      evidence_source: 'live',
      status_reason: null,
      configuration_reason: null,
      valid_protected_asset_count: 1,
      linked_monitored_system_count: 1,
      persisted_enabled_config_count: 0,
      valid_target_system_link_count: 1,
      contradiction_flags: [],
    }));

    expect(truth.contradiction_flags).toContain('workspace_configured_missing_required_links');
    expect(hasLiveTelemetry(truth)).toBeFalsy();
  });

  test('fresh live coverage telemetry supports live runtime proof even when target-event telemetry is absent', () => {
    const truth = resolveWorkspaceMonitoringTruth(runtimeWithSummary({
      workspace_configured: true,
      monitoring_mode: 'live',
      runtime_status: 'healthy',
      configured_systems: 2,
      reporting_systems: 2,
      protected_assets: 2,
      coverage_state: { configured_systems: 2, reporting_systems: 2, protected_assets: 2 },
      freshness_status: 'fresh',
      confidence_status: 'high',
      last_poll_at: null,
      last_heartbeat_at: null,
      last_telemetry_at: null,
      last_coverage_telemetry_at: '2026-04-15T09:59:30Z',
      last_detection_at: null,
      evidence_source: 'live',
      status_reason: null,
      valid_protected_asset_count: 1,
      linked_monitored_system_count: 1,
      persisted_enabled_config_count: 1,
      valid_target_system_link_count: 1,
      contradiction_flags: [],
    }));

    expect(hasLiveTelemetry(truth)).toBeTruthy();
  });

  test('healthy runtime payload with unavailable confidence never yields live telemetry proof', () => {
    const truth = resolveWorkspaceMonitoringTruth(runtimeWithSummary({
      workspace_configured: true,
      monitoring_mode: 'live',
      runtime_status: 'healthy',
      configured_systems: 2,
      reporting_systems: 2,
      protected_assets: 2,
      coverage_state: { configured_systems: 2, reporting_systems: 2, protected_assets: 2 },
      freshness_status: 'fresh',
      confidence_status: 'unavailable',
      last_poll_at: null,
      last_heartbeat_at: null,
      last_telemetry_at: null,
      last_coverage_telemetry_at: '2026-04-15T09:59:30Z',
      last_detection_at: null,
      evidence_source: 'live',
      status_reason: 'monitoring_confidence_unavailable',
      contradiction_flags: [],
    }));

    expect(hasLiveTelemetry(truth)).toBeFalsy();
  });

  test('telemetry unavailable plus high confidence is always contradicted', () => {
    const truth = resolveWorkspaceMonitoringTruth(runtimeWithSummary({
      workspace_configured: true,
      runtime_status: 'healthy',
      monitoring_status: 'active',
      reporting_systems_count: 2,
      monitored_systems_count: 2,
      protected_assets_count: 2,
      telemetry_freshness: 'unavailable',
      confidence: 'high',
      last_poll_at: '2026-04-15T10:00:00Z',
      last_heartbeat_at: '2026-04-15T10:00:00Z',
      last_telemetry_at: null,
      active_alerts_count: 0,
      active_incidents_count: 0,
      evidence_source_summary: 'live',
      status_reason: null,
      contradiction_flags: [],
    }));

    expect(truth.contradiction_flags).toContain('telemetry_unavailable_with_high_confidence');
    expect(truth.status_reason).toContain('guard:');
    expect(hasLiveTelemetry(truth)).toBeFalsy();
  });

  test('db failure reason suppresses live telemetry and healthy copy allowances', () => {
    const truth = resolveWorkspaceMonitoringTruth(runtimeWithSummary({
      workspace_configured: true,
      runtime_status: 'healthy',
      monitoring_status: 'live',
      reporting_systems_count: 2,
      monitored_systems_count: 2,
      protected_assets_count: 2,
      telemetry_freshness: 'fresh',
      confidence: 'high',
      last_poll_at: '2026-04-15T10:00:00Z',
      last_heartbeat_at: '2026-04-15T10:00:00Z',
      last_telemetry_at: '2026-04-15T09:59:30Z',
      evidence_source_summary: 'live',
      status_reason: 'Database unavailable',
      db_failure_classification: 'unavailable',
      db_failure_reason: 'Database unavailable',
      contradiction_flags: [],
      guard_flags: [],
      active_alerts_count: 0,
      active_incidents_count: 0,
    }));

    expect(truth.db_failure_reason).toBe('Database unavailable');
    expect(hasLiveTelemetry(truth)).toBeFalsy();
    expect(monitoringHealthyCopyAllowed(truth)).toBeFalsy();
  });

  test('live telemetry verification with missing timestamp is contradicted', () => {
    const truth = resolveWorkspaceMonitoringTruth(runtimeWithSummary({
      workspace_configured: true,
      runtime_status: 'healthy',
      monitoring_status: 'active',
      reporting_systems_count: 2,
      monitored_systems_count: 2,
      protected_assets_count: 2,
      telemetry_freshness: 'fresh',
      confidence: 'high',
      last_poll_at: '2026-04-15T10:00:00Z',
      last_heartbeat_at: '2026-04-15T10:00:00Z',
      last_telemetry_at: null,
      active_alerts_count: 0,
      active_incidents_count: 0,
      evidence_source_summary: 'live',
      status_reason: null,
      contradiction_flags: [],
    }));

    expect(truth.contradiction_flags).toContain('live_telemetry_verified_without_timestamp');
    expect(monitoringHealthyCopyAllowed(truth)).toBeFalsy();
  });

  test('idle runtime with fresh/high live telemetry is always contradicted', () => {
    const contradicted = resolveWorkspaceMonitoringTruth(runtimeWithSummary({
      workspace_configured: true,
      runtime_status: 'idle',
      monitoring_status: 'idle',
      reporting_systems_count: 1,
      monitored_systems_count: 1,
      protected_assets_count: 1,
      telemetry_freshness: 'fresh',
      confidence: 'high',
      last_poll_at: '2026-04-15T10:00:00Z',
      last_heartbeat_at: '2026-04-15T10:00:00Z',
      last_telemetry_at: '2026-04-15T09:59:30Z',
      active_alerts_count: 0,
      active_incidents_count: 0,
      evidence_source_summary: 'live',
      status_reason: null,
      contradiction_flags: [],
    }));
    expect(contradicted.contradiction_flags).toContain('idle_runtime_with_active_monitoring_claim');
    expect(contradicted.guard_flags).toContain('idle_runtime_with_active_monitoring_claim');
    expect(contradicted.status_reason).toBe('guard:idle_runtime_with_active_monitoring_claim');
  });

  test('idle runtime with explicit degraded reason does not trigger healthy-idle contradiction', () => {
    const truth = resolveWorkspaceMonitoringTruth(runtimeWithSummary({
      workspace_configured: true,
      runtime_status: 'idle',
      monitoring_status: 'limited',
      reporting_systems_count: 1,
      monitored_systems_count: 1,
      protected_assets_count: 1,
      telemetry_freshness: 'fresh',
      confidence: 'high',
      last_poll_at: '2026-04-15T10:00:00Z',
      last_heartbeat_at: '2026-04-15T10:00:00Z',
      last_telemetry_at: '2026-04-15T09:59:30Z',
      active_alerts_count: 0,
      active_incidents_count: 0,
      evidence_source_summary: 'live',
      status_reason: 'runtime_status_degraded:database_error',
      contradiction_flags: [],
      guard_flags: [],
    }));

    expect(truth.contradiction_flags).not.toContain('idle_runtime_with_active_monitoring_claim');
    expect(truth.status_reason).toBe('runtime_status_degraded:database_error');
  });

  test('status reason uses deterministic hard-guard priority', () => {
    const truth = resolveWorkspaceMonitoringTruth(runtimeWithSummary({
      workspace_configured: true,
      runtime_status: 'offline',
      monitoring_status: 'limited',
      reporting_systems_count: 0,
      monitored_systems_count: 1,
      protected_assets_count: 1,
      telemetry_freshness: 'fresh',
      confidence: 'high',
      last_poll_at: '2026-04-15T10:00:00Z',
      last_heartbeat_at: '2026-04-15T10:00:00Z',
      last_telemetry_at: null,
      active_alerts_count: 0,
      active_incidents_count: 0,
      evidence_source_summary: 'live',
      status_reason: null,
      contradiction_flags: [],
      guard_flags: [],
    }));

    expect(truth.guard_flags).toEqual(expect.arrayContaining([
      'offline_with_current_telemetry',
      'live_monitoring_without_reporting_systems',
      'live_telemetry_verified_without_timestamp',
    ]));
    expect(truth.status_reason).toBe('guard:offline_with_current_telemetry');
  });

  test('real telemetry-backed chain requires continuous live continuity but not incidents', () => {
    const noLinkedAnomaly = resolveWorkspaceMonitoringTruth(runtimeWithSummary({
      workspace_configured: true,
      runtime_status: 'live',
      monitoring_status: 'live',
      reporting_systems_count: 1,
      monitored_systems_count: 1,
      protected_assets_count: 1,
      telemetry_freshness: 'fresh',
      confidence: 'high',
      last_poll_at: '2026-04-15T10:00:00Z',
      last_heartbeat_at: '2026-04-15T10:00:00Z',
      last_telemetry_at: '2026-04-15T09:59:30Z',
      evidence_source_summary: 'live',
      contradiction_flags: [],
      guard_flags: [],
      active_alerts_count: 0,
      active_incidents_count: 0,
      continuity_status: 'degraded',
      status_reason: null,
    }));
    expect(hasLiveTelemetry(noLinkedAnomaly)).toBeTruthy();
    expect(hasRealTelemetryBackedChain(noLinkedAnomaly)).toBeFalsy();

    const continuousLiveNoIncidents = resolveWorkspaceMonitoringTruth(runtimeWithSummary({
      ...noLinkedAnomaly,
      continuity_status: 'continuous_live',
      active_alerts_count: 0,
      active_incidents_count: 0,
    }));
    expect(hasRealTelemetryBackedChain(continuousLiveNoIncidents)).toBeTruthy();
  });

  test('healthy/live stakeholder copy is allowed with verified telemetry and continuous live continuity even without incidents', () => {
    const truth = resolveWorkspaceMonitoringTruth(runtimeWithSummary({
      workspace_configured: true,
      runtime_status: 'live',
      monitoring_status: 'live',
      reporting_systems_count: 2,
      monitored_systems_count: 2,
      protected_assets_count: 2,
      telemetry_freshness: 'fresh',
      confidence: 'high',
      last_poll_at: '2026-04-15T10:00:00Z',
      last_heartbeat_at: '2026-04-15T10:00:00Z',
      last_telemetry_at: '2026-04-15T09:59:30Z',
      evidence_source_summary: 'live',
      contradiction_flags: [],
      guard_flags: [],
      active_alerts_count: 0,
      active_incidents_count: 0,
      continuity_status: 'continuous_live',
      status_reason: null,
    }));
    expect(monitoringHealthyCopyAllowed(truth)).toBeTruthy();
    expect(truth.monitoring_status).toBe('live');
    expect(truth.runtime_status).toBe('live');
  });
});
