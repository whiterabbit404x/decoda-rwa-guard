"""Unit tests for the deterministic source-health engine (Screen 4).

Covers health-score calculation, threshold boundaries, block-lag / oracle-heartbeat
classification, provider ranking, failover eligibility, hysteresis, cooldown
enforcement, route restoration and AI-recommendation validation. The engine is
pure, so these tests need no database, network or LLM.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta, timezone

from services.api.app import monitoring_health_engine as e

NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Threshold boundary classification.
# ---------------------------------------------------------------------------
def test_block_lag_boundaries_match_spec() -> None:
    assert e.classify_block_lag(0) == e.HEALTH_HEALTHY
    assert e.classify_block_lag(2) == e.HEALTH_HEALTHY       # <= 2 healthy
    assert e.classify_block_lag(3) == e.HEALTH_WARNING       # 3..5 warning
    assert e.classify_block_lag(5) == e.HEALTH_WARNING
    assert e.classify_block_lag(6) == e.HEALTH_CRITICAL      # > 5 critical
    assert e.classify_block_lag(None) == e.HEALTH_UNKNOWN    # unmeasured != healthy


def test_error_rate_boundaries_match_spec() -> None:
    assert e.classify_error_rate(0.009) == e.HEALTH_HEALTHY  # < 1%
    assert e.classify_error_rate(0.01) == e.HEALTH_WARNING   # exactly 1% -> warning
    assert e.classify_error_rate(0.05) == e.HEALTH_WARNING   # 5% inclusive warning
    assert e.classify_error_rate(0.0501) == e.HEALTH_CRITICAL
    assert e.classify_error_rate(None) == e.HEALTH_UNKNOWN


def test_timeout_rate_boundaries_match_spec() -> None:
    assert e.classify_timeout_rate(0.0) == e.HEALTH_HEALTHY
    assert e.classify_timeout_rate(0.02) == e.HEALTH_WARNING
    assert e.classify_timeout_rate(0.10) == e.HEALTH_CRITICAL


def test_latency_boundaries_match_spec() -> None:
    assert e.classify_latency(699) == e.HEALTH_HEALTHY       # < 700
    assert e.classify_latency(700) == e.HEALTH_WARNING       # 700..1500 warning
    assert e.classify_latency(1500) == e.HEALTH_WARNING
    assert e.classify_latency(1501) == e.HEALTH_CRITICAL     # > 1500 critical


def test_consecutive_failures_classification() -> None:
    assert e.classify_consecutive_failures(0) == e.HEALTH_HEALTHY
    assert e.classify_consecutive_failures(1) == e.HEALTH_WARNING
    assert e.classify_consecutive_failures(2) == e.HEALTH_WARNING
    assert e.classify_consecutive_failures(3) == e.HEALTH_CRITICAL   # 3 consecutive -> critical


def test_availability_classification() -> None:
    assert e.classify_availability(0.999) == e.HEALTH_HEALTHY
    assert e.classify_availability(0.99) == e.HEALTH_HEALTHY
    assert e.classify_availability(0.97) == e.HEALTH_WARNING
    assert e.classify_availability(0.90) == e.HEALTH_CRITICAL


def test_heartbeat_missed_classification() -> None:
    assert e.classify_heartbeat(0) == e.HEALTH_HEALTHY
    assert e.classify_heartbeat(1) == e.HEALTH_WARNING          # one missed -> warning
    assert e.classify_heartbeat(2) == e.HEALTH_CRITICAL         # two missed -> critical


def test_telemetry_recency_window() -> None:
    t = e.DEFAULT_THRESHOLDS
    fresh = NOW - timedelta(seconds=t.telemetry_freshness_seconds - 1)
    stale = NOW - timedelta(seconds=t.telemetry_freshness_seconds + 60)
    silent = NOW - timedelta(seconds=t.telemetry_freshness_seconds * 3)
    assert e.classify_telemetry_recency(fresh, now=NOW) == e.HEALTH_HEALTHY
    assert e.classify_telemetry_recency(stale, now=NOW) == e.HEALTH_WARNING
    assert e.classify_telemetry_recency(silent, now=NOW) == e.HEALTH_CRITICAL
    assert e.classify_telemetry_recency(None, now=NOW) == e.HEALTH_UNKNOWN


def test_thresholds_are_configurable() -> None:
    custom = e.HealthThresholds.from_overrides({'block_lag_healthy_max': 5, 'p95_latency_healthy_max_ms': 300})
    assert custom.block_lag_healthy_max == 5
    assert custom.p95_latency_healthy_max_ms == 300.0
    assert e.classify_block_lag(4, custom) == e.HEALTH_HEALTHY   # 4 <= 5 now healthy
    assert e.classify_latency(400, custom) == e.HEALTH_WARNING   # 400 > 300 now warning


def test_thresholds_from_overrides_ignores_bad_values() -> None:
    # Garbage values fall back to the spec default rather than crashing.
    custom = e.HealthThresholds.from_overrides({'block_lag_healthy_max': 'not-a-number', 'unknown_key': 9})
    assert custom.block_lag_healthy_max == e.DEFAULT_THRESHOLDS.block_lag_healthy_max


# ---------------------------------------------------------------------------
# worse_status helper (fail-closed severity fold).
# ---------------------------------------------------------------------------
def test_worse_status_ignores_unknown_but_never_returns_healthy_when_all_unknown() -> None:
    assert e.worse_status(e.HEALTH_HEALTHY, e.HEALTH_WARNING) == e.HEALTH_WARNING
    assert e.worse_status(e.HEALTH_WARNING, e.HEALTH_CRITICAL) == e.HEALTH_CRITICAL
    assert e.worse_status(e.HEALTH_HEALTHY, e.HEALTH_UNKNOWN) == e.HEALTH_HEALTHY
    assert e.worse_status(e.HEALTH_UNKNOWN, e.HEALTH_UNKNOWN) == e.HEALTH_UNKNOWN
    assert e.worse_status() == e.HEALTH_UNKNOWN


# ---------------------------------------------------------------------------
# Health score / assessment.
# ---------------------------------------------------------------------------
def _healthy_metrics(**over) -> e.SourceMetrics:
    base = dict(
        availability=1.0, error_rate=0.0, timeout_rate=0.0, p95_latency_ms=120, block_lag=0,
        consecutive_failures=0, heartbeat_missed=0, heartbeat_present=True,
        last_telemetry_at=NOW - timedelta(seconds=60),
    )
    base.update(over)
    return e.SourceMetrics(**base)


def test_fully_healthy_source_scores_high() -> None:
    a = e.assess_source_health(_healthy_metrics(), now=NOW)
    assert a.status == e.HEALTH_HEALTHY
    assert a.score is not None and a.score >= 95
    assert a.triggered_rules == []
    assert a.has_live_evidence is True


def test_critical_source_scores_low_and_lists_rules() -> None:
    m = _healthy_metrics(availability=0.90, error_rate=0.08, p95_latency_ms=1800, block_lag=8, consecutive_failures=3)
    a = e.assess_source_health(m, now=NOW)
    assert a.status == e.HEALTH_CRITICAL
    assert a.score is not None and a.score < 60
    assert 'error_rate.critical' in a.triggered_rules
    assert 'block_lag.critical' in a.triggered_rules


def test_score_is_monotonic_worse_metrics_lower_score() -> None:
    good = e.assess_source_health(_healthy_metrics(p95_latency_ms=100), now=NOW).score
    mid = e.assess_source_health(_healthy_metrics(p95_latency_ms=1000), now=NOW).score
    bad = e.assess_source_health(_healthy_metrics(p95_latency_ms=3000), now=NOW).score
    assert good is not None and mid is not None and bad is not None
    assert good > mid > bad


def test_unavailable_endpoint_is_always_critical() -> None:
    m = _healthy_metrics(endpoint_unavailable=True)
    a = e.assess_source_health(m, now=NOW)
    assert a.status == e.HEALTH_CRITICAL
    assert 'endpoint.unavailable' in a.triggered_rules


def test_no_live_evidence_is_never_healthy() -> None:
    # Good latency/error rate measured, but no heartbeat and no telemetry.
    m = e.SourceMetrics(error_rate=0.0, p95_latency_ms=100, availability=1.0)
    a = e.assess_source_health(m, now=NOW)
    assert a.status != e.HEALTH_HEALTHY
    assert a.has_live_evidence is False
    assert 'no_live_evidence' in a.triggered_rules


def test_nothing_measured_is_unknown_not_healthy() -> None:
    a = e.assess_source_health(e.SourceMetrics(), now=NOW)
    assert a.status == e.HEALTH_UNKNOWN
    assert a.score is None
    assert a.measured_dimensions == 0


def test_assessment_as_dict_rounds_and_serialises() -> None:
    a = e.assess_source_health(_healthy_metrics(), now=NOW)
    d = a.as_dict()
    assert set(d) >= {'status', 'score', 'dimensions', 'triggered_rules', 'component_scores', 'has_live_evidence'}
    assert isinstance(d['dimensions'], dict)


# ---------------------------------------------------------------------------
# Oracle heartbeat classification.
# ---------------------------------------------------------------------------
def test_oracle_fresh_heartbeat_is_healthy() -> None:
    hb = e.OracleHeartbeat(expected_interval_seconds=60, last_update_at=NOW - timedelta(seconds=30))
    assert e.classify_oracle_heartbeat(hb, now=NOW).status == e.HEALTH_HEALTHY


def test_oracle_one_missed_is_warning_two_is_critical() -> None:
    one = e.OracleHeartbeat(expected_interval_seconds=60, last_update_at=NOW - timedelta(seconds=90))
    two = e.OracleHeartbeat(expected_interval_seconds=60, last_update_at=NOW - timedelta(seconds=150))
    assert e.classify_oracle_heartbeat(one, now=NOW).status == e.HEALTH_WARNING
    assert e.classify_oracle_heartbeat(two, now=NOW).status == e.HEALTH_CRITICAL


def test_oracle_grace_period_extends_freshness() -> None:
    hb = e.OracleHeartbeat(expected_interval_seconds=60, grace_period_seconds=60,
                           last_update_at=NOW - timedelta(seconds=90))
    # 90s old but 60s grace -> effective age 30s -> healthy.
    assert e.classify_oracle_heartbeat(hb, now=NOW).status == e.HEALTH_HEALTHY


def test_oracle_non_monotonic_round_is_warning_even_when_fresh() -> None:
    hb = e.OracleHeartbeat(expected_interval_seconds=60, last_update_at=NOW - timedelta(seconds=5),
                           latest_round=7, previous_round=7)
    res = e.classify_oracle_heartbeat(hb, now=NOW)
    assert res.status == e.HEALTH_WARNING
    assert res.reason == 'non_monotonic_round'


def test_oracle_value_deviation_escalates_to_threat_as_stale_risk_not_exploit() -> None:
    hb = e.OracleHeartbeat(expected_interval_seconds=60, last_update_at=NOW - timedelta(seconds=5),
                           deviation_pct=25.0)
    res = e.classify_oracle_heartbeat(hb, now=NOW, deviation_alert_pct=10.0)
    assert res.escalate_to_threat is True
    assert res.stale_source_risk is True
    # A fresh feed with a big value jump is a warning pending correlation, not critical/exploit.
    assert res.status == e.HEALTH_WARNING


def test_oracle_missing_update_is_critical() -> None:
    hb = e.OracleHeartbeat(expected_interval_seconds=60, last_update_at=None)
    assert e.classify_oracle_heartbeat(hb, now=NOW).status == e.HEALTH_CRITICAL


# ---------------------------------------------------------------------------
# Provider ranking.
# ---------------------------------------------------------------------------
def test_ranking_orders_healthy_before_degraded_then_by_score_lag_latency() -> None:
    healthy = e.assess_source_health(_healthy_metrics(), now=NOW)
    critical = e.assess_source_health(_healthy_metrics(error_rate=0.5, availability=0.4), now=NOW)
    candidates = [
        e.RankedProvider('critical-provider', critical, 10, 3000),
        e.RankedProvider('healthy-slow', healthy, 1, 500),
        e.RankedProvider('healthy-fast', healthy, 0, 100),
    ]
    ranked = [p.provider_id for p in e.rank_providers(candidates)]
    assert ranked == ['healthy-fast', 'healthy-slow', 'critical-provider']


def test_ranking_is_stable_and_deterministic() -> None:
    a = e.assess_source_health(_healthy_metrics(), now=NOW)
    candidates = [e.RankedProvider(f'p{i}', a, 0, 100) for i in range(5)]
    ranked = [p.provider_id for p in e.rank_providers(candidates)]
    assert ranked == ['p0', 'p1', 'p2', 'p3', 'p4']   # tie-break on id


# ---------------------------------------------------------------------------
# Failover eligibility, cooldown, hysteresis.
# ---------------------------------------------------------------------------
def _failover_ctx(**over) -> e.FailoverContext:
    healthy_fb = e.assess_source_health(_healthy_metrics(), now=NOW)
    critical_primary = e.assess_source_health(
        _healthy_metrics(availability=0.4, error_rate=0.5, p95_latency_ms=4000, block_lag=12, consecutive_failures=3),
        now=NOW,
    )
    base = dict(
        auto_routing_enabled=True, fallback_configured=True, fallback_approved=True,
        fallback_same_chain=True, fallback_supports_methods=True, fallback_recent_connectivity_ok=True,
        fallback_assessment=healthy_fb, fallback_block_lag=0, primary_assessment=critical_primary,
        primary_consecutive_critical=3, primary_unavailable_seconds=0, telemetry_breached=False,
        seconds_since_last_route_change=None,
    )
    base.update(over)
    return e.FailoverContext(**base)


def test_failover_authorised_when_all_guards_pass() -> None:
    d = e.evaluate_failover(_failover_ctx())
    assert d.should_failover is True
    assert d.trigger == 'consecutive_critical_probes'
    assert d.blocked_reasons == []


def test_failover_blocked_when_auto_routing_disabled() -> None:
    d = e.evaluate_failover(_failover_ctx(auto_routing_enabled=False))
    assert d.should_failover is False
    assert 'auto_routing_disabled' in d.blocked_reasons


def test_failover_blocked_when_no_fallback_configured() -> None:
    d = e.evaluate_failover(_failover_ctx(fallback_configured=False, fallback_assessment=None, fallback_block_lag=None))
    assert d.should_failover is False
    assert 'no_fallback_configured' in d.blocked_reasons


def test_failover_blocked_when_fallback_not_approved() -> None:
    d = e.evaluate_failover(_failover_ctx(fallback_approved=False))
    assert 'fallback_not_approved' in d.blocked_reasons
    assert d.should_failover is False


def test_failover_blocked_when_fallback_unhealthy_or_lagging() -> None:
    weak = e.assess_source_health(_healthy_metrics(error_rate=0.5, availability=0.4), now=NOW)
    d = e.evaluate_failover(_failover_ctx(fallback_assessment=weak, fallback_block_lag=9))
    assert d.should_failover is False
    assert 'fallback_score_below_threshold' in d.blocked_reasons
    assert 'fallback_block_lag_too_high' in d.blocked_reasons


def test_failover_blocked_by_chain_or_method_mismatch() -> None:
    d1 = e.evaluate_failover(_failover_ctx(fallback_same_chain=False))
    d2 = e.evaluate_failover(_failover_ctx(fallback_supports_methods=False))
    assert 'fallback_chain_mismatch' in d1.blocked_reasons
    assert 'fallback_missing_rpc_methods' in d2.blocked_reasons


def test_cooldown_prevents_flapping() -> None:
    # A route change 60s ago is inside the default 300s cooldown.
    d = e.evaluate_failover(_failover_ctx(seconds_since_last_route_change=60))
    assert d.cooldown_active is True
    assert d.should_failover is False
    assert 'cooldown_active' in d.blocked_reasons


def test_cooldown_elapsed_allows_failover() -> None:
    d = e.evaluate_failover(_failover_ctx(seconds_since_last_route_change=600))
    assert d.cooldown_active is False
    assert d.should_failover is True


def test_failover_not_triggered_until_primary_breaches() -> None:
    healthy_primary = e.assess_source_health(_healthy_metrics(), now=NOW)
    d = e.evaluate_failover(_failover_ctx(
        primary_assessment=healthy_primary, primary_consecutive_critical=0, telemetry_breached=False,
    ))
    assert d.should_failover is False
    assert 'primary_not_breached' in d.blocked_reasons


def test_failover_trigger_endpoint_unavailable() -> None:
    healthy_primary = e.assess_source_health(_healthy_metrics(), now=NOW)
    d = e.evaluate_failover(_failover_ctx(
        primary_assessment=healthy_primary, primary_consecutive_critical=0, primary_unavailable_seconds=45,
    ))
    assert d.should_failover is True
    assert d.trigger == 'endpoint_unavailable'


def test_failover_trigger_telemetry_breach() -> None:
    healthy_primary = e.assess_source_health(_healthy_metrics(), now=NOW)
    d = e.evaluate_failover(_failover_ctx(
        primary_assessment=healthy_primary, primary_consecutive_critical=0, telemetry_breached=True,
    ))
    assert d.should_failover is True
    assert d.trigger == 'telemetry_freshness_breached'


# ---------------------------------------------------------------------------
# Route restoration (recovery period + hysteresis).
# ---------------------------------------------------------------------------
def test_route_restoration_requires_sustained_recovery() -> None:
    healthy = e.assess_source_health(_healthy_metrics(), now=NOW)
    ready = e.RestorationContext(
        auto_routing_enabled=True, original_primary_assessment=healthy,
        original_primary_healthy_seconds=1000, seconds_since_last_route_change=1000,
    )
    assert e.evaluate_route_restoration(ready).should_failover is True


def test_route_restoration_blocked_before_recovery_period() -> None:
    healthy = e.assess_source_health(_healthy_metrics(), now=NOW)
    too_soon = e.RestorationContext(
        auto_routing_enabled=True, original_primary_assessment=healthy,
        original_primary_healthy_seconds=100, seconds_since_last_route_change=1000,
    )
    d = e.evaluate_route_restoration(too_soon)
    assert d.should_failover is False
    assert 'recovery_period_not_elapsed' in d.blocked_reasons


def test_route_restoration_blocked_when_primary_still_unhealthy() -> None:
    unhealthy = e.assess_source_health(_healthy_metrics(error_rate=0.5, availability=0.4), now=NOW)
    ctx = e.RestorationContext(
        auto_routing_enabled=True, original_primary_assessment=unhealthy,
        original_primary_healthy_seconds=5000, seconds_since_last_route_change=5000,
    )
    d = e.evaluate_route_restoration(ctx)
    assert d.should_failover is False
    assert 'original_primary_not_healthy' in d.blocked_reasons


# ---------------------------------------------------------------------------
# AI recommendation validation.
# ---------------------------------------------------------------------------
def test_valid_recommendation_passes_and_flags_approval() -> None:
    res = e.validate_ai_recommendation(
        {'recommended_action': 'switch_primary_provider', 'provider_id': 'p1',
         'supporting_record_ids': ['r1', 'r2'], 'confidence': 0.9, 'risk_level': 'high'},
        known_provider_ids=['p1', 'p2'], known_record_ids=['r1', 'r2', 'r3'],
    )
    assert res.valid is True
    assert res.approval_required is True   # switching primary always needs approval


def test_recommendation_rejected_for_unknown_provider_and_action() -> None:
    res = e.validate_ai_recommendation(
        {'recommended_action': 'launch_missiles', 'provider_id': 'ghost',
         'supporting_record_ids': ['r1'], 'confidence': 0.5, 'risk_level': 'low'},
        known_provider_ids=['p1'], known_record_ids=['r1'],
    )
    assert res.valid is False
    assert any(r.startswith('unsupported_action') for r in res.rejected_reasons)
    assert any(r.startswith('unknown_provider') for r in res.rejected_reasons)


def test_recommendation_rejected_for_hallucinated_evidence() -> None:
    res = e.validate_ai_recommendation(
        {'recommended_action': 'no_action', 'supporting_record_ids': ['does-not-exist'], 'confidence': 0.5},
        known_provider_ids=[], known_record_ids=['r1'],
    )
    assert res.valid is False
    assert any(r.startswith('unknown_record') for r in res.rejected_reasons)


def test_recommendation_rejected_for_out_of_range_confidence() -> None:
    res = e.validate_ai_recommendation(
        {'recommended_action': 'no_action', 'supporting_record_ids': [], 'confidence': 1.5},
        known_provider_ids=[], known_record_ids=[],
    )
    assert res.valid is False
    assert 'confidence_out_of_range' in res.rejected_reasons


def test_low_risk_auto_action_does_not_require_approval() -> None:
    res = e.validate_ai_recommendation(
        {'recommended_action': 'increase_polling_interval', 'supporting_record_ids': [], 'confidence': 0.6,
         'risk_level': 'low'},
        known_provider_ids=[], known_record_ids=[],
    )
    assert res.valid is True
    assert res.approval_required is False
