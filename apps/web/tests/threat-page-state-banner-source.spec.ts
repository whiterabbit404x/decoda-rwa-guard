import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

test('threat page maps snapshot failures to fetch-error banner copy', () => {
  const threat = fs.readFileSync(path.join(__dirname, '..', 'app', 'threat-operations-panel.tsx'), 'utf-8');

  expect(threat).toContain('if (snapshotError) {');
  expect(threat).toContain("return 'fetch_error';");
  expect(threat).toContain("if (state === 'fetch_error') {");
  expect(threat).toContain('Telemetry retrieval degraded');
  expect(threat).toContain('Backend telemetry/runtime retrieval failed, so monitoring data is temporarily unavailable.');
});

test('threat page forwards runtime and summary configuration diagnostics to unconfigured banner copy', () => {
  const threat = fs.readFileSync(path.join(__dirname, '..', 'app', 'threat-operations-panel.tsx'), 'utf-8');

  expect(threat).toContain('const summaryConfigurationReason = runtimeSummary?.configuration_reason ?? null;');
  expect(threat).toContain('const summaryConfigurationReasonCodes = Array.isArray(runtimeSummary?.configuration_reason_codes)');
  expect(threat).toContain('const configurationReason = runtimeStatusSnapshot?.configuration_reason ?? summaryConfigurationReason;');
  expect(threat).toContain('const configurationReasonCodes = Array.isArray(runtimeStatusSnapshot?.configuration_reason_codes)');
  expect(threat).toContain('configurationReason,');
  expect(threat).toContain('configurationReasonCodes,');
  expect(threat).toContain('summaryConfigurationReason,');
  expect(threat).toContain('summaryConfigurationReasonCodes,');
  expect(threat).toContain('configurationReason={configurationReason}');
});
