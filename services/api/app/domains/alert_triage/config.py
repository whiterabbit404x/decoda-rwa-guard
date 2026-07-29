"""Centralized policy + constants for the Alert Triage Agent (Screen 6).

Single source of truth for the canonical severity levels, the deterministic
severity/confidence weighting policy, the recommendation catalogue, the reason
codes, and the triage-mode resolution. Keeping these here (never in the
frontend, never duplicated per component) guarantees Screen 6 has exactly one
definition of severity/status/mode — no competing frontend interpretation.
"""

from __future__ import annotations

from typing import Any, Optional

# --------------------------------------------------------------------------
# Canonical severity — the SAME four levels used everywhere else in the product
# (alerts, detections, asset risk). Never a frontend colour, never an LLM's word.
# --------------------------------------------------------------------------
CANONICAL_SEVERITIES: tuple[str, ...] = ('low', 'medium', 'high', 'critical')
SEVERITY_RANK: dict[str, int] = {'low': 1, 'medium': 2, 'high': 3, 'critical': 4}
SEVERITY_BY_RANK: dict[int, str] = {v: k for k, v in SEVERITY_RANK.items()}


def normalize_severity(value: Any) -> str:
    """Clamp any raw severity to a canonical level (defaults to 'low')."""
    s = str(value or '').strip().lower()
    return s if s in SEVERITY_RANK else 'low'


def bump_severity(base: str, steps: int) -> str:
    """Raise (or lower) a severity by N canonical steps, bounded to the scale."""
    rank = SEVERITY_RANK.get(normalize_severity(base), 1)
    rank = max(1, min(4, rank + steps))
    return SEVERITY_BY_RANK[rank]


# --------------------------------------------------------------------------
# Clustering knobs.
# --------------------------------------------------------------------------
# A "cluster" is a fingerprint group with at least this many active members.
# Alerts alone (unique fingerprint, or no stable evidence key) are UNCLUSTERED.
MIN_CLUSTER_SIZE = 2
# Upper bound on the active-alert population a single triage run / read model will
# load. Active (unresolved) alerts are bounded in practice; this caps worst-case
# work and is surfaced truthfully via a `truncated` flag rather than silently
# dropping data.
MAX_ACTIVE_ALERTS_SCAN = 1000

# --------------------------------------------------------------------------
# Detection families — coarse root-cause buckets derived from an alert's
# detection_type / rule / module. Used for the fingerprint and for routing the
# recommendation. Truthful fallback: 'unknown' (never guessed).
# --------------------------------------------------------------------------
DETECTION_FAMILY_LABELS: dict[str, str] = {
    'wallet_transfer': 'Wallet Transfer',
    'oracle_deviation': 'Oracle Deviation',
    'reserve_discrepancy': 'Reserve Discrepancy',
    'privileged_action': 'Privileged Contract Action',
    'access_control': 'Access Control',
    'compliance': 'Compliance',
    'anomaly': 'Behavioral Anomaly',
    'unknown': 'Uncategorized',
}

# Substring signals mapped to a family. Ordered: first match wins.
_FAMILY_SIGNALS: tuple[tuple[str, str], ...] = (
    ('oracle', 'oracle_deviation'),
    ('price', 'oracle_deviation'),
    ('feed', 'oracle_deviation'),
    ('reserve', 'reserve_discrepancy'),
    ('collateral', 'reserve_discrepancy'),
    ('backing', 'reserve_discrepancy'),
    ('ownership', 'privileged_action'),
    ('owner', 'privileged_action'),
    ('role', 'privileged_action'),
    ('admin', 'privileged_action'),
    ('pause', 'privileged_action'),
    ('upgrade', 'privileged_action'),
    ('mint', 'privileged_action'),
    ('privileg', 'privileged_action'),
    ('access', 'access_control'),
    ('unauthorized', 'access_control'),
    ('compliance', 'compliance'),
    ('sanction', 'compliance'),
    ('policy', 'compliance'),
    ('wallet_transfer', 'wallet_transfer'),
    ('outbound_transfer', 'wallet_transfer'),
    ('transfer', 'wallet_transfer'),
    ('anomaly', 'anomaly'),
    ('deviation', 'anomaly'),
)

# Families that involve a privileged/high-impact contract surface — they bump
# severity and steer the recommendation toward escalation.
PRIVILEGED_FAMILIES: frozenset[str] = frozenset({'privileged_action', 'access_control'})
ORACLE_FAMILIES: frozenset[str] = frozenset({'oracle_deviation'})
RESERVE_FAMILIES: frozenset[str] = frozenset({'reserve_discrepancy'})


def detection_family(*signals: Any) -> str:
    """Resolve a coarse detection family from any of the provided text signals."""
    for signal in signals:
        text = str(signal or '').strip().lower()
        if not text:
            continue
        for needle, family in _FAMILY_SIGNALS:
            if needle in text:
                return family
    return 'unknown'


def detection_family_label(family: Optional[str]) -> str:
    return DETECTION_FAMILY_LABELS.get(str(family or '').strip().lower(), 'Uncategorized')


# --------------------------------------------------------------------------
# Recommendation catalogue. The deterministic category is chosen first; optional
# AI enrichment may only improve the human-readable summary, never the category.
# --------------------------------------------------------------------------
RECOMMENDATION_LABELS: dict[str, str] = {
    'review_immediately': 'Review immediately',
    'investigate_actor_activity': 'Investigate actor activity',
    'validate_oracle_source': 'Validate oracle source',
    'check_reserve_discrepancy': 'Check reserve discrepancy',
    'escalate_to_incident': 'Escalate to incident',
    'continue_monitoring': 'Continue monitoring',
}


def recommendation_label(category: Optional[str]) -> str:
    return RECOMMENDATION_LABELS.get(str(category or '').strip().lower(), 'Continue monitoring')


# --------------------------------------------------------------------------
# Reason codes. Each is only emitted when it is actually derivable from alert /
# asset fields (see clustering.reason_codes) — never as boilerplate.
# --------------------------------------------------------------------------
REASON_LABELS: dict[str, str] = {
    'repeated_detector_signature': 'Repeated detector signature',
    'same_asset_and_contract': 'Same asset and contract',
    'common_actor': 'Common actor or counterparty',
    'transaction_lineage': 'Shared transaction lineage',
    'narrow_time_correlation': 'Narrow time correlation',
    'asset_criticality': 'High asset criticality',
    'recurrence_frequency': 'High recurrence frequency',
    'historical_severity': 'Historically high severity',
    'evidence_completeness': 'Complete on-chain evidence',
    'privileged_contract': 'Privileged contract involvement',
    'linked_incident': 'Already linked to an incident',
}


def reason_label(code: Optional[str]) -> str:
    return REASON_LABELS.get(str(code or '').strip().lower(), str(code or ''))


# --------------------------------------------------------------------------
# Confidence bands.
# --------------------------------------------------------------------------
def confidence_band(value: Optional[float]) -> Optional[str]:
    if value is None:
        return None
    if value >= 0.75:
        return 'high'
    if value >= 0.5:
        return 'medium'
    return 'low'


# --------------------------------------------------------------------------
# Triage mode. Reuses the canonical AI-triage configuration (AI_TRIAGE_ENABLED /
# AI_PROVIDER) so Screen 6 never invents its own AI toggle. A run is only ever
# labelled 'ai_assisted' when an AI provider actually produced part of the result
# (decided at run time); this function reports whether AI is even *available*.
# --------------------------------------------------------------------------
def ai_available(config: dict[str, Any] | None = None) -> bool:
    """True when a REAL (live) AI provider is enabled AND validly configured.

    The offline mock provider does NOT count as available AI — a run that only
    used the mock is rules-based, and the panel must show 'Rules-based', never
    'AI-assisted'. A misconfigured live provider (missing key/model) is treated
    as unavailable so the panel falls back to rules-based rather than claiming
    assistance it did not get.
    """
    from services.api.app import ai_triage

    cfg = config or ai_triage.triage_config()
    if not cfg.get('enabled'):
        return False
    provider = str(cfg.get('provider') or '').strip().lower()
    if provider not in ai_triage.LIVE_PROVIDERS:
        return False
    if ai_triage.blocking_configuration_errors(cfg):
        return False
    return True
