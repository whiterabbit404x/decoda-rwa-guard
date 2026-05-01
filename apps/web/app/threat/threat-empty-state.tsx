import type { ReactNode } from 'react';

export default function ThreatEmptyState({ children }: { children: ReactNode }) {
  return <div className="emptyStatePanel">{children}</div>;
}
