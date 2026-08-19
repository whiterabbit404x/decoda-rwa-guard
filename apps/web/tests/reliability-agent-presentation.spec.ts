/**
 * Screen 12 Self-Healing Reliability Agent — presentation mapping coverage.
 *
 * Pure-logic tests for the truthfulness-critical status mapping: the normalized
 * reliability vocabulary {healthy,degraded,unhealthy,unknown,disabled} must map
 * onto the design-system pills WITHOUT ever rendering "unknown" or "disabled" as
 * the healthy (green) pill, and without collapsing "disabled" into "unhealthy".
 */
import { expect, test } from '@playwright/test';

import {
  reliabilityPillClass,
  reliabilityStatusLabel,
  severityPillClass,
  type NormalizedStatus,
  type Severity,
} from '../app/(product)/system-health/_components/reliability-types';

test('healthy maps to the green (live) pill', () => {
  expect(reliabilityPillClass('healthy')).toBe('statusBadge statusBadge-live');
  expect(reliabilityStatusLabel('healthy')).toBe('Healthy');
});

test('unhealthy maps to the red (offline) pill — a real failure reads red', () => {
  expect(reliabilityPillClass('unhealthy')).toBe('statusBadge statusBadge-offline');
  expect(reliabilityStatusLabel('unhealthy')).toBe('Unhealthy');
});

test('unknown is NEVER shown as healthy (Rule 2: unknown is not healthy)', () => {
  const cls = reliabilityPillClass('unknown');
  expect(cls).not.toContain('statusBadge-live');
  expect(cls).toBe('statusBadge statusBadge-unavailable');
  expect(reliabilityStatusLabel('unknown')).toBe('Unknown');
  expect(reliabilityStatusLabel('unknown')).not.toBe('Healthy');
});

test('disabled is distinct from unhealthy and from healthy (Rule 3: disabled is not crashed)', () => {
  expect(reliabilityPillClass('disabled')).toBe('statusBadge statusBadge-unavailable');
  expect(reliabilityStatusLabel('disabled')).toBe('Disabled');
  // Not the crash (red) pill and not the healthy (green) pill.
  expect(reliabilityPillClass('disabled')).not.toContain('statusBadge-offline');
  expect(reliabilityPillClass('disabled')).not.toContain('statusBadge-live');
});

test('degraded maps to the amber pill', () => {
  expect(reliabilityPillClass('degraded')).toBe('statusBadge statusBadge-degraded');
});

test('severity pills escalate correctly', () => {
  const map: Record<Severity, string> = {
    critical: 'statusBadge statusBadge-offline',
    high: 'statusBadge statusBadge-offline',
    medium: 'statusBadge statusBadge-degraded',
    low: 'statusBadge statusBadge-unavailable',
  };
  (Object.keys(map) as Severity[]).forEach((sev) => {
    expect(severityPillClass(sev)).toBe(map[sev]);
  });
});

test('every normalized status has a non-empty label and pill', () => {
  const statuses: NormalizedStatus[] = ['healthy', 'degraded', 'unhealthy', 'unknown', 'disabled'];
  for (const s of statuses) {
    expect(reliabilityStatusLabel(s).length).toBeGreaterThan(0);
    expect(reliabilityPillClass(s)).toContain('statusBadge');
  }
});
