'use client';

import Link from 'next/link';
import { detectorKindLabel } from './threat/detector-labels';

type ThreatChainPanelProps = {
  chainLinkedIds?: {
    detection_id?: string | null;
    alert_id?: string | null;
    incident_id?: string | null;
    action_id?: string | null;
  } | null;
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
  evidenceDrawerLabel?: string;
  onOpenEvidence: () => void;
};

type ChainStep = {
  key: string;
  label: string;
  id?: string | null;
  href: string;
};

export default function ThreatChainPanel({
  chainLinkedIds,
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
  evidenceDrawerLabel = 'Open evidence drawer',
  onOpenEvidence,
}: ThreatChainPanelProps) {
  const normalizedDetectionId = chainLinkedIds?.detection_id ?? detectionId ?? null;
  const normalizedAlertId = chainLinkedIds?.alert_id ?? alertId ?? null;
  const normalizedIncidentId = chainLinkedIds?.incident_id ?? incidentId ?? null;
  const normalizedActionId = chainLinkedIds?.action_id ?? actionId ?? null;
  const chainSteps: ChainStep[] = [
    { key: 'detection', label: 'Detection', id: normalizedDetectionId, href: '/alerts' },
    { key: 'alert', label: 'Alert', id: normalizedAlertId, href: '/alerts' },
    { key: 'incident', label: 'Incident', id: normalizedIncidentId, href: '/incidents' },
    { key: 'action', label: 'Action', id: normalizedActionId, href: '/history' },
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
        tx {txHash || 'n/a'} · block {blockNumber || 'n/a'} · detector {detectorKindLabel(detectorKind)}
      </p>
      {liveLikeMode && evidenceCount <= 0 ? <p className="statusLine">Degraded evidence state: LIVE/HYBRID monitoring is active but this chain has no persisted evidence yet.</p> : null}
      {!liveLikeMode && evidenceCount <= 0 ? <p className="tableMeta">No linked evidence is currently persisted for this chain.</p> : null}
      <button type="button" onClick={onOpenEvidence}>{evidenceDrawerLabel}</button>
    </div>
  );
}
