import Link from 'next/link';
import { THREAT_COPY } from './threat-copy';

type ChainItem = { id: string; label: string; status: string; detail?: string };

type Props = {
  alert?: ChainItem | null;
  incident?: ChainItem | null;
  responseAction?: ChainItem | null;
  domainLabels?: string[];
};

type StepProps = { title: string; value: string; statusColor?: string };

function ChainStep({ title, value, statusColor }: StepProps) {
  return (
    <div
      style={{
        flex: 1,
        padding: '1rem',
        background: 'rgba(255,255,255,0.03)',
        borderRadius: '0.75rem',
        border: '1px solid var(--border)',
      }}
    >
      <p style={{ fontSize: '0.75rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', color: 'var(--text-muted)', margin: '0 0 0.35rem' }}>{title}</p>
      <p style={{ fontSize: '0.9rem', fontWeight: 600, color: statusColor ?? 'var(--text-secondary)', margin: 0 }}>{value}</p>
    </div>
  );
}

export default function AlertIncidentChain({ alert, incident, responseAction, domainLabels = [] }: Props) {
  const hasActiveChain = Boolean(alert || incident || responseAction);

  return (
    <article className="dataCard" aria-label="Alert Incident Response Chain">
      <p className="sectionEyebrow">Incident chain</p>
      <h3 style={{ fontSize: '1.1rem', fontWeight: 700, margin: '0 0 1rem' }}>Alert → Incident → Response Action</h3>
      {hasActiveChain ? (
        <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap', marginBottom: '1rem' }}>
          <ChainStep
            title="Alert"
            value={alert?.label ?? THREAT_COPY.noAlertLinkedYet}
            statusColor={alert ? 'var(--danger-fg)' : undefined}
          />
          <ChainStep
            title="Incident"
            value={incident?.label ?? THREAT_COPY.noIncidentLinkedYet}
            statusColor={incident ? 'var(--warning-fg)' : undefined}
          />
          <ChainStep
            title="Response action"
            value={responseAction?.label ?? THREAT_COPY.noResponseActionLinkedYet}
            statusColor={responseAction ? 'var(--info-fg)' : undefined}
          />
        </div>
      ) : (
        <div style={{ marginBottom: '1rem' }}>
          <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap', marginBottom: '1rem' }}>
            <ChainStep title="Alert" value="No open alerts" />
            <ChainStep title="Incident" value="No active incidents" />
            <ChainStep title="Response action" value="No response action required" />
          </div>
          <p style={{ fontSize: '0.875rem', color: 'var(--text-muted)', margin: '0 0 0.75rem' }}>
            {THREAT_COPY.noActiveIncidentChain}
          </p>
          <div style={{ display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
            <Link href="/alerts" prefetch={false} className="secondaryCta">Review monitoring coverage</Link>
            <Link href="/incidents" prefetch={false} className="secondaryCta">Open incident queue</Link>
            <Link href="/response-actions" prefetch={false} className="secondaryCta">Configure response policy</Link>
          </div>
        </div>
      )}
      {domainLabels.length > 0 ? (
        <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)', margin: '0.5rem 0 0' }}>
          Monitored domain entities: {domainLabels.join(' · ')}
        </p>
      ) : null}
    </article>
  );
}
