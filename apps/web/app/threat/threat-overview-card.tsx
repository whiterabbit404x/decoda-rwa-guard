import Link from 'next/link';
import type { SecurityWorkspaceStatus } from '../security-workspace-status';
import { THREAT_COPY } from './threat-copy';

const POSTURE_STYLES: Record<SecurityWorkspaceStatus['posture'], { label: string; color: string }> = {
  healthy: { label: 'Healthy', color: 'var(--success-fg)' },
  degraded: { label: 'Limited coverage', color: 'var(--warning-fg)' },
  offline: { label: 'Offline', color: 'var(--danger-fg)' },
  setup_required: { label: 'Setup required', color: 'var(--info-fg)' },
};

type Props = {
  status?: SecurityWorkspaceStatus;
  securityStatus?: SecurityWorkspaceStatus;
  loading?: boolean;
};

export default function ThreatOverviewCard({ status, securityStatus, loading = false }: Props) {
  const resolved = status ?? securityStatus!;
  const postureStyle = POSTURE_STYLES[resolved.posture];

  return (
    <article className="dataCard" aria-label="Security Overview">
      <p className="sectionEyebrow">Security overview</p>
      <h3 style={{ fontSize: '1.125rem', fontWeight: 700, margin: '0 0 0.5rem' }}>Threat posture</h3>
      <p
        style={{
          fontSize: '1.75rem',
          fontWeight: 800,
          color: loading ? 'var(--text-muted)' : postureStyle.color,
          margin: '0 0 0.5rem',
          textTransform: 'capitalize',
        }}
      >
        {loading ? 'Loading…' : postureStyle.label}
      </p>
      <p style={{ fontSize: '0.9rem', color: 'var(--text-secondary)', margin: '0 0 1rem' }}>{resolved.customerMessage}</p>
      <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)', margin: '0 0 1rem' }}>{THREAT_COPY.overviewCoverageSummary}</p>
      <div
        className="monitoringKpiGrid"
        style={{ marginTop: '0.75rem', gap: '0.75rem' }}
      >
        <div>
          <p className="sectionEyebrow">Protected assets</p>
          <p style={{ fontSize: '1.75rem', fontWeight: 700, margin: '0.25rem 0 0', color: 'var(--text-primary)' }}>
            {loading ? '—' : resolved.protectedAssets}
          </p>
        </div>
        <div>
          <p className="sectionEyebrow">Monitored systems</p>
          <p style={{ fontSize: '1.75rem', fontWeight: 700, margin: '0.25rem 0 0', color: 'var(--text-primary)' }}>
            {loading ? '—' : resolved.monitoredSystems}
          </p>
        </div>
        <div>
          <p className="sectionEyebrow">Reporting systems</p>
          <p style={{ fontSize: '1.75rem', fontWeight: 700, margin: '0.25rem 0 0', color: 'var(--text-primary)' }}>
            {loading ? '—' : resolved.reportingSystems}
          </p>
        </div>
        <div>
          <p className="sectionEyebrow">Open alerts</p>
          <p
            style={{
              fontSize: '1.75rem',
              fontWeight: 700,
              margin: '0.25rem 0 0',
              color: !loading && resolved.openAlerts > 0 ? 'var(--danger-fg)' : 'var(--text-primary)',
            }}
          >
            {loading ? '—' : resolved.openAlerts}
          </p>
        </div>
        <div>
          <p className="sectionEyebrow">Active incidents</p>
          <p
            style={{
              fontSize: '1.75rem',
              fontWeight: 700,
              margin: '0.25rem 0 0',
              color: !loading && resolved.activeIncidents > 0 ? 'var(--warning-fg)' : 'var(--text-primary)',
            }}
          >
            {loading ? '—' : resolved.activeIncidents}
          </p>
        </div>
        <div>
          <p className="sectionEyebrow">Last telemetry</p>
          <p style={{ fontSize: '0.9rem', fontWeight: 600, margin: '0.25rem 0 0', color: 'var(--text-secondary)' }}>
            {resolved.lastTelemetryAt ? new Date(resolved.lastTelemetryAt).toLocaleString() : THREAT_COPY.noLiveSignalYet}
          </p>
        </div>
      </div>
      <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)', margin: '0.75rem 0 0.75rem' }}>
        Last detection: {resolved.lastDetectionAt ? new Date(resolved.lastDetectionAt).toLocaleString() : 'None yet'}
      </p>
      <Link href={resolved.recommendedNextAction.href} prefetch={false} className="secondaryCta">
        {resolved.recommendedNextAction.label}
      </Link>
    </article>
  );
}
