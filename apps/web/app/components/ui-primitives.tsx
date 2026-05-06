'use client';

import Link from 'next/link';
import { ReactNode } from 'react';

/* ── Surface card ─────────────────────────────────────────────── */
export function SurfaceCard({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <article className={`dataCard sharedSurfaceCard ${className}`.trim()}>{children}</article>;
}

/* ── Metric tile ──────────────────────────────────────────────── */
export function MetricTile({ label, value, meta }: { label: string; value: ReactNode; meta?: ReactNode }) {
  return (
    <article className="metricCard sharedMetricTile">
      <p className="metricLabel">{label}</p>
      <p className="metricValue">{value}</p>
      {meta ? <p className="metricMeta">{meta}</p> : null}
    </article>
  );
}

/* ── Status pill ──────────────────────────────────────────────── */
export type PillVariant = 'success' | 'warning' | 'danger' | 'info' | 'neutral' | 'default';

function pillClass(variant: PillVariant): string {
  if (variant === 'default') return 'ruleChip sharedStatusPill';
  return `ruleChip sharedStatusPill pill-${variant}`;
}

export function StatusPill({ label, variant = 'default' }: { label: string; variant?: PillVariant }) {
  return <span className={pillClass(variant)}>{label}</span>;
}

export function statusVariantFromSeverity(severity: string): PillVariant {
  switch (severity.toLowerCase()) {
    case 'critical': case 'high':   return 'danger';
    case 'medium':                   return 'warning';
    case 'low':   case 'resolved':  return 'success';
    case 'info':                     return 'info';
    default:                         return 'neutral';
  }
}

export function statusVariantFromStatus(status: string): PillVariant {
  switch (status.toLowerCase()) {
    case 'live':    case 'healthy':  case 'active':   case 'succeeded': return 'success';
    case 'degraded': case 'stale':  case 'warning':  case 'pending':   return 'warning';
    case 'offline': case 'failed':  case 'critical':                    return 'danger';
    case 'investigating': case 'in_progress':                           return 'info';
    default:                                                            return 'neutral';
  }
}

/* ── Table shell ──────────────────────────────────────────────── */
export function TableShell({ headers, children, compact = false }: { headers: string[]; children: ReactNode; compact?: boolean }) {
  return (
    <div className={`tableWrap sharedTableShell${compact ? ' tableCompact' : ''}`}>
      <table>
        <thead>
          <tr>{headers.map((h) => <th key={h}>{h}</th>)}</tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

/* ── Empty state ──────────────────────────────────────────────── */
export function EmptyStateBlocker({ title, body, ctaHref, ctaLabel }: { title: string; body: string; ctaHref?: string; ctaLabel?: string }) {
  return (
    <div className="emptyStatePanel sharedEmptyStateBlocker">
      <h4>{title}</h4>
      <p className="muted">{body}</p>
      {ctaHref && ctaLabel ? <Link href={ctaHref} prefetch={false} className="btn btn-secondary" style={{ marginTop: '0.75rem' }}>{ctaLabel}</Link> : null}
    </div>
  );
}

/* ── Tab strip ────────────────────────────────────────────────── */
export function TabStrip({ tabs, active, onChange }: { tabs: Array<{ key: string; label: string }>; active: string; onChange: (key: string) => void }) {
  return (
    <div className="buttonRow sharedTabStrip" role="tablist" aria-label="Views">
      {tabs.map((tab) => (
        <button
          key={tab.key}
          type="button"
          role="tab"
          aria-selected={active === tab.key}
          className={active === tab.key ? 'activeTab' : ''}
          onClick={() => onChange(tab.key)}
        >
          {tab.label}
        </button>
      ))}
    </div>
  );
}

/* ── CTA panel ────────────────────────────────────────────────── */
export function CtaPanel({ title, children }: { title: string; children: ReactNode }) {
  return <article className="dataCard sharedCtaPanel"><p className="sectionEyebrow">{title}</p>{children}</article>;
}

/* ── Buttons ──────────────────────────────────────────────────── */
export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger';

export function Button({
  children,
  variant = 'secondary',
  disabled = false,
  onClick,
  type = 'button',
}: {
  children: ReactNode;
  variant?: ButtonVariant;
  disabled?: boolean;
  onClick?: () => void;
  type?: 'button' | 'submit' | 'reset';
}) {
  return (
    <button
      type={type}
      className={`btn btn-${variant}`}
      disabled={disabled}
      onClick={onClick}
    >
      {children}
    </button>
  );
}

export function LinkButton({ href, children, variant = 'secondary' }: { href: string; children: ReactNode; variant?: ButtonVariant }) {
  return (
    <Link href={href} prefetch={false} className={`btn btn-${variant}`}>
      {children}
    </Link>
  );
}

/* ── Step rail (onboarding) ───────────────────────────────────── */
export function StepRail({ steps }: { steps: Array<{ key: string; title: string; detail: string; complete: boolean; source?: string; href: string; cta: string }> }) {
  return (
    <div className="stack compactStack">
      {steps.map((step) => (
        <SurfaceCard key={step.key}>
          <div className="listHeader">
            <div>
              <h3>{step.complete ? '✓' : '○'} {step.title}</h3>
              <p className="muted">{step.detail}</p>
            </div>
            {step.source ? <StatusPill label={step.source} variant="info" /> : null}
          </div>
          <Link href={step.href} prefetch={false}>{step.complete ? 'Review' : step.cta}</Link>
        </SurfaceCard>
      ))}
    </div>
  );
}

/* ── Runtime banner (inline variant for other uses) ──────────── */
export function RuntimeBanner({ title, detail }: { title: string; detail: string }) {
  return <div className="statusLine statusLine-warning"><strong>{title}</strong> {detail}</div>;
}

/* ── Aliases for backwards compatibility ──────────────────────── */
export const MetricCard  = MetricTile;
export const DataTable   = TableShell;
export const ActionPanel = CtaPanel;
