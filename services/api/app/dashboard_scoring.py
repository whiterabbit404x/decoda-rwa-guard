"""Deterministic dashboard risk and system-health scoring.

This module is the canonical, *explainable* scoring engine behind Screen 2
(Dashboard / Executive Summary). It is intentionally a set of **pure
functions**: they take already-aggregated, workspace-scoped facts and return a
numeric score plus the per-component contributions that produced it. The LLM
Dashboard Co-Pilot never computes or edits these numbers — it only narrates
them. The "Top Risk Drivers" and "System Health Insights" surfaced in the UI
are derived directly from the component contributions returned here.

Design rules (see CLAUDE.md truthfulness rules):

* No configured monitoring must never read as "100% healthy" — the health
  scorer returns an explicit ``not_configured`` status in that case.
* A new critical incident must never *lower* the risk score. Component
  combiners are monotonic in severity/volume.
* Duplicate alerts inside one cluster must not inflate risk linearly. Alert
  pressure is combined per-cluster with a saturating "noisy-or", so N copies of
  the same finding saturate instead of adding N times.
* Every number is reproducible from the inputs and is unit-tested.

--------------------------------------------------------------------------
GLOBAL RISK SCORE (0-100)
--------------------------------------------------------------------------
Six weighted components, summed then clamped to [0, 100]:

    incident_pressure        <= 25   active incidents weighted by severity
    alert_pressure           <= 25   active alert clusters weighted by
                                      severity * affected-asset criticality
    anomaly_rate             <= 20   current anomaly rate vs historical baseline
    asset_exposure           <= 15   criticality/exposure of affected assets
    monitoring_degradation   <= 10   provider/target degradation (fallback aware)
    pending_controls         <=  5   pending high-impact recommendations / gaps

Bands: 0-24 low · 25-49 moderate · 50-74 high · 75-100 critical

Each severity/volume component uses a *noisy-or* combiner

    points = cap * (1 - prod_i (1 - w_i))

where ``w_i`` in [0, 1) is the per-item weight. This is monotonic (adding any
positive-weight item never decreases the score) and saturating (duplicates
inside a cluster cannot push a single component past its cap or add linearly).

--------------------------------------------------------------------------
GLOBAL SYSTEM HEALTH SCORE (0-100)
--------------------------------------------------------------------------
Six weighted components, each a 0..1 sub-score:

    telemetry_continuity     30%   ingestion freshness + stale-target fraction
    worker_heartbeats        20%   fraction of required workers beating
    provider_health          20%   RPC/oracle health, primary+fallback aware
    detection_pipeline       15%   detection freshness / queue / worker errors
    infrastructure           10%   database, redis, storage, evidence service
    critical_pressure         5%   inverse of critical incident/alert pressure

Status: 90-100 healthy · 75-89 degraded · 50-74 at_risk · 0-49 critical, plus
an explicit ``not_configured`` when no monitoring targets exist.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------

RISK_BANDS = (
    (0, 24, 'low'),
    (25, 49, 'moderate'),
    (50, 74, 'high'),
    (75, 100, 'critical'),
)

HEALTH_STATES = (
    (90, 100, 'healthy'),
    (75, 89, 'degraded'),
    (50, 74, 'at_risk'),
    (0, 49, 'critical'),
)

# Per-incident / per-alert severity weights for the noisy-or combiners. These
# are deliberately < 1 so no single item saturates a component on its own, and
# ordered so critical > high > medium > low (monotonic in severity).
INCIDENT_SEVERITY_WEIGHT = {'critical': 0.85, 'high': 0.55, 'medium': 0.30, 'low': 0.12}
ALERT_SEVERITY_WEIGHT = {'critical': 0.80, 'high': 0.50, 'medium': 0.25, 'low': 0.10}

# Multiplier applied to an alert cluster's weight based on the criticality of
# the asset it affects. Missing/unknown criticality is treated as medium.
ASSET_CRITICALITY_MULTIPLIER = {
    'critical': 1.0,
    'high': 0.9,
    'medium': 0.75,
    'low': 0.6,
    'unknown': 0.75,
}

# Weight of an affected asset (by risk tier) in the exposure component.
ASSET_EXPOSURE_WEIGHT = {'critical': 0.7, 'high': 0.5, 'medium': 0.3, 'low': 0.15, 'unknown': 0.3}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _normalize_severity(value: Any) -> str:
    text = str(value or '').strip().lower()
    if text in {'critical', 'crit', 'sev1', 'p1'}:
        return 'critical'
    if text in {'high', 'sev2', 'p2'}:
        return 'high'
    if text in {'medium', 'moderate', 'med', 'sev3', 'p3'}:
        return 'medium'
    if text in {'low', 'info', 'informational', 'sev4', 'p4'}:
        return 'low'
    return 'medium'


def _normalize_criticality(value: Any) -> str:
    text = str(value or '').strip().lower()
    if text in ASSET_CRITICALITY_MULTIPLIER:
        return text
    if text in {'tier1', 'tier_1', 'sensitive'}:
        return 'critical'
    if text in {'tier2', 'tier_2'}:
        return 'high'
    if text in {'tier3', 'tier_3'}:
        return 'medium'
    return 'unknown'


def _noisy_or(weights: list[float]) -> float:
    """Combine independent [0,1) weights so the result saturates toward 1.

    Monotonic: adding any positive weight never lowers the result. Saturating:
    many similar weights approach — but never exceed — 1. Returns 0 for an empty
    list.
    """
    product = 1.0
    for weight in weights:
        product *= (1.0 - _clamp(float(weight), 0.0, 0.999))
    return 1.0 - product


def _band(score: int, table: tuple) -> str:
    for low, high, label in table:
        if low <= score <= high:
            return label
    return table[-1][2]


# --------------------------------------------------------------------------
# Risk score
# --------------------------------------------------------------------------


@dataclass
class AlertCluster:
    """A deduplicated group of alerts that share a root cause / signature.

    ``count`` is informational (used for a mild volume nudge); it never
    multiplies risk linearly because clusters are combined with a noisy-or.
    """

    severity: str = 'medium'
    count: int = 1
    asset_criticality: str = 'unknown'
    key: str = ''


@dataclass
class RiskInputs:
    # Active incidents (unresolved) — either explicit severities or a count map.
    incident_severities: list[str] = field(default_factory=list)
    # Active alerts, ideally already clustered by root cause. When only raw
    # counts are available, pass ``alert_severity_counts`` instead.
    alert_clusters: list[AlertCluster] = field(default_factory=list)
    alert_severity_counts: dict[str, int] = field(default_factory=dict)
    # Anomaly rate: current window vs a historical baseline (same units, e.g.
    # anomalies per hour). ``baseline`` <= 0 means "no baseline available".
    anomaly_rate_current: float = 0.0
    anomaly_rate_baseline: float = 0.0
    anomaly_count_24h: int = 0
    # Risk tiers of the assets affected by active alerts/incidents.
    affected_asset_criticalities: list[str] = field(default_factory=list)
    # Monitoring / provider degradation, already fallback-adjusted to [0, 1]
    # where 0 = fully healthy and 1 = fully degraded. See
    # ``provider_degradation_factor``.
    monitoring_degradation_factor: float = 0.0
    # Count of pending high-impact recommendations / unresolved control gaps.
    pending_control_gap_count: int = 0

    def has_anomaly_baseline(self) -> bool:
        return self.anomaly_rate_baseline > 0


@dataclass
class ScoreComponent:
    key: str
    label: str
    points: float
    max_points: float
    detail: str = ''

    def to_dict(self) -> dict[str, Any]:
        return {
            'key': self.key,
            'label': self.label,
            'points': round(self.points, 2),
            'max_points': self.max_points,
            'detail': self.detail,
        }


@dataclass
class RiskScoreResult:
    score: int
    band: str
    components: list[ScoreComponent]
    top_risk_drivers: list[dict[str, Any]]
    evidence_quality: str  # 'complete' | 'partial'

    def to_dict(self) -> dict[str, Any]:
        return {
            'score': self.score,
            'band': self.band,
            'components': [c.to_dict() for c in self.components],
            'top_risk_drivers': self.top_risk_drivers,
            'evidence_quality': self.evidence_quality,
        }


def _incident_pressure(inputs: RiskInputs) -> ScoreComponent:
    weights = [INCIDENT_SEVERITY_WEIGHT[_normalize_severity(s)] for s in inputs.incident_severities]
    points = 25.0 * _noisy_or(weights)
    crit = sum(1 for s in inputs.incident_severities if _normalize_severity(s) == 'critical')
    high = sum(1 for s in inputs.incident_severities if _normalize_severity(s) == 'high')
    detail = f'{len(weights)} active incident(s); {crit} critical, {high} high'
    return ScoreComponent('incident_pressure', 'Active incident pressure', points, 25.0, detail)


def _resolve_alert_clusters(inputs: RiskInputs) -> list[AlertCluster]:
    if inputs.alert_clusters:
        return inputs.alert_clusters
    # Fall back to raw severity counts: treat each severity bucket as one
    # cluster so duplicate raw alerts of the same severity still saturate.
    clusters: list[AlertCluster] = []
    for severity, count in inputs.alert_severity_counts.items():
        if int(count) > 0:
            clusters.append(AlertCluster(severity=severity, count=int(count), key=f'severity:{severity}'))
    return clusters


def _alert_pressure(inputs: RiskInputs) -> ScoreComponent:
    clusters = _resolve_alert_clusters(inputs)
    weights: list[float] = []
    for cluster in clusters:
        base = ALERT_SEVERITY_WEIGHT[_normalize_severity(cluster.severity)]
        crit_mult = ASSET_CRITICALITY_MULTIPLIER[_normalize_criticality(cluster.asset_criticality)]
        # Mild sub-linear volume nudge: a cluster of many alerts is slightly
        # heavier than a singleton, but log-scaled so it can never dominate.
        volume_nudge = 1.0 + 0.10 * math.log1p(max(int(cluster.count) - 1, 0))
        weights.append(_clamp(base * crit_mult * volume_nudge, 0.0, 0.95))
    points = 25.0 * _noisy_or(weights)
    detail = f'{len(clusters)} alert cluster(s) after de-duplication'
    return ScoreComponent('alert_pressure', 'Active alert severity & volume', points, 25.0, detail)


def _anomaly_rate(inputs: RiskInputs) -> ScoreComponent:
    if inputs.has_anomaly_baseline():
        ratio = inputs.anomaly_rate_current / inputs.anomaly_rate_baseline
        # 1x baseline = 0 points; 3x baseline (or more) = full 20 points.
        points = 20.0 * _clamp((ratio - 1.0) / 2.0, 0.0, 1.0)
        detail = f'{inputs.anomaly_rate_current:.2f} vs {inputs.anomaly_rate_baseline:.2f} baseline ({ratio:.1f}x)'
    else:
        # No baseline: fall back to absolute anomaly volume, saturating at ~20
        # anomalies/24h, and flag reduced evidence quality via detail.
        points = 20.0 * _clamp(inputs.anomaly_count_24h / 20.0, 0.0, 1.0)
        detail = f'{inputs.anomaly_count_24h} anomalies/24h (no historical baseline)'
    return ScoreComponent('anomaly_rate', 'Anomaly rate vs baseline', points, 20.0, detail)


def _asset_exposure(inputs: RiskInputs) -> ScoreComponent:
    weights = [ASSET_EXPOSURE_WEIGHT[_normalize_criticality(c)] for c in inputs.affected_asset_criticalities]
    points = 15.0 * _noisy_or(weights)
    crit = sum(1 for c in inputs.affected_asset_criticalities if _normalize_criticality(c) in {'critical', 'high'})
    detail = f'{len(weights)} affected asset(s); {crit} high/critical tier'
    return ScoreComponent('asset_exposure', 'Affected asset exposure', points, 15.0, detail)


def _monitoring_degradation(inputs: RiskInputs) -> ScoreComponent:
    factor = _clamp(inputs.monitoring_degradation_factor, 0.0, 1.0)
    points = 10.0 * factor
    detail = f'degradation factor {factor:.2f} (fallback-adjusted)'
    return ScoreComponent('monitoring_degradation', 'Monitoring/provider degradation', points, 10.0, detail)


def _pending_controls(inputs: RiskInputs) -> ScoreComponent:
    count = max(int(inputs.pending_control_gap_count), 0)
    points = 5.0 * _clamp(count / 3.0, 0.0, 1.0)
    detail = f'{count} pending high-impact control gap(s)'
    return ScoreComponent('pending_controls', 'Pending high-impact controls', points, 5.0, detail)


def compute_risk_score(inputs: RiskInputs) -> RiskScoreResult:
    """Deterministically compute the 0-100 global risk score.

    The score is the clamped sum of six explainable components. ``top_risk_drivers``
    ranks the components that actually contributed (points > 0) and expresses
    each as a percentage of the total risk points, so the UI's driver list is
    always exactly the score's provenance — never an LLM guess.
    """
    components = [
        _incident_pressure(inputs),
        _alert_pressure(inputs),
        _anomaly_rate(inputs),
        _asset_exposure(inputs),
        _monitoring_degradation(inputs),
        _pending_controls(inputs),
    ]
    total = sum(c.points for c in components)
    score = int(round(_clamp(total, 0.0, 100.0)))
    band = _band(score, RISK_BANDS)

    contributing = [c for c in components if c.points > 0.05]
    contributing.sort(key=lambda c: c.points, reverse=True)
    drivers_total = sum(c.points for c in contributing) or 1.0
    top_risk_drivers = [
        {
            'key': c.key,
            'label': c.label,
            'points': round(c.points, 1),
            'percent': int(round(100 * c.points / drivers_total)),
            'detail': c.detail,
        }
        for c in contributing
    ]
    evidence_quality = 'complete' if inputs.has_anomaly_baseline() else 'partial'
    return RiskScoreResult(score, band, components, top_risk_drivers, evidence_quality)


# --------------------------------------------------------------------------
# System health score
# --------------------------------------------------------------------------


@dataclass
class HealthInsight:
    severity: str  # 'info' | 'warning' | 'critical'
    message: str
    source_type: str = ''
    source_id: str = ''
    occurred_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            'severity': self.severity,
            'message': self.message,
            'source_type': self.source_type,
            'source_id': self.source_id,
            'occurred_at': self.occurred_at,
        }


@dataclass
class HealthInputs:
    # Telemetry / target continuity
    configured_target_count: int = 0
    reporting_target_count: int = 0
    stale_target_count: int = 0
    telemetry_freshness: str = 'unavailable'  # fresh | stale | unavailable
    stale_target_refs: list[dict[str, Any]] = field(default_factory=list)
    # Workers
    required_worker_count: int = 0
    healthy_worker_count: int = 0
    missing_worker_refs: list[dict[str, Any]] = field(default_factory=list)
    # Providers (each: {name, primary_healthy, fallback_healthy, rate_limited})
    providers: list[dict[str, Any]] = field(default_factory=list)
    # Detection pipeline
    detection_fresh: bool = True
    detection_queue_lagged: bool = False
    detection_worker_errors: int = 0
    detection_freshness_known: bool = True
    # Infrastructure components (each: {name, healthy})
    infra_components: list[dict[str, Any]] = field(default_factory=list)
    # Critical operational pressure
    critical_incident_count: int = 0
    critical_alert_count: int = 0

    def is_configured(self) -> bool:
        return self.configured_target_count > 0


@dataclass
class HealthScoreResult:
    score: int
    status: str
    summary: str
    components: list[ScoreComponent]
    insights: list[HealthInsight]

    def to_dict(self) -> dict[str, Any]:
        return {
            'score': self.score,
            'status': self.status,
            'summary': self.summary,
            'components': [c.to_dict() for c in self.components],
            'insights': [i.to_dict() for i in self.insights],
        }


# Component weights sum to 100. Kept as points so a sub-score of 1.0 yields the
# full weighted points (e.g. telemetry 1.0 -> 30 points).
_HEALTH_WEIGHTS = {
    'telemetry_continuity': 30.0,
    'worker_heartbeats': 20.0,
    'provider_health': 20.0,
    'detection_pipeline': 15.0,
    'infrastructure': 10.0,
    'critical_pressure': 5.0,
}


def _telemetry_subscore(inputs: HealthInputs, insights: list[HealthInsight]) -> float:
    freshness_base = {'fresh': 1.0, 'stale': 0.5, 'unavailable': 0.0}.get(inputs.telemetry_freshness, 0.0)
    stale_fraction = 0.0
    if inputs.configured_target_count > 0:
        stale_fraction = _clamp(inputs.stale_target_count / inputs.configured_target_count, 0.0, 1.0)
    subscore = _clamp(freshness_base * (1.0 - 0.5 * stale_fraction), 0.0, 1.0)
    if inputs.telemetry_freshness == 'unavailable':
        insights.append(HealthInsight('critical', 'Telemetry is unavailable across monitored targets.', 'telemetry'))
    elif inputs.telemetry_freshness == 'stale':
        insights.append(HealthInsight('warning', 'Telemetry is stale; latest events are older than the polling window.', 'telemetry'))
    for ref in inputs.stale_target_refs[:5]:
        insights.append(HealthInsight(
            'warning',
            f"Monitoring target {ref.get('label') or ref.get('id') or ''} has stale telemetry.".strip(),
            'monitoring_target', str(ref.get('id') or ''), ref.get('occurred_at'),
        ))
    return subscore


def _worker_subscore(inputs: HealthInputs, insights: list[HealthInsight]) -> float:
    if inputs.required_worker_count <= 0:
        return 1.0
    subscore = _clamp(inputs.healthy_worker_count / inputs.required_worker_count, 0.0, 1.0)
    for ref in inputs.missing_worker_refs[:5]:
        insights.append(HealthInsight(
            'critical',
            f"Required worker {ref.get('label') or ref.get('id') or ''} is missing its heartbeat.".strip(),
            'worker_heartbeat', str(ref.get('id') or ''), ref.get('occurred_at'),
        ))
    return subscore


def _provider_subscore(inputs: HealthInputs, insights: list[HealthInsight]) -> float:
    if not inputs.providers:
        return 1.0
    per_provider: list[float] = []
    for provider in inputs.providers:
        name = str(provider.get('name') or 'provider')
        primary = bool(provider.get('primary_healthy', True))
        fallback = bool(provider.get('fallback_healthy', False))
        rate_limited = bool(provider.get('rate_limited', False))
        if primary and not rate_limited:
            per_provider.append(1.0)
        elif primary and rate_limited:
            per_provider.append(0.8)
            insights.append(HealthInsight('warning', f'{name} is returning rate limits; latency may be elevated.', 'provider'))
        elif fallback:
            # Primary down but fallback working: degraded, NOT critical.
            per_provider.append(0.6)
            insights.append(HealthInsight('warning', f'{name} primary is unhealthy; fallback routing is active.', 'provider'))
        else:
            # Both primary and fallback unavailable: severe penalty.
            per_provider.append(0.0)
            insights.append(HealthInsight('critical', f'{name} primary and fallback are both unavailable.', 'provider'))
    return sum(per_provider) / len(per_provider)


def _detection_subscore(inputs: HealthInputs, insights: list[HealthInsight]) -> float:
    if not inputs.detection_freshness_known:
        return 0.75  # unknown pipeline state is neither healthy nor failed
    subscore = 1.0
    if not inputs.detection_fresh:
        subscore -= 0.4
        insights.append(HealthInsight('warning', 'Detection pipeline output is lagging behind ingested telemetry.', 'detection'))
    if inputs.detection_queue_lagged:
        subscore -= 0.3
        insights.append(HealthInsight('warning', 'Detection queue depth is above the healthy threshold.', 'detection'))
    if inputs.detection_worker_errors > 0:
        subscore -= _clamp(inputs.detection_worker_errors / 10.0, 0.0, 0.3)
        insights.append(HealthInsight('warning', f'{inputs.detection_worker_errors} detection worker error(s) in the recent window.', 'detection'))
    return _clamp(subscore, 0.0, 1.0)


def _infra_subscore(inputs: HealthInputs, insights: list[HealthInsight]) -> float:
    if not inputs.infra_components:
        return 1.0
    healthy = 0
    for component in inputs.infra_components:
        if bool(component.get('healthy', True)):
            healthy += 1
        else:
            insights.append(HealthInsight('critical', f"{component.get('name') or 'Infrastructure component'} is unhealthy.", 'infrastructure', str(component.get('name') or '')))
    return healthy / len(inputs.infra_components)


def _critical_pressure_subscore(inputs: HealthInputs) -> float:
    pressure = _noisy_or(
        [0.6] * max(int(inputs.critical_incident_count), 0)
        + [0.4] * max(int(inputs.critical_alert_count), 0)
    )
    return 1.0 - pressure


def compute_health_score(inputs: HealthInputs) -> HealthScoreResult:
    """Deterministically compute the 0-100 system-health score.

    Correlates small failures across six operational layers. When no monitoring
    targets are configured the result is explicitly ``not_configured`` (never a
    misleading "100% healthy"), because telemetry continuity — the largest
    component — cannot be proven.
    """
    insights: list[HealthInsight] = []
    subscores = {
        'telemetry_continuity': _telemetry_subscore(inputs, insights),
        'worker_heartbeats': _worker_subscore(inputs, insights),
        'provider_health': _provider_subscore(inputs, insights),
        'detection_pipeline': _detection_subscore(inputs, insights),
        'infrastructure': _infra_subscore(inputs, insights),
        'critical_pressure': _critical_pressure_subscore(inputs),
    }
    labels = {
        'telemetry_continuity': 'Telemetry ingestion continuity',
        'worker_heartbeats': 'Required worker heartbeats',
        'provider_health': 'RPC/provider/oracle health',
        'detection_pipeline': 'Detection pipeline lag & queue',
        'infrastructure': 'Database/Redis/storage/evidence',
        'critical_pressure': 'Critical operational pressure',
    }
    components: list[ScoreComponent] = []
    total = 0.0
    for key, weight in _HEALTH_WEIGHTS.items():
        points = weight * subscores[key]
        total += points
        components.append(ScoreComponent(key, labels[key], points, weight, f'sub-score {subscores[key]:.2f}'))

    score = int(round(_clamp(total, 0.0, 100.0)))

    if not inputs.is_configured():
        status = 'not_configured'
        summary = 'No monitoring targets are configured for this workspace.'
        insights.insert(0, HealthInsight('info', 'No monitoring targets configured; health cannot be proven healthy.', 'monitoring_target'))
        # Never let an unconfigured workspace read as a healthy 90+.
        score = min(score, 60)
    else:
        status = _band(score, HEALTH_STATES)
        summary = _health_summary(status, inputs)

    # Most severe insights first, then de-duplicate by message.
    order = {'critical': 0, 'warning': 1, 'info': 2}
    seen: set[str] = set()
    ordered_insights: list[HealthInsight] = []
    for insight in sorted(insights, key=lambda i: order.get(i.severity, 3)):
        if insight.message in seen:
            continue
        seen.add(insight.message)
        ordered_insights.append(insight)

    return HealthScoreResult(score, status, summary, components, ordered_insights)


def _health_summary(status: str, inputs: HealthInputs) -> str:
    if status == 'healthy':
        return 'All ingestion pipelines, workers, and providers are operational.'
    reasons: list[str] = []
    degraded_providers = sum(
        1 for p in inputs.providers
        if not bool(p.get('primary_healthy', True)) or bool(p.get('rate_limited', False))
    )
    if degraded_providers:
        reasons.append(f'{degraded_providers} provider degraded')
    if inputs.stale_target_count:
        reasons.append(f'{inputs.stale_target_count} target(s) stale')
    missing_workers = max(inputs.required_worker_count - inputs.healthy_worker_count, 0)
    if missing_workers:
        reasons.append(f'{missing_workers} required worker(s) not beating')
    if inputs.telemetry_freshness != 'fresh':
        reasons.append(f'telemetry {inputs.telemetry_freshness}')
    if not reasons:
        reasons.append('one or more operational layers degraded')
    return ' and '.join(reasons[:3]) + '.'


# --------------------------------------------------------------------------
# Fallback-aware degradation factor helper (shared by risk + aggregation)
# --------------------------------------------------------------------------


def provider_degradation_factor(
    *,
    providers: list[dict[str, Any]],
    configured_target_count: int,
    stale_target_count: int,
) -> float:
    """Collapse provider + target degradation into a single [0, 1] factor.

    Fallback-aware: a provider whose primary is down but whose fallback is
    healthy counts as *partial* degradation, not full. Used by the risk scorer's
    ``monitoring_degradation`` component so risk and health stay consistent.
    """
    provider_penalty = 0.0
    if providers:
        per: list[float] = []
        for provider in providers:
            primary = bool(provider.get('primary_healthy', True))
            fallback = bool(provider.get('fallback_healthy', False))
            rate_limited = bool(provider.get('rate_limited', False))
            if primary and not rate_limited:
                per.append(0.0)
            elif primary and rate_limited:
                per.append(0.25)
            elif fallback:
                per.append(0.5)
            else:
                per.append(1.0)
        provider_penalty = sum(per) / len(per)
    target_penalty = 0.0
    if configured_target_count > 0:
        target_penalty = _clamp(stale_target_count / configured_target_count, 0.0, 1.0)
    # Weight provider health slightly above target staleness.
    return _clamp(0.6 * provider_penalty + 0.4 * target_penalty, 0.0, 1.0)
