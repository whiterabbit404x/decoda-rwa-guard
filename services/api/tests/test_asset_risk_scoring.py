"""Deterministic asset risk scoring — Decimal-safe reserve/market/monitoring math.

Pure unit tests (no DB). Cover the guarantees in the Screen 3 spec: reserve
coverage, decimal safety, missing/stale feed behavior, price baseline deviation,
insufficient history, monitoring coverage, weighted score, confidence, risk-level
thresholds, and the truthfulness rule that missing evidence raises risk (never
silently zero).
"""

from __future__ import annotations

from decimal import Decimal

from services.api.app.domains.asset_risk import scoring as s


def _healthy_inputs(**overrides):
    base = dict(
        reserve_required=True,
        reserve_feed_configured=True,
        reserve_verified=True,
        reserve_value_usd=Decimal('128000000'),
        liability_value_usd=Decimal('100000000'),
        reserve_min_coverage_ratio=Decimal('1.0'),
        reserve_age_seconds=120,
        reserve_stale_seconds=86400,
        price_source_configured=True,
        price_usd=Decimal('1.00'),
        baseline_30d=Decimal('1.00'),
        baseline_7d=Decimal('1.00'),
        price_stddev_30d=Decimal('0.001'),
        price_sample_count=30,
        price_age_seconds=60,
        price_stale_seconds=3600,
        monitoring_controls=[('target', True, True), ('recent_telemetry', True, True), ('price_source', True, True), ('reserve_feed', True, True)],
        has_monitoring_target=True,
    )
    base.update(overrides)
    return s.AssetRiskInputs(**base)


# --------------------------------------------------------------------------
# Reserve coverage
# --------------------------------------------------------------------------
def test_reserve_coverage_healthy_128_percent():
    r = s.evaluate_reserve(_healthy_inputs())
    assert r.status == s.RESERVE_HEALTHY
    assert r.coverage_percent == Decimal('128.0000')
    assert r.reserve_difference_usd == Decimal('28000000.00')
    assert r.risk_score == 0


def test_reserve_coverage_is_decimal_exact_not_float():
    # 1/3 style ratio must not accumulate binary-float error.
    r = s.evaluate_reserve(_healthy_inputs(reserve_value_usd=Decimal('100'), liability_value_usd=Decimal('300')))
    assert r.status == s.RESERVE_CRITICAL
    assert r.coverage_ratio == Decimal('0.333333')
    assert isinstance(r.coverage_percent, Decimal)


def test_reserve_shortfall_is_critical_and_high_dimension_score():
    r = s.evaluate_reserve(_healthy_inputs(reserve_value_usd=Decimal('80'), liability_value_usd=Decimal('100')))
    assert r.status == s.RESERVE_CRITICAL
    assert r.risk_score >= 80


def test_reserve_warning_band_just_below_minimum():
    r = s.evaluate_reserve(_healthy_inputs(reserve_value_usd=Decimal('99'), liability_value_usd=Decimal('100')))
    assert r.status == s.RESERVE_WARNING
    assert 40 <= r.risk_score < 80


def test_missing_reserve_feed_is_insufficient_not_zero():
    r = s.evaluate_reserve(_healthy_inputs(reserve_feed_configured=False, reserve_verified=False))
    assert r.status == s.RESERVE_INSUFFICIENT
    assert r.risk_score >= 60  # missing evidence RAISES risk


def test_stale_reserve_feed_is_insufficient():
    r = s.evaluate_reserve(_healthy_inputs(reserve_age_seconds=999999, reserve_stale_seconds=3600))
    assert r.status == s.RESERVE_INSUFFICIENT
    assert 'stale' in r.reason.lower()


def test_over_collateralization_is_flagged_not_auto_healthy():
    r = s.evaluate_reserve(_healthy_inputs(reserve_value_usd=Decimal('300'), liability_value_usd=Decimal('100'), over_collateralization_ratio=Decimal('2.0')))
    assert r.status == s.RESERVE_OVER_COLLATERALIZED
    assert r.risk_score > 0


def test_reserve_not_required_is_neutral():
    r = s.evaluate_reserve(_healthy_inputs(reserve_required=False))
    assert r.status == s.RESERVE_NOT_REQUIRED
    assert r.risk_score == 0


# --------------------------------------------------------------------------
# Market deviation
# --------------------------------------------------------------------------
def test_market_within_baseline_is_normal():
    r = s.evaluate_market(_healthy_inputs())
    assert r.status == s.MARKET_NORMAL
    assert r.risk_score < 30


def test_market_medium_deviation():
    # 8% deviation but a moderate z-score (1.6) so it stays medium, not high.
    r = s.evaluate_market(_healthy_inputs(price_usd=Decimal('1.08'), baseline_30d=Decimal('1.00'), price_stddev_30d=Decimal('0.05')))
    assert r.status == s.MARKET_MEDIUM
    assert r.deviation_30d_percent == Decimal('8.0000')


def test_market_high_deviation():
    r = s.evaluate_market(_healthy_inputs(price_usd=Decimal('1.30'), baseline_30d=Decimal('1.00'), price_stddev_30d=Decimal('0.02')))
    assert r.status == s.MARKET_HIGH


def test_market_critical_deviation_with_irregularity():
    r = s.evaluate_market(_healthy_inputs(price_usd=Decimal('1.40'), baseline_30d=Decimal('1.00'), price_stddev_30d=Decimal('0.02'), has_reserve_or_minting_irregularity=True))
    assert r.status == s.MARKET_CRITICAL


def test_new_asset_insufficient_history_is_baseline_learning():
    r = s.evaluate_market(_healthy_inputs(price_sample_count=2, min_baseline_samples=5))
    assert r.status == s.MARKET_BASELINE_LEARNING
    assert r.risk_score < 20  # never flagged anomalous


def test_no_price_source_is_uncertain_not_healthy():
    r = s.evaluate_market(_healthy_inputs(price_source_configured=False))
    assert r.status == s.MARKET_NO_PRICE
    assert r.risk_score > 0


def test_oracle_disagreement_flagged_high():
    r = s.evaluate_market(_healthy_inputs(secondary_price_usd=Decimal('1.10'), oracle_disagreement_percent=Decimal('2')))
    assert r.status == s.MARKET_HIGH
    assert 'disagreement' in r.reason.lower()


# --------------------------------------------------------------------------
# Monitoring coverage
# --------------------------------------------------------------------------
def test_monitoring_coverage_full():
    r = s.evaluate_monitoring(_healthy_inputs())
    assert r.coverage_percent == Decimal('100.00')
    assert r.risk_score == 0
    assert r.health == s.HEALTH_HEALTHY


def test_monitoring_coverage_partial():
    inp = _healthy_inputs(monitoring_controls=[('target', True, True), ('recent_telemetry', True, False), ('price_source', True, False), ('reserve_feed', True, True)])
    r = s.evaluate_monitoring(inp)
    assert r.coverage_percent == Decimal('50.00')
    assert r.risk_score == 50
    assert set(r.missing_controls) == {'recent_telemetry', 'price_source'}


def test_monitoring_no_target_is_not_configured():
    inp = _healthy_inputs(has_monitoring_target=False, monitoring_controls=[('target', True, False)])
    r = s.evaluate_monitoring(inp)
    assert r.health == s.HEALTH_NOT_CONFIGURED


# --------------------------------------------------------------------------
# Composite score, thresholds, confidence
# --------------------------------------------------------------------------
def test_composite_healthy_is_low_with_full_confidence():
    result = s.compute_asset_risk(_healthy_inputs())
    assert result.risk_level == 'low'
    assert result.risk_score <= 20
    assert result.confidence >= 0.9
    assert result.score_version == 'asset-risk-v1'
    # Weighted contributions sum-consistent with dimension scores.
    assert len(result.dimensions) == 6
    assert abs(sum(d.weight for d in result.dimensions) - Decimal('1')) < Decimal('0.001')


def test_composite_clamped_0_100_across_extremes():
    worst = s.AssetRiskInputs(
        reserve_required=True, reserve_feed_configured=True, reserve_verified=True,
        reserve_value_usd=Decimal('1'), liability_value_usd=Decimal('100'), reserve_age_seconds=10,
        price_source_configured=True, price_usd=Decimal('2'), baseline_30d=Decimal('1'), price_stddev_30d=Decimal('0.001'),
        price_sample_count=40, has_reserve_or_minting_irregularity=True,
        monitoring_controls=[('t', True, False), ('p', True, False)], has_monitoring_target=False,
        governance_signals=['upgradeable_proxy', 'unverified_implementation', 'mint_authority'],
        recent_high_severity_findings=3,
    )
    result = s.compute_asset_risk(worst)
    assert 0 <= result.risk_score <= 100
    assert result.risk_level == 'critical'


def test_risk_level_thresholds():
    assert s.risk_level_for_score(0) == 'low'
    assert s.risk_level_for_score(29) == 'low'
    assert s.risk_level_for_score(30) == 'medium'
    assert s.risk_level_for_score(59) == 'medium'
    assert s.risk_level_for_score(60) == 'high'
    assert s.risk_level_for_score(79) == 'high'
    assert s.risk_level_for_score(80) == 'critical'
    assert s.risk_level_for_score(100) == 'critical'


def test_confidence_drops_with_missing_evidence_but_risk_does_not_zero():
    inp = s.AssetRiskInputs(
        reserve_required=True, reserve_feed_configured=False,
        price_source_configured=False,
        monitoring_controls=[('target', True, False)], has_monitoring_target=False,
        contract_discovery_failed=True,
    )
    result = s.compute_asset_risk(inp)
    assert result.confidence < 0.6  # thin evidence -> low confidence
    assert result.risk_score >= 40  # but risk is NOT silently zero
    assert result.risk_level in ('medium', 'high', 'critical')


def test_reserve_shortfall_floors_composite_to_critical():
    inp = _healthy_inputs(reserve_value_usd=Decimal('70'), liability_value_usd=Decimal('100'))
    result = s.compute_asset_risk(inp)
    assert result.risk_level == 'critical'
    assert result.risk_score >= 80


# --------------------------------------------------------------------------
# Liability helper (Decimal-safe, base-unit scaling)
# --------------------------------------------------------------------------
def test_on_chain_liability_scales_by_decimals():
    # 100,000,000 tokens with 6 decimals at $1.00 = $100,000,000.
    liab = s.compute_on_chain_liability_usd('100000000000000', 6, Decimal('1.00'))
    assert liab == Decimal('100000000.00')


def test_on_chain_liability_missing_inputs_returns_none():
    assert s.compute_on_chain_liability_usd(None, 18, Decimal('1')) is None
    assert s.compute_on_chain_liability_usd('1000', 18, None) is None


def test_on_chain_liability_uses_no_float():
    # A price that is not representable exactly in binary float must stay exact.
    liab = s.compute_on_chain_liability_usd('1000000000000000000', 18, Decimal('0.10'))
    assert liab == Decimal('0.10')
