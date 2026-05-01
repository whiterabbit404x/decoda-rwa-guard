import type { ReactNode } from 'react';

export default function MonitoringHealthCard({ children }: { children: ReactNode }) {
  return <section aria-label="Monitoring Health">{children}</section>;
}
