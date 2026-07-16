'use client';

import type { ReactNode } from 'react';

import { StatusPill, type PillVariant } from '../../components/ui-primitives';
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
    <article
      className="dataCard"
      style={{ padding: '0.85rem 1rem', flex: '1 1 180px', minWidth: '170px', display: 'flex', flexDirection: 'column', gap: '0.35rem' }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: '0.4rem' }}>
        <p className="sectionEyebrow" style={{ margin: 0, fontSize: '0.68rem' }}>{title}</p>
        {variant ? <span style={{ width: 8, height: 8, borderRadius: '50%', background: `var(--${variant}-fg, var(--text-muted))` }} /> : null}
      </div>
      <div style={{ fontSize: '1.4rem', fontWeight: 700, lineHeight: 1.1 }}>{primary}</div>
      <div style={{ fontSize: '0.72rem', color: 'var(--text-muted)', display: 'flex', flexDirection: 'column', gap: '0.1rem' }}>
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
      <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap', marginBottom: '1rem' }}>
        {[0, 1, 2, 3, 4].map((i) => (
          <article key={i} className="dataCard" style={{ padding: '0.85rem 1rem', flex: '1 1 180px', minWidth: '170px' }}>
            <p className="muted" style={{ fontSize: '0.75rem' }}>Loading…</p>
          </article>
        ))}
      </div>
    );
  }

  if (!summary) {
    return (
      <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap', marginBottom: '1rem' }}>
        <article className="dataCard" style={{ padding: '0.85rem 1rem', flex: 1 }}>
          <p className="muted" style={{ fontSize: '0.8rem', margin: 0 }}>
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
    <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap', marginBottom: '1rem' }}>
      {/* 1 — Source Health */}
      <Card
        title="Source Health"
        variant={healthVariant(sh.health_pct)}
        primary={sh.total === 0 ? <span className="muted">No sources</span> : `${sh.healthy}/${sh.total}`}
      >
        <span>{sh.health_pct == null ? 'No live health evidence' : `${fmtPercent(sh.health_pct)} overall health`}</span>
        <span>{sh.trend_24h == null ? 'Trend vs 24h: not yet recorded' : `Trend 24h: ${sh.trend_24h > 0 ? '+' : ''}${sh.trend_24h}`}</span>
      </Card>

      {/* 2 — Active Routes */}
      <Card title="Active Routes" primary={`${ar.primary}`}>
        <span>{ar.primary} primary · {ar.fallback} fallback</span>
        <span>{ar.changed_24h == null ? 'Route changes 24h: —' : `${ar.changed_24h} route change(s) in 24h`}</span>
      </Card>

      {/* 3 — Telemetry Coverage */}
      <Card
        title="Telemetry Coverage"
        variant={tc.coverage_pct == null ? 'neutral' : tc.coverage_pct >= 90 ? 'success' : tc.coverage_pct >= 60 ? 'warning' : 'danger'}
        primary={tc.coverage_pct == null ? <span className="muted">—</span> : fmtPercent(tc.coverage_pct)}
      >
        <span>{tc.fresh}/{tc.eligible} target(s) receiving fresh telemetry</span>
        <span>{tc.stale > 0 ? <span style={{ color: 'var(--warning-fg)' }}>{tc.stale} with stale telemetry</span> : 'No stale telemetry'}</span>
      </Card>

      {/* 4 — Oracle Heartbeats */}
      <Card
        title="Oracle Heartbeats"
        variant={oh.total === 0 ? 'neutral' : oh.missed > 0 ? 'danger' : oh.delayed > 0 ? 'warning' : 'success'}
        primary={oh.total === 0 ? <span className="muted">None</span> : `${oh.healthy}`}
      >
        {oh.total === 0 ? (
          <span>No oracle feeds configured</span>
        ) : (
          <>
            <span>{oh.healthy} healthy · {oh.delayed} delayed</span>
            <span>{oh.missed > 0 ? <span style={{ color: 'var(--danger-fg)' }}>{oh.missed} missed heartbeat(s)</span> : 'No missed heartbeats'}</span>
          </>
        )}
      </Card>

      {/* 5 — Agent Activity */}
      <Card
        title="Agent Activity"
        primary={aa.autonomous_actions_24h == null ? <span className="muted">—</span> : `${aa.autonomous_actions_24h}`}
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
