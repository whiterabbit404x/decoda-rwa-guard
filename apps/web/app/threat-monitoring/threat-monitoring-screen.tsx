'use client';

import { Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';

import { EmptyStateBlocker, Select, StatusPill, TabStrip, TableShell } from '../components/ui-primitives';
import { usePilotAuth } from '../pilot-auth-context';
import {
  ANOMALIES_TOOLTIP,
  CONFIDENCE_TOOLTIP,
  DETECTIONS_TOOLTIP,
  EVIDENCE_QUALITY_TOOLTIP,
  MTTD_TOOLTIP,
  RUN_SOURCE_DIAGNOSTIC_LABEL,
  SOURCE_DIAGNOSTIC_HREF,
  TELEMETRY_TOOLTIP,
  WINDOW_OPTIONS,
  confidenceBand,
  confidencePercent,
  confidenceVariant,
  dataFreshnessLabel,
  dataFreshnessVariant,
  degradedReasonCopy,
  detectionStatusLabel,
  detectionStatusVariant,
  detectionTypeLabel,
  emptyStateCopy,
  eventTypeLabel,
  evidenceQualityLabel,
  ingestionSourceLabel,
  evidenceSourceLabel,
  evidenceSourceVariant,
  formatMttd,
  investigateOutcomeMessage,
  nextActionLabel,
  relativeTime,
  resolveTab,
  resolveWindow,
  rowFreshnessLabel,
  rowFreshnessVariant,
  severityLabel,
  severityVariant,
  shortHex,
  trend,
  trendColor,
  windowLabel,
  type DetectionRow,
  type TabKey,
  type TelemetryRow,
  type ThreatSummary,
  type TrendMetric,
  type WindowKey,
} from './presentation';
import ThreatDetectionEngineerPanel from './threat-detection-engineer-panel';

const TABS: Array<{ key: TabKey; label: string }> = [
  { key: 'overview', label: 'Overview' },
  { key: 'telemetry', label: 'Telemetry' },
  { key: 'detections', label: 'Detections' },
  { key: 'anomalies', label: 'Anomalies' },
];

const PAGE_SIZE = 25;

export default function ThreatMonitoringScreen() {
  // useSearchParams requires a Suspense boundary; the page owns the <h1>.
  return (
    <Suspense fallback={<div className="muted" style={{ padding: '1.5rem 0' }}>Loading threat monitoring…</div>}>
      <ThreatMonitoringScreenInner />
    </Suspense>
  );
}

function ThreatMonitoringScreenInner() {
  const { authHeaders, signOut } = usePilotAuth();
  const router = useRouter();
  const searchParams = useSearchParams();

  // URL is the single source of truth for the selected tab + window, so refresh
  // restores them and browser Back/Forward navigates between them.
  const activeTab = resolveTab(searchParams.get('tab'));
  const windowKey = resolveWindow(searchParams.get('window'));

  const [summary, setSummary] = useState<ThreatSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const [investigatingId, setInvestigatingId] = useState<string | null>(null);
  const [investigateError, setInvestigateError] = useState('');

  const setUrl = useCallback((next: { tab?: TabKey; window?: WindowKey }) => {
    const params = new URLSearchParams();
    params.set('tab', next.tab ?? activeTab);
    params.set('window', next.window ?? windowKey);
    // Navigation only — reading Screen 5 never writes to the database.
    router.push(`/threat?${params.toString()}`, { scroll: false });
  }, [router, activeTab, windowKey]);

  const loadSummary = useCallback(async () => {
    setError('');
    const headers = authHeaders();
    if (!headers.Authorization) {
      setError('Your session is missing or expired. Please sign in again.');
      setLoading(false);
      return;
    }
    try {
      // GET only — opening/refreshing/switching windows never writes.
      const res = await fetch(`/api/threat-monitoring/summary?window=${windowKey}`, { headers: { ...headers }, cache: 'no-store' });
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
  }, [authHeaders, signOut, windowKey]);

  useEffect(() => {
    setLoading(true);
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
      setInvestigateError(payload.detail || investigateOutcomeMessage(payload));
    } catch {
      setInvestigateError('Unable to start the investigation. Please try again.');
    } finally {
      setInvestigatingId(null);
    }
  }, [authHeaders, router]);

  const onViewEvidence = useCallback((detectionId: string) => {
    setUrl({ tab: 'detections' });
    if (typeof window !== 'undefined') {
      window.setTimeout(() => {
        document.getElementById(`detection-${detectionId}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' });
      }, 120);
    }
  }, [setUrl]);

  const stale = summary ? summary.data_freshness !== 'fresh' : false;
  const nextAction = summary?.next_action ?? 'diagnose_ingestion';

  return (
    // Left-aligned (no auto-centering) so the header/content share the SAME left edge
    // as the page <h1> above — the title never sits isolated at the far left while the
    // subtitle starts in a centered column. maxWidth keeps ultra-wide lines readable.
    <div style={{ maxWidth: '1680px', width: '100%' }}>
      {/* Page header — the page owns the <h1>; the subtitle sits directly beneath it as
          one title block, with the window selector + Refresh aligned to the right. */}
      <header style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: '1rem', flexWrap: 'wrap', marginTop: '-0.75rem', marginBottom: '1.5rem' }}>
        <p className="muted" data-testid="threat-subtitle" style={{ margin: 0, fontSize: '0.95rem', maxWidth: '60ch', color: 'var(--text-secondary)' }}>
          Correlated telemetry, behavioral anomalies, and exploit detections.
        </p>
        <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', flexWrap: 'wrap' }}>
          <div style={{ minWidth: '180px' }}>
            <Select
              value={windowKey}
              onValueChange={(v) => setUrl({ window: v as WindowKey })}
              options={WINDOW_OPTIONS.map((o) => ({ value: o.value, label: o.label }))}
              ariaLabel="Select time window"
              testId="window-selector"
            />
          </div>
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

      {/* KPI row — labelled with the SAME selected window as every table below it. */}
      <KpiRow summary={summary} loading={loading} windowKey={windowKey} />

      {error ? (
        <p className="statusLine" style={{ color: 'var(--danger-fg)', margin: '0 0 1rem' }} role="alert">{error}</p>
      ) : null}

      <TabStrip tabs={TABS} active={activeTab} onChange={(k) => setUrl({ tab: k as TabKey })} />

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
            windowText={windowLabel(windowKey)}
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

      {activeTab === 'telemetry' ? <TelemetryTab authHeaders={authHeaders} windowKey={windowKey} /> : null}
      {activeTab === 'detections' ? (
        <DetectionsTab authHeaders={authHeaders} windowKey={windowKey} stale={stale} nextAction={nextAction} onInvestigate={onInvestigate} investigatingId={investigatingId} investigateError={investigateError} />
      ) : null}
      {activeTab === 'anomalies' ? <AnomaliesTab authHeaders={authHeaders} windowKey={windowKey} stale={stale} /> : null}
    </div>
  );
}

/* ── KPI row ─────────────────────────────────────────────────────── */
function KpiRow({ summary, loading, windowKey }: { summary: ThreatSummary | null; loading: boolean; windowKey: WindowKey }) {
  const freshness = summary?.data_freshness ?? 'unavailable';
  const windowText = windowLabel(windowKey);
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
        windowText={windowText}
        freshnessLabel={dataFreshnessLabel(freshness)}
        freshnessVariant={dataFreshnessVariant(freshness)}
      />
      <Kpi
        label="Detections"
        value={loading || !summary ? '—' : String(summary.detection_count)}
        tooltip={DETECTIONS_TOOLTIP}
        metric="detections"
        changePercent={summary?.detection_change_percent}
        windowText={windowText}
      />
      <Kpi
        label="Anomalies"
        value={loading || !summary ? '—' : String(summary.anomaly_count)}
        tooltip={ANOMALIES_TOOLTIP}
        metric="anomalies"
        changePercent={summary?.anomaly_change_percent}
        windowText={windowText}
      />
      <Kpi
        label="Mean Time to Detect"
        value={loading || !summary ? '—' : formatMttd(summary.mean_time_to_detect_seconds)}
        tooltip={MTTD_TOOLTIP}
        metric="mttd"
        changePercent={null}
        windowText={windowText}
        emptyHint={summary && summary.mean_time_to_detect_seconds === null ? 'No promoted detections to measure' : undefined}
      />
    </div>
  );
}

function Kpi({
  label, value, tooltip, metric, changePercent, windowText, freshnessLabel, freshnessVariant, emptyHint,
}: {
  label: string;
  value: string;
  tooltip: string;
  metric: TrendMetric;
  changePercent: number | null | undefined;
  windowText: string;
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
        <span data-testid={`kpi-window-${metric}`}>{windowText}</span>
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
  const emptyCopy = emptyStateCopy(summary?.empty_state_reason, {
    windowText: windowLabel(summary?.window),
    latestEverAt: summary?.last_security_telemetry_ever_at ?? null,
    stale: summary ? summary.data_freshness === 'stale' || summary.worker_status === 'stale' || summary.worker_status === 'offline' : false,
  });
  return (
    <article className="dataCard" aria-label="Telemetry Volume" style={{ minHeight: '12rem' }}>
      <p className="sectionEyebrow">Security Telemetry Volume · {windowLabel(summary?.window)}</p>
      {loading ? (
        <div className="skelBlock" style={{ height: '8rem' }} aria-hidden="true" />
      ) : buckets.length === 0 ? (
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '8rem', color: 'var(--text-muted)', gap: '0.35rem', textAlign: 'center', padding: '0 1rem' }} data-testid="chart-empty-state">
          <span style={{ fontWeight: 600, color: 'var(--text-secondary)' }}>{emptyCopy.title}</span>
          <span style={{ fontSize: '0.875rem' }}>{emptyCopy.body}</span>
          {emptyCopy.staleWarning ? <span style={{ fontSize: '0.8rem', color: 'var(--warning-fg, #f59e0b)' }}>{emptyCopy.staleWarning}</span> : null}
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
        {summary?.ingestion_health?.last_security_telemetry_at ? `Last security event ${relativeTime(summary.ingestion_health.last_security_telemetry_at)}` : 'No security events received'}
        {summary && summary.ingestion_health?.source_breakdown?.simulator ? ' · includes simulator data' : ''}
      </p>
    </article>
  );
}

/* ── Detections by type ─────────────────────────────────────────── */
function DetectionsByTypeCard({ summary, loading }: { summary: ThreatSummary | null; loading: boolean }) {
  const rows = summary?.detections_by_type ?? [];
  const unsupported = rows.filter((r) => !r.supported);
  return (
    <article className="dataCard" aria-label="Detections by Type" style={{ minHeight: '10rem' }}>
      <p className="sectionEyebrow">Detections by Type</p>
      {loading ? (
        <div className="skelBlock" style={{ height: '6rem' }} aria-hidden="true" />
      ) : (
        <>
          {/* Every canonical detection type is listed. Supported detectors show a
              real count (0 is a valid "evaluated, none found"); unsupported ones show
              "Not evaluated" so a 0 is never mistaken for an executed detector. */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', marginTop: '0.5rem' }} data-testid="detections-by-type">
            {rows.map((r) => (
              <div key={r.type} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: '0.9rem' }}>
                <span style={{ color: 'var(--text-secondary)' }}>{r.label}</span>
                {r.supported ? (
                  <span style={{ color: 'var(--text-accent)', fontWeight: 700, minWidth: '2rem', textAlign: 'right' }}>{r.count}</span>
                ) : (
                  <span className="muted" title={r.unsupported_reason ?? 'Evidence unavailable'} style={{ fontSize: '0.78rem', textAlign: 'right' }}>Not evaluated</span>
                )}
              </div>
            ))}
          </div>
          {unsupported.length > 0 ? (
            <p
              className="assessorMeta"
              data-testid="detector-capability-note"
              style={{ marginTop: '0.75rem', fontSize: '0.78rem', color: 'var(--text-muted)', lineHeight: 1.45 }}
            >
              <strong style={{ fontWeight: 600 }}>Evidence unavailable</strong> — not evaluated: {unsupported.map((r) => r.label).join(', ')}. These detectors need trace/oracle evidence this workspace does not collect.
            </p>
          ) : null}
        </>
      )}
    </article>
  );
}

/* ── Watchlist matches ──────────────────────────────────────────── */
function WatchlistCard({ summary, loading, onViewEvidence }: { summary: ThreatSummary | null; loading: boolean; onViewEvidence: (id: string) => void }) {
  const matches = summary?.active_watchlist_matches ?? [];
  // Stale coverage means an unmatched watchlist may simply be under-observed — never
  // imply that no risky addresses exist globally.
  const stale = summary ? summary.data_freshness !== 'fresh' || summary.worker_status === 'stale' || summary.worker_status === 'offline' : false;
  return (
    <article className="dataCard" aria-label="Active Watchlist Matches">
      <p className="sectionEyebrow">Active Watchlist Matches</p>
      {loading ? (
        <div className="skelBlock" style={{ height: '5rem' }} aria-hidden="true" />
      ) : matches.length === 0 ? (
        <div data-testid="watchlist-empty-state" style={{ padding: '0.5rem 0' }}>
          <p style={{ fontSize: '0.9rem', fontWeight: 600, color: 'var(--text-secondary)', margin: 0 }}>No active watchlist matches</p>
          <p className="muted" style={{ fontSize: '0.85rem', margin: '0.25rem 0 0' }}>No monitored addresses matched active watchlists during the selected period.</p>
          {stale ? (
            <p className="statusLine statusLine-warning" role="status" data-testid="watchlist-stale-warning" style={{ marginTop: '0.5rem', fontSize: '0.82rem' }}>
              Results may be incomplete until telemetry ingestion resumes.
            </p>
          ) : null}
        </div>
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
  authHeaders, windowKey, stale, nextAction, onInvestigate, investigatingId, investigateError,
}: {
  authHeaders: () => Record<string, string>;
  windowKey: WindowKey;
  stale: boolean;
  nextAction: string;
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
  const [statusValue, setStatusValue] = useState('');
  const filtersKey = `${severity}|${type}|${statusValue}|${windowKey}|${offset}`;
  const reqId = useRef(0);

  useEffect(() => {
    const id = ++reqId.current;
    setLoading(true);
    setErr('');
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset), window: windowKey });
    if (severity) params.set('severity', severity);
    if (type) params.set('detection_type', type);
    if (statusValue) params.set('status_value', statusValue);
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
      <DetectionFilterBar
        severity={severity}
        type={type}
        statusValue={statusValue}
        onSeverity={(v) => { setSeverity(v); setOffset(0); }}
        onType={(v) => { setType(v); setOffset(0); }}
        onStatus={(v) => { setStatusValue(v); setOffset(0); }}
      />
      {investigateError ? <p className="statusLine" role="alert" style={{ color: 'var(--danger-fg)' }}>{investigateError}</p> : null}
      {loading ? (
        <p className="muted" style={{ padding: '1.5rem 0' }}>Loading detections…</p>
      ) : err ? (
        <p className="statusLine" role="alert" style={{ color: 'var(--danger-fg)' }}>{err}</p>
      ) : rows.length === 0 ? (
        <StaleEmptyState
          title="No detections"
          body="No promoted threat detections match the current filters during this period."
          stale={stale}
          staleWarning="Detection coverage may be incomplete because fresh telemetry is unavailable."
          nextAction={nextAction}
        />
      ) : (
        <>
          <TableShell headers={['Detection', 'Type', 'Severity', 'Confidence', 'Asset', 'Evidence', 'First seen', 'Last seen', 'Status', 'Action']} compact>
            {rows.map((d) => (
              <tr key={d.id} id={`detection-${d.id}`}>
                <td>{d.title || (d.detection_type_label || detectionTypeLabel(d.detection_type))}</td>
                <td>{d.detection_type_label || detectionTypeLabel(d.detection_type)}</td>
                <td title="Potential impact if valid."><StatusPill label={severityLabel(d.severity)} variant={severityVariant(d.severity)} /></td>
                <td title={CONFIDENCE_TOOLTIP}><StatusPill label={`${confidencePercent(d.confidence)} (${confidenceBand(d.confidence)})`} variant={confidenceVariant(d.confidence)} /></td>
                <td>{d.asset_name ?? '—'}</td>
                <td>{d.evidence_count}</td>
                <td style={{ whiteSpace: 'nowrap' }}>{relativeTime(d.first_seen_at)}</td>
                <td style={{ whiteSpace: 'nowrap' }}>{relativeTime(d.last_seen_at)}</td>
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
function AnomaliesTab({ authHeaders, windowKey, stale }: { authHeaders: () => Record<string, string>; windowKey: WindowKey; stale: boolean }) {
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
    fetch(`/api/threat-monitoring/anomalies?limit=${PAGE_SIZE}&offset=${offset}&window=${windowKey}`, { headers: { ...authHeaders() }, cache: 'no-store' })
      .then(async (res) => {
        if (id !== reqId.current) return;
        if (!res.ok) { setErr('Unable to load anomalies.'); return; }
        const payload = await res.json();
        setRows((payload.anomalies ?? []) as any[]);
        setTotal(Number(payload.total ?? 0));
      })
      .catch(() => { if (id === reqId.current) setErr('Anomalies are temporarily unavailable.'); })
      .finally(() => { if (id === reqId.current) setLoading(false); });
  }, [offset, windowKey, authHeaders]);

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
        <StaleEmptyState
          title="No anomalies tracked"
          body="No sub-threshold deviations match the selected period."
          stale={stale}
          staleWarning="Results may be incomplete because telemetry ingestion is stale."
          nextAction="diagnose_ingestion"
        />
      ) : (
        <>
          <TableShell headers={['Type', 'Asset', 'Confidence', 'Evidence', 'First seen', 'Last seen', 'Promotion status', 'Why not promoted']} compact>
            {rows.map((a) => (
              <tr key={a.id}>
                <td>{a.detection_type_label || detectionTypeLabel(a.detection_type)}</td>
                <td>{a.asset_name ?? '—'}</td>
                <td title={CONFIDENCE_TOOLTIP}>{confidencePercent(a.confidence)} ({confidenceBand(a.confidence)})</td>
                <td>{a.evidence_count}</td>
                <td style={{ whiteSpace: 'nowrap' }}>{relativeTime(a.first_seen_at)}</td>
                <td style={{ whiteSpace: 'nowrap' }}>{relativeTime(a.last_seen_at)}</td>
                <td><StatusPill label="Not promoted" variant="neutral" /></td>
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
function TelemetryTab({ authHeaders, windowKey }: { authHeaders: () => Record<string, string>; windowKey: WindowKey }) {
  const [rows, setRows] = useState<TelemetryRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [offset, setOffset] = useState(0);
  const [eventType, setEventType] = useState('');
  const [source, setSource] = useState('');
  const [freshness, setFreshness] = useState('');
  const [sortDesc, setSortDesc] = useState(true);
  const [copied, setCopied] = useState<string | null>(null);
  const filtersKey = `${eventType}|${source}|${freshness}|${windowKey}|${offset}`;
  const reqId = useRef(0);

  useEffect(() => {
    const id = ++reqId.current;
    setLoading(true);
    setErr('');
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset), window: windowKey, category: 'security' });
    if (eventType) params.set('event_type', eventType);
    if (source) params.set('evidence_source', source);
    if (freshness) params.set('freshness', freshness);
    fetch(`/api/threat-monitoring/telemetry?${params.toString()}`, { headers: { ...authHeaders() }, cache: 'no-store' })
      .then(async (res) => {
        if (id !== reqId.current) return;
        if (!res.ok) { setErr('Unable to load telemetry.'); return; }
        const payload = await res.json();
        setRows((payload.telemetry ?? []) as TelemetryRow[]);
        setTotal(Number(payload.total ?? 0));
      })
      .catch(() => { if (id === reqId.current) setErr('Telemetry is temporarily unavailable.'); })
      .finally(() => { if (id === reqId.current) setLoading(false); });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filtersKey, authHeaders]);

  const sorted = [...rows].sort((a, b) => {
    const ta = a.observed_at ? new Date(a.observed_at).getTime() : 0;
    const tb = b.observed_at ? new Date(b.observed_at).getTime() : 0;
    return sortDesc ? tb - ta : ta - tb;
  });

  const copyHash = useCallback((hash: string) => {
    if (typeof navigator !== 'undefined' && navigator.clipboard) {
      void navigator.clipboard.writeText(hash).then(() => {
        setCopied(hash);
        window.setTimeout(() => setCopied((c) => (c === hash ? null : c)), 1200);
      }).catch(() => undefined);
    }
  }, []);

  return (
    <div role="tabpanel" aria-label="Telemetry" style={{ marginTop: '0.75rem' }}>
      <TelemetryFilterBar
        eventType={eventType}
        source={source}
        freshness={freshness}
        onEventType={(v) => { setEventType(v); setOffset(0); }}
        onSource={(v) => { setSource(v); setOffset(0); }}
        onFreshness={(v) => { setFreshness(v); setOffset(0); }}
      />
      <p className="muted" style={{ fontSize: '0.8rem', marginBottom: '0.5rem' }}>
        Canonical on-chain security telemetry for the selected window. Ingestion heartbeats (RPC polling, provider checks) are shown under Monitoring Sources, not here. Times are your local timezone.
      </p>
      {loading ? (
        <p className="muted" style={{ padding: '1.5rem 0' }}>Loading telemetry…</p>
      ) : err ? (
        <p className="statusLine" role="alert" style={{ color: 'var(--danger-fg)' }}>{err}</p>
      ) : sorted.length === 0 ? (
        <EmptyStateBlocker title="No security telemetry" body="No canonical on-chain security telemetry has been ingested for this workspace in the selected window." ctaHref={SOURCE_DIAGNOSTIC_HREF} ctaLabel="Check Monitoring Sources" />
      ) : (
        <>
          <TableShell headers={['Event Type', 'Asset', 'Transaction', 'Block', 'Ingestion Source', 'Evidence Quality', 'Freshness', 'Observed At']} compact>
            {sorted.map((e) => (
              <tr key={e.id}>
                <td>{eventTypeLabel(e.event_type)}</td>
                <td>{e.asset_name ?? '—'}</td>
                <td>
                  {e.tx_hash ? (
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '0.35rem' }}>
                      <span style={{ fontFamily: 'monospace', fontSize: '0.82rem' }} title={e.tx_hash}>{shortHex(e.tx_hash)}</span>
                      <button
                        type="button"
                        className="btn btn-secondary"
                        aria-label="Copy transaction hash"
                        title="Copy transaction hash"
                        style={{ fontSize: '0.7rem', padding: '0.1rem 0.35rem', lineHeight: 1.2 }}
                        onClick={() => copyHash(e.tx_hash as string)}
                      >
                        {copied === e.tx_hash ? '✓' : 'Copy'}
                      </button>
                    </span>
                  ) : '—'}
                </td>
                <td>{e.block_number ?? '—'}</td>
                <td>{ingestionSourceLabel(e.ingestion_source)}</td>
                <td title={EVIDENCE_QUALITY_TOOLTIP}>{evidenceQualityLabel(e.evidence_quality)}</td>
                <td>
                  <span style={{ display: 'inline-flex', gap: '0.3rem', alignItems: 'center' }}>
                    <StatusPill label={evidenceSourceLabel(e.evidence_mode ?? e.evidence_source)} variant={evidenceSourceVariant(e.evidence_mode ?? e.evidence_source)} />
                    <StatusPill label={rowFreshnessLabel(e.freshness)} variant={rowFreshnessVariant(e.freshness)} />
                  </span>
                </td>
                <td style={{ whiteSpace: 'nowrap' }} title={e.observed_at ?? ''}>
                  <button type="button" className="btnLink" onClick={() => setSortDesc((s) => !s)} title="Sort by observed time" style={{ background: 'none', border: 'none', color: 'inherit', cursor: 'pointer', padding: 0 }}>
                    {relativeTime(e.observed_at)}
                  </button>
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

/* ── Shared: stale empty state ──────────────────────────────────── */
function StaleEmptyState({ title, body, stale, staleWarning, nextAction }: { title: string; body: string; stale: boolean; staleWarning: string; nextAction: string }) {
  // A stale state always recommends restoring ingestion (Run Source Diagnostic),
  // derived from the canonical next action rather than invented per-tab.
  const showDiagnostic = stale || nextAction === 'diagnose_ingestion';
  return (
    <div className="emptyStatePanel sharedEmptyStateBlocker" data-testid="stale-empty-state">
      <h4>{title}</h4>
      <p className="muted">{body}</p>
      {stale ? (
        <p className="statusLine statusLine-warning" role="status" data-testid="empty-stale-warning" style={{ marginTop: '0.6rem' }}>{staleWarning}</p>
      ) : null}
      {showDiagnostic ? (
        <a href={SOURCE_DIAGNOSTIC_HREF} className="btn btn-secondary" data-testid="run-source-diagnostic" style={{ marginTop: '0.75rem', display: 'inline-block' }}>
          {nextActionLabel('diagnose_ingestion') || RUN_SOURCE_DIAGNOSTIC_LABEL}
        </a>
      ) : null}
    </div>
  );
}

/* ── Shared: filter bars + pager ────────────────────────────────── */
function DetectionFilterBar({ severity, type, statusValue, onSeverity, onType, onStatus }: {
  severity: string; type: string; statusValue: string;
  onSeverity: (v: string) => void; onType: (v: string) => void; onStatus: (v: string) => void;
}) {
  return (
    <div style={{ display: 'flex', gap: '0.75rem', marginBottom: '0.75rem', flexWrap: 'wrap' }}>
      <FilterField label="Severity">
        <Select
          value={severity}
          onValueChange={onSeverity}
          ariaLabel="Filter by severity"
          options={[
            { value: '', label: 'All severities' },
            { value: 'critical', label: 'Critical' },
            { value: 'high', label: 'High' },
            { value: 'medium', label: 'Medium' },
            { value: 'low', label: 'Low' },
          ]}
        />
      </FilterField>
      <FilterField label="Type">
        <Select
          value={type}
          onValueChange={onType}
          ariaLabel="Filter by detection type"
          options={[
            { value: '', label: 'All types' },
            { value: 'unusual_transfer', label: 'Unusual Transfer' },
            { value: 'coordinated_activity', label: 'Coordinated Activity' },
            { value: 'mint_burn_irregularity', label: 'Mint/Burn Irregularity' },
            { value: 'privileged_action', label: 'Privileged/Admin Action' },
          ]}
        />
      </FilterField>
      <FilterField label="Status">
        <Select
          value={statusValue}
          onValueChange={onStatus}
          ariaLabel="Filter by investigation status"
          options={[
            { value: '', label: 'All statuses' },
            { value: 'open', label: 'Open' },
            { value: 'investigating', label: 'Investigating' },
            { value: 'resolved', label: 'Resolved' },
            { value: 'dismissed', label: 'Dismissed' },
          ]}
        />
      </FilterField>
    </div>
  );
}

function TelemetryFilterBar({ eventType, source, freshness, onEventType, onSource, onFreshness }: {
  eventType: string; source: string; freshness: string;
  onEventType: (v: string) => void; onSource: (v: string) => void; onFreshness: (v: string) => void;
}) {
  return (
    <div style={{ display: 'flex', gap: '0.75rem', marginBottom: '0.75rem', flexWrap: 'wrap' }}>
      <FilterField label="Event type">
        <Select
          value={eventType}
          onValueChange={onEventType}
          ariaLabel="Filter by event type"
          options={[
            { value: '', label: 'All event types' },
            { value: 'native_transfer', label: 'Native Transfer' },
            { value: 'wallet_transfer_detected', label: 'Wallet Transfer Detected' },
            { value: 'erc20_transfer', label: 'Token Transfer' },
            { value: 'ownership_transferred', label: 'Ownership Transferred' },
            { value: 'role_granted', label: 'Role Granted' },
          ]}
        />
      </FilterField>
      <FilterField label="Evidence mode">
        <Select
          value={source}
          onValueChange={onSource}
          ariaLabel="Filter by evidence mode"
          options={[
            { value: '', label: 'All modes' },
            { value: 'live', label: 'Live' },
            { value: 'simulator', label: 'Simulator' },
            { value: 'replay', label: 'Replay' },
          ]}
        />
      </FilterField>
      <FilterField label="Freshness">
        <Select
          value={freshness}
          onValueChange={onFreshness}
          ariaLabel="Filter by freshness"
          options={[
            { value: '', label: 'Any freshness' },
            { value: 'fresh', label: 'Fresh' },
            { value: 'stale', label: 'Stale' },
          ]}
        />
      </FilterField>
    </div>
  );
}

function FilterField({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={{ display: 'flex', flexDirection: 'column', fontSize: '0.8rem', color: 'var(--text-muted)', minWidth: '160px' }}>
      {label}
      <span style={{ marginTop: '0.25rem' }}>{children}</span>
    </label>
  );
}

function Pager({ offset, total, onPrev, onNext }: { offset: number; total: number; onPrev: () => void; onNext: () => void }) {
  const from = total === 0 ? 0 : offset + 1;
  const to = Math.min(offset + PAGE_SIZE, total);
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '0.75rem' }}>
      <span className="muted" style={{ fontSize: '0.85rem' }} data-testid="pager-total">{from}–{to} of {total}</span>
      <div style={{ display: 'flex', gap: '0.5rem' }}>
        <button type="button" className="btn btn-secondary" disabled={offset <= 0} onClick={onPrev}>Previous</button>
        <button type="button" className="btn btn-secondary" disabled={offset + PAGE_SIZE >= total} onClick={onNext}>Next</button>
      </div>
    </div>
  );
}
