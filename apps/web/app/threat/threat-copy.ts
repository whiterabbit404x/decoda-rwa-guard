export const THREAT_COPY = {
  noRecentTelemetry: 'No recent telemetry for this protected system',
  noLinkedEvidence: 'No linked real evidence yet',
  noLinkedContext: 'No linked alert/incident context available.',
} as const;

export const EVIDENCE_LABELS = {
  rawEvidenceRefs: 'raw evidence refs',
  evidenceId: 'evidence_id',
  provider: 'provider',
} as const;

export function formatRawEvidenceReference(params: {
  evidenceId?: string | null;
  txHash?: string | null;
  blockNumber?: number | null;
  provider?: string | null;
}): string {
  return `${EVIDENCE_LABELS.rawEvidenceRefs}: ${EVIDENCE_LABELS.evidenceId} ${params.evidenceId || 'n/a'} · tx ${params.txHash || 'n/a'} · block ${params.blockNumber ?? 'n/a'} · ${EVIDENCE_LABELS.provider} ${params.provider || 'n/a'}`;
}
