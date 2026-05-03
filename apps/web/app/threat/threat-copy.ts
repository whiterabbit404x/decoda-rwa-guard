export const THREAT_COPY = {
  headerSubtitle: 'Continuous monitoring for protected RWA assets, Treasury-backed instruments, oracle/NAV signals, detections, incidents, and response workflows.',
  overviewCoverageSummary: 'Continuous monitoring coverage for Treasury-backed assets across custody wallets, issuer contracts, oracle/NAV feeds, and compliance exposure controls.',
  noRecentTelemetry: 'No recent telemetry for this protected system',
  noLiveSignalYet: 'No live telemetry yet from Treasury-backed assets, custody wallets, issuer contracts, or oracle/NAV feeds.',
  noDetectionRecords: 'No detections yet. Live telemetry coverage for Treasury-backed assets, custody wallets, issuer contracts, oracle/NAV feeds, and compliance exposure checks will populate detections here once signals arrive.',
  noActiveIncidentChain: 'No active incident chain yet. Live telemetry for Treasury-backed assets, custody wallets, issuer contracts, oracle/NAV feeds, and compliance exposure checks has not produced alert-to-incident activity in this workspace.',
  noAlertLinkedYet: 'No alert linked yet (no live telemetry coverage event yet)',
  noIncidentLinkedYet: 'No incident linked yet (no live telemetry coverage event yet)',
  noResponseActionLinkedYet: 'No response action linked yet (no live telemetry coverage event yet)',
  emptyWorkspaceSetup: 'Connect Treasury-backed assets, custody wallets, issuer contracts, and oracle/NAV feeds to start live telemetry and compliance exposure coverage.',
  noLinkedEvidence: 'No linked real evidence yet',
  noLinkedContext: 'No linked alert/incident context available.',
  generateEvidencePackage: 'Generate evidence package',
  generatingEvidencePackage: 'Generating evidence package…',
  evidencePackageUnavailable: 'Evidence package generation is unavailable in the current state.',
  evidencePackageAlreadyRunning: 'Evidence package generation is already running.',
  generateEvidencePackageUnavailable: 'Generate evidence package is currently unavailable.',
  evidencePackageGenerated: 'Evidence package generated and monitoring status refreshed.',
  failedToGenerateEvidencePackage: 'Failed to generate evidence package.',
} as const;

export const EVIDENCE_LABELS = {
  rawEvidenceRefs: 'raw evidence references',
  evidenceId: 'evidence ID',
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
