import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// Source assertions for the realtime degraded-with-fallback truth path:
// when the WSS provider fails (TLS internal error / reconnect loop), the worker
// publishes fallback_active=true with a canonical fallback provider_mode, and
// every customer-facing surface says "Realtime degraded — stable polling
// fallback active" instead of a false "Paused / Disabled" or a healthy claim.

const appDir = path.join(__dirname, '..', 'app');
const repoRoot = path.join(__dirname, '..', '..', '..');

function read(...segments: string[]): string {
  return fs.readFileSync(path.join(...segments), 'utf-8');
}

const telemetryPageSource = read(
  appDir, '(product)', 'monitoring-sources', '[targetId]', 'telemetry', 'page.tsx',
);
const contractSource = read(appDir, 'monitoring-status-contract.ts');
const healthTypesSource = read(
  appDir, '(product)', 'system-health', '_components', 'types.ts',
);
const healthPanelSource = read(
  appDir, '(product)', 'system-health', '_components', 'live-chain-monitoring-panel.tsx',
);
const workerStatusPy = read(repoRoot, 'services', 'api', 'app', 'worker_status.py');
const systemHealthPy = read(repoRoot, 'services', 'api', 'app', 'system_health.py');
const ingestorPy = read(repoRoot, 'services', 'api', 'app', 'base_realtime_ingestor.py');

const FALLBACK_PHRASE = 'Realtime degraded — stable polling fallback active';

test('telemetry page has an explicit degraded branch naming the stable polling fallback', () => {
  expect(telemetryPageSource).toContain("realtimeState === 'degraded'");
  expect(telemetryPageSource).toContain(FALLBACK_PHRASE);
  expect(telemetryPageSource).toContain('realtime_fallback_active');
  // Degraded must never fall through to the healthy 'Enabled' label: the degraded
  // branch is checked before the enabled fallback chain.
  expect(telemetryPageSource.indexOf("realtimeState === 'degraded'"))
    .toBeLessThan(telemetryPageSource.indexOf("? 'Enabled'"));
});

test('contract carries the realtime fallback facts', () => {
  expect(contractSource).toContain('fallback_active?: boolean');
  expect(contractSource).toContain('provider_mode?: string | null');
});

test('system-health panel renders the fallback path for a degraded realtime worker', () => {
  expect(healthTypesSource).toContain('fallback_active?: boolean');
  expect(healthTypesSource).toContain('worker_provider_mode?: string | null');
  expect(healthPanelSource).toContain('isDegradedWithFallback');
  expect(healthPanelSource).toContain('quicknode_http_fast_tail');
});

test('backend worker_status publishes the degraded-with-fallback headline', () => {
  expect(workerStatusPy).toContain(
    'Stable polling active. Realtime degraded — stable polling fallback active.',
  );
  expect(workerStatusPy).toContain("'fallback_active': realtime_fallback_active");
});

test('backend system health labels degraded-with-fallback truthfully', () => {
  expect(systemHealthPy).toContain('Realtime: Degraded — stable polling fallback active');
  // The watcher row must keep matching after the worker switches to a fallback
  // ingestion_mode — otherwise the card would claim no heartbeat during fallback.
  expect(systemHealthPy).toContain(
    "ingestion_mode IN ('realtime', 'http_fast_tail', 'stable_rpc_polling_fallback')",
  );
});

test('worker treats TLS internal errors as provider failure with a circuit breaker', () => {
  expect(ingestorPy).toContain('realtime_ws_provider_unhealthy');
  expect(ingestorPy).toContain('tls_internal_error');
  expect(ingestorPy).toContain('provider_circuit_open');
  expect(ingestorPy).toContain('provider_circuit_half_open');
  expect(ingestorPy).toContain("STABLE_POLLING_FALLBACK_MODE = 'stable_rpc_polling_fallback'");
});

test('realtime proof is locked to realtime_websocket / realtime_backfill only', () => {
  expect(workerStatusPy).toContain(
    "REALTIME_PROOF_DETECTED_BY: tuple[str, ...] = ('realtime_websocket', 'realtime_backfill')",
  );
  expect(workerStatusPy).toContain('def is_realtime_detection_proof');
});
