'use client';

import Link from 'next/link';
import { useParams } from 'next/navigation';
import { useEffect, useState } from 'react';

import { TableShell } from '../../../../components/ui-primitives';
import { usePilotAuth } from '../../../../pilot-auth-context';

type TelemetryRow = {
  id: string;
  workspace_id?: string | null;
  target_id?: string | null;
  provider_type?: string | null;
  source_type?: string | null;
  evidence_source?: string | null;
  chain_id?: string | null;
  block_number?: number | null;
  observed_at?: string | null;
  ingested_at?: string | null;
  payload_json?: Record<string, unknown> | null;
};

const HEADERS = [
  'ID',
  'Provider Type',
  'Source Type',
  'Evidence Source',
  'Chain ID',
  'Block Number',
  'Observed At',
  'Raw Response',
];

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

export default function TargetTelemetryPage() {
  const params = useParams();
  const targetId = typeof params?.targetId === 'string' ? params.targetId : '';

  const [rows, setRows] = useState<TelemetryRow[]>([]);
  const [workspaceId, setWorkspaceId] = useState('');
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');

  const { authHeaders } = usePilotAuth();

  useEffect(() => {
    if (!targetId) return;
    const controller = new AbortController();
    setLoading(true);
    setLoadError('');

    fetch(`/api/monitoring/targets/${encodeURIComponent(targetId)}/telemetry`, {
      headers: authHeaders(),
      cache: 'no-store',
      signal: controller.signal,
    })
      .then(async (res) => {
        const payload = await res.json().catch(() => ({}));
        if (!res.ok) {
          const detail = typeof payload?.detail === 'string' ? payload.detail : `HTTP ${res.status}`;
          setLoadError(`Unable to load telemetry: ${detail}`);
          return;
        }
        setRows((payload.telemetry as TelemetryRow[]) ?? []);
        if (typeof payload.workspace_id === 'string') {
          setWorkspaceId(payload.workspace_id);
        }
      })
      .catch((err: unknown) => {
        if ((err as { name?: string }).name === 'AbortError') return;
        setLoadError(`Network error: ${err instanceof Error ? err.message : 'unknown error'}`);
      })
      .finally(() => setLoading(false));

    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [targetId, authHeaders]);

  return (
    <main className="productPage">
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
      </div>

      <div
        style={{
          background: 'var(--surface-secondary, #f8f9fa)',
          border: '1px solid var(--border-subtle, #e5e7eb)',
          borderRadius: '6px',
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

      <div
        style={{
          background: 'var(--surface-secondary, #f8f9fa)',
          border: '1px solid var(--border-subtle, #e5e7eb)',
          borderRadius: '6px',
          padding: '0.75rem 1rem',
          marginBottom: '1.25rem',
          fontSize: '0.85rem',
          color: 'var(--text-secondary)',
        }}
      >
        Each row is a persisted live RPC polling result. Raw responses are retained as evidence so detections, alerts, incidents, and audits can be traced back to provider data.
      </div>

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
            border: '1px solid var(--border-subtle, #e5e7eb)',
            borderRadius: '8px',
            color: 'var(--text-muted)',
          }}
        >
          <p style={{ margin: 0, fontWeight: 600, fontSize: '1rem' }}>No telemetry data</p>
          <p style={{ margin: '0.5rem 0 0', fontSize: '0.875rem' }}>
            No live telemetry has been persisted for this target yet.
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
            rows.map((row) => (
              <tr key={row.id}>
                <td>
                  <code style={{ fontFamily: 'monospace', fontSize: '0.78rem' }}>{row.id.slice(0, 8)}…</code>
                </td>
                <td>{row.provider_type ?? '-'}</td>
                <td>{row.source_type ?? '-'}</td>
                <td>{row.evidence_source ?? '-'}</td>
                <td>{row.chain_id ?? '-'}</td>
                <td>{row.block_number != null ? String(row.block_number) : '-'}</td>
                <td style={{ whiteSpace: 'nowrap' }}>{fmt(row.observed_at)}</td>
                <td>
                  {row.payload_json != null ? (
                    <details>
                      <summary style={{ cursor: 'pointer', fontSize: '0.78rem', color: 'var(--text-accent)' }}>
                        View
                      </summary>
                      <pre
                        style={{
                          fontSize: '0.72rem',
                          maxWidth: '320px',
                          overflow: 'auto',
                          margin: '0.25rem 0 0',
                          background: 'var(--surface-secondary, #f8f9fa)',
                          padding: '0.5rem',
                          borderRadius: '4px',
                        }}
                      >
                        {safeJson(row.payload_json)}
                      </pre>
                    </details>
                  ) : (
                    <span className="muted">-</span>
                  )}
                </td>
              </tr>
            ))
          )}
        </TableShell>
      )}
    </main>
  );
}
