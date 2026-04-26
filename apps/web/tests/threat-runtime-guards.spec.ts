import { expect, test } from '@playwright/test';

import {
  derivePageState,
  formatSystemsPanelWarning,
  formatOperationalStateLabel,
  hasRuntimeQueryFailureMarker,
  pageStatePrimaryCopy,
} from '../app/threat-operations-panel';
import { hasLiveTelemetry, resolveWorkspaceMonitoringTruth } from '../app/workspace-monitoring-truth';
import type { MonitoringRuntimeStatus } from '../app/monitoring-status-contract';

test.describe('threat runtime guards', () => {
  test('handles undefined operational state labels without crashing', async () => {
    expect(formatOperationalStateLabel(undefined)).toBe('unknown');
    expect(formatOperationalStateLabel('offline_no_telemetry')).toBe('offline no telemetry');
  });

  test('defensively parses null and undefined runtime summary fields', async () => {
    const truth = resolveWorkspaceMonitoringTruth({
      workspace_slug: null,
      workspace_name: undefined,
      status_reason: undefined,
      workspace_monitoring_summary: {
        workspace_configured: true,
        monitoring_mode: 'live',
        runtime_status: 'healthy',
        configured_systems: 1,
        reporting_systems: 1,
        protected_assets: 1,
        coverage_state: { configured_systems: 1, reporting_systems: 1, protected_assets: 1 },
        freshness_status: 'fresh',
        confidence_status: 'high',
        last_heartbeat_at: 'not-a-date',
        last_telemetry_at: null,
        last_poll_at: undefined as unknown as string | null,
        last_detection_at: undefined as unknown as string | null,
        evidence_source: 'live',
        status_reason: undefined as unknown as string,
        contradiction_flags: [],
      },
    } as MonitoringRuntimeStatus);

    expect(truth.workspace_slug).toBeNull();
    expect(truth.workspace_name).toBeNull();
    expect(truth.last_heartbeat_at).toBeNull();
    expect(truth.last_telemetry_at).toBeNull();
    expect(truth.status_reason).toBeNull();
  });

  test('returns safe offline truth when runtime payload is missing', async () => {
    const truth = resolveWorkspaceMonitoringTruth(null);

    expect(truth.workspace_configured).toBe(false);
    expect(truth.runtime_status).toBe('offline');
    expect(truth.freshness_status).toBe('unavailable');
  });

  test('maps backend query failure payload to degraded backend-error state', async () => {
    const payload: MonitoringRuntimeStatus = {
      monitoring_status: 'error',
      error: {
        code: 'runtime_status_db_error',
        type: 'SyntaxError',
        message: 'syntax error at or near "$1"',
        stage: 'query',
        hint: 'Verify runtime status SQL compatibility.',
      },
      status_reason: 'runtime_status_degraded:database_error',
      configuration_reason: 'runtime_status_unavailable',
      configuration_reason_codes: ['runtime_status_unavailable'],
      field_reason_codes: {
        protected_assets: ['query_failure'],
        configured_systems: ['query_failure'],
      },
      workspace_monitoring_summary: {
        workspace_configured: false,
        monitoring_mode: 'unavailable',
        runtime_status: 'failed',
        configured_systems: 0,
        reporting_systems: 0,
        protected_assets: 0,
        coverage_state: { configured_systems: 0, reporting_systems: 0, protected_assets: 0 },
        freshness_status: 'unavailable',
        confidence_status: 'unavailable',
        last_heartbeat_at: null,
        last_telemetry_at: null,
        last_poll_at: null,
        last_detection_at: null,
        evidence_source: 'none',
        status_reason: 'runtime_status_degraded:database_error',
        configuration_reason: 'runtime_status_unavailable',
        configuration_reason_codes: ['runtime_status_unavailable'],
        contradiction_flags: [],
      },
    };

    const truth = resolveWorkspaceMonitoringTruth(payload);
    const queryFailure = hasRuntimeQueryFailureMarker({
      statusReason: payload.status_reason,
      configurationReason: payload.configuration_reason,
      configurationReasonCodes: payload.configuration_reason_codes,
      runtimeErrorCode: payload.error?.code,
      runtimeDegradedReason: payload.degraded_reason,
      runtimeMonitoringStatus: payload.monitoring_status,
      fieldReasonCodes: payload.field_reason_codes,
      summaryStatusReason: payload.workspace_monitoring_summary?.status_reason,
      summaryConfigurationReason: payload.workspace_monitoring_summary?.configuration_reason,
      summaryConfigurationReasonCodes: payload.workspace_monitoring_summary?.configuration_reason_codes ?? [],
    });
    const state = derivePageState({
      loadingSnapshot: false,
      snapshotError: false,
      targets: [],
      liveDetections: [],
      workspaceConfigured: truth.workspace_configured,
      freshnessStatus: truth.freshness_status,
      contradictionFlags: truth.contradiction_flags,
      reportingSystems: truth.reporting_systems,
      runtimeStatus: truth.runtime_status,
      monitoredSystems: truth.monitored_systems_count,
      hasLiveTelemetry: false,
      statusReason: truth.status_reason,
      configurationReason: payload.configuration_reason,
      configurationReasonCodes: payload.configuration_reason_codes,
      runtimeErrorCode: payload.error?.code,
      runtimeDegradedReason: payload.degraded_reason,
      runtimeMonitoringStatus: payload.monitoring_status,
    });

    expect(queryFailure).toBe(true);
    expect(state).toBe('fetch_error');
    expect(pageStatePrimaryCopy(state, payload.configuration_reason)).toContain('Backend telemetry/runtime retrieval failed');
    expect(pageStatePrimaryCopy(state, payload.configuration_reason).toLowerCase()).not.toContain('workspace is not configured');
  });

  test('maps true config-invalid payload to workspace-not-configured state', async () => {
    const payload: MonitoringRuntimeStatus = {
      monitoring_status: 'idle',
      status_reason: 'workspace_configuration_invalid:no_valid_protected_assets',
      configuration_reason: 'no_valid_protected_assets',
      configuration_reason_codes: [
        'no_valid_protected_assets',
        'no_linked_monitored_systems',
      ],
      workspace_monitoring_summary: {
        workspace_configured: false,
        monitoring_mode: 'live',
        runtime_status: 'idle',
        configured_systems: 0,
        reporting_systems: 0,
        protected_assets: 0,
        coverage_state: { configured_systems: 0, reporting_systems: 0, protected_assets: 0 },
        freshness_status: 'unavailable',
        confidence_status: 'unavailable',
        last_heartbeat_at: null,
        last_telemetry_at: null,
        last_poll_at: null,
        last_detection_at: null,
        evidence_source: 'none',
        status_reason: 'workspace_configuration_invalid:no_valid_protected_assets',
        configuration_reason: 'no_valid_protected_assets',
        configuration_reason_codes: ['no_valid_protected_assets'],
        contradiction_flags: [],
      },
    };
    const truth = resolveWorkspaceMonitoringTruth(payload);
    const state = derivePageState({
      loadingSnapshot: false,
      snapshotError: false,
      targets: [],
      liveDetections: [],
      workspaceConfigured: truth.workspace_configured,
      freshnessStatus: truth.freshness_status,
      contradictionFlags: truth.contradiction_flags,
      reportingSystems: truth.reporting_systems,
      runtimeStatus: truth.runtime_status,
      monitoredSystems: truth.monitored_systems_count,
      hasLiveTelemetry: false,
      statusReason: truth.status_reason,
      configurationReason: payload.configuration_reason,
      configurationReasonCodes: payload.configuration_reason_codes,
      runtimeErrorCode: payload.error?.code,
      runtimeDegradedReason: payload.degraded_reason,
      runtimeMonitoringStatus: payload.monitoring_status,
      fieldReasonCodes: payload.field_reason_codes,
      summaryStatusReason: payload.workspace_monitoring_summary?.status_reason,
      summaryConfigurationReason: payload.workspace_monitoring_summary?.configuration_reason,
      summaryConfigurationReasonCodes: payload.workspace_monitoring_summary?.configuration_reason_codes ?? [],
    });

    expect(state).toBe('unconfigured_workspace');
    expect(pageStatePrimaryCopy(state, payload.configuration_reason)).toContain('Workspace is not configured');
  });

  test('treats structural configuration reason codes emitted by backend as unconfigured workspace', async () => {
    const structuralReasonCodes = [
      'no_valid_protected_assets',
      'no_linked_monitored_systems',
      'no_persisted_enabled_monitoring_config',
      'target_system_linkage_invalid',
    ];

    structuralReasonCodes.forEach((reasonCode) => {
      const state = derivePageState({
        loadingSnapshot: false,
        snapshotError: false,
        targets: [],
        liveDetections: [],
        workspaceConfigured: false,
        freshnessStatus: 'unavailable',
        contradictionFlags: [],
        reportingSystems: 0,
        runtimeStatus: 'idle',
        monitoredSystems: 0,
        hasLiveTelemetry: false,
        statusReason: `workspace_configuration_invalid:${reasonCode}`,
        configurationReason: reasonCode,
        configurationReasonCodes: [reasonCode],
        runtimeMonitoringStatus: 'limited',
        summaryStatusReason: `workspace_configuration_invalid:${reasonCode}`,
        summaryConfigurationReason: reasonCode,
        summaryConfigurationReasonCodes: [reasonCode],
      });

      expect(state).toBe('unconfigured_workspace');
      expect(pageStatePrimaryCopy(state, reasonCode)).toContain('Workspace is not configured');
    });
  });

  test('treats missing workspace identifiers with query-failure payload as fetch error', async () => {
    const payload: MonitoringRuntimeStatus = {
      workspace_slug: null,
      monitoring_status: 'error',
      configuration_reason: 'runtime_status_unavailable',
      configuration_reason_codes: ['runtime_status_unavailable'],
      field_reason_codes: {
        workspace_id: ['query_failure'],
        workspace_slug: ['query_failure'],
      },
      workspace_monitoring_summary: {
        workspace_configured: false,
        monitoring_mode: 'unavailable',
        runtime_status: 'failed',
        configured_systems: 0,
        reporting_systems: 0,
        protected_assets: 0,
        coverage_state: { configured_systems: 0, reporting_systems: 0, protected_assets: 0 },
        freshness_status: 'unavailable',
        confidence_status: 'unavailable',
        last_heartbeat_at: null,
        last_telemetry_at: null,
        last_poll_at: null,
        last_detection_at: null,
        evidence_source: 'none',
        status_reason: null,
        configuration_reason: 'runtime_status_unavailable',
        configuration_reason_codes: ['runtime_status_unavailable', 'workspace_slug_query_failure'],
        contradiction_flags: [],
      },
    } as MonitoringRuntimeStatus;

    const truth = resolveWorkspaceMonitoringTruth(payload);
    const state = derivePageState({
      loadingSnapshot: false,
      snapshotError: false,
      targets: [],
      liveDetections: [],
      workspaceConfigured: truth.workspace_configured,
      freshnessStatus: truth.freshness_status,
      contradictionFlags: truth.contradiction_flags,
      reportingSystems: truth.reporting_systems,
      runtimeStatus: truth.runtime_status,
      monitoredSystems: truth.monitored_systems_count,
      hasLiveTelemetry: false,
      statusReason: truth.status_reason,
      configurationReason: truth.configuration_reason,
      configurationReasonCodes: truth.configuration_reason_codes,
      runtimeErrorCode: payload.error?.code,
      runtimeDegradedReason: payload.degraded_reason,
      runtimeMonitoringStatus: payload.monitoring_status,
      fieldReasonCodes: payload.field_reason_codes,
      summaryStatusReason: payload.workspace_monitoring_summary?.status_reason,
      summaryConfigurationReason: payload.workspace_monitoring_summary?.configuration_reason,
      summaryConfigurationReasonCodes: payload.workspace_monitoring_summary?.configuration_reason_codes ?? [],
    });

    expect(state).toBe('fetch_error');
    expect(pageStatePrimaryCopy(state, truth.configuration_reason)).toContain('Backend telemetry/runtime retrieval failed');
  });

  test('preserves summary-level degraded reason codes and avoids config-blame copy', async () => {
    const payload: MonitoringRuntimeStatus = {
      monitoring_status: 'error',
      configuration_reason: 'runtime_status_unavailable',
      configuration_reason_codes: ['runtime_status_unavailable'],
      workspace_monitoring_summary: {
        workspace_configured: false,
        monitoring_mode: 'unavailable',
        runtime_status: 'failed',
        configured_systems: 0,
        reporting_systems: 0,
        protected_assets: 0,
        coverage_state: { configured_systems: 0, reporting_systems: 0, protected_assets: 0 },
        freshness_status: 'unavailable',
        confidence_status: 'unavailable',
        last_heartbeat_at: null,
        last_telemetry_at: null,
        last_poll_at: null,
        last_detection_at: null,
        evidence_source: 'none',
        status_reason: null,
        configuration_reason: 'runtime_status_unavailable',
        configuration_reason_codes: ['runtime_status_unavailable', 'configured_systems_query_failure'],
        contradiction_flags: [],
      },
    } as MonitoringRuntimeStatus;

    const truth = resolveWorkspaceMonitoringTruth(payload);
    expect(truth.configuration_reason_codes).toEqual([
      'runtime_status_unavailable',
      'configured_systems_query_failure',
    ]);

    const state = derivePageState({
      loadingSnapshot: false,
      snapshotError: false,
      targets: [],
      liveDetections: [],
      workspaceConfigured: truth.workspace_configured,
      freshnessStatus: truth.freshness_status,
      contradictionFlags: truth.contradiction_flags,
      reportingSystems: truth.reporting_systems,
      runtimeStatus: truth.runtime_status,
      monitoredSystems: truth.monitored_systems_count,
      hasLiveTelemetry: false,
      statusReason: truth.status_reason,
      configurationReason: truth.configuration_reason,
      configurationReasonCodes: truth.configuration_reason_codes,
      runtimeMonitoringStatus: payload.monitoring_status,
      summaryStatusReason: payload.workspace_monitoring_summary?.status_reason,
      summaryConfigurationReason: payload.workspace_monitoring_summary?.configuration_reason,
      summaryConfigurationReasonCodes: payload.workspace_monitoring_summary?.configuration_reason_codes ?? [],
    });

    expect(state).toBe('fetch_error');
    expect(pageStatePrimaryCopy(state, truth.configuration_reason).toLowerCase()).not.toContain('workspace is not configured');
  });

  test('healthy runtime labels do not open healthy page state when confidence is unavailable', async () => {
    const payload: MonitoringRuntimeStatus = {
      monitoring_status: 'active',
      status_reason: null,
      configuration_reason: null,
      configuration_reason_codes: [],
      workspace_monitoring_summary: {
        workspace_configured: true,
        monitoring_mode: 'live',
        runtime_status: 'healthy',
        configured_systems: 2,
        reporting_systems: 2,
        protected_assets: 2,
        coverage_state: { configured_systems: 2, reporting_systems: 2, protected_assets: 2 },
        freshness_status: 'fresh',
        confidence_status: 'unavailable',
        last_heartbeat_at: '2026-04-15T10:00:00Z',
        last_telemetry_at: null,
        last_coverage_telemetry_at: '2026-04-15T09:59:30Z',
        last_poll_at: '2026-04-15T10:00:00Z',
        last_detection_at: null,
        evidence_source: 'live',
        status_reason: null,
        contradiction_flags: [],
      },
    };
    const truth = resolveWorkspaceMonitoringTruth(payload);
    const state = derivePageState({
      loadingSnapshot: false,
      snapshotError: false,
      targets: [],
      liveDetections: [],
      workspaceConfigured: truth.workspace_configured,
      freshnessStatus: truth.freshness_status,
      contradictionFlags: truth.contradiction_flags,
      reportingSystems: truth.reporting_systems,
      runtimeStatus: truth.runtime_status,
      monitoredSystems: truth.monitored_systems_count,
      hasLiveTelemetry: hasLiveTelemetry(truth),
      statusReason: truth.status_reason,
      configurationReason: truth.configuration_reason,
      configurationReasonCodes: truth.configuration_reason_codes,
      runtimeErrorCode: payload.error?.code,
      runtimeDegradedReason: payload.degraded_reason,
      runtimeMonitoringStatus: payload.monitoring_status,
      summaryStatusReason: payload.workspace_monitoring_summary?.status_reason,
      summaryConfigurationReason: payload.workspace_monitoring_summary?.configuration_reason,
      summaryConfigurationReasonCodes: payload.workspace_monitoring_summary?.configuration_reason_codes ?? [],
    });

    expect(hasLiveTelemetry(truth)).toBeFalsy();
    expect(state).toBe('degraded_partial');
  });

  test('partial snapshot failure maps to fetch error while runtime is otherwise live', async () => {
    const state = derivePageState({
      loadingSnapshot: false,
      snapshotError: true,
      targets: [],
      liveDetections: [],
      workspaceConfigured: true,
      freshnessStatus: 'fresh',
      contradictionFlags: [],
      reportingSystems: 2,
      runtimeStatus: 'healthy',
      monitoredSystems: 2,
      hasLiveTelemetry: true,
      statusReason: null,
      configurationReason: null,
      configurationReasonCodes: [],
      runtimeMonitoringStatus: 'active',
      summaryStatusReason: null,
      summaryConfigurationReason: null,
      summaryConfigurationReasonCodes: [],
    });

    expect(state).toBe('fetch_error');
    expect(state).not.toBe('offline_no_telemetry');
    expect(pageStatePrimaryCopy(state, null)).toContain('Backend telemetry/runtime retrieval failed');
  });

  test('runtime healthy with snapshot endpoint failure shows fetch-error banner copy', async () => {
    const state = derivePageState({
      loadingSnapshot: false,
      snapshotError: true,
      targets: [],
      liveDetections: [],
      workspaceConfigured: true,
      freshnessStatus: 'fresh',
      contradictionFlags: [],
      reportingSystems: 2,
      runtimeStatus: 'healthy',
      monitoredSystems: 2,
      hasLiveTelemetry: true,
      statusReason: null,
      configurationReason: null,
      configurationReasonCodes: [],
      runtimeMonitoringStatus: 'active',
      summaryStatusReason: null,
      summaryConfigurationReason: null,
      summaryConfigurationReasonCodes: [],
    });

    expect(state).toBe('fetch_error');
    expect(pageStatePrimaryCopy(state, null)).toContain('Backend telemetry/runtime retrieval failed');
    expect(formatSystemsPanelWarning(['runtime-status'])).toBe('Runtime status unavailable');
  });

  test('runtime endpoint failure marker takes precedence and renders fetch-error copy', async () => {
    const state = derivePageState({
      loadingSnapshot: false,
      snapshotError: false,
      targets: [],
      liveDetections: [],
      workspaceConfigured: false,
      freshnessStatus: 'unavailable',
      monitoringStatus: 'limited',
      reportingSystems: 0,
      runtimeStatus: 'failed',
      monitoredSystems: 0,
      hasLiveTelemetry: false,
      statusReason: 'runtime_status_degraded:database_error',
      configurationReason: 'runtime_status_unavailable',
      configurationReasonCodes: ['runtime_status_unavailable'],
      runtimeErrorCode: 'runtime_status_db_error',
      runtimeMonitoringStatus: 'error',
      summaryStatusReason: null,
      summaryConfigurationReason: null,
      summaryConfigurationReasonCodes: [],
    });

    expect(state).toBe('fetch_error');
    expect(pageStatePrimaryCopy(state, 'runtime_status_unavailable')).toContain('Backend telemetry/runtime retrieval failed');
  });

  test('structural configuration reasons map to unconfigured workspace copy', async () => {
    const state = derivePageState({
      loadingSnapshot: false,
      snapshotError: false,
      targets: [],
      liveDetections: [],
      workspaceConfigured: true,
      freshnessStatus: 'unavailable',
      monitoringStatus: 'live',
      reportingSystems: 0,
      runtimeStatus: 'live',
      monitoredSystems: 0,
      hasLiveTelemetry: false,
      statusReason: null,
      configurationReason: null,
      configurationReasonCodes: [],
      runtimeMonitoringStatus: 'active',
      summaryStatusReason: 'workspace_configuration_invalid:no_valid_protected_assets',
      summaryConfigurationReason: 'no_valid_protected_assets',
      summaryConfigurationReasonCodes: ['no_valid_protected_assets'],
      continuityStatus: 'continuous_live',
    });

    expect(state).toBe('unconfigured_workspace');
    expect(pageStatePrimaryCopy(state, 'no_valid_protected_assets')).toContain('Workspace is not configured');
  });

  test('continuity live without detections returns configured-no-signals copy', async () => {
    const state = derivePageState({
      loadingSnapshot: false,
      snapshotError: false,
      targets: [],
      liveDetections: [],
      workspaceConfigured: true,
      freshnessStatus: 'fresh',
      monitoringStatus: 'live',
      reportingSystems: 2,
      runtimeStatus: 'live',
      monitoredSystems: 2,
      hasLiveTelemetry: true,
      statusReason: null,
      configurationReason: null,
      configurationReasonCodes: [],
      runtimeMonitoringStatus: 'active',
      summaryStatusReason: null,
      summaryConfigurationReason: null,
      summaryConfigurationReasonCodes: [],
      continuityStatus: 'continuous_live',
    });

    expect(state).toBe('configured_no_signals');
    expect(pageStatePrimaryCopy(state, null, 'continuous_live')).toContain('Continuous live monitoring proven');
  });
});
