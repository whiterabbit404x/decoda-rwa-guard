'use client';

import type { ReactNode } from 'react';

import { AnimatedNumber } from '../../components/motion';
import { StatusPill, type PillVariant } from '../../components/ui-primitives';
import { StatRow, type StatItem } from '../../components/page-header';
import { fmtPercent, fmtRelative, type SourceSummary } from './source-types';

// Five top summary cards. Every value comes from the backend `summary` object.
// A null field renders as an honest "—" / "Not measured" — never a fabricated 100%.

function Card({
  title,
  primary,
  variant,
  children,
}: {
  title: string;
  primary: ReactNode;
  variant?: PillVariant;
  children?: ReactNode;
}) {
  return (
    <article className={`dataCard msrcKpi${variant ? ` msrcKpi--${variant}` : ''}`}>
      <div className="msrcKpiHead">
        <p className="sectionEyebrow msrcKpiTitle">{title}</p>
        {variant ? <span className={`msrcDot msrcDot--${variant}`} aria-hidden /> : null}
      </div>
      <div className="msrcKpiValue">{primary}</div>
      <div className="msrcKpiMeta">
        {children}
      </div>
    </article>
  );
}

function healthVariant(pct: number | null): PillVariant {
  if (pct == null) return 'neutral';
  if (pct >= 90) return 'success';
  if (pct >= 70) return 'warning';
  return 'danger';
}

export function SummaryCards({ summary, loading }: { summary: SourceSummary | null; loading: boolean }) {
  if (loading && !summary) {
    return (
      <div className="msrcKpis" aria-busy="true">
        {[0, 1, 2, 3, 4].map((i) => (
          <article key={i} className="dataCard msrcKpi msrcKpi--loading">
            <span className="skelBlock msrcKpiSkel" aria-hidden />
            <p className="msrcKpiLoading">Loading…</p>
          </article>
        ))}
      </div>
    );
  }

  if (!summary) {
    return (
      <div className="msrcKpis">
        <article className="dataCard msrcKpi msrcKpiUnavailable">
          <p className="msrcKpiUnavailableText">
            Source summary is temporarily unavailable. Deterministic monitoring remains active.
          </p>
        </article>
      </div>
    );
  }

  const sh = summary.source_health;
  const ar = summary.active_routes;
  const tc = summary.telemetry_coverage;
  const oh = summary.oracle_heartbeats;
  const aa = summary.agent_activity;

  return (
    <div className="msrcKpis">
      {/* 1 — Source Health. Provider/runtime health (healthy/total) is separate from
             the evidence/event-detection signal: `quiet` counts healthy sources that
             are polling with fresh coverage but have observed no recent event — shown as
             "N live · no recent events", never as a degraded/unhealthy source. */}
      <Card
        title="Source Health"
        variant={healthVariant(sh.health_pct)}
        primary={sh.total === 0 ? <span className="msrcKpiNone">No sources</span> : <><AnimatedNumber value={sh.healthy} /><span className="msrcKpiDenom">/{sh.total}</span></>}
      >
        <span>{sh.health_pct == null ? 'No live health evidence' : `${fmtPercent(sh.health_pct)} overall health`}</span>
        {sh.quiet != null && sh.quiet > 0 ? (
          <span className="msrcTextMuted">{sh.quiet} live · no recent events</span>
        ) : null}
        <span>{sh.trend_24h == null ? 'Trend vs 24h: not yet recorded' : `Trend 24h: ${sh.trend_24h > 0 ? '+' : ''}${sh.trend_24h}`}</span>
      </Card>

      {/* 2 — Active Routes */}
      <Card title="Active Routes" primary={<AnimatedNumber value={ar.primary} />}>
        <span>{ar.primary} primary · {ar.fallback} fallback</span>
        <span>{ar.changed_24h == null ? 'Route changes 24h: —' : `${ar.changed_24h} route change(s) in 24h`}</span>
      </Card>

      {/* 3 — Telemetry Coverage. Primary metric is LIVE coverage; configured and
             historical/replay evidence are shown separately and never as live. */}
      <Card
        title="Live Telemetry Coverage"
        variant={tc.coverage_pct == null ? 'neutral' : tc.coverage_pct >= 90 ? 'success' : tc.coverage_pct >= 60 ? 'warning' : 'danger'}
        primary={tc.coverage_pct == null ? <span className="msrcKpiNone">—</span> : <AnimatedNumber value={tc.coverage_pct} format={fmtPercent} />}
      >
        <span>{tc.fresh}/{tc.configured ?? tc.eligible} target(s) with fresh live evidence</span>
        <span>{tc.configured ?? tc.eligible} configured{tc.stale > 0 ? <span className="msrcTextWarn"> · {tc.stale} stale</span> : null}</span>
        <span>
          {tc.historical_available
            ? <span className="msrcTextMuted">Historical/replay evidence available{(tc.replay_only ?? 0) > 0 ? ` (${tc.replay_only} replay-only)` : ''}</span>
            : 'No historical evidence'}
        </span>
      </Card>

      {/* 4 — Oracle Heartbeats */}
      <Card
        title="Oracle Heartbeats"
        variant={oh.total === 0 ? 'neutral' : oh.missed > 0 ? 'danger' : oh.delayed > 0 ? 'warning' : 'success'}
        primary={oh.total === 0 ? <span className="msrcKpiNone">None</span> : <AnimatedNumber value={oh.healthy} />}
      >
        {oh.total === 0 ? (
          <span>No oracle feeds configured</span>
        ) : (
          <>
            <span>{oh.healthy} healthy · {oh.delayed} delayed</span>
            <span>{oh.missed > 0 ? <span className="msrcTextDanger">{oh.missed} missed heartbeat(s)</span> : 'No missed heartbeats'}</span>
          </>
        )}
      </Card>

      {/* 5 — Agent Activity */}
      <Card
        title="Agent Activity"
        primary={aa.autonomous_actions_24h == null ? <span className="msrcKpiNone">—</span> : <AnimatedNumber value={aa.autonomous_actions_24h} />}
      >
        <span>autonomous action(s) in 24h</span>
        <span>
          {aa.approvals_required ? (
            <StatusPill label={`${aa.approvals_required} need approval`} variant="warning" />
          ) : (
            'No approvals pending'
          )}
        </span>
        <span>Last run: {fmtRelative(aa.last_optimization_at)}</span>
      </Card>
    </div>
  );
}

/**
 * The same canonical summary as SummaryCards, as one row of figures: what a
 * person needs at a glance (are sources reporting, is live telemetry arriving,
 * what is routed, does the agent need a decision). The per-source evidence --
 * latency, block lag, error rate, heartbeats -- lives in each source's detail
 * drawer. Nothing missing is shown as fine: no sources reads "No sources", no
 * live evidence reads "—", and only a measured share is coloured.
 */
export function SummaryFigures({ summary, loading }: { summary: SourceSummary | null; loading: boolean }) {
  if (loading && !summary) {
    return (
      <div className="statRow" aria-busy="true" aria-label="Source summary">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="statRowItem execSkeletonCard dashSkeletonFigure" />
        ))}
      </div>
    );
  }
  if (!summary) {
    return (
      <p className="statusLine msrcNotice">
        Source summary is temporarily unavailable. Deterministic monitoring remains active.
      </p>
    );
  }
  const sh = summary.source_health;
  const ar = summary.active_routes;
  const tc = summary.telemetry_coverage;
  const oh = summary.oracle_heartbeats;
  const aa = summary.agent_activity;
  const tone = (pct: number | null, warnAt: number) => (pct == null ? undefined : pct >= 90 ? undefined : pct >= warnAt ? 'warning' as const : 'danger' as const);
  const items: StatItem[] = [
    {
      label: 'Sources Healthy',
      value: sh.total === 0 ? 'No sources' : <><AnimatedNumber value={sh.healthy} /><span className="statRowUnit">/{sh.total}</span></>,
      meta: sh.health_pct == null
        ? 'No live health evidence'
        : `${fmtPercent(sh.health_pct)} overall health${sh.quiet ? ` · ${sh.quiet} live, no recent events` : ''}`,
      tone: tone(sh.health_pct, 70),
    },
    {
      label: 'Live Telemetry Coverage',
      value: tc.coverage_pct == null ? '—' : <AnimatedNumber value={tc.coverage_pct} format={fmtPercent} />,
      meta: `${tc.fresh}/${tc.configured ?? tc.eligible} target(s) with fresh live evidence${tc.stale > 0 ? ` · ${tc.stale} stale` : ''}`,
      tone: tone(tc.coverage_pct, 60),
    },
    {
      label: 'Active Routes',
      value: <AnimatedNumber value={ar.primary} />,
      meta: `${ar.primary} primary · ${ar.fallback} fallback`,
    },
    {
      label: 'Agent',
      value: aa.approvals_required
        ? <AnimatedNumber value={aa.approvals_required} />
        : aa.autonomous_actions_24h == null ? '—' : <AnimatedNumber value={aa.autonomous_actions_24h} />,
      meta: aa.approvals_required ? 'decision(s) need approval' : 'autonomous action(s) in 24h · No approvals pending',
      tone: aa.approvals_required ? 'warning' : undefined,
    },
  ];
  if (oh.total > 0) {
    items.push({
      label: 'Oracle Heartbeats',
      value: <><AnimatedNumber value={oh.healthy} /><span className="statRowUnit">/{oh.total}</span></>,
      meta: oh.missed > 0 ? `${oh.missed} missed heartbeat(s)` : `${oh.delayed} delayed · No missed heartbeats`,
      tone: oh.missed > 0 ? 'danger' : oh.delayed > 0 ? 'warning' : undefined,
    });
  }
  return <StatRow label="Source summary" items={items} />;
}
