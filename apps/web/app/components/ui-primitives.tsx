'use client';

import Link from 'next/link';
import { ReactNode } from 'react';

export function SurfaceCard({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <article className={`dataCard sharedSurfaceCard ${className}`.trim()}>{children}</article>;
}

export function MetricTile({ label, value, meta }: { label: string; value: ReactNode; meta?: ReactNode }) {
  return <article className="metricCard sharedMetricTile"><p className="metricLabel">{label}</p><p className="metricValue">{value}</p>{meta ? <p className="metricMeta">{meta}</p> : null}</article>;
}

export function StatusPill({ label }: { label: string }) {
  return <span className="ruleChip sharedStatusPill">{label}</span>;
}

export function TableShell({ headers, children }: { headers: string[]; children: ReactNode }) {
  return <div className="tableWrap sharedTableShell"><table><thead><tr>{headers.map((header) => <th key={header}>{header}</th>)}</tr></thead><tbody>{children}</tbody></table></div>;
}

export function EmptyStateBlocker({ title, body, ctaHref, ctaLabel }: { title: string; body: string; ctaHref?: string; ctaLabel?: string }) {
  return <div className="emptyStatePanel sharedEmptyStateBlocker"><h4>{title}</h4><p className="muted">{body}</p>{ctaHref && ctaLabel ? <Link href={ctaHref} prefetch={false}>{ctaLabel}</Link> : null}</div>;
}

export function TabStrip({ tabs, active, onChange }: { tabs: Array<{ key: string; label: string }>; active: string; onChange: (key: string) => void }) {
  return <div className="buttonRow sharedTabStrip" role="tablist" aria-label="Views">{tabs.map((tab) => <button key={tab.key} type="button" role="tab" aria-selected={active === tab.key} className={active === tab.key ? 'activeTab' : ''} onClick={() => onChange(tab.key)}>{tab.label}</button>)}</div>;
}

export function CtaPanel({ title, children }: { title: string; children: ReactNode }) {
  return <article className="dataCard sharedCtaPanel"><p className="sectionEyebrow">{title}</p>{children}</article>;
}

export const MetricCard = MetricTile;
export const DataTable = TableShell;
export const ActionPanel = CtaPanel;

export function StepRail({ steps }: { steps: Array<{ key: string; title: string; detail: string; complete: boolean; source?: string; href: string; cta: string }> }) {
  return <div className="stack compactStack">{steps.map((step) => <SurfaceCard key={step.key}><div className="listHeader"><div><h3>{step.complete ? '✓' : '○'} {step.title}</h3><p className="muted">{step.detail}</p></div>{step.source ? <StatusPill label={step.source} /> : null}</div><Link href={step.href} prefetch={false}>{step.complete ? 'Review' : step.cta}</Link></SurfaceCard>)}</div>;
}

export function RuntimeBanner({ title, detail }: { title: string; detail: string }) {
  return <div className="statusLine statusLine-warning"><strong>{title}</strong> {detail}</div>;
}
