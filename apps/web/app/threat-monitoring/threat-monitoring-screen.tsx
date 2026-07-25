'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';

import { EmptyStateBlocker, StatusPill, TabStrip, TableShell } from '../components/ui-primitives';
import { usePilotAuth } from '../pilot-auth-context';
import {
  ANOMALIES_TOOLTIP,
  CONFIDENCE_TOOLTIP,
  DETECTIONS_TOOLTIP,
  EVIDENCE_QUALITY_TOOLTIP,
  MTTD_TOOLTIP,
  TELEMETRY_TOOLTIP,
  confidenceBand,
  confidencePercent,
  confidenceVariant,
  dataFreshnessLabel,
  dataFreshnessVariant,
  degradedReasonCopy,
  detectionStatusLabel,
  detectionStatusVariant,
  detectionTypeLabel,
  evidenceQualityLabel,
  evidenceSourceLabel,
  evidenceSourceVariant,
  formatMttd,
  investigateOutcomeMessage,
  relativeTime,
  severityLabel,
  severityVariant,
  shortHex,
  trend,
  trendColor,
  type DetectionRow,
  type ThreatSummary,
  type TrendMetric,
} from './presentation';
import ThreatDetectionEngineerPanel from './threat-detection-engineer-panel';

type TabKey = 'overview' | 'telemetry' | 'detections' | 'anomalies';

const TABS = [
  { key: 'overview', label: 'Overview' },
  { key: 'telemetry', label: 'Telemetry' },
  { key: 'detections', label: 'Detections' },
  { key: 'anomalies', label: 'Anomalies' },
];

const PAGE_SIZE = 25;

export default function ThreatMonitoringScreen() {
  const { authHeaders, signOut, isAuthenticated } = usePilotAuth();
  const router = useRouter();

  const [summary, setSummary] = useState<ThreatSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [activeTab, setActiveTab] = useState<TabKey>('overview');

  const [investigatingId, setInvestigatingId] = useState<string | null>(null);
  const [investigateError, setInvestigateError] = useState('');

  const loadSummary = useCallback(async () => {
    setError('');
    const headers = authHeaders();
    if (!headers.Authorization) {
      setError('Your session is missing or expired. Please sign in again.');
      setLoading(false);
      return;
    }
    try {
      // GET only — opening/refreshing Screen 5 never writes.
      const res = await fetch('/api/threat-monitoring/summary?window_days=7', { headers: { ...headers }, cache: 'no-store' });
      if (res.status === 401 || res.status === 403) {
        await signOut();
        setError('Your session is missing or expired. Please sign in again.');
        return;
      }
      if (!res.ok) {
        setError('Unable to load the threat monitoring summary right now.');
        return;
      }
      const payload = await res.json();
      setSummary((payload.summary ?? null) as ThreatSummary | null);
    } catch {
      setError('The threat monitoring summary is temporarily unavailable.');
    } finally {
      setLoading(false);
    }
  }, [authHeaders, signOut]);

  useEffect(() => {
    void loadSummary();
  }, [loadSummary]);

  const onInvestigate = useCallback(async (detectionId: string) => {
    setInvestigateError('');
    setInvestigatingId(detectionId);
    try {
      const res = await fetch(`/api/threat-monitoring/detections/${encodeURIComponent(detectionId)}/investigate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        cache: 'no-store',
        body: JSON.stringify({}),
      });
      const payload = await res.json().catch(() => ({}));
      if (res.status === 201 || res.status === 200) {
        const destination = typeof payload.destination === 'string' ? payload.destination : '/alerts';
        router.push(destination);
        return;
      }
      // Actionable errors: 422 insufficient evidence / 409 resolved / others.
      setInvestigateError(payload.detail || investigateOutcomeMessage(payload));
    } catch {
      setInvestigateError('Unable to start the investigation. Please try again.');
    } finally {
      setInvestigatingId(null);
    }
  }, [authHeaders, router]);

  const onViewEvidence = useCallback((detectionId: string) => {
    setActiveTab('detections');
    if (typeof window !== 'undefined') {
      window.setTimeout(() => {
        document.getElementById(`detection-${detectionId}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }, 60);
    }
  }, []);

  return (
    <div style={{ maxWidth: '1440px', width: '100%', margin: '0 auto' }}>
      <header style={{ marginBottom: '1.25rem' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '1rem', flexWrap: 'wrap' }}>
          <p className="muted" style={{ margin: 0, fontSize: '0.95rem' }}>
            Correlated telemetry, behavioral anomalies, and exploit detections.
          </p>
          <button type="button" className="btn btn-secondary" onClick={() => { setLoading(true); void loadSummary(); }}>
            Refresh
          </button>
        </div>
      </header>

      {summary && summary.degraded_reasons.length > 0 ? (
        <div className="statusLine statusLine-warning" role="status" data-testid="degraded-banner" style={{ marginBottom: '1rem' }}>
          {summary.degraded_reasons.map((r) => degradedReasonCopy(r)).join(' ')}
        </div>
      ) : null}

      {/* KPI row */}
      <KpiRow summary={summary} loading={loading} />

      {error ? (
        <p className="statusLine" style={{ color: 'var(--danger-fg)', margin: '0 0 1rem' }} role="alert">{error}</p>
      ) : null}

      <TabStrip tabs={TABS} active={activeTab} onChange={(k) => setActiveTab(k as TabKey)} />

      {activeTab === 'overview' ? (
        <div role="tabpanel" aria-label="Overview" style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 2fr) minmax(0, 1fr)', gap: '1.5rem', marginTop: '0.5rem' }}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '1.5rem', minWidth: 0 }}>
            <TelemetryVolumeCard summary={summary} loading={loading} />
            <DetectionsByTypeCard summary={summary} loading={loading} />
            <WatchlistCard summary={summary} loading={loading} onViewEvidence={onViewEvidence} />
          </div>
          <ThreatDetectionEngineerPanel
            panel={summary?.engine_panel ?? null}
            degradedReasons={summary?.degraded_reasons ?? []}
            loading={loading}
            error={error}
            investigating={investigatingId !== null}
            investigateError={investigateError}
            onInvestigate={onInvestigate}
            onViewEvidence={onViewEvidence}
            onRetry={() => { setLoading(true); void loadSummary(); }}
          />
        </div>
      ) : null}

      {activeTab === 'telemetry' ? <TelemetryTab authHeaders={authHeaders} /> : null}
      {activeTab === 'detections' ? (
        <DetectionsTab authHeaders={authHeaders} onInvestigate={onInvestigate} investigatingId={investigatingId} investigateError={investigateError} />
      ) : null}
      {activeTab === 'anomalies' ? <AnomaliesTab authHeaders={authHeaders} /> : null}
    </div>
  );
}

/* ── KPI row ─────────────────────────────────────────────────────── */
function KpiRow({ summary, loading }: { summary: ThreatSummary | null; loading: boolean }) {
  const freshness = summary?.data_freshness ?? 'unavailable';
  return (
    <div
      style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(190px, 1fr))', gap: '1.25rem', margin: '0 0 1.5rem' }}
      data-testid="kpi-row"
    >
      <Kpi
        label="Telemetry Events"
        value={loading || !summary ? '—' : String(summary.telemetry_events_count)}
        tooltip={TELEMETRY_TOOLTIP}
        metric="telemetry"
        changePercent={summary?.telemetry_change_percent}
        windowDays={summary?.window_days}
        freshnessLabel={dataFreshnessLabel(freshness)}
        freshnessVariant={dataFreshnessVariant(freshness)}
      />
      <Kpi
        label="Detections"
        value={loading || !summary ? '—' : String(summary.detection_count)}
        tooltip={DETECTIONS_TOOLTIP}
        metric="detections"
        changePercent={summary?.detection_change_percent}
        windowDays={summary?.window_days}
      />
      <Kpi
        label="Anomalies"
        value={loading || !summary ? '—' : String(summary.anomaly_count)}
        tooltip={ANOMALIES_TOOLTIP}
        metric="anomalies"
        changePercent={summary?.anomaly_change_percent}
        windowDays={summary?.window_days}
      />
      <Kpi
        label="Mean Time to Detect"
        value={loading || !summary ? '—' : formatMttd(summary.mean_time_to_detect_seconds)}
        tooltip={MTTD_TOOLTIP}
        metric="mttd"
        changePercent={null}
        windowDays={summary?.window_days}
        emptyHint={summary && summary.mean_time_to_detect_seconds === null ? 'No promoted detections to measure' : undefined}
      />
    </div>
  );
}

function Kpi({
  label, value, tooltip, metric, changePercent, windowDays, freshnessLabel, freshnessVariant, emptyHint,
}: {
  label: string;
  value: string;
  tooltip: string;
  metric: TrendMetric;
  changePercent: number | null | undefined;
  windowDays?: number;
  freshnessLabel?: string;
  freshnessVariant?: 'success' | 'warning' | 'danger' | 'info' | 'neutral' | 'default';
  emptyHint?: string;
}) {
  const t = trend(metric, changePercent);
  return (
    <article className="metricCard sharedMetricTile" data-testid={`kpi-${metric}`}>
      <p className="metricLabel" title={tooltip} style={{ cursor: 'help' }}>{label}</p>
      <p className="metricValue">{value}</p>
      <p className="metricMeta" style={{ display: 'flex', alignItems: 'center', gap: '0.4rem', flexWrap: 'wrap' }}>
        <span>{windowDays ? `Last ${windowDays}d` : 'Selected window'}</span>
        {t ? (
          <span data-testid={`trend-${metric}`} style={{ color: trendColor(t.tone), fontWeight: 600 }}>
            {t.arrow} {t.label}
          </span>
        ) : null}
        {freshnessLabel ? <StatusPill label={freshnessLabel} variant={freshnessVariant ?? 'neutral'} /> : null}
        {emptyHint ? <span className="muted">{emptyHint}</span> : null}
      </p>
    </article>
  );
}

/* ── Telemetry volume chart ─────────────────────────────────────── */
function TelemetryVolumeCard({ summary, loading }: { summary: ThreatSummary | null; loading: boolean }) {
  const buckets = summary?.telemetry_volume_buckets ?? [];
  const max = buckets.reduce((m, b) => Math.max(m, b.count), 0);
  return (
    <article className="dataCard" aria-label="Telemetry Volume" style={{ minHeight: '12rem' }}>
      <p className="sectionEyebrow">Telemetry Volume</p>
      {loading ? (
        <div className="skelBlock" style={{ height: '8rem' }} aria-hidden="true" />
      ) : buckets.length === 0 ? (
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '8rem', color: 'var(--text-muted)', gap: '0.35rem' }}>
          <span style={{ fontWeight: 600, color: 'var(--text-secondary)' }}>No telemetry in this window</span>
          <span style={{ fontSize: '0.875rem' }}>Bars appear once the monitoring worker delivers signals.</span>
        </div>
      ) : (
        <div style={{ display: 'flex', alignItems: 'flex-end', gap: '3px', height: '8rem', padding: '0.5rem 0 0', overflowX: 'auto' }} data-testid="telemetry-volume-bars">
          {buckets.map((b) => {
            const h = max > 0 ? Math.max(4, Math.round((b.count / max) * 100)) : 4;
            const nonLive = b.count > b.live_count;
            return (
              <div
                key={b.bucket_epoch}
                title={`${new Date(b.bucket_epoch * 1000).toLocaleString()} · ${b.count} events${nonLive ? ` (${b.count - b.live_count} non-live)` : ''}`}
                style={{ flex: '1 0 6px', minWidth: '6px', background: nonLive ? 'var(--info-fg, #60a5fa)' : 'var(--text-accent, #3b82f6)', borderRadius: '2px 2px 0 0', height: `${h}%` }}
              />
            );
          })}
        </div>
      )}
      <p className="muted" style={{ fontSize: '0.85rem', marginTop: '0.5rem' }}>
        {summary?.ingestion_health?.last_telemetry_at ? `Last event ${relativeTime(summary.ingestion_health.last_telemetry_at)}` : 'No events received'}
        {summary && summary.ingestion_health?.source_breakdown?.simulator ? ' · includes simulator data' : ''}
      </p>
    </article>
  );
}

/* ── Detections by type ─────────────────────────────────────────── */
function DetectionsByTypeCard({ summary, loading }: { summary: ThreatSummary | null; loading: boolean }) {
  const rows = summary?.detections_by_type ?? [];
  const supported = rows.filter((r) => r.supported);
  const unsupported = rows.filter((r) => !r.supported);
  return (
    <article className="dataCard" aria-label="Detections by Type" style={{ minHeight: '10rem' }}>
      <p className="sectionEyebrow">Detections by Type</p>
      {loading ? (
        <div className="skelBlock" style={{ height: '6rem' }} aria-hidden="true" />
      ) : (
        <>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', marginTop: '0.5rem' }} data-testid="detections-by-type">
            {supported.map((r) => (
              <div key={r.type} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '0.9rem' }}>
                <span style={{ color: 'var(--text-secondary)' }}>{r.label}</span>
                <span style={{ color: 'var(--text-accent)', fontWeight: 700, minWidth: '2rem', textAlign: 'right' }}>{r.count}</span>
              </div>
            ))}
          </div>
          {unsupported.length > 0 ? (
            <details style={{ marginTop: '0.75rem' }}>
              <summary className="muted" style={{ cursor: 'pointer', fontSize: '0.8rem' }}>
                {unsupported.length} detector(s) not evaluated (evidence unavailable)
              </summary>
              <ul className="assessorGapList" style={{ marginTop: '0.5rem' }}>
                {unsupported.map((r) => (
                  <li key={r.type}><span>{r.label}</span><strong title={r.unsupported_reason ?? ''} style={{ color: 'var(--text-muted)' }}>n/a</strong></li>
                ))}
              </ul>
            </details>
          ) : null}
        </>
      )}
    </article>
  );
}

/* ── Watchlist matches ──────────────────────────────────────────── */
function WatchlistCard({ summary, loading, onViewEvidence }: { summary: ThreatSummary | null; loading: boolean; onViewEvidence: (id: string) => void }) {
  const matches = summary?.active_watchlist_matches ?? [];
  return (
    <article className="dataCard" aria-label="Active Watchlist Matches">
      <p className="sectionEyebrow">Active Watchlist Matches</p>
      {loading ? (
        <div className="skelBlock" style={{ height: '5rem' }} aria-hidden="true" />
      ) : matches.length === 0 ? (
        <p className="muted" style={{ fontSize: '0.9rem', padding: '0.5rem 0' }}>No active watchlist matches.</p>
      ) : (
        <TableShell headers={['Actor', 'Asset', 'Reason', 'Confidence', 'First seen', 'Last seen', 'Status', 'Evidence']} compact>
          {matches.map((m) => (
            <tr key={`${m.detection_id}-${m.actor_address}`}>
              <td style={{ fontFamily: 'monospace', fontSize: '0.82rem' }}>{shortHex(m.actor_address)}</td>
              <td>{m.asset_name ?? '—'}</td>
              <td>{m.match_reason ?? '—'}</td>
              <td title={CONFIDENCE_TOOLTIP}>{confidencePercent(m.confidence)}</td>
              <td style={{ whiteSpace: 'nowrap' }}>{relativeTime(m.first_seen_at)}</td>
              <td style={{ whiteSpace: 'nowrap' }}>{relativeTime(m.last_seen_at)}</td>
              <td><StatusPill label={detectionStatusLabel(m.status)} variant={detectionStatusVariant(m.status)} /></td>
              <td>
                <button type="button" className="btn btn-secondary" style={{ fontSize: '0.8rem', padding: '0.3rem 0.6rem' }} onClick={() => onViewEvidence(m.detection_id)}>
                  View
                </button>
              </td>
            </tr>
          ))}
        </TableShell>
      )}
    </article>
  );
}

/* ── Detections tab ─────────────────────────────────────────────── */
function DetectionsTab({
  authHeaders, onInvestigate, investigatingId, investigateError,
}: {
  authHeaders: () => Record<string, string>;
  onInvestigate: (id: string) => void;
  investigatingId: string | null;
  investigateError: string;
}) {
  const [rows, setRows] = useState<DetectionRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [offset, setOffset] = useState(0);
  const [severity, setSeverity] = useState('');
  const [type, setType] = useState('');
  const filtersKey = `${severity}|${type}|${offset}`;
  const reqId = useRef(0);

  useEffect(() => {
    const id = ++reqId.current;
    setLoading(true);
    setErr('');
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset) });
    if (severity) params.set('severity', severity);
    if (type) params.set('detection_type', type);
    fetch(`/api/threat-monitoring/detections?${params.toString()}`, { headers: { ...authHeaders() }, cache: 'no-store' })
      .then(async (res) => {
        if (id !== reqId.current) return;
        if (!res.ok) { setErr('Unable to load detections.'); return; }
        const payload = await res.json();
        setRows((payload.detections ?? []) as DetectionRow[]);
        setTotal(Number(payload.total ?? 0));
      })
      .catch(() => { if (id === reqId.current) setErr('Detections are temporarily unavailable.'); })
      .finally(() => { if (id === reqId.current) setLoading(false); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filtersKey, authHeaders]);

  return (
    <div role="tabpanel" aria-label="Detections" style={{ marginTop: '0.75rem' }}>
      <FilterBar
        severity={severity}
        type={type}
        onSeverity={(v) => { setSeverity(v); setOffset(0); }}
        onType={(v) => { setType(v); setOffset(0); }}
      />
      {investigateError ? <p className="statusLine" role="alert" style={{ color: 'var(--danger-fg)' }}>{investigateError}</p> : null}
      {loading ? (
        <p className="muted" style={{ padding: '1.5rem 0' }}>Loading detections…</p>
      ) : err ? (
        <p className="statusLine" role="alert" style={{ color: 'var(--danger-fg)' }}>{err}</p>
      ) : rows.length === 0 ? (
        <EmptyStateBlocker title="No detections" body="No promoted threat detections match the current filters in this window. Anomalies below detection criteria appear in the Anomalies tab." ctaHref="/monitoring-sources" ctaLabel="Review monitoring coverage" />
      ) : (
        <>
          <TableShell headers={['Detection', 'Severity', 'Confidence', 'Asset', 'First seen', 'Last seen', 'Evidence', 'Status', 'Action']} compact>
            {rows.map((d) => (
              <tr key={d.id} id={`detection-${d.id}`}>
                <td>{d.detection_type_label || detectionTypeLabel(d.detection_type)}</td>
                <td title="Potential impact if valid."><StatusPill label={severityLabel(d.severity)} variant={severityVariant(d.severity)} /></td>
                <td title={CONFIDENCE_TOOLTIP}><StatusPill label={`${confidencePercent(d.confidence)} (${confidenceBand(d.confidence)})`} variant={confidenceVariant(d.confidence)} /></td>
                <td>{d.asset_name ?? '—'}</td>
                <td style={{ whiteSpace: 'nowrap' }}>{relativeTime(d.first_seen_at)}</td>
                <td style={{ whiteSpace: 'nowrap' }}>{relativeTime(d.last_seen_at)}</td>
                <td>{d.evidence_count}</td>
                <td><StatusPill label={detectionStatusLabel(d.status)} variant={detectionStatusVariant(d.status)} /></td>
                <td>
                  {d.status === 'open' ? (
                    <button type="button" className="btn btn-secondary" style={{ fontSize: '0.8rem', padding: '0.3rem 0.6rem' }} disabled={investigatingId === d.id} onClick={() => onInvestigate(d.id)}>
                      {investigatingId === d.id ? 'Opening…' : 'Investigate'}
                    </button>
                  ) : (
                    <span className="muted" style={{ fontSize: '0.82rem' }}>{detectionStatusLabel(d.status)}</span>
                  )}
                </td>
              </tr>
            ))}
          </TableShell>
          <Pager offset={offset} total={total} onPrev={() => setOffset(Math.max(0, offset - PAGE_SIZE))} onNext={() => setOffset(offset + PAGE_SIZE)} />
        </>
      )}
    </div>
  );
}

/* ── Anomalies tab ──────────────────────────────────────────────── */
function AnomaliesTab({ authHeaders }: { authHeaders: () => Record<string, string> }) {
  const [rows, setRows] = useState<any[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [offset, setOffset] = useState(0);
  const reqId = useRef(0);

  useEffect(() => {
    const id = ++reqId.current;
    setLoading(true);
    setErr('');
    fetch(`/api/threat-monitoring/anomalies?limit=${PAGE_SIZE}&offset=${offset}`, { headers: { ...authHeaders() }, cache: 'no-store' })
      .then(async (res) => {
        if (id !== reqId.current) return;
        if (!res.ok) { setErr('Unable to load anomalies.'); return; }
        const payload = await res.json();
        setRows((payload.anomalies ?? []) as any[]);
        setTotal(Number(payload.total ?? 0));
      })
      .catch(() => { if (id === reqId.current) setErr('Anomalies are temporarily unavailable.'); })
      .finally(() => { if (id === reqId.current) setLoading(false); });
  }, [offset, authHeaders]);

  return (
    <div role="tabpanel" aria-label="Anomalies" style={{ marginTop: '0.75rem' }}>
      <p className="muted" style={{ fontSize: '0.9rem', marginBottom: '0.75rem' }}>
        Deviations that have not yet crossed detection criteria. An anomaly is not a confirmed threat.
      </p>
      {loading ? (
        <p className="muted" style={{ padding: '1.5rem 0' }}>Loading anomalies…</p>
      ) : err ? (
        <p className="statusLine" role="alert" style={{ color: 'var(--danger-fg)' }}>{err}</p>
      ) : rows.length === 0 ? (
        <EmptyStateBlocker title="No anomalies" body="No sub-threshold deviations are currently tracked for this workspace." />
      ) : (
        <>
          <TableShell headers={['Type', 'Confidence', 'Asset', 'Evidence', 'First seen', 'Last seen', 'Why not promoted']} compact>
            {rows.map((a) => (
              <tr key={a.id}>
                <td>{a.detection_type_label || detectionTypeLabel(a.detection_type)}</td>
                <td title={CONFIDENCE_TOOLTIP}>{confidencePercent(a.confidence)} ({confidenceBand(a.confidence)})</td>
                <td>{a.asset_name ?? '—'}</td>
                <td>{a.evidence_count}</td>
                <td style={{ whiteSpace: 'nowrap' }}>{relativeTime(a.first_seen_at)}</td>
                <td style={{ whiteSpace: 'nowrap' }}>{relativeTime(a.last_seen_at)}</td>
                <td className="muted" style={{ maxWidth: '22rem' }}>{a.not_promoted_reason ?? 'Below the promotion threshold.'}</td>
              </tr>
            ))}
          </TableShell>
          <Pager offset={offset} total={total} onPrev={() => setOffset(Math.max(0, offset - PAGE_SIZE))} onNext={() => setOffset(offset + PAGE_SIZE)} />
        </>
      )}
    </div>
  );
}

/* ── Telemetry tab ──────────────────────────────────────────────── */
function TelemetryTab({ authHeaders }: { authHeaders: () => Record<string, string> }) {
  const [rows, setRows] = useState<any[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [offset, setOffset] = useState(0);
  const reqId = useRef(0);

  useEffect(() => {
    const id = ++reqId.current;
    setLoading(true);
    setErr('');
    fetch(`/api/threat-monitoring/telemetry?limit=${PAGE_SIZE}&offset=${offset}`, { headers: { ...authHeaders() }, cache: 'no-store' })
      .then(async (res) => {
        if (id !== reqId.current) return;
        if (!res.ok) { setErr('Unable to load telemetry.'); return; }
        const payload = await res.json();
        setRows((payload.telemetry ?? []) as any[]);
        setTotal(Number(payload.total ?? 0));
      })
      .catch(() => { if (id === reqId.current) setErr('Telemetry is temporarily unavailable.'); })
      .finally(() => { if (id === reqId.current) setLoading(false); });
  }, [offset, authHeaders]);

  return (
    <div role="tabpanel" aria-label="Telemetry" style={{ marginTop: '0.75rem' }}>
      {loading ? (
        <p className="muted" style={{ padding: '1.5rem 0' }}>Loading telemetry…</p>
      ) : err ? (
        <p className="statusLine" role="alert" style={{ color: 'var(--danger-fg)' }}>{err}</p>
      ) : rows.length === 0 ? (
        <EmptyStateBlocker title="No telemetry events" body="No normalized telemetry has been ingested for this workspace yet." ctaHref="/monitoring-sources" ctaLabel="Check Monitoring Sources" />
      ) : (
        <>
          <TableShell headers={['Event Type', 'Asset', 'Tx Hash', 'Block', 'Source', 'Evidence Quality', 'Observed']} compact>
            {rows.map((e) => (
              <tr key={e.id}>
                <td>{e.event_type ?? '—'}</td>
                <td>{e.asset_name ?? '—'}</td>
                <td style={{ fontFamily: 'monospace', fontSize: '0.82rem' }}>{shortHex(e.tx_hash)}</td>
                <td>{e.block_number ?? '—'}</td>
                <td><StatusPill label={evidenceSourceLabel(e.evidence_source)} variant={evidenceSourceVariant(e.evidence_source)} /></td>
                <td title={EVIDENCE_QUALITY_TOOLTIP}>{evidenceQualityLabel(e.evidence_quality)}</td>
                <td style={{ whiteSpace: 'nowrap' }}>{relativeTime(e.observed_at)}</td>
              </tr>
            ))}
          </TableShell>
          <Pager offset={offset} total={total} onPrev={() => setOffset(Math.max(0, offset - PAGE_SIZE))} onNext={() => setOffset(offset + PAGE_SIZE)} />
        </>
      )}
    </div>
  );
}

/* ── Shared: filter bar + pager ─────────────────────────────────── */
function FilterBar({ severity, type, onSeverity, onType }: { severity: string; type: string; onSeverity: (v: string) => void; onType: (v: string) => void }) {
  return (
    <div style={{ display: 'flex', gap: '0.75rem', marginBottom: '0.75rem', flexWrap: 'wrap' }}>
      <label style={{ display: 'flex', flexDirection: 'column', fontSize: '0.8rem', color: 'var(--text-muted)' }}>
        Severity
        <select value={severity} onChange={(e) => onSeverity(e.target.value)} aria-label="Filter by severity" style={{ marginTop: '0.25rem' }}>
          <option value="">All</option>
          <option value="critical">Critical</option>
          <option value="high">High</option>
          <option value="medium">Medium</option>
          <option value="low">Low</option>
        </select>
      </label>
      <label style={{ display: 'flex', flexDirection: 'column', fontSize: '0.8rem', color: 'var(--text-muted)' }}>
        Type
        <select value={type} onChange={(e) => onType(e.target.value)} aria-label="Filter by detection type" style={{ marginTop: '0.25rem' }}>
          <option value="">All</option>
          <option value="unusual_transfer">Unusual Transfer</option>
          <option value="coordinated_activity">Coordinated Activity</option>
          <option value="mint_burn_irregularity">Mint/Burn Irregularity</option>
          <option value="privileged_action">Privileged/Admin Action</option>
        </select>
      </label>
    </div>
  );
}

function Pager({ offset, total, onPrev, onNext }: { offset: number; total: number; onPrev: () => void; onNext: () => void }) {
  const from = total === 0 ? 0 : offset + 1;
  const to = Math.min(offset + PAGE_SIZE, total);
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '0.75rem' }}>
      <span className="muted" style={{ fontSize: '0.85rem' }}>{from}–{to} of {total}</span>
      <div style={{ display: 'flex', gap: '0.5rem' }}>
        <button type="button" className="btn btn-secondary" disabled={offset <= 0} onClick={onPrev}>Previous</button>
        <button type="button" className="btn btn-secondary" disabled={offset + PAGE_SIZE >= total} onClick={onNext}>Next</button>
      </div>
    </div>
  );
}
