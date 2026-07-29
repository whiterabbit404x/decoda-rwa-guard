"""Pure, deterministic alert-clustering logic (no DB, no network, no env).

Everything here operates on in-memory normalized alert dicts and returns plain
data, so the fingerprint / severity / confidence / recommendation policy is unit
testable without a database or an AI provider — mirroring the repo's existing
``_deterministic_result_from_snapshot`` style.

Root-cause fingerprint (the cluster identity) is built ONLY from stable,
normalized evidence — workspace identity, chain, the monitored asset/contract
anchor, the detection family, and the detector/rule id. It deliberately excludes
mutable display text, per-event tx hashes, page timestamps, and random values,
so repeated runs over the same alerts produce the same clusters.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Optional

from services.api.app.domains.alert_triage import config as cfg

FINGERPRINT_PREFIX = 'atc1'


# --------------------------------------------------------------------------
# Normalization
# --------------------------------------------------------------------------
def _s(value: Any) -> str:
    return str(value if value is not None else '').strip()


def _lower(value: Any) -> str:
    return _s(value).lower()


def _payload(alert: dict[str, Any]) -> dict[str, Any]:
    p = alert.get('payload')
    return p if isinstance(p, dict) else {}


def _first(*values: Any) -> Optional[str]:
    for v in values:
        s = _s(v)
        if s:
            return s
    return None


def _to_float(value: Any) -> Optional[float]:
    if value is None or value == '':
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _epoch(value: Any) -> Optional[int]:
    """Best-effort epoch seconds from a datetime or ISO string (UTC-assumed)."""
    if value is None:
        return None
    ts = value
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts.replace('Z', '+00:00'))
        except ValueError:
            return None
    try:
        if getattr(ts, 'tzinfo', None) is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return int(ts.timestamp())
    except Exception:
        return None


def normalize_alert(alert: dict[str, Any]) -> dict[str, Any]:
    """Extract the canonical clustering evidence from a raw alert read-row.

    Accepts either a payload-carrying alert row or one already flattened by
    ``list_alerts``; every field falls back safely and is never invented.
    """
    payload = _payload(alert)
    family = cfg.detection_family(
        alert.get('detection_type'),
        payload.get('detection_type'),
        alert.get('rule_identifier'),
        alert.get('detector_kind'),
        payload.get('rule_key'),
        alert.get('module_key'),
        alert.get('alert_type'),
        alert.get('title'),
    )
    rule_id = _first(
        alert.get('rule_identifier'),
        alert.get('detector_kind'),
        payload.get('rule_key'),
        alert.get('alert_type'),
    )
    contract_address = _first(
        alert.get('contract_address'),
        payload.get('contract_address'),
        payload.get('contract_identifier'),
    )
    actor = _first(alert.get('from_address'), payload.get('from_address'), payload.get('actor'))
    counterparty = _first(alert.get('to_address'), payload.get('to_address'), payload.get('counterparty'))
    tx_hash = _first(alert.get('tx_hash'), payload.get('tx_hash'))
    asset_id = _first(alert.get('primary_asset_id'), alert.get('asset_id'))
    return {
        'alert_id': _s(alert.get('id')),
        'workspace_id': _s(alert.get('workspace_id')),
        'severity': cfg.normalize_severity(alert.get('severity')),
        'status': _lower(alert.get('status')),
        'title': _s(alert.get('title')),
        'chain_id': _lower(_first(alert.get('chain_id'), payload.get('chain_id'))) or None,
        'asset_id': asset_id,
        'asset_name': _first(alert.get('primary_asset_name'), alert.get('asset_name'), payload.get('asset_label')),
        'asset_risk_tier': cfg.normalize_severity(_first(alert.get('asset_risk_tier'), 'low')),
        'target_id': _first(alert.get('target_id')),
        'contract_address': _lower(contract_address) or None,
        'detection_family': family,
        'rule_id': _lower(rule_id) or None,
        'actor': _lower(actor) or None,
        'counterparty': _lower(counterparty) or None,
        'tx_hash': _lower(tx_hash) or None,
        'occurred_at': alert.get('created_at') or alert.get('occurred_at'),
        'occurred_epoch': _epoch(alert.get('created_at') or alert.get('occurred_at')),
        'detector_confidence': _to_float(_first(alert.get('confidence'), payload.get('confidence'))),
        'occurrence_count': int(alert.get('occurrence_count') or 1),
        'incident_id': _first(alert.get('incident_id')),
    }


# --------------------------------------------------------------------------
# Fingerprint
# --------------------------------------------------------------------------
def cluster_anchor(n: dict[str, Any]) -> Optional[tuple[str, str]]:
    """The stable evidence anchor as (kind, value): asset > contract > target."""
    if n.get('asset_id'):
        return ('asset', _lower(n['asset_id']))
    if n.get('contract_address'):
        return ('contract', _lower(n['contract_address']))
    if n.get('target_id'):
        return ('target', _lower(n['target_id']))
    return None


def cluster_fingerprint(n: dict[str, Any]) -> Optional[str]:
    """Stable root-cause fingerprint, or None when the alert lacks a stable
    anchor + attribution (which makes it deliberately UNCLUSTERED rather than
    force-grouped with unrelated alerts).
    """
    anchor = cluster_anchor(n)
    if anchor is None:
        return None
    family = _lower(n.get('detection_family')) or 'unknown'
    rule_id = _lower(n.get('rule_id'))
    # Too vague to attribute a shared root cause: no rule AND no family signal.
    if family == 'unknown' and not rule_id:
        return None
    components = [
        _lower(n.get('workspace_id')),
        _lower(n.get('chain_id')),
        f'{anchor[0]}:{anchor[1]}',
        family,
        rule_id,
    ]
    digest = hashlib.sha256('|'.join(components).encode('utf-8')).hexdigest()
    return f'{FINGERPRINT_PREFIX}:{digest}'


# --------------------------------------------------------------------------
# Severity policy (deterministic; centralized)
# --------------------------------------------------------------------------
def derive_cluster_severity(members: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Cluster severity from deterministic inputs. The highest member severity is
    the floor; asset criticality, recurrence, and privileged involvement can only
    raise it. Returns (severity, contributing_factors)."""
    if not members:
        return 'low', []
    factors: list[dict[str, Any]] = []

    base_rank = max(cfg.SEVERITY_RANK[m['severity']] for m in members)
    base_sev = cfg.SEVERITY_BY_RANK[base_rank]
    factors.append({'factor': 'highest_member_severity', 'value': base_sev, 'delta': 0})

    rank = base_rank

    # Asset criticality (risk tier of the monitored asset).
    top_tier = max((cfg.SEVERITY_RANK[m['asset_risk_tier']] for m in members), default=1)
    if top_tier >= cfg.SEVERITY_RANK['critical'] and rank < 4:
        rank += 1
        factors.append({'factor': 'asset_criticality', 'value': 'critical', 'delta': 1})
    elif top_tier >= cfg.SEVERITY_RANK['high']:
        factors.append({'factor': 'asset_criticality', 'value': 'high', 'delta': 0})

    # Privileged-contract involvement never sits below 'high'.
    family = members[0].get('detection_family')
    if family in cfg.PRIVILEGED_FAMILIES and rank < cfg.SEVERITY_RANK['high']:
        rank = cfg.SEVERITY_RANK['high']
        factors.append({'factor': 'privileged_contract', 'value': True, 'delta': 'floor_high'})

    # Recurrence / repeated occurrence.
    total_occurrences = sum(int(m.get('occurrence_count') or 1) for m in members)
    if (len(members) >= 5 or total_occurrences >= 8) and rank < 4:
        rank += 1
        factors.append({'factor': 'recurrence_frequency', 'value': total_occurrences, 'delta': 1})
    elif len(members) >= 3:
        factors.append({'factor': 'recurrence_frequency', 'value': total_occurrences, 'delta': 0})

    rank = max(1, min(4, rank))
    return cfg.SEVERITY_BY_RANK[rank], factors


# --------------------------------------------------------------------------
# Confidence policy (reproducible; measurable inputs only)
# --------------------------------------------------------------------------
def _dominant_actor_fraction(members: list[dict[str, Any]]) -> Optional[float]:
    actors = [m['actor'] for m in members if m.get('actor')]
    if not actors:
        return None
    counts: dict[str, int] = {}
    for a in actors:
        counts[a] = counts.get(a, 0) + 1
    return max(counts.values()) / len(members)


def compute_confidence(members: list[dict[str, Any]]) -> tuple[Optional[float], Optional[str], list[dict[str, Any]]]:
    """Reproducible confidence in [0,1] from measurable evidence. Returns
    (value, band, factors). Value is None ("not calculated") when NO component is
    measurable — never an arbitrary number."""
    if not members:
        return None, None, []
    n = len(members)
    factors: list[dict[str, Any]] = []
    # (component_value, weight, factor_name)
    components: list[tuple[float, float, str]] = []

    # Evidence completeness: fraction of members carrying an on-chain tx hash.
    with_tx = sum(1 for m in members if m.get('tx_hash'))
    evidence_completeness = with_tx / n
    components.append((evidence_completeness, 0.30, 'evidence_completeness'))
    factors.append({'factor': 'evidence_completeness', 'value': round(evidence_completeness, 3)})

    # Actor consistency: how concentrated the initiating actor is.
    actor_fraction = _dominant_actor_fraction(members)
    if actor_fraction is not None:
        components.append((actor_fraction, 0.25, 'actor_consistency'))
        factors.append({'factor': 'actor_consistency', 'value': round(actor_fraction, 3)})

    # Detector confidence: mean of the member detector confidences that exist.
    det = [m['detector_confidence'] for m in members if m.get('detector_confidence') is not None]
    if det:
        det_mean = max(0.0, min(1.0, sum(det) / len(det)))
        components.append((det_mean, 0.20, 'detector_confidence'))
        factors.append({'factor': 'detector_confidence', 'value': round(det_mean, 3)})

    # Member support: more corroborating members => more confident, capped at 4.
    support = min(1.0, n / 4.0)
    components.append((support, 0.15, 'member_support'))
    factors.append({'factor': 'member_support', 'value': n})

    # Chain/asset consistency is structural (all members share the fingerprint
    # anchor + chain), counted only when a chain id is actually present.
    if any(m.get('chain_id') for m in members):
        components.append((1.0, 0.10, 'chain_asset_consistency'))
        factors.append({'factor': 'chain_asset_consistency', 'value': 1.0})

    total_weight = sum(w for _v, w, _name in components)
    if total_weight <= 0:
        return None, None, factors
    value = sum(v * w for v, w, _name in components) / total_weight
    value = round(max(0.0, min(1.0, value)), 3)
    return value, cfg.confidence_band(value), factors


# --------------------------------------------------------------------------
# Reason codes (only when derivable from real fields)
# --------------------------------------------------------------------------
def reason_codes(members: list[dict[str, Any]]) -> list[str]:
    if not members:
        return []
    codes: list[str] = []
    n = len(members)

    rule_ids = {m['rule_id'] for m in members if m.get('rule_id')}
    if len(rule_ids) == 1 and n >= 2:
        codes.append('repeated_detector_signature')

    anchors = {cluster_anchor(m) for m in members}
    if len(anchors) == 1 and next(iter(anchors)) is not None:
        codes.append('same_asset_and_contract')

    actor_fraction = _dominant_actor_fraction(members)
    if actor_fraction is not None and actor_fraction >= 0.5:
        codes.append('common_actor')
        # Multiple transactions from the same actor => shared transaction lineage.
        if sum(1 for m in members if m.get('tx_hash')) >= 2:
            codes.append('transaction_lineage')

    epochs = [m['occurred_epoch'] for m in members if m.get('occurred_epoch') is not None]
    if len(epochs) >= 2 and (max(epochs) - min(epochs)) <= 3600:
        codes.append('narrow_time_correlation')

    if any(m['asset_risk_tier'] in ('high', 'critical') for m in members):
        codes.append('asset_criticality')

    total_occurrences = sum(int(m.get('occurrence_count') or 1) for m in members)
    if n >= 3 or total_occurrences >= 5:
        codes.append('recurrence_frequency')

    if any(m['severity'] in ('high', 'critical') for m in members):
        codes.append('historical_severity')

    if all(m.get('tx_hash') for m in members):
        codes.append('evidence_completeness')

    if members[0].get('detection_family') in cfg.PRIVILEGED_FAMILIES:
        codes.append('privileged_contract')

    if any(m.get('incident_id') for m in members):
        codes.append('linked_incident')

    # Stable de-dup preserving order.
    seen: set[str] = set()
    ordered: list[str] = []
    for c in codes:
        if c not in seen:
            seen.add(c)
            ordered.append(c)
    return ordered


# --------------------------------------------------------------------------
# Recommendation (deterministic category first)
# --------------------------------------------------------------------------
def recommendation_category(*, family: Optional[str], severity: str, members: list[dict[str, Any]]) -> str:
    has_incident = any(m.get('incident_id') for m in members)
    if has_incident:
        return 'continue_monitoring'
    fam = _lower(family)
    if fam in cfg.ORACLE_FAMILIES:
        return 'validate_oracle_source'
    if fam in cfg.RESERVE_FAMILIES:
        return 'check_reserve_discrepancy'
    if fam in cfg.PRIVILEGED_FAMILIES and severity == 'critical':
        return 'escalate_to_incident'
    if severity == 'critical':
        return 'review_immediately'
    actor_fraction = _dominant_actor_fraction(members)
    if fam == 'wallet_transfer' and actor_fraction is not None and actor_fraction >= 0.5 and len(members) >= 2:
        return 'investigate_actor_activity'
    if severity == 'high':
        return 'review_immediately'
    return 'continue_monitoring'


def cluster_title(*, family: Optional[str], asset_name: Optional[str], chain_id: Optional[str], member_count: int) -> str:
    label = cfg.detection_family_label(family)
    if asset_name:
        return f'{label} — {asset_name}'
    if chain_id:
        return f'{label} on chain {chain_id}'
    return f'{label} ({member_count} alerts)'


def deterministic_summary(*, category: str, title: str, member_count: int, reasons: list[str]) -> str:
    """Rules-based, evidence-grounded one-liner. Never fabricates specifics."""
    action = cfg.recommendation_label(category)
    reason_txt = cfg.reason_label(reasons[0]).lower() if reasons else 'shared root-cause evidence'
    plural = 's' if member_count != 1 else ''
    return f'{action}: {member_count} related alert{plural} grouped by {reason_txt}. {title}.'


# --------------------------------------------------------------------------
# Top-level clustering
# --------------------------------------------------------------------------
def build_clusters(normalized_alerts: list[dict[str, Any]]) -> dict[str, Any]:
    """Group normalized alerts into clusters (>= MIN_CLUSTER_SIZE members sharing
    a fingerprint). Returns {clusters: [...], unclustered_alert_ids: [...]}.

    Deterministic: clusters are ordered by triage priority (severity, confidence,
    size, fingerprint) so the "top recommendation" is stable across runs.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    unclustered: list[str] = []
    seen_alert_ids: set[str] = set()

    for n in normalized_alerts:
        alert_id = n.get('alert_id')
        if not alert_id or alert_id in seen_alert_ids:
            # An alert must never appear twice in the clustering input.
            continue
        seen_alert_ids.add(alert_id)
        fp = cluster_fingerprint(n)
        if fp is None:
            unclustered.append(alert_id)
        else:
            groups.setdefault(fp, []).append(n)

    clusters: list[dict[str, Any]] = []
    for fingerprint, members in groups.items():
        if len(members) < cfg.MIN_CLUSTER_SIZE:
            unclustered.extend(m['alert_id'] for m in members)
            continue
        clusters.append(_assemble_cluster(fingerprint, members))

    clusters.sort(
        key=lambda c: (
            cfg.SEVERITY_RANK[c['derived_severity']],
            c['confidence'] if c['confidence'] is not None else -1.0,
            c['member_count'],
            c['fingerprint'],
        ),
        reverse=True,
    )
    return {'clusters': clusters, 'unclustered_alert_ids': unclustered}


def _assemble_cluster(fingerprint: str, members: list[dict[str, Any]]) -> dict[str, Any]:
    # Stable member order (by occurrence time then id) so history diffs are clean.
    members = sorted(members, key=lambda m: (m.get('occurred_epoch') or 0, m['alert_id']))
    family = members[0].get('detection_family')
    severity, severity_factors = derive_cluster_severity(members)
    confidence, band, confidence_factors = compute_confidence(members)
    reasons = reason_codes(members)
    category = recommendation_category(family=family, severity=severity, members=members)

    # Primary asset: the most common named asset among members (truthful fallback).
    asset_name = next((m['asset_name'] for m in members if m.get('asset_name')), None)
    asset_id = next((m['asset_id'] for m in members if m.get('asset_id')), None)
    chain_id = next((m['chain_id'] for m in members if m.get('chain_id')), None)
    title = cluster_title(family=family, asset_name=asset_name, chain_id=chain_id, member_count=len(members))
    epochs = [m['occurred_epoch'] for m in members if m.get('occurred_epoch') is not None]

    return {
        'fingerprint': fingerprint,
        'title': title,
        'detection_family': family,
        'derived_severity': severity,
        'severity_factors': severity_factors,
        'confidence': confidence,
        'confidence_band': band,
        'confidence_factors': confidence_factors,
        'reason_codes': reasons,
        'recommendation_category': category,
        'recommendation_summary': deterministic_summary(
            category=category, title=title, member_count=len(members), reasons=reasons,
        ),
        'primary_asset_id': asset_id,
        'primary_asset_name': asset_name,
        'chain_id': chain_id,
        'member_count': len(members),
        'member_alert_ids': [m['alert_id'] for m in members],
        'member_severities': {m['alert_id']: m['severity'] for m in members},
        'first_seen_epoch': min(epochs) if epochs else None,
        'last_seen_epoch': max(epochs) if epochs else None,
        'linked_incident': any(m.get('incident_id') for m in members),
    }
