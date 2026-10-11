'use client';

import { Suspense, useCallback, useEffect, useRef, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';

import { EmptyStateBlocker, Select, StatusPill, TabStrip, TableShell } from '../components/ui-primitives';
import { followDecodaRefusal } from '../decoda-identity-shared';
import { usePilotAuth } from '../pilot-auth-context';
import { useRuntimeSummary } from '../runtime-summary-context';
import {
  coverageIncompleteWarningApplies,
  coverageNotice,
  liveCoverageIsCurrent,
  telemetryFreshnessPill,
} from './live-coverage-copy';
import {
  ANOMALIES_TOOLTIP,
  CONFIDENCE_TOOLTIP,
  DETECTIONS_TOOLTIP,
  EVIDENCE_QUALITY_TOOLTIP,
  MTTD_TOOLTIP,
  RUN_SOURCE_DIAGNOSTIC_LABEL,
  SEVERITY_TOOLTIP,
  SOURCE_DIAGNOSTIC_HREF,
  TELEMETRY_TOOLTIP,
  WINDOW_OPTIONS,
  confidenceBand,
  confidencePercent,
  confidenceVariant,
  dataFreshnessLabel,
  dataFreshnessVariant,
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
  metricCount,
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
import DetectionDetailPanels from './detection-detail-panels';
import {
  CATEGORY_OPERATIONAL_INTEGRITY,
  EMPTY_STATE_BODY,
  categoryLabel,
  categoryOptions,
  coverageNotice as operationalCoverageNotice,
  coverageSourceLine,
  formatAmount,
  isOperationalIntegrity,
  reasonCodeLabel,
} from './operational-integrity';
import ThreatDetectionEngineerPanel from './threat-detection-engineer-panel';

const TABS: Array<{ key: TabKey; label: string }> = [
  { key: 'overview', label: 'Overview' },
  { key: 'telemetry', label: 'Telemetry' },
  { key: 'detections', label: 'Detections' },
  { key: 'anomalies', label: 'Anomalies' },
];

const PAGE_SIZE = 25;

/* ── Icons ──────────────────────────────────────────────────────────
   Small stroke glyphs that inherit currentColor, so every icon takes its
   colour from the surrounding token (never a literal). Decorative only. */
type TmIconName = 'refresh' | 'alert' | 'info' | 'search' | 'activity' | 'target' | 'wave' | 'clock';

const TM_ICON_PATHS: Record<TmIconName, string> = {
  refresh: 'M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7',
  alert: 'M12 4 2.5 20h19L12 4Zm0 6v4m0 3.2v.3',
  info: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-10v6m0-9.2v.3',
  search: 'M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14Zm5-2 4.5 4.5',
  activity: 'M3 12h4l3-7 4 14 3-7h4',
  target: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-5a4 4 0 1 0 0-8 4 4 0 0 0 0 8Zm0-4h.01',
  wave: 'M2 12c2.5 0 2.5-5 5-5s2.5 10 5 10 2.5-10 5-10 2.5 5 5 5',
  clock: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18Zm0-13v4.5l3 2',
};

const KPI_ICONS: Record<TrendMetric, TmIconName> = {
  telemetry: 'activity',
  detections: 'target',
  anomalies: 'wave',
  mttd: 'clock',
};

function TmIcon({ name }: { name: TmIconName }) {
  return (
    <svg className="tmIcon" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      <path d={TM_ICON_PATHS[name]} />
    </svg>
  );
}

export default function ThreatMonitoringScreen() {
  // useSearchParams requires a Suspense boundary; the page owns the <h1>.
  return (
    <Suspense fallback={<div className="muted tmLoadingLine" role="status">Loading threat monitoring…</div>}>
      <ThreatMonitoringScreenInner />
    </Suspense>
  );
}

function ThreatMonitoringScreenInner() {
  const { authHeaders, signOut } = usePilotAuth();
  const { summary: runtimeSummary } = useRuntimeSummary();
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
    // No token check here: the session is the HttpOnly cookie, attached by the
    // server. A missing or expired session is the 401 handled below.
    const headers = authHeaders();
    try {
      // GET only — opening/refreshing/switching windows never writes.
      const res = await fetch(`/api/threat-monitoring/summary?window=${windowKey}`, { headers: { ...headers }, cache: 'no-store' });
      if (res.status === 401 || res.status === 403) {
        if (await followDecodaRefusal(res)) return;
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

  // Canonical live-coverage verdict from /ops/monitoring/runtime-status. The threat
  // summary's own data_freshness only measures how long ago the last matched SECURITY
  // event happened; it is not a statement about whether monitoring coverage is current.
  const liveCoverageFresh = liveCoverageIsCurrent(runtimeSummary.realtime_ingestion);
  const securityEventsStale = summary ? summary.data_freshness !== 'fresh' : false;
  // Only propagate "results may be incomplete" downstream when coverage really is
  // not current — a quiet wallet under fresh live coverage has complete results.
  const stale = coverageIncompleteWarningApplies(securityEventsStale, liveCoverageFresh);
  const nextAction = summary?.next_action ?? 'diagnose_ingestion';
  const notice = summary ? coverageNotice(summary.degraded_reasons, liveCoverageFresh) : null;

  return (
    // Left-aligned (no auto-centering) so the header/content share the SAME left edge
    // as the page <h1> above — the title never sits isolated at the far left while the
    // subtitle starts in a centered column. maxWidth keeps ultra-wide lines readable.
    <div className="tmScreen" style={{ maxWidth: '1680px', width: '100%' }}>
      {/* Page header — the page owns the <h1>; the subtitle sits directly beneath it as
          one title block, with the window selector + Refresh aligned to the right. */}
      <header className="tmHeader">
        <p className="muted tmSubtitle" data-testid="threat-subtitle">
          Correlated telemetry, behavioral anomalies, and exploit detections.
        </p>
        <div className="tmHeaderControls">
          <div className="tmWindowSelect">
            <Select
              value={windowKey}
              onValueChange={(v) => setUrl({ window: v as WindowKey })}
              options={WINDOW_OPTIONS.map((o) => ({ value: o.value, label: o.label }))}
              ariaLabel="Select time window"
              testId="window-selector"
            />
          </div>
          <button type="button" className="btn btn-secondary tmRefreshBtn" aria-busy={loading} onClick={() => { setLoading(true); void loadSummary(); }}>
            <TmIcon name="refresh" />
            Refresh
          </button>
        </div>
      </header>

      {notice ? (
        <div
          className={notice.tone === 'warning' ? 'statusLine statusLine-warning tmNotice' : 'statusLine tmNotice'}
          role="status"
          data-testid="degraded-banner"
          data-tone={notice.tone}
        >
          <TmIcon name={notice.tone === 'warning' ? 'alert' : 'info'} />
          <span>{notice.text}</span>
        </div>
      ) : null}

      {/* KPI row — labelled with the SAME selected window as every table below it. */}
      <KpiRow summary={summary} loading={loading} windowKey={windowKey} liveCoverageFresh={liveCoverageFresh} />

      {error ? (
        <p className="statusLine tmError" role="alert">{error}</p>
      ) : null}

      <TabStrip tabs={TABS} active={activeTab} onChange={(k) => setUrl({ tab: k as TabKey })} />

      {activeTab === 'overview' ? (
        <div role="tabpanel" aria-label="Overview" className="tmTabPanel tmOverviewGrid" style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 2fr) minmax(0, 1fr)', gap: '1.5rem', marginTop: '0.5rem' }}>
          <div className="tmOverviewMain">
            <TelemetryVolumeCard summary={summary} loading={loading} liveCoverageFresh={liveCoverageFresh} />
            <DetectionsByTypeCard summary={summary} loading={loading} />
            <WatchlistCard summary={summary} loading={loading} onViewEvidence={onViewEvidence} liveCoverageFresh={liveCoverageFresh} />
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
        <DetectionsTab authHeaders={authHeaders} windowKey={windowKey} stale={stale} nextAction={nextAction} onInvestigate={onInvestigate} investigatingId={investigatingId} investigateError={investigateError} summary={summary} />
      ) : null}
      {activeTab === 'anomalies' ? <AnomaliesTab authHeaders={authHeaders} windowKey={windowKey} stale={stale} /> : null}
    </div>
  );
}

/* ── KPI row ─────────────────────────────────────────────────────── */
function KpiRow({ summary, loading, windowKey, liveCoverageFresh }: { summary: ThreatSummary | null; loading: boolean; windowKey: WindowKey; liveCoverageFresh: boolean }) {
  const freshness = summary?.data_freshness ?? 'unavailable';
  const windowText = windowLabel(windowKey);
  // The card keeps showing the historical security-event age; the pill must not turn
  // that age into "monitoring is stale" while live coverage is proven current.
  const freshnessPill = telemetryFreshnessPill(freshness, liveCoverageFresh, {
    label: dataFreshnessLabel(freshness),
    variant: dataFreshnessVariant(freshness),
  });
  return (
    <div className="tmKpiRow" data-testid="kpi-row">
      <Kpi
        label="Telemetry Events"
        value={loading || !summary ? '—' : metricCount(summary.telemetry_events_count)}
        tooltip={TELEMETRY_TOOLTIP}
        metric="telemetry"
        changePercent={summary?.telemetry_change_percent}
        windowText={windowText}
        freshnessLabel={freshnessPill.label}
        freshnessVariant={freshnessPill.variant}
      />
      <Kpi
        label="Detections"
        value={loading || !summary ? '—' : metricCount(summary.detection_count)}
        tooltip={DETECTIONS_TOOLTIP}
        metric="detections"
        changePercent={summary?.detection_change_percent}
        windowText={windowText}
      />
      <Kpi
        label="Anomalies"
        value={loading || !summary ? '—' : metricCount(summary.anomaly_count)}
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
    <article className="metricCard sharedMetricTile tmKpi" data-testid={`kpi-${metric}`}>
      <div className="tmKpiHead">
        <p className="metricLabel tmKpiLabel" title={tooltip}>{label}</p>
        <span className="tmKpiIcon" aria-hidden="true"><TmIcon name={KPI_ICONS[metric]} /></span>
      </div>
      <p className="metricValue">{value}</p>
      <p className="metricMeta tmKpiMeta">
        <span data-testid={`kpi-window-${metric}`}>{windowText}</span>
        {t ? (
          // Tone comes from the canonical trend() helper (an increase in detections is
          // "bad", telemetry volume is informational); trendColor() stays the colour source.
          <span data-testid={`trend-${metric}`} className="tmTrend" data-tone={t.tone} style={{ color: trendColor(t.tone) }}>
            {t.arrow} {t.label}
          </span>
        ) : null}
        {freshnessLabel ? <StatusPill label={freshnessLabel} variant={freshnessVariant ?? 'neutral'} /> : null}
        {emptyHint ? <span className="muted tmKpiHint">{emptyHint}</span> : null}
      </p>
    </article>
  );
}

/* ── Telemetry volume chart ─────────────────────────────────────── */
function TelemetryVolumeCard({ summary, loading, liveCoverageFresh }: { summary: ThreatSummary | null; loading: boolean; liveCoverageFresh: boolean }) {
  const buckets = summary?.telemetry_volume_buckets ?? [];
  const max = buckets.reduce((m, b) => Math.max(m, b.count), 0);
  const ingestionStale = summary ? summary.data_freshness === 'stale' || summary.worker_status === 'stale' || summary.worker_status === 'offline' : false;
  const emptyCopy = emptyStateCopy(summary?.empty_state_reason, {
    windowText: windowLabel(summary?.window),
    latestEverAt: summary?.last_security_telemetry_ever_at ?? null,
    stale: coverageIncompleteWarningApplies(ingestionStale, liveCoverageFresh),
  });
  // Bars are stacked from the two counts the backend returns per bucket: the live
  // share and whatever is left (simulator / replay). Non-live volume is drawn with a
  // distinct hatched fill and named in the legend, so it can never pass as live.
  const anyNonLive = buckets.some((b) => b.count > b.live_count);
  const firstBucket = buckets[0];
  const lastBucket = buckets[buckets.length - 1];
  return (
    <article className="dataCard tmCard tmVolumeCard" aria-label="Telemetry Volume">
      <div className="tmCardHead">
        <p className="sectionEyebrow">Security Telemetry Volume · {windowLabel(summary?.window)}</p>
        {!loading && buckets.length > 0 ? (
          <ul className="tmLegend" aria-label="Chart legend">
            <li><span className="tmLegendSwatch" data-series="live" aria-hidden="true" />Live</li>
            {anyNonLive ? <li><span className="tmLegendSwatch" data-series="non-live" aria-hidden="true" />Non-live (e.g. simulator, replay)</li> : null}
          </ul>
        ) : null}
      </div>
      {loading ? (
        <div className="skelBlock tmChartSkeleton" aria-hidden="true" />
      ) : !summary ? (
        <SummaryUnavailable section="telemetry volume" />
      ) : buckets.length === 0 ? (
        <div className="tmChartEmpty" data-testid="chart-empty-state">
          <span className="tmChartEmptyTitle">{emptyCopy.title}</span>
          <span className="tmChartEmptyBody">{emptyCopy.body}</span>
          {emptyCopy.staleWarning ? <span className="tmChartEmptyWarning" role="status">{emptyCopy.staleWarning}</span> : null}
        </div>
      ) : (
        <div className="tmChart">
          <div className="tmChartAxisY" aria-hidden="true">
            <span>{max}</span>
            <span>0</span>
          </div>
          <div className="tmChartPlot">
            <div
              className="tmBars"
              data-testid="telemetry-volume-bars"
              role="img"
              aria-label={`Security telemetry volume across ${buckets.length} intervals; peak ${max} events in one interval${anyNonLive ? '; includes non-live events' : ''}.`}
            >
              {buckets.map((b) => {
                const h = max > 0 ? Math.max(4, Math.round((b.count / max) * 100)) : 4;
                const nonLive = b.count > b.live_count;
                const nonLiveShare = nonLive && b.count > 0 ? Math.round(((b.count - b.live_count) / b.count) * 100) : 0;
                return (
                  <div
                    key={b.bucket_epoch}
                    className="tmBarCol"
                    title={`${new Date(b.bucket_epoch * 1000).toLocaleString()} · ${b.count} events${nonLive ? ` (${b.count - b.live_count} non-live)` : ''}`}
                  >
                    <div className="tmBar" data-empty={b.count === 0 ? 'true' : undefined} style={{ height: `${h}%` }}>
                      {nonLive ? <span className="tmBarNonLive" style={{ height: `${nonLiveShare}%` }} /> : null}
                    </div>
                  </div>
                );
              })}
            </div>
            {firstBucket && lastBucket ? (
              <div className="tmChartAxisX" aria-hidden="true">
                <span>{bucketTick(firstBucket.bucket_epoch)}</span>
                <span>{bucketTick(lastBucket.bucket_epoch)}</span>
              </div>
            ) : null}
          </div>
        </div>
      )}
      <p className="muted tmCardFoot">
        {/* Without a summary there is no fact to state: never "No security events received". */}
        {!summary ? (loading ? '' : 'Last security event: unavailable') : summary.ingestion_health?.last_security_telemetry_at ? `Last security event ${relativeTime(summary.ingestion_health.last_security_telemetry_at)}` : 'No security events received'}
        {summary && summary.ingestion_health?.source_breakdown?.simulator ? ' · includes simulator data' : ''}
      </p>
    </article>
  );
}

/** Axis tick for a bucket start: short date + time in the viewer's locale. */
function bucketTick(epochSeconds: number): string {
  const d = new Date(epochSeconds * 1000);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

/* ── Detections by type ─────────────────────────────────────────── */
function DetectionsByTypeCard({ summary, loading }: { summary: ThreatSummary | null; loading: boolean }) {
  const rows = summary?.detections_by_type ?? [];
  const unsupported = rows.filter((r) => !r.supported);
  // Bar lengths are relative to the largest real count in this window; with no
  // detections every track stays empty rather than drawing a placeholder.
  const maxCount = rows.reduce((m, r) => (r.supported ? Math.max(m, r.count) : m), 0);
  return (
    <article className="dataCard tmCard" aria-label="Detections by Type">
      <p className="sectionEyebrow">Detections by Type</p>
      {loading ? (
        <div className="skelBlock tmTypeSkeleton" aria-hidden="true" />
      ) : !summary ? (
        <SummaryUnavailable section="detector results" />
      ) : (
        <>
          {/* Every canonical detection type is listed. Supported detectors show a
              real count (0 is a valid "evaluated, none found"); unsupported ones show
              "Not evaluated" so a 0 is never mistaken for an executed detector. */}
          <div className="tmTypeList" data-testid="detections-by-type">
            {rows.map((r) => (
              <div key={r.type} className="tmTypeRow" data-supported={r.supported ? 'true' : 'false'}>
                <span className="tmTypeLabel">{r.label}</span>
                <span className="tmTypeTrack" aria-hidden="true">
                  {r.supported && r.count > 0 && maxCount > 0 ? (
                    <span className="tmTypeFill" style={{ width: `${Math.max(3, Math.round((r.count / maxCount) * 100))}%` }} />
                  ) : null}
                </span>
                {r.supported ? (
                  <span className="tmTypeCount">{r.count}</span>
                ) : (
                  <span className="tmTypeUnsupported" title={r.unsupported_reason ?? 'Evidence unavailable'}>Not evaluated</span>
                )}
              </div>
            ))}
          </div>
          {rows.length === 0 ? (
            <p className="muted tmDetailNote" data-testid="detections-by-type-empty">No detector results were reported for this window.</p>
          ) : null}
          {unsupported.length > 0 ? (
            <p
              className="assessorMeta tmCapabilityNote"
              data-testid="detector-capability-note"
            >
              <strong>Evidence unavailable</strong> — not evaluated: {unsupported.map((r) => r.label).join(', ')}. These detectors need trace/oracle evidence this workspace does not collect.
            </p>
          ) : null}
        </>
      )}
    </article>
  );
}

/* ── Watchlist matches ──────────────────────────────────────────── */
function WatchlistCard({ summary, loading, onViewEvidence, liveCoverageFresh }: { summary: ThreatSummary | null; loading: boolean; onViewEvidence: (id: string) => void; liveCoverageFresh: boolean }) {
  const matches = summary?.active_watchlist_matches ?? [];
  // Stale coverage means an unmatched watchlist may simply be under-observed — never
  // imply that no risky addresses exist globally. An old last-security-event age is
  // NOT stale coverage: under a proven-current live verdict the window was observed.
  const coverageStale = summary ? summary.data_freshness !== 'fresh' || summary.worker_status === 'stale' || summary.worker_status === 'offline' : false;
  const stale = coverageIncompleteWarningApplies(coverageStale, liveCoverageFresh);
  return (
    <article className="dataCard tmCard" aria-label="Active Watchlist Matches">
      <p className="sectionEyebrow">Active Watchlist Matches</p>
      {loading ? (
        <div className="skelBlock tmWatchSkeleton" aria-hidden="true" />
      ) : !summary ? (
        <SummaryUnavailable section="watchlist matches" />
      ) : matches.length === 0 ? (
        <div data-testid="watchlist-empty-state" className="tmInlineEmpty">
          <p className="tmInlineEmptyTitle">No active watchlist matches</p>
          <p className="muted tmInlineEmptyBody">No monitored addresses matched active watchlists during the selected period.</p>
          {stale ? (
            <p className="statusLine statusLine-warning tmInlineWarning" role="status" data-testid="watchlist-stale-warning">
              Results may be incomplete until telemetry ingestion resumes.
            </p>
          ) : null}
        </div>
      ) : (
        <TableShell headers={['Actor', 'Asset', 'Reason', 'Confidence', 'First seen', 'Last seen', 'Status', 'Evidence']} compact className="tmTable tmWatchTable">
          {matches.map((m) => (
            <tr key={`${m.detection_id}-${m.actor_address}`}>
              <td className="tmMono" title={m.actor_address ?? undefined}>{shortHex(m.actor_address)}</td>
              <td>{m.asset_name ?? '—'}</td>
              <td className="tmReasonCell">{m.match_reason ?? '—'}</td>
              <td title={CONFIDENCE_TOOLTIP} className="tmNum">{confidencePercent(m.confidence)}</td>
              <td className="tmNowrap">{relativeTime(m.first_seen_at)}</td>
              <td className="tmNowrap">{relativeTime(m.last_seen_at)}</td>
              <td><StatusPill label={detectionStatusLabel(m.status)} variant={detectionStatusVariant(m.status)} /></td>
              <td>
                <button type="button" className="btn btn-secondary tmBtnSm" onClick={() => onViewEvidence(m.detection_id)}>
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
  authHeaders, windowKey, stale, nextAction, onInvestigate, investigatingId, investigateError, summary,
}: {
  authHeaders: () => Record<string, string>;
  windowKey: WindowKey;
  stale: boolean;
  nextAction: string;
  onInvestigate: (id: string) => void;
  investigatingId: string | null;
  investigateError: string;
  summary: ThreatSummary | null;
}) {
  const [rows, setRows] = useState<DetectionRow[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState('');
  const [offset, setOffset] = useState(0);
  const [severity, setSeverity] = useState('');
  const [type, setType] = useState('');
  const [statusValue, setStatusValue] = useState('');
  // Category is a first-class filter over a stored backend column, not a
  // frontend label: selecting it narrows real detection records.
  const [category, setCategory] = useState('');
  const [searchInput, setSearchInput] = useState('');
  const [search, setSearch] = useState('');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const filtersKey = `${severity}|${type}|${statusValue}|${category}|${search}|${windowKey}|${offset}`;
  const reqId = useRef(0);

  // Debounced search — one request per pause in typing, never per keystroke.
  useEffect(() => {
    const handle = window.setTimeout(() => { setSearch(searchInput.trim()); setOffset(0); }, 350);
    return () => window.clearTimeout(handle);
  }, [searchInput]);

  useEffect(() => {
    const id = ++reqId.current;
    setLoading(true);
    setErr('');
    const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(offset), window: windowKey });
    if (severity) params.set('severity', severity);
    if (type) params.set('detection_type', type);
    if (statusValue) params.set('status_value', statusValue);
    if (category) params.set('category', category);
    if (search) params.set('search', search);
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

  // A row that is no longer in the result set must not keep a detail panel open.
  useEffect(() => {
    if (selectedId && !rows.some((r) => r.id === selectedId)) setSelectedId(null);
  }, [rows, selectedId]);

  const operationalSelected = category === CATEGORY_OPERATIONAL_INTEGRITY;
  const operational = summary?.operational_integrity ?? null;
  const operationalNotice = operationalSelected ? operationalCoverageNotice(operational?.coverage ?? null) : null;
  // Observed/Expected only carry meaning for the operational lane; the columns
  // appear when that lane is in view rather than adding two permanently empty columns to
  // every cyber detection.
  const showAmounts = operationalSelected || rows.some((r) => isOperationalIntegrity(r.category));

  return (
    <div role="tabpanel" aria-label="Detections" className="tmTabPanel">
      <DetectionFilterBar
        severity={severity}
        type={type}
        statusValue={statusValue}
        category={category}
        categories={categoryOptions(summary?.detection_categories)}
        search={searchInput}
        onSeverity={(v) => { setSeverity(v); setOffset(0); }}
        onType={(v) => { setType(v); setOffset(0); }}
        onStatus={(v) => { setStatusValue(v); setOffset(0); }}
        onCategory={(v) => { setCategory(v); setType(''); setOffset(0); setSelectedId(null); }}
        onSearch={setSearchInput}
      />

      {operationalSelected ? (
        <OperationalCoverageStrip notice={operationalNotice} operational={operational} />
      ) : null}

      {investigateError ? <p className="statusLine tmError" role="alert">{investigateError}</p> : null}
      {loading ? (
        <p className="muted tmLoadingLine" role="status">Loading detections…</p>
      ) : err ? (
        <p className="statusLine tmError" role="alert">{err}</p>
      ) : rows.length === 0 ? (
        <StaleEmptyState
          title={operationalSelected ? 'No operational integrity detections' : 'No detections'}
          body={operationalSelected ? EMPTY_STATE_BODY : 'No promoted threat detections match the current filters during this period.'}
          stale={stale}
          staleWarning="Detection coverage may be incomplete because fresh telemetry is unavailable."
          nextAction={nextAction}
        />
      ) : (
        <>
          <TableShell
            headers={[
              'Severity', 'Detection', 'Asset',
              ...(showAmounts ? ['Observed', 'Expected'] : []),
              'Status', 'Confidence', 'First seen', 'Action',
            ]}
            compact
            className="tmTable tmDetectionTable"
          >
            {rows.map((d) => {
              const operationalRow = isOperationalIntegrity(d.category);
              const selected = selectedId === d.id;
              return (
                <tr
                  key={d.id}
                  id={`detection-${d.id}`}
                  data-testid={`detection-row-${d.id}`}
                  data-category={String(d.category ?? '')}
                  aria-selected={selected}
                  // Severity tone comes from the canonical severityVariant() helper; it
                  // only drives the row's edge marker, the pill still spells the level.
                  data-severity-tone={severityVariant(d.severity)}
                  className="tmDetectionRow"
                  tabIndex={0}
                  onClick={() => setSelectedId(selected ? null : d.id)}
                  onKeyDown={(e) => {
                    // Keyboard parity with the click: Enter/Space on the row itself (not on
                    // the Investigate button inside it) opens or closes the details.
                    if (e.target !== e.currentTarget) return;
                    if (e.key === 'Enter' || e.key === ' ') {
                      e.preventDefault();
                      setSelectedId(selected ? null : d.id);
                    }
                  }}
                >
                  <td title={SEVERITY_TOOLTIP}><StatusPill label={severityLabel(d.severity)} variant={severityVariant(d.severity)} /></td>
                  <td className="tmDetectionCell">
                    <span className="tmDetectionName">
                      {d.detection_type_label || detectionTypeLabel(d.detection_type)}
                    </span>
                    <span className="muted tmDetectionSub">
                      {operationalRow
                        ? reasonCodeLabel(d.deterministic_reason_code)
                        : categoryLabel(d.category ?? 'CYBER_SECURITY')}
                    </span>
                  </td>
                  <td>{d.asset_name ?? '—'}</td>
                  {showAmounts ? (
                    <>
                      <td className="tmNowrap tmNum" data-testid="observed-amount">
                        {operationalRow
                          ? formatAmount(d.observed_amount, d.amount_decimals, { signed: true, unit: d.amount_unit })
                          : '—'}
                      </td>
                      <td className="tmNowrap tmNum" data-testid="expected-amount">
                        {operationalRow
                          ? formatAmount(d.expected_amount, d.amount_decimals, { unit: d.amount_unit })
                          : '—'}
                      </td>
                    </>
                  ) : null}
                  <td><StatusPill label={detectionStatusLabel(d.status)} variant={detectionStatusVariant(d.status)} /></td>
                  <td title={CONFIDENCE_TOOLTIP}><StatusPill label={`${confidencePercent(d.confidence)} (${confidenceBand(d.confidence)})`} variant={confidenceVariant(d.confidence)} /></td>
                  <td className="tmNowrap">{relativeTime(d.first_seen_at)}</td>
                  <td onClick={(e) => e.stopPropagation()}>
                    {d.status === 'open' ? (
                      <button type="button" className="btn btn-secondary tmBtnSm" disabled={investigatingId === d.id} aria-busy={investigatingId === d.id} onClick={() => onInvestigate(d.id)}>
                        {investigatingId === d.id ? 'Opening…' : 'Investigate'}
                      </button>
                    ) : (
                      <span className="muted tmActionNote">{detectionStatusLabel(d.status)}</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </TableShell>
          <Pager offset={offset} total={total} onPrev={() => setOffset(Math.max(0, offset - PAGE_SIZE))} onNext={() => setOffset(offset + PAGE_SIZE)} />
          {selectedId ? (
            <DetectionDetailPanels
              detectionId={selectedId}
              authHeaders={authHeaders}
              onClose={() => setSelectedId(null)}
            />
          ) : null}
        </>
      )}
    </div>
  );
}

/**
 * What the Operational Integrity lane can honestly claim: which telemetry lane
 * is feeding it, and which detectors are NOT evaluated because their
 * authoritative source does not exist. A provider failure is never allowed to
 * read as "no threats detected".
 */
function OperationalCoverageStrip({
  notice, operational,
}: {
  notice: { tone: 'info' | 'warning'; text: string } | null;
  operational: ThreatSummary['operational_integrity'];
}) {
  const unsupported = (operational?.by_type ?? []).filter((t) => !t.supported);
  return (
    <div className="tmCoverageStrip">
      {notice ? (
        <p
          className={notice.tone === 'warning' ? 'statusLine statusLine-warning tmNotice' : 'statusLine tmNotice'}
          role="status"
          data-testid="operational-coverage-notice"
          data-tone={notice.tone}
        >
          {notice.text}
        </p>
      ) : null}
      <p className="muted tmCoverageSource" data-testid="operational-telemetry-source">
        {coverageSourceLine(operational?.coverage ?? null)}
      </p>
      {unsupported.length > 0 ? (
        <p className="muted tmCoverageUnsupported" data-testid="operational-unsupported-note">
          <strong>Not evaluated</strong>: {unsupported.map((t) => t.label).join(', ')}. These
          detectors need an authoritative source this workspace does not have; a count of zero would not mean they ran.
        </p>
      ) : null}
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
    <div role="tabpanel" aria-label="Anomalies" className="tmTabPanel">
      <p className="muted tmTabIntro">
        <TmIcon name="info" />
        <span>Deviations that have not yet crossed detection criteria. An anomaly is not a confirmed threat.</span>
      </p>
      {loading ? (
        <p className="muted tmLoadingLine" role="status">Loading anomalies…</p>
      ) : err ? (
        <p className="statusLine tmError" role="alert">{err}</p>
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
          <TableShell headers={['Type', 'Asset', 'Confidence', 'Evidence', 'First seen', 'Last seen', 'Promotion status', 'Why not promoted']} compact className="tmTable tmAnomalyTable">
            {rows.map((a) => (
              <tr key={a.id}>
                <td className="tmStrongCell">{a.detection_type_label || detectionTypeLabel(a.detection_type)}</td>
                <td>{a.asset_name ?? '—'}</td>
                <td title={CONFIDENCE_TOOLTIP} className="tmNowrap tmNum">{confidencePercent(a.confidence)} ({confidenceBand(a.confidence)})</td>
                <td className="tmNum">{a.evidence_count}</td>
                <td className="tmNowrap">{relativeTime(a.first_seen_at)}</td>
                <td className="tmNowrap">{relativeTime(a.last_seen_at)}</td>
                <td><StatusPill label="Not promoted" variant="neutral" /></td>
                <td className="muted tmReasonCell">{a.not_promoted_reason ?? 'Below the promotion threshold.'}</td>
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
    <div role="tabpanel" aria-label="Telemetry" className="tmTabPanel">
      <TelemetryFilterBar
        eventType={eventType}
        source={source}
        freshness={freshness}
        onEventType={(v) => { setEventType(v); setOffset(0); }}
        onSource={(v) => { setSource(v); setOffset(0); }}
        onFreshness={(v) => { setFreshness(v); setOffset(0); }}
      />
      <p className="muted tmTabIntro">
        <TmIcon name="info" />
        <span>Canonical on-chain security telemetry for the selected window. Ingestion heartbeats (RPC polling, provider checks) are shown under Monitoring Sources, not here. Times are your local timezone.</span>
      </p>
      {loading ? (
        <p className="muted tmLoadingLine" role="status">Loading telemetry…</p>
      ) : err ? (
        <p className="statusLine tmError" role="alert">{err}</p>
      ) : sorted.length === 0 ? (
        <EmptyStateBlocker title="No security telemetry" body="No canonical on-chain security telemetry has been ingested for this workspace in the selected window." ctaHref={SOURCE_DIAGNOSTIC_HREF} ctaLabel="Check Monitoring Sources" />
      ) : (
        <>
          <TableShell headers={['Event Type', 'Asset', 'Transaction', 'Block', 'Ingestion Source', 'Evidence Quality', 'Freshness', 'Observed At']} compact className="tmTable tmTelemetryTable">
            {sorted.map((e) => (
              // The row's evidence mode is the same canonical field the pill renders;
              // it only adds an edge marker so non-live rows stand apart while scanning.
              <tr key={e.id} data-evidence-mode={String(e.evidence_mode ?? e.evidence_source ?? '').toLowerCase()}>
                <td className="tmStrongCell">{eventTypeLabel(e.event_type)}</td>
                <td>{e.asset_name ?? '—'}</td>
                <td>
                  {e.tx_hash ? (
                    <span className="tmHashCell">
                      <span className="tmMono" title={e.tx_hash}>{shortHex(e.tx_hash)}</span>
                      <button
                        type="button"
                        className="btn btn-secondary tmCopyBtn"
                        aria-label="Copy transaction hash"
                        title="Copy transaction hash"
                        data-copied={copied === e.tx_hash ? 'true' : undefined}
                        onClick={() => copyHash(e.tx_hash as string)}
                      >
                        {copied === e.tx_hash ? '✓' : 'Copy'}
                      </button>
                    </span>
                  ) : '—'}
                </td>
                <td className="tmMono tmNum">{e.block_number ?? '—'}</td>
                <td className="tmNowrap">{ingestionSourceLabel(e.ingestion_source)}</td>
                <td title={EVIDENCE_QUALITY_TOOLTIP}>{evidenceQualityLabel(e.evidence_quality)}</td>
                <td>
                  <span className="tmPillPair">
                    <StatusPill label={evidenceSourceLabel(e.evidence_mode ?? e.evidence_source)} variant={evidenceSourceVariant(e.evidence_mode ?? e.evidence_source)} />
                    <StatusPill label={rowFreshnessLabel(e.freshness)} variant={rowFreshnessVariant(e.freshness)} />
                  </span>
                </td>
                <td className="tmNowrap" title={e.observed_at ?? ''}>
                  <button type="button" className="btnLink tmSortBtn" onClick={() => setSortDesc((s) => !s)} title="Sort by observed time">
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

/* ── Shared: summary unavailable ─────────────────────────────────── */
/**
 * The summary did not load (request failed, or the payload carried no summary).
 * Fail closed: say the section has no result rather than rendering an empty
 * state that would read as "nothing arrived" or "no matches".
 */
function SummaryUnavailable({ section }: { section: string }) {
  return (
    <div className="tmInlineEmpty tmUnavailable" data-testid="summary-unavailable">
      <p className="tmInlineEmptyTitle">Unavailable</p>
      <p className="muted tmInlineEmptyBody">The threat monitoring summary did not load, so {section} cannot be shown. Use Refresh to try again.</p>
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
        <p className="statusLine statusLine-warning tmInlineWarning" role="status" data-testid="empty-stale-warning">{staleWarning}</p>
      ) : null}
      {showDiagnostic ? (
        <a href={SOURCE_DIAGNOSTIC_HREF} className="btn btn-secondary tmEmptyCta" data-testid="run-source-diagnostic">
          {nextActionLabel('diagnose_ingestion') || RUN_SOURCE_DIAGNOSTIC_LABEL}
        </a>
      ) : null}
    </div>
  );
}

/* ── Shared: filter bars + pager ────────────────────────────────── */
function DetectionFilterBar({
  severity, type, statusValue, category, categories, search,
  onSeverity, onType, onStatus, onCategory, onSearch,
}: {
  severity: string; type: string; statusValue: string; category: string; search: string;
  categories: Array<{ value: string; label: string }>;
  onSeverity: (v: string) => void; onType: (v: string) => void; onStatus: (v: string) => void;
  onCategory: (v: string) => void; onSearch: (v: string) => void;
}) {
  const operational = category === CATEGORY_OPERATIONAL_INTEGRITY;
  // The type list follows the selected lane so a cyber detector is never
  // offered as a filter for a business-reconciliation view (and vice versa).
  const typeOptions = operational
    ? [
        { value: '', label: 'All types' },
        { value: 'unmatched_issuance', label: 'Unmatched Issuance' },
        { value: 'settlement_timeout', label: 'Settlement Timeout' },
        { value: 'nav_valuation_drift', label: 'NAV / Valuation Drift' },
        { value: 'transfer_agent_mismatch', label: 'Transfer-Agent Mismatch' },
        { value: 'unauthorized_admin_change', label: 'Unauthorized Admin Change' },
      ]
    : [
        { value: '', label: 'All types' },
        { value: 'unusual_transfer', label: 'Unusual Transfer' },
        { value: 'coordinated_activity', label: 'Coordinated Activity' },
        { value: 'mint_burn_irregularity', label: 'Mint/Burn Irregularity' },
        { value: 'privileged_action', label: 'Privileged/Admin Action' },
      ];
  return (
    <div className="tmToolbar" data-testid="detection-filter-bar">
      <FilterField label="Category">
        <Select
          value={category}
          onValueChange={onCategory}
          ariaLabel="Filter by detection category"
          testId="category-filter"
          options={categories}
        />
      </FilterField>
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
          options={typeOptions}
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
      <FilterField label="Search" wide>
        <span className="tmSearch">
          <TmIcon name="search" />
          <input
            type="search"
            className="input tmSearchInput"
            value={search}
            onChange={(e) => onSearch(e.target.value)}
            placeholder="Search detections..."
            aria-label="Search detections"
            data-testid="detection-search"
          />
        </span>
      </FilterField>
    </div>
  );
}

function TelemetryFilterBar({ eventType, source, freshness, onEventType, onSource, onFreshness }: {
  eventType: string; source: string; freshness: string;
  onEventType: (v: string) => void; onSource: (v: string) => void; onFreshness: (v: string) => void;
}) {
  return (
    <div className="tmToolbar" data-testid="telemetry-filter-bar">
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

function FilterField({ label, children, wide = false }: { label: string; children: React.ReactNode; wide?: boolean }) {
  return (
    <label className={wide ? 'tmField tmField--wide' : 'tmField'}>
      <span className="tmFieldLabel">{label}</span>
      <span className="tmFieldControl">{children}</span>
    </label>
  );
}

function Pager({ offset, total, onPrev, onNext }: { offset: number; total: number; onPrev: () => void; onNext: () => void }) {
  const from = total === 0 ? 0 : offset + 1;
  const to = Math.min(offset + PAGE_SIZE, total);
  return (
    <div className="tmPager">
      <span className="muted tmPagerTotal" data-testid="pager-total">{from}–{to} of {total}</span>
      <div className="tmPagerButtons">
        <button type="button" className="btn btn-secondary" disabled={offset <= 0} onClick={onPrev}>Previous</button>
        <button type="button" className="btn btn-secondary" disabled={offset + PAGE_SIZE >= total} onClick={onNext}>Next</button>
      </div>
    </div>
  );
}
