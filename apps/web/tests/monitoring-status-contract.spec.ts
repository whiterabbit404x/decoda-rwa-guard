import { expect, test } from '@playwright/test';

import { monitoringModeLabel, normalizeMonitoringMode } from '../app/monitoring-status-contract';

test.describe('monitoring status contract', () => {
  test('normalizes runtime payload modes including degraded', async () => {
    expect(normalizeMonitoringMode('live')).toBe('LIVE');
    expect(normalizeMonitoringMode('hybrid')).toBe('HYBRID');
    expect(normalizeMonitoringMode('degraded')).toBe('DEGRADED');
    expect(normalizeMonitoringMode('unknown')).toBe('DEMO');
  });

  test('renders mode labels used in dashboard status surfaces', async () => {
    expect(monitoringModeLabel('DEMO')).toBe('DEMO MODE');
    expect(monitoringModeLabel('LIVE')).toBe('LIVE MODE');
    expect(monitoringModeLabel('HYBRID')).toBe('HYBRID MODE');
    expect(monitoringModeLabel('DEGRADED')).toBe('DEGRADED');
  });
});
