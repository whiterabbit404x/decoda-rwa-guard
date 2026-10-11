'use client';

import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';

import { TableShell } from '../../../../components/ui-primitives';
import { usePilotAuth } from '../../../../pilot-auth-context';
import { connectTelemetryStream, type TelemetryStreamStatus } from '../../../../telemetry-stream-client';
import {
  REALTIME_DETECTED_BY,
  deriveDetectedBy,
  formatDetectedBy,
  walletTransferDetectedBy,
} from './detected-by';

type TelemetryRow = {
  id: string;
  workspace_id?: string | null;
  target_id?: string | null;
  provider_type?: string | null;
  source_type?: string | null;
  detected_by?: string | null;
  detected_by_source?: string | null;
  provider_mode?: string | null;
  observed_latency_seconds?: number | null;
  evidence_source?: string | null;
  chain_id?: string | null;
  block_number?: number | null;
  observed_at?: string | null;
  ingested_at?: string | null;
  payload_json?: Record<string, unknown> | null;
};

type QuickFilter = 'all' | 'wallet_transfers' | 'rpc_polling' | 'alerts_only' | 'live_evidence_only';

const QUICK_FILTERS: Array<{ id: QuickFilter; label: string }> = [
  { id: 'all', label: 'All' },
  { id: 'wallet_transfers', label: 'Wallet transfers' },
  { id: 'rpc_polling', label: 'RPC polling' },
  { id: 'alerts_only', label: 'Alerts only' },
  { id: 'live_evidence_only', label: 'Live evidence only' },
];

const HEADERS = [
  'Event Type',
  'Detected By',
  'Tx Hash',
  'From',
  'To',
  'Amount',
  'Chain ID',
  'Block Number',
  'Observed At',
  'Evidence Source',
  'Details',
];

// Detected By labels + resolution shared with tests: ./detected-by.ts
// (mirrors worker_status.classify_wallet_transfer_detected_by on the backend).

// Shape of POST /api/ops/monitoring/diagnose-tx (backend diagnose_wallet_transaction).
type TxDiagnosis = {
  tx_found?: boolean;
  block_number?: number | null;
  live_tail_from_block?: number | null;
  live_tail_to_block?: number | null;
  realtime_scanned_spans?: number[][];
  was_block_scanned?: boolean;
  below_realtime_checkpoint?: boolean;
  rate_limited_at_time?: boolean | string;
  existing_detected_by?: string | null;
  realtime_duplicate_skipped?: boolean;
  realtime_verdict?: string;
  receipt_status?: number | null;
};

// Human-readable text for the backend's canonical realtime_verdict values
// (worker_status.classify_realtime_tx_verdict). Dynamic suffixes carry the
// detecting path, so match on prefix and name the path with its UI label.
function formatRealtimeVerdict(verdict: string | null | undefined): string {
  const v = (verdict ?? '').trim();
  if (!v) return 'No verdict returned';
  if (v === 'transaction_not_found') return 'Transaction not found on the chain RPC';
  if (v === 'not_matched_no_watched_wallet_in_tx')
    return 'Not matched — no monitored wallet is in this transaction';
  if (v === 'already_exists_stable_rpc_polling_realtime_duplicate_skipped')
    return 'Detected by Stable RPC Polling — realtime skipped it as a duplicate';
  if (v.startsWith('matched_and_persisted_by_'))
    return `Realtime matched — detected by ${formatDetectedBy(v.slice('matched_and_persisted_by_'.length))}`;
  if (v.startsWith('outside_scanned_window_imported_by_'))
    return `Imported — block was outside the scanned window; recovered by ${formatDetectedBy(v.slice('outside_scanned_window_imported_by_'.length))}`;
  if (v.startsWith('already_exists_detected_by_'))
    return `Already persisted — detected by ${formatDetectedBy(v.slice('already_exists_detected_by_'.length))}`;
  if (v === 'scanned_but_not_persisted_check_matching')
    return 'Block was scanned but no row was persisted — check wallet matching';
  if (v === 'missed_provider_rate_limited')
    return 'Missed by realtime — provider was rate-limited when the tx landed (stable polling is the fallback)';
  if (v === 'outside_scanned_window_not_yet_imported')
    return 'Outside the scanned window — not yet imported (run tx import to recover)';
  if (v === 'pending_forward_scan') return 'Pending — block is ahead of the forward scan';
  return v;
}

function formatSpans(spans: number[][] | undefined): string | null {
  if (!Array.isArray(spans) || spans.length === 0) return null;
  const parts = spans
    .filter((s) => Array.isArray(s) && s.length === 2)
    .map((s) => `${s[0]}–${s[1]}`);
  return parts.length > 0 ? parts.join(', ') : null;
}

function fmt(value?: string | null): string {
  if (!value) return '-';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '-';
  return parsed.toLocaleString();
}

function safeJson(value: unknown): string {
  if (value == null) return '-';
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
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

function shortenHash(hash: string): string {
  if (hash.length <= 12) return hash;
  return `${hash.slice(0, 8)}...${hash.slice(-4)}`;
}

function shortenAddress(addr: string): string {
  if (addr.length <= 12) return addr;
  return `${addr.slice(0, 6)}...${addr.slice(-4)}`;
}

type EventKind = 'wallet_transfer' | 'block_poll' | 'unknown';

function classifyEvent(
  payload: Record<string, unknown> | null | undefined,
  sourceType?: string | null,
): EventKind {
  if (!payload) return sourceType === 'rpc_polling' ? 'block_poll' : 'unknown';
  const txHash = extractField(payload, 'tx_hash', 'transactionHash', 'hash');
  const fromAddr = extractField(payload, 'from', 'from_address', 'fromAddress');
  const toAddr = extractField(payload, 'to', 'to_address', 'toAddress');
  if (txHash || (fromAddr && toAddr)) return 'wallet_transfer';
  if (
    payload.telemetry_kind === 'coverage' ||
    sourceType === 'rpc_polling' ||
    'eth_blockNumber' in payload ||
    typeof payload.result === 'string'
  )
    return 'block_poll';
  return 'unknown';
}

function matchesSearch(row: TelemetryRow, query: string): boolean {
  if (!query.trim()) return true;
  const q = query.toLowerCase().trim();
  const payload = row.payload_json;
  const txHash = extractField(payload, 'tx_hash', 'transactionHash', 'hash');
  const fromAddr = extractField(payload, 'from', 'from_address', 'fromAddress');
  const toAddr = extractField(payload, 'to', 'to_address', 'toAddress');
  const blockNum =
    row.block_number != null
      ? String(row.block_number)
      : extractField(payload, 'block_number', 'blockNumber');
  const eventType = row.source_type ?? '';
  return Boolean(
    txHash?.toLowerCase().includes(q) ||
      fromAddr?.toLowerCase().includes(q) ||
      toAddr?.toLowerCase().includes(q) ||
      blockNum?.includes(q) ||
      eventType?.toLowerCase().includes(q) ||
      row.id?.toLowerCase().includes(q),
  );
}

function matchesQuickFilter(row: TelemetryRow, filter: QuickFilter): boolean {
  if (filter === 'all') return true;
  const kind = classifyEvent(row.payload_json, row.source_type);
  if (filter === 'wallet_transfers') return kind === 'wallet_transfer';
  if (filter === 'rpc_polling') return kind === 'block_poll';
  if (filter === 'alerts_only') return kind === 'wallet_transfer';
  if (filter === 'live_evidence_only') return row.evidence_source === 'live';
  return true;
}

const BASE_CHAIN_ID = '8453';
const BASESCAN_TX_BASE = 'https://basescan.org/tx/';

function TelemetryDetailModal({
  row,
  onClose,
  monitoredAddress,
}: {
  row: TelemetryRow;
  onClose: () => void;
  monitoredAddress?: string | null;
}) {
  const payload = row.payload_json;
  const kind = classifyEvent(payload, row.source_type);
  const jsonString = safeJson(payload);

  // Full monitored address: prefer the target-level value, fall back to the
  // payload's asset_context so the exact watched wallet is always visible.
  const assetContext =
    payload && typeof payload === 'object'
      ? ((payload as Record<string, unknown>).asset_context as Record<string, unknown> | undefined)
      : undefined;
  const monitoredAddressFull =
    monitoredAddress ??
    (assetContext && typeof assetContext.asset_identifier === 'string'
      ? (assetContext.asset_identifier as string)
      : null);

  const txHash = extractField(payload, 'tx_hash', 'transactionHash', 'hash');
  const fromAddr = extractField(payload, 'from', 'from_address', 'fromAddress');
  const toAddr = extractField(payload, 'to', 'to_address', 'toAddress');
  const amount = extractField(payload, 'amount', 'value', 'amount_wei');
  const blockNum =
    row.block_number != null
      ? String(row.block_number)
      : extractField(payload, 'block_number', 'blockNumber');

  const [copiedJson, setCopiedJson] = useState(false);
  const [copiedTx, setCopiedTx] = useState(false);

  // Exact tx-hash detection-path diagnosis: fetches the tx by hash server-side,
  // compares tx.blockNumber to the realtime worker's scanned live-tail windows,
  // and reports whether it was realtime matched, imported, stable-polling
  // detected, or duplicate-skipped.
  const { authHeaders } = usePilotAuth();
  const [diagnosis, setDiagnosis] = useState<TxDiagnosis | null>(null);
  const [diagnosing, setDiagnosing] = useState(false);
  const [diagnosisError, setDiagnosisError] = useState('');

  const runDiagnosis = useCallback(() => {
    if (!txHash || diagnosing) return;
    setDiagnosing(true);
    setDiagnosisError('');
    fetch('/api/ops/monitoring/diagnose-tx', {
      method: 'POST',
      headers: { ...authHeaders(), 'Content-Type': 'application/json' },
      cache: 'no-store',
      body: JSON.stringify({ tx_hash: txHash }),
    })
      .then(async (res) => {
        const payload = await res.json().catch(() => ({}));
        if (!res.ok) {
          const detail =
            typeof (payload as { detail?: unknown })?.detail === 'string'
              ? ((payload as { detail: string }).detail)
              : `HTTP ${res.status}`;
          setDiagnosisError(`Diagnosis failed: ${detail}`);
          return;
        }
        setDiagnosis(payload as TxDiagnosis);
      })
      .catch((err: unknown) => {
        setDiagnosisError(
          `Network error: ${err instanceof Error ? err.message : 'unknown error'}`,
        );
      })
      .finally(() => setDiagnosing(false));
  }, [txHash, diagnosing, authHeaders]);

  const copyJson = useCallback(() => {
    navigator.clipboard.writeText(jsonString).then(() => {
      setCopiedJson(true);
      setTimeout(() => setCopiedJson(false), 2000);
    }).catch(() => {});
  }, [jsonString]);

  const copyTxHash = useCallback(() => {
    if (!txHash) return;
    navigator.clipboard.writeText(txHash).then(() => {
      setCopiedTx(true);
      setTimeout(() => setCopiedTx(false), 2000);
    }).catch(() => {});
  }, [txHash]);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [onClose]);

  const isBaseScan = row.chain_id === BASE_CHAIN_ID;

  const eventTypeLabel =
    kind === 'wallet_transfer'
      ? 'Wallet transfer detected'
      : kind === 'block_poll'
        ? 'RPC polling heartbeat'
        : row.source_type ?? null;

  // Wallet transfers must always name their detection path (fail-closed to
  // "Unknown"); other event kinds only show it when a fact resolves.
  const detectedByValue =
    kind === 'wallet_transfer' ? walletTransferDetectedBy(row) : deriveDetectedBy(row);
  const detectedByLabel = detectedByValue ? formatDetectedBy(detectedByValue) : null;
  const providerMode = row.provider_mode ?? extractField(row.payload_json, 'provider_mode');
  const latencySeconds =
    row.observed_latency_seconds != null
      ? String(row.observed_latency_seconds)
      : extractField(row.payload_json, 'observed_latency_seconds');

  const summaryFields: Array<[string, string | null]> = [
    ['Event type', eventTypeLabel],
    ['Detected by', detectedByLabel],
    ['Source type', row.source_type ?? null],
    ['Provider type', row.provider_type ?? null],
    ['Provider mode', providerMode],
    ['Latency (s)', latencySeconds],
    ['Chain ID', row.chain_id ?? null],
    ['Block number', blockNum],
    ['Observed at', row.observed_at ? fmt(row.observed_at) : null],
    ['Evidence source', row.evidence_source ?? null],
    ['Monitored address (full)', monitoredAddressFull],
    ['From address', fromAddr],
    ['To address', toAddr],
    ['Amount', amount],
  ];

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Telemetry event details"
      className="msrcModal"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="msrcModalPanel">
        {/* Modal header */}
        <div className="msrcModalHead">
          <div className="msrcModalHeadText">
            <h2 className="msrcModalTitle">
              Telemetry Event Details
            </h2>
            <p className="msrcModalId">
              {row.id}
            </p>
          </div>
          <button
            type="button"
            aria-label="Close"
            onClick={onClose}
            className="msrcModalClose"
          >
            ✕
          </button>
        </div>

        {/* Event classification banner */}
        {kind === 'wallet_transfer' && (
          <div className="msrcKindBanner msrcKindBanner--transfer">
            Wallet transfer detected
          </div>
        )}
        {kind === 'block_poll' && (
          <div className="msrcKindBanner msrcKindBanner--poll">
            RPC polling heartbeat — no wallet transfer detected
          </div>
        )}

        {/* Human-readable summary grid */}
        <div className="msrcSummaryGrid">
          {summaryFields
            .filter(([, v]) => v != null && v !== '')
            .map(([label, value]) => (
              <Fragment key={label}>
                <span className="msrcSummaryKey">
                  {label}:
                </span>
                <code className="msrcSummaryVal">
                  {value}
                </code>
              </Fragment>
            ))}

          {/* Transaction hash with optional Basescan link */}
          {txHash && (
            <Fragment key="tx_hash">
              <span className="msrcSummaryKey">
                Transaction hash:
              </span>
              <span className="msrcSummaryTx">
                <code className="msrcSummaryVal">
                  {txHash}
                </code>
                {isBaseScan && (
                  <a
                    href={`${BASESCAN_TX_BASE}${txHash}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="msrcExtLink"
                  >
                    View on Basescan ↗
                  </a>
                )}
              </span>
            </Fragment>
          )}
        </div>

        {/* Exact tx-hash detection-path diagnosis (wallet transfers only) */}
        {kind === 'wallet_transfer' && txHash ? (
          <div data-testid="tx-detection-diagnosis" className="msrcDiagnosis">
            <div className="msrcDiagnosisHead">
              <span className="msrcSectionLabel">
                Detection Path Diagnosis
              </span>
              <button
                type="button"
                onClick={runDiagnosis}
                disabled={diagnosing}
                className="msrcGhostBtn"
              >
                {diagnosing
                  ? 'Checking scan windows…'
                  : diagnosis
                    ? 'Re-run diagnosis'
                    : 'Diagnose this transaction'}
              </button>
            </div>
            {!diagnosis && !diagnosing && !diagnosisError ? (
              <p className="msrcDiagnosisHint">
                Fetches this tx by hash, compares its block number to the realtime
                worker&apos;s scanned live-tail windows, and reports whether it was realtime
                matched, imported, stable-polling detected, or duplicate-skipped.
              </p>
            ) : null}
            {diagnosisError ? (
              <p className="msrcDiagnosisError">
                {diagnosisError}
              </p>
            ) : null}
            {diagnosis ? (
              <div className="msrcDiagnosisResult">
                <div className="msrcVerdict">
                  {formatRealtimeVerdict(diagnosis.realtime_verdict)}
                </div>
                <div className="msrcDiagnosisGrid">
                  <span className="muted">Tx block:</span>
                  <code className="msrcMono">
                    {diagnosis.block_number != null ? diagnosis.block_number : 'not found'}
                  </code>
                  <span className="muted">Last live-tail window:</span>
                  <code className="msrcMono">
                    {diagnosis.live_tail_from_block != null && diagnosis.live_tail_to_block != null
                      ? `${diagnosis.live_tail_from_block}–${diagnosis.live_tail_to_block}${
                          diagnosis.block_number != null
                            ? diagnosis.block_number >= diagnosis.live_tail_from_block &&
                              diagnosis.block_number <= diagnosis.live_tail_to_block
                              ? ' (includes tx block)'
                              : ' (does not include tx block)'
                            : ''
                        }`
                      : 'no live-tail window recorded'}
                  </code>
                  <span className="muted">Scanned spans:</span>
                  <code className="msrcMono msrcBreak">
                    {formatSpans(diagnosis.realtime_scanned_spans) ?? 'none recorded'}
                  </code>
                  <span className="muted">Block was scanned:</span>
                  <code className="msrcMono">
                    {diagnosis.was_block_scanned === true
                      ? 'yes'
                      : diagnosis.was_block_scanned === false
                        ? 'no'
                        : 'unknown'}
                  </code>
                  <span className="muted">Persisted row detected by:</span>
                  <code className="msrcMono">
                    {diagnosis.existing_detected_by
                      ? formatDetectedBy(diagnosis.existing_detected_by)
                      : 'no persisted row found'}
                  </code>
                  {diagnosis.realtime_duplicate_skipped ? (
                    <>
                      <span className="muted">Duplicate handling:</span>
                      <code className="msrcMono">
                        realtime duplicate skipped — first detector kept
                      </code>
                    </>
                  ) : null}
                  {diagnosis.rate_limited_at_time === true ? (
                    <>
                      <span className="muted">Rate limited at tx time:</span>
                      <code className="msrcMono">yes</code>
                    </>
                  ) : null}
                </div>
              </div>
            ) : null}
          </div>
        ) : null}

        {/* Raw Response toolbar */}
        <div className="msrcRawHead">
          <span className="msrcSectionLabel">
            Raw Response
          </span>
          <div className="msrcRawActions">
            {txHash && (
              <button
                type="button"
                onClick={copyTxHash}
                className={copiedTx ? 'msrcGhostBtn msrcGhostBtn--done' : 'msrcGhostBtn'}
              >
                {copiedTx ? 'Copied!' : 'Copy Tx Hash'}
              </button>
            )}
            <button
              type="button"
              onClick={copyJson}
              className={copiedJson ? 'msrcGhostBtn msrcGhostBtn--done' : 'msrcGhostBtn'}
            >
              {copiedJson ? 'Copied!' : 'Copy JSON'}
            </button>
          </div>
        </div>

        {/* JSON viewer on the inset base surface (var(--bg-base)), monospace. This
            modal only mounts on click, so the inline token is applied client-side. */}
        <pre className="msrcJson" style={{ background: 'var(--bg-base)', fontFamily: 'var(--font-mono), monospace' }}>
          {jsonString}
        </pre>
      </div>
    </div>
  );
}

const PAGE_SIZE = 50;

// Interval for the HTTP refresh fallback that runs only while the SSE stream is not
// live. Short enough to surface a row persisted during an SSE outage within seconds,
// but only paid while the stream is actually down (cleared once it reconnects).
const HTTP_FALLBACK_POLL_MS = 10000;

// Map quick filters to backend event_type_filter values for server-side scoping.
// wallet_transfers → backend maps to IN('wallet_transfer_detected','native_transfer')
// alerts_only → backend joins with alerts to return only alert-linked telemetry
const QUICK_FILTER_TO_EVENT_TYPE: Partial<Record<QuickFilter, string>> = {
  wallet_transfers: 'wallet_transfers',
  rpc_polling: 'rpc_polling',
  alerts_only: 'alerts_only',
};

function buildTelemetryUrl(
  targetId: string,
  q: string,
  quickFilter: QuickFilter,
  page: number,
): string {
  const base = `/api/monitoring/targets/${encodeURIComponent(targetId)}/telemetry`;
  const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(page * PAGE_SIZE) });
  const trimmed = q.trim();
  if (trimmed) params.set('q', trimmed);
  const etf = QUICK_FILTER_TO_EVENT_TYPE[quickFilter];
  if (etf) params.set('event_type_filter', etf);
  return `${base}?${params.toString()}`;
}

// --- Real-time telemetry (SSE) helpers -------------------------------------
// The /stream/telemetry SSE pushes a compact event per newly-persisted row (see
// services/api/app/telemetry_realtime.build_telemetry_stream_event). These map it
// into the same TelemetryRow shape the table already renders, and decide whether a
// live event belongs in the current view (so an active search/filter is respected).

const WALLET_TRANSFER_EVENT_TYPES = new Set(['wallet_transfer_detected', 'native_transfer']);

type TelemetryStreamPayload = {
  type?: string;
  telemetry_id?: string;
  target_id?: string;
  workspace_id?: string;
  event_type?: string;
  detected_by?: string | null;
  tx_hash?: string | null;
  from?: string | null;
  to?: string | null;
  amount?: string | null;
  chain_id?: string | null;
  block_number?: number | null;
  observed_at?: string | null;
  ingested_at?: string | null;
  evidence_source?: string | null;
};

// Deterministic dedupe key so the same transfer seen by two paths (or an SSE event
// that also arrives via the next HTTP refetch) collapses to one row.
function liveRowKey(row: TelemetryRow): string {
  const payload = row.payload_json ?? {};
  const tx = String((payload as Record<string, unknown>).tx_hash ?? '').toLowerCase();
  if (tx) return `${row.chain_id ?? ''}:${tx}:${(row.source_type ?? '').toLowerCase()}`;
  return `id:${row.id}`;
}

// Turn a validated SSE telemetry envelope into a table row, or null if it is not a
// telemetry event for this target. Cross-target / cross-workspace / non-telemetry
// envelopes are rejected here so they never reach the table.
function normalizeLiveTelemetry(payload: unknown, targetId: string): TelemetryRow | null {
  if (!payload || typeof payload !== 'object') return null;
  const p = payload as TelemetryStreamPayload;
  if (p.type !== 'telemetry') return null;
  if (!p.telemetry_id || String(p.target_id ?? '') !== targetId) return null;
  const eventType = String(p.event_type ?? 'wallet_transfer_detected');
  return {
    id: String(p.telemetry_id),
    workspace_id: p.workspace_id ?? null,
    target_id: p.target_id ?? null,
    provider_type: p.detected_by ?? null,
    source_type: eventType,
    detected_by: p.detected_by ?? null,
    evidence_source: p.evidence_source ?? null,
    chain_id: p.chain_id ?? null,
    block_number: typeof p.block_number === 'number' ? p.block_number : null,
    observed_at: p.observed_at ?? null,
    ingested_at: p.ingested_at ?? null,
    payload_json: {
      tx_hash: p.tx_hash ?? null,
      from: p.from ?? null,
      to: p.to ?? null,
      amount: p.amount ?? null,
      chain_id: p.chain_id ?? null,
      block_number: p.block_number ?? null,
      detected_by: p.detected_by ?? null,
      event_type: eventType,
      source_type: eventType,
      observed_at: p.observed_at ?? null,
    },
  };
}

function liveRowMatchesQuery(row: TelemetryRow, q: string): boolean {
  const trimmed = q.trim().toLowerCase();
  if (!trimmed) return true;
  const payload = (row.payload_json ?? {}) as Record<string, unknown>;
  const haystacks = [
    String(payload.tx_hash ?? ''),
    String(payload.from ?? ''),
    String(payload.to ?? ''),
    String(row.block_number ?? ''),
    String(row.source_type ?? ''),
    String(row.id ?? ''),
  ].map((s) => s.toLowerCase());
  return haystacks.some((h) => h.includes(trimmed));
}

function liveRowMatchesQuickFilter(row: TelemetryRow, quickFilter: QuickFilter): boolean {
  const eventType = String(row.source_type ?? '').toLowerCase();
  switch (quickFilter) {
    case 'wallet_transfers':
      return WALLET_TRANSFER_EVENT_TYPES.has(eventType);
    case 'rpc_polling':
      return eventType === 'rpc_polling';
    case 'live_evidence_only':
      return row.evidence_source === 'live';
    case 'alerts_only':
      // Alert linkage is a server-side join we cannot evaluate client-side; the
      // recovery refetch surfaces the row once its alert exists.
      return false;
    case 'all':
    default:
      return true;
  }
}

function liveRowMatchesView(row: TelemetryRow, q: string, quickFilter: QuickFilter): boolean {
  return liveRowMatchesQuickFilter(row, quickFilter) && liveRowMatchesQuery(row, q);
}

export default function TargetTelemetryPage() {
  const params = useParams();
  const targetId = typeof params?.targetId === 'string' ? params.targetId : '';

  const [rows, setRows] = useState<TelemetryRow[]>([]);
  const [workspaceId, setWorkspaceId] = useState('');
  const [monitoredAddress, setMonitoredAddress] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [selectedRow, setSelectedRow] = useState<TelemetryRow | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [debouncedQuery, setDebouncedQuery] = useState('');
  const [quickFilter, setQuickFilter] = useState<QuickFilter>('all');
  const [copiedTxId, setCopiedTxId] = useState<string | null>(null);
  const [currentPage, setCurrentPage] = useState(0);
  const [hasNext, setHasNext] = useState(false);
  const [hasPrev, setHasPrev] = useState(false);
  const [totalCount, setTotalCount] = useState(0);
  // Separated detection-path facts for the worker-status strip. The three workers
  // are distinct: QuickNode Stream (webhook push), Stable RPC Polling (backup), and
  // the legacy WebSocket realtime ingestor.
  const [realtimeEnabled, setRealtimeEnabled] = useState<boolean | null>(null);
  const [realtimeState, setRealtimeState] = useState<string | null>(null);
  const [realtimeFallbackActive, setRealtimeFallbackActive] = useState<boolean>(false);
  const [lastStablePollAt, setLastStablePollAt] = useState<string | null>(null);
  const [stablePollingActive, setStablePollingActive] = useState<boolean>(false);
  const [lastRealtimeEventAt, setLastRealtimeEventAt] = useState<string | null>(null);
  // QuickNode Stream worker facts (separate from the legacy WebSocket worker).
  const [quicknodeStreamState, setQuicknodeStreamState] = useState<string | null>(null);
  const [lastStreamEventAt, setLastStreamEventAt] = useState<string | null>(null);
  const [lastStreamBlock, setLastStreamBlock] = useState<number | null>(null);
  // QuickNode LIVE chain-tip lane facts (distinct from the webhook stream checkpoint):
  // live / catching_up / degraded / stale / failed + lag in blocks from chain head.
  const [quicknodeLiveLaneState, setQuicknodeLiveLaneState] = useState<string | null>(null);
  const [quicknodeLiveLagBlocks, setQuicknodeLiveLagBlocks] = useState<number | null>(null);
  // Real-time telemetry SSE connection status + the live rows it has pushed for this
  // target since mount (newest-first, deduped). Merged onto page 0 so a new transfer
  // appears without a manual refresh, search, or navigation.
  const [streamStatus, setStreamStatus] = useState<TelemetryStreamStatus | 'connecting'>('connecting');
  const [liveRows, setLiveRows] = useState<TelemetryRow[]>([]);

  const { authHeaders } = usePilotAuth();

  // Debounce the search so backend is called 400ms after the user stops typing
  useEffect(() => {
    const timer = setTimeout(() => setDebouncedQuery(searchQuery), 400);
    return () => clearTimeout(timer);
  }, [searchQuery]);

  // Reset to first page when search or filter changes
  useEffect(() => {
    setCurrentPage(0);
  }, [debouncedQuery, quickFilter]);

  // One fetch of the telemetry list. `silent` skips the loading flash so the
  // reconnect recovery refetch does not blank the table. Always a no-store fetch so
  // a newly persisted event is never served stale.
  const fetchTelemetry = useCallback(
    async (options?: { signal?: AbortSignal; silent?: boolean }) => {
      if (!targetId) return;
      if (!options?.silent) setLoading(true);
      setLoadError('');
      try {
        const res = await fetch(buildTelemetryUrl(targetId, debouncedQuery, quickFilter, currentPage), {
          headers: authHeaders(),
          cache: 'no-store',
          signal: options?.signal,
        });
        const payload = await res.json().catch(() => ({}));
        if (!res.ok) {
          const detail = typeof payload?.detail === 'string' ? payload.detail : `HTTP ${res.status}`;
          setLoadError(`Unable to load telemetry: ${detail}`);
          return;
        }
        setRows((payload.telemetry as TelemetryRow[]) ?? []);
        setHasNext(payload.has_next === true);
        setHasPrev(payload.has_prev === true);
        setTotalCount(typeof payload.total_count === 'number' ? payload.total_count : 0);
        setRealtimeEnabled(typeof payload.realtime_enabled === 'boolean' ? payload.realtime_enabled : null);
        setRealtimeState(typeof payload.realtime_state === 'string' ? payload.realtime_state : null);
        setRealtimeFallbackActive(payload.realtime_fallback_active === true);
        setLastStablePollAt(typeof payload.last_stable_poll_at === 'string' ? payload.last_stable_poll_at : null);
        setStablePollingActive(payload.stable_polling_active === true);
        setLastRealtimeEventAt(typeof payload.last_realtime_event_at === 'string' ? payload.last_realtime_event_at : null);
        setQuicknodeStreamState(typeof payload.quicknode_stream_state === 'string' ? payload.quicknode_stream_state : null);
        setLastStreamEventAt(typeof payload.last_stream_event_at === 'string' ? payload.last_stream_event_at : null);
        setLastStreamBlock(typeof payload.last_stream_block === 'number' ? payload.last_stream_block : null);
        setQuicknodeLiveLaneState(typeof payload.quicknode_live_lane_state === 'string' ? payload.quicknode_live_lane_state : null);
        setQuicknodeLiveLagBlocks(typeof payload.quicknode_live_lag_blocks === 'number' ? payload.quicknode_live_lag_blocks : null);
        if (typeof payload.workspace_id === 'string') {
          setWorkspaceId(payload.workspace_id);
        }
        setMonitoredAddress(
          typeof payload.monitored_address === 'string' ? payload.monitored_address : null,
        );
      } catch (err: unknown) {
        if ((err as { name?: string }).name === 'AbortError') return;
        setLoadError(`Network error: ${err instanceof Error ? err.message : 'unknown error'}`);
      } finally {
        if (!options?.silent) setLoading(false);
      }
    },
    [targetId, debouncedQuery, quickFilter, currentPage, authHeaders],
  );

  // Keep the latest fetch in a ref so the SSE subscription can trigger a recovery
  // refetch on reconnect WITHOUT re-subscribing on every search keystroke.
  const fetchTelemetryRef = useRef(fetchTelemetry);
  useEffect(() => {
    fetchTelemetryRef.current = fetchTelemetry;
  }, [fetchTelemetry]);

  useEffect(() => {
    const controller = new AbortController();
    void fetchTelemetry({ signal: controller.signal });
    return () => controller.abort();
  }, [fetchTelemetry]);

  // A new target resets the accumulated live rows so events never leak across targets.
  useEffect(() => {
    setLiveRows([]);
  }, [targetId]);

  // Subscribe to the real-time telemetry SSE stream. On each event for THIS target,
  // prepend it to the live buffer (deduped). On reconnect, run one silent recovery
  // refetch to catch anything missed while disconnected. Keyed only on targetId so a
  // search/filter/page change never tears down the stream.
  useEffect(() => {
    if (!targetId) return;
    let sawDisconnect = false;
    const disconnect = connectTelemetryStream(authHeaders(), {
      onConnected: () => {
        if (sawDisconnect) {
          sawDisconnect = false;
          void fetchTelemetryRef.current({ silent: true });
        }
      },
      onEvent: ({ payload }) => {
        const row = normalizeLiveTelemetry(payload, targetId);
        if (!row) return;
        setLiveRows((prev) => {
          const key = liveRowKey(row);
          if (prev.some((r) => r.id === row.id || liveRowKey(r) === key)) return prev;
          return [row, ...prev].slice(0, 200);
        });
      },
      onHeartbeat: () => {},
      onStatusChange: (status) => {
        setStreamStatus(status);
        if (status === 'reconnecting' || status === 'disconnected') sawDisconnect = true;
      },
    });
    return () => {
      disconnect();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetId, authHeaders]);

  // Periodic HTTP refresh fallback — active ONLY while the SSE stream is NOT live
  // (connecting / reconnecting / disconnected). This is what makes the
  // "HTTP refresh fallback active" label truthful and what surfaces a row that was
  // persisted while the browser's SSE was down (the production symptom: Stable RPC
  // persisted the transfer but the disconnected page only showed it "later"). Once
  // the stream is live the interval is cleared, so a healthy connection does not
  // aggressively poll — SSE push carries new rows and the merge dedupes them.
  useEffect(() => {
    if (!targetId) return;
    if (streamStatus === 'live') return;
    const timer = setInterval(() => {
      void fetchTelemetryRef.current({ silent: true });
    }, HTTP_FALLBACK_POLL_MS);
    return () => clearInterval(timer);
  }, [targetId, streamStatus]);

  // Merge live SSE rows onto the fetched page. Live events are newest, so they only
  // inject on page 0; they must also match the active search/filter (requirement 4)
  // and are deduped against the fetched rows by id / deterministic tx key so the
  // same row never appears twice once the next HTTP refetch includes it.
  const mergedRows = useMemo<TelemetryRow[]>(() => {
    if (currentPage !== 0) return rows;
    const matching = liveRows.filter((r) => liveRowMatchesView(r, debouncedQuery, quickFilter));
    if (matching.length === 0) return rows;
    const seen = new Set<string>();
    const out: TelemetryRow[] = [];
    for (const r of [...matching, ...rows]) {
      const idKey = `id:${r.id}`;
      const txKey = liveRowKey(r);
      if (seen.has(idKey) || seen.has(txKey)) continue;
      seen.add(idKey);
      seen.add(txKey);
      out.push(r);
    }
    return out;
  }, [rows, liveRows, currentPage, debouncedQuery, quickFilter]);

  // Number of live rows injected beyond the fetched page, so the row count stays
  // truthful when a real-time event arrives before the next HTTP refresh.
  const injectedLiveCount = currentPage === 0 ? Math.max(0, mergedRows.length - rows.length) : 0;
  const displayTotalCount = totalCount + injectedLiveCount;

  // When a server-side filter handles the event_type, no client-side filter needed.
  // For live_evidence_only we still filter client-side since it's not an event_type.
  const filteredRows =
    quickFilter === 'live_evidence_only'
      ? mergedRows.filter((row) => row.evidence_source === 'live')
      : mergedRows;

  const telemetryStreamConnected = streamStatus === 'live';

  // QuickNode Stream status label. The live chain-tip lane is authoritative when it
  // has reported (live / catching_up / degraded / stale / failed); otherwise we fall
  // back to the webhook delivery state. Historical backfill ("catching up") is never
  // shown as a green "live" — only chain-tip proximity is.
  const quicknodeStatus: { label: string; tone: 'success' | 'info' | 'warning' | 'danger' | 'muted' } = (() => {
    switch (quicknodeLiveLaneState) {
      case 'live':
        return { label: 'Live at chain tip', tone: 'success' };
      case 'catching_up':
        return { label: 'Catching up (historical backfill)', tone: 'info' };
      case 'degraded':
        return {
          label:
            quicknodeLiveLagBlocks != null
              ? `Degraded — ${quicknodeLiveLagBlocks} blocks behind (stable polling fallback)`
              : 'Degraded — stable polling fallback',
          tone: 'warning',
        };
      case 'stale':
        return { label: 'Stale — live lane not advancing', tone: 'warning' };
      case 'failed':
        return { label: 'Failed — provider error (stable polling fallback)', tone: 'danger' };
      default:
        break;
    }
    // No live-lane report yet — fall back to the webhook stream delivery state.
    if (quicknodeStreamState === 'active') return { label: 'Active', tone: 'success' };
    if (quicknodeStreamState === 'receiving')
      return { label: 'Receiving blocks — no recent matched transfer', tone: 'info' };
    if (quicknodeStreamState === 'stale') return { label: 'Stale — stream not delivering', tone: 'warning' };
    return { label: 'No stream activity', tone: 'muted' };
  })();
  // ── Detection-path pairing (presentation only) ─────────────────────────────
  // Restates the two facts above side by side so a stale stream is read WITH its
  // fallback: "Stream: Stale · Polling fallback: Active". Nothing new is derived —
  // the stream half is the quicknodeStatus label (split at its em dash into
  // state + reason) and the polling half is the same three-way label the Stable
  // RPC Polling worker card shows. The stream's tone is never upgraded; when the
  // stream is not current and neither polling nor the legacy WebSocket is active,
  // the pairing is shown as a failure rather than a warning (fail-closed).
  const [streamShort, ...streamReasonParts] = quicknodeStatus.label.split(' — ');
  const streamReason = streamReasonParts.join(' — ');
  const stablePollingLabel = stablePollingActive
    ? 'Active fallback'
    : lastStablePollAt
      ? 'Idle — awaiting next poll'
      : 'No poll recorded';
  const pollingShort = stablePollingActive ? 'Active' : stablePollingLabel.split(' — ')[0];
  const pollingTone = stablePollingActive ? 'info' : 'warning';
  const streamNotCurrent = quicknodeStatus.tone === 'warning' || quicknodeStatus.tone === 'danger';
  const noDetectionPathActive = !stablePollingActive && realtimeState !== 'active';
  const pairingTone = streamNotCurrent && noDetectionPathActive ? 'danger' : quicknodeStatus.tone;
  const streamPageTone = telemetryStreamConnected
    ? 'success'
    : streamStatus === 'reconnecting' || streamStatus === 'connecting'
      ? 'warning'
      : 'muted';

  return (
    <main className="productPage msrcPage msrcTelemetryPage">
      {selectedRow && (
        <TelemetryDetailModal
          row={selectedRow}
          onClose={() => setSelectedRow(null)}
          monitoredAddress={monitoredAddress}
        />
      )}

      <div className="msrcBackRow">
        <Link
          href="/monitoring-sources"
          prefetch={false}
          className="msrcBack"
        >
          ← Monitoring Sources
        </Link>
      </div>

      <header className="msrcHeader">
        <div className="msrcHeaderText">
          <h1 className="productPageTitle msrcTitle">Target Telemetry</h1>
          <p className="msrcLede">
            Live telemetry events persisted for this monitoring target.
          </p>
          {monitoredAddress && (
            <p className="msrcAddress">
              Monitored address:{' '}
              <span
                className="msrcAddressValue"
                title="Full monitored wallet address — confirm this matches your wallet exactly"
              >
                {monitoredAddress}
              </span>
            </p>
          )}
        </div>

        {/* Real-time telemetry push (SSE) — this PAGE's live-update channel. When
            connected, new transfers stream into the table with no refresh. Truthful
            degraded state on disconnect, with HTTP polling as fallback. */}
        <div
          data-testid="telemetry-stream-status"
          className={`msrcStreamChip msrcTone--${streamPageTone}`}
          title="This page's live-update channel to Decoda — separate from the ingestion paths below."
        >
          <span className="msrcStreamDot" aria-hidden />
          <span className="msrcStreamText">
            <span className="msrcStreamLabel">
              Real-time telemetry
            </span>
            <span className="msrcStreamValue">
              {telemetryStreamConnected
                ? 'Live — streaming new events'
                : streamStatus === 'reconnecting'
                  ? 'Reconnecting… (HTTP refresh fallback active)'
                  : streamStatus === 'connecting'
                    ? 'Connecting…'
                    : 'Offline — HTTP refresh fallback active'}
            </span>
          </span>
        </div>
      </header>

      <div className="msrcIdStrip">
        <span className="msrcIdItem">
          <span className="msrcIdLabel">Target ID: </span>
          <code className="msrcMono">{targetId || '-'}</code>
        </span>
        {workspaceId ? (
          <span className="msrcIdItem">
            <span className="msrcIdLabel">Workspace ID: </span>
            <code className="msrcMono">{workspaceId}</code>
          </span>
        ) : null}
      </div>

      {/* Worker / detection-path status. Three DISTINCT workers, each in its own
          card so the legacy WebSocket status never stands in as "the" realtime
          status: QuickNode Stream (webhook push — the primary realtime path), Stable
          RPC Polling (always-on backup), and the legacy WebSocket ingestor. When the
          QuickNode Stream is active, a paused/degraded WebSocket is expected legacy
          state and is never surfaced as the main realtime status. */}
      {realtimeEnabled !== null ? (
        <section data-testid="telemetry-worker-status" className="msrcPaths">
          <div className="msrcPathsHead">
            <p className="sectionEyebrow msrcPathsEyebrow">Detection paths</p>
            <span className="msrcPathsHint">Each path reports on its own; a stale stream never borrows the polling status.</span>
          </div>

          {/* Stream ↔ fallback pairing, with the reason. */}
          <div data-testid="telemetry-coverage-pairing" className={`msrcPairing msrcTone--${pairingTone}`}>
            <div className="msrcPairingChips">
              <span className={`msrcPairChip msrcTone--${quicknodeStatus.tone}`}>
                <span className="msrcPairDot" aria-hidden />
                <span className="msrcPairKey">Stream:</span>
                <span className="msrcPairVal">{streamShort}</span>
              </span>
              <span className="msrcPairSep" aria-hidden>·</span>
              <span className={`msrcPairChip msrcTone--${pollingTone}`}>
                <span className="msrcPairDot" aria-hidden />
                <span className="msrcPairKey">Polling fallback:</span>
                <span className="msrcPairVal">{pollingShort}</span>
              </span>
            </div>
            {streamNotCurrent ? (
              <p className="msrcPairingReason">
                {stablePollingActive ? (
                  <>
                    Realtime stream delivery is not current{streamReason ? ` — ${streamReason}` : ''}.
                    {' '}Stable RPC Polling remains active as the fallback detection path (last poll {fmt(lastStablePollAt)});
                    the Detected By column shows which path recorded each row.
                  </>
                ) : (
                  <>
                    Realtime stream delivery is not current{streamReason ? ` — ${streamReason}` : ''}.
                    {' '}Stable RPC Polling is not active ({stablePollingLabel.toLowerCase()}).
                  </>
                )}
              </p>
            ) : null}
          </div>

          <div className="msrcWorkers">
            {/* Worker 1 — QuickNode Stream (primary realtime path) */}
            <div
              data-testid="worker-quicknode-stream"
              className={`msrcWorker msrcTone--${quicknodeStatus.tone}`}
            >
              <div className="msrcWorkerHead">
                <span className="msrcWorkerName">
                  QuickNode Stream
                </span>
                <span className="msrcWorkerRole">Primary realtime path</span>
              </div>
              <span className="msrcWorkerStatus">
                <span className="msrcWorkerDot" aria-hidden />
                {quicknodeStatus.label}
              </span>
              <dl className="msrcWorkerFacts">
                <div>
                  <dt>Last stream block</dt>
                  <dd className="msrcMono">
                    {lastStreamBlock != null ? String(lastStreamBlock) : '-'}
                  </dd>
                </div>
                <div>
                  <dt>Last stream event</dt>
                  <dd>{fmt(lastStreamEventAt)}</dd>
                </div>
              </dl>
            </div>

            {/* Worker 2 — Stable RPC Polling (always-on backup) */}
            <div
              data-testid="worker-stable-rpc-polling"
              className={`msrcWorker msrcTone--${pollingTone}`}
            >
              <div className="msrcWorkerHead">
                <span className="msrcWorkerName">
                  Stable RPC Polling
                </span>
                <span className="msrcWorkerRole">Always-on backup</span>
              </div>
              <span className="msrcWorkerStatus">
                <span className="msrcWorkerDot" aria-hidden />
                {stablePollingActive
                  ? 'Active fallback'
                  : lastStablePollAt
                    ? 'Idle — awaiting next poll'
                    : 'No poll recorded'}
              </span>
              <dl className="msrcWorkerFacts">
                <div>
                  <dt>Last stable poll</dt>
                  <dd>{fmt(lastStablePollAt)}</dd>
                </div>
              </dl>
            </div>

            {/* Worker 3 — Legacy WebSocket realtime ingestor */}
            <div
              data-testid="worker-legacy-websocket"
              className={`msrcWorker msrcWorker--legacy msrcTone--${
                realtimeState === 'active'
                  ? 'success'
                  : realtimeState === 'degraded'
                    ? 'warning'
                    : realtimeEnabled
                      ? 'success'
                      : 'muted'
              }`}
            >
              <div className="msrcWorkerHead">
                <span className="msrcWorkerName">
                  Legacy WebSocket
                </span>
                <span className="msrcWorkerRole">Legacy realtime ingestor</span>
              </div>
              <span className="msrcWorkerStatus">
                <span className="msrcWorkerDot" aria-hidden />
                {realtimeState === 'active'
                  ? 'Active'
                  : realtimeState === 'degraded'
                    ? realtimeFallbackActive
                      ? 'Realtime degraded — stable polling fallback active'
                      : 'Degraded'
                    : realtimeEnabled
                      ? 'Enabled'
                      : 'Paused / Disabled'}
              </span>
              <dl className="msrcWorkerFacts">
                <div>
                  <dt>Last realtime event</dt>
                  <dd>{fmt(lastRealtimeEventAt)}</dd>
                </div>
              </dl>
            </div>
          </div>

          {/* Contextual note only when the legacy WebSocket is actually the active
              realtime path — i.e. the QuickNode Stream is NOT active. When the stream
              is active, a paused/degraded WebSocket is expected legacy state and must
              never be surfaced as the main realtime status (nor claim stable polling
              is the detector when the stream is delivering). */}
          {/* The legacy-WebSocket note is suppressed whenever the real-time telemetry
              SSE is connected (requirement 7): while events are streaming to the page,
              a paused/degraded legacy WebSocket is expected and must never be surfaced
              as "realtime is down". It also never claims "stable polling active" while
              the Stable RPC Polling worker itself reports idle / no poll. */}
          {telemetryStreamConnected ? null : quicknodeStreamState !== 'active' && realtimeState === 'degraded' ? (
            <p className="msrcPathsNote">
              Realtime WebSocket is degraded (provider failure). Stable RPC polling remains
              active and continues detecting wallet transfers; realtime resumes automatically
              once the provider recovers.
            </p>
          ) : quicknodeStreamState !== 'active' && !realtimeEnabled && stablePollingActive ? (
            <p className="msrcPathsNote">
              Realtime paused; stable polling active. Wallet transfers are detected by Stable RPC Polling.
            </p>
          ) : null}
        </section>
      ) : null}

      {/* Search bar */}
      <div className="msrcToolbar msrcTelemetryToolbar">
        <label className="msrcSearch msrcSearch--wide">
          <input
            type="search"
            aria-label="Search telemetry"
            placeholder="Search by tx hash, wallet address, block number, event type, or ID…"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="msrcSearchInput"
          />
        </label>
      </div>

      {/* Quick filters */}
      <div className="msrcChips">
        {QUICK_FILTERS.map((f) => (
          <button
            key={f.id}
            type="button"
            onClick={() => setQuickFilter(f.id)}
            aria-pressed={quickFilter === f.id}
            className={quickFilter === f.id ? 'msrcChip msrcChip--active' : 'msrcChip'}
          >
            {f.label}
          </button>
        ))}
      </div>

      {/* Pagination info */}
      {!loading && !loadError && filteredRows.length > 0 && (
        <div className="msrcPager msrcPager--top">
          <span className="msrcPagerInfo">
            Page {currentPage + 1} &middot; {filteredRows.length} of {displayTotalCount} row{displayTotalCount !== 1 ? 's' : ''}
          </span>
          <div className="msrcPagerBtns">
            <button
              type="button"
              disabled={!hasPrev}
              onClick={() => setCurrentPage((p) => Math.max(0, p - 1))}
              className="btn btn-secondary msrcRowBtn"
            >
              Prev
            </button>
            <button
              type="button"
              disabled={!hasNext}
              onClick={() => setCurrentPage((p) => p + 1)}
              className="btn btn-secondary msrcRowBtn"
            >
              Next
            </button>
          </div>
        </div>
      )}

      {loadError ? (
        <p className="statusLine msrcNotice msrcNotice--danger">
          {loadError}
        </p>
      ) : null}

      {!loading && !loadError && filteredRows.length === 0 && (debouncedQuery.trim() !== '' || quickFilter !== 'all') ? (
        // Active search/filter with no matches — must NOT claim the target has no
        // telemetry (requirement 10). A live event that arrives while a search is
        // active is added only if it matches; clearing the search re-includes the
        // already-cached live rows.
        <div className="msrcEmpty">
          <p className="msrcEmptyTitle">No matching telemetry</p>
          <p className="msrcEmptyBody">
            No telemetry matches this search. Clear the search or filter to see all events for this target.
          </p>
        </div>
      ) : !loading && !loadError && filteredRows.length === 0 ? (
        <div className="msrcEmpty">
          <p className="msrcEmptyTitle">No telemetry data</p>
          <p className="msrcEmptyBody">
            No live telemetry has been persisted for this target yet. New transfers stream in
            automatically once detected — no refresh needed.
          </p>
        </div>
      ) : (
        <TableShell headers={HEADERS} compact className="msrcTable msrcTelemetryTable">
          {loading ? (
            <tr>
              <td
                colSpan={HEADERS.length}
                className="msrcTableNote"
              >
                Loading telemetry...
              </td>
            </tr>
          ) : (
            filteredRows.map((row) => {
              const payload = row.payload_json;
              const kind = classifyEvent(payload, row.source_type);
              const txHash = extractField(payload, 'tx_hash', 'transactionHash', 'hash');
              const fromAddr = extractField(payload, 'from', 'from_address', 'fromAddress');
              const toAddr = extractField(payload, 'to', 'to_address', 'toAddress');
              const amount = extractField(payload, 'amount', 'value', 'amount_wei');
              const isBaseScan = row.chain_id === BASE_CHAIN_ID;
              // Wallet transfers must never render a blank Detected By — resolve
              // with payload/details/metadata fallbacks, then fail closed to an
              // explicit "Unknown" (or the simulator/replay evidence source).
              const detectedByRaw =
                kind === 'wallet_transfer' ? walletTransferDetectedBy(row) : deriveDetectedBy(row);
              const detectedByIsRealtime = detectedByRaw ? REALTIME_DETECTED_BY.has(detectedByRaw) : false;
              const detectedByIsStable = detectedByRaw === 'stable_rpc_polling';
              return (
                <tr key={row.id}>
                  {/* Event Type */}
                  <td className="msrcNowrap">
                    {kind === 'wallet_transfer' ? (
                      <span className="msrcKind msrcKind--transfer">
                        Wallet transfer detected
                      </span>
                    ) : kind === 'block_poll' ? (
                      <span className="msrcKind msrcKind--poll">
                        RPC polling heartbeat
                      </span>
                    ) : (
                      <span className="msrcCellMuted">
                        {row.source_type ?? '-'}
                      </span>
                    )}
                  </td>

                  {/* Detected By — never blank for wallet transfer rows */}
                  <td className="msrcNowrap">
                    {kind === 'wallet_transfer' && detectedByRaw ? (
                      <span
                        className={`msrcVia ${
                          detectedByIsStable
                            ? 'msrcVia--stable'
                            : detectedByIsRealtime
                              ? 'msrcVia--realtime'
                              : 'msrcVia--other'
                        }`}
                      >
                        {formatDetectedBy(detectedByRaw)}
                      </span>
                    ) : detectedByRaw ? (
                      <span className="msrcCellMuted">
                        {formatDetectedBy(detectedByRaw)}
                      </span>
                    ) : (
                      <span className="msrcCellMuted">-</span>
                    )}
                  </td>

                  {/* Tx Hash */}
                  <td>
                    {txHash ? (
                      <span className="msrcTxCell">
                        {isBaseScan ? (
                          <a
                            href={`${BASESCAN_TX_BASE}${txHash}`}
                            target="_blank"
                            rel="noopener noreferrer"
                            title={txHash}
                            className="msrcTxLink msrcMono"
                          >
                            {shortenHash(txHash)} ↗
                          </a>
                        ) : (
                          <code className="msrcMono" title={txHash}>
                            {shortenHash(txHash)}
                          </code>
                        )}
                        <button
                          type="button"
                          onClick={() => {
                            navigator.clipboard
                              .writeText(txHash)
                              .then(() => {
                                setCopiedTxId(row.id);
                                setTimeout(() => setCopiedTxId(null), 2000);
                              })
                              .catch(() => {});
                          }}
                          title="Copy transaction hash"
                          aria-label="Copy transaction hash"
                          className={copiedTxId === row.id ? 'msrcCopyBtn msrcCopyBtn--done' : 'msrcCopyBtn'}
                        >
                          {copiedTxId === row.id ? '✓' : '⧉'}
                        </button>
                      </span>
                    ) : (
                      <span className="muted">-</span>
                    )}
                  </td>

                  {/* From */}
                  <td>
                    {fromAddr ? (
                      <code className="msrcMono" title={fromAddr}>
                        {shortenAddress(fromAddr)}
                      </code>
                    ) : (
                      <span className="muted">-</span>
                    )}
                  </td>

                  {/* To */}
                  <td>
                    {toAddr ? (
                      <code className="msrcMono" title={toAddr}>
                        {shortenAddress(toAddr)}
                      </code>
                    ) : (
                      <span className="muted">-</span>
                    )}
                  </td>

                  {/* Amount */}
                  <td>
                    {amount ? (
                      <code className="msrcMono">{amount}</code>
                    ) : (
                      <span className="muted">-</span>
                    )}
                  </td>

                  {/* Chain ID */}
                  <td className="msrcNum">{row.chain_id ?? '-'}</td>

                  {/* Block Number */}
                  <td className="msrcNum">{row.block_number != null ? String(row.block_number) : '-'}</td>

                  {/* Observed At */}
                  <td className="msrcNum">{fmt(row.observed_at)}</td>

                  {/* Evidence Source — anything other than live stays visibly tagged. */}
                  <td>
                    {row.evidence_source ? (
                      <span className={row.evidence_source === 'live' ? 'msrcEvidence' : 'msrcEvidence msrcEvidence--nonlive'}>
                        {row.evidence_source}
                      </span>
                    ) : (
                      '-'
                    )}
                  </td>

                  {/* Details */}
                  <td>
                    {row.payload_json != null ? (
                      <button
                        type="button"
                        onClick={() => setSelectedRow(row)}
                        className="msrcLinkBtn"
                      >
                        View
                      </button>
                    ) : (
                      <span className="muted">-</span>
                    )}
                  </td>
                </tr>
              );
            })
          )}
        </TableShell>
      )}
    </main>
  );
}
