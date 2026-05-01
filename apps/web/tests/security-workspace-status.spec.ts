import { expect, test } from '@playwright/test';

import { buildSecurityWorkspaceStatus } from '../app/security-workspace-status';

test.describe('security workspace status mapping', () => {
  test('maps degraded runtime to a clear customer-safe message', async () => {
    const status = buildSecurityWorkspaceStatus(
      {
        runtime_status: 'degraded',
        status_reason: 'summary_unavailable',
        reporting_systems_count: 2,
        monitored_systems_count: 2,
        protected_assets_count: 3,
        last_telemetry_at: '2026-04-30T10:00:00Z',
      },
      [],
      [],
      [],
      [],
    );

    expect(status.posture).toBe('degraded');
    expect(status.customerMessage).toBe('Monitoring summary temporarily unavailable');
  });

  test('zero reporting systems never resolves to healthy', async () => {
    const status = buildSecurityWorkspaceStatus(
      {
        runtime_status: 'healthy',
        reporting_systems_count: 0,
        monitored_systems_count: 3,
        protected_assets_count: 3,
        last_telemetry_at: '2026-04-30T10:00:00Z',
      },
      [],
      [],
      [],
      [],
    );

    expect(status.posture === 'setup_required' || status.posture === 'degraded').toBeTruthy();
    expect(status.posture).not.toBe('healthy');
    expect(['Complete setup to enable continuous monitoring', 'No active monitoring source']).toContain(status.customerMessage);
  });

  test('telemetry unavailable is never represented as live monitoring', async () => {
    const status = buildSecurityWorkspaceStatus(
      {
        runtime_status: 'healthy',
        reporting_systems_count: 2,
        monitored_systems_count: 2,
        protected_assets_count: 2,
        last_telemetry_at: null,
      },
      [],
      [],
      [],
      [],
    );

    expect(status.posture).toBe('degraded');
    expect(status.customerMessage).toBe('No live signal received yet');
  });

  test('contradiction flags block healthy copy and add guard detail', async () => {
    const status = buildSecurityWorkspaceStatus(
      {
        runtime_status: 'healthy',
        contradiction_flags: ['open_alerts_without_detection_evidence'],
        reporting_systems_count: 2,
        monitored_systems_count: 2,
        protected_assets_count: 2,
        last_telemetry_at: '2026-04-30T10:00:00Z',
      },
      [],
      [],
      [],
      [],
    );

    expect(status.posture).toBe('degraded');
    expect(status.customerMessage).not.toBe('Monitoring is active and operating normally');
    expect(status.details).toContain('Status guarded due to conflicting runtime signals');
  });
});
