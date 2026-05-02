import fs from 'node:fs';
import path from 'node:path';

describe('threat saas refactor source contracts', () => {
  const panel = fs.readFileSync(path.join(process.cwd(), 'app/threat-operations-panel.tsx'), 'utf8');
  const overview = fs.readFileSync(path.join(process.cwd(), 'app/threat/threat-overview-card.tsx'), 'utf8');
  const tech = fs.readFileSync(path.join(process.cwd(), 'app/threat/technical-runtime-details.tsx'), 'utf8');
  const detect = fs.readFileSync(path.join(process.cwd(), 'app/threat/detection-feed.tsx'), 'utf8');
  const chain = fs.readFileSync(path.join(process.cwd(), 'app/threat/alert-incident-chain.tsx'), 'utf8');
  const action = fs.readFileSync(path.join(process.cwd(), 'app/threat/response-action-panel.tsx'), 'utf8');

  it('keeps buildSecurityWorkspaceStatus owned by monitoring model builder', () => {
    const monitoringModel = fs.readFileSync(path.join(process.cwd(), 'app/threat/build-monitoring-health-model.ts'), 'utf8');
    expect(panel).not.toContain("import { buildSecurityWorkspaceStatus }");
    expect(panel).not.toContain('const securityStatus = useMemo(');
    expect(monitoringModel).toContain("import { buildSecurityWorkspaceStatus } from '../security-workspace-status';");
    expect(monitoringModel).toContain('buildSecurityWorkspaceStatus(');
  });

  it('ThreatOverviewCard accepts SecurityWorkspaceStatus', () => {
    expect(overview).toContain('SecurityWorkspaceStatus');
  });

  it('technical details collapsed by default', () => {
    expect(tech).toContain('<details className="tableMeta">');
    expect(tech).not.toContain('<details open');
  });

  it('customer-safe empty states and labels are present', () => {
    expect(detect).toContain('No detections yet. Monitoring will show detections here once telemetry matches a rule.');
    expect(chain).toContain('Alert → Incident → Response Action');
    expect(action).toContain('Simulation only');
    expect(action).toContain('Manual recommendation');
    expect(action).toContain('Live executable');
  });
});
