import type { ReactNode } from 'react';

export default function ThreatOverviewCard({ children }: { children: ReactNode }) {
  return <section aria-label="Security Overview">{children}</section>;
}
