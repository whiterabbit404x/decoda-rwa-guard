import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';

import { expect, test } from '@playwright/test';

const appRoot = existsSync(path.join(process.cwd(), 'apps/web/app'))
  ? path.join(process.cwd(), 'apps/web/app')
  : path.join(process.cwd(), 'app');

function source(relativePath: string): string {
  return readFileSync(path.join(appRoot, relativePath), 'utf8');
}

test.describe('assets page telemetry coverage', () => {
  test('assets manager shows Live telemetry verified when monitoring_status is live_verified', () => {
    const src = source('assets-manager.tsx');
    expect(src).toContain("case 'live_verified': return { label: 'Live telemetry verified'");
  });

  test('assets manager getMonitoringStatus uses backend monitoring_status field first', () => {
    const src = source('assets-manager.tsx');
    expect(src).toContain('const backendStatus = asset?.monitoring_status;');
    expect(src).toContain("case 'live_verified':");
    expect(src).toContain("case 'waiting_for_telemetry':");
    expect(src).toContain("case 'not_configured':");
  });

  test('assets manager next action returns View telemetry for live_verified status', () => {
    const src = source('assets-manager.tsx');
    expect(src).toContain("if (monStatus === 'live_verified')");
    expect(src).toContain("return 'View telemetry';");
  });

  test('assets manager View telemetry links to target telemetry page when linked_target_id exists', () => {
    const src = source('assets-manager.tsx');
    expect(src).toContain("action === 'View telemetry' && asset.linked_target_id");
    expect(src).toContain('/monitoring-sources/');
    expect(src).toContain('/telemetry`');
    expect(src).toContain('linked_target_id');
  });

  test('assets manager shows Add monitoring source when not_configured', () => {
    const src = source('assets-manager.tsx');
    expect(src).toContain("if (monStatus === 'not_configured') return 'Add monitoring source';");
  });

  test('assets manager does not show fake live status', () => {
    const src = source('assets-manager.tsx');
    // Must not claim live without real telemetry
    expect(src).not.toContain("label: 'Monitoring', variant: 'success'");
    // live_verified must only come from monitoring_status field, not hardcoded
    expect(src).not.toContain("label: 'Live', variant: 'success'");
  });
});

test.describe('monitoring sources target name fallback', () => {
  test('monitoring sources page has getTargetDisplayName fallback function', () => {
    const src = source('(product)/monitoring-sources/page.tsx');
    expect(src).toContain('function getTargetDisplayName');
    expect(src).toContain('Smart Contract Monitor');
    expect(src).toContain('Ethereum Wallet Monitor');
    expect(src).toContain('Monitoring Target');
  });

  test('monitoring sources uses fallback for short names (length < 3)', () => {
    const src = source('(product)/monitoring-sources/page.tsx');
    expect(src).toContain('isShort = name.length < 3');
  });

  test('monitoring sources uses fallback for repeated names', () => {
    const src = source('(product)/monitoring-sources/page.tsx');
    expect(src).toContain('isRepeated');
    expect(src).toContain('allTargets.filter');
  });

  test('monitoring sources table uses getTargetDisplayName instead of raw name', () => {
    const src = source('(product)/monitoring-sources/page.tsx');
    expect(src).toContain('const displayName = getTargetDisplayName(target, targets)');
    expect(src).toContain('{displayName}');
    expect(src).not.toContain("{target.name || 'Unnamed target'}");
  });
});

test.describe('target telemetry page helper copy', () => {
  test('target telemetry page explains raw response rows', () => {
    const src = source('(product)/monitoring-sources/[targetId]/telemetry/page.tsx');
    expect(src).toContain('Each row is a persisted live RPC polling result');
    expect(src).toContain('Raw responses are retained as evidence');
    expect(src).toContain('detections, alerts, incidents, and audits');
    expect(src).toContain('traced back to provider data');
  });
});
