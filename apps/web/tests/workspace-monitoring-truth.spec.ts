import { expect, test } from '@playwright/test';

import type { MonitoringRuntimeStatus } from '../app/monitoring-status-contract';
import {
  hasLiveTelemetry,
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
      last_poll_at: '2026-04-15T10:00:00Z',
      last_heartbeat_at: '2026-04-15T10:00:00Z',
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

    expect(truth.contradiction_flags).toContain('healthy_without_reporting_systems');
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
    expect(monitoringHealthyCopyAllowed(truth)).toBeTruthy();
  });
});
