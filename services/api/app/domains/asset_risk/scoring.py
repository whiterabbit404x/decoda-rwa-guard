"""Deterministic, Decimal-safe asset risk scoring (score_version ``asset-risk-v1``).

This is the single canonical source of truth for an asset's risk score. It is
pure — no database, no network, no clock reads beyond what the caller passes in
— so it is fully deterministic and unit-testable offline. The frontend never
recomputes any of this.

Design rules enforced here:
  * Higher score == higher risk. Range is a clamped integer 0..100.
  * Financial values are ``Decimal`` end-to-end; never float. Callers pass
    Decimals (or values coercible to Decimal); ``_to_decimal`` guards bad input.
  * Missing / stale / unverified evidence RAISES risk and LOWERS confidence —
    it never silently produces zero risk.
  * A new asset without enough history is "baseline_learning", never anomalous.
  * Confidence is computed and stored separately from the risk score.

The weighted dimensions and their weights follow the product specification:

    reserve_backing        30%
    market_valuation       20%
    monitoring_coverage    20%
    oracle_feed_freshness  10%
    contract_governance    15%
    recent_activity         5%
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Optional

SCORE_VERSION = 'asset-risk-v1'

# --------------------------------------------------------------------------
# Canonical weights. Kept as Decimal so contributions stay exact. They sum to 1.
# --------------------------------------------------------------------------
DIMENSION_WEIGHTS: dict[str, Decimal] = {
    'reserve_backing': Decimal('0.30'),
    'market_valuation': Decimal('0.20'),
    'monitoring_coverage': Decimal('0.20'),
    'oracle_feed_freshness': Decimal('0.10'),
    'contract_governance': Decimal('0.15'),
    'recent_activity': Decimal('0.05'),
}

RISK_LEVEL_BANDS = (
    (0, 29, 'low'),
    (30, 59, 'medium'),
    (60, 79, 'high'),
    (80, 100, 'critical'),
)

# Reserve statuses (customer-facing vocabulary; fail-closed).
RESERVE_HEALTHY = 'healthy'
RESERVE_WARNING = 'warning'
RESERVE_CRITICAL = 'critical'
RESERVE_INSUFFICIENT = 'insufficient_evidence'
RESERVE_OVER_COLLATERALIZED = 'over_collateralized'
# Reserve backing does not apply to this asset shape (e.g. a plain wallet or a
# non-reserve RWA type). This must never be presented as "missing reserve
# evidence" — it is simply out of scope for that asset. RESERVE_NOT_REQUIRED is
# retained as a backward-compatible alias of the same concept.
RESERVE_NOT_APPLICABLE = 'not_applicable'
RESERVE_NOT_REQUIRED = RESERVE_NOT_APPLICABLE

# Market statuses.
MARKET_BASELINE_LEARNING = 'baseline_learning'
MARKET_NORMAL = 'normal'
MARKET_MEDIUM = 'medium'
MARKET_HIGH = 'high'
MARKET_CRITICAL = 'critical'
MARKET_NO_PRICE = 'no_price_source'

# Monitoring health (mirrors the customer-facing set on Screen 3).
HEALTH_HEALTHY = 'healthy'
HEALTH_WARNING = 'warning'
HEALTH_CRITICAL = 'critical'
HEALTH_DEGRADED = 'degraded'
HEALTH_PROVISIONING = 'provisioning'
HEALTH_NOT_CONFIGURED = 'not_configured'
HEALTH_UNKNOWN = 'unknown'

# Governance / contract exposure signal weights (combined with a noisy-or so
# duplicates saturate rather than multiply linearly). All < 1.
GOVERNANCE_SIGNAL_WEIGHTS: dict[str, float] = {
    'upgradeable_proxy': 0.40,
    'unverified_implementation': 0.55,
    'single_owner_admin': 0.35,
    'pausable': 0.20,
    'mint_authority': 0.45,
    'blacklist_authority': 0.30,
    'role_concentration': 0.35,
    'recent_ownership_change': 0.55,
    'recent_implementation_change': 0.55,
    'unexpected_supply_change': 0.65,
    'contract_discovery_failure': 0.40,
}


def _to_decimal(value: Any) -> Optional[Decimal]:
    """Coerce to Decimal without ever using float. Returns None for empty/bad input."""
    if value is None or value == '':
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        return None
    try:
        if isinstance(value, float):
            # Route floats through str() so we get the shortest exact repr, not
            # the binary-float artifact. Financial callers should pass Decimal/str.
            return Decimal(str(value))
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def _clamp_int(value: Any, low: int = 0, high: int = 100) -> int:
    try:
        n = int(round(float(value)))
    except (ValueError, TypeError):
        n = low
    return max(low, min(high, n))


def _clamp_decimal(value: Decimal, low: Decimal, high: Decimal) -> Decimal:
    return max(low, min(high, value))


def _quantize(value: Optional[Decimal], places: str = '0.0001') -> Optional[Decimal]:
    if value is None:
        return None
    try:
        return value.quantize(Decimal(places), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return None


def risk_level_for_score(score: int) -> str:
    for low, high, label in RISK_LEVEL_BANDS:
        if low <= score <= high:
            return label
    return RISK_LEVEL_BANDS[-1][2]


def _noisy_or(weights: list[float]) -> float:
    """Combine independent [0,1) weights so the result saturates toward 1.

    Monotonic (adding any positive weight never lowers the result) and saturating
    (many similar weights approach but never exceed 1). Mirrors the combiner used
    by the dashboard scoring engine for consistency.
    """
    product = 1.0
    for weight in weights:
        product *= (1.0 - max(0.0, min(0.999, float(weight))))
    return 1.0 - product


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------
@dataclass
class AssetRiskInputs:
    """Structured, already-gathered evidence for one asset. All I/O happens in the
    service layer; this object is a pure snapshot."""

    # --- Reserve backing -------------------------------------------------
    reserve_required: bool = False
    reserve_feed_configured: bool = False
    reserve_verified: bool = False
    reserve_value_usd: Optional[Decimal] = None
    liability_value_usd: Optional[Decimal] = None
    reserve_min_coverage_ratio: Decimal = Decimal('1.0')
    reserve_age_seconds: Optional[int] = None
    reserve_stale_seconds: int = 86400
    over_collateralization_ratio: Decimal = Decimal('2.0')
    historical_max_coverage_ratio: Optional[Decimal] = None

    # --- Market / valuation ---------------------------------------------
    price_source_configured: bool = False
    price_usd: Optional[Decimal] = None
    baseline_7d: Optional[Decimal] = None
    baseline_30d: Optional[Decimal] = None
    price_stddev_30d: Optional[Decimal] = None
    price_sample_count: int = 0
    min_baseline_samples: int = 5
    secondary_price_usd: Optional[Decimal] = None
    deviation_medium_percent: Decimal = Decimal('5')
    deviation_high_percent: Decimal = Decimal('15')
    zscore_high: Decimal = Decimal('3')
    oracle_disagreement_percent: Decimal = Decimal('2')
    has_reserve_or_minting_irregularity: bool = False

    # --- Monitoring coverage --------------------------------------------
    # Each control: (name, required: bool, satisfied: bool)
    monitoring_controls: list[tuple[str, bool, bool]] = field(default_factory=list)
    has_monitoring_target: bool = False

    # --- Oracle / feed freshness ----------------------------------------
    price_age_seconds: Optional[int] = None
    price_stale_seconds: int = 3600

    # --- Contract / governance exposure ---------------------------------
    # Signal keys from GOVERNANCE_SIGNAL_WEIGHTS that are present for this asset.
    governance_signals: list[str] = field(default_factory=list)
    contract_discovery_failed: bool = False
    # Whether this asset has an on-chain contract at all. A plain wallet (EOA)
    # has no contract/governance surface, so that dimension is not applicable and
    # must not dilute (or inflate) the score.
    contract_applicable: bool = False

    # --- Recent abnormal activity ---------------------------------------
    recent_high_severity_findings: int = 0
    recent_anomaly_events: int = 0

    # --- Provider health (affects confidence only) ----------------------
    provider_failures: int = 0


# --------------------------------------------------------------------------
# Dimension results
# --------------------------------------------------------------------------
@dataclass
class ReserveResult:
    status: str
    risk_score: int
    coverage_ratio: Optional[Decimal] = None
    coverage_percent: Optional[Decimal] = None
    reserve_difference_usd: Optional[Decimal] = None
    reserve_value_usd: Optional[Decimal] = None
    liability_value_usd: Optional[Decimal] = None
    evidence_fresh: bool = False
    reason: str = ''


@dataclass
class MarketResult:
    status: str
    risk_score: int
    deviation_7d_percent: Optional[Decimal] = None
    deviation_30d_percent: Optional[Decimal] = None
    zscore: Optional[Decimal] = None
    oracle_disagreement_percent: Optional[Decimal] = None
    reason: str = ''


@dataclass
class MonitoringResult:
    coverage_percent: Decimal
    risk_score: int
    health: str
    missing_controls: list[str] = field(default_factory=list)
    reason: str = ''


@dataclass
class ScoreDimension:
    key: str
    score: int
    weight: Decimal
    contribution: Decimal
    findings: list[dict[str, Any]] = field(default_factory=list)
    # Whether this dimension applies to the asset. Not-applicable dimensions are
    # excluded from the weighted composite (their weight is redistributed) so an
    # asset is never penalized — or credited — for evidence that cannot apply.
    applicable: bool = True
    effective_weight: Decimal = Decimal('0')

    def to_dict(self) -> dict[str, Any]:
        return {
            'key': self.key,
            'score': self.score,
            'weight': float(self.weight),
            'effective_weight': float(self.effective_weight),
            'contribution': float(self.contribution),
            'applicable': self.applicable,
            'findings': self.findings,
        }


@dataclass
class AssetRiskResult:
    risk_score: int
    risk_level: str
    confidence: float
    score_version: str
    dimensions: list[ScoreDimension]
    reserve: ReserveResult
    market: MarketResult
    monitoring: MonitoringResult
    data_completeness: float

    def to_dict(self) -> dict[str, Any]:
        return {
            'risk_score': self.risk_score,
            'risk_level': self.risk_level,
            'confidence': self.confidence,
            'score_version': self.score_version,
            'dimensions': [d.to_dict() for d in self.dimensions],
            'data_completeness': self.data_completeness,
        }


# --------------------------------------------------------------------------
# Reserve backing
# --------------------------------------------------------------------------
def evaluate_reserve(inputs: AssetRiskInputs) -> ReserveResult:
    if not inputs.reserve_required:
        return ReserveResult(
            status=RESERVE_NOT_APPLICABLE,
            risk_score=0,
            evidence_fresh=True,
            reason='Reserve backing does not apply to this asset type.',
        )

    reserve = _to_decimal(inputs.reserve_value_usd)
    liability = _to_decimal(inputs.liability_value_usd)
    fresh = (
        inputs.reserve_age_seconds is None
        or inputs.reserve_age_seconds <= inputs.reserve_stale_seconds
    )

    # Fail closed: any missing / unverified / stale evidence is INSUFFICIENT and
    # elevates risk (never zero). Missing evidence must not read as "safe".
    if (
        not inputs.reserve_feed_configured
        or not inputs.reserve_verified
        or reserve is None
        or liability is None
        or liability <= 0
        or not fresh
    ):
        reason = 'Reserve feed missing, unverified, or stale — coverage cannot be proven.'
        if inputs.reserve_feed_configured and not fresh:
            reason = 'Reserve feed is stale beyond its maximum update interval.'
        elif inputs.reserve_feed_configured and not inputs.reserve_verified:
            reason = 'Reserve feed is configured but the latest value is unverified.'
        return ReserveResult(
            status=RESERVE_INSUFFICIENT,
            risk_score=65,
            reserve_value_usd=reserve,
            liability_value_usd=liability,
            evidence_fresh=fresh,
            reason=reason,
        )

    ratio = reserve / liability
    percent = ratio * Decimal('100')
    difference = reserve - liability
    min_ratio = _to_decimal(inputs.reserve_min_coverage_ratio) or Decimal('1.0')
    # Warning band: within 2% below the configured minimum.
    warning_floor = min_ratio * Decimal('0.98')

    base = ReserveResult(
        status=RESERVE_HEALTHY,
        risk_score=0,
        coverage_ratio=_quantize(ratio, '0.000001'),
        coverage_percent=_quantize(percent, '0.0001'),
        reserve_difference_usd=_quantize(difference, '0.01'),
        reserve_value_usd=_quantize(reserve, '0.01'),
        liability_value_usd=_quantize(liability, '0.01'),
        evidence_fresh=True,
    )

    if ratio < warning_floor:
        # Material shortfall -> critical, scaled by depth of the shortfall.
        shortfall = (min_ratio - ratio) / min_ratio if min_ratio > 0 else Decimal('1')
        base.status = RESERVE_CRITICAL
        base.risk_score = _clamp_int(80 + float(shortfall) * 100, 80, 100)
        base.reason = f'Reserve shortfall: coverage {percent:.2f}% is below the {min_ratio * 100:.0f}% minimum.'
        return base

    if ratio < min_ratio:
        base.status = RESERVE_WARNING
        base.risk_score = 55
        base.reason = f'Reserve coverage {percent:.2f}% is slightly below the {min_ratio * 100:.0f}% minimum.'
        return base

    over_ratio = _to_decimal(inputs.over_collateralization_ratio) or Decimal('2.0')
    historical_max = _to_decimal(inputs.historical_max_coverage_ratio)
    exceeds_historical = historical_max is not None and ratio > historical_max * Decimal('1.25')
    if ratio > over_ratio or exceeds_historical:
        # Unexpected over-collateralization is flagged, not auto-"healthy".
        base.status = RESERVE_OVER_COLLATERALIZED
        base.risk_score = 30
        base.reason = (
            f'Coverage {percent:.2f}% significantly exceeds the expected range; '
            'unexpected over-collateralization warrants review.'
        )
        return base

    # Healthy — mild residual risk as coverage approaches the minimum.
    margin = (ratio - min_ratio) / min_ratio if min_ratio > 0 else Decimal('1')
    base.risk_score = 0 if margin >= Decimal('0.1') else 12
    base.reason = f'Reserve coverage {percent:.2f}% meets the {min_ratio * 100:.0f}% minimum.'
    return base


# --------------------------------------------------------------------------
# Market / valuation deviation
# --------------------------------------------------------------------------
def evaluate_market(inputs: AssetRiskInputs) -> MarketResult:
    if not inputs.price_source_configured:
        # No configured price source: we cannot value on-chain. Moderate
        # uncertainty risk, never "healthy".
        return MarketResult(
            status=MARKET_NO_PRICE,
            risk_score=35,
            reason='No price / oracle source configured for market valuation.',
        )

    price = _to_decimal(inputs.price_usd)
    if price is None:
        return MarketResult(
            status=MARKET_NO_PRICE,
            risk_score=35,
            reason='Price source configured but no price observation available.',
        )

    # Not enough history yet: baseline learning, do NOT flag as anomalous.
    if inputs.price_sample_count < inputs.min_baseline_samples or inputs.baseline_30d is None:
        return MarketResult(
            status=MARKET_BASELINE_LEARNING,
            risk_score=5,
            reason=(
                f'Baseline learning: {inputs.price_sample_count} of '
                f'{inputs.min_baseline_samples} samples collected.'
            ),
        )

    baseline_30d = _to_decimal(inputs.baseline_30d)
    baseline_7d = _to_decimal(inputs.baseline_7d)
    dev_30d = ((price - baseline_30d) / baseline_30d * Decimal('100')) if baseline_30d and baseline_30d != 0 else None
    dev_7d = ((price - baseline_7d) / baseline_7d * Decimal('100')) if baseline_7d and baseline_7d != 0 else None
    std = _to_decimal(inputs.price_stddev_30d)
    zscore = ((price - baseline_30d) / std) if (std and std > 0 and baseline_30d is not None) else None

    secondary = _to_decimal(inputs.secondary_price_usd)
    disagreement = None
    if secondary is not None and price != 0:
        disagreement = abs(price - secondary) / price * Decimal('100')

    result = MarketResult(
        status=MARKET_NORMAL,
        risk_score=5,
        deviation_7d_percent=_quantize(dev_7d, '0.0001'),
        deviation_30d_percent=_quantize(dev_30d, '0.0001'),
        zscore=_quantize(zscore, '0.0001'),
        oracle_disagreement_percent=_quantize(disagreement, '0.0001'),
    )

    abs_dev = abs(dev_30d) if dev_30d is not None else Decimal('0')
    abs_z = abs(zscore) if zscore is not None else Decimal('0')
    medium = _to_decimal(inputs.deviation_medium_percent) or Decimal('5')
    high = _to_decimal(inputs.deviation_high_percent) or Decimal('15')
    z_high = _to_decimal(inputs.zscore_high) or Decimal('3')
    disagree_threshold = _to_decimal(inputs.oracle_disagreement_percent) or Decimal('2')

    severe = abs_dev >= high or abs_z >= z_high
    disagreement_flag = disagreement is not None and disagreement >= disagree_threshold

    if severe and inputs.has_reserve_or_minting_irregularity:
        result.status = MARKET_CRITICAL
        result.risk_score = 92
        result.reason = 'Severe price deviation combined with a reserve/minting irregularity.'
    elif severe or disagreement_flag:
        result.status = MARKET_HIGH
        result.risk_score = 70
        if disagreement_flag and not severe:
            result.reason = f'Oracle disagreement of {disagreement:.2f}% between trusted sources.'
        else:
            result.reason = f'Severe valuation deviation of {abs_dev:.2f}% from the 30-day baseline.'
    elif abs_dev >= medium:
        result.status = MARKET_MEDIUM
        result.risk_score = 45
        result.reason = f'Material valuation deviation of {abs_dev:.2f}% from the 30-day baseline.'
    else:
        result.reason = f'Valuation within baseline ({abs_dev:.2f}% from 30-day mean).'
    return result


# --------------------------------------------------------------------------
# Monitoring coverage
# --------------------------------------------------------------------------
def evaluate_monitoring(inputs: AssetRiskInputs) -> MonitoringResult:
    required = [(name, satisfied) for (name, req, satisfied) in inputs.monitoring_controls if req]
    if not required:
        # Nothing required (unusual) — treat as fully covered but unknown health.
        return MonitoringResult(
            coverage_percent=Decimal('100.00'),
            risk_score=0,
            health=HEALTH_UNKNOWN,
            reason='No required monitoring controls defined.',
        )
    satisfied_count = sum(1 for (_n, sat) in required if sat)
    total = len(required)
    coverage = (Decimal(satisfied_count) / Decimal(total) * Decimal('100')).quantize(Decimal('0.01'))
    missing = [name for (name, sat) in required if not sat]
    risk_score = _clamp_int(float(Decimal('100') - coverage))

    if not inputs.has_monitoring_target:
        health = HEALTH_NOT_CONFIGURED
    elif satisfied_count == total:
        health = HEALTH_HEALTHY
    elif coverage >= Decimal('60'):
        health = HEALTH_WARNING
    else:
        health = HEALTH_CRITICAL

    return MonitoringResult(
        coverage_percent=coverage,
        risk_score=risk_score,
        health=health,
        missing_controls=missing,
        reason=f'{satisfied_count} of {total} required monitoring controls satisfied.',
    )


# --------------------------------------------------------------------------
# Oracle / feed freshness
# --------------------------------------------------------------------------
def evaluate_feed_freshness(inputs: AssetRiskInputs) -> int:
    """0..100 risk score for price/oracle feed freshness (reserve freshness is
    handled by the reserve dimension)."""
    if not inputs.price_source_configured:
        # Required-but-missing price feed elevates freshness risk.
        return 40
    if inputs.price_age_seconds is None:
        # Configured but never observed.
        return 30
    stale = max(1, inputs.price_stale_seconds)
    if inputs.price_age_seconds <= stale:
        return 0
    # Scale up as the feed ages past the stale threshold.
    over = inputs.price_age_seconds / stale
    return _clamp_int(40 + (over - 1) * 40, 0, 100)


# --------------------------------------------------------------------------
# Contract / governance exposure
# --------------------------------------------------------------------------
def evaluate_governance(inputs: AssetRiskInputs) -> int:
    weights = [
        GOVERNANCE_SIGNAL_WEIGHTS[s]
        for s in inputs.governance_signals
        if s in GOVERNANCE_SIGNAL_WEIGHTS
    ]
    if inputs.contract_discovery_failed:
        weights.append(GOVERNANCE_SIGNAL_WEIGHTS['contract_discovery_failure'])
    if not weights:
        return 0
    return _clamp_int(100.0 * _noisy_or(weights))


# --------------------------------------------------------------------------
# Recent abnormal activity
# --------------------------------------------------------------------------
def evaluate_recent_activity(inputs: AssetRiskInputs) -> int:
    weights: list[float] = []
    weights.extend([0.6] * max(0, int(inputs.recent_high_severity_findings)))
    weights.extend([0.3] * max(0, int(inputs.recent_anomaly_events)))
    if not weights:
        return 0
    return _clamp_int(100.0 * _noisy_or(weights))


# --------------------------------------------------------------------------
# Confidence
# --------------------------------------------------------------------------
def dimension_applicability(inputs: AssetRiskInputs) -> dict[str, bool]:
    """Which weighted dimensions apply to this asset.

    Monitoring coverage and recent activity always apply. Reserve, market, and
    oracle-freshness apply to value-bearing tokens (a reserve requirement or a
    configured price source). Contract/governance applies only when the asset has
    an on-chain contract or produced governance signals. A plain wallet therefore
    is scored on monitoring + activity, not penalized for a reserve or price feed
    it will never have."""
    market_relevant = bool(inputs.price_source_configured or inputs.reserve_required)
    return {
        'reserve_backing': bool(inputs.reserve_required),
        'market_valuation': market_relevant,
        'monitoring_coverage': True,
        'oracle_feed_freshness': market_relevant,
        'contract_governance': bool(
            inputs.contract_applicable or inputs.governance_signals or inputs.contract_discovery_failed
        ),
        'recent_activity': True,
    }


def evaluate_confidence(
    inputs: AssetRiskInputs,
    reserve: ReserveResult,
    market: MarketResult,
    monitoring: MonitoringResult,
    applicability: dict[str, bool] | None = None,
) -> tuple[float, float]:
    """Return (confidence, data_completeness), both in [0, 1] rounded to 3 dp.

    Confidence is deliberately separate from the risk score: a high risk score
    computed from thin evidence should carry LOW confidence so the UI can be
    honest about uncertainty. Not-applicable dimensions neither raise nor lower
    confidence — an asset is never marked "incomplete" for evidence it can't have.
    """
    applic = applicability or dimension_applicability(inputs)
    market_applies = applic.get('market_valuation', True)

    # Data completeness — fraction of the *applicable* evidence categories we have.
    categories: list[bool] = []
    categories.append(monitoring.health not in (HEALTH_UNKNOWN, HEALTH_NOT_CONFIGURED))
    if market_applies:
        categories.append(market.status not in (MARKET_NO_PRICE, MARKET_BASELINE_LEARNING))
    if inputs.reserve_required:
        categories.append(reserve.status not in (RESERVE_INSUFFICIENT,))
    if applic.get('contract_governance', False):
        categories.append(not inputs.contract_discovery_failed)
    categories.append(inputs.has_monitoring_target)
    completeness = sum(1 for c in categories if c) / len(categories) if categories else 0.0

    confidence = 1.0
    if market_applies and market.status == MARKET_NO_PRICE:
        confidence -= 0.15
    if market_applies and market.status == MARKET_BASELINE_LEARNING:
        confidence -= 0.10
    if inputs.reserve_required and reserve.status == RESERVE_INSUFFICIENT:
        confidence -= 0.20
    if not inputs.has_monitoring_target:
        confidence -= 0.15
    if inputs.contract_discovery_failed:
        confidence -= 0.10
    if monitoring.missing_controls:
        confidence -= min(0.15, 0.05 * len(monitoring.missing_controls))
    confidence -= min(0.20, 0.05 * max(0, int(inputs.provider_failures)))

    confidence = max(0.05, min(1.0, confidence))
    return round(confidence, 3), round(completeness, 3)


def _severity_floor(reserve: ReserveResult, market: MarketResult) -> int:
    """The minimum composite score implied by the most severe active condition.

    A material reserve shortfall or a critical market event is a solvency-grade
    problem that must not blend down to a low/medium headline. Insufficient
    reserve evidence keeps the score out of the "low" band.
    """
    floor = 0
    if reserve.status == RESERVE_CRITICAL:
        floor = max(floor, 80)
    elif reserve.status == RESERVE_WARNING:
        floor = max(floor, 60)
    elif reserve.status == RESERVE_INSUFFICIENT:
        floor = max(floor, 40)
    if market.status == MARKET_CRITICAL:
        floor = max(floor, 80)
    elif market.status == MARKET_HIGH:
        floor = max(floor, 60)
    return floor


# --------------------------------------------------------------------------
# Canonical composite score
# --------------------------------------------------------------------------
def compute_asset_risk(inputs: AssetRiskInputs) -> AssetRiskResult:
    reserve = evaluate_reserve(inputs)
    market = evaluate_market(inputs)
    monitoring = evaluate_monitoring(inputs)
    freshness_score = evaluate_feed_freshness(inputs)
    governance_score = evaluate_governance(inputs)
    activity_score = evaluate_recent_activity(inputs)

    dimension_scores: dict[str, int] = {
        'reserve_backing': reserve.risk_score,
        'market_valuation': market.risk_score,
        'monitoring_coverage': monitoring.risk_score,
        'oracle_feed_freshness': freshness_score,
        'contract_governance': governance_score,
        'recent_activity': activity_score,
    }

    dimension_findings: dict[str, list[dict[str, Any]]] = {
        'reserve_backing': ([{'reason': reserve.reason, 'status': reserve.status}] if reserve.reason else []),
        'market_valuation': ([{'reason': market.reason, 'status': market.status}] if market.reason else []),
        'monitoring_coverage': (
            [{'reason': monitoring.reason, 'missing_controls': monitoring.missing_controls}]
            if monitoring.missing_controls else []
        ),
        'oracle_feed_freshness': [],
        'contract_governance': (
            [{'signal': s} for s in inputs.governance_signals if s in GOVERNANCE_SIGNAL_WEIGHTS]
        ),
        'recent_activity': [],
    }

    applicability = dimension_applicability(inputs)
    applicable_weight_total = sum(
        (DIMENSION_WEIGHTS[k] for k, ok in applicability.items() if ok), Decimal('0')
    )

    dimensions: list[ScoreDimension] = []
    total = Decimal('0')
    for key, weight in DIMENSION_WEIGHTS.items():
        score = _clamp_int(dimension_scores[key])
        is_applicable = applicability.get(key, True)
        # Redistribute not-applicable weight across the applicable dimensions so
        # the composite always reflects a full 100% of the relevant risk surface.
        effective_weight = (
            (weight / applicable_weight_total) if (is_applicable and applicable_weight_total > 0) else Decimal('0')
        )
        contribution = (Decimal(score) * effective_weight).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        if is_applicable:
            total += contribution
        dimensions.append(
            ScoreDimension(
                key=key,
                score=score,
                weight=weight,
                contribution=contribution,
                findings=dimension_findings.get(key, []),
                applicable=is_applicable,
                effective_weight=effective_weight.quantize(Decimal('0.0001')),
            )
        )

    weighted_score = _clamp_int(float(total))
    # Severity floor: a single dimension only carries its weight, so a full
    # solvency shortfall (reserve critical, 30% weight) would otherwise blend to
    # "medium". Truthfulness requires a critical finding never read as low/medium
    # risk. The transparent weighted contributions are preserved on the
    # dimensions; the headline score is floored to at least the finding's band.
    floor = _severity_floor(reserve, market)
    risk_score = max(weighted_score, floor)
    risk_level = risk_level_for_score(risk_score)
    confidence, completeness = evaluate_confidence(inputs, reserve, market, monitoring, applicability)

    return AssetRiskResult(
        risk_score=risk_score,
        risk_level=risk_level,
        confidence=confidence,
        score_version=SCORE_VERSION,
        dimensions=dimensions,
        reserve=reserve,
        market=market,
        monitoring=monitoring,
        data_completeness=completeness,
    )


# --------------------------------------------------------------------------
# Liability helper (on-chain circulating supply x reference price)
# --------------------------------------------------------------------------
def compute_on_chain_liability_usd(
    circulating_supply_base_units: Any,
    token_decimals: Any,
    reference_price_usd: Any,
) -> Optional[Decimal]:
    """on_chain_liability_usd = circulating_supply x reference_price_usd.

    ``circulating_supply_base_units`` is the raw integer supply (uint256 base
    units); it is scaled down by 10**token_decimals. All arithmetic is Decimal.
    Returns None when inputs are missing/invalid.
    """
    supply = _to_decimal(circulating_supply_base_units)
    price = _to_decimal(reference_price_usd)
    if supply is None or price is None:
        return None
    try:
        decimals = int(token_decimals) if token_decimals is not None else 0
    except (ValueError, TypeError):
        decimals = 0
    if decimals < 0 or decimals > 36:
        decimals = 0
    scale = Decimal(10) ** decimals
    if scale == 0:
        return None
    tokens = supply / scale
    return (tokens * price).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
