import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

test('threat panel renders telemetry copy from truth model timestamps only', () => {
  const threat = fs.readFileSync(path.join(__dirname, '..', 'app', 'threat-operations-panel.tsx'), 'utf-8');
  expect(threat).toContain("const truth = feed.runtimeStatus?.workspace_monitoring_summary;");
  expect(threat).toContain("const lastTelemetryAt = truth?.last_telemetry_at ?? null;");
  expect(threat).toContain("const hasLiveTelemetry = Boolean(lastTelemetryAt && truth?.freshness_status === 'fresh' && reportingSystems > 0);");
  expect(threat).toContain("{hasLiveTelemetry ? `Live telemetry ${telemetryLabel}` : 'Current telemetry unavailable'}");
  expect(threat).toContain('Guarded fallback copy active');
});
