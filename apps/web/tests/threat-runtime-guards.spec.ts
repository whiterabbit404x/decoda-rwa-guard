import { expect, test } from '@playwright/test';

import { formatOperationalStateLabel } from '../app/threat-operations-panel';
import { resolveWorkspaceMonitoringTruth } from '../app/workspace-monitoring-truth';
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
});
