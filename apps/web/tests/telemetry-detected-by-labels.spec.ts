import { expect, test } from '@playwright/test';
import {
  DETECTED_BY_LABELS,
  canonicalDetectedBy,
  deriveDetectedBy,
  formatDetectedBy,
  walletTransferDetectedBy,
} from '../app/(product)/monitoring-sources/[targetId]/telemetry/detected-by';

test('renders the four canonical Detected By labels', () => {
  expect(formatDetectedBy('stable_rpc_polling')).toBe('Stable RPC Polling');
  expect(formatDetectedBy('realtime_websocket')).toBe('Realtime WebSocket');
  expect(formatDetectedBy('realtime_backfill')).toBe('Realtime Backfill');
  expect(formatDetectedBy('realtime_tx_import')).toBe('Realtime Tx Import');
});

test('renders fallback and non-live labels truthfully', () => {
  expect(formatDetectedBy('quicknode_http_fast_tail')).toBe('Realtime HTTP Fast-Tail');
  expect(formatDetectedBy('quicknode_stream')).toBe('QuickNode Stream');
  expect(formatDetectedBy('simulator')).toBe('Simulator (not live)');
  expect(formatDetectedBy('replay')).toBe('Replay (not live)');
  expect(formatDetectedBy('unknown')).toBe('Unknown');
  expect(formatDetectedBy(null)).toBe('-');
  // An unmapped backend value passes through verbatim rather than being hidden.
  expect(formatDetectedBy('some_new_path')).toBe('some_new_path');
  expect(DETECTED_BY_LABELS.tx_hash_import).toBe('Realtime Tx Import');
});

test('canonicalDetectedBy maps source/ingestion spellings to canonical tags', () => {
  expect(canonicalDetectedBy('tx_hash_import')).toBe('realtime_tx_import');
  expect(canonicalDetectedBy('rpc_polling')).toBe('stable_rpc_polling');
  expect(canonicalDetectedBy('polling')).toBe('stable_rpc_polling');
  expect(canonicalDetectedBy('evm_rpc')).toBe('stable_rpc_polling');
  expect(canonicalDetectedBy('rpc_backfill')).toBe('stable_rpc_polling');
  expect(canonicalDetectedBy('realtime_websocket')).toBe('realtime_websocket');
  expect(canonicalDetectedBy('quicknode_stream')).toBe('quicknode_stream');
  expect(canonicalDetectedBy('demo')).toBeNull();
  expect(canonicalDetectedBy('')).toBeNull();
});

test('deriveDetectedBy prefers explicit facts over mappings', () => {
  expect(deriveDetectedBy({ detected_by: 'realtime_websocket' })).toBe('realtime_websocket');
  expect(
    deriveDetectedBy({ payload_json: { details: { detected_by: 'realtime_backfill' } } }),
  ).toBe('realtime_backfill');
  expect(
    deriveDetectedBy({ payload_json: { metadata: { detected_by: 'stable_rpc_polling' } } }),
  ).toBe('stable_rpc_polling');
  expect(
    deriveDetectedBy({ payload_json: { source_type: 'tx_hash_import' } }),
  ).toBe('realtime_tx_import');
  expect(
    deriveDetectedBy({ payload_json: { ingestion_source: 'rpc_polling' } }),
  ).toBe('stable_rpc_polling');
});

test('deriveDetectedBy falls back to the stable-family provider_type for live rows', () => {
  // Rows persisted before the payload stamps: no payload facts at all, but the
  // provider_type column names the stable polling writer.
  expect(
    deriveDetectedBy({
      evidence_source: 'live',
      provider_type: 'evm_activity_provider',
      payload_json: { tx_hash: '0xef5324' },
    }),
  ).toBe('stable_rpc_polling');
  expect(
    deriveDetectedBy({
      evidence_source: 'live',
      provider_type: 'evm_rpc',
      payload_json: { tx_hash: '0xef5324' },
    }),
  ).toBe('stable_rpc_polling');
  expect(
    deriveDetectedBy({
      evidence_source: 'live',
      provider_type: 'realtime_websocket',
      payload_json: { tx_hash: '0xef5324' },
    }),
  ).toBe('realtime_websocket');
  // Simulator rows must never claim a live detection path via provider_type.
  expect(
    deriveDetectedBy({
      evidence_source: 'simulator',
      provider_type: 'evm_activity_provider',
      payload_json: { tx_hash: '0xef5324' },
    }),
  ).toBeNull();
});

test('walletTransferDetectedBy never returns blank and fails closed truthfully', () => {
  // Live row with a classifiable provider: the production case — never Unknown.
  expect(
    walletTransferDetectedBy({
      evidence_source: 'live',
      provider_type: 'evm_activity_provider',
      payload_json: { tx_hash: '0xef5324' },
    }),
  ).toBe('stable_rpc_polling');
  // Simulator row names its evidence source, never a live path.
  expect(
    walletTransferDetectedBy({
      evidence_source: 'simulator',
      payload_json: { tx_hash: '0xef5324' },
    }),
  ).toBe('simulator');
  // Truly unclassifiable live row: explicit unknown (warned), never blank.
  const unclassifiable = walletTransferDetectedBy({
    evidence_source: 'live',
    provider_type: 'guided_workflow',
    payload_json: { tx_hash: '0xef5324' },
  });
  expect(unclassifiable).toBe('unknown');
  expect(unclassifiable.length).toBeGreaterThan(0);
});
