import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const appDir = path.join(__dirname, '..', 'app');
const repoRoot = path.join(__dirname, '..', '..', '..');

function read(...segments: string[]): string {
  return fs.readFileSync(path.join(...segments), 'utf-8');
}

const contractSource = read(appDir, 'monitoring-status-contract.ts');
const truthSource = read(appDir, 'workspace-monitoring-truth.ts');
const modeBannerSource = read(appDir, 'workspace-monitoring-mode-banner.tsx');
const runtimeBannerSource = read(appDir, 'components', 'runtime-banner.tsx');
const reasonContextSource = read(appDir, 'runtime-summary-context.tsx');
const telemetryPageSource = read(
  appDir, '(product)', 'monitoring-sources', '[targetId]', 'telemetry', 'page.tsx',
);
const workerStatusPy = read(repoRoot, 'services', 'api', 'app', 'worker_status.py');

// --- Contract carries the separated worker status shape ---------------------

test('contract defines WorkerStatusSummary with the three separated workers', () => {
  expect(contractSource).toContain('export type WorkerStatusSummary');
  expect(contractSource).toContain('stable_polling');
  expect(contractSource).toContain('realtime');
  expect(contractSource).toContain('provider_realtime');
  expect(contractSource).toContain('monitoring_source_live');
  // MonitoringRuntimeStatus surfaces the canonical worker_status + realtime flag.
  expect(contractSource).toContain('worker_status?: WorkerStatusSummary');
  expect(contractSource).toContain('realtime_enabled?: boolean');
});

test('truth resolver reads worker_status and realtime_enabled from the top-level status', () => {
  expect(truthSource).toContain('worker_status');
  expect(truthSource).toContain('realtime_enabled');
  expect(truthSource).toContain('statusRecord?.worker_status');
  expect(truthSource).toContain('Boolean(statusRecord?.realtime_enabled)');
});

// --- Banner wording: uses separated worker status, not a generic heartbeat ---

test('workspace banner surfaces the separated worker status headline', () => {
  // The banner renders worker_status.headline (the truthful "Stable polling active.
  // Realtime WebSocket paused." line) rather than inventing its own.
  expect(modeBannerSource).toContain('worker_status');
  expect(modeBannerSource).toContain('ws.headline');
  expect(modeBannerSource).toContain('workerStatusBannerLine');
  expect(modeBannerSource).toContain('data-testid="worker-status-line"');
});

test('runtime banner does not show generic heartbeat-stale when stable polling is active', () => {
  expect(runtimeBannerSource).toContain('worker_status');
  expect(runtimeBannerSource).toContain('stablePollingActive');
  expect(runtimeBannerSource).toContain('suppressHeartbeatLimitation');
  // A Workers field surfaces the separated worker headline.
  expect(runtimeBannerSource).toContain('label="Workers"');
});

test('stale_heartbeat reason copy is scoped to the stable RPC polling worker', () => {
  expect(reasonContextSource).toContain('Stable RPC polling worker heartbeat is stale');
  // It must NOT be the old undifferentiated wording.
  expect(reasonContextSource).not.toContain('Worker heartbeat is stale. The monitoring worker may have stopped');
});

// --- Exact acceptance phrase is locked at the backend source of truth -------

test('backend worker_status produces the exact acceptance headline', () => {
  expect(workerStatusPy).toContain('Stable polling active. Realtime WebSocket paused.');
  expect(workerStatusPy).toContain('BASE_REALTIME_ENABLED_not_true');
  expect(workerStatusPy).toContain('monitoring_source_live');
});

// --- Telemetry page: separated detection-path facts -------------------------

test('telemetry page renders a worker-status strip with separated detection facts', () => {
  expect(telemetryPageSource).toContain('data-testid="telemetry-worker-status"');
  expect(telemetryPageSource).toContain('realtime_enabled');
  expect(telemetryPageSource).toContain('last_stable_poll_at');
  expect(telemetryPageSource).toContain('last_realtime_event_at');
  expect(telemetryPageSource).toContain('Last stable poll');
  expect(telemetryPageSource).toContain('Last realtime event');
  expect(telemetryPageSource).toContain('Paused / Disabled');
  expect(telemetryPageSource).toContain('Realtime paused; stable polling active');
});

test('telemetry page keeps the Detected By column unchanged', () => {
  // Detected By column + label map must remain (Stable RPC Polling / Realtime WebSocket / Realtime Backfill).
  expect(telemetryPageSource).toContain("'Detected By'");
  expect(telemetryPageSource).toContain('stable_rpc_polling');
  expect(telemetryPageSource).toContain('realtime_websocket');
  expect(telemetryPageSource).toContain('realtime_backfill');
});
