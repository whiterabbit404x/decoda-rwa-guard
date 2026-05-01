import Link from 'next/link';
import type { ReactNode } from 'react';

export default function ThreatEmptyState({ children }: { children?: ReactNode }) {
  if (children) return <div className="emptyStatePanel">{children}</div>;
  return (
    <div className="emptyStatePanel">
      <h4>Threat workspace is ready for setup</h4>
      <p className="muted">Connect Treasury-backed assets, custody wallets, issuer contracts, and oracle/NAV feeds to begin continuous monitoring.</p>
      <div className="buttonRow"><Link href="/monitored-systems" prefetch={false}>Configure monitoring</Link></div>
    </div>
  );
}
