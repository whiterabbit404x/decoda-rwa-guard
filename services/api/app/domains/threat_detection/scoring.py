"""Deterministic threat-detection scoring (detector_version ``threat-detect-v1``).

Single canonical source of truth for a threat detection's severity, confidence,
evidence quality, and stable cluster key. Pure — no database, no network, no
clock reads beyond what the caller passes in — so it is fully deterministic and
unit-testable offline. The frontend never recomputes any of this, and the LLM
never sets severity or confidence.

Two concepts are kept deliberately separate (per the Screen 5 spec):

  * Severity   — "what would the potential impact be if this detection is valid?"
                 Driven by value/deviation, privileged access, blast radius, and
                 persistence. Independent of how sure we are.
  * Confidence — "how strongly does the available evidence support this?" Driven
                 by evidence quality, count of independent evidence items,
                 baseline deviation, signal count, actor repetition, and temporal
                 correlation. A high-severity detection built on thin evidence
                 carries LOW confidence so the UI is honest about uncertainty.
"""

from __future__ import annotations

import hashlib
from typing import Any, Optional

SCORE_VERSION = 'threat-detect-v1'

# --------------------------------------------------------------------------
# Evidence quality — a strict ranking from richest (call traces) to weakest
# (partial metadata). Higher rank == stronger grounding for a detection.
# --------------------------------------------------------------------------
EVIDENCE_QUALITY_RANK: dict[str, int] = {
    'transaction_trace': 5,
    'decoded_call': 4,
    'event_logs': 3,
    'normalized_telemetry': 2,
    'partial_metadata': 1,
}


def evidence_quality_rank(quality: Any) -> int:
    return EVIDENCE_QUALITY_RANK.get(str(quality or '').strip().lower(), 0)


def best_evidence_quality(qualities: list[str]) -> str:
    """The strongest evidence quality present, or ``partial_metadata`` if none."""
    best = None
    best_rank = -1
    for q in qualities:
        r = evidence_quality_rank(q)
        if r > best_rank:
            best_rank = r
            best = str(q or '').strip().lower()
    return best or 'partial_metadata'


# --------------------------------------------------------------------------
# Severity — canonical platform vocabulary, fail-closed ordering.
# --------------------------------------------------------------------------
SEVERITY_LEVELS = ('low', 'medium', 'high', 'critical')
_SEVERITY_RANK = {'low': 1, 'medium': 2, 'high': 3, 'critical': 4}

# Base impact points per detection type (before per-detection escalation).
_BASE_IMPACT_POINTS: dict[str, int] = {
    'privileged_action': 45,
    'mint_burn_irregularity': 40,
    'oracle_deviation': 40,
    'flash_loan_pattern': 45,
    'reentrancy_pattern': 45,
    'unusual_transfer': 20,
    'coordinated_activity': 25,
    'other': 15,
}


def severity_rank(severity: Any) -> int:
    return _SEVERITY_RANK.get(str(severity or '').strip().lower(), 0)


def severity_for_points(points: int) -> str:
    if points >= 80:
        return 'critical'
    if points >= 55:
        return 'high'
    if points >= 30:
        return 'medium'
    return 'low'


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def compute_severity(
    *,
    detection_type: str,
    value_usd: Optional[float] = None,
    amount_deviation: Optional[float] = None,
    privileged: bool = False,
    affected_asset_count: int = 1,
    actor_count: int = 1,
    persistence_windows: int = 1,
    asset_importance: int = 0,
) -> dict[str, Any]:
    """Return {level, points, inputs}. Higher points == higher potential impact.

    ``value_usd`` is often unknown at the detection layer (telemetry carries raw
    amounts, not priced USD); when None, severity leans on structural signals
    (deviation, privilege, blast radius, persistence) rather than inventing a
    value. ``asset_importance`` is 0 (unknown), 10, or 20.
    """
    base = _BASE_IMPACT_POINTS.get(str(detection_type or '').strip().lower(), _BASE_IMPACT_POINTS['other'])
    points = base

    if value_usd is not None:
        if value_usd >= 1_000_000:
            points += 40
        elif value_usd >= 100_000:
            points += 25
        elif value_usd >= 10_000:
            points += 10

    if amount_deviation is not None:
        if amount_deviation >= 10:
            points += 30
        elif amount_deviation >= 4:
            points += 15

    if privileged:
        points += 30

    # Blast radius: more affected assets / correlated actors => higher impact.
    points += min(20, 6 * max(0, int(affected_asset_count) - 1))
    points += min(15, 3 * max(0, int(actor_count) - 1))

    # Persistence: a pattern sustained across multiple rolling windows is worse
    # than a one-off.
    if int(persistence_windows) >= 3:
        points += 20
    elif int(persistence_windows) >= 2:
        points += 12

    points += max(0, min(20, int(asset_importance)))

    points = int(_clamp(points, 0, 100))
    inputs = {
        'base_points': base,
        'value_usd': value_usd,
        'amount_deviation': amount_deviation,
        'privileged': bool(privileged),
        'affected_asset_count': int(affected_asset_count),
        'actor_count': int(actor_count),
        'persistence_windows': int(persistence_windows),
        'asset_importance': int(asset_importance),
        'points': points,
    }
    return {'level': severity_for_points(points), 'points': points, 'inputs': inputs}


def compute_confidence(
    *,
    evidence_quality: str,
    evidence_count: int,
    signal_count: int = 1,
    baseline_deviation: Optional[float] = None,
    actor_repetition: int = 0,
    temporal_correlation: bool = False,
    baseline_samples: int = 0,
    min_baseline_samples: int = 8,
) -> dict[str, Any]:
    """Return {confidence, inputs}. Confidence in [0.05, 0.98].

    Never returns 1.0 — a behavioral/telemetry detection is never certain. Thin
    evidence (few items, weak quality, no baseline) yields LOW confidence even
    when severity is high, so the UI can be truthful about uncertainty.
    """
    quality_rank = evidence_quality_rank(evidence_quality)
    # Evidence quality contributes up to 0.40 (normalized_telemetry -> ~0.16).
    conf = (quality_rank / 5.0) * 0.40

    # Independent evidence items: more corroboration -> higher confidence.
    conf += min(0.25, 0.05 * max(0, int(evidence_count)))

    # Distinct detector signals that fired for this cluster.
    conf += min(0.15, 0.05 * max(0, int(signal_count) - 1))

    # Baseline deviation magnitude (multiples over the rolling mean), only
    # credited when we actually have enough baseline history to trust it.
    have_baseline = int(baseline_samples) >= int(min_baseline_samples)
    if baseline_deviation is not None and have_baseline:
        conf += min(0.15, (float(baseline_deviation) / 10.0) * 0.15)
    elif baseline_deviation is not None and not have_baseline:
        # Deviation observed but the baseline is still learning — do not over-trust.
        conf -= 0.05

    # Actor repetition and temporal tightness corroborate a real pattern.
    conf += min(0.10, 0.03 * max(0, int(actor_repetition)))
    if temporal_correlation:
        conf += 0.05

    confidence = round(_clamp(conf, 0.05, 0.98), 3)
    inputs = {
        'evidence_quality': str(evidence_quality or '').strip().lower(),
        'evidence_quality_rank': quality_rank,
        'evidence_count': int(evidence_count),
        'signal_count': int(signal_count),
        'baseline_deviation': baseline_deviation,
        'baseline_samples': int(baseline_samples),
        'have_baseline': have_baseline,
        'actor_repetition': int(actor_repetition),
        'temporal_correlation': bool(temporal_correlation),
        'confidence': confidence,
    }
    return {'confidence': confidence, 'inputs': inputs}


# --------------------------------------------------------------------------
# Stable cluster key + supporting signatures
# --------------------------------------------------------------------------
def actor_signature(actors: Any) -> str:
    """A stable signature for a set of actor addresses.

    Order-independent (sorted, lower-cased) and bounded (hashed) so the same
    behavioral cluster always maps to the same key regardless of the order
    telemetry arrived in. An empty set yields ``noactor``."""
    cleaned = sorted({str(a or '').strip().lower() for a in (actors or []) if str(a or '').strip()})
    if not cleaned:
        return 'noactor'
    digest = hashlib.sha256('|'.join(cleaned).encode('utf-8')).hexdigest()
    return digest[:16]


def window_bucket(epoch_seconds: Optional[int], window_seconds: int) -> int:
    """Floor a unix timestamp to a window boundary so events within the same
    window share a cluster key. Returns 0 when the timestamp is missing."""
    if epoch_seconds is None or window_seconds <= 0:
        return 0
    try:
        return (int(epoch_seconds) // int(window_seconds)) * int(window_seconds)
    except (ValueError, TypeError):
        return 0


def cluster_key(
    *,
    workspace_id: str,
    detection_type: str,
    chain_id: Any = None,
    asset_id: Any = None,
    contract_address: Any = None,
    actor_signature_value: str = 'noactor',
    window_bucket_value: int = 0,
    detector_version: str = SCORE_VERSION,
) -> str:
    """Build the stable idempotency key for a detection cluster.

    Elements (workspace, chain, type, primary asset/contract, actor cluster,
    bounded time window, detector version) guarantee that repeated telemetry
    from the same root pattern updates ONE detection instead of creating
    duplicates. Returns a sha256 hex digest.
    """
    scope = '|'.join(
        [
            'threat-detect',
            str(workspace_id or ''),
            str(detection_type or ''),
            str(chain_id if chain_id is not None else ''),
            str(asset_id or ''),
            str(contract_address or '').strip().lower(),
            str(actor_signature_value or 'noactor'),
            str(int(window_bucket_value or 0)),
            str(detector_version or SCORE_VERSION),
        ]
    )
    return hashlib.sha256(scope.encode('utf-8')).hexdigest()
