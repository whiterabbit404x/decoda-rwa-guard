"""Deterministic source-health engine for the Source Optimization Agent (Screen 4).

This module is the *authority* for provider health classification, health scoring,
oracle-heartbeat classification, provider ranking and failover/route-restoration
eligibility. It is intentionally pure: no database, no network, no I/O, no LLM.
Every function is a deterministic transform of measured facts into a classification.

Design rules (mirrors CLAUDE.md truthfulness rules):

* Absence of evidence is never rendered as healthy. A metric that was not measured
  is classified ``unknown`` and can never lift the overall status to ``healthy``.
* Routing / failover decisions are made here by deterministic rules only. An AI
  model may *summarise* the output of this engine, but it must never invent metrics
  or initiate an action the engine did not authorise.
* Thresholds are configurable per workspace via :class:`HealthThresholds`; the
  defaults encode the Screen-4 specification exactly and are the single source of
  truth instead of being hard-coded across the application.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

# ---------------------------------------------------------------------------
# Canonical health vocabulary. Every source/provider classification the customer
# sees maps to exactly one of these — no invented health strings.
# ---------------------------------------------------------------------------
HEALTH_HEALTHY = 'healthy'
HEALTH_WARNING = 'warning'
HEALTH_CRITICAL = 'critical'
HEALTH_UNKNOWN = 'unknown'

# Higher number == worse, so the overall status is ``max`` over measured dimensions.
_SEVERITY: dict[str, int] = {
    HEALTH_HEALTHY: 0,
    HEALTH_WARNING: 1,
    HEALTH_CRITICAL: 2,
}


def worse_status(*statuses: str) -> str:
    """Return the most severe *measured* status, ignoring ``unknown``.

    ``unknown`` is not "safe" — it simply carries no severity signal, so it never
    lowers a measured warning/critical. If every input is unknown the result is
    ``unknown`` (no live health evidence), never ``healthy``.
    """
    measured = [s for s in statuses if s in _SEVERITY]
    if not measured:
        return HEALTH_UNKNOWN
    return max(measured, key=lambda s: _SEVERITY[s])


# ---------------------------------------------------------------------------
# Configurable thresholds. Defaults encode the Screen-4 spec exactly.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class HealthThresholds:
    """Workspace-configurable health/failover thresholds.

    Rates are fractions in ``[0, 1]`` (``0.01`` == 1%). Latency is milliseconds.
    """

    # Block-height lag (integer blocks behind chain head).
    block_lag_healthy_max: int = 2          # <= 2 healthy
    block_lag_warning_max: int = 5          # 3..5 warning, > 5 critical

    # Error rate (fraction of failed RPC responses).
    error_rate_healthy_max: float = 0.01    # < 1% healthy
    error_rate_warning_max: float = 0.05    # 1%..5% warning, > 5% critical

    # Timeout rate (fraction of timed-out RPC responses).
    timeout_rate_healthy_max: float = 0.01
    timeout_rate_warning_max: float = 0.05

    # P95 latency in milliseconds.
    p95_latency_healthy_max_ms: float = 700.0    # < 700 healthy
    p95_latency_warning_max_ms: float = 1500.0   # 700..1500 warning, > 1500 critical

    # Consecutive failed probes.
    consecutive_failures_warning_min: int = 1    # 1..2 warning
    consecutive_failures_critical_min: int = 3   # >= 3 critical

    # Missed *expected* heartbeats.
    heartbeat_missed_warning: int = 1            # 1 missed -> warning
    heartbeat_missed_critical: int = 2           # >= 2 missed -> critical

    # Availability (fraction of successful probes over the window).
    availability_healthy_min: float = 0.99       # >= 99% healthy
    availability_warning_min: float = 0.95       # 95%..99% warning, < 95% critical

    # Telemetry freshness window in seconds. No telemetry inside the window is critical.
    telemetry_freshness_seconds: int = 900       # 15 minutes

    # --- failover / routing policy ------------------------------------------
    failover_min_fallback_score: float = 80.0    # fallback must score >= 80
    failover_max_fallback_block_lag: int = 2     # fallback block lag <= 2
    failover_consecutive_critical: int = 3       # 3 consecutive critical probes triggers
    failover_unavailable_seconds: int = 30       # endpoint unavailable >= 30s triggers
    failover_cooldown_seconds: int = 300         # 5-minute cooldown after a route change
    route_recovery_seconds: int = 900            # primary must be healthy this long to restore

    @classmethod
    def from_overrides(cls, overrides: Mapping[str, Any] | None) -> 'HealthThresholds':
        """Build thresholds from a (possibly partial, possibly noisy) settings map.

        Unknown keys are ignored. Values that fail to coerce to the field type are
        skipped so a malformed persisted setting can never crash health scoring —
        it falls back to the spec default for that single field.
        """
        base = cls()
        if not overrides:
            return base
        patch: dict[str, Any] = {}
        for f in base.__dataclass_fields__.values():  # type: ignore[attr-defined]
            if f.name not in overrides:
                continue
            raw = overrides[f.name]
            if raw is None:
                continue
            try:
                patch[f.name] = int(raw) if f.type == 'int' else float(raw)
            except (TypeError, ValueError):
                continue
        return replace(base, **patch) if patch else base


DEFAULT_THRESHOLDS = HealthThresholds()


# ---------------------------------------------------------------------------
# Per-dimension classifiers. Each returns one of the HEALTH_* constants.
# A ``None`` metric is unmeasured -> HEALTH_UNKNOWN (never healthy).
# ---------------------------------------------------------------------------
def classify_block_lag(lag: int | float | None, t: HealthThresholds = DEFAULT_THRESHOLDS) -> str:
    if lag is None:
        return HEALTH_UNKNOWN
    if lag <= t.block_lag_healthy_max:
        return HEALTH_HEALTHY
    if lag <= t.block_lag_warning_max:
        return HEALTH_WARNING
    return HEALTH_CRITICAL


def classify_error_rate(rate: float | None, t: HealthThresholds = DEFAULT_THRESHOLDS) -> str:
    if rate is None:
        return HEALTH_UNKNOWN
    if rate < t.error_rate_healthy_max:
        return HEALTH_HEALTHY
    if rate <= t.error_rate_warning_max:
        return HEALTH_WARNING
    return HEALTH_CRITICAL


def classify_timeout_rate(rate: float | None, t: HealthThresholds = DEFAULT_THRESHOLDS) -> str:
    if rate is None:
        return HEALTH_UNKNOWN
    if rate < t.timeout_rate_healthy_max:
        return HEALTH_HEALTHY
    if rate <= t.timeout_rate_warning_max:
        return HEALTH_WARNING
    return HEALTH_CRITICAL


def classify_latency(p95_ms: float | None, t: HealthThresholds = DEFAULT_THRESHOLDS) -> str:
    if p95_ms is None:
        return HEALTH_UNKNOWN
    if p95_ms < t.p95_latency_healthy_max_ms:
        return HEALTH_HEALTHY
    if p95_ms <= t.p95_latency_warning_max_ms:
        return HEALTH_WARNING
    return HEALTH_CRITICAL


def classify_consecutive_failures(count: int | None, t: HealthThresholds = DEFAULT_THRESHOLDS) -> str:
    if count is None:
        return HEALTH_UNKNOWN
    if count >= t.consecutive_failures_critical_min:
        return HEALTH_CRITICAL
    if count >= t.consecutive_failures_warning_min:
        return HEALTH_WARNING
    return HEALTH_HEALTHY


def classify_availability(availability: float | None, t: HealthThresholds = DEFAULT_THRESHOLDS) -> str:
    if availability is None:
        return HEALTH_UNKNOWN
    if availability >= t.availability_healthy_min:
        return HEALTH_HEALTHY
    if availability >= t.availability_warning_min:
        return HEALTH_WARNING
    return HEALTH_CRITICAL


def classify_heartbeat(missed: int | None, t: HealthThresholds = DEFAULT_THRESHOLDS) -> str:
    """Classify by the number of *expected* heartbeats missed."""
    if missed is None:
        return HEALTH_UNKNOWN
    if missed >= t.heartbeat_missed_critical:
        return HEALTH_CRITICAL
    if missed >= t.heartbeat_missed_warning:
        return HEALTH_WARNING
    return HEALTH_HEALTHY


def classify_telemetry_recency(
    last_telemetry_at: datetime | None,
    *,
    now: datetime | None = None,
    t: HealthThresholds = DEFAULT_THRESHOLDS,
) -> str:
    """No telemetry inside the freshness window is critical; twice the window is
    warning-or-worse; missing entirely is unknown (never healthy)."""
    if last_telemetry_at is None:
        return HEALTH_UNKNOWN
    now = now or datetime.now(timezone.utc)
    age = (now - _as_aware(last_telemetry_at)).total_seconds()
    if age <= t.telemetry_freshness_seconds:
        return HEALTH_HEALTHY
    if age <= t.telemetry_freshness_seconds * 2:
        return HEALTH_WARNING
    return HEALTH_CRITICAL


# ---------------------------------------------------------------------------
# 0..100 health score. Weighted mean of the *measured* component sub-scores.
# ---------------------------------------------------------------------------
_COMPONENT_WEIGHTS: dict[str, float] = {
    'availability': 25.0,
    'error_rate': 15.0,
    'timeout_rate': 10.0,
    'p95_latency': 15.0,
    'block_lag': 15.0,
    'heartbeat': 10.0,
    'consecutive_failures': 5.0,
    'telemetry_recency': 5.0,
}


def _clamp_score(value: float) -> float:
    return max(0.0, min(100.0, value))


def _score_lower_better(value: float, healthy_max: float, warning_max: float, zero_at: float) -> float:
    """Piecewise-linear score for a "lower is better" metric.

    * ``0``          -> 100
    * ``healthy_max``-> 80  (healthy band is 80..100)
    * ``warning_max``-> 50  (warning band is 50..80)
    * ``zero_at``    -> 0   (critical band is 0..50)
    """
    if value <= 0:
        return 100.0
    if value <= healthy_max:
        return _clamp_score(100.0 - (value / healthy_max) * 20.0)
    if value <= warning_max:
        span = warning_max - healthy_max or 1.0
        return _clamp_score(80.0 - ((value - healthy_max) / span) * 30.0)
    if value < zero_at:
        span = zero_at - warning_max or 1.0
        return _clamp_score(50.0 - ((value - warning_max) / span) * 50.0)
    return 0.0


def _score_higher_better(value: float, healthy_min: float, warning_min: float) -> float:
    """Piecewise-linear score for a "higher is better" metric in [0, 1]."""
    if value >= 1.0:
        return 100.0
    if value >= healthy_min:
        span = 1.0 - healthy_min or 1.0
        return _clamp_score(80.0 + ((value - healthy_min) / span) * 20.0)
    if value >= warning_min:
        span = healthy_min - warning_min or 1.0
        return _clamp_score(50.0 + ((value - warning_min) / span) * 30.0)
    span = warning_min or 1.0
    return _clamp_score((value / span) * 50.0)


def _stepped_score(status: str) -> float:
    """Deterministic sub-score for count-based dimensions expressed as a status."""
    return {HEALTH_HEALTHY: 100.0, HEALTH_WARNING: 60.0, HEALTH_CRITICAL: 0.0}.get(status, 0.0)


@dataclass(frozen=True)
class HealthAssessment:
    """The engine's verdict for one source/provider."""

    status: str                                  # HEALTH_* overall classification
    score: float | None                          # 0..100, or None when nothing was measured
    dimensions: dict[str, str]                   # per-dimension HEALTH_* classifications
    triggered_rules: list[str]                   # human/machine-readable rule ids that fired
    component_scores: dict[str, float]           # per-dimension 0..100 sub-scores
    measured_dimensions: int                     # how many dimensions had a real measurement
    has_live_evidence: bool                      # heartbeat and/or fresh telemetry present

    def as_dict(self) -> dict[str, Any]:
        return {
            'status': self.status,
            'score': None if self.score is None else round(self.score, 1),
            'dimensions': dict(self.dimensions),
            'triggered_rules': list(self.triggered_rules),
            'component_scores': {k: round(v, 1) for k, v in self.component_scores.items()},
            'measured_dimensions': self.measured_dimensions,
            'has_live_evidence': self.has_live_evidence,
        }


@dataclass(frozen=True)
class SourceMetrics:
    """Measured facts for one source. Every field defaults to ``None`` (unmeasured)."""

    availability: float | None = None
    error_rate: float | None = None
    timeout_rate: float | None = None
    p95_latency_ms: float | None = None
    block_lag: int | None = None
    consecutive_failures: int | None = None
    heartbeat_missed: int | None = None
    heartbeat_present: bool | None = None
    last_telemetry_at: datetime | None = None
    endpoint_unavailable: bool = False


def assess_source_health(
    metrics: SourceMetrics,
    *,
    thresholds: HealthThresholds = DEFAULT_THRESHOLDS,
    now: datetime | None = None,
) -> HealthAssessment:
    """Classify and score one source from measured metrics only.

    The overall status is the worst *measured* dimension, with two fail-closed
    guards: an unavailable endpoint is always critical, and a source with no
    live evidence (no heartbeat and no fresh telemetry) can never be reported as
    healthy — it is capped at ``warning`` (if something else was measured) or
    ``unknown`` (if nothing was).
    """
    now = now or datetime.now(timezone.utc)
    t = thresholds

    dims: dict[str, str] = {
        'availability': classify_availability(metrics.availability, t),
        'error_rate': classify_error_rate(metrics.error_rate, t),
        'timeout_rate': classify_timeout_rate(metrics.timeout_rate, t),
        'p95_latency': classify_latency(metrics.p95_latency_ms, t),
        'block_lag': classify_block_lag(metrics.block_lag, t),
        'consecutive_failures': classify_consecutive_failures(metrics.consecutive_failures, t),
        'heartbeat': classify_heartbeat(metrics.heartbeat_missed, t),
        'telemetry_recency': classify_telemetry_recency(metrics.last_telemetry_at, now=now, t=t),
    }

    components: dict[str, float] = {}
    if metrics.availability is not None:
        components['availability'] = _score_higher_better(
            metrics.availability, t.availability_healthy_min, t.availability_warning_min
        )
    if metrics.error_rate is not None:
        components['error_rate'] = _score_lower_better(
            metrics.error_rate, t.error_rate_healthy_max, t.error_rate_warning_max, 0.20
        )
    if metrics.timeout_rate is not None:
        components['timeout_rate'] = _score_lower_better(
            metrics.timeout_rate, t.timeout_rate_healthy_max, t.timeout_rate_warning_max, 0.20
        )
    if metrics.p95_latency_ms is not None:
        components['p95_latency'] = _score_lower_better(
            metrics.p95_latency_ms, t.p95_latency_healthy_max_ms, t.p95_latency_warning_max_ms, 5000.0
        )
    if metrics.block_lag is not None:
        components['block_lag'] = _score_lower_better(
            float(metrics.block_lag), float(t.block_lag_healthy_max), float(t.block_lag_warning_max), 20.0
        )
    if metrics.consecutive_failures is not None:
        components['consecutive_failures'] = _stepped_score(dims['consecutive_failures'])
    if metrics.heartbeat_missed is not None:
        components['heartbeat'] = _stepped_score(dims['heartbeat'])
    if metrics.last_telemetry_at is not None:
        components['telemetry_recency'] = _stepped_score(dims['telemetry_recency'])

    # Weighted mean over measured components only (renormalised weights).
    score: float | None = None
    if components:
        total_weight = sum(_COMPONENT_WEIGHTS[name] for name in components)
        if total_weight > 0:
            score = sum(_COMPONENT_WEIGHTS[name] * val for name, val in components.items()) / total_weight

    measured = [name for name, cls in dims.items() if cls != HEALTH_UNKNOWN]
    status = worse_status(*(dims[name] for name in measured))

    triggered: list[str] = []
    for name in measured:
        cls = dims[name]
        if cls in (HEALTH_WARNING, HEALTH_CRITICAL):
            triggered.append(f'{name}.{cls}')

    # Fail-closed guard 1: an unavailable endpoint is unconditionally critical.
    if metrics.endpoint_unavailable:
        status = HEALTH_CRITICAL
        if 'endpoint.unavailable' not in triggered:
            triggered.append('endpoint.unavailable')

    # Fail-closed guard 2: never report healthy without live evidence.
    has_live_evidence = bool(metrics.heartbeat_present) or dims['telemetry_recency'] == HEALTH_HEALTHY
    if status == HEALTH_HEALTHY and not has_live_evidence:
        status = HEALTH_WARNING if measured else HEALTH_UNKNOWN
        triggered.append('no_live_evidence')
        if score is not None:
            score = min(score, 79.0)

    return HealthAssessment(
        status=status,
        score=score,
        dimensions=dims,
        triggered_rules=triggered,
        component_scores=components,
        measured_dimensions=len(measured),
        has_live_evidence=has_live_evidence,
    )


# ---------------------------------------------------------------------------
# Oracle heartbeat classification.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class OracleHeartbeat:
    expected_interval_seconds: int
    last_update_at: datetime | None
    grace_period_seconds: int = 0
    latest_round: int | None = None
    previous_round: int | None = None
    latest_value: float | None = None
    previous_value: float | None = None
    deviation_pct: float | None = None


@dataclass(frozen=True)
class OracleAssessment:
    status: str
    reason: str
    missed_heartbeats: int
    escalate_to_threat: bool           # abnormal value deviation -> Threat Monitoring
    stale_source_risk: bool            # a delayed value is stale-source risk, not a confirmed exploit


def classify_oracle_heartbeat(
    hb: OracleHeartbeat,
    *,
    now: datetime | None = None,
    deviation_alert_pct: float = 10.0,
    t: HealthThresholds = DEFAULT_THRESHOLDS,
) -> OracleAssessment:
    """Classify an oracle feed's heartbeat health.

    * one missed expected heartbeat -> warning; two -> critical
    * an invalid / non-monotonic round -> immediate warning
    * an abnormal value deviation is *flagged for Threat Monitoring* but is only
      labelled stale-source risk here — never a confirmed exploit.
    """
    now = now or datetime.now(timezone.utc)

    # Invalid or non-monotonic round: the feed itself is misbehaving.
    non_monotonic = (
        hb.latest_round is not None
        and hb.previous_round is not None
        and hb.latest_round <= hb.previous_round
    )

    if hb.last_update_at is None:
        missed = t.heartbeat_missed_critical
    else:
        interval = max(hb.expected_interval_seconds, 1)
        age = (now - _as_aware(hb.last_update_at)).total_seconds() - max(hb.grace_period_seconds, 0)
        missed = int(age // interval) if age > 0 else 0

    hb_status = classify_heartbeat(missed, t)

    escalate = hb.deviation_pct is not None and abs(hb.deviation_pct) >= deviation_alert_pct
    stale_risk = hb_status in (HEALTH_WARNING, HEALTH_CRITICAL)

    if non_monotonic:
        status = worse_status(hb_status, HEALTH_WARNING)
        reason = 'non_monotonic_round'
    elif hb_status == HEALTH_CRITICAL:
        status = HEALTH_CRITICAL
        reason = 'heartbeat_missed_critical' if hb.last_update_at is not None else 'no_heartbeat'
    elif hb_status == HEALTH_WARNING:
        status = HEALTH_WARNING
        reason = 'heartbeat_missed_warning'
    else:
        status = HEALTH_HEALTHY
        reason = 'heartbeat_fresh'

    if escalate and status == HEALTH_HEALTHY:
        # A fresh feed with an abnormal value change is stale-source risk pending
        # threat correlation — surface it as a warning, not a confirmed exploit.
        status = HEALTH_WARNING
        reason = 'value_deviation_pending_threat_correlation'

    return OracleAssessment(
        status=status,
        reason=reason,
        missed_heartbeats=missed,
        escalate_to_threat=escalate,
        stale_source_risk=stale_risk or escalate,
    )


# ---------------------------------------------------------------------------
# Provider ranking.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RankedProvider:
    provider_id: str
    assessment: HealthAssessment
    block_lag: int | None
    p95_latency_ms: float | None


def rank_providers(candidates: Sequence[RankedProvider]) -> list[RankedProvider]:
    """Rank providers best-first, deterministically.

    Order key: healthy before warning before critical before unknown; then higher
    score; then lower block lag; then lower latency; then provider_id for a stable,
    reproducible tie-break.
    """
    status_rank = {HEALTH_HEALTHY: 0, HEALTH_WARNING: 1, HEALTH_CRITICAL: 2, HEALTH_UNKNOWN: 3}

    def key(p: RankedProvider) -> tuple[Any, ...]:
        return (
            status_rank.get(p.assessment.status, 3),
            -(p.assessment.score if p.assessment.score is not None else -1.0),
            p.block_lag if p.block_lag is not None else 10 ** 9,
            p.p95_latency_ms if p.p95_latency_ms is not None else float('inf'),
            p.provider_id,
        )

    return sorted(candidates, key=key)


# ---------------------------------------------------------------------------
# Failover / route-restoration eligibility.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FailoverContext:
    auto_routing_enabled: bool
    fallback_configured: bool
    fallback_approved: bool
    fallback_same_chain: bool
    fallback_supports_methods: bool
    fallback_recent_connectivity_ok: bool
    fallback_assessment: HealthAssessment | None
    fallback_block_lag: int | None
    primary_assessment: HealthAssessment
    primary_consecutive_critical: int
    primary_unavailable_seconds: int
    telemetry_breached: bool
    seconds_since_last_route_change: int | None


@dataclass(frozen=True)
class FailoverDecision:
    eligible: bool
    should_failover: bool
    trigger: str | None
    blocked_reasons: list[str]
    cooldown_active: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            'eligible': self.eligible,
            'should_failover': self.should_failover,
            'trigger': self.trigger,
            'blocked_reasons': list(self.blocked_reasons),
            'cooldown_active': self.cooldown_active,
        }


def _failover_trigger(ctx: FailoverContext, t: HealthThresholds) -> str | None:
    """Return the deterministic trigger id if the primary has breached, else None."""
    if ctx.primary_consecutive_critical >= t.failover_consecutive_critical:
        return 'consecutive_critical_probes'
    if ctx.primary_unavailable_seconds >= t.failover_unavailable_seconds:
        return 'endpoint_unavailable'
    if ctx.telemetry_breached:
        return 'telemetry_freshness_breached'
    if (
        ctx.primary_assessment.dimensions.get('block_lag') == HEALTH_CRITICAL
        and ctx.primary_consecutive_critical >= 2
    ):
        return 'critical_block_lag_sustained'
    return None


def evaluate_failover(
    ctx: FailoverContext,
    *,
    thresholds: HealthThresholds = DEFAULT_THRESHOLDS,
) -> FailoverDecision:
    """Decide whether an automatic failover is authorised.

    Returns ``should_failover`` only when the primary has hit a trigger *and* every
    guard (auto-routing on, cooldown elapsed, fallback configured/approved/compatible/
    healthy) passes. ``blocked_reasons`` explains any guard that failed so the caller
    can surface an honest, evidence-backed decision or escalate for approval.
    """
    t = thresholds
    blocked: list[str] = []

    if not ctx.auto_routing_enabled:
        blocked.append('auto_routing_disabled')
    if not ctx.fallback_configured:
        blocked.append('no_fallback_configured')
    if ctx.fallback_configured and not ctx.fallback_approved:
        blocked.append('fallback_not_approved')
    if ctx.fallback_configured and not ctx.fallback_same_chain:
        blocked.append('fallback_chain_mismatch')
    if ctx.fallback_configured and not ctx.fallback_supports_methods:
        blocked.append('fallback_missing_rpc_methods')
    if ctx.fallback_configured and not ctx.fallback_recent_connectivity_ok:
        blocked.append('fallback_connectivity_stale')

    fa = ctx.fallback_assessment
    if ctx.fallback_configured:
        if fa is None or fa.score is None:
            blocked.append('fallback_health_unknown')
        elif fa.score < t.failover_min_fallback_score:
            blocked.append('fallback_score_below_threshold')
        if ctx.fallback_block_lag is None:
            blocked.append('fallback_block_lag_unknown')
        elif ctx.fallback_block_lag > t.failover_max_fallback_block_lag:
            blocked.append('fallback_block_lag_too_high')

    # Cooldown / hysteresis: never change routes again inside the cooldown window.
    cooldown_active = (
        ctx.seconds_since_last_route_change is not None
        and ctx.seconds_since_last_route_change < t.failover_cooldown_seconds
    )
    if cooldown_active:
        blocked.append('cooldown_active')

    trigger = _failover_trigger(ctx, t)
    if trigger is None:
        blocked.append('primary_not_breached')

    eligible = not blocked
    should_failover = eligible and trigger is not None
    return FailoverDecision(
        eligible=eligible,
        should_failover=should_failover,
        trigger=trigger if should_failover else None,
        blocked_reasons=blocked,
        cooldown_active=cooldown_active,
    )


@dataclass(frozen=True)
class RestorationContext:
    auto_routing_enabled: bool
    original_primary_assessment: HealthAssessment
    original_primary_healthy_seconds: int
    seconds_since_last_route_change: int | None


def evaluate_route_restoration(
    ctx: RestorationContext,
    *,
    thresholds: HealthThresholds = DEFAULT_THRESHOLDS,
) -> FailoverDecision:
    """Decide whether to restore ingestion to the recovered original primary.

    Hysteresis: the original primary must be *continuously healthy* for the full
    recovery period, and the cooldown after the last route change must have elapsed,
    before an automatic restoration is authorised. This prevents provider flapping.
    """
    t = thresholds
    blocked: list[str] = []

    if not ctx.auto_routing_enabled:
        blocked.append('auto_routing_disabled')
    if ctx.original_primary_assessment.status != HEALTH_HEALTHY:
        blocked.append('original_primary_not_healthy')
    if ctx.original_primary_healthy_seconds < t.route_recovery_seconds:
        blocked.append('recovery_period_not_elapsed')

    cooldown_active = (
        ctx.seconds_since_last_route_change is not None
        and ctx.seconds_since_last_route_change < t.failover_cooldown_seconds
    )
    if cooldown_active:
        blocked.append('cooldown_active')

    eligible = not blocked
    return FailoverDecision(
        eligible=eligible,
        should_failover=eligible,
        trigger='route_restored' if eligible else None,
        blocked_reasons=blocked,
        cooldown_active=cooldown_active,
    )


# ---------------------------------------------------------------------------
# AI recommendation validation. The AI layer may only *explain* engine facts; a
# recommendation that references a provider or action the engine did not surface,
# or that cites evidence not present in the input, is rejected.
# ---------------------------------------------------------------------------
ALLOWED_RECOMMENDATION_ACTIONS = frozenset({
    'switch_primary_provider',
    'add_fallback_provider',
    'increase_polling_interval',
    'reduce_polling_interval',
    'investigate_websocket_disconnections',
    'rotate_api_key',
    'narrow_provider_permissions',
    'update_oracle_heartbeat_threshold',
    'repair_stale_telemetry_worker',
    'no_action',
})

# Actions that must never be auto-executed — they require explicit human approval.
APPROVAL_REQUIRED_ACTIONS = frozenset({
    'switch_primary_provider',
    'add_fallback_provider',
    'rotate_api_key',
    'narrow_provider_permissions',
    'update_oracle_heartbeat_threshold',
})


@dataclass(frozen=True)
class RecommendationValidation:
    valid: bool
    rejected_reasons: list[str]
    approval_required: bool


def validate_ai_recommendation(
    recommendation: Mapping[str, Any],
    *,
    known_provider_ids: Sequence[str],
    known_record_ids: Sequence[str],
) -> RecommendationValidation:
    """Reject any AI recommendation that is not grounded in engine facts.

    Guards: the action must be in the allowed set; any referenced provider must
    exist in ``known_provider_ids``; every ``supporting_record_ids`` entry must
    exist in ``known_record_ids``; confidence must be in ``[0, 1]``; risk level
    must be valid.
    """
    reasons: list[str] = []
    known_providers = set(known_provider_ids)
    known_records = set(known_record_ids)

    action = str(recommendation.get('recommended_action') or recommendation.get('action') or '').strip()
    if action not in ALLOWED_RECOMMENDATION_ACTIONS:
        reasons.append(f'unsupported_action:{action or "missing"}')

    provider_ref = recommendation.get('provider_id') or recommendation.get('target_provider')
    if provider_ref is not None and str(provider_ref) not in known_providers:
        reasons.append(f'unknown_provider:{provider_ref}')

    supporting = recommendation.get('supporting_record_ids') or []
    if not isinstance(supporting, (list, tuple)):
        reasons.append('supporting_record_ids_not_a_list')
        supporting = []
    for rid in supporting:
        if str(rid) not in known_records:
            reasons.append(f'unknown_record:{rid}')

    confidence = recommendation.get('confidence')
    if confidence is not None:
        try:
            c = float(confidence)
            if not (0.0 <= c <= 1.0):
                reasons.append('confidence_out_of_range')
        except (TypeError, ValueError):
            reasons.append('confidence_not_numeric')

    risk = recommendation.get('risk_level')
    if risk is not None and str(risk).lower() not in {'low', 'medium', 'high'}:
        reasons.append('invalid_risk_level')

    approval_required = action in APPROVAL_REQUIRED_ACTIONS
    if isinstance(recommendation.get('approval_required'), bool):
        approval_required = recommendation['approval_required'] or approval_required

    return RecommendationValidation(
        valid=not reasons,
        rejected_reasons=reasons,
        approval_required=approval_required,
    )


# ---------------------------------------------------------------------------
# Small internal helper.
# ---------------------------------------------------------------------------
def _as_aware(value: datetime) -> datetime:
    """Treat naive datetimes as UTC so age math never raises on mixed inputs."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
