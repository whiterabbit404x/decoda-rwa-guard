import { expect, test } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

// Source assertions for the three separated Target Telemetry worker statuses:
//   1. QuickNode Stream  (webhook push — the primary realtime path)
//   2. Stable RPC Polling (always-on backup)
//   3. Legacy WebSocket   (paused or degraded)
// A paused/degraded legacy WebSocket must NEVER be presented as the main realtime
// status when the QuickNode Stream is active. The QuickNode Stream facts come from
// canonical backend fields (list_target_telemetry): quicknode_stream_state,
// last_stream_event_at (telemetry rows detected_by=quicknode_stream), and
// last_stream_block (the quicknode_stream_checkpoints latest_stream_block).

const appDir = path.join(__dirname, '..', 'app');
const repoRoot = path.join(__dirname, '..', '..', '..');

function read(...segments: string[]): string {
  return fs.readFileSync(path.join(...segments), 'utf-8');
}

const telemetryPageSource = read(
  appDir, '(product)', 'monitoring-sources', '[targetId]', 'telemetry', 'page.tsx',
);
const monitoringRunnerPy = read(repoRoot, 'services', 'api', 'app', 'monitoring_runner.py');
const workerStatusPy = read(repoRoot, 'services', 'api', 'app', 'worker_status.py');
const quicknodeStreamsPy = read(repoRoot, 'services', 'api', 'app', 'quicknode_streams.py');

// --- Telemetry page renders three DISTINCT worker rows ----------------------

test('telemetry page renders three separated worker status rows', () => {
  expect(telemetryPageSource).toContain('data-testid="worker-quicknode-stream"');
  expect(telemetryPageSource).toContain('data-testid="worker-stable-rpc-polling"');
  expect(telemetryPageSource).toContain('data-testid="worker-legacy-websocket"');
  expect(telemetryPageSource).toContain('QuickNode Stream');
  expect(telemetryPageSource).toContain('Stable RPC Polling');
  expect(telemetryPageSource).toContain('Legacy WebSocket');
});

test('QuickNode Stream row shows active state, last stream block, and last stream event', () => {
  expect(telemetryPageSource).toContain('quicknode_stream_state');
  expect(telemetryPageSource).toContain('last_stream_event_at');
  expect(telemetryPageSource).toContain('last_stream_block');
  expect(telemetryPageSource).toContain('Last stream block');
  expect(telemetryPageSource).toContain('Last stream event');
  // The primary QuickNode Stream row is ordered BEFORE the legacy WebSocket row.
  expect(telemetryPageSource.indexOf('data-testid="worker-quicknode-stream"'))
    .toBeLessThan(telemetryPageSource.indexOf('data-testid="worker-legacy-websocket"'));
});

test('Stable RPC Polling stays visible as backup with a truthful active label', () => {
  expect(telemetryPageSource).toContain('stable_polling_active');
  expect(telemetryPageSource).toContain('Active fallback');
  // The label is derived from the backend fact, never hardcoded to always-active.
  expect(telemetryPageSource).toContain('stablePollingActive');
});

// --- Requirement: legacy WebSocket degraded is not the main realtime status --

test('legacy WebSocket paused/degraded note is suppressed while QuickNode Stream is active', () => {
  // Both contextual notes are gated on the stream NOT being active, so a degraded /
  // paused WebSocket never becomes the headline while the stream is delivering.
  expect(telemetryPageSource).toContain("quicknodeStreamState !== 'active' && realtimeState === 'degraded'");
  expect(telemetryPageSource).toContain("quicknodeStreamState !== 'active' && !realtimeEnabled");
});

// --- Backend supplies the canonical separated facts ------------------------

test('backend list route returns separated QuickNode Stream + stable polling facts', () => {
  expect(monitoringRunnerPy).toContain("'quicknode_stream_state': quicknode_stream_state");
  expect(monitoringRunnerPy).toContain("'last_stream_event_at': last_stream_event_at");
  expect(monitoringRunnerPy).toContain("'last_stream_block': last_stream_block");
  expect(monitoringRunnerPy).toContain("'stable_polling_active': stable_polling_active");
  // last_realtime_event_at is scoped to the WSS realtime family, EXCLUDING the stream.
  expect(monitoringRunnerPy).toContain('d != QUICKNODE_STREAM_DETECTED_BY');
});

test('backend names QuickNode Stream as its own detection worker and reads the checkpoint', () => {
  expect(workerStatusPy).toContain("QUICKNODE_STREAM_DETECTED_BY = 'quicknode_stream'");
  expect(quicknodeStreamsPy).toContain('def load_base_stream_checkpoint');
  expect(monitoringRunnerPy).toContain('load_base_stream_checkpoint');
});

// --- Requirement 7: a stale / non-matching stream is never shown as "Active" -------

test('QuickNode Stream renders receiving and stale states, not just active', () => {
  // A green "Active" is reserved for a fresh MATCHED stream event; the two non-green
  // states ('receiving' — blocks flow but no matched transfer; 'stale' — stream
  // stopped) must both be rendered so the header never over-claims.
  expect(telemetryPageSource).toContain("quicknodeStreamState === 'receiving'");
  expect(telemetryPageSource).toContain("quicknodeStreamState === 'stale'");
  expect(telemetryPageSource).toContain('Receiving blocks — no recent matched transfer');
  expect(telemetryPageSource).toContain('Stale — stream not delivering');
});

test('backend derives active only from a fresh MATCHED stream event, else receiving / stale', () => {
  // 'active' requires a fresh per-target matched stream event; a fresh GLOBAL webhook
  // checkpoint alone yields 'receiving' (blocks flowing, no matched transfer for this
  // target) so a target served only by Stable RPC Polling never shows a green Active.
  expect(monitoringRunnerPy).toContain('_stream_event_fresh = _stream_fact_fresh(last_stream_event_at)');
  expect(monitoringRunnerPy).toContain('_stream_checkpoint_fresh = _stream_fact_fresh(quicknode_stream_checkpoint_at)');
  expect(monitoringRunnerPy).toContain("quicknode_stream_state = 'active'");
  expect(monitoringRunnerPy).toContain("quicknode_stream_state = 'receiving'");
  expect(monitoringRunnerPy).toContain("quicknode_stream_state = 'stale'");
});
