import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

test.describe('threat saas refactor source contracts', () => {
  const panel = fs.readFileSync(path.join(process.cwd(), 'app/threat-operations-panel.tsx'), 'utf8');
  const overview = fs.readFileSync(path.join(process.cwd(), 'app/threat/threat-overview-card.tsx'), 'utf8');
  const tech = fs.readFileSync(path.join(process.cwd(), 'app/threat/technical-runtime-details.tsx'), 'utf8');
  const detect = fs.readFileSync(path.join(process.cwd(), 'app/threat/detection-feed.tsx'), 'utf8');
  const chain = fs.readFileSync(path.join(process.cwd(), 'app/threat/alert-incident-chain.tsx'), 'utf8');
  const action = fs.readFileSync(path.join(process.cwd(), 'app/threat/response-action-panel.tsx'), 'utf8');
  const threatCopy = fs.readFileSync(path.join(process.cwd(), 'app/threat/threat-copy.ts'), 'utf8');

  test('keeps buildSecurityWorkspaceStatus owned by monitoring model builder', () => {
    const monitoringModel = fs.readFileSync(path.join(process.cwd(), 'app/threat/build-monitoring-health-model.ts'), 'utf8');
    expect(panel).not.toContain('import { buildSecurityWorkspaceStatus }');
    expect(panel).not.toContain('const securityStatus = useMemo(');
    expect(monitoringModel).toContain("import { buildSecurityWorkspaceStatus } from '../security-workspace-status';");
    expect(monitoringModel).toContain('buildSecurityWorkspaceStatus(');
  });

  test('ThreatOverviewCard accepts SecurityWorkspaceStatus', () => {
    expect(overview).toContain('SecurityWorkspaceStatus');
  });

  test('technical details collapsed by default', () => {
    expect(tech).toContain('<details className="tableMeta">');
    expect(tech).not.toContain('<details open');
  });

  test('customer-safe empty states and labels are present', () => {
    expect(detect).toContain("import { THREAT_COPY } from './threat-copy';");
    expect(detect).toContain('{THREAT_COPY.noDetectionRecords}');
    expect(threatCopy).toContain('No detections yet. Live telemetry coverage for Treasury-backed assets, custody wallets, issuer contracts, oracle/NAV feeds, and compliance exposure checks will populate detections here once signals arrive.');
    expect(chain).toContain('Alert → Incident → Response Action');
    expect(action).toContain('Simulation only');
    expect(action).toContain('Manual recommendation');
    expect(action).toContain('Live executable');
  });
});
