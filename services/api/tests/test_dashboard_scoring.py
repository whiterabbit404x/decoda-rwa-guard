"""Deterministic dashboard scoring — risk + system health.

Covers the required backend scoring guarantees (task spec 1-8, 13):

  1. Risk score stays within 0-100.
  2. A critical incident increases or preserves risk (never lowers it).
  3. Resolving high-severity conditions lowers risk.
  4. Duplicate clustered alerts do not multiply risk linearly.
  5. A stale target lowers system health.
  6. A failed required worker lowers system health.
  7. Healthy fallback routing prevents an unnecessary critical status.
  8. No configured targets never returns a false "100% healthy".
"""

from __future__ import annotations

from services.api.app.dashboard_scoring import (
    AlertCluster,
    HealthInputs,
    RiskInputs,
    compute_health_score,
    compute_risk_score,
    provider_degradation_factor,
)


# --------------------------------------------------------------------------
# Risk score
# --------------------------------------------------------------------------


def test_risk_score_within_bounds_across_extremes():
    empty = compute_risk_score(RiskInputs())
    assert 0 <= empty.score <= 100
    assert empty.score == 0
    assert empty.band == 'low'

    maxed = compute_risk_score(RiskInputs(
        incident_severities=['critical'] * 10,
        alert_clusters=[AlertCluster(severity='critical', count=50, asset_criticality='critical')] * 10,
        anomaly_rate_current=1000, anomaly_rate_baseline=1,
        affected_asset_criticalities=['critical'] * 20,
        monitoring_degradation_factor=1.0,
        pending_control_gap_count=99,
    ))
    assert 0 <= maxed.score <= 100
    assert maxed.band in {'high', 'critical'}


def test_critical_incident_never_lowers_risk():
    base = compute_risk_score(RiskInputs())
    one = compute_risk_score(RiskInputs(incident_severities=['critical']))
    two = compute_risk_score(RiskInputs(incident_severities=['critical', 'critical']))
    assert one.score >= base.score
    assert two.score >= one.score

    # Adding a critical incident on top of existing alerts also never lowers it.
    with_alerts = RiskInputs(alert_clusters=[AlertCluster(severity='high', count=2)])
    before = compute_risk_score(with_alerts).score
    with_alerts.incident_severities = ['critical']
    after = compute_risk_score(with_alerts).score
    assert after >= before


def test_resolving_high_severity_conditions_lowers_risk():
    loaded = compute_risk_score(RiskInputs(
        incident_severities=['critical', 'high'],
        alert_clusters=[AlertCluster(severity='high', count=3, asset_criticality='high')],
    ))
    # Resolve the incidents (they leave the active set).
    resolved = compute_risk_score(RiskInputs(
        incident_severities=[],
        alert_clusters=[AlertCluster(severity='high', count=3, asset_criticality='high')],
    ))
    assert resolved.score < loaded.score
    # Resolving everything returns to zero.
    assert compute_risk_score(RiskInputs()).score == 0


def test_duplicate_clustered_alerts_do_not_multiply_risk_linearly():
    single = compute_risk_score(RiskInputs(alert_clusters=[AlertCluster(severity='high', count=1)]))
    many = compute_risk_score(RiskInputs(alert_clusters=[AlertCluster(severity='high', count=100)]))
    single_alert = single.components[1].points
    many_alert = many.components[1].points
    # More alerts in one cluster nudge risk up, but far less than linearly and
    # never past the component cap.
    assert many_alert > single_alert
    assert many_alert < single_alert * 3
    assert many_alert <= 25.0

    # Ten separate clusters of one high alert saturate (noisy-or) rather than
    # summing to 10x a single cluster.
    ten_clusters = compute_risk_score(RiskInputs(
        alert_clusters=[AlertCluster(severity='high', count=1, key=f'c{i}') for i in range(10)]
    ))
    assert ten_clusters.components[1].points < single_alert * 10
    assert ten_clusters.components[1].points <= 25.0


def test_top_risk_drivers_match_component_contributions():
    result = compute_risk_score(RiskInputs(
        incident_severities=['critical'],
        alert_clusters=[AlertCluster(severity='high', count=2)],
    ))
    drivers = result.top_risk_drivers
    assert drivers, 'expected at least one driver'
    # Percent sums to ~100 across contributing drivers.
    assert abs(sum(d['percent'] for d in drivers) - 100) <= 2
    # Drivers are sorted by contribution (descending points).
    points = [d['points'] for d in drivers]
    assert points == sorted(points, reverse=True)
    # Every driver maps to a real component key.
    component_keys = {c.key for c in result.components}
    assert all(d['key'] in component_keys for d in drivers)


def test_asset_criticality_weights_alert_risk():
    low_crit = compute_risk_score(RiskInputs(alert_clusters=[AlertCluster(severity='high', count=1, asset_criticality='low')]))
    high_crit = compute_risk_score(RiskInputs(alert_clusters=[AlertCluster(severity='high', count=1, asset_criticality='critical')]))
    assert high_crit.components[1].points > low_crit.components[1].points


def test_anomaly_component_uses_baseline_when_available():
    # 3x baseline saturates the anomaly component; equal to baseline contributes 0.
    at_baseline = compute_risk_score(RiskInputs(anomaly_rate_current=10, anomaly_rate_baseline=10))
    spiking = compute_risk_score(RiskInputs(anomaly_rate_current=30, anomaly_rate_baseline=10))
    assert at_baseline.components[2].points == 0
    assert spiking.components[2].points > 0
    assert at_baseline.evidence_quality == 'complete'
    # No baseline => partial evidence quality.
    no_base = compute_risk_score(RiskInputs(anomaly_count_24h=20))
    assert no_base.evidence_quality == 'partial'


# --------------------------------------------------------------------------
# System health score
# --------------------------------------------------------------------------


def _healthy_inputs(**over) -> HealthInputs:
    base = dict(
        configured_target_count=3,
        reporting_target_count=3,
        stale_target_count=0,
        telemetry_freshness='fresh',
        required_worker_count=1,
        healthy_worker_count=1,
        providers=[{'name': 'primary', 'primary_healthy': True, 'fallback_healthy': True}],
        infra_components=[{'name': 'database', 'healthy': True}],
    )
    base.update(over)
    return HealthInputs(**base)


def test_health_score_within_bounds_and_healthy_baseline():
    result = compute_health_score(_healthy_inputs())
    assert 0 <= result.score <= 100
    assert result.status == 'healthy'
    assert result.score >= 90


def test_stale_target_lowers_system_health():
    healthy = compute_health_score(_healthy_inputs())
    stale = compute_health_score(_healthy_inputs(
        stale_target_count=2, telemetry_freshness='stale',
        stale_target_refs=[{'id': 't1', 'label': 'Datto USDC'}],
    ))
    assert stale.score < healthy.score
    assert any('stale' in i.message.lower() for i in stale.insights)


def test_failed_required_worker_lowers_system_health():
    healthy = compute_health_score(_healthy_inputs())
    worker_down = compute_health_score(_healthy_inputs(
        required_worker_count=2, healthy_worker_count=1,
        missing_worker_refs=[{'id': 'w1', 'label': 'monitoring worker'}],
    ))
    assert worker_down.score < healthy.score
    assert any('heartbeat' in i.message.lower() for i in worker_down.insights)


def test_healthy_fallback_routing_prevents_unnecessary_critical():
    # Primary down, fallback up: degraded contribution (0.6), not zero. With the
    # rest of the stack healthy the workspace must NOT be marked critical.
    fallback = compute_health_score(_healthy_inputs(
        providers=[{'name': 'primary', 'primary_healthy': False, 'fallback_healthy': True}],
    ))
    assert fallback.status != 'critical'
    # Both primary and fallback down is a severe penalty (much lower score).
    both_down = compute_health_score(_healthy_inputs(
        providers=[{'name': 'primary', 'primary_healthy': False, 'fallback_healthy': False}],
    ))
    assert both_down.score < fallback.score


def test_no_configured_targets_is_not_false_healthy():
    result = compute_health_score(HealthInputs(configured_target_count=0))
    assert result.status == 'not_configured'
    assert result.score < 90
    assert 'no monitoring targets' in result.summary.lower()


def test_health_summary_reports_specific_degradation_reasons():
    result = compute_health_score(_healthy_inputs(
        stale_target_count=1, telemetry_freshness='stale',
        providers=[{'name': 'primary', 'primary_healthy': False, 'fallback_healthy': True}],
    ))
    assert result.status in {'degraded', 'at_risk', 'critical'}
    # The one-line reason names the concrete problem(s).
    assert 'provider' in result.summary.lower() or 'target' in result.summary.lower() or 'telemetry' in result.summary.lower()


def test_health_insights_carry_severity_and_source():
    result = compute_health_score(_healthy_inputs(
        stale_target_count=1, telemetry_freshness='stale',
        stale_target_refs=[{'id': 't-123', 'label': 'Datto USDC', 'occurred_at': '2026-07-23T10:00:00Z'}],
    ))
    stale_insight = next((i for i in result.insights if i.source_type == 'monitoring_target'), None)
    assert stale_insight is not None
    assert stale_insight.severity in {'warning', 'critical', 'info'}
    assert stale_insight.source_id == 't-123'


def test_provider_degradation_factor_is_fallback_aware():
    healthy = provider_degradation_factor(
        providers=[{'name': 'p', 'primary_healthy': True}], configured_target_count=3, stale_target_count=0,
    )
    fallback = provider_degradation_factor(
        providers=[{'name': 'p', 'primary_healthy': False, 'fallback_healthy': True}], configured_target_count=3, stale_target_count=0,
    )
    both_down = provider_degradation_factor(
        providers=[{'name': 'p', 'primary_healthy': False, 'fallback_healthy': False}], configured_target_count=3, stale_target_count=0,
    )
    assert healthy == 0.0
    assert 0 < fallback < both_down
    assert both_down > fallback
