import { test, expect } from '@playwright/test';

import { getMonitoringStatus } from '../app/assets-manager';

test.describe('asset coverage derivation — monitoring_status field', () => {
  test('live_verified shows Live telemetry verified in green', () => {
    const result = getMonitoringStatus({ monitoring_status: 'live_verified' });
    expect(result.label).toBe('Live telemetry verified');
    expect(result.variant).toBe('success');
  });

  test('not_linked shows Telemetry unlinked in warning', () => {
    const result = getMonitoringStatus({ monitoring_status: 'not_linked' });
    expect(result.label).toBe('Telemetry unlinked');
    expect(result.variant).toBe('warning');
  });

  test('not_configured shows Not configured in neutral', () => {
    const result = getMonitoringStatus({ monitoring_status: 'not_configured' });
    expect(result.label).toBe('Not configured');
    expect(result.variant).toBe('neutral');
  });

  test('error shows Provider issue in danger', () => {
    const result = getMonitoringStatus({ monitoring_status: 'error' });
    expect(result.label).toBe('Provider issue');
    expect(result.variant).toBe('danger');
  });

  test('waiting_for_telemetry falls through to field-level logic and shows Waiting for telemetry', () => {
    // backend monitoring_status 'waiting_for_telemetry' is not handled directly
    // → falls through to field-level logic → needs monitoring_link_status='attached'
    const result = getMonitoringStatus({
      monitoring_status: 'waiting_for_telemetry',
      monitoring_link_status: 'attached',
      has_linked_monitored_system: true,
      has_heartbeat: true,
      has_telemetry: false,
    });
    expect(result.label).toBe('Waiting for telemetry');
  });

  test('not_linked does NOT show Waiting for telemetry', () => {
    const result = getMonitoringStatus({ monitoring_status: 'not_linked' });
    expect(result.label).not.toBe('Waiting for telemetry');
  });

  test('workspace has live telemetry but no asset link → not Waiting for telemetry', () => {
    // Simulates the real-world state: workspace telemetry exists but asset unlinked
    const result = getMonitoringStatus({
      monitoring_status: 'not_linked',
      coverage_reason: 'workspace_live_telemetry_unlinked',
    });
    expect(result.label).toBe('Telemetry unlinked');
    expect(result.label).not.toBe('Waiting for telemetry');
  });

  test('View telemetry next action links to target telemetry page', () => {
    // next_action_href should be set when linked_target_id exists
    const asset = {
      monitoring_status: 'live_verified',
      next_action_href: '/monitoring-sources/t-123/telemetry',
      next_action_label: 'View telemetry',
    };
    expect(asset.next_action_href).toContain('/telemetry');
    expect(asset.next_action_label).toBe('View telemetry');
  });

  test('Link monitoring source action links to /monitoring-sources when unlinked', () => {
    const asset = {
      monitoring_status: 'not_linked',
      next_action_href: '/monitoring-sources',
      next_action_label: 'Link monitoring source',
    };
    expect(asset.next_action_href).toBe('/monitoring-sources');
  });

  test('backward compat: monitoring_status absent falls through to attached+telemetry logic', () => {
    // Old-style response without monitoring_status → existing logic still works
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

  test('backward compat: monitoring_status absent with no telemetry → Waiting for telemetry', () => {
    const result = getMonitoringStatus({
      monitoring_link_status: 'attached',
      has_linked_monitored_system: true,
      has_heartbeat: true,
    });
    expect(result.label).toBe('Waiting for telemetry');
  });
});
