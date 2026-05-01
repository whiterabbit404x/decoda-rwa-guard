import Link from 'next/link';
import type { SecurityWorkspaceStatus } from '../security-workspace-status';

type Props = {
  status?: SecurityWorkspaceStatus;
  securityStatus?: SecurityWorkspaceStatus;
  loading?: boolean;
};

export default function ThreatOverviewCard({ status, securityStatus, loading = false }: Props) {
  const resolved = status ?? securityStatus!;
  return (
    <article className="dataCard" aria-label="Security Overview">
      <p className="sectionEyebrow">Security overview</p>
      <h3>Strategic Infrastructure Guard posture</h3>
      <p className="kpiValue">{loading ? 'Loading…' : resolved.posture.replace('_', ' ')}</p>
      <p className="tableMeta">{resolved.customerMessage}</p>
      <p className="tableMeta">Continuous monitoring for protected RWA assets including Treasury-backed assets, tokenized debt infrastructure, oracle/NAV monitoring, custody and redemption-path monitoring, and compliance exposure.</p>
      <div className="monitoringKpiGrid" style={{ marginTop: 12 }}>
        <div><p className="sectionEyebrow">Protected assets</p><p className="kpiValue">{loading ? '—' : resolved.protectedAssets}</p></div>
        <div><p className="sectionEyebrow">Monitored systems</p><p className="kpiValue">{loading ? '—' : resolved.monitoredSystems}</p></div>
        <div><p className="sectionEyebrow">Reporting systems</p><p className="kpiValue">{loading ? '—' : resolved.reportingSystems}</p></div>
        <div><p className="sectionEyebrow">Open alerts</p><p className="kpiValue">{loading ? '—' : resolved.openAlerts}</p></div>
        <div><p className="sectionEyebrow">Active incidents</p><p className="kpiValue">{loading ? '—' : resolved.activeIncidents}</p></div>
        <div><p className="sectionEyebrow">Last telemetry</p><p className="kpiValue">{resolved.lastTelemetryAt ? new Date(resolved.lastTelemetryAt).toLocaleString() : 'No live signal received yet'}</p></div>
      </div>
      <p className="tableMeta">Last detection: {resolved.lastDetectionAt ? new Date(resolved.lastDetectionAt).toLocaleString() : 'None yet'}</p>
      <Link href={resolved.recommendedNextAction.href} prefetch={false} className="secondaryCta">{resolved.recommendedNextAction.label}</Link>
    </article>
  );
}
