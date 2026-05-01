export const DETECTOR_KIND_LABELS: Record<string, string> = {
  oracle_divergence: 'Oracle divergence',
  reserve_mismatch: 'Reserve mismatch',
  unauthorized_mint_burn: 'Unauthorized mint/burn',
  abnormal_redemption_activity: 'Abnormal redemption activity',
  contract_upgrade_anomaly: 'Contract upgrade anomaly',
  custody_transfer_anomaly: 'Custody transfer anomaly',
  compliance_exposure: 'Compliance exposure',
  monitoring_coverage_gap: 'Monitoring coverage gap',
};

export function detectorKindLabel(detectorKind?: string | null): string {
  const key = String(detectorKind || '').trim().toLowerCase();
  return DETECTOR_KIND_LABELS[key] || (detectorKind || 'n/a');
}
