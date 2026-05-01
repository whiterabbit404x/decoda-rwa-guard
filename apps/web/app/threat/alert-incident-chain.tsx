import type { ReactNode } from 'react';

export default function AlertIncidentChain({ children }: { children: ReactNode }) {
  return <section aria-label="Alert Incident Response Chain">{children}</section>;
}
