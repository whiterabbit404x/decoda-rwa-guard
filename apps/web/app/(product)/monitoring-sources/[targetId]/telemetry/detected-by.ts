// Canonical "Detected By" resolution + labels for wallet-transfer telemetry.
//
// Mirrors the backend classifier (services/api/app/worker_status.py:
// resolve_telemetry_detected_by + classify_wallet_transfer_detected_by) so a
// row renders the same truthful detection path whether the API already
// normalized it or the row came from an older API build. Extracted from the
// telemetry page so the mapping is unit-testable (tests/telemetry-detected-by-
// labels.spec.ts).

export type DetectedByRow = {
  detected_by?: string | null;
  detected_by_source?: string | null;
  provider_type?: string | null;
  evidence_source?: string | null;
  payload_json?: Record<string, unknown> | null;
};

export const DETECTED_BY_LABELS: Record<string, string> = {
  realtime_websocket: 'Realtime WebSocket',
  realtime_backfill: 'Realtime Backfill',
  realtime_tx_import: 'Realtime Tx Import',
  quicknode_http_fast_tail: 'Realtime HTTP Fast-Tail',
  realtime_http_fast_tail: 'Realtime HTTP Fast-Tail',
  quicknode_stream: 'QuickNode Stream',
  stable_rpc_polling: 'Stable RPC Polling',
  tx_hash_import: 'Realtime Tx Import',
  simulator: 'Simulator (not live)',
  replay: 'Replay (not live)',
  unknown: 'Unknown',
};

export function formatDetectedBy(val: string | null | undefined): string {
  if (!val) return '-';
  return DETECTED_BY_LABELS[val] ?? val;
}

// Canonical detection paths that come from the realtime worker family.
export const REALTIME_DETECTED_BY = new Set([
  'realtime_websocket',
  'realtime_backfill',
  'realtime_tx_import',
  'quicknode_http_fast_tail',
  'realtime_http_fast_tail',
  'quicknode_stream',
]);

// Payload source/ingestion values that map onto a canonical detected_by tag —
// mirrors worker_status._canonical_detected_by_or_none on the backend.
export function canonicalDetectedBy(raw: string | null | undefined): string | null {
  const v = (raw ?? '').trim().toLowerCase();
  if (!v) return null;
  if (REALTIME_DETECTED_BY.has(v) || v === 'stable_rpc_polling') return v;
  if (v === 'tx_hash_import') return 'realtime_tx_import';
  if (v === 'polling' || v === 'rpc_polling' || v === 'evm_rpc' || v === 'rpc_backfill') {
    return 'stable_rpc_polling';
  }
  return null;
}

// telemetry_events.provider_type values written by the stable-polling family
// (mirrors worker_status.STABLE_PROVIDER_TYPES; 'evm_rpc' resolves through
// canonicalDetectedBy already). Realtime writers always stamp payload markers,
// so a live row with a stable-family provider and no marker is stable polling.
const STABLE_PROVIDER_TYPES = new Set(['evm_activity_provider', 'monitoring_provider', 'evm_rpc']);

function asRecord(val: unknown): Record<string, unknown> | null {
  return val && typeof val === 'object' && !Array.isArray(val)
    ? (val as Record<string, unknown>)
    : null;
}

function extractField(
  payload: Record<string, unknown> | null | undefined,
  ...keys: string[]
): string | null {
  if (!payload) return null;
  for (const key of keys) {
    const val = payload[key];
    if (typeof val === 'string' && val.length > 0) return val;
    if (typeof val === 'number') return String(val);
  }
  return null;
}

// Resolve the Detected By value for a row: top-level field first, then the
// payload's detected_by, then details/metadata copies, then source/ingestion
// mappings, then the row's provider_type column (live rows only — a simulator
// row must never claim a live detection path). Returns null only when no fact
// names a detection path — callers render an explicit "Unknown" for wallet
// transfers, never a blank cell.
export function deriveDetectedBy(row: DetectedByRow): string | null {
  const payload = row.payload_json;
  const details = asRecord(payload?.details);
  const metadata = asRecord(payload?.metadata);
  const candidates = [
    row.detected_by,
    payload ? extractField(payload, 'detected_by') : null,
    details ? extractField(details, 'detected_by') : null,
    metadata ? extractField(metadata, 'detected_by') : null,
  ];
  for (const c of candidates) {
    const v = (c ?? '').trim();
    if (v) return canonicalDetectedBy(v) ?? v;
  }
  const mappable = [
    payload ? extractField(payload, 'source_type') : null,
    details ? extractField(details, 'source_type') : null,
    metadata ? extractField(metadata, 'source_type') : null,
    payload ? extractField(payload, 'ingestion_source', 'ingestion_method') : null,
  ];
  for (const c of mappable) {
    const mapped = canonicalDetectedBy(c);
    if (mapped) return mapped;
  }
  // Row-level fallback for rows persisted before the payload stamps existed:
  // the provider_type column names the writer. Realtime tags map to
  // themselves; stable-family writers map to stable_rpc_polling. Gated to
  // live evidence so simulator/replay rows keep naming their evidence source.
  const evidence = (row.evidence_source ?? '').trim().toLowerCase();
  if (!evidence || evidence === 'live') {
    const provider = (row.provider_type ?? '').trim().toLowerCase();
    const mappedProvider = canonicalDetectedBy(provider);
    if (mappedProvider) return mappedProvider;
    if (provider && STABLE_PROVIDER_TYPES.has(provider)) return 'stable_rpc_polling';
  }
  return null;
}

// Fail-closed display value for wallet-transfer rows: never blank. Non-live
// rows name their evidence source; unattributable live rows say Unknown —
// which, with the backend normalization + 0118 backfill in place, remains
// only for rows the backend truly cannot classify.
export function walletTransferDetectedBy(row: DetectedByRow): string {
  const derived = deriveDetectedBy(row);
  if (derived) return derived;
  const evidence = (row.evidence_source ?? '').trim().toLowerCase();
  return evidence && evidence !== 'live' ? evidence : 'unknown';
}
