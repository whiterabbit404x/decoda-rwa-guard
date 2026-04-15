import { existsSync, readFileSync } from 'node:fs';
import path from 'node:path';

import { expect, test } from '@playwright/test';

const appRoot = existsSync(path.join(process.cwd(), 'apps/web/app'))
  ? path.join(process.cwd(), 'apps/web/app')
  : path.join(process.cwd(), 'app');

function source(relativePath: string): string {
  return readFileSync(path.join(appRoot, relativePath), 'utf8');
}

const LEGACY_PRESENTATION_SIGNALS = [
  'recent_evidence_state',
  'systems_with_recent_heartbeat',
  'successful_detection_evaluation_recent',
  'recent_confidence_basis',
  'status.mode',
  'degraded_reason',
  'liveFeed.degraded',
  'liveFeed.offline',
  'liveFeed.stale',
  'checkpoint_age_seconds',
  'last_confirmed_checkpoint',
  'last_detection_evaluation_at',
  'synthetic_leak_detected',
  'invalid_enabled_targets',
  'detection_outcome',
] as const;

test.describe('monitoring truthfulness UI copy', () => {
  test('workspace banner uses only normalized presentation fields', async () => {
    const banner = source('workspace-monitoring-mode-banner.tsx');
    expect(banner).toContain('normalizeMonitoringPresentation');
    expect(banner).toContain('Fresh telemetry unavailable.');
    expect(banner).toContain("timestampLine('Last telemetry'");
    expect(banner).not.toContain('recent_confidence_basis');
    expect(banner).not.toContain('recent_evidence_state');
    expect(banner).not.toContain('synthetic_leak_detected');
    expect(banner.toLowerCase()).not.toContain('demo');
    expect(banner.toLowerCase()).not.toContain('synthetic');
  });

  test('overview and threat panels present enterprise-safe degraded/offline states', async () => {
    const panel = source('monitoring-overview-panel.tsx');
    const threatPanel = source('threat-operations-panel.tsx');

    expect(panel).toContain('Workspace monitoring offline. Fresh telemetry unavailable until connectivity returns.');
    expect(panel).toContain('Coverage degraded. Incident absence does not prove safety.');
    expect(panel).toContain('Monitoring data delayed. Await fresh telemetry and event updates.');

    expect(threatPanel).toContain('Threat monitoring command center');
    expect(threatPanel).toContain('Operational state');
    expect(threatPanel).toContain('Monitoring data unavailable');
    expect(threatPanel).toContain('Loading monitoring state…');
    expect(threatPanel).toContain('Refreshing monitoring state…');
  });

  test('critical monitoring presentation files do not reference legacy presentation signals', async () => {
    const threatPanel = source('threat-operations-panel.tsx');
    const statusPresentation = source('monitoring-status-presentation.ts');
    const workspaceBanner = source('workspace-monitoring-mode-banner.tsx');
    const monitoringOverview = source('monitoring-overview-panel.tsx');

    LEGACY_PRESENTATION_SIGNALS.forEach((legacySignal) => {
      expect(threatPanel).not.toContain(legacySignal);
      expect(statusPresentation).not.toContain(legacySignal);
      expect(workspaceBanner).not.toContain(legacySignal);
      expect(monitoringOverview).not.toContain(legacySignal);
    });
  });

  test('threat panel keeps contradiction guardrails visible in operator copy', async () => {
    const threatPanel = source('threat-operations-panel.tsx');
    expect(threatPanel).toContain('contradictionFlags.length > 0');
    expect(threatPanel).toContain('Guarded fallback copy active');
    expect(threatPanel).toContain('monitoringHealthyCopyAllowed(truth)');
  });
});
