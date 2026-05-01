import Link from 'next/link';
import type { ReactNode } from 'react';
import type { SecurityWorkspaceStatus } from '../security-workspace-status';

type Props = {
  status?: SecurityWorkspaceStatus;
  securityStatus?: SecurityWorkspaceStatus;
  loading?: boolean;
  children?: ReactNode;
};

function formatTime(value: string | null): string {
  if (!value) return 'Not yet available';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'Not yet available';
  return date.toLocaleString();
}

export default function ThreatOverviewCard({ status, securityStatus, loading = false, children }: Props) {
  const resolved = status ?? securityStatus;
  if (!resolved) return <section aria-label="Security Overview">{children}</section>;

  return (
    <section aria-label="Security Overview" className="sidebarMetaCard">
      <h3>Threat overview</h3>
      <p className="tableMeta">{loading ? 'Refreshing workspace threat posture…' : resolved.customerMessage}</p>
      <div className="statusMatrix">
        <div className="statusMatrixRow"><span>Posture</span><strong>{resolved.posture.replace('_', ' ')}</strong></div>
        <div className="statusMatrixRow"><span>Protected assets</span><strong>{resolved.protectedAssets}</strong></div>
        <div className="statusMatrixRow"><span>Monitored systems</span><strong>{resolved.monitoredSystems}</strong></div>
        <div className="statusMatrixRow"><span>Reporting systems</span><strong>{resolved.reportingSystems}</strong></div>
        <div className="statusMatrixRow"><span>Open alerts</span><strong>{resolved.openAlerts}</strong></div>
        <div className="statusMatrixRow"><span>Active incidents</span><strong>{resolved.activeIncidents}</strong></div>
        <div className="statusMatrixRow"><span>Last telemetry</span><strong>{formatTime(resolved.lastTelemetryAt)}</strong></div>
        <div className="statusMatrixRow"><span>Last detection</span><strong>{formatTime(resolved.lastDetectionAt)}</strong></div>
      </div>
      <div style={{ marginTop: '0.9rem' }}>
        <Link href={resolved.recommendedNextAction.href} prefetch={false}>{resolved.recommendedNextAction.label}</Link>
      </div>
      {children}
    </section>
  );
}
