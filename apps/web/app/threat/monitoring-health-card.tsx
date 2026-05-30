type SignalRowProps = { label: string; value: string; highlight?: 'ok' | 'warn' | 'err' };

function SignalRow({ label, value, highlight }: SignalRowProps) {
  const color =
    highlight === 'ok'
      ? 'var(--success-fg)'
      : highlight === 'warn'
        ? 'var(--warning-fg)'
        : highlight === 'err'
          ? 'var(--danger-fg)'
          : 'var(--text-secondary)';
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '0.5rem 0', borderBottom: '1px solid var(--border)' }}>
      <span style={{ fontSize: '0.875rem', color: 'var(--text-muted)' }}>{label}</span>
      <span style={{ fontSize: '0.875rem', fontWeight: 600, color }}>{value}</span>
    </div>
  );
}

type Props = {
  heartbeatLabel: string;
  pollLabel: string;
  telemetryLabel: string;
  reportingSystems: number;
  configuredSystems: number;
  freshnessStatus: string;
  confidenceStatus: string;
  providerHealthLabel?: string;
  domainLabels?: string[];
};

function signalHighlight(label: string): SignalRowProps['highlight'] {
  const v = label.toLowerCase();
  if (v === 'not received' || v === 'no telemetry' || v === 'unavailable' || v === 'offline') return 'err';
  if (v.includes('stale') || v.includes('degraded') || v.includes('limited')) return 'warn';
  return 'ok';
}

export default function MonitoringHealthCard(props: Props) {
  return (
    <article className="dataCard" aria-label="Monitoring Health">
      <p className="sectionEyebrow">Monitoring health</p>
      <h3 style={{ fontSize: '1.1rem', fontWeight: 700, margin: '0 0 1rem' }}>Runtime signal quality</h3>
      <SignalRow label="Worker heartbeat" value={props.heartbeatLabel} highlight={signalHighlight(props.heartbeatLabel)} />
      <SignalRow label="Poll loop" value={props.pollLabel} highlight={signalHighlight(props.pollLabel)} />
      <SignalRow label="Last telemetry" value={props.telemetryLabel} highlight={signalHighlight(props.telemetryLabel)} />
      <SignalRow
        label="Reporting systems"
        value={`${props.reportingSystems} / ${props.configuredSystems}`}
        highlight={props.reportingSystems === 0 ? 'err' : props.reportingSystems < props.configuredSystems ? 'warn' : 'ok'}
      />
      <SignalRow label="Freshness" value={props.freshnessStatus || 'Unavailable'} highlight={signalHighlight(props.freshnessStatus || '')} />
      <SignalRow label="Confidence" value={props.confidenceStatus || 'Unavailable'} highlight={signalHighlight(props.confidenceStatus || '')} />
      {props.providerHealthLabel ? (
        <SignalRow label="Provider health" value={props.providerHealthLabel} highlight={signalHighlight(props.providerHealthLabel)} />
      ) : null}
      {props.domainLabels && props.domainLabels.length > 0 ? (
        <p style={{ fontSize: '0.85rem', color: 'var(--text-muted)', marginTop: '0.75rem' }}>
          Domain coverage: {props.domainLabels.join(' · ')}
        </p>
      ) : null}
    </article>
  );
}
