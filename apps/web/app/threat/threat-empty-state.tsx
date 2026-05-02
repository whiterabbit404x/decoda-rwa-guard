import Link from 'next/link';
import type { ReactNode } from 'react';
import { THREAT_COPY } from './threat-copy';

export default function ThreatEmptyState({ children }: { children?: ReactNode }) {
  if (children) return <div className="emptyStatePanel">{children}</div>;
  return (
    <div className="emptyStatePanel">
      <h4>Threat workspace is ready for setup</h4>
      <p className="muted">{THREAT_COPY.emptyWorkspaceSetup}</p>
      <div className="buttonRow"><Link href="/monitored-systems" prefetch={false}>Configure monitoring</Link></div>
    </div>
  );
}
