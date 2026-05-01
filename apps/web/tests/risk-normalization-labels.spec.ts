import { expect, test } from '@playwright/test';
import { renderRiskLabel } from '../app/risk-normalization-labels';

test('risk normalization labels use guarded stale telemetry copy when guards disagree', () => {
    expect(
      renderRiskLabel({
        asset_criticality_score: 15,
        exposure_severity: 'low',
        market_confidence_impact: 45,
        redemption_liquidity_stress: 30,
        contagion_risk_label: 'guarded_due_to_stale_telemetry',
        regulatory_evidence_priority: 'high',
      })
    ).toBe('Guarded state (telemetry stale)');
});

