'use client';

import Link from 'next/link';
import { ReactNode } from 'react';

export function MetricCard({ label, value, meta }: { label: string; value: ReactNode; meta?: ReactNode }) {
  return <article className="metricCard"><p className="metricLabel">{label}</p><p className="metricValue">{value}</p>{meta ? <p className="metricMeta">{meta}</p> : null}</article>;
}

export function StatusPill({ label }: { label: string }) {
  return <span className="ruleChip">{label}</span>;
}

export function DataTable({ headers, children }: { headers: string[]; children: ReactNode }) {
  return <div className="tableWrap"><table><thead><tr>{headers.map((header) => <th key={header}>{header}</th>)}</tr></thead><tbody>{children}</tbody></table></div>;
}

export function EmptyStateBlocker({ title, body, ctaHref, ctaLabel }: { title: string; body: string; ctaHref?: string; ctaLabel?: string }) {
  return <div className="emptyStatePanel"><h4>{title}</h4><p className="muted">{body}</p>{ctaHref && ctaLabel ? <Link href={ctaHref} prefetch={false}>{ctaLabel}</Link> : null}</div>;
}

export function StepRail({ steps }: { steps: Array<{ key: string; title: string; detail: string; complete: boolean; source?: string; href: string; cta: string }> }) {
  return <div className="stack compactStack">{steps.map((step) => <article key={step.key} className="dataCard"><div className="listHeader"><div><h3>{step.complete ? '✓' : '○'} {step.title}</h3><p className="muted">{step.detail}</p></div>{step.source ? <StatusPill label={step.source} /> : null}</div><Link href={step.href} prefetch={false}>{step.complete ? 'Review' : step.cta}</Link></article>)}</div>;
}

export function ActionPanel({ title, children }: { title: string; children: ReactNode }) {
  return <article className="dataCard"><p className="sectionEyebrow">{title}</p>{children}</article>;
}

export function RuntimeBanner({ title, detail }: { title: string; detail: string }) {
  return <div className="statusLine statusLine-warning"><strong>{title}</strong> {detail}</div>;
}
