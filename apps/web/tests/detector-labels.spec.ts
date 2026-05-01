import { detectorKindLabel } from '../app/threat/detector-labels';

test('maps canonical detector codes to customer-readable labels', () => {
  expect(detectorKindLabel('oracle_divergence')).toBe('Oracle divergence');
  expect(detectorKindLabel('reserve_mismatch')).toBe('Reserve mismatch');
  expect(detectorKindLabel('unauthorized_mint_burn')).toBe('Unauthorized mint/burn');
  expect(detectorKindLabel('abnormal_redemption_activity')).toBe('Abnormal redemption activity');
  expect(detectorKindLabel('contract_upgrade_anomaly')).toBe('Contract upgrade anomaly');
  expect(detectorKindLabel('custody_transfer_anomaly')).toBe('Custody transfer anomaly');
  expect(detectorKindLabel('compliance_exposure')).toBe('Compliance exposure');
  expect(detectorKindLabel('monitoring_coverage_gap')).toBe('Monitoring coverage gap');
});
