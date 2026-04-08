import { expect, test } from '@playwright/test';

import { monitoringModeLabel, normalizeMonitoringMode, runtimeStatusModeFromMonitoringStatus } from '../app/monitoring-status-contract';

test.describe('monitoring status contract', () => {
  test('normalizes runtime payload modes including degraded', async () => {
    expect(normalizeMonitoringMode('live')).toBe('LIVE');
    expect(normalizeMonitoringMode('hybrid')).toBe('LIMITED_COVERAGE');
    expect(normalizeMonitoringMode('demo')).toBe('LIMITED_COVERAGE');
    expect(normalizeMonitoringMode('degraded')).toBe('DEGRADED');
    expect(normalizeMonitoringMode('offline')).toBe('OFFLINE');
    expect(normalizeMonitoringMode('stale')).toBe('STALE');
    expect(normalizeMonitoringMode('synthetic_leak')).toBe('LIMITED_COVERAGE');
    expect(normalizeMonitoringMode('unknown')).toBe('LIMITED_COVERAGE');
  });

  test('renders mode labels used in dashboard status surfaces', async () => {
    expect(monitoringModeLabel('LIMITED_COVERAGE')).toBe('LIMITED COVERAGE');
    expect(monitoringModeLabel('LIVE')).toBe('LIVE');
    expect(monitoringModeLabel('DEGRADED')).toBe('DEGRADED');
    expect(monitoringModeLabel('OFFLINE')).toBe('OFFLINE');
    expect(monitoringModeLabel('STALE')).toBe('STALE');
  });

  test('maps runtime monitoring statuses without collapsing idle into offline', async () => {
    expect(runtimeStatusModeFromMonitoringStatus('active')).toBe('LIVE');
    expect(runtimeStatusModeFromMonitoringStatus('degraded')).toBe('DEGRADED');
    expect(runtimeStatusModeFromMonitoringStatus('idle')).toBe('LIMITED_COVERAGE');
    expect(runtimeStatusModeFromMonitoringStatus('offline')).toBe('OFFLINE');
    expect(runtimeStatusModeFromMonitoringStatus('error')).toBe('OFFLINE');
  });
});
