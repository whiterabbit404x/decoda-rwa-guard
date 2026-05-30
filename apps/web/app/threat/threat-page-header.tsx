import Link from 'next/link';
import { THREAT_COPY } from './threat-copy';

type Posture = 'healthy' | 'degraded' | 'offline' | 'setup_required';

const POSTURE_BADGE: Record<Posture, { label: string; style: React.CSSProperties }> = {
  healthy: { label: 'Live', style: { background: 'var(--success-bg)', color: 'var(--success-fg)', border: '1px solid var(--success-bdr)' } },
  degraded: { label: 'Limited', style: { background: 'var(--warning-bg)', color: 'var(--warning-fg)', border: '1px solid var(--warning-bdr)' } },
  offline: { label: 'Offline', style: { background: 'var(--danger-bg)', color: 'var(--danger-fg)', border: '1px solid var(--danger-bdr)' } },
  setup_required: { label: 'Setup Required', style: { background: 'var(--info-bg)', color: 'var(--info-fg)', border: '1px solid var(--info-bdr)' } },
};

type Props = {
  showLiveTelemetry: boolean;
  ensuringProofChain: boolean;
  proofChainDisabled: boolean;
  proofChainReason?: string;
  posture?: Posture;
  onRefreshNow: () => void;
  onGenerateProofChain: () => void;
};

export default function ThreatPageHeader({ showLiveTelemetry, ensuringProofChain, proofChainDisabled, proofChainReason, posture, onRefreshNow, onGenerateProofChain }: Props) {
  const badge = posture ? POSTURE_BADGE[posture] : showLiveTelemetry ? POSTURE_BADGE.healthy : POSTURE_BADGE.degraded;

  return (
    <article className="dataCard monitoringHeaderCard">
      <div className="monitoringHeaderTop">
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', marginBottom: '0.25rem' }}>
            <p className="sectionEyebrow" style={{ margin: 0 }}>Threat monitoring</p>
            <span
              style={{
                display: 'inline-block',
                fontSize: '0.75rem',
                fontWeight: 700,
                letterSpacing: '0.04em',
                padding: '0.2rem 0.65rem',
                borderRadius: '999px',
                textTransform: 'uppercase',
                ...badge.style,
              }}
            >
              {badge.label}
            </span>
          </div>
          <h2 style={{ fontSize: '1.75rem', fontWeight: 700, margin: '0 0 0.4rem' }}>Threat Monitoring</h2>
          <p style={{ fontSize: '1rem', color: 'var(--text-secondary)', margin: '0 0 0.35rem' }}>{THREAT_COPY.headerSubtitle}</p>
          <p style={{ fontSize: '0.9rem', color: 'var(--text-muted)', margin: 0 }}>
            {showLiveTelemetry
              ? 'Live telemetry supports oversight of treasury-backed assets and tokenized debt infrastructure, including oracle/NAV integrity checks, custody and redemption-path monitoring, and compliance exposure controls.'
              : 'When telemetry is available, this workspace can support oversight of treasury-backed assets and tokenized debt infrastructure with oracle/NAV checks, custody and redemption-path monitoring, and compliance exposure review.'}
          </p>
        </div>
        <div className="monitoringHeaderActions">
          <button type="button" className="secondaryCta" onClick={onRefreshNow}>Refresh now</button>
          <button
            type="button"
            className="secondaryCta"
            disabled={proofChainDisabled}
            onClick={onGenerateProofChain}
            title={proofChainReason}
          >
            {ensuringProofChain ? THREAT_COPY.generatingEvidencePackage : THREAT_COPY.generateEvidencePackage}
          </button>
          <Link href="/alerts" prefetch={false} className="secondaryCta">Review alerts</Link>
          <Link href="/incidents" prefetch={false} className="secondaryCta">Open incident queue</Link>
          <Link href="/monitored-systems" prefetch={false} className="secondaryCta">Manage monitored systems</Link>
          <Link href="/exports" prefetch={false} className="secondaryCta">Export evidence</Link>
        </div>
      </div>
    </article>
  );
}
