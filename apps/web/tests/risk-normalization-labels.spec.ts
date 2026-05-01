import { renderRiskLabel } from '../app/risk-normalization-labels';

describe('risk normalization labels', () => {
  it('uses guarded stale telemetry copy when guards disagree', () => {
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
});

