/**
 * Screen 5 - Threat Monitoring contract tests.
 * Source-level: reads .tsx files and asserts on string/structural presence.
 * No browser required.
 */
import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function appSource(fileName: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', fileName), 'utf-8');
}

// -- 1. /threat route renders -------------------------------------
test('/threat route file exists and exports a default page component', () => {
  const src = appSource('(product)/threat/page.tsx');
  expect(src).toContain('export default function ThreatPage');
});

// -- 2. Page title "Threat Monitoring" exists in the header component ---------------------
test('page title "Threat Monitoring" exists in ThreatPageHeader', () => {
  const header = appSource('threat/threat-page-header.tsx');
  expect(header).toContain('Threat Monitoring');
});

// -- 3. Subtitle is correct in threat copy ---------------------------------------
test('page subtitle matches spec in threat-copy', () => {
  const copy = appSource('threat/threat-copy.ts');
  expect(copy).toContain(
    'Monitor telemetry, detections, alerts, incidents, evidence, and response readiness across protected RWA systems.',
  );
});

// -- 4. Tabs exist: Overview, Telemetry, Detections (Anomalies removed — no backend endpoint) --
test('tabs exist: Overview, Telemetry, Detections', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain("label: 'Overview'");
  expect(panel).toContain("label: 'Telemetry'");
  expect(panel).toContain("label: 'Detections'");
  expect(panel).toContain("key: 'overview'");
  expect(panel).toContain("key: 'telemetry'");
  expect(panel).toContain("key: 'detections'");
  // Anomalies tab removed — no backend /anomalies endpoint
  expect(panel).not.toContain("label: 'Anomalies'");
  expect(panel).not.toContain("key: 'anomalies'");
});

// -- 5. Top metric cards exist ----------------------------
test('metric card "Latest Telemetry" exists (replaces misleading Telemetry Events)', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain('Latest Telemetry');
  // Telemetry Events KPI removed — it always showed 0 because no telemetry list endpoint is loaded
  expect(panel).not.toContain('Telemetry Events');
});

test('metric card "Active Detections" exists', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain('Active Detections');
});

test('metric card "Evidence Freshness" exists (replaces Data Freshness)', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain('Evidence Freshness');
  // Anomalies KPI removed — no backend endpoint
  expect(panel).not.toContain('"Anomalies"');
});

// -- 6. Telemetry Volume section exists ---------------------------
test('Telemetry Volume card exists', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain('Telemetry Volume');
  expect(panel).toContain('aria-label="Telemetry Volume"');
});

// -- 7. Top Detection Types section exists ------------------------
test('Top Detection Types card exists', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain('Top Detection Types');
  expect(panel).toContain('aria-label="Top Detection Types"');
});

// -- 8. Pipeline status includes all 9 nodes ----------------------
test('pipeline includes node Asset', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain("'Asset'");
});

test('pipeline includes node Target', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain("'Target'");
});

test('pipeline includes node System', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain("'System'");
});

test('pipeline includes node Heartbeat', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain("'Heartbeat'");
});

test('pipeline includes node Poll', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain("'Poll'");
});

test('pipeline includes node Telemetry', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain("'Telemetry'");
});

test('pipeline includes node Detection', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain("'Detection'");
});

test('pipeline includes node Alert', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain("'Alert'");
});

test('pipeline includes node Incident', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain("'Incident'");
});

// -- 9. Telemetry table columns exist -----------------------------
test('telemetry table has column "Event ID"', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain('Event ID');
});

test('telemetry table has column "Event Type"', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain('Event Type');
});

test('telemetry table has column "Received At"', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain('Received At');
});

test('telemetry table has column "Evidence Source"', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain('Evidence Source');
});

// -- 10. Detections table columns exist ---------------------------
test('detections table has column "Detection ID"', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain('Detection ID');
});

test('detections table has column "Severity"', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain('Severity');
});

test('detections table has column "Confidence"', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain('Confidence');
});

// -- 11. Anomalies tab/table removed (no backend /anomalies endpoint) -----------
test('Anomalies tab and table are removed from threat monitoring panel', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  // All anomaly-specific identifiers must be absent
  expect(panel).not.toContain('Anomaly ID');
  expect(panel).not.toContain('First Seen');
  expect(panel).not.toContain("key: 'anomalies'");
  expect(panel).not.toContain("label: 'Anomalies'");
});

// -- 12. Page does not show live_provider for simulator data ------
test('evidencePill returns simulator when workspace evidence_source is simulator', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain("workspaceSource === 'simulator'");
  expect(panel).toContain("label: 'simulator'");

  const fnStart = panel.indexOf('function evidencePill');
  const fnEnd = panel.indexOf('function nodeStatusVariant');
  const fnText = panel.slice(fnStart, fnEnd);
  const simulatorGuardPos = fnText.indexOf("workspaceSource === 'simulator'");
  const liveProviderBranchPos = fnText.indexOf("label: 'live_provider'");
  expect(simulatorGuardPos).toBeGreaterThan(-1);
  expect(liveProviderBranchPos).toBeGreaterThan(simulatorGuardPos);
});

// -- 13. Page does not show live telemetry when last_telemetry_at is unavailable --
test('freshnessLabel returns "No telemetry" when last_telemetry_at is null', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain("return 'No telemetry'");
});

// -- 14. Page shows exact blocker message when no telemetry exists --
test('page shows exact blocker when no telemetry exists (Case E)', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain(
    'Worker is reporting, but no telemetry event has been received yet.',
  );
});
// -- 15. Empty state blockers for all pipeline cases --------------
test('Case A blocker: no asset', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain('No protected asset exists yet.');
});

test('Case B blocker: no target', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain('No monitoring target is linked to this asset yet.');
});

test('Case C blocker: no system', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain('Target exists, but no monitored system is enabled.');
});

test('Case D blocker: no heartbeat', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain('Monitored system is not reporting yet.');
});

test('Case F blocker: no detection', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain(
    'Telemetry has been received, but no detection has been generated yet.',
  );
});

test('Case G blocker: no alert', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  expect(panel).toContain('Detection exists, but no alert has been opened yet.');
});

// -- 16. Page structure -------------------
test('page does not render RuntimeSummaryPanel at top level', () => {
  const src = appSource('(product)/threat/page.tsx');
  // RuntimeSummaryPanel removed from /threat — it was the debug panel that appeared before the SaaS header
  expect(src).not.toContain('RuntimeSummaryPanel');
});

test('page uses ThreatMonitoringPanel', () => {
  const src = appSource('(product)/threat/page.tsx');
  expect(src).toContain('ThreatMonitoringPanel');
});

// -- 17. Simulator signal CTA only shown in simulator mode --------
test('simulator CTA uses neutral wording gated on isSimulatorMode (not "Generate Simulator Signal")', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  const caseEBlock = panel.slice(
    panel.indexOf('// Case E'),
    panel.indexOf('// Case F'),
  );
  expect(caseEBlock).toContain('isSimulatorMode');
  expect(caseEBlock).toContain('Create test signal');
  expect(caseEBlock).not.toContain('Generate Simulator Signal');
});

// -- 18. Page does not contradict itself (no contradiction_flags usage) --
test('page.tsx does not reference contradiction_flags', () => {
  const src = appSource('(product)/threat/page.tsx');
  expect(src).not.toContain('contradiction_flags');
});

// 19. Simulator mode changes "operational" claim text
test('"All pipeline stages" message is gated on isSimulatorMode', () => {
  const panel = appSource('threat-monitoring-panel.tsx');
  // Simulator mode must show "simulator mode" qualifier, not "operational" claim
  expect(panel).toContain('isSimulatorMode');
  expect(panel).toContain('All pipeline stages are active (simulator mode)');
  expect(panel).toContain('All pipeline stages are operational');
  // The conditional must appear before the operational copy
  const simModeIdx = panel.indexOf("'All pipeline stages are active (simulator mode)");
  const operationalIdx = panel.indexOf("'All pipeline stages are operational");
  expect(simModeIdx).toBeGreaterThan(-1);
  expect(operationalIdx).toBeGreaterThan(-1);
});
