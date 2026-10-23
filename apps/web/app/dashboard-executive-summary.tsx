'use client';

import Link from 'next/link';
import { useCallback, useEffect, useId, useRef, useState } from 'react';

import { usePilotAuth } from './pilot-auth-context';
import { markDashboardPerf } from './dashboard-perf';
import { AnimatedNumber } from './components/motion';
import { PageHeader, StatRow, type StatItem } from './components/page-header';
import type { useLiveWorkspaceFeed } from './use-live-workspace-feed';
import {
  StatusPill,
  EmptyStateBlocker,
  statusVariantFromSeverity,
  statusVariantFromStatus,
  type PillVariant,
} from './components/ui-primitives';
import {
  EXECUTIVE_SUMMARY_ENDPOINT,
  formatAgeSeconds,
  formatAssetValue,
  formatRelativeTime,
  healthStatusVariant,
  mapExecutiveSummary,
  riskBandVariant,
  connectionStatusFromStream,
  CONNECTION_STATUS_LABELS,
  DATA_CONFIDENCE_LABELS,
  HEALTH_STATUS_LABELS,
  MONITORING_STATUS_LABELS,
  RISK_BAND_LABELS,
  TELEMETRY_FRESHNESS_LABELS,
  type ExecutiveSummary,
  type EvidenceFreshness,
  type RecentAlert,
  type RiskDriver,
  type HealthInsight,
  type RiskTrendPoint,
} from './dashboard-executive-summary-data';

// Deep-link destinations for recommended-focus items and metric navigation. No
// broken placeholder links — every destination resolves to a real route.
const DESTINATION_ROUTES: Record<string, string> = {
  alerts: '/alerts',
  incidents: '/incidents',
  monitoring: '/monitoring-sources',
  assets: '/assets',
  'system-health': '/system-health',
};

function destinationRoute(destination: string): string {
  return DESTINATION_ROUTES[destination] ?? '/system-health';
}

// Zero-alert wording is coverage-aware and never over-claims safety: a zero
// count only means "no active alerts", and under degraded/stale coverage it is
// downgraded to "No active alerts detected" (the caveat banner adds the rest).
function activeAlertsMeta(data: ExecutiveSummary): string {
  if (data.metrics.active_alert_count > 0) return 'Requires attention';
  const coverageDegraded = data.monitoring_state.state !== 'live' || data.data_freshness.status !== 'fresh';
  return coverageDegraded ? 'No active alerts detected' : 'No active alerts';
}

type Props = {
  liveFeed?: ReturnType<typeof useLiveWorkspaceFeed>;
};

type LoadState = {
  status: 'loading' | 'ready' | 'error';
  data: ExecutiveSummary | null;
  error: string | null;
  refreshing: boolean;
};

function useExecutiveSummary(refreshSignal: string | null | undefined) {
  const { authHeaders, isAuthenticated, user } = usePilotAuth();
  const workspaceId = user?.current_workspace?.id ?? user?.current_workspace_id ?? null;
  const [state, setState] = useState<LoadState>({ status: 'loading', data: null, error: null, refreshing: false });
  const inFlight = useRef(false);

  const load = useCallback(
    async (isRefresh: boolean) => {
      if (!isAuthenticated) {
        setState({ status: 'error', data: null, error: 'Sign in to view the dashboard.', refreshing: false });
        return;
      }
      if (inFlight.current) return;
      inFlight.current = true;
      setState((prev) => ({ ...prev, refreshing: isRefresh, status: prev.data ? prev.status : 'loading' }));
      if (!isRefresh) {
        markDashboardPerf('summary.request.start');
      }
      try {
        const response = await fetch(EXECUTIVE_SUMMARY_ENDPOINT, {
          method: 'GET',
          headers: { Accept: 'application/json', ...authHeaders(workspaceId) },
          cache: 'no-store',
        });
        if (!isRefresh) {
          markDashboardPerf('summary.response', { ok: response.ok, status: response.status });
        }
        if (!response.ok) {
          throw new Error(`Dashboard request failed (${response.status})`);
        }
        const json = (await response.json()) as unknown;
        setState({ status: 'ready', data: mapExecutiveSummary(json), error: null, refreshing: false });
      } catch (error) {
        // Keep any previously loaded data visible (stale) instead of blanking.
        setState((prev) => ({
          status: prev.data ? 'ready' : 'error',
          data: prev.data,
          error: error instanceof Error ? error.message : 'Failed to load dashboard.',
          refreshing: false,
        }));
      } finally {
        inFlight.current = false;
      }
    },
    [authHeaders, isAuthenticated, workspaceId],
  );

  // Initial load.
  useEffect(() => {
    void load(false);
  }, [load]);

  // Refresh when the live workspace feed signals an update (SSE event or poll),
  // debounced so a burst of events triggers a single refetch. Chart/scroll
  // state is preserved because we mutate state in place rather than remount.
  //
  // The feed's FIRST completion is not an update: it is the feed's own initial
  // snapshot, fetched in parallel with the initial load above. Refetching on it
  // requested the summary twice on every dashboard load. Later completions (the
  // poll cadence, an SSE alert event) still refresh exactly as before.
  const initialFeedSignalSeen = useRef(false);
  useEffect(() => {
    if (refreshSignal == null) return;
    if (!initialFeedSignalSeen.current) {
      initialFeedSignalSeen.current = true;
      return;
    }
    const timer = setTimeout(() => void load(true), 400);
    return () => clearTimeout(timer);
  }, [refreshSignal, load]);

  return { ...state, reload: () => load(true) };
}

export default function DashboardExecutiveSummary({ liveFeed }: Props) {
  const refreshSignal = liveFeed?.lastFetchCompletedAt ?? null;
  const { status, data, error, refreshing, reload } = useExecutiveSummary(refreshSignal);
  const [briefOpen, setBriefOpen] = useState(false);
  const streamStatus = liveFeed?.streamStatus ?? 'disconnected';

  // Closes the critical-path timeline: the skeleton below clears when the load
  // resolves either way, so an error state is recorded as an end too rather
  // than leaving the measurement open.
  const skeletonVisible = status === 'loading' && !data;
  const skeletonDismissed = useRef(false);
  useEffect(() => {
    if (skeletonVisible || skeletonDismissed.current) return;
    skeletonDismissed.current = true;
    markDashboardPerf('dashboard.skeleton.dismissed', { outcome: data ? 'ready' : status });
  }, [skeletonVisible, status, data]);

  // The one primary action: the backend's own top recommended focus (derived
  // from canonical alerts, incidents and coverage), never a frontend guess.
  const primaryFocus = data?.executive_brief.recommended_focus[0] ?? data?.ai_copilot.recommended_focus[0] ?? null;

  return (
    <main className="container productPage dashboardExecPage">
      <PageHeader
        title="Dashboard"
        description="Protected assets, monitoring coverage, alerts, incidents and system health for this workspace."
        actions={(
          <>
            <button type="button" className="btn btn-ghost execRefreshBtn" onClick={() => reload()} disabled={refreshing}>
              {refreshing ? 'Refreshing…' : 'Refresh'}
            </button>
            {primaryFocus ? (
              <Link href={destinationRoute(primaryFocus.destination)} prefetch={false} className="btn btn-primary" data-testid="dashboard-primary-action">
                {primaryFocus.title}
              </Link>
            ) : null}
          </>
        )}
      />

      {status === 'loading' && !data ? <LoadingSkeleton /> : null}

      {status === 'error' && !data ? (
        <ErrorState message={error ?? 'The dashboard is temporarily unavailable.'} onRetry={reload} />
      ) : null}

      {data ? (
        <>
          {error ? (
            <div className="execStaleBanner" role="status">
              <strong>Showing last known data.</strong> Live refresh failed: {error}.
            </div>
          ) : null}

          <MonitoringStatusCard data={data} streamStatus={streamStatus} />

          <StatRow label="Key figures" items={keyFigures(data)} />
          <CoverageCaveatBanner data={data} />

          <div className="dashGrid">
            <RecentAlertsCard alerts={data.recent_alerts} />
            <ExecutiveBriefCard data={data} onOpen={() => setBriefOpen(true)} />
          </div>

          {data.trend_available ? (
            <RiskTrendChart trend={data.risk_trend} available={data.trend_available} partial={data.trend_partial} />
          ) : null}
        </>
      ) : null}

      {briefOpen && data ? (
        <BriefDrawer data={data} onClose={() => setBriefOpen(false)} />
      ) : null}
    </main>
  );
}

/* ── Key figures ─────────────────────────────────────────────── */

// Four figures, each the way into the list it counts. Colour marks a count
// that needs someone; a zero is never painted as healthy.
function keyFigures(data: ExecutiveSummary): StatItem[] {
  const m = data.metrics;
  const value = m.total_asset_value_usd;
  return [
    {
      label: 'Open Incidents',
      value: <AnimatedNumber value={m.open_incident_count} />,
      meta: m.critical_or_high_incident_count > 0 ? `${m.critical_or_high_incident_count} critical/high` : 'None critical',
      tone: m.open_incident_count > 0 ? 'warning' : undefined,
      href: '/incidents',
      testId: 'figure-open-incidents',
    },
    {
      label: 'Active Alerts',
      value: <AnimatedNumber value={m.active_alert_count} />,
      meta: activeAlertsMeta(data),
      tone: m.active_alert_count > 0 ? 'danger' : undefined,
      href: '/alerts',
      testId: 'figure-active-alerts',
    },
    {
      label: 'Monitored Assets',
      value: <AnimatedNumber value={m.monitored_asset_count} />,
      // A missing valuation reads "Not available" (formatAssetValue), never $0.
      meta: value == null
        ? `${m.active_monitor_count} active monitor${m.active_monitor_count === 1 ? '' : 's'}`
        : `Total Asset Value ${formatAssetValue(value)}`,
      href: '/assets',
      testId: 'figure-assets',
    },
    {
      label: 'Risk Score',
      value: (
        <>
          <AnimatedNumber value={m.risk_score} />
          <span className="statRowUnit">/100</span>
        </>
      ),
      meta: <StatusPill label={RISK_BAND_LABELS[m.risk_band]} variant={riskBandVariant(m.risk_band)} />,
      testId: 'figure-risk',
    },
  ];
}

/* ── Monitoring status ───────────────────────────────────────── */

// The dashboard's first answer: is this workspace being monitored, and what is
// the next step. Three evidence axes stay separate and are read straight from
// the backend's monitoring facts -- heartbeat proves the worker is alive,
// ingestion that the pipeline processed, telemetry that monitored data arrived
// -- and the browser's own connection is a fourth fact, never a monitoring one.
// A missing fact reads "not confirmed", never fine.
function MonitoringStatusCard({ data, streamStatus }: { data: ExecutiveSummary; streamStatus: string }) {
  const monitoring = data.monitoring_state;
  const connection = connectionStatusFromStream(streamStatus);
  const activity = data.activity;
  // Period movement is shown only when the backend reported a period; an
  // older payload without one must not render as "0 opened".
  const activityReported = activity.period_start != null;
  const live = monitoring.state === 'live';
  const health = data.metrics.system_health_status;
  return (
    <section className="dashStatus" data-state={monitoring.state} aria-label="Monitoring status">
      <div className="dashStatusMain">
        <p className="dashStatusHeadline">
          <span className="dashStatusDot" aria-hidden="true" />
          {MONITORING_STATUS_LABELS[monitoring.state]}
        </p>
        <p className="dashStatusReason">{monitoring.reason || freshnessSentence(data)}</p>
        {monitoring.reason ? <p className="dashStatusFreshness">{freshnessSentence(data)}</p> : null}
        <ul className="dashFacts" aria-label="Monitoring evidence">
          <li className="dashFact" data-ok={data.data_freshness.status === 'fresh' ? 'true' : 'false'}>
            {TELEMETRY_FRESHNESS_LABELS[data.data_freshness.status]}
          </li>
          <li className="dashFact" data-ok={monitoring.workers_fresh ? 'true' : 'false'} title="Worker liveness only — not proof that telemetry arrived.">
            {monitoring.workers_fresh ? 'Heartbeat current' : 'Heartbeat not confirmed'}
          </li>
          <li className="dashFact" data-ok={monitoring.ingestion_healthy ? 'true' : 'false'} title="Telemetry pipeline processing, as reported by the backend.">
            {monitoring.ingestion_healthy ? 'Ingestion healthy' : 'Ingestion not confirmed'}
          </li>
          <li className="dashFact dashFact--transport" data-connection={connection}>
            Live updates: {CONNECTION_STATUS_LABELS[connection]}
          </li>
          <li className="dashFact dashFact--transport">
            System health: {HEALTH_STATUS_LABELS[health]}
          </li>
        </ul>
        <p className="dashActivity">
          {activityReported ? (
            <>
              Last 24 hours: {activity.alerts_created_during_period} alert{activity.alerts_created_during_period === 1 ? '' : 's'} created
              {' · '}{activity.incidents_opened_during_period} incident{activity.incidents_opened_during_period === 1 ? '' : 's'} opened
              {' · '}{activity.incidents_resolved_during_period} resolved
            </>
          ) : 'Last 24 hours: Not reported'}
        </p>
        <p className="dashFactNote">Heartbeat shows the worker is alive; only telemetry shows monitored data arrived.</p>
      </div>
      <div className="dashStatusActions">
        {live ? null : (
          <Link href="/monitoring-sources" prefetch={false} className="btn btn-secondary">
            Set up monitoring
          </Link>
        )}
        <Link href="/system-health" prefetch={false} className="dashStatusLink">
          {live ? 'View system health' : 'Diagnose'}
        </Link>
      </div>
    </section>
  );
}

// Fail-closed zero-alert caveat: a zero alert count under degraded/offline
// monitoring or stale telemetry does not prove the absence of threats.
function CoverageCaveatBanner({ data }: { data: ExecutiveSummary }) {
  const noAlerts = data.metrics.active_alert_count === 0;
  const coverageDegraded = data.monitoring_state.state !== 'live' || data.data_freshness.status !== 'fresh';
  if (!noAlerts || !coverageDegraded) return null;
  return (
    <div className="execCoverageCaveat" role="status">
      <strong>No active alerts detected.</strong>{' '}
      Monitoring coverage is degraded; zero alerts does not confirm absence of threats.
    </div>
  );
}

/* ── Telemetry freshness ──────────────────────────────────────── */

// Telemetry freshness in one sentence. Fresh telemetry says when it last
// arrived; stale and missing telemetry say so plainly.
function freshnessSentence(data: ExecutiveSummary): string {
  const { status, latest_event_at } = data.data_freshness;
  if (status === 'fresh') return `Telemetry fresh — last verified event ${formatRelativeTime(latest_event_at)}.`;
  if (status === 'unavailable') return 'Telemetry unavailable. No verified telemetry has been received from any monitored system.';
  return `Telemetry is stale. Last verified event ${formatRelativeTime(latest_event_at)}. Metrics reflect the last received data.`;
}

/* ── Executive Brief card ─────────────────────────────────────── */

// Evidence-freshness line: keeps *generation time* (when the brief text was
// written) visually distinct from *evidence freshness* (how current the
// underlying telemetry is). A brief can be freshly generated over stale data.
function EvidenceFreshnessLine({ evidence }: { evidence: EvidenceFreshness }) {
  const current = evidence.data_current_through
    ? `${formatRelativeTime(evidence.data_current_through)} (${formatAgeSeconds(evidence.telemetry_age_seconds)} old)`
    : 'no verified telemetry';
  return (
    <p className="execBriefEvidence" data-telemetry={evidence.telemetry_status}>
      <span className="execBriefEvidenceItem">Data current through: {current}</span>
    </p>
  );
}

function ExecutiveBriefCard({ data, onOpen }: { data: ExecutiveSummary; onOpen: () => void }) {
  const brief = data.executive_brief;
  const isAi = brief.generation_mode === 'ai';
  const focus = brief.recommended_focus.length > 0 ? brief.recommended_focus : data.ai_copilot.recommended_focus;
  const evidence = brief.evidence;
  return (
    <article className="dashCard dashBrief" aria-label="Executive Brief">
      <div className="dashCardHead">
        <h2 className="dashCardTitle">Executive Brief</h2>
        <StatusPill label={isAi ? 'AI generated' : 'Deterministic'} variant={isAi ? 'info' : 'neutral'} />
      </div>
      <p className="dashBriefHeadline">{brief.headline || 'Operational summary'}</p>
      <p className="dashBriefSummary">{brief.summary || 'No summary available for this period.'}</p>
      {focus.length > 0 ? (
        <div className="dashFocus">
          <h3 className="dashFocusTitle">Recommended Focus</h3>
          <ul className="dashFocusList">
            {focus.slice(0, 3).map((item, index) => (
              <li key={`${item.destination}-${index}`}>
                <Link href={destinationRoute(item.destination)} prefetch={false} className="dashFocusLink">
                  {item.title}
                </Link>
                <span className="dashFocusReason">{item.reason}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      <EvidenceFreshnessLine evidence={evidence} />
      <div className="dashBriefFooter">
        {/* Confidence is the deterministic, monitoring-derived value — never the
            LLM's self-reported number. */}
        <span className="execBriefConfidence" data-confidence={evidence.data_confidence} title={evidence.data_confidence_reason}>
          Confidence: {DATA_CONFIDENCE_LABELS[evidence.data_confidence]} · Generated {formatRelativeTime(evidence.generated_at)}
        </span>
        <button type="button" className="btn btn-secondary dashBriefBtn" onClick={onOpen}>
          View Full AI Summary
        </button>
      </div>
    </article>
  );
}

/* ── Risk Trend chart ─────────────────────────────────────────── */

// Real snapshots only. `available`/`partial` come from the backend, which derives
// them from real daily points (never fabricated dates or forward-filled scores):
//   * fewer than 2 real daily points -> "Historical trend not available yet";
//   * >= 2 points but < 7 days covered -> "Partial data" (window not yet full).
function RiskTrendChart({ trend, available, partial }: { trend: RiskTrendPoint[]; available: boolean; partial: boolean }) {
  const points = trend.filter((p) => p.captured_at);
  const hasEnough = available && points.length >= 2;

  return (
    <section className="execSectionCard dataCard" aria-label="Risk Trend">
      <div className="execSectionHeader">
        <div>
          <p className="sectionEyebrow">Trend</p>
          <h2 className="execSectionTitle">Risk Trend — Last 7 Days</h2>
        </div>
        {hasEnough && partial ? <StatusPill label="Partial data" variant="warning" /> : null}
      </div>
      {!hasEnough ? (
        <div className="execEmptyState">
          <EmptyStateBlocker
            title="Historical trend not available yet"
            body="Risk history builds from real dashboard snapshots. At least two snapshots are needed before a trend can be drawn — no synthetic history is shown."
            ctaHref="/system-health"
            ctaLabel="View system health"
          />
        </div>
      ) : (
        <TrendSvg points={points} />
      )}
    </section>
  );
}

function TrendSvg({ points }: { points: RiskTrendPoint[] }) {
  const width = 640;
  const height = 200;
  const padX = 36;
  const padY = 20;
  const [hover, setHover] = useState<number | null>(null);
  const areaId = `execTrendArea-${useId().replace(/:/g, '')}`;

  const n = points.length;
  const xFor = (i: number) => padX + (i * (width - 2 * padX)) / Math.max(n - 1, 1);
  const yFor = (score: number) => height - padY - (score / 100) * (height - 2 * padY);

  const riskPath = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${xFor(i).toFixed(1)} ${yFor(p.risk_score).toFixed(1)}`).join(' ');
  const healthPath = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${xFor(i).toFixed(1)} ${yFor(p.health_score).toFixed(1)}`).join(' ');
  // The area under the risk line is the same real points, closed to the
  // baseline — shading only, no interpolated or extra values.
  const riskArea = `${riskPath} L ${xFor(n - 1).toFixed(1)} ${(height - padY).toFixed(1)} L ${xFor(0).toFixed(1)} ${(height - padY).toFixed(1)} Z`;
  const active = hover != null ? points[hover] : null;

  return (
    <div className="execTrendChart">
      <div className="execTrendLegend">
        <span className="execTrendLegendItem execTrendLegendItem--risk">Risk score</span>
        <span className="execTrendLegendItem execTrendLegendItem--health">Health score</span>
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} className="execTrendSvg" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Risk and health score over the last seven days">
        <defs>
          <linearGradient id={areaId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" className="execTrendAreaStopTop" />
            <stop offset="1" className="execTrendAreaStopBottom" />
          </linearGradient>
        </defs>
        {[0, 25, 50, 75, 100].map((tick) => (
          <g key={tick}>
            <line x1={padX} x2={width - padX} y1={yFor(tick)} y2={yFor(tick)} className="execTrendGrid" />
            <text x={4} y={yFor(tick) + 3} className="execTrendAxisLabel">{tick}</text>
          </g>
        ))}
        <path d={riskArea} className="execTrendArea" fill={`url(#${areaId})`} />
        {hover != null ? (
          <line x1={xFor(hover)} x2={xFor(hover)} y1={padY} y2={height - padY} className="execTrendHoverLine" />
        ) : null}
        <path d={healthPath} pathLength={1} className="execTrendLine execTrendLine--health" fill="none" />
        <path d={riskPath} pathLength={1} className="execTrendLine execTrendLine--risk" fill="none" />
        {points.map((p, i) => (
          <g key={i}>
            <circle cx={xFor(i)} cy={yFor(p.risk_score)} r={hover === i ? 5 : 3} className="execTrendDot execTrendDot--risk" />
            <rect
              x={xFor(i) - 14}
              y={padY}
              width={28}
              height={height - 2 * padY}
              fill="transparent"
              onMouseEnter={() => setHover(i)}
              onMouseLeave={() => setHover((h) => (h === i ? null : h))}
            >
              <title>{`${new Date(p.captured_at ?? '').toLocaleDateString()} — risk ${p.risk_score}, health ${p.health_score}`}</title>
            </rect>
          </g>
        ))}
      </svg>
      {active ? (
        <div className="execTrendReadout">
          {new Date(active.captured_at ?? '').toLocaleDateString()} · Risk <strong>{active.risk_score}</strong> · Health{' '}
          <strong>{active.health_score}</strong> · Alerts <strong>{active.active_alert_count}</strong>
        </div>
      ) : null}
    </div>
  );
}

/* ── Recent Alerts ────────────────────────────────────────────── */

function RecentAlertsCard({ alerts }: { alerts: RecentAlert[] }) {
  return (
    <section className="dashCard dashAlerts" aria-label="Recent Alerts">
      <div className="dashCardHead">
        <h2 className="dashCardTitle">Recent Alerts</h2>
        <Link href="/alerts" prefetch={false} className="dashCardLink">
          View all alerts
        </Link>
      </div>
      {alerts.length === 0 ? (
        <EmptyStateBlocker
          title="No active alerts"
          body="No alerts are in an active state. When telemetry produces a detection, alerts will appear here."
          ctaHref="/alerts"
          ctaLabel="Go to Alerts"
        />
      ) : (
        <ul className="dashAlertList">
          {alerts.map((alert) => (
            <li key={alert.id}>
              <Link href={alert.url} prefetch={false} className="dashAlertRow execAlertRowLink">
                <StatusPill label={alert.severity} variant={statusVariantFromSeverity(alert.severity)} />
                <span className="dashAlertTitle">{alert.title}</span>
                <span className="dashAlertTime">{formatRelativeTime(alert.occurred_at)}</span>
                <StatusPill label={alert.status} variant={statusVariantFromStatus(alert.status)} />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

/* ── Co-Pilot detail (rendered in the full summary drawer) ────── */

function DriverRow({ driver }: { driver: RiskDriver }) {
  return (
    <li className="execDriverRow">
      <div className="execDriverTop">
        <span className="execDriverLabel">{driver.label}</span>
        <span className="execDriverPercent">{driver.percent}%</span>
      </div>
      <div className="execDriverBarTrack">
        <div className="execDriverBarFill" style={{ width: `${Math.max(0, Math.min(100, driver.percent))}%` }} />
      </div>
    </li>
  );
}

function insightVariant(severity: string): PillVariant {
  const s = severity.toLowerCase();
  if (s === 'critical') return 'danger';
  if (s === 'warning' || s === 'warn') return 'warning';
  return 'info';
}

function InsightRow({ insight }: { insight: HealthInsight }) {
  const link = insightSourceLink(insight);
  return (
    <li className="execInsightRow">
      <StatusPill label={insight.severity} variant={insightVariant(insight.severity)} />
      <div className="execInsightBody">
        <span className="execInsightMessage">{insight.message}</span>
        {link ? (
          <Link href={link} prefetch={false} className="execInsightSource">
            View source
          </Link>
        ) : (
          <span className="execInsightSourceMeta muted">{insight.occurred_at ? formatRelativeTime(insight.occurred_at) : insight.source_type || 'system'}</span>
        )}
      </div>
    </li>
  );
}

function insightSourceLink(insight: HealthInsight): string | null {
  if (!insight.source_id) return null;
  switch (insight.source_type) {
    case 'monitoring_target':
    case 'provider':
    case 'worker_heartbeat':
      return '/monitoring-sources';
    case 'alert':
      return `/alerts/${insight.source_id}`;
    case 'incident':
      return `/incidents/${insight.source_id}`;
    default:
      return '/system-health';
  }
}

/* ── Full Brief drawer ────────────────────────────────────────── */

function BriefDrawer({ data, onClose }: { data: ExecutiveSummary; onClose: () => void }) {
  const brief = data.executive_brief;
  const { top_risk_drivers, system_health_insights } = data.ai_copilot;
  const uptime = data.metrics.uptime_30d_percent;
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  const isAi = brief.generation_mode === 'ai';
  const evidence = brief.evidence;
  return (
    <div className="execDrawerBackdrop" role="presentation" onClick={onClose}>
      <div className="execDrawer" role="dialog" aria-modal="true" aria-label="Executive Brief detail" onClick={(e) => e.stopPropagation()}>
        <div className="execDrawerHeader">
          <div>
            <p className="sectionEyebrow">Executive Brief</p>
            <h2 className="execDrawerTitle">{brief.headline}</h2>
          </div>
          <button type="button" className="btn btn-ghost execDrawerClose" onClick={onClose} aria-label="Close">
            ✕
          </button>
        </div>

        <div className="execDrawerMetaRow">
          <StatusPill label={isAi ? 'AI generated' : 'Deterministic fallback'} variant={isAi ? 'info' : 'neutral'} />
          <span className="muted">Generated {formatRelativeTime(evidence.generated_at)}</span>
          <span className="muted">
            Data current through {evidence.data_current_through ? formatRelativeTime(evidence.data_current_through) : 'no verified telemetry'}
          </span>
          <span className="muted">Telemetry age: {formatAgeSeconds(evidence.telemetry_age_seconds)}</span>
          <span className="muted" data-confidence={evidence.data_confidence}>
            Confidence: {DATA_CONFIDENCE_LABELS[evidence.data_confidence]}
          </span>
        </div>
        {evidence.data_confidence_reason ? (
          <p className="execDrawerConfidenceReason muted">{evidence.data_confidence_reason}</p>
        ) : null}

        <section className="execDrawerSection">
          <h3 className="execDrawerSubhead">Summary</h3>
          <p className="execDrawerText">{brief.summary}</p>
        </section>

        <section className="execDrawerSection">
          <h3 className="execDrawerSubhead">Key Findings</h3>
          {brief.key_findings.length === 0 ? (
            <p className="muted">No material findings for this period.</p>
          ) : (
            <ul className="execDrawerFindings">
              {brief.key_findings.map((finding, index) => (
                <li key={index} className="execDrawerFinding">
                  <div className="execDrawerFindingHead">
                    <StatusPill label={finding.severity} variant={statusVariantFromSeverity(finding.severity)} />
                    <span className="execDrawerFindingTitle">{finding.title}</span>
                  </div>
                  <p className="execDrawerText">{finding.description}</p>
                  {finding.source_refs.length > 0 ? (
                    <div className="execDrawerRefs">
                      {finding.source_refs.map((ref) => (
                        <Link key={`${ref.source_type}-${ref.source_id}`} href={ref.url || '#'} prefetch={false} className="execDrawerRef">
                          {ref.label || `${ref.source_type} ${ref.source_id}`}
                        </Link>
                      ))}
                    </div>
                  ) : null}
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="execDrawerSection">
          <h3 className="execDrawerSubhead">Recommended Focus</h3>
          {brief.recommended_focus.length === 0 ? (
            <p className="muted">No action required.</p>
          ) : (
            <ul className="execDrawerFocusList">
              {brief.recommended_focus.map((focus, index) => (
                <li key={index}>
                  <Link href={destinationRoute(focus.destination)} prefetch={false} className="execFocusLink">
                    {focus.title}
                  </Link>{' '}
                  <span className="muted">— {focus.reason}</span>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="execDrawerSection" aria-label="AI Dashboard Co-Pilot">
          <h3 className="execDrawerSubhead">Top Risk Drivers</h3>
          {top_risk_drivers.length === 0 ? (
            <p className="muted">No material risk drivers in the current data.</p>
          ) : (
            <ul className="execDriverList">
              {top_risk_drivers.map((driver) => (
                <DriverRow key={driver.key} driver={driver} />
              ))}
            </ul>
          )}
        </section>

        <section className="execDrawerSection">
          <h3 className="execDrawerSubhead">System Health Insights</h3>
          {system_health_insights.length === 0 ? (
            <p className="muted">All required health checks are passing.</p>
          ) : (
            <ul className="execInsightList">
              {system_health_insights.map((insight, index) => (
                <InsightRow key={`${insight.source_type}-${insight.source_id}-${index}`} insight={insight} />
              ))}
            </ul>
          )}
        </section>

        <section className="execDrawerSection">
          <h3 className="execDrawerSubhead">Coverage</h3>
          <dl className="dashCoverageList">
            <div><dt>Monitored Assets</dt><dd>{data.metrics.monitored_asset_count}</dd></div>
            <div><dt>Active Monitors</dt><dd>{data.metrics.active_monitor_count}</dd></div>
            <div><dt>Data Sources</dt><dd>{data.metrics.data_source_count}</dd></div>
            <div><dt>Total Asset Value</dt><dd>{formatAssetValue(data.metrics.total_asset_value_usd)}</dd></div>
            <div><dt>30-day Uptime</dt><dd>{uptime == null ? 'Not available' : `${uptime.toFixed(2)}%`}</dd></div>
            <div>
              <dt>System Health</dt>
              <dd>
                {data.metrics.system_health_status === 'not_configured' ? '—' : `${data.metrics.system_health_score}/100 `}
                <StatusPill label={HEALTH_STATUS_LABELS[data.metrics.system_health_status]} variant={healthStatusVariant(data.metrics.system_health_status)} />
              </dd>
            </div>
          </dl>
          {!data.trend_available ? (
            <p className="muted">Historical trend not available yet — risk history builds from real dashboard snapshots, and no synthetic history is shown.</p>
          ) : null}
        </section>

        {brief.citations.length > 0 ? (
          <section className="execDrawerSection">
            <h3 className="execDrawerSubhead">Evidence References</h3>
            <ul className="execDrawerCitations">
              {brief.citations.map((citation) => (
                <li key={`${citation.source_type}-${citation.source_id}`}>
                  <Link href={citation.url || '#'} prefetch={false} className="execDrawerRef">
                    {citation.label || `${citation.source_type} ${citation.source_id}`}
                  </Link>
                  {citation.occurred_at ? <span className="muted"> · {formatRelativeTime(citation.occurred_at)}</span> : null}
                </li>
              ))}
            </ul>
          </section>
        ) : null}

        <div className="execDrawerFootnote muted">
          {isAi ? `Model: ${brief.provider ?? 'provider'} / ${brief.model ?? 'model'}` : 'Deterministic brief — generated without a model call.'}
          {brief.prompt_version ? ` · ${brief.prompt_version}` : ''}
        </div>
      </div>
    </div>
  );
}

/* ── Loading / error states ───────────────────────────────────── */

function LoadingSkeleton() {
  return (
    <div className="execSkeleton" aria-hidden="true">
      <div className="dashStatus execSkeletonCard dashSkeletonStatus" />
      <div className="statRow">
        {Array.from({ length: 4 }).map((_, index) => (
          <div key={index} className="statRowItem execSkeletonCard dashSkeletonFigure" />
        ))}
      </div>
      <div className="dashGrid">
        <div className="dashCard execSkeletonCard dashSkeletonPanel" />
        <div className="dashCard execSkeletonCard dashSkeletonPanel" />
      </div>
    </div>
  );
}

function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <section className="execSectionCard dataCard" aria-label="Dashboard error">
      <EmptyStateBlocker
        title="Dashboard temporarily unavailable"
        body={`${message} The application shell is preserved — retry to reload the executive summary.`}
        ctaLabel="Retry"
        ctaOnClick={onRetry}
      />
    </section>
  );
}
