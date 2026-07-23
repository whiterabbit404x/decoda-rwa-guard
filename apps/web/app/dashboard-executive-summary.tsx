'use client';

import Link from 'next/link';
import { useCallback, useEffect, useRef, useState } from 'react';

import { usePilotAuth } from './pilot-auth-context';
import type { DashboardPageData } from './dashboard-data';
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
  formatDelta,
  formatRelativeTime,
  healthStatusVariant,
  mapExecutiveSummary,
  monitoringStateVariant,
  riskBandVariant,
  connectionStatusFromStream,
  CONNECTION_STATUS_LABELS,
  DATA_CONFIDENCE_LABELS,
  HEALTH_STATUS_LABELS,
  MONITORING_STATUS_LABELS,
  RISK_BAND_LABELS,
  TELEMETRY_FRESHNESS_LABELS,
  type ExecutiveSummary,
  type ExecutiveBrief,
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
  data?: DashboardPageData;
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
      try {
        const response = await fetch(EXECUTIVE_SUMMARY_ENDPOINT, {
          method: 'GET',
          headers: { Accept: 'application/json', ...authHeaders(workspaceId) },
          cache: 'no-store',
        });
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
  useEffect(() => {
    if (refreshSignal == null) return;
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

  return (
    <main className="container productPage dashboardExecPage">
      <div className="dashboardPageHeader dashboardExecHeader">
        <div>
          <h1 className="dashboardPageTitle">Dashboard</h1>
          <p className="dashboardPageSubtitle">
            Executive summary of protected assets, monitoring coverage, alerts, incidents, and system health.
          </p>
        </div>
        <div className="dashboardExecHeaderActions">
          <MonitoringStatusIndicators data={data} streamStatus={streamStatus} />
          <button type="button" className="btn btn-ghost execRefreshBtn" onClick={() => reload()} disabled={refreshing}>
            {refreshing ? 'Refreshing…' : 'Refresh'}
          </button>
        </div>
      </div>

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
          <FreshnessBanner data={data} />
          <CoverageCaveatBanner data={data} />

          <div className="execMetricRow execMetricRowScreen2">
            <ExecutiveBriefCard brief={data.executive_brief} onOpen={() => setBriefOpen(true)} />
            <div className="execMetricCluster">
              <TotalAssetValueCard data={data} />
              <MetricCard
                label="Open Incidents"
                value={String(data.metrics.open_incident_count)}
                meta={data.metrics.critical_or_high_incident_count > 0 ? `${data.metrics.critical_or_high_incident_count} critical/high` : 'None critical'}
                delta={formatDelta(data.metrics.deltas.open_incident_count)}
                valueVariant={data.metrics.open_incident_count > 0 ? 'warning' : undefined}
                href="/incidents"
              />
              <MetricCard
                label="Active Alerts"
                value={String(data.metrics.active_alert_count)}
                meta={activeAlertsMeta(data)}
                delta={formatDelta(data.metrics.deltas.active_alert_count)}
                valueVariant={data.metrics.active_alert_count > 0 ? 'danger' : undefined}
                href="/alerts"
              />
              <RiskScoreCard data={data} />
              <SystemHealthCard data={data} />
            </div>
          </div>

          <div className="execMainGrid execMainGridScreen2">
            <div className="execMainColumn">
              <RiskTrendChart trend={data.risk_trend} available={data.trend_available} partial={data.trend_partial} />
              <RecentAlertsCard alerts={data.recent_alerts} />
            </div>
            <CoPilotPanel data={data} onOpenBrief={() => setBriefOpen(true)} />
          </div>

          <BottomMetricsRow data={data} />
        </>
      ) : null}

      {briefOpen && data ? (
        <BriefDrawer brief={data.executive_brief} onClose={() => setBriefOpen(false)} />
      ) : null}
    </main>
  );
}

/* ── Status indicators: monitoring vs telemetry vs transport ──── */

// Three independent axes, deliberately kept separate so an open event channel
// is never mistaken for live monitoring:
//   1. Monitoring status  — operational truth from backend evidence.
//   2. Telemetry freshness — is the underlying data current?
//   3. Connection          — is the browser/SSE transport attached?
// "Live monitoring" is driven ONLY by data.monitoring_state.state === 'live';
// streamStatus can be 'live' (SSE connected) while monitoring is degraded.
function MonitoringStatusIndicators({
  data,
  streamStatus,
}: {
  data: ExecutiveSummary | null;
  streamStatus: string;
}) {
  const connection = connectionStatusFromStream(streamStatus);
  return (
    <div className="execStatusCluster" role="status" aria-label="Monitoring status">
      {data ? (
        <span
          className="execStatusPill execStatusPill--monitoring"
          data-state={data.monitoring_state.state}
          title={data.monitoring_state.reason}
        >
          <span className="execStatusDot" aria-hidden="true" />
          {MONITORING_STATUS_LABELS[data.monitoring_state.state]}
        </span>
      ) : null}
      {data ? (
        <span className="execStatusPill execStatusPill--telemetry" data-freshness={data.data_freshness.status}>
          {TELEMETRY_FRESHNESS_LABELS[data.data_freshness.status]}
        </span>
      ) : null}
      <span className="execStatusPill execStatusPill--connection" data-connection={connection}>
        {CONNECTION_STATUS_LABELS[connection]}
      </span>
    </div>
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

/* ── Freshness / stale banner ─────────────────────────────────── */

function FreshnessBanner({ data }: { data: ExecutiveSummary }) {
  const { status, latest_event_at } = data.data_freshness;
  if (status === 'fresh') return null;
  const isUnavailable = status === 'unavailable';
  return (
    <div className={`execFreshnessBanner${isUnavailable ? ' execFreshnessBanner--unavailable' : ' execFreshnessBanner--stale'}`} role="status">
      <strong>{isUnavailable ? 'Telemetry unavailable.' : 'Telemetry is stale.'}</strong>{' '}
      {isUnavailable
        ? 'No verified telemetry has been received from any monitored system.'
        : `Last verified event ${formatRelativeTime(latest_event_at)}. Metrics reflect the last received data.`}
    </div>
  );
}

/* ── Metric cards ─────────────────────────────────────────────── */

function MetricCard({
  label,
  value,
  meta,
  delta,
  valueVariant,
  href,
}: {
  label: string;
  value: string;
  meta?: string;
  delta?: { text: string; tone: 'up' | 'down' | 'flat' };
  valueVariant?: 'danger' | 'warning' | 'success';
  href?: string;
}) {
  const body = (
    <article className="execMetricCard dataCard" data-metric-label={label}>
      <p className="execMetricLabel">{label}</p>
      <p className={`execMetricValue${valueVariant ? ` execMetricValue--${valueVariant}` : ''}`}>{value}</p>
      <div className="execMetricFootRow">
        {meta ? <span className="execMetricMeta">{meta}</span> : <span />}
        {delta && delta.text ? <span className={`execMetricDelta execMetricDelta--${delta.tone}`}>{delta.text}</span> : null}
      </div>
    </article>
  );
  return href ? (
    <Link href={href} prefetch={false} className="execMetricLink">
      {body}
    </Link>
  ) : (
    body
  );
}

function TotalAssetValueCard({ data }: { data: ExecutiveSummary }) {
  const value = data.metrics.total_asset_value_usd;
  const unavailable = value == null;
  return (
    <Link href="/assets" prefetch={false} className="execMetricLink">
      <article className="execMetricCard dataCard" data-metric-label="Total Asset Value">
        <p className="execMetricLabel">Total Asset Value</p>
        <p className={`execMetricValue${unavailable ? ' execMetricValue--muted' : ''}`}>{formatAssetValue(value)}</p>
        <div className="execMetricFootRow">
          <span className="execMetricMeta">
            {data.metrics.monitored_asset_count} monitored asset{data.metrics.monitored_asset_count === 1 ? '' : 's'}
          </span>
        </div>
      </article>
    </Link>
  );
}

function RiskScoreCard({ data }: { data: ExecutiveSummary }) {
  const { risk_score, risk_band, deltas } = data.metrics;
  const delta = formatDelta(deltas.risk_score);
  return (
    <article className="execMetricCard execMetricCardScore dataCard" data-metric-label="Risk Score">
      <p className="execMetricLabel">Risk Score</p>
      <p className="execMetricValue">
        {risk_score}
        <span className="execMetricValueUnit">/100</span>
      </p>
      <div className="execMetricFootRow">
        <StatusPill label={RISK_BAND_LABELS[risk_band]} variant={riskBandVariant(risk_band)} />
        {delta.text ? <span className={`execMetricDelta execMetricDelta--${delta.tone}`}>{delta.text}</span> : null}
      </div>
    </article>
  );
}

function SystemHealthCard({ data }: { data: ExecutiveSummary }) {
  const status = data.metrics.system_health_status;
  const score = data.metrics.system_health_score;
  const delta = formatDelta(data.metrics.deltas.system_health_score);
  // Truthfulness: the label is derived solely from the backend's deterministic
  // status. "Healthy" only ever appears when status === 'healthy'; an
  // unconfigured workspace reads "Not configured", never a green 100.
  return (
    <Link href="/system-health" prefetch={false} className="execMetricLink">
      <article className="execMetricCard execMetricCardHealth dataCard" data-metric-label="System Health">
        <p className="execMetricLabel">System Health</p>
        <p className="execMetricValue">
          {status === 'not_configured' ? '—' : score}
          {status === 'not_configured' ? null : <span className="execMetricValueUnit">/100</span>}
        </p>
        <div className="execMetricFootRow">
          <StatusPill label={HEALTH_STATUS_LABELS[status]} variant={healthStatusVariant(status)} />
          {delta.text ? <span className={`execMetricDelta execMetricDelta--${delta.tone}`}>{delta.text}</span> : null}
        </div>
      </article>
    </Link>
  );
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

function ExecutiveBriefCard({ brief, onOpen }: { brief: ExecutiveBrief; onOpen: () => void }) {
  const isAi = brief.generation_mode === 'ai';
  const focus = brief.recommended_focus[0];
  const evidence = brief.evidence;
  return (
    <article className="execBriefCard dataCard" aria-label="Executive Brief">
      <div className="execBriefHeader">
        <div>
          <p className="sectionEyebrow">Executive Brief</p>
          <h2 className="execBriefHeadline">{brief.headline || 'Operational summary'}</h2>
        </div>
        <StatusPill label={isAi ? 'AI generated' : 'Deterministic'} variant={isAi ? 'info' : 'neutral'} />
      </div>
      <p className="execBriefMeta">
        {brief.period_start ? 'Last 24 hours' : 'Current period'} · Generated {formatRelativeTime(evidence.generated_at)}
      </p>
      <p className="execBriefSummary">{brief.summary || 'No summary available for this period.'}</p>
      {focus ? (
        <p className="execBriefFocus">
          <span className="execBriefFocusLabel">Focus:</span> {focus.title}
        </p>
      ) : null}
      <EvidenceFreshnessLine evidence={evidence} />
      <div className="execBriefFooter">
        {/* Confidence is the deterministic, monitoring-derived value — never the
            LLM's self-reported number. */}
        <span className="execBriefConfidence" data-confidence={evidence.data_confidence} title={evidence.data_confidence_reason}>
          Confidence: {DATA_CONFIDENCE_LABELS[evidence.data_confidence]}
        </span>
        <button type="button" className="btn btn-secondary execBriefBtn" onClick={onOpen}>
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

  const n = points.length;
  const xFor = (i: number) => padX + (i * (width - 2 * padX)) / Math.max(n - 1, 1);
  const yFor = (score: number) => height - padY - (score / 100) * (height - 2 * padY);

  const riskPath = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${xFor(i).toFixed(1)} ${yFor(p.risk_score).toFixed(1)}`).join(' ');
  const healthPath = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${xFor(i).toFixed(1)} ${yFor(p.health_score).toFixed(1)}`).join(' ');
  const active = hover != null ? points[hover] : null;

  return (
    <div className="execTrendChart">
      <div className="execTrendLegend">
        <span className="execTrendLegendItem execTrendLegendItem--risk">Risk score</span>
        <span className="execTrendLegendItem execTrendLegendItem--health">Health score</span>
      </div>
      <svg viewBox={`0 0 ${width} ${height}`} className="execTrendSvg" preserveAspectRatio="xMidYMid meet" role="img" aria-label="Risk and health score over the last seven days">
        {[0, 25, 50, 75, 100].map((tick) => (
          <g key={tick}>
            <line x1={padX} x2={width - padX} y1={yFor(tick)} y2={yFor(tick)} className="execTrendGrid" />
            <text x={4} y={yFor(tick) + 3} className="execTrendAxisLabel">{tick}</text>
          </g>
        ))}
        <path d={healthPath} className="execTrendLine execTrendLine--health" fill="none" />
        <path d={riskPath} className="execTrendLine execTrendLine--risk" fill="none" />
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
    <section className="execSectionCard dataCard" aria-label="Recent Alerts">
      <div className="execSectionHeader">
        <div>
          <p className="sectionEyebrow">Alerts</p>
          <h2 className="execSectionTitle">Recent Alerts</h2>
        </div>
        <Link href="/alerts" prefetch={false} className="execSeeAllLink">
          View all alerts
        </Link>
      </div>
      {alerts.length === 0 ? (
        <div className="execEmptyState">
          <EmptyStateBlocker
            title="No active alerts"
            body="No alerts are in an active state. When telemetry produces a detection, alerts will appear here."
            ctaHref="/alerts"
            ctaLabel="Go to Alerts"
          />
        </div>
      ) : (
        <div className="execAlertList">
          {alerts.map((alert) => (
            <Link key={alert.id} href={alert.url} prefetch={false} className="execAlertRow execAlertRowLink">
              <div className="execAlertMeta">
                <StatusPill label={alert.severity} variant={statusVariantFromSeverity(alert.severity)} />
                <span className="execAlertTitle">{alert.title}</span>
              </div>
              <div className="execAlertRight">
                {alert.asset ? <span className="execAlertAsset">{alert.asset}</span> : null}
                <span className="execAlertTime">{formatRelativeTime(alert.occurred_at)}</span>
                <StatusPill label={alert.status} variant={statusVariantFromStatus(alert.status)} />
              </div>
            </Link>
          ))}
        </div>
      )}
    </section>
  );
}

/* ── Bottom metrics ───────────────────────────────────────────── */

function BottomMetricsRow({ data }: { data: ExecutiveSummary }) {
  const uptime = data.metrics.uptime_30d_percent;
  return (
    <div className="execBottomMetricsRow">
      <BottomMetric label="Monitored Assets" value={String(data.metrics.monitored_asset_count)} href="/assets" />
      <BottomMetric label="Active Monitors" value={String(data.metrics.active_monitor_count)} href="/monitoring-sources" />
      <BottomMetric label="Data Sources" value={String(data.metrics.data_source_count)} href="/monitoring-sources" />
      <BottomMetric
        label="30-day Uptime"
        value={uptime == null ? 'Not available' : `${uptime.toFixed(2)}%`}
        href="/system-health"
        muted={uptime == null}
      />
    </div>
  );
}

function BottomMetric({ label, value, href, muted }: { label: string; value: string; href: string; muted?: boolean }) {
  return (
    <Link href={href} prefetch={false} className="execBottomMetric dataCard">
      <span className="execBottomMetricLabel">{label}</span>
      <span className={`execBottomMetricValue${muted ? ' execBottomMetricValue--muted' : ''}`}>{value}</span>
    </Link>
  );
}

/* ── AI Dashboard Co-Pilot panel ──────────────────────────────── */

function CoPilotPanel({ data, onOpenBrief }: { data: ExecutiveSummary; onOpenBrief: () => void }) {
  const { top_risk_drivers, system_health_insights, recommended_focus, generation_mode } = data.ai_copilot;
  return (
    <aside className="execCopilotPanel dataCard" aria-label="AI Dashboard Co-Pilot">
      <div className="execSectionHeader">
        <div>
          <p className="sectionEyebrow">Co-Pilot</p>
          <h2 className="execSectionTitle">AI Dashboard Co-Pilot</h2>
        </div>
        <StatusPill label={generation_mode === 'ai' ? 'AI' : 'Deterministic'} variant={generation_mode === 'ai' ? 'info' : 'neutral'} />
      </div>
      <p className="execCopilotGenerated">Generated {formatRelativeTime(data.ai_copilot.generated_at)}</p>

      <div className="execCopilotSection">
        <h3 className="execCopilotSubhead">Top Risk Drivers</h3>
        {top_risk_drivers.length === 0 ? (
          <p className="execCopilotEmpty muted">No material risk drivers in the current data.</p>
        ) : (
          <ul className="execDriverList">
            {top_risk_drivers.map((driver) => (
              <DriverRow key={driver.key} driver={driver} />
            ))}
          </ul>
        )}
      </div>

      <div className="execCopilotSection">
        <h3 className="execCopilotSubhead">System Health Insights</h3>
        {system_health_insights.length === 0 ? (
          <p className="execCopilotEmpty muted">All required health checks are passing.</p>
        ) : (
          <ul className="execInsightList">
            {system_health_insights.map((insight, index) => (
              <InsightRow key={`${insight.source_type}-${insight.source_id}-${index}`} insight={insight} />
            ))}
          </ul>
        )}
      </div>

      <div className="execCopilotSection">
        <h3 className="execCopilotSubhead">Recommended Focus</h3>
        {recommended_focus.length === 0 ? (
          <p className="execCopilotEmpty muted">No action required right now.</p>
        ) : (
          <ul className="execFocusList">
            {recommended_focus.map((focus, index) => (
              <li key={`${focus.destination}-${index}`} className="execFocusItem">
                <Link href={destinationRoute(focus.destination)} prefetch={false} className="execFocusLink">
                  {focus.title}
                </Link>
                <span className="execFocusReason muted">{focus.reason}</span>
              </li>
            ))}
          </ul>
        )}
      </div>

      <button type="button" className="btn btn-ghost execCopilotFullBtn" onClick={onOpenBrief}>
        View Full AI Insights
      </button>
    </aside>
  );
}

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

function BriefDrawer({ brief, onClose }: { brief: ExecutiveBrief; onClose: () => void }) {
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
      <div className="execMetricRow execMetricRowScreen2">
        <div className="execBriefCard dataCard execSkeletonCard execSkeletonBrief" />
        <div className="execMetricCluster">
          {Array.from({ length: 5 }).map((_, index) => (
            <div key={index} className="execMetricCard dataCard execSkeletonCard" />
          ))}
        </div>
      </div>
      <div className="execMainGrid execMainGridScreen2">
        <div className="execSectionCard dataCard execSkeletonChart" />
        <div className="execSectionCard dataCard execSkeletonPanel" />
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
