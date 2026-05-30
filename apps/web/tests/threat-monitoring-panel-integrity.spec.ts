/**
 * Integrity tests for the refactored ThreatMonitoringPanel.
 * Source-level: reads .tsx files and asserts structural correctness.
 * No browser required.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function appSource(fileName: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', fileName), 'utf-8');
}

// ─── 1. /threat does not fetch nonexistent endpoints ──────────────────────────

test('threat-monitoring-panel does not fetch /telemetry endpoint', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  // The panel must not call the workspace-level /telemetry endpoint which does not exist.
  expect(panel).not.toContain('`${apiUrl}/telemetry`');
  expect(panel).not.toContain("apiUrl}/telemetry'");
  expect(panel).not.toContain('/telemetry`, {');
});

test('threat-monitoring-panel does not fetch /anomalies endpoint', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  // The panel must not call /anomalies which has no backend route.
  expect(panel).not.toContain('`${apiUrl}/anomalies`');
  expect(panel).not.toContain("apiUrl}/anomalies'");
  expect(panel).not.toContain('/anomalies`, {');
});

test('threat-monitoring-panel only fetches known backend endpoints', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  // The only permitted direct fetch is /detections which the backend provides.
  expect(panel).toContain('`${apiUrl}/detections`');
  // Ensure no other ad-hoc endpoint strings beside detections are fetched
  const fetchMatches = [...panel.matchAll(/fetch\(\s*`\$\{apiUrl\}\/([^`]+)`/g)].map((m) => m[1]);
  for (const endpoint of fetchMatches) {
    expect(['detections', 'detections?limit=50'].some((allowed) => endpoint.startsWith(allowed))).toBe(true);
  }
});

// ─── 2. No corrupted text appears ─────────────────────────────────────────────

test('no mojibake character 閻 appears in threat-monitoring-panel', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).not.toContain('閻');
  expect(panel).not.toContain('閻');
});

test('telemetry summary line uses · separator, not corrupted characters', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain('· Last:');
  expect(panel).toContain('· None received');
});

// ─── 3. Simulator evidence is never labeled live_provider ─────────────────────

test('evidencePill guards simulator source before allowing live_provider label', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  const fnStart = panel.indexOf('function evidencePill');
  const fnEnd = panel.indexOf('function nodeStatusVariant');
  const fnText = panel.slice(fnStart, fnEnd);

  const simulatorGuardPos = fnText.indexOf("workspaceSource === 'simulator'");
  const liveProviderPos = fnText.indexOf("label: 'live_provider'");

  // Simulator guard must appear before the live_provider label assignment
  expect(simulatorGuardPos).toBeGreaterThan(-1);
  expect(liveProviderPos).toBeGreaterThan(-1);
  expect(simulatorGuardPos).toBeLessThan(liveProviderPos);
});

test('evidencePill returns simulator label when evidence source is simulator', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain("raw === 'simulator'");
  expect(panel).toContain("label: 'simulator', variant: 'info'");
});

test('simulator workspace evidence_source forces simulator label over live_provider', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  // workspaceSource check must exist and guard the simulator path
  expect(panel).toContain("workspaceSource === 'simulator'");
  // After the simulator guard, live_provider must not be reachable for simulator sources
  const guardIdx = panel.indexOf("workspaceSource === 'simulator'");
  const liveIdx = panel.indexOf("label: 'live_provider'");
  expect(guardIdx).toBeLessThan(liveIdx);
});

// ─── 4. Live status only shown when telemetry/detection evidence exists ────────

test('freshnessLabel returns "No telemetry" when last_telemetry_at is null', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  // Cannot claim fresh/live when there is no telemetry timestamp.
  expect(panel).toContain("return 'No telemetry'");
});

test('metric tile for Latest Telemetry uses freshnessLabel which guards null telemetry', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain('Latest Telemetry');
  expect(panel).toContain('freshnessLabel()');
});

test('ThreatOverviewCard is driven by buildSecurityWorkspaceStatus not inline live claims', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  // SecurityWorkspaceStatus is built via the canonical builder, not ad-hoc
  expect(panel).toContain('buildSecurityWorkspaceStatus(');
  expect(panel).toContain('<ThreatOverviewCard');
  // No direct "live monitoring is healthy" copy invented in the panel
  expect(panel.toLowerCase()).not.toContain('live monitoring is healthy');
});

// ─── 5. Empty state CTAs link to real next actions, not back to /threat ────────

test('Case E simulator empty state does not link back to /threat', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  const caseEBlock = panel.slice(panel.indexOf('// Case E'), panel.indexOf('// Case F'));
  expect(caseEBlock).not.toContain("'/threat'");
  // Must reference real pages for both simulator and non-simulator paths
  expect(caseEBlock).toContain('/monitored-systems');
  expect(caseEBlock).toContain('/system-health');
});

test('Case F empty state does not link back to /threat', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  const caseFBlock = panel.slice(panel.indexOf('// Case F'), panel.indexOf('// Case G'));
  expect(caseFBlock).not.toContain("ctaHref: '/threat'");
  expect(caseFBlock).toContain("ctaHref: '/monitoring-sources'");
});

test('telemetry tab empty state simulator CTA does not link back to /threat', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  // In the telemetry tab empty state, the simulator CTA must not link to /threat
  expect(panel).not.toContain("ctaHref={isSimulatorMode ? '/threat'");
});

// ─── 6. Newer components are integrated into ThreatMonitoringPanel ────────────

test('ThreatMonitoringPanel imports ThreatPageHeader', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain("import ThreatPageHeader from './threat/threat-page-header'");
});

test('ThreatMonitoringPanel imports ThreatOverviewCard', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain("import ThreatOverviewCard from './threat/threat-overview-card'");
});

test('ThreatMonitoringPanel imports MonitoringHealthCard', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain("import MonitoringHealthCard from './threat/monitoring-health-card'");
});

test('ThreatMonitoringPanel imports DetectionFeed', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain("import DetectionFeed from './threat/detection-feed'");
});

test('ThreatMonitoringPanel imports AlertIncidentChain', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain("import AlertIncidentChain from './threat/alert-incident-chain'");
});

test('ThreatMonitoringPanel imports ResponseActionPanel', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain("import ResponseActionPanel from './threat/response-action-panel'");
});

test('ThreatMonitoringPanel imports TechnicalRuntimeDetails', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain("import TechnicalRuntimeDetails from './threat/technical-runtime-details'");
});

test('TechnicalRuntimeDetails appears after ResponseActionPanel in ThreatMonitoringPanel', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  const responseActionIdx = panel.indexOf('<ResponseActionPanel');
  const technicalDetailsIdx = panel.indexOf('<TechnicalRuntimeDetails');
  expect(responseActionIdx).toBeGreaterThan(-1);
  expect(technicalDetailsIdx).toBeGreaterThan(-1);
  expect(responseActionIdx).toBeLessThan(technicalDetailsIdx);
});

test('ThreatOverviewCard appears before AlertIncidentChain in ThreatMonitoringPanel', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  const overviewIdx = panel.indexOf('<ThreatOverviewCard');
  const chainIdx = panel.indexOf('<AlertIncidentChain');
  expect(overviewIdx).toBeGreaterThan(-1);
  expect(chainIdx).toBeGreaterThan(-1);
  expect(overviewIdx).toBeLessThan(chainIdx);
});

// ─── 7. Technical details remain collapsible, not primary UI ──────────────────

test('TechnicalRuntimeDetails component uses <details> for collapsible rendering', () => {
  const tech = appSource('threat/technical-runtime-details.tsx');
  expect(tech).toContain('<details className="tableMeta">');
  expect(tech).not.toContain('<details open');
});

// ─── 8. Workflow contradiction: System must not be Blocked when systems are running ──

test('pipeline Target status uses targetInferred to avoid Pending when systems exist', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  // The fix: infer target presence from monitoredSystems/reportingSystems
  expect(panel).toContain('targetInferred');
  expect(panel).toContain('effectiveTargetOk');
});

test('pipeline System is never Blocked when effectiveTargetOk is used as guard', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  // System must be gated on effectiveTargetOk not raw targetOk
  expect(panel).toContain("!effectiveTargetOk ? 'Blocked'");
  // Raw targetOk must not gate System status
  const systemStatusLine = panel.match(/System:.*Blocked.*/)?.[0] ?? '';
  expect(systemStatusLine).not.toContain('!targetOk');
});

test('pipeline Target shows Configured when target is inferred from running systems', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain("targetInferred ? 'Configured'");
});

// ─── 9. Telemetry contradiction: no plain "No events yet" when runtime telemetry exists ──

test('Telemetry Volume card does not say plain "No events yet" when lastTelemetryAt exists', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  // Must be guarded: "No events yet" only appears inside an !lastTelemetryAt branch
  expect(panel).toContain('Telemetry list is not loaded in this view.');
  expect(panel).toContain('Latest runtime telemetry received:');
  // Verify "No events yet" is inside an else branch gated by lastTelemetryAt
  const telVolumeBlock = panel.slice(
    panel.indexOf('Telemetry Volume'),
    panel.indexOf('Top Detection Types'),
  );
  const lastTelemetryGuardPos = telVolumeBlock.indexOf('lastTelemetryAt ?');
  const noEventsPos = telVolumeBlock.indexOf('No events yet');
  expect(lastTelemetryGuardPos).toBeGreaterThan(-1);
  expect(noEventsPos).toBeGreaterThan(lastTelemetryGuardPos);
});

// ─── 10. Live mode does not render "Simulation signal" ────────────────────────

test('ResponseActionPanel does not unconditionally default to Simulation only capability', () => {
  const panel = appSource('threat/response-action-panel.tsx');
  // Must not have the fallback that always shows 'Simulation only' when capabilities is empty
  expect(panel).not.toContain("capabilities.length > 0 ? capabilities : ['Simulation only']");
});

test('threat-monitoring-panel passes isSimulatorMode to ResponseActionPanel', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain('isSimulatorMode={isSimulatorMode}');
});

test('threat-monitoring-panel passes live capabilities in non-simulator mode', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain("'Review alerts'");
  expect(panel).toContain("'Open incident queue'");
  expect(panel).toContain("'Configure response policy'");
  expect(panel).toContain("'Export evidence'");
  // Live capabilities must be gated on !isSimulatorMode
  const capBlock = panel.slice(panel.indexOf('isSimulatorMode\n'), panel.indexOf('actions={[]}'));
  expect(capBlock).toContain('isSimulatorMode');
});

// ─── 11. Hero does not render disabled "Generate evidence package" as primary action ──

test('ThreatPageHeader hides evidence package button when proofChainDisabled is true', () => {
  const header = appSource('threat/threat-page-header.tsx');
  // Button must be conditional on !proofChainDisabled, not unconditionally rendered disabled
  expect(header).toContain('!proofChainDisabled');
  // Must not have an unconditional disabled button as primary action
  expect(header).not.toContain('disabled={proofChainDisabled}');
});

test('ThreatPageHeader always renders Export evidence link', () => {
  const header = appSource('threat/threat-page-header.tsx');
  expect(header).toContain('href="/exports"');
  expect(header).toContain('Export evidence');
});

// ─── 12. Detection truth model — evaluation vs active records ─────────────────

test('panel splits detectionOk into detectionEvaluationOk and activeDetectionRecordsOk', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain('detectionEvaluationOk');
  expect(panel).toContain('activeDetectionRecordsOk');
  // Old combined detectionOk must not exist
  expect(panel).not.toContain('const detectionOk');
});

test('pipeline Detection shows Evaluated when lastDetectionAt exists but no active detection records', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  // Detection: Complete only when activeDetectionRecordsOk; Evaluated when only evaluation ran
  const nodeStatusesText = panel.slice(
    panel.indexOf('const nodeStatuses:'),
    panel.indexOf('// Empty state'),
  );
  expect(nodeStatusesText).toContain('activeDetectionRecordsOk');
  expect(nodeStatusesText).toContain("'Evaluated'");
  // Complete must be gated on activeDetectionRecordsOk not detectionOk
  const detectionLine = nodeStatusesText.match(/Detection:.*,/)?.[0] ?? '';
  expect(detectionLine).toContain('activeDetectionRecordsOk');
  expect(detectionLine).not.toContain('detectionOk');
});

test('Next Required Action does not say Detection exists when active detections are zero', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  // Case G must be gated on activeDetectionRecordsOk
  const caseGBlock = panel.slice(panel.indexOf('// Case G'), panel.indexOf('// Case H'));
  expect(caseGBlock).toContain('activeDetectionRecordsOk');
  expect(caseGBlock).not.toContain('detectionOk');
});

test('detection-feed shows No active detections title when telemetry and evaluation exist but no records', () => {
  const feed = appSource('threat/detection-feed.tsx');
  expect(feed).toContain('No active detections');
  expect(feed).toContain('lastDetectionAt');
  expect(feed).toContain('lastTelemetryAt');
});

test('detection-feed shows context-aware empty state for waiting-for-evaluation case', () => {
  const feed = appSource('threat/detection-feed.tsx');
  expect(feed).toContain('Waiting for detection evaluation');
  expect(feed).toContain('Waiting for telemetry');
});

// ─── 13. Evidence and Response nodes in pipeline ──────────────────────────────

test('pipeline workflow PIPELINE_NODES includes Evidence and Response', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  const nodesMatch = panel.match(/const PIPELINE_NODES = \[([\s\S]*?)\] as const/);
  expect(nodesMatch).not.toBeNull();
  const nodesText = nodesMatch![1];
  expect(nodesText).toContain("'Evidence'");
  expect(nodesText).toContain("'Response'");
});

test('nodeStatuses includes Evidence and Response entries', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  const nodeStatusesText = panel.slice(
    panel.indexOf('const nodeStatuses:'),
    panel.indexOf('// Empty state'),
  );
  expect(nodeStatusesText).toContain('Evidence:');
  expect(nodeStatusesText).toContain('Response:');
});

// ─── 14. Incident and Alert use Not required instead of Blocked ───────────────

test('pipeline Incident uses Not required when no active alerts not Blocked', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  const nodeStatusesText = panel.slice(
    panel.indexOf('const nodeStatuses:'),
    panel.indexOf('// Empty state'),
  );
  const incidentLine = nodeStatusesText.match(/Incident:.*,/)?.[0] ?? '';
  expect(incidentLine).toContain('Not required');
  expect(incidentLine).not.toContain("'Blocked'");
});

test('pipeline Alert uses Not required when evaluation ran but no active detections', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  const nodeStatusesText = panel.slice(
    panel.indexOf('const nodeStatuses:'),
    panel.indexOf('// Empty state'),
  );
  const alertLine = nodeStatusesText.match(/Alert:.*,/)?.[0] ?? '';
  expect(alertLine).toContain('Not required');
  expect(alertLine).not.toContain("'Blocked'");
});

// ─── 15. Simulator wording guard: no "Generate Simulator Signal" in source ───

test('threat-monitoring-panel does not contain literal "Generate Simulator Signal"', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).not.toContain('Generate Simulator Signal');
});

test('simulator CTA uses neutral "Create test signal" wording gated on isSimulatorMode', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  const occurrences = [...panel.matchAll(/Create test signal/g)];
  // Every occurrence must be preceded by isSimulatorMode within 300 chars
  for (const match of occurrences) {
    const precedingText = panel.slice(Math.max(0, (match.index ?? 0) - 300), match.index ?? 0);
    expect(precedingText).toContain('isSimulatorMode');
  }
});

test('LIVE mode Case E CTA is "Check Worker Status" not simulator wording', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  const caseEBlock = panel.slice(panel.indexOf('// Case E'), panel.indexOf('// Case F'));
  expect(caseEBlock).toContain('Check Worker Status');
  expect(caseEBlock).not.toContain('Generate Simulator Signal');
  expect(caseEBlock).not.toContain('Simulation signal');
});

test('Case F CTA is "Review monitoring rules" not "Run Detection"', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  const caseFBlock = panel.slice(panel.indexOf('// Case F'), panel.indexOf('// Case F2'));
  expect(caseFBlock).toContain('Review monitoring rules');
  expect(caseFBlock).not.toContain('Run Detection');
});

// ─── 16. No anomalies endpoint fetch ─────────────────────────────────────────

test('threat-monitoring-panel does not fetch /anomalies endpoint (duplicate guard)', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).not.toContain('/anomalies');
});

// ─── 17. No mojibake in detection-feed ───────────────────────────────────────

test('detection-feed has no mojibake characters', () => {
  const feed = appSource('threat/detection-feed.tsx');
  expect(feed).not.toContain('闻');
  expect(feed).not.toContain('鰹');
  expect(feed).not.toContain('�');
});
