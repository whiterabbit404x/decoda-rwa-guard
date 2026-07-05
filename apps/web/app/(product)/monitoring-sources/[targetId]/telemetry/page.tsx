'use client';

import { Fragment, useCallback, useEffect, useState } from 'react';
import Link from 'next/link';
import { useParams } from 'next/navigation';

import { TableShell } from '../../../../components/ui-primitives';
import { usePilotAuth } from '../../../../pilot-auth-context';
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
      style={{
        position: 'fixed',
        inset: 0,
        zIndex: 1000,
        display: 'flex',
        alignItems: 'flex-start',
        justifyContent: 'center',
        background: 'rgba(0, 0, 0, 0.72)',
        padding: '2rem 1rem',
        overflowY: 'auto',
      }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        style={{
          background: 'var(--bg-surface)',
          border: '1px solid var(--border-accent)',
          borderRadius: 'var(--radius-lg)',
          width: '100%',
          maxWidth: '720px',
          padding: '1.5rem',
          marginBottom: '2rem',
        }}
      >
        {/* Modal header */}
        <div
          style={{
            display: 'flex',
            alignItems: 'flex-start',
            justifyContent: 'space-between',
            marginBottom: '1rem',
          }}
        >
          <div>
            <h2 style={{ margin: 0, fontSize: '1.1rem', fontWeight: 700 }}>
              Telemetry Event Details
            </h2>
            <p
              className="muted"
              style={{ margin: '0.2rem 0 0', fontSize: '0.78rem', fontFamily: 'monospace' }}
            >
              {row.id}
            </p>
          </div>
          <button
            type="button"
            aria-label="Close"
            onClick={onClose}
            style={{
              background: 'none',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-xs)',
              color: 'var(--text-secondary)',
              cursor: 'pointer',
              fontSize: '0.9rem',
              lineHeight: 1,
              padding: '0.3rem 0.65rem',
            }}
          >
            ✕
          </button>
        </div>

        {/* Event classification banner */}
        {kind === 'wallet_transfer' && (
          <div
            style={{
              background: 'var(--success-bg)',
              border: '1px solid var(--success-bdr)',
              borderRadius: 'var(--radius-sm)',
              color: 'var(--success-fg)',
              display: 'inline-flex',
              fontSize: '0.82rem',
              fontWeight: 600,
              marginBottom: '1rem',
              padding: '0.35rem 0.85rem',
            }}
          >
            Wallet transfer detected
          </div>
        )}
        {kind === 'block_poll' && (
          <div
            style={{
              background: 'var(--info-bg)',
              border: '1px solid var(--info-bdr)',
              borderRadius: 'var(--radius-sm)',
              color: 'var(--info-fg)',
              display: 'inline-flex',
              fontSize: '0.82rem',
              fontWeight: 600,
              marginBottom: '1rem',
              padding: '0.35rem 0.85rem',
            }}
          >
            RPC polling heartbeat — no wallet transfer detected
          </div>
        )}

        {/* Human-readable summary grid */}
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: 'max-content 1fr',
            columnGap: '1.25rem',
            rowGap: '0.45rem',
            marginBottom: '1.25rem',
            fontSize: '0.875rem',
          }}
        >
          {summaryFields
            .filter(([, v]) => v != null && v !== '')
            .map(([label, value]) => (
              <Fragment key={label}>
                <span className="muted" style={{ whiteSpace: 'nowrap', alignSelf: 'center' }}>
                  {label}:
                </span>
                <code
                  style={{
                    fontFamily: 'monospace',
                    fontSize: '0.82rem',
                    wordBreak: 'break-all',
                    color: 'var(--text-primary)',
                  }}
                >
                  {value}
                </code>
              </Fragment>
            ))}

          {/* Transaction hash with optional Basescan link */}
          {txHash && (
            <Fragment key="tx_hash">
              <span className="muted" style={{ whiteSpace: 'nowrap', alignSelf: 'center' }}>
                Transaction hash:
              </span>
              <span
                style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}
              >
                <code
                  style={{
                    fontFamily: 'monospace',
                    fontSize: '0.82rem',
                    wordBreak: 'break-all',
                    color: 'var(--text-primary)',
                  }}
                >
                  {txHash}
                </code>
                {isBaseScan && (
                  <a
                    href={`${BASESCAN_TX_BASE}${txHash}`}
                    target="_blank"
                    rel="noopener noreferrer"
                    style={{
                      color: 'var(--text-accent)',
                      fontSize: '0.78rem',
                      whiteSpace: 'nowrap',
                    }}
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
          <div
            data-testid="tx-detection-diagnosis"
            style={{
              background: 'var(--bg-base)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-sm)',
              marginBottom: '1.25rem',
              padding: '0.85rem 1rem',
            }}
          >
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: '0.75rem',
                flexWrap: 'wrap',
              }}
            >
              <span
                style={{
                  fontSize: '0.75rem',
                  fontWeight: 600,
                  color: 'var(--text-secondary)',
                  textTransform: 'uppercase',
                  letterSpacing: '0.07em',
                }}
              >
                Detection Path Diagnosis
              </span>
              <button
                type="button"
                onClick={runDiagnosis}
                disabled={diagnosing}
                style={{
                  background: 'transparent',
                  border: '1px solid var(--border)',
                  borderRadius: 'var(--radius-xs)',
                  color: diagnosing ? 'var(--text-muted)' : 'var(--text-accent)',
                  cursor: diagnosing ? 'default' : 'pointer',
                  fontSize: '0.78rem',
                  padding: '0.25rem 0.7rem',
                }}
              >
                {diagnosing
                  ? 'Checking scan windows…'
                  : diagnosis
                    ? 'Re-run diagnosis'
                    : 'Diagnose this transaction'}
              </button>
            </div>
            {!diagnosis && !diagnosing && !diagnosisError ? (
              <p className="muted" style={{ margin: '0.5rem 0 0', fontSize: '0.78rem' }}>
                Fetches this tx by hash, compares its block number to the realtime
                worker&apos;s scanned live-tail windows, and reports whether it was realtime
                matched, imported, stable-polling detected, or duplicate-skipped.
              </p>
            ) : null}
            {diagnosisError ? (
              <p style={{ margin: '0.5rem 0 0', fontSize: '0.8rem', color: 'var(--danger-fg)' }}>
                {diagnosisError}
              </p>
            ) : null}
            {diagnosis ? (
              <div style={{ marginTop: '0.65rem' }}>
                <div
                  style={{
                    background: 'var(--bg-surface)',
                    border: '1px solid var(--border-accent)',
                    borderRadius: 'var(--radius-xs)',
                    fontSize: '0.82rem',
                    fontWeight: 600,
                    marginBottom: '0.6rem',
                    padding: '0.4rem 0.7rem',
                  }}
                >
                  {formatRealtimeVerdict(diagnosis.realtime_verdict)}
                </div>
                <div
                  style={{
                    display: 'grid',
                    gridTemplateColumns: 'max-content 1fr',
                    columnGap: '1.25rem',
                    rowGap: '0.35rem',
                    fontSize: '0.8rem',
                  }}
                >
                  <span className="muted">Tx block:</span>
                  <code style={{ fontFamily: 'monospace' }}>
                    {diagnosis.block_number != null ? diagnosis.block_number : 'not found'}
                  </code>
                  <span className="muted">Last live-tail window:</span>
                  <code style={{ fontFamily: 'monospace' }}>
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
                  <code style={{ fontFamily: 'monospace', wordBreak: 'break-all' }}>
                    {formatSpans(diagnosis.realtime_scanned_spans) ?? 'none recorded'}
                  </code>
                  <span className="muted">Block was scanned:</span>
                  <code style={{ fontFamily: 'monospace' }}>
                    {diagnosis.was_block_scanned === true
                      ? 'yes'
                      : diagnosis.was_block_scanned === false
                        ? 'no'
                        : 'unknown'}
                  </code>
                  <span className="muted">Persisted row detected by:</span>
                  <code style={{ fontFamily: 'monospace' }}>
                    {diagnosis.existing_detected_by
                      ? formatDetectedBy(diagnosis.existing_detected_by)
                      : 'no persisted row found'}
                  </code>
                  {diagnosis.realtime_duplicate_skipped ? (
                    <>
                      <span className="muted">Duplicate handling:</span>
                      <code style={{ fontFamily: 'monospace' }}>
                        realtime duplicate skipped — first detector kept
                      </code>
                    </>
                  ) : null}
                  {diagnosis.rate_limited_at_time === true ? (
                    <>
                      <span className="muted">Rate limited at tx time:</span>
                      <code style={{ fontFamily: 'monospace' }}>yes</code>
                    </>
                  ) : null}
                </div>
              </div>
            ) : null}
          </div>
        ) : null}

        {/* Raw Response toolbar */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            marginBottom: '0.5rem',
          }}
        >
          <span
            style={{
              fontSize: '0.75rem',
              fontWeight: 600,
              color: 'var(--text-secondary)',
              textTransform: 'uppercase',
              letterSpacing: '0.07em',
            }}
          >
            Raw Response
          </span>
          <div style={{ display: 'flex', gap: '0.4rem' }}>
            {txHash && (
              <button
                type="button"
                onClick={copyTxHash}
                style={{
                  background: copiedTx ? 'var(--success-bg)' : 'transparent',
                  border: '1px solid var(--border)',
                  borderRadius: 'var(--radius-xs)',
                  color: copiedTx ? 'var(--success-fg)' : 'var(--text-secondary)',
                  cursor: 'pointer',
                  fontSize: '0.75rem',
                  padding: '0.25rem 0.65rem',
                  transition: 'color 0.15s, background 0.15s',
                }}
              >
                {copiedTx ? 'Copied!' : 'Copy Tx Hash'}
              </button>
            )}
            <button
              type="button"
              onClick={copyJson}
              style={{
                background: copiedJson ? 'var(--success-bg)' : 'transparent',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius-xs)',
                color: copiedJson ? 'var(--success-fg)' : 'var(--text-secondary)',
                cursor: 'pointer',
                fontSize: '0.75rem',
                padding: '0.25rem 0.65rem',
                transition: 'color 0.15s, background 0.15s',
              }}
            >
              {copiedJson ? 'Copied!' : 'Copy JSON'}
            </button>
          </div>
        </div>

        {/* Dark-mode JSON viewer */}
        <pre
          style={{
            background: 'var(--bg-base)',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius-sm)',
            color: 'var(--text-primary)',
            fontFamily: '"Cascadia Code", "JetBrains Mono", "Fira Code", Consolas, monospace',
            fontSize: '0.78rem',
            lineHeight: 1.65,
            margin: 0,
            maxHeight: '360px',
            overflowX: 'auto',
            overflowY: 'auto',
            padding: '1rem',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-all',
          }}
        >
          {jsonString}
        </pre>
      </div>
    </div>
  );
}

const PAGE_SIZE = 50;

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
  // Separated detection-path facts for the worker-status strip.
  const [realtimeEnabled, setRealtimeEnabled] = useState<boolean | null>(null);
  const [realtimeState, setRealtimeState] = useState<string | null>(null);
  const [lastStablePollAt, setLastStablePollAt] = useState<string | null>(null);
  const [lastRealtimeEventAt, setLastRealtimeEventAt] = useState<string | null>(null);

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

  // When a server-side filter handles the event_type, no client-side filter needed.
  // For live_evidence_only we still filter client-side since it's not an event_type.
  const filteredRows =
    quickFilter === 'live_evidence_only'
      ? rows.filter((row) => row.evidence_source === 'live')
      : rows;

  useEffect(() => {
    if (!targetId) return;
    const controller = new AbortController();
    setLoading(true);
    setLoadError('');

    fetch(buildTelemetryUrl(targetId, debouncedQuery, quickFilter, currentPage), {
      headers: authHeaders(),
      cache: 'no-store',
      signal: controller.signal,
    })
      .then(async (res) => {
        const payload = await res.json().catch(() => ({}));
        if (!res.ok) {
          const detail =
            typeof payload?.detail === 'string' ? payload.detail : `HTTP ${res.status}`;
          setLoadError(`Unable to load telemetry: ${detail}`);
          return;
        }
        setRows((payload.telemetry as TelemetryRow[]) ?? []);
        setHasNext(payload.has_next === true);
        setHasPrev(payload.has_prev === true);
        setTotalCount(typeof payload.total_count === 'number' ? payload.total_count : 0);
        setRealtimeEnabled(typeof payload.realtime_enabled === 'boolean' ? payload.realtime_enabled : null);
        setRealtimeState(typeof payload.realtime_state === 'string' ? payload.realtime_state : null);
        setLastStablePollAt(typeof payload.last_stable_poll_at === 'string' ? payload.last_stable_poll_at : null);
        setLastRealtimeEventAt(typeof payload.last_realtime_event_at === 'string' ? payload.last_realtime_event_at : null);
        if (typeof payload.workspace_id === 'string') {
          setWorkspaceId(payload.workspace_id);
        }
        setMonitoredAddress(
          typeof payload.monitored_address === 'string' ? payload.monitored_address : null,
        );
      })
      .catch((err: unknown) => {
        if ((err as { name?: string }).name === 'AbortError') return;
        setLoadError(
          `Network error: ${err instanceof Error ? err.message : 'unknown error'}`,
        );
      })
      .finally(() => setLoading(false));

    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetId, debouncedQuery, quickFilter, currentPage, authHeaders]);

  return (
    <main className="productPage">
      {selectedRow && (
        <TelemetryDetailModal
          row={selectedRow}
          onClose={() => setSelectedRow(null)}
          monitoredAddress={monitoredAddress}
        />
      )}

      <div style={{ marginBottom: '1.25rem' }}>
        <Link
          href="/monitoring-sources"
          prefetch={false}
          style={{ fontSize: '0.85rem', color: 'var(--text-accent)', textDecoration: 'none' }}
        >
          ← Monitoring Sources
        </Link>
      </div>

      <div style={{ marginBottom: '1.25rem' }}>
        <h1 style={{ margin: 0, fontSize: '1.45rem', fontWeight: 700 }}>Target Telemetry</h1>
        <p className="muted" style={{ margin: '0.35rem 0 0', fontSize: '0.9rem' }}>
          Live telemetry events persisted for this monitoring target.
        </p>
        {monitoredAddress && (
          <p
            className="muted"
            style={{ margin: '0.35rem 0 0', fontSize: '0.8rem' }}
          >
            Monitored address:{' '}
            <span
              style={{ fontFamily: 'monospace', wordBreak: 'break-all', color: 'var(--text)' }}
              title="Full monitored wallet address — confirm this matches your wallet exactly"
            >
              {monitoredAddress}
            </span>
          </p>
        )}
      </div>

      <div
        style={{
          background: 'var(--bg-surface)',
          border: '1px solid var(--border)',
          borderRadius: 'var(--radius-sm)',
          padding: '0.75rem 1rem',
          marginBottom: '1.25rem',
          fontSize: '0.85rem',
          display: 'flex',
          flexDirection: 'column',
          gap: '0.25rem',
        }}
      >
        <span>
          <span className="muted">Target ID: </span>
          <code style={{ fontFamily: 'monospace' }}>{targetId || '-'}</code>
        </span>
        {workspaceId ? (
          <span>
            <span className="muted">Workspace ID: </span>
            <code style={{ fontFamily: 'monospace' }}>{workspaceId}</code>
          </span>
        ) : null}
      </div>

      {/* Worker / detection-path status strip: stable RPC polling and realtime
          WebSocket are distinct. Never collapse a paused realtime worker into a
          dead source — stable polling keeps detecting transfers. */}
      {realtimeEnabled !== null ? (
        <div
          data-testid="telemetry-worker-status"
          style={{
            background: 'var(--bg-surface)',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius-sm)',
            padding: '0.75rem 1rem',
            marginBottom: '1.25rem',
            fontSize: '0.82rem',
            display: 'flex',
            flexWrap: 'wrap',
            gap: '1.25rem',
            alignItems: 'center',
          }}
        >
          <span style={{ display: 'inline-flex', flexDirection: 'column', gap: '0.1rem' }}>
            <span className="muted">Realtime WebSocket</span>
            <span style={{ fontWeight: 600, color: realtimeEnabled ? 'var(--success-fg)' : 'var(--warning-fg, #d97706)' }}>
              {realtimeState === 'active'
                ? 'Active'
                : realtimeEnabled
                  ? 'Enabled'
                  : 'Paused / Disabled'}
            </span>
          </span>
          <span style={{ display: 'inline-flex', flexDirection: 'column', gap: '0.1rem' }}>
            <span className="muted">Last stable poll</span>
            <span style={{ fontWeight: 600 }}>{fmt(lastStablePollAt)}</span>
          </span>
          <span style={{ display: 'inline-flex', flexDirection: 'column', gap: '0.1rem' }}>
            <span className="muted">Last realtime event</span>
            <span style={{ fontWeight: 600 }}>{fmt(lastRealtimeEventAt)}</span>
          </span>
          {!realtimeEnabled ? (
            <span className="muted" style={{ fontSize: '0.78rem', flex: '1 1 100%' }}>
              Realtime paused; stable polling active. Wallet transfers are detected by Stable RPC Polling.
            </span>
          ) : null}
        </div>
      ) : null}

      {/* Search bar */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '0.75rem',
          marginBottom: '0.75rem',
          flexWrap: 'wrap',
        }}
      >
        <input
          type="search"
          aria-label="Search telemetry"
          placeholder="Search by tx hash, wallet address, block number, event type, or ID…"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          style={{
            flex: '1 1 320px',
            background: 'var(--bg-surface)',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius-sm)',
            color: 'var(--text-primary)',
            fontSize: '0.875rem',
            padding: '0.5rem 0.85rem',
            outline: 'none',
            minWidth: 0,
          }}
        />
      </div>

      {/* Quick filters */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '0.4rem',
          marginBottom: '1rem',
          flexWrap: 'wrap',
        }}
      >
        {QUICK_FILTERS.map((f) => (
          <button
            key={f.id}
            type="button"
            onClick={() => setQuickFilter(f.id)}
            style={{
              background: quickFilter === f.id ? 'var(--text-accent)' : 'var(--bg-surface)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-xs)',
              color: quickFilter === f.id ? '#fff' : 'var(--text-secondary)',
              cursor: 'pointer',
              fontSize: '0.8rem',
              fontWeight: quickFilter === f.id ? 600 : 400,
              padding: '0.3rem 0.75rem',
              transition: 'background 0.12s, color 0.12s',
            }}
          >
            {f.label}
          </button>
        ))}
      </div>

      {/* Pagination info */}
      {!loading && !loadError && rows.length > 0 && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            marginBottom: '0.5rem',
            fontSize: '0.82rem',
            color: 'var(--text-secondary)',
          }}
        >
          <span>
            Page {currentPage + 1} &middot; {filteredRows.length} of {totalCount} row{totalCount !== 1 ? 's' : ''}
          </span>
          <div style={{ display: 'flex', gap: '0.4rem' }}>
            <button
              type="button"
              disabled={!hasPrev}
              onClick={() => setCurrentPage((p) => Math.max(0, p - 1))}
              style={{
                background: 'var(--bg-surface)',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius-xs)',
                color: !hasPrev ? 'var(--text-muted)' : 'var(--text-secondary)',
                cursor: !hasPrev ? 'default' : 'pointer',
                fontSize: '0.78rem',
                padding: '0.25rem 0.6rem',
              }}
            >
              Prev
            </button>
            <button
              type="button"
              disabled={!hasNext}
              onClick={() => setCurrentPage((p) => p + 1)}
              style={{
                background: 'var(--bg-surface)',
                border: '1px solid var(--border)',
                borderRadius: 'var(--radius-xs)',
                color: !hasNext ? 'var(--text-muted)' : 'var(--text-secondary)',
                cursor: !hasNext ? 'default' : 'pointer',
                fontSize: '0.78rem',
                padding: '0.25rem 0.6rem',
              }}
            >
              Next
            </button>
          </div>
        </div>
      )}

      {loadError ? (
        <p className="statusLine" style={{ color: 'var(--danger-fg)' }}>
          {loadError}
        </p>
      ) : null}

      {!loading && !loadError && rows.length === 0 ? (
        <div
          style={{
            padding: '2.5rem 1.5rem',
            textAlign: 'center',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius-md)',
            color: 'var(--text-muted)',
          }}
        >
          <p style={{ margin: 0, fontWeight: 600, fontSize: '1rem' }}>No telemetry data</p>
          <p style={{ margin: '0.5rem 0 0', fontSize: '0.875rem' }}>
            No live telemetry has been persisted for this target yet.
          </p>
        </div>
      ) : !loading && !loadError && filteredRows.length === 0 ? (
        <div
          style={{
            padding: '2.5rem 1.5rem',
            textAlign: 'center',
            border: '1px solid var(--border)',
            borderRadius: 'var(--radius-md)',
            color: 'var(--text-muted)',
          }}
        >
          <p style={{ margin: 0, fontWeight: 600, fontSize: '1rem' }}>No results</p>
          <p style={{ margin: '0.5rem 0 0', fontSize: '0.875rem' }}>
            No wallet transfer found yet. Try searching by tx hash or wait for the next polling cycle.
          </p>
        </div>
      ) : (
        <TableShell headers={HEADERS} compact>
          {loading ? (
            <tr>
              <td
                colSpan={HEADERS.length}
                style={{ textAlign: 'center', color: 'var(--text-muted)', padding: '2rem' }}
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
                  <td style={{ whiteSpace: 'nowrap' }}>
                    {kind === 'wallet_transfer' ? (
                      <span
                        style={{
                          background: 'var(--success-bg)',
                          border: '1px solid var(--success-bdr)',
                          borderRadius: 'var(--radius-xs)',
                          color: 'var(--success-fg)',
                          display: 'inline-block',
                          fontSize: '0.75rem',
                          fontWeight: 600,
                          padding: '0.15rem 0.5rem',
                        }}
                      >
                        Wallet transfer detected
                      </span>
                    ) : kind === 'block_poll' ? (
                      <span
                        style={{
                          background: 'var(--info-bg)',
                          border: '1px solid var(--info-bdr)',
                          borderRadius: 'var(--radius-xs)',
                          color: 'var(--info-fg)',
                          display: 'inline-block',
                          fontSize: '0.75rem',
                          padding: '0.15rem 0.5rem',
                        }}
                      >
                        RPC polling heartbeat
                      </span>
                    ) : (
                      <span className="muted" style={{ fontSize: '0.8rem' }}>
                        {row.source_type ?? '-'}
                      </span>
                    )}
                  </td>

                  {/* Detected By — never blank for wallet transfer rows */}
                  <td style={{ whiteSpace: 'nowrap' }}>
                    {kind === 'wallet_transfer' && detectedByRaw ? (
                      <span
                        style={{
                          background: detectedByIsStable
                            ? 'var(--info-bg)'
                            : detectedByIsRealtime
                              ? 'var(--success-bg)'
                              : 'var(--warning-bg)',
                          border: `1px solid ${
                            detectedByIsStable
                              ? 'var(--info-bdr)'
                              : detectedByIsRealtime
                                ? 'var(--success-bdr)'
                                : 'var(--warning-bdr)'
                          }`,
                          borderRadius: 'var(--radius-xs)',
                          color: detectedByIsStable
                            ? 'var(--info-fg)'
                            : detectedByIsRealtime
                              ? 'var(--success-fg)'
                              : 'var(--warning-fg, #d97706)',
                          display: 'inline-block',
                          fontSize: '0.72rem',
                          fontWeight: 600,
                          padding: '0.15rem 0.5rem',
                        }}
                      >
                        {formatDetectedBy(detectedByRaw)}
                      </span>
                    ) : detectedByRaw ? (
                      <span className="muted" style={{ fontSize: '0.78rem' }}>
                        {formatDetectedBy(detectedByRaw)}
                      </span>
                    ) : (
                      <span className="muted" style={{ fontSize: '0.8rem' }}>-</span>
                    )}
                  </td>

                  {/* Tx Hash */}
                  <td>
                    {txHash ? (
                      <span
                        style={{ display: 'flex', alignItems: 'center', gap: '0.35rem', flexWrap: 'wrap' }}
                      >
                        {isBaseScan ? (
                          <a
                            href={`${BASESCAN_TX_BASE}${txHash}`}
                            target="_blank"
                            rel="noopener noreferrer"
                            title={txHash}
                            style={{
                              fontFamily: 'monospace',
                              fontSize: '0.78rem',
                              color: 'var(--text-accent)',
                            }}
                          >
                            {shortenHash(txHash)} ↗
                          </a>
                        ) : (
                          <code
                            style={{ fontFamily: 'monospace', fontSize: '0.78rem' }}
                            title={txHash}
                          >
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
                          style={{
                            background: 'none',
                            border: '1px solid var(--border)',
                            borderRadius: 'var(--radius-xs)',
                            color:
                              copiedTxId === row.id
                                ? 'var(--success-fg)'
                                : 'var(--text-secondary)',
                            cursor: 'pointer',
                            fontSize: '0.68rem',
                            lineHeight: 1.4,
                            padding: '0.1rem 0.35rem',
                          }}
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
                      <code
                        style={{ fontFamily: 'monospace', fontSize: '0.78rem' }}
                        title={fromAddr}
                      >
                        {shortenAddress(fromAddr)}
                      </code>
                    ) : (
                      <span className="muted">-</span>
                    )}
                  </td>

                  {/* To */}
                  <td>
                    {toAddr ? (
                      <code
                        style={{ fontFamily: 'monospace', fontSize: '0.78rem' }}
                        title={toAddr}
                      >
                        {shortenAddress(toAddr)}
                      </code>
                    ) : (
                      <span className="muted">-</span>
                    )}
                  </td>

                  {/* Amount */}
                  <td>
                    {amount ? (
                      <code style={{ fontFamily: 'monospace', fontSize: '0.78rem' }}>{amount}</code>
                    ) : (
                      <span className="muted">-</span>
                    )}
                  </td>

                  {/* Chain ID */}
                  <td>{row.chain_id ?? '-'}</td>

                  {/* Block Number */}
                  <td>{row.block_number != null ? String(row.block_number) : '-'}</td>

                  {/* Observed At */}
                  <td style={{ whiteSpace: 'nowrap' }}>{fmt(row.observed_at)}</td>

                  {/* Evidence Source */}
                  <td>{row.evidence_source ?? '-'}</td>

                  {/* Details */}
                  <td>
                    {row.payload_json != null ? (
                      <button
                        type="button"
                        onClick={() => setSelectedRow(row)}
                        style={{
                          background: 'none',
                          border: 'none',
                          color: 'var(--text-accent)',
                          cursor: 'pointer',
                          fontSize: '0.78rem',
                          padding: 0,
                          textDecoration: 'underline',
                        }}
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
