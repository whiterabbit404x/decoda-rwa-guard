import Link from 'next/link';

type Props = {
  showLiveTelemetry: boolean;
  ensuringProofChain: boolean;
  proofChainDisabled: boolean;
  proofChainReason?: string;
  onRefreshNow: () => void;
  onGenerateProofChain: () => void;
};

export default function ThreatPageHeader({ showLiveTelemetry, ensuringProofChain, proofChainDisabled, proofChainReason, onRefreshNow, onGenerateProofChain }: Props) {
  return (
    <article className="dataCard monitoringHeaderCard">
      <div className="monitoringHeaderTop">
        <div>
          <p className="sectionEyebrow">Threat monitoring</p>
          <h2>Threat Monitoring</h2>
          <p className="tableMeta">Continuous monitoring for protected RWA assets, detections, alerts, incidents, and response workflows.</p>
          <p className="tableMeta">{showLiveTelemetry ? 'Live telemetry currently supports oversight of treasury-backed assets and tokenized debt infrastructure, including oracle/NAV integrity checks, custody and redemption-path monitoring, and compliance exposure controls.' : 'When telemetry is available, this workspace can support oversight of treasury-backed assets and tokenized debt infrastructure with oracle/NAV checks, custody and redemption-path monitoring, and compliance exposure review.'}</p>
        </div>
        <div className="monitoringHeaderActions">
          <button type="button" className="secondaryCta" onClick={onRefreshNow}>Refresh now</button>
          <button type="button" className="secondaryCta" disabled={proofChainDisabled} onClick={onGenerateProofChain} title={proofChainReason}>
            {ensuringProofChain ? 'Generating simulator proof chain…' : 'Generate simulator proof chain'}
          </button>
          <Link href="/alerts" prefetch={false} className="secondaryCta">Review alerts</Link>
          <Link href="/incidents" prefetch={false} className="secondaryCta">Open incident queue</Link>
          <Link href="/monitored-systems" prefetch={false} className="secondaryCta">Manage monitored systems</Link>
        </div>
      </div>
    </article>
  );
}
