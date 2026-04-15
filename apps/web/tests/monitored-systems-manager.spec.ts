import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

function readAppFile(relativePath: string): string {
  return fs.readFileSync(path.join(__dirname, '..', 'app', relativePath), 'utf-8');
}

test('monitored systems UI separates config enabled state from runtime state', () => {
  const source = readAppFile('monitored-systems-manager.tsx');
  expect(source).toContain('Config: {system.is_enabled ? \'Enabled\' : \'Disabled\'}');
  expect(source).toContain('Runtime: {system.runtime_status}');
  expect(source).toContain("{system.is_enabled ? 'Disable' : 'Enable'}");
});

test('monitored systems UI consumes workspace summary and telemetry timestamp truthfully', () => {
  const source = readAppFile('monitored-systems-manager.tsx');
  expect(source).toContain('setSummary(payload.workspace_monitoring_summary ?? null);');
  expect(source).toContain("const telemetryLabel = summary?.last_telemetry_at ? new Date(summary.last_telemetry_at).toLocaleString() : 'Not available';");
  expect(source).toContain('summary?.freshness_status === \'fresh\'');
  expect(source).toContain('summary?.coverage_state?.reporting_systems ?? 0');
  expect(source).toContain('Live telemetry {hasLiveTelemetry ? telemetryLabel : \'unavailable\'}');
});

test('monitored systems toggle waits for backend and re-fetches state', () => {
  const source = readAppFile('monitored-systems-manager.tsx');
  expect(source).toContain('if (!response.ok)');
  expect(source).toContain('await load();');
});

test('clicking repair with fetch rejection shows error message', () => {
  const source = readAppFile('monitored-systems-manager.tsx');
  expect(source).toContain("setMessage('Repair request failed before the server responded.')");
});

test('clicking repair with non-json response shows parse error message', () => {
  const source = readAppFile('monitored-systems-manager.tsx');
  expect(source).toContain("contentType.toLowerCase().includes('application/json')");
  expect(source).toContain("setMessage('Repair response could not be parsed.')");
});

test('clicking repair with success shows success message', () => {
  const source = readAppFile('monitored-systems-manager.tsx');
  expect(source).toContain('Repair completed. ${summary.created_or_updated} monitored systems created or updated from ${summary.targets_scanned} targets scanned.');
});

test('clicking repair with reconcile success but reload failure shows partial failure message', () => {
  const source = readAppFile('monitored-systems-manager.tsx');
  expect(source).toContain("failureMessage: 'Repair request completed or failed, but refreshing monitored systems did not succeed.'");
  expect(source).toContain("setMessage('Repair request completed or failed, but refreshing monitored systems did not succeed.')");
});


test('clicking repair surfaces backend detail from flat string errors', () => {
  const source = readAppFile('monitored-systems-manager.tsx');
  expect(source).toContain('function extractErrorDetail(payload: unknown): ErrorDetail');
  expect(source).toContain("typeof value.detail === 'string' ? value.detail.trim() : ''");
  expect(source).toContain('Repair failed: ${errorDetail.message}${stageSuffix}${codeSuffix}');
});

test('clicking repair surfaces backend detail from nested FastAPI error payloads', () => {
  const source = readAppFile('monitored-systems-manager.tsx');
  expect(source).toContain("const errorObject = nestedDetail && typeof nestedDetail === 'object'");
  expect(source).toContain("typeof errorObject.detail === 'string' ? errorObject.detail.trim() : ''");
});

test('clicking repair includes stage context when present and logs non-OK payload in dev', () => {
  const source = readAppFile('monitored-systems-manager.tsx');
  expect(source).toContain("const stageSuffix = errorDetail.stage ? ` (stage: ${errorDetail.stage})` : '';");
  expect(source).toContain("console.debug('[monitored-systems] reconcile non-OK payload', errorPayload);");
});

test('monitored systems UI exposes repair action, status line, and reconcile diagnostics', () => {
  const source = readAppFile('monitored-systems-manager.tsx');
  expect(source).toContain("const effectiveApiUrl = runtimeApiUrl || apiUrl;");
  expect(source).toContain("const reconcileUrl = '/api/monitoring/systems/reconcile';");
  expect(source).toContain('Repair monitored systems');
  expect(source).toContain('Repairing monitored systems…');
  expect(source).toContain('reconcileSummary');
  expect(source).toContain('created_or_updated');
  expect(source).toContain('invalid_reasons');
  expect(source).toContain('skipped_reasons');
  expect(source).toContain("console.debug('[monitored-systems] reconcile request started')");
  expect(source).toContain("console.debug('[monitored-systems] reconcile response received')");
  expect(source).toContain("console.debug('[monitored-systems] reconcile response parsed')");
  expect(source).toContain("console.debug('[monitored-systems] reloading monitored systems')");
  expect(source).toContain("console.debug('[monitored-systems] repair click received')");
  expect(source).toContain("console.debug('[monitored-systems] client build tag', monitoredSystemsClientBuildTag)");
  expect(source).toContain("console.debug('[monitored-systems] runtime config apiUrl', runtimeApiUrl || '(missing)')");
  expect(source).toContain("console.debug('[monitored-systems] server-rendered apiUrl', apiUrl || '(missing)')");
  expect(source).toContain("console.debug('[monitored-systems] effective apiUrl', effectiveApiUrl || '(missing)')");
  expect(source).toContain("console.info('[monitored-systems] reconcile URL', reconcileUrl)");
  expect(source).toContain('data-monitored-systems-build={monitoredSystemsClientBuildTag}');
  expect(source).toContain('data-testid="repair-click-debug"');
  expect(source).toContain("console.debug('[monitored-systems] reconcile HTTP status', response.status)");
  expect(source).toContain("console.debug('[monitored-systems] reconcile response content-type', contentType || '(none)')");
  expect(source).toContain("console.debug('[monitored-systems] reconcile parsed payload', payload)");
  expect(source).toContain("console.debug('[monitored-systems] reconcile reload result count', reloadedSystems?.length ?? 0)");
  expect(source).toContain("console.debug('[monitored-systems] finally clearing isReconciling')");
  expect(source).toContain('Repair reported success, but no monitored systems were visible after reload.');
});

test('runReconcile always clears loading state and fetches with timeout controls', () => {
  const source = readAppFile('monitored-systems-manager.tsx');
  expect(source).toContain('const REQUEST_TIMEOUT_MS = 15000;');
  expect(source).toContain('async function fetchWithTimeout');
  expect(source).toContain('controller.abort()');
  expect(source).toContain('finally {');
  expect(source).toContain('setIsReconciling(false);');
});
