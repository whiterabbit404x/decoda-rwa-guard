import type { ReactNode } from 'react';

type Props = {
  title?: string;
  message?: string;
  action?: ReactNode;
  children?: ReactNode;
};

export default function ThreatEmptyState({
  title = 'Nothing to show right now',
  message,
  action,
  children,
}: Props) {
  return (
    <section className="emptyStatePanel" role="status" aria-live="polite">
      <h3>{title}</h3>
      {message ? <p className="tableMeta">{message}</p> : null}
      {children}
      {action ? <div style={{ marginTop: '0.75rem' }}>{action}</div> : null}
    </section>
  );
}
