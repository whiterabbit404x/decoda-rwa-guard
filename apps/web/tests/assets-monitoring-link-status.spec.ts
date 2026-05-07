import { test, expect } from '@playwright/test';

import { monitoringLinkStatusLabel, getMonitoringStatus } from '../app/assets-manager';

test.describe('asset monitoring column status', () => {
  test('maps backend monitoring_link_status to correct labels', () => {
    // "attached" with system present → Monitoring (not "Monitoring attached")
    expect(monitoringLinkStatusLabel({ monitoring_link_status: 'attached' })).toBe('Monitoring');
    // system_missing → System not enabled
    expect(monitoringLinkStatusLabel({ monitoring_link_status: 'system_missing' })).toBe('System not enabled');
    // target_missing → Target missing
    expect(monitoringLinkStatusLabel({ monitoring_link_status: 'target_missing' })).toBe('Target missing');
    // not_configured → Target missing
    expect(monitoringLinkStatusLabel({ monitoring_link_status: 'not_configured' })).toBe('Target missing');
    // null/undefined → Target missing
    expect(monitoringLinkStatusLabel({ monitoring_link_status: undefined })).toBe('Target missing');
    expect(monitoringLinkStatusLabel({})).toBe('Target missing');
  });

  test('never shows Monitoring when has_linked_monitored_system is false', () => {
    const result = getMonitoringStatus({
      monitoring_link_status: 'attached',
      has_linked_monitored_system: false,
    });
    expect(result.label).not.toBe('Monitoring');
    expect(result.label).not.toBe('Monitoring attached');
    expect(result.label).toBe('System not enabled');
  });

  test('never shows Monitoring when monitoring_systems_count is 0', () => {
    const result = getMonitoringStatus({
      monitoring_link_status: 'attached',
      monitoring_systems_count: 0,
    });
    expect(result.label).not.toBe('Monitoring');
    expect(result.label).toBe('System not enabled');
  });

  test('shows Not reporting when system exists but no heartbeat', () => {
    const result = getMonitoringStatus({
      monitoring_link_status: 'attached',
      has_linked_monitored_system: true,
      has_heartbeat: false,
    });
    expect(result.label).toBe('Not reporting');
  });

  test('shows Waiting for telemetry when heartbeat exists but no telemetry', () => {
    const result = getMonitoringStatus({
      monitoring_link_status: 'attached',
      has_linked_monitored_system: true,
      has_heartbeat: true,
      has_telemetry: false,
    });
    expect(result.label).toBe('Waiting for telemetry');
  });

  test('shows Telemetry stale when telemetry is not fresh', () => {
    const result = getMonitoringStatus({
      monitoring_link_status: 'attached',
      has_linked_monitored_system: true,
      has_heartbeat: true,
      has_telemetry: true,
      telemetry_fresh: false,
    });
    expect(result.label).toBe('Telemetry stale');
  });

  test('shows Monitoring only when fully attached with fresh telemetry', () => {
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
});
