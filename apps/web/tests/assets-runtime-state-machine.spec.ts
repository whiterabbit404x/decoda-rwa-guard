/**
 * State machine correctness: runtime banner state + asset row monitoring label.
 * No browser required — pure logic contracts.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

import type { MonitoringRuntimeStatus } from '../app/monitoring-status-contract';
import { resolveWorkspaceMonitoringTruth, hasLiveTelemetry } from '../app/workspace-monitoring-truth';
import { getMonitoringStatus } from '../app/assets-manager';

const panelSrc = fs.readFileSync(path.join(__dirname, '..', 'app', 'runtime-summary-panel.tsx'), 'utf-8');
const contextSrc = fs.readFileSync(path.join(__dirname, '..', 'app', 'runtime-summary-context.tsx'), 'utf-8');

function summaryFromRuntime(overrides: Partial<MonitoringRuntimeStatus['workspace_monitoring_summary']>): MonitoringRuntimeStatus {
  return {
    mode: 'LIMITED_COVERAGE',
    workspace_monitoring_summary: {
      workspace_configured: false,
      runtime_status: 'offline',
      monitoring_status: 'offline',
      freshness_status: 'unavailable',
      confidence_status: 'unavailable',
      telemetry_freshness: 'unavailable',
      confidence: 'unavailable',
      protected_assets: 0,
      protected_assets_count: 0,
      reporting_systems: 0,
      reporting_systems_count: 0,
      monitored_systems: 0,
      monitored_systems_count: 0,
      monitoring_targets: 0,
      last_poll_at: null,
      last_heartbeat_at: null,
      last_telemetry_at: null,
      last_detection_at: null,
      active_alerts: 0,
      active_alerts_count: 0,
      open_incidents: 0,
      active_incidents_count: 0,
      evidence_source: 'none',
      evidence_source_summary: 'none',
      reason_codes: [],
      contradiction_flags: [],
      guard_flags: [],
      status_reason: null,
      next_required_action: 'add_asset',
      current_step: 'asset_created',
      workflow_steps: [],
      ...overrides,
    },
  } as MonitoringRuntimeStatus;
}

test.describe('runtime banner state derivation', () => {
  test('asset exists with no telemetry → NOT offline, not live — resolves to limited coverage signals', () => {
    const truth = resolveWorkspaceMonitoringTruth(summaryFromRuntime({
      workspace_configured: true,
      runtime_status: 'live',
      monitoring_status: 'limited',
      protected_assets_count: 1,
      protected_assets: 1,
      reporting_systems_count: 1,
      reporting_systems: 1,
      telemetry_freshness: 'unavailable',
      confidence: 'unavailable',
      last_poll_at: '2026-04-15T10:00:00Z',
      last_heartbeat_at: '2026-04-15T10:00:00Z',
      last_telemetry_at: null,
    }));
    expect(hasLiveTelemetry(truth)).toBeFalsy();
    expect(truth.runtime_status).not.toBe('offline');
    expect(truth.protected_assets_count).toBe(1);
    expect(truth.reporting_systems_count).toBe(1);
  });

  test('reporting_systems = 0 prevents live telemetry even with all other fields set', () => {
    const truth = resolveWorkspaceMonitoringTruth(summaryFromRuntime({
      workspace_configured: true,
      runtime_status: 'live',
      monitoring_status: 'live',
      protected_assets_count: 1,
      protected_assets: 1,
      reporting_systems_count: 0,
      reporting_systems: 0,
      telemetry_freshness: 'fresh',
      confidence: 'high',
      last_poll_at: '2026-04-15T10:00:00Z',
      last_heartbeat_at: '2026-04-15T10:00:00Z',
      last_telemetry_at: '2026-04-15T09:59:00Z',
    }));
    expect(hasLiveTelemetry(truth)).toBeFalsy();
    expect(truth.contradiction_flags).toContain('live_monitoring_without_reporting_systems');
    expect(truth.guard_flags).toContain('live_monitoring_without_reporting_systems');
  });

  test('worker heartbeat without telemetry is not live monitoring', () => {
    const truth = resolveWorkspaceMonitoringTruth(summaryFromRuntime({
      workspace_configured: true,
      runtime_status: 'live',
      monitoring_status: 'live',
      protected_assets_count: 1,
      protected_assets: 1,
      reporting_systems_count: 1,
      reporting_systems: 1,
      freshness_status: 'fresh',
      confidence_status: 'high',
      last_heartbeat_at: '2026-04-15T10:00:00Z',
      last_poll_at: null,
      last_telemetry_at: null,
    }));
    expect(hasLiveTelemetry(truth)).toBeFalsy();
    expect(truth.contradiction_flags.some((f) => f.includes('telemetry') || f.includes('heartbeat'))).toBeTruthy();
  });

  test('missing EVM_RPC_URL context: offline backend with no data is OFFLINE state', () => {
    const truth = resolveWorkspaceMonitoringTruth(summaryFromRuntime({
      workspace_configured: false,
      runtime_status: 'offline',
      monitoring_status: 'offline',
      protected_assets_count: 0,
      protected_assets: 0,
      reporting_systems_count: 0,
      reporting_systems: 0,
      telemetry_freshness: 'unavailable',
      confidence: 'unavailable',
      last_heartbeat_at: null,
      last_poll_at: null,
      last_telemetry_at: null,
    }));
    expect(truth.runtime_status).toBe('offline');
    expect(truth.protected_assets_count).toBe(0);
    expect(hasLiveTelemetry(truth)).toBeFalsy();
  });

  test('runtime summary protected_assets_count matches what assets list would show', () => {
    const truth = resolveWorkspaceMonitoringTruth(summaryFromRuntime({
      workspace_configured: true,
      runtime_status: 'live',
      monitoring_status: 'limited',
      protected_assets: 3,
      protected_assets_count: 3,
    }));
    expect(truth.protected_assets_count).toBe(3);
  });

  test('live_evidence_ready is impossible when reporting_systems = 0', () => {
    const truth = resolveWorkspaceMonitoringTruth(summaryFromRuntime({
      workspace_configured: true,
      runtime_status: 'live',
      monitoring_status: 'live',
      protected_assets_count: 2,
      reporting_systems_count: 0,
      telemetry_freshness: 'fresh',
      confidence: 'high',
      last_telemetry_at: '2026-04-15T10:00:00Z',
      evidence_source_summary: 'live',
    }));
    expect(hasLiveTelemetry(truth)).toBeFalsy();
    expect(truth.guard_flags).toContain('live_monitoring_without_reporting_systems');
  });
});

test.describe('asset table monitoring column fail-closed', () => {
  test('asset with monitoring_link_status=attached but no telemetry fields → Waiting for telemetry', () => {
    const result = getMonitoringStatus({ monitoring_link_status: 'attached' });
    expect(result.label).toBe('Waiting for telemetry');
    expect(result.label).not.toBe('Monitoring');
  });

  test('asset with attached + system present but has_telemetry not set → Waiting for telemetry', () => {
    const result = getMonitoringStatus({
      monitoring_link_status: 'attached',
      has_linked_monitored_system: true,
      has_heartbeat: true,
    });
    expect(result.label).toBe('Waiting for telemetry');
  });

  test('asset row never shows Monitoring when has_telemetry is explicitly false', () => {
    const result = getMonitoringStatus({
      monitoring_link_status: 'attached',
      has_linked_monitored_system: true,
      has_heartbeat: true,
      has_telemetry: false,
    });
    expect(result.label).not.toBe('Monitoring');
    expect(result.label).toBe('Waiting for telemetry');
  });

  test('asset row shows Monitoring only when has_telemetry is explicitly true', () => {
    const result = getMonitoringStatus({
      monitoring_link_status: 'attached',
      has_linked_monitored_system: true,
      has_heartbeat: true,
      has_telemetry: true,
      telemetry_fresh: true,
    });
    expect(result.label).toBe('Monitoring');
    expect(result.variant).toBe('success');
  });

  test('asset with stale telemetry shows Telemetry stale not Monitoring', () => {
    const result = getMonitoringStatus({
      monitoring_link_status: 'attached',
      has_linked_monitored_system: true,
      has_heartbeat: true,
      has_telemetry: true,
      telemetry_fresh: false,
    });
    expect(result.label).toBe('Telemetry stale');
    expect(result.label).not.toBe('Monitoring');
  });
});

test.describe('runtime-summary-panel source contracts', () => {
  test('panel derives OFFLINE / SETUP_REQUIRED / LIMITED_COVERAGE / LIVE states', () => {
    expect(panelSrc).toContain('OFFLINE');
    expect(panelSrc).toContain('SETUP_REQUIRED');
    expect(panelSrc).toContain('LIMITED_COVERAGE');
    expect(panelSrc).toContain('LIVE');
  });

  test('panel contains setup checklist with all 7 required steps', () => {
    expect(panelSrc).toContain('Verify protected asset');
    expect(panelSrc).toContain('Link monitoring source');
    expect(panelSrc).toContain('Enable worker');
    expect(panelSrc).toContain('Receive first provider poll');
    expect(panelSrc).toContain('Receive first telemetry event');
    expect(panelSrc).toContain('Generate first detection');
    expect(panelSrc).toContain('Create alert / incident evidence');
  });

  test('panel contains Provider Health and Worker Health cards', () => {
    expect(panelSrc).toContain('Provider Health');
    expect(panelSrc).toContain('Worker Health');
  });

  test('panel contains Telemetry Timeline with empty state message', () => {
    expect(panelSrc).toContain('Telemetry Timeline');
    expect(panelSrc).toContain('No telemetry received yet');
    expect(panelSrc).toContain('Waiting for first provider poll');
  });

  test('context exposes providerHealth and workerHealth', () => {
    expect(contextSrc).toContain('providerHealth');
    expect(contextSrc).toContain('workerHealth');
    expect(contextSrc).toContain('ProviderHealthInfo');
    expect(contextSrc).toContain('WorkerHealthInfo');
  });
});
