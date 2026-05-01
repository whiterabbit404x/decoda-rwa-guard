import type { ReactNode } from 'react';
import type { SecurityWorkspaceStatus } from '../security-workspace-status';

export default function ThreatOverviewCard({ children }: { children: ReactNode; securityStatus: SecurityWorkspaceStatus }) {
  return <section aria-label="Security Overview">{children}</section>;
}
