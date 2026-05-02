import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const appDir = path.join(__dirname, '..', 'app');

function appSource(fileName: string): string {
  return fs.readFileSync(path.join(appDir, fileName), 'utf-8');
}

test.describe('threat operations panel requested contracts (source)', () => {
  test('buildSecurityWorkspaceStatus remains wired in threat flow', () => {
    const panel = appSource('threat-operations-panel.tsx');

    expect(panel).toContain("import { buildSecurityWorkspaceStatus } from './security-workspace-status';");
    expect(panel).toContain('buildSecurityWorkspaceStatus(runtimeStatusSnapshot, detections, alerts, incidents, evidence)');
  });

  test('renders TechnicalRuntimeDetails after ResponseActionPanel and keeps technical details collapsed by default', () => {
    const panel = appSource('threat-operations-panel.tsx');
    const technicalDetails = appSource('threat/technical-runtime-details.tsx');

    const responseActionIdx = panel.indexOf('<ResponseActionPanel');
    const technicalDetailsIdx = panel.indexOf('<TechnicalRuntimeDetails');

    expect(responseActionIdx).toBeGreaterThan(-1);
    expect(technicalDetailsIdx).toBeGreaterThan(-1);
    expect(responseActionIdx).toBeLessThan(technicalDetailsIdx);

    expect(technicalDetails).toContain('<details className="tableMeta">');
    expect(technicalDetails).not.toContain('<details className="tableMeta" open>');
  });

  test('DetectionFeed and ResponseActionPanel do not require/accept children for primary content', () => {
    const detectionFeed = appSource('threat/detection-feed.tsx');
    const responseActionPanel = appSource('threat/response-action-panel.tsx');

    expect(detectionFeed).toContain('type Props = { detections: DetectionRecord[]; loading: boolean };');
    expect(detectionFeed).not.toMatch(/children\??\s*:/);

    expect(responseActionPanel).toContain('type Props = {');
    expect(responseActionPanel).toContain('capabilities: string[];');
    expect(responseActionPanel).toContain('actions: ResponseAction[];');
    expect(responseActionPanel).not.toMatch(/children\??\s*:/);
  });

  test('customer-facing output excludes simulator proof-chain action copy', () => {
    const customerFacing = [
      appSource('threat/threat-page-header.tsx'),
      appSource('threat/threat-overview-card.tsx'),
      appSource('threat/monitoring-health-card.tsx'),
      appSource('threat/detection-feed.tsx'),
      appSource('threat/alert-incident-chain.tsx'),
      appSource('threat/response-action-panel.tsx'),
      appSource('threat/threat-empty-state.tsx'),
    ].join('\n');

    expect(customerFacing).not.toContain('Generate simulator proof chain');
    expect(appSource('threat-operations-panel.tsx')).not.toContain('Generate simulator proof chain');
    expect(appSource('threat/threat-copy.ts')).not.toContain('Generate simulator proof chain');
  });

  test('proof-chain internals and contradiction_flags are only rendered from technical-runtime-details path', () => {
    const technicalDetails = appSource('threat/technical-runtime-details.tsx');
    const nonTechnical = [
      appSource('threat-operations-panel.tsx'),
      appSource('threat/threat-page-header.tsx'),
      appSource('threat/threat-overview-card.tsx'),
      appSource('threat/monitoring-health-card.tsx'),
      appSource('threat/detection-feed.tsx'),
      appSource('threat/alert-incident-chain.tsx'),
      appSource('threat/response-action-panel.tsx'),
      appSource('threat/threat-empty-state.tsx'),
      appSource('security-workspace-status.ts'),
    ].join('\n');

    expect(technicalDetails).toContain('proof-chain internals:');
    expect(technicalDetails).toContain('contradiction_flags:');

    expect(nonTechnical).not.toContain('proof-chain internals:');
    expect(nonTechnical).not.toContain('contradiction_flags:');
  });

  test('truth-model copy never claims healthy/live status with zero reporting systems and still shows no-live-signal copy without telemetry', () => {
    const securityStatus = appSource('security-workspace-status.ts');

    expect(securityStatus).toContain("if (reportingSystems === 0) return 'No active monitoring source';");
    expect(securityStatus).not.toContain('All monitored systems reporting healthy live telemetry.');

    expect(securityStatus).toContain("if (!telemetryAt) return 'No live signal received yet';");
  });
});
