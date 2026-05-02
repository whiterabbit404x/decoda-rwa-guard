import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const appDir = path.join(__dirname, '..', 'app');

function appSource(fileName: string): string {
  return fs.readFileSync(path.join(appDir, fileName), 'utf-8');
}

test.describe('threat operations panel composition guards (source)', () => {
  test('imports and calls buildSecurityWorkspaceStatus in threat operations panel', () => {
    const panel = appSource('threat-operations-panel.tsx');

    expect(panel).toContain("import { buildSecurityWorkspaceStatus } from './security-workspace-status';");
    expect(panel).toContain('() => buildSecurityWorkspaceStatus(runtimeStatusSnapshot, detections, alerts, incidents, evidence)');
  });

  test('DetectionFeed and ResponseActionPanel props do not require children for core content', () => {
    const detectionFeed = appSource('threat/detection-feed.tsx');
    const responseActionPanel = appSource('threat/response-action-panel.tsx');

    expect(detectionFeed).toContain('type Props = { detections: DetectionRecord[]; loading: boolean };');
    expect(detectionFeed).not.toMatch(/children\??\s*:/);
    expect(responseActionPanel).toContain('type Props = {');
    expect(responseActionPanel).not.toMatch(/children\??\s*:/);
  });

  test('renders ResponseActionPanel before TechnicalRuntimeDetails', () => {
    const panel = appSource('threat-operations-panel.tsx');
    const responseActionIdx = panel.indexOf('<ResponseActionPanel');
    const technicalDetailsIdx = panel.indexOf('<TechnicalRuntimeDetails');

    expect(responseActionIdx).toBeGreaterThan(-1);
    expect(technicalDetailsIdx).toBeGreaterThan(-1);
    expect(responseActionIdx).toBeLessThan(technicalDetailsIdx);
  });

  test('technical flags are only rendered within TechnicalRuntimeDetails', () => {
    const panel = appSource('threat-operations-panel.tsx');
    const technicalDetails = appSource('threat/technical-runtime-details.tsx');

    expect(panel).not.toContain('contradiction_flags:');
    expect(panel).not.toContain('guard_flags:');
    expect(panel).not.toContain('db_failure_classification:');

    expect(technicalDetails).toContain('contradiction_flags:');
    expect(technicalDetails).toContain('guard_flags:');
    expect(technicalDetails).toContain('db_failure_classification:');
  });

  test('zero reporting systems and no telemetry states avoid healthy/live claims and banned copy', () => {
    const panel = appSource('threat-operations-panel.tsx');
    const emptyState = appSource('threat/threat-empty-state.tsx');
    const healthCard = appSource('threat/monitoring-health-card.tsx');

    expect(panel.toLowerCase()).not.toContain('reporting systems: 0 and healthy');
    expect(panel.toLowerCase()).not.toContain('reporting systems: 0 and live');
    expect(healthCard.toLowerCase()).not.toContain('reporting systems: 0 and healthy');
    expect(emptyState.toLowerCase()).not.toContain('reporting systems: 0 and live');

    expect(panel).not.toContain('No live signal received yet');
  });
});
