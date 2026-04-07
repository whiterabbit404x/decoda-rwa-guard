import { expect, test } from '@playwright/test';

import {
  getDashboardFreshnessLabel,
  getDashboardPresentationLabel,
  normalizeDashboardPresentationState,
} from '../app/dashboard-status-presentation';

test.describe('dashboard status presentation adapter', () => {
  test('maps internal raw states into enterprise-safe presentation states only', async () => {
    expect(normalizeDashboardPresentationState({ internalSource: 'live', degraded: false })).toBe('live');
    expect(normalizeDashboardPresentationState({ internalSource: 'live', degraded: true })).toBe('live_degraded');
    expect(normalizeDashboardPresentationState({ internalSource: 'fallback' })).toBe('limited_coverage');
    expect(normalizeDashboardPresentationState({ internalSource: 'sample' })).toBe('limited_coverage');
    expect(normalizeDashboardPresentationState({ internalSource: 'unavailable' })).toBe('unavailable');

    expect(normalizeDashboardPresentationState({ internalEvidence: 'delayed' })).toBe('delayed');
    expect(normalizeDashboardPresentationState({ internalEvidence: 'stale' })).toBe('stale');
    expect(normalizeDashboardPresentationState({ internalEvidence: 'offline' })).toBe('offline');
    expect(normalizeDashboardPresentationState({ internalEvidence: 'degraded' })).toBe('degraded');
  });

  test('does not mislabel weak evidence as verified telemetry', async () => {
    expect(getDashboardPresentationLabel('limited_coverage')).toBe('Coverage currently limited');
    expect(getDashboardPresentationLabel('delayed')).toBe('Telemetry delayed');
    expect(getDashboardPresentationLabel('unavailable')).toBe('Telemetry unavailable');
    expect(getDashboardFreshnessLabel('degraded')).toBe('Recent telemetry');
    expect(normalizeDashboardPresentationState({ internalSource: 'fallback', internalEvidence: 'live' })).toBe('live');
    expect(normalizeDashboardPresentationState({ internalSource: 'fallback', internalEvidence: 'degraded' })).toBe('degraded');
    expect(normalizeDashboardPresentationState({ internalSource: 'sample', internalEvidence: 'delayed' })).toBe('delayed');
  });
});
