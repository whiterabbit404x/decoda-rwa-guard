import Link from 'next/link';
import type { ReactNode } from 'react';

/**
 * The page hierarchy every product screen shares (Decoda Assets' idiom): one
 * title, one line saying what the screen is for, and the screen's actions --
 * the primary one last, filled. Nothing above it but the shell header.
 */
export function PageHeader({
  title,
  description,
  actions,
  meta,
}: {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
  meta?: ReactNode;
}) {
  return (
    <header className="pageHead">
      <div className="pageHeadText">
        <h1 className="pageHeadTitle">{title}</h1>
        {description ? <p className="pageHeadLede">{description}</p> : null}
        {meta ? <div className="pageHeadMeta">{meta}</div> : null}
      </div>
      {actions ? <div className="pageHeadActions">{actions}</div> : null}
    </header>
  );
}

export type StatTone = 'danger' | 'warning' | 'info' | 'neutral';

export type StatItem = {
  label: string;
  value: ReactNode;
  meta?: ReactNode;
  href?: string;
  /** Colours the figure only. Absent means neutral: a count is never green. */
  tone?: StatTone;
  testId?: string;
};

/**
 * A row of key figures. Each one is a figure the backend reported, with its
 * own definition beside it; a figure that links somewhere is the way into the
 * list it counts.
 */
export function StatRow({ items, label }: { items: StatItem[]; label: string }) {
  return (
    <section className="statRow" aria-label={label}>
      {items.map((item) => {
        const body = (
          <>
            <span className="statRowLabel">{item.label}</span>
            <span className="statRowValue" data-tone={item.tone ?? 'neutral'}>{item.value}</span>
            {item.meta ? <span className="statRowMeta">{item.meta}</span> : null}
          </>
        );
        return item.href ? (
          <Link key={item.label} href={item.href} prefetch={false} className="statRowItem statRowItem--link" data-testid={item.testId}>
            {body}
          </Link>
        ) : (
          <div key={item.label} className="statRowItem" data-testid={item.testId}>
            {body}
          </div>
        );
      })}
    </section>
  );
}
