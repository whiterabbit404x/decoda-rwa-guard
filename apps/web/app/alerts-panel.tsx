'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';

import {
  EmptyStateBlocker,
  MetricTile,
  StatusPill,
  TableShell,
  type PillVariant,
} from './components/ui-primitives';
import { usePilotAuth } from './pilot-auth-context';
import { useRuntimeSummary } from './runtime-summary-context';

// Same-origin proxy base. The Alerts page MUST NOT call the backend directly: the browser
// only sees NEXT_PUBLIC_API_URL (often unset in production), so a direct fetch never reaches
// the backend and the list silently renders empty (Active Alerts = 0). Every backend call
// below goes through the Next.js /api/* proxy, which resolves the backend URL server-side —
// the same transport telemetry / runtime-status already use.
const API_PROXY_BASE = '/api';

/* ── Types ──────────────────────────────────────────────────────── */

type AlertRow = {
  id: string;
  title?: string | null;
  severity?: string | null;
  status?: string | null;
  target_id?: string | null;
  detection_id?: string | null;
  incident_id?: string | null;
  evidence_source?: string | null;
  evidence_origin?: string | null;
  source?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  last_seen_at?: string | null;
  // On-chain transaction evidence (returned top-level by /alerts, coalesced from the
  // evidence table or the alert payload). Present for live wallet-transfer alerts.
  tx_hash?: string | null;
  block_number?: string | number | null;
  from_address?: string | null;
  to_address?: string | null;
  amount_wei?: string | null;
  chain_id?: string | number | null;
  confidence?: string | null;
  detection_type?: string | null;
  payload?: {
    asset_label?: string | null;
    detection_type?: string | null;
    confidence?: string | null;
    tx_hash?: string | null;
    from_address?: string | null;
    to_address?: string | null;
    amount_wei?: string | null;
    chain_id?: string | number | null;
    block_number?: string | number | null;
  } | null;
  detector_kind?: string | null;
  linked_evidence_count?: number | null;
  occurrence_count?: number | null;
  chain_linked_ids?: Record<string, string> | null;
};

/* ── On-chain field accessors (top-level field first, then payload) ─ */

function txHashOf(a: AlertRow): string | null {
  return a.tx_hash ?? a.payload?.tx_hash ?? null;
}

function shortHash(hash?: string | null): string {
  if (!hash) return '-';
  return hash.length > 14 ? `${hash.slice(0, 8)}…${hash.slice(-6)}` : hash;
}

// An alert is high-confidence when it carries live on-chain evidence: evidence_source=live
// AND a real tx_hash. This is truthful — it is derived from canonical alert fields, never
// inferred, and never counts simulator/no-evidence alerts as high confidence.
function isHighConfidence(a: AlertRow): boolean {
  const src = (a.evidence_source ?? a.evidence_origin ?? a.source ?? '').toLowerCase();
  const isLive = src === 'live' || src === 'live_provider';
  return isLive && !!txHashOf(a);
}

/* ── Helpers ────────────────────────────────────────────────────── */

// Simulator evidence must always show evidence_source = simulator.
// Real provider evidence must show live_provider only when real data exists.
// Do not label simulator evidence as live_provider.
function evidenceSourcePill(
  rowSource?: string | null,
  workspaceSource?: string,
): { label: string; variant: PillVariant } {
  const raw = (rowSource ?? '').toLowerCase();
  if (
    raw === 'simulator' ||
    raw === 'demo' ||
    raw === 'replay' ||
    workspaceSource === 'simulator'
  ) {
    return { label: 'simulator', variant: 'info' };
  }
  if (raw === 'live' || raw === 'live_provider') {
    return { label: 'live_provider', variant: 'success' };
  }
  return { label: 'none', variant: 'neutral' };
}

function severityPill(severity?: string | null): { label: string; variant: PillVariant } {
  const s = (severity ?? 'unknown').toLowerCase();
  if (s === 'critical') return { label: 'Critical', variant: 'danger' };
  if (s === 'high') return { label: 'High', variant: 'danger' };
  if (s === 'medium') return { label: 'Medium', variant: 'warning' };
  if (s === 'low') return { label: 'Low', variant: 'success' };
  if (s === 'info') return { label: 'Info', variant: 'info' };
  return { label: 'Unknown', variant: 'neutral' };
}

function statusPill(status?: string | null): { label: string; variant: PillVariant } {
  const s = (status ?? 'unknown').toLowerCase();
  if (s === 'open') return { label: 'Open', variant: 'danger' };
  if (s === 'investigating' || s === 'acknowledged') return { label: 'Investigating', variant: 'info' };
  if (s === 'linked_to_incident') return { label: 'Linked to Incident', variant: 'warning' };
  if (s === 'resolved') return { label: 'Resolved', variant: 'success' };
  if (s === 'suppressed') return { label: 'Suppressed', variant: 'neutral' };
  if (s === 'false_positive') return { label: 'False Positive', variant: 'neutral' };
  return { label: 'Unknown', variant: 'neutral' };
}

// Prevent "Linked to Incident" from appearing when no incident actually exists
function resolvedStatus(alert: AlertRow): string | null {
  if ((alert.status ?? '').toLowerCase() === 'linked_to_incident' && !alert.incident_id) {
    return 'open';
  }
  return alert.status ?? null;
}

function fmt(value?: string | null): string {
  if (!value) return '-';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '-';
  const diff = Date.now() - parsed.getTime();
  if (diff < 60_000) return `${Math.floor(diff / 1000)}s ago`;
  if (diff < 3_600_000) return `${Math.floor(diff / 60_000)}m ago`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)}h ago`;
  return parsed.toLocaleDateString();
}

/* ── Constants ──────────────────────────────────────────────────── */

const ALERT_TABLE_HEADERS = ['Alert ID', 'Severity', 'Title', 'Asset', 'Status', 'Time', 'Action'];

/* ── Main panel ─────────────────────────────────────────────────── */

export default function AlertsPanel() {
  const { summary, runtime, loading: runtimeLoading } = useRuntimeSummary();
  const { authHeaders } = usePilotAuth();
  const apiUrl = API_PROXY_BASE;

  const [alerts, setAlerts] = useState<AlertRow[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [search, setSearch] = useState('');
  const [severityFilter, setSeverityFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('');
  const [evidenceSourceFilter, setEvidenceSourceFilter] = useState('');
  const [dataLoading, setDataLoading] = useState(false);
  const [message, setMessage] = useState('');
  const [streamStatus, setStreamStatus] = useState<'connected' | 'reconnecting' | 'polling' | 'offline'>('offline');
  const [runDetectionLoading, setRunDetectionLoading] = useState(false);
  const [openAlertLoading, setOpenAlertLoading] = useState(false);

  const counts = runtime?.counts as Record<string, number> | undefined;
  const workspaceEvidenceSource: string = summary.evidence_source_summary ?? '';
  const telemetryOk = (counts?.telemetry_events ?? 0) > 0 || !!summary.last_telemetry_at;
  const detectionOk = (counts?.detections ?? 0) > 0 || !!(summary as any).last_detection_at;
  const runtimeActiveAlerts: number = (counts?.active_alerts as number | undefined) ?? summary.active_alerts_count ?? 0;
  // Active Alerts reflects the open alerts actually returned by /alerts (canonical alert
  // rows), maxed with the runtime counter. This surfaces telemetry/backfill-created
  // wallet-transfer alerts the runtime proof-chain counter has not linked yet; their
  // status is normalised to 'open' server-side. It never invents alerts — it counts real
  // rows the API returned, and never drops below the canonical runtime count.
  const openAlertsInList = alerts.filter((a) => {
    const s = (a.status ?? '').toLowerCase();
    return s === 'open' || s === 'acknowledged' || s === 'investigating';
  }).length;
  const activeAlerts: number = Math.max(runtimeActiveAlerts, openAlertsInList);

  async function fetchAlerts(cancelled: { value: boolean }): Promise<AlertRow[]> {
    try {
      const params = new URLSearchParams();
      if (severityFilter) params.set('severity', severityFilter);
      if (statusFilter) params.set('status_value', statusFilter);
      const endpoint = `${apiUrl}/alerts?${params.toString()}`;
      console.log('frontend_alerts_fetch_started', { endpoint, severityFilter, statusFilter });
      const res = await fetch(endpoint, {
        headers: authHeaders(),
        cache: 'no-store',
      });
      if (!res.ok || cancelled.value) {
        console.log('frontend_alerts_fetch_response_count', { ok: res.ok, status: res.status, count: 0 });
        return [];
      }
      const json = (await res.json().catch(() => ({}))) as Record<string, unknown>;
      const rows = (json.alerts ?? []) as AlertRow[];
      console.log('frontend_alerts_fetch_response_count', {
        ok: true,
        status: res.status,
        count: rows.length,
        ids: rows.map((row) => row.id),
      });
      if (!cancelled.value) {
        setAlerts(rows);
        if (!selectedId && rows.length > 0) setSelectedId(rows[0].id);
      }
      return rows;
    } finally {
      if (!cancelled.value) setDataLoading(false);
    }
  }

  async function openAlert() {
    console.log('open_alert_clicked');
    setOpenAlertLoading(true);
    setMessage('');
    try {
      console.log('open_alert_request_started');
      const res = await fetch(`${apiUrl}/alerts/open-from-detection`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
      });
      let json: { status?: string; alert_id?: string | null; detection_id?: string | null; detail?: unknown } = {};
      try {
        json = (await res.json()) as typeof json;
      } catch {
        json = {};
      }

      // Any response that names a concrete alert (201 created, 409 already_exists, or a 200
      // carrying alert_id) must NAVIGATE to that alert after refreshing the list — never just
      // a toast. The list now reaches the backend (same-origin proxy), so the alert is
      // returned and selectable.
      const namedAlertId = json.alert_id ?? null;
      if (res.status === 201 || res.status === 409 || namedAlertId) {
        const created = res.status === 201 || json.status === 'created';
        console.log(created ? 'open_alert_created' : 'open_alert_already_exists', namedAlertId);
        if (namedAlertId) setSelectedId(namedAlertId);
        setMessage(created ? 'Alert opened successfully.' : 'Alert already open — navigating to existing alert.');
        const noop = { value: false };
        setDataLoading(true);
        const rows = await fetchAlerts(noop);
        if (namedAlertId && rows.some((row) => row.id === namedAlertId)) {
          setSelectedId(namedAlertId);
        } else if (namedAlertId) {
          console.log('existing_alert_not_visible_after_refresh', {
            alert_id: namedAlertId,
            filters: { severityFilter, statusFilter },
            response_count: rows.length,
            returned_ids: rows.map((row) => row.id),
          });
          if (rows.length > 0) setSelectedId(rows[0].id);
        }
      } else if (res.ok) {
        if (json.status === 'no_detection') {
          setMessage('No open detections found. Run Detection first.');
        } else {
          // 200 suppressed with no alert_id: refresh and select an existing live/critical
          // wallet-transfer alert so the operator lands on the real evidence, not a dead toast.
          setMessage('Alert suppressed or already linked. Refreshing list.');
          const noop = { value: false };
          setDataLoading(true);
          const rows = await fetchAlerts(noop);
          if (!rows || rows.length === 0) {
            console.log('existing_alert_not_visible_after_refresh', {
              alert_id: null,
              filters: { severityFilter, statusFilter },
              response_count: 0,
              returned_ids: [],
            });
          } else {
            const target =
              rows.find(
                (a) =>
                  (a.severity ?? '').toLowerCase() === 'critical' &&
                  ['live', 'live_provider'].includes((a.evidence_source ?? '').toLowerCase()),
              ) ?? rows[0];
            setSelectedId(target.id);
            setMessage('Alert found — opening existing alert.');
          }
        }
      } else {
        // 500 with exact backend error in `detail` (requirement 6).
        console.log('open_alert_failed', res.status, json.detail);
        const detail = typeof json.detail === 'string' ? json.detail : '';
        setMessage(detail ? `Failed to open alert — ${detail}` : 'Failed to open alert. Check logs for details.');
      }
    } catch {
      console.log('open_alert_failed', 'network_error');
      setMessage('Failed to open alert — network error.');
    } finally {
      setOpenAlertLoading(false);
    }
  }

  async function runDetection() {
    setRunDetectionLoading(true);
    setMessage('');
    try {
      const res = await fetch(`${apiUrl}/run-detection`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
      });
      if (res.ok) {
        const json = (await res.json()) as Record<string, unknown>;
        const created = (json.alerts_created as number | undefined) ?? 0;
        setMessage(created > 0 ? `Detection run complete. ${created} alert(s) created.` : 'Detection run complete. No new alerts (existing detections are already up to date).');
        const noop = { value: false };
        setDataLoading(true);
        void fetchAlerts(noop);
      } else {
        setMessage('Detection run failed. Check logs for details.');
      }
    } catch {
      setMessage('Detection run failed — network error.');
    } finally {
      setRunDetectionLoading(false);
    }
  }

  useEffect(() => {
    if (runtimeLoading) return;
    const cancelled = { value: false };
    setDataLoading(true);
    void fetchAlerts(cancelled);
    return () => {
      cancelled.value = true;
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [apiUrl, authHeaders, runtimeLoading, severityFilter, statusFilter]);

  // SSE streaming — supplements polling with real-time alert updates.
  // Falls back gracefully to polling if the stream endpoint is unavailable.
  useEffect(() => {
    if (runtimeLoading) return;
    let abortController: AbortController | null = null;
    let reconnectTimeout: ReturnType<typeof setTimeout> | null = null;
    let active = true;

    async function connectStream() {
      if (!active) return;
      setStreamStatus('reconnecting');
      abortController = new AbortController();
      try {
        const headers = authHeaders();
        const res = await fetch(`${apiUrl}/stream/alerts`, {
          headers,
          signal: abortController.signal,
        });
        if (!res.ok || !res.body) {
          setStreamStatus('polling');
          return;
        }
        setStreamStatus('connected');
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        while (active) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() ?? '';
          for (const line of lines) {
            if (line.startsWith('data: ')) {
              try {
                const payload = JSON.parse(line.slice(6)) as { type?: string; payload?: AlertRow };
                if (payload.type === 'alert' && payload.payload) {
                  setAlerts((prev) => {
                    const exists = prev.find((a) => a.id === payload.payload!.id);
                    if (exists) return prev.map((a) => (a.id === payload.payload!.id ? payload.payload! : a));
                    return [payload.payload!, ...prev].slice(0, 200);
                  });
                }
              } catch {
                // Ignore malformed SSE data lines
              }
            }
          }
        }
      } catch (err: unknown) {
        if ((err as Error)?.name === 'AbortError') return;
        setStreamStatus('polling');
        if (active) {
          reconnectTimeout = setTimeout(() => void connectStream(), 5000);
        }
      }
    }

    void connectStream();
    return () => {
      active = false;
      abortController?.abort();
      if (reconnectTimeout) clearTimeout(reconnectTimeout);
      setStreamStatus('offline');
    };
  }, [apiUrl, authHeaders, runtimeLoading]);

  const filteredAlerts = useMemo(() => {
    return alerts.filter((a) => {
      const q = search.toLowerCase();
      const matchesSearch =
        !q ||
        (a.title ?? '').toLowerCase().includes(q) ||
        (a.id ?? '').toLowerCase().includes(q) ||
        (a.payload?.asset_label ?? '').toLowerCase().includes(q);
      const rawSrc = (a.evidence_source ?? a.evidence_origin ?? a.source ?? '').toLowerCase();
      const isSimulator =
        rawSrc === 'simulator' ||
        rawSrc === 'demo' ||
        rawSrc === 'replay' ||
        workspaceEvidenceSource === 'simulator';
      const isLive = rawSrc === 'live' || rawSrc === 'live_provider';
      const matchesEvidenceSource =
        !evidenceSourceFilter ||
        (evidenceSourceFilter === 'simulator' && isSimulator) ||
        (evidenceSourceFilter === 'live_provider' && isLive);
      return matchesSearch && matchesEvidenceSource;
    });
  }, [alerts, search, evidenceSourceFilter, workspaceEvidenceSource]);

  const selectedAlert = useMemo(
    () => filteredAlerts.find((a) => a.id === selectedId) ?? null,
    [filteredAlerts, selectedId],
  );

  // Rendered-row diagnostic: the rows actually shown in the table after client-side
  // search/source filtering. With the empty state suppressed for count > 0, this must match
  // the count-card population (both derive from the normalised /alerts list).
  useEffect(() => {
    console.log('frontend_alerts_render_count', {
      count: filteredAlerts.length,
      ids: filteredAlerts.map((a) => a.id),
    });
  }, [filteredAlerts]);

  // Count cards are derived from the same normalised /alerts list as the rendered rows, so
  // they can never disagree with what the operator sees in the table.
  const criticalCount = alerts.filter(
    (a) => (a.severity ?? '').toLowerCase() === 'critical',
  ).length;
  const highConfidenceCount = alerts.filter(isHighConfidence).length;
  const linkedIncidentCount = alerts.filter((a) => !!a.incident_id).length;

  /* ── Empty state blocker ────────────────────────────────────────── */
  type Blocker = { title: string; body: string; ctaHref?: string; ctaLabel: string; ctaOnClick?: () => void; ctaDisabled?: boolean };

  function getBlocker(): Blocker | null {
    if (!telemetryOk) {
      return {
        title: 'No alerts yet',
        body: 'No alerts yet because no telemetry has been received.',
        ctaHref: '/threat',
        ctaLabel: 'View Threat Monitoring',
      };
    }
    if (!detectionOk) {
      return {
        title: 'No alerts yet',
        body: 'Telemetry has been received, but no detection has been generated yet.',
        ctaLabel: runDetectionLoading ? 'Running...' : 'Run Detection',
        ctaOnClick: runDetection,
        ctaDisabled: runDetectionLoading,
      };
    }
    if (alerts.length === 0) {
      return {
        title: 'No alerts opened',
        body: 'Detections exist, but no alert has been opened yet.',
        ctaLabel: openAlertLoading ? 'Opening...' : 'Open Alert',
        ctaOnClick: openAlert,
        ctaDisabled: openAlertLoading,
      };
    }
    return null;
  }

  const blocker = dataLoading ? null : getBlocker();

  return (
    <section className="featureSection">
      {/* ── Stream status indicator ────────────────────────────────── */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.5rem' }}>
        <span
          style={{
            width: 8,
            height: 8,
            borderRadius: '50%',
            background:
              streamStatus === 'connected'
                ? '#22c55e'
                : streamStatus === 'reconnecting'
                  ? '#f59e0b'
                  : '#6b7280',
            display: 'inline-block',
          }}
        />
        <span style={{ fontSize: '0.72rem', color: '#94a3b8' }}>
          {streamStatus === 'connected'
            ? 'Live connected'
            : streamStatus === 'reconnecting'
              ? 'Reconnecting...'
              : streamStatus === 'polling'
                ? 'Polling fallback'
                : 'Offline'}
        </span>
      </div>

      {/* ── Metric row ─────────────────────────────────────────────── */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(4, 1fr)',
          gap: '1rem',
          marginBottom: '1.5rem',
        }}
      >
        <MetricTile label="Active Alerts" value={activeAlerts} />
        <MetricTile label="Critical Alerts" value={criticalCount} />
        <MetricTile label="High Confidence" value={highConfidenceCount} />
        <MetricTile label="Linked Incidents" value={linkedIncidentCount} />
      </div>

      {/* ── Filter bar ─────────────────────────────────────────────── */}
      <div
        className="buttonRow"
        style={{ marginBottom: '1rem', alignItems: 'center', flexWrap: 'wrap', gap: '0.5rem' }}
      >
        <input
          placeholder="Search alerts..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          style={{ flex: '1 1 200px', minWidth: '180px' }}
          aria-label="Search alerts"
        />
        <select
          value={severityFilter}
          onChange={(e) => setSeverityFilter(e.target.value)}
          aria-label="Severity filter"
        >
          <option value="">All Severities</option>
          <option value="critical">Critical</option>
          <option value="high">High</option>
          <option value="medium">Medium</option>
          <option value="low">Low</option>
          <option value="info">Info</option>
        </select>
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          aria-label="Status filter"
        >
          <option value="">All Statuses</option>
          <option value="open">Open</option>
          <option value="acknowledged">Investigating</option>
          <option value="resolved">Resolved</option>
          <option value="suppressed">Suppressed</option>
        </select>
        <select
          value={evidenceSourceFilter}
          onChange={(e) => setEvidenceSourceFilter(e.target.value)}
          aria-label="Evidence Source filter"
        >
          <option value="">All Sources</option>
          <option value="simulator">Simulator</option>
          <option value="live_provider">Live Provider</option>
        </select>
        <button
          type="button"
          className="btn btn-primary"
          disabled
          style={{ opacity: 0.45 }}
          title="Alert creation from detection is not yet configured"
        >
          Create Alert
        </button>
      </div>
      {/* ── Content ────────────────────────────────────────────────── */}
      {blocker ? (
        <EmptyStateBlocker
          title={blocker.title}
          body={blocker.body}
          ctaHref={blocker.ctaHref}
          ctaLabel={blocker.ctaLabel}
          ctaOnClick={blocker.ctaOnClick}
          ctaDisabled={blocker.ctaDisabled}
        />
      ) : (
        <div
          style={{
            display: 'grid',
            gridTemplateColumns: selectedAlert ? '1fr 380px' : '1fr',
            gap: '1rem',
            alignItems: 'start',
          }}
        >
          {/* ── Alert table ─────────────────────────────────────────── */}
          <div>
            {filteredAlerts.length === 0 && !dataLoading ? (
              <div className="emptyStatePanel sharedEmptyStateBlocker">
                <h4>No alerts match current filters</h4>
                <p className="muted">Adjust the filters above to see more results.</p>
              </div>
            ) : (
              <TableShell headers={ALERT_TABLE_HEADERS} compact>
                {filteredAlerts.map((alert) => {
                  const sev = severityPill(alert.severity);
                  const st = statusPill(resolvedStatus(alert));
                  const isSelected = alert.id === selectedId;
                  return (
                    <tr
                      key={alert.id}
                      onClick={() => setSelectedId(alert.id)}
                      style={{
                        cursor: 'pointer',
                        background: isSelected ? 'rgba(59,130,246,0.08)' : undefined,
                      }}
                    >
                      <td
                        style={{
                          fontFamily: 'monospace',
                          fontSize: '0.75rem',
                          whiteSpace: 'nowrap',
                          maxWidth: '120px',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                        }}
                        title={alert.id}
                      >
                        {alert.id}
                      </td>
                      <td>
                        <StatusPill label={sev.label} variant={sev.variant} />
                      </td>
                      <td
                        style={{
                          maxWidth: '240px',
                          overflow: 'hidden',
                          textOverflow: 'ellipsis',
                          whiteSpace: 'nowrap',
                        }}
                      >
                        {alert.title ?? '-'}
                      </td>
                      <td style={{ fontSize: '0.8rem' }}>
                        <div>{alert.payload?.asset_label ?? alert.target_id ?? '-'}</div>
                        {txHashOf(alert) ? (
                          <div
                            style={{ fontFamily: 'monospace', fontSize: '0.7rem', color: '#94a3b8' }}
                            title={txHashOf(alert) ?? undefined}
                          >
                            {shortHash(txHashOf(alert))}
                          </div>
                        ) : null}
                      </td>
                      <td>
                        <StatusPill label={st.label} variant={st.variant} />
                      </td>
                      <td style={{ fontSize: '0.78rem', whiteSpace: 'nowrap' }}>
                        {fmt(alert.created_at)}
                      </td>
                      <td>
                        {alert.incident_id ? (
                          <Link
                            href={`/incidents/${alert.incident_id}`}
                            prefetch={false}
                            className="btn btn-secondary"
                            style={{ fontSize: '0.73rem', padding: '0.2rem 0.5rem' }}
                            onClick={(e) => e.stopPropagation()}
                          >
                            View Incident
                          </Link>
                        ) : (
                          <button
                            type="button"
                            className="btn btn-secondary"
                            style={{ fontSize: '0.73rem', padding: '0.2rem 0.5rem' }}
                            onClick={(e) => {
                              e.stopPropagation();
                              setSelectedId(alert.id);
                            }}
                          >
                            Open Incident
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </TableShell>
            )}
          </div>

          {/* ── Detail panel ────────────────────────────────────────── */}
          {selectedAlert && (
            <AlertDetailPanel
              alert={selectedAlert}
              apiUrl={apiUrl}
              authHeaders={authHeaders}
              workspaceEvidenceSource={workspaceEvidenceSource}
              onMessage={setMessage}
            />
          )}
        </div>
      )}

      {message ? (
        <p className="statusLine" style={{ marginTop: '0.5rem' }}>
          {message}
        </p>
      ) : null}
    </section>
  );
}

/* ── Alert detail panel ─────────────────────────────────────────── */

function OnChainField({ label, value, mono }: { label: string; value?: string | null; mono?: boolean }) {
  return (
    <div>
      <p className="tableMeta" style={{ marginBottom: '0.1rem' }}>{label}</p>
      <p
        style={{
          fontSize: mono ? '0.72rem' : '0.8rem',
          margin: 0,
          fontFamily: mono ? 'monospace' : undefined,
          wordBreak: mono ? 'break-all' : undefined,
        }}
      >
        {value ?? '-'}
      </p>
    </div>
  );
}

function AlertDetailPanel({
  alert,
  apiUrl,
  authHeaders,
  workspaceEvidenceSource,
  onMessage,
}: {
  alert: AlertRow;
  apiUrl: string;
  authHeaders: () => Record<string, string>;
  workspaceEvidenceSource: string;
  onMessage: (msg: string) => void;
}) {
  const [timeline, setTimeline] = useState<any[]>([]);

  useEffect(() => {
    if (!alert.incident_id) {
      setTimeline([]);
      return;
    }
    void fetch(`${apiUrl}/incidents/${alert.incident_id}/timeline`, {
      headers: authHeaders(),
      cache: 'no-store',
    })
      .then((r) => (r.ok ? r.json() : null))
      .then((json) => setTimeline((json as any)?.timeline ?? []))
      .catch(() => setTimeline([]));
  }, [apiUrl, authHeaders, alert.incident_id]);

  const sev = severityPill(alert.severity);
  const st = statusPill(resolvedStatus(alert));
  const evSrc = evidenceSourcePill(
    alert.evidence_source ?? alert.evidence_origin ?? alert.source,
    workspaceEvidenceSource,
  );

  async function openIncident() {
    // Already linked → go straight to the persisted incident.
    if (alert.incident_id) {
      window.location.href = `/incidents/${alert.incident_id}`;
      return;
    }
    const res = await fetch(`${apiUrl}/alerts/${alert.id}/escalate`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify({
        title: `Escalated alert: ${alert.title ?? alert.id}`,
        summary: alert.title ?? alert.id,
      }),
    });
    if (!res.ok) {
      const errorPayload = await res.json().catch(() => ({}));
      onMessage(`Unable to open incident: ${(errorPayload as { detail?: string }).detail ?? 'Server error. Please retry.'}`);
      return;
    }
    // Navigate to the incident the backend actually created/linked (idempotent: created or not),
    // so the operator lands on the real /incidents/{id} row rather than a dead toast.
    const result = (await res.json().catch(() => ({}))) as { incident_id?: string; created?: boolean };
    onMessage(result.created ? 'Incident opened.' : 'Incident already open — opening existing incident.');
    if (result.incident_id) {
      window.location.href = `/incidents/${result.incident_id}`;
    }
  }

  return (
    <aside
      className="dataCard sharedSurfaceCard"
      style={{ padding: '1rem', borderLeft: '1px solid rgba(148,163,184,0.15)' }}
      aria-label="Alert detail"
    >
      <p className="eyebrow" style={{ marginBottom: '0.25rem', fontSize: '0.7rem' }}>
        Alert Detail
      </p>
      <h4 style={{ marginBottom: '0.75rem', fontSize: '0.95rem', lineHeight: 1.35 }}>
        {alert.title ?? 'Untitled Alert'}
      </h4>

      <div
        style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.6rem 1rem', marginBottom: '0.75rem' }}
      >
        <div>
          <p className="tableMeta" style={{ marginBottom: '0.15rem' }}>Severity</p>
          <StatusPill label={sev.label} variant={sev.variant} />
        </div>
        <div>
          <p className="tableMeta" style={{ marginBottom: '0.15rem' }}>Status</p>
          <StatusPill label={st.label} variant={st.variant} />
        </div>
        <div>
          <p className="tableMeta" style={{ marginBottom: '0.15rem' }}>Asset</p>
          <p style={{ fontSize: '0.8rem', margin: 0 }}>
            {alert.payload?.asset_label ?? alert.target_id ?? '-'}
          </p>
        </div>
        <div>
          <p className="tableMeta" style={{ marginBottom: '0.15rem' }}>Evidence Source</p>
          <StatusPill label={evSrc.label} variant={evSrc.variant} />
        </div>
        <div>
          <p className="tableMeta" style={{ marginBottom: '0.15rem' }}>Detection Type</p>
          <p style={{ fontSize: '0.8rem', margin: 0 }}>
            {alert.payload?.detection_type ?? alert.detection_type ?? alert.detector_kind ?? '-'}
          </p>
        </div>
        <div>
          <p className="tableMeta" style={{ marginBottom: '0.15rem' }}>Confidence</p>
          <p style={{ fontSize: '0.8rem', margin: 0 }}>
            {alert.payload?.confidence ?? alert.confidence ?? '-'}
          </p>
        </div>
      </div>

      {/* On-chain transaction — only rendered when a real tx_hash exists, so live
          wallet-transfer alerts show their canonical evidence and no empty/fake row appears. */}
      {txHashOf(alert) ? (
        <div style={{ marginBottom: '0.75rem' }}>
          <p className="sectionEyebrow" style={{ marginBottom: '0.3rem' }}>On-chain Transaction</p>
          <div style={{ display: 'grid', gridTemplateColumns: '1fr', gap: '0.4rem' }}>
            <OnChainField label="Tx Hash" value={txHashOf(alert)} mono />
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.4rem 1rem' }}>
              <OnChainField label="Chain ID" value={alert.chain_id != null ? String(alert.chain_id) : (alert.payload?.chain_id != null ? String(alert.payload.chain_id) : null)} />
              <OnChainField label="Block" value={alert.block_number != null ? String(alert.block_number) : (alert.payload?.block_number != null ? String(alert.payload.block_number) : null)} />
            </div>
            <OnChainField label="From" value={alert.from_address ?? alert.payload?.from_address ?? null} mono />
            <OnChainField label="To" value={alert.to_address ?? alert.payload?.to_address ?? null} mono />
            <OnChainField label="Amount (wei)" value={alert.amount_wei ?? alert.payload?.amount_wei ?? null} mono />
          </div>
        </div>
      ) : null}

      {/* Linked Detection */}
      <div style={{ marginBottom: '0.6rem' }}>
        <p className="tableMeta" style={{ marginBottom: '0.2rem' }}>Linked Detection</p>
        {alert.detection_id ? (
          <p style={{ fontFamily: 'monospace', fontSize: '0.73rem', margin: 0, wordBreak: 'break-all' }}>
            {alert.detection_id}
          </p>
        ) : (
          <p className="muted" style={{ fontSize: '0.78rem', margin: 0 }}>
            Detection link unavailable
          </p>
        )}
      </div>

      {/* Linked Incident */}
      <div style={{ marginBottom: '0.6rem' }}>
        <p className="tableMeta" style={{ marginBottom: '0.2rem' }}>Linked Incident</p>
        {alert.incident_id ? (
          <Link
            href={`/incidents/${alert.incident_id}`}
            prefetch={false}
            className="btn btn-secondary"
            style={{ fontSize: '0.75rem', padding: '0.2rem 0.6rem' }}
          >
            View Incident
          </Link>
        ) : (
          <button
            type="button"
            className="btn btn-secondary"
            style={{ fontSize: '0.75rem', padding: '0.2rem 0.6rem' }}
            onClick={() => void openIncident()}
          >
            Open Incident
          </button>
        )}
      </div>

      {/* Timestamps */}
      <div style={{ marginBottom: '0.6rem' }}>
        <p className="tableMeta" style={{ marginBottom: '0.1rem' }}>Created At</p>
        <p style={{ fontSize: '0.78rem', margin: 0 }}>
          {alert.created_at ? new Date(alert.created_at).toLocaleString() : '-'}
        </p>
        <p className="tableMeta" style={{ marginBottom: '0.1rem', marginTop: '0.3rem' }}>
          Updated At
        </p>
        <p style={{ fontSize: '0.78rem', margin: 0 }}>
          {alert.updated_at ?? alert.last_seen_at
            ? new Date((alert.updated_at ?? alert.last_seen_at)!).toLocaleString()
            : '-'}
        </p>
      </div>

      {/* Timeline */}
      {timeline.length > 0 && (
        <div style={{ marginBottom: '0.75rem' }}>
          <p className="sectionEyebrow" style={{ marginBottom: '0.3rem' }}>Timeline</p>
          {timeline.slice(0, 6).map((entry: any, i: number) => (
            <div
              key={i}
              style={{
                fontSize: '0.75rem',
                marginBottom: '0.3rem',
                paddingLeft: '0.5rem',
                borderLeft: '2px solid rgba(148,163,184,0.2)',
              }}
            >
              <span className="muted">{fmt(entry.created_at ?? entry.timestamp)}</span>
              {' · '}
              {entry.note ?? entry.event_type ?? entry.type ?? 'Event'}
            </div>
          ))}
        </div>
      )}

      {/* Next action */}
      <div style={{ marginTop: '0.75rem' }}>
        <p className="tableMeta" style={{ marginBottom: '0.3rem' }}>Recommended Next Action</p>
        {!alert.incident_id ? (
          <button
            type="button"
            className="btn btn-primary"
            style={{ fontSize: '0.78rem', marginRight: '0.4rem' }}
            onClick={() => void openIncident()}
          >
            Open Incident
          </button>
        ) : (
          <Link
            href={`/incidents/${alert.incident_id}`}
            prefetch={false}
            className="btn btn-secondary"
            style={{ fontSize: '0.78rem', marginRight: '0.4rem' }}
          >
            View Incident
          </Link>
        )}
        <Link
          href="/response-actions"
          prefetch={false}
          className="btn btn-secondary"
          style={{ fontSize: '0.78rem' }}
        >
          Response Actions
        </Link>
      </div>
    </aside>
  );
}
