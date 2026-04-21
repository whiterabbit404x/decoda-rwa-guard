'use client';

import Link from 'next/link';

type ThreatChainPanelProps = {
  detectionId?: string | null;
  alertId?: string | null;
  incidentId?: string | null;
  actionId?: string | null;
  linkedEvidenceCount?: number | null;
  lastEvidenceAt?: string | null;
  evidenceOrigin?: string | null;
  txHash?: string | null;
  blockNumber?: string | number | null;
  detectorKind?: string | null;
  liveLikeMode?: boolean;
  onOpenEvidence: () => void;
};

type ChainStep = {
  key: string;
  label: string;
  id?: string | null;
  href: string;
};

export default function ThreatChainPanel({
  detectionId,
  alertId,
  incidentId,
  actionId,
  linkedEvidenceCount,
  lastEvidenceAt,
  evidenceOrigin,
  txHash,
  blockNumber,
  detectorKind,
  liveLikeMode = false,
  onOpenEvidence,
}: ThreatChainPanelProps) {
  const chainSteps: ChainStep[] = [
    { key: 'detection', label: 'Detection', id: detectionId, href: '/alerts' },
    { key: 'alert', label: 'Alert', id: alertId, href: '/alerts' },
    { key: 'incident', label: 'Incident', id: incidentId, href: '/incidents' },
    { key: 'action', label: 'Action', id: actionId, href: '/history' },
  ];
  const evidenceCount = Number(linkedEvidenceCount || 0);

  return (
    <div className="emptyStatePanel">
      <h4>Threat chain summary</h4>
      <div className="buttonRow">
        {chainSteps.map((step) => (
          <p key={step.key} className="tableMeta">
            {step.id ? <Link href={step.href} prefetch={false}>{step.label}: {step.id}</Link> : `${step.label}: n/a`}
          </p>
        ))}
      </div>
      <p className="tableMeta">
        Evidence records {evidenceCount} · Last evidence at {lastEvidenceAt ? new Date(lastEvidenceAt).toLocaleString() : 'n/a'} · Origin/source {evidenceOrigin || 'n/a'}
      </p>
      <p className="tableMeta">
        tx {txHash || 'n/a'} · block {blockNumber || 'n/a'} · detector {detectorKind || 'n/a'}
      </p>
      {liveLikeMode && evidenceCount <= 0 ? (
        <p className="statusLine">Degraded evidence state: LIVE/HYBRID monitoring is active but this chain has no persisted evidence yet.</p>
      ) : null}
      <button type="button" onClick={onOpenEvidence}>Open evidence</button>
    </div>
  );
}
