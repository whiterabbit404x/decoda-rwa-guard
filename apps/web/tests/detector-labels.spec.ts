import { expect, test } from '@playwright/test';
import { detectorKindLabel } from '../app/threat/detector-labels';

test('maps canonical detector codes to customer-readable labels', () => {
  expect(detectorKindLabel('oracle_nav_divergence')).toBe('Oracle NAV divergence');
  expect(detectorKindLabel('proof_of_reserve_stale')).toBe('Proof of reserve stale');
  expect(detectorKindLabel('custody_wallet_movement_anomaly')).toBe('Custody wallet movement anomaly');
  expect(detectorKindLabel('oracle_divergence')).toBe('Oracle divergence');
  expect(detectorKindLabel('reserve_mismatch')).toBe('Reserve mismatch');
  expect(detectorKindLabel('unauthorized_mint_burn')).toBe('Unauthorized mint/burn');
  expect(detectorKindLabel('abnormal_redemption_activity')).toBe('Abnormal redemption activity');
  expect(detectorKindLabel('contract_upgrade_anomaly')).toBe('Contract upgrade anomaly');
  expect(detectorKindLabel('custody_transfer_anomaly')).toBe('Custody transfer anomaly');
  expect(detectorKindLabel('compliance_exposure')).toBe('Compliance exposure');
  expect(detectorKindLabel('monitoring_coverage_gap')).toBe('Monitoring coverage gap');
});
