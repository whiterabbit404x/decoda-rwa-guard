"""Digital Forensics Investigator (Screen 7) — deterministic forensic layer.

This module is the deterministic, evidence-grounded core of the Digital Forensics
Investigator. It is intentionally NOT an LLM: it derives factual findings, matches
a versioned local technical ruleset, and computes confidence + evidence coverage
straight from the immutable, server-built evidence snapshot (``ai_triage``). The AI
narrative (summary, prioritization, drafting) is layered on top by the existing AI
Triage Agent and is always clearly distinguished from these deterministic facts.

Truthfulness rules honored here (see CLAUDE.md):
  * The analyzer never invents a transaction hash, wallet address, asset name,
    timestamp, loss value, rule violation, evidence record, or confidence value —
    every finding and rule match cites canonical evidence references that exist in
    the snapshot, and unknown data is reported as an explicit evidence gap.
  * Confidence and evidence coverage are computed deterministically with recorded
    factors and a calculation version; they are clamped to valid ranges.
  * Because the analysis is keyed by the immutable snapshot hash + calc version, a
    page refresh re-derives the identical result (persisted, never random).
  * Findings distinguish ``verified_fact`` / ``rule_match`` / ``ai_interpretation``
    / ``unverified_hypothesis``; a hypothesis is never presented as a verified fact.

The pure functions (``derive_findings``, ``match_rules``, ``compute_confidence``,
``compute_coverage``, ``derive_workflow_stages``, ``build_analysis``) take the plain
snapshot dict and are unit-tested without a database. The DB-facing functions reuse
``ai_triage`` for snapshot assembly and are workspace-scoped, authenticated, and
audited, mirroring the repository's existing endpoint conventions.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import HTTPException, status

from services.api.app import ai_triage, pilot

logger = logging.getLogger(__name__)

# Version of the deterministic confidence / coverage / findings calculation. Bump
# this when the derivation logic changes so persisted analyses remain attributable
# to the exact algorithm that produced them (and re-derive under the new key).
CALC_VERSION = 'forensic-calc-v1'

# Verification states a finding can carry. Deterministic derivations are
# ``verified_fact``; local ruleset hits are ``rule_match``; the AI layer's
# interpretations are ``ai_interpretation`` and any unproven theory is an
# ``unverified_hypothesis``. The UI must style these distinctly.
VERIFIED_FACT = 'verified_fact'
RULE_MATCH = 'rule_match'
AI_INTERPRETATION = 'ai_interpretation'
UNVERIFIED_HYPOTHESIS = 'unverified_hypothesis'
VERIFICATION_STATES = frozenset({VERIFIED_FACT, RULE_MATCH, AI_INTERPRETATION, UNVERIFIED_HYPOTHESIS})

# Versioned local technical ruleset. This is a repository-internal security /
# forensic ruleset for tokenized-RWA monitoring — NOT a legal or compliance
# framework. Matches are surfaced as "Potential rule match" and always link to the
# concrete evidence that satisfied the deterministic predicate; a match never
# asserts that a person or protocol violated a law.
FORENSIC_RULESET_NAME = 'Decoda RWA Forensic Ruleset'
FORENSIC_RULESET_VERSION = '1.0'

# Bounded default for the "large transfer" heuristic. Kept explicit and documented
# so the deterministic threshold is auditable; a real observed value below it simply
# does not match (nothing is invented either way).
LARGE_TRANSFER_DEFAULT_THRESHOLD = 250000.0

REQUIRED_TABLES = ('incident_forensic_analyses', 'incident_reference_counters')
FORENSIC_SCHEMA_MIGRATION = '0135_incident_forensic_investigation'

# Canonical workflow stages, in order. Their STATE is derived from persisted facts
# (never inferred only in the browser). ``derive_workflow_stages`` maps each to one
# of: pending / queued / in_progress / completed / failed / skipped / degraded.
WORKFLOW_STAGES = (
    ('detection', 'Detection'),
    ('triage', 'Triage'),
    ('evidence_collection', 'Evidence Collection'),
    ('correlation', 'Correlation'),
    ('analysis', 'Analysis'),
    ('recommendation', 'Recommendation'),
    ('report', 'Report Generated'),
)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def _clamp01(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _to_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def confidence_band(value: float) -> str:
    """High / Medium / Low band from a [0,1] confidence. Boundaries are inclusive
    at the lower edge (>= 0.75 High, >= 0.40 Medium)."""
    v = _clamp01(value)
    if v >= 0.75:
        return 'high'
    if v >= 0.40:
        return 'medium'
    return 'low'


def _telemetry_rows(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    rows = snapshot.get('telemetry')
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _distinct_tx_hashes(telemetry: list[dict[str, Any]]) -> list[str]:
    return sorted({str(r.get('tx_hash')).lower() for r in telemetry if r.get('tx_hash')})


def _distinct_addresses(telemetry: list[dict[str, Any]]) -> list[str]:
    addrs: set[str] = set()
    for r in telemetry:
        for role in ('from', 'to'):
            if r.get(role):
                addrs.add(str(r[role]).lower())
    return sorted(addrs)


def _distinct_blocks(telemetry: list[dict[str, Any]]) -> list[int]:
    blocks: set[int] = set()
    for r in telemetry:
        bn = r.get('block_number')
        if bn is not None:
            try:
                blocks.add(int(bn))
            except (TypeError, ValueError):
                continue
    return sorted(blocks)


def _observed_timestamps(telemetry: list[dict[str, Any]]) -> list[str]:
    return sorted({str(r.get('observed_at')) for r in telemetry if r.get('observed_at')})


def valid_reference_set(snapshot: dict[str, Any]) -> set[str]:
    """All canonical evidence references that exist in this snapshot. Deterministic
    findings/rule matches may only cite refs from this set — the same grounding
    contract the AI layer is held to (``ai_triage.derive_valid_references``)."""
    return set(ai_triage.derive_valid_references(snapshot).get('refs') or set())


# Cap the corroborated-evidence list surfaced to the UI. The full inventory remains
# reachable via the existing Evidence tab/route; this keeps the investigation payload
# bounded (no unbounded query result).
EVIDENCE_ROW_CAP = 100

_TELEMETRY_TYPE_LABELS = {
    'wallet_transfer_detected': 'Wallet transfer',
    'wallet_transfer': 'Wallet transfer',
    'contract_event': 'Contract event',
    'oracle_update': 'Oracle signal',
    'reserve_update': 'Reserve signal',
}


def build_evidence_rows(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Build the bounded 'Corroborated evidence' list from the immutable snapshot.

    Every row is a real record from the snapshot (never demo data): the source alert,
    the originating detection rule, and each correlated telemetry event with its
    canonical identifiers. Corroboration state is derived from whether the row carries
    a canonical on-chain identifier.
    """
    rows: list[dict[str, Any]] = []
    alert = snapshot.get('alert') or {}
    if alert.get('alert_id'):
        rows.append({
            'kind': 'alert',
            'type': 'Alert record',
            'title': f"Source alert {alert['alert_id']}",
            'source': 'alert',
            'evidence_source': None,
            'tx_hash': None,
            'block_number': None,
            'chain_id': None,
            'observed_at': alert.get('created_at'),
            'reference': f"alert:{alert['alert_id']}",
            'corroboration': 'corroborated',
        })
    rule = snapshot.get('rule') or {}
    if rule.get('rule_id'):
        rows.append({
            'kind': 'rule',
            'type': 'Detection rule',
            'title': str(rule.get('name') or rule['rule_id']),
            'source': 'rule',
            'evidence_source': None,
            'tx_hash': None,
            'block_number': None,
            'chain_id': None,
            'observed_at': None,
            'reference': f"rule:{rule['rule_id']}",
            'corroboration': 'corroborated',
        })
    for row in _telemetry_rows(snapshot):
        tx = row.get('tx_hash')
        rows.append({
            'kind': 'telemetry',
            'type': _TELEMETRY_TYPE_LABELS.get(str(row.get('event_type') or '').lower(), 'Telemetry event'),
            'title': str(row.get('event_type') or 'Telemetry event'),
            'source': row.get('detected_by') or row.get('evidence_source'),
            'evidence_source': row.get('evidence_source'),
            'tx_hash': tx,
            'block_number': row.get('block_number'),
            'chain_id': row.get('chain_id'),
            'observed_at': row.get('observed_at'),
            'reference': f"telemetry:{row['telemetry_id']}" if row.get('telemetry_id') else None,
            # Corroborated when a canonical on-chain transaction anchors the event.
            'corroboration': 'corroborated' if tx else 'partial',
        })
    total = len(rows)
    return {'rows': rows[:EVIDENCE_ROW_CAP], 'total': total, 'truncated': total > EVIDENCE_ROW_CAP}


# ---------------------------------------------------------------------------
# Deterministic findings (verified facts + explicit evidence gaps)
# ---------------------------------------------------------------------------
def derive_findings(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Derive factual findings from the snapshot. Each finding is a verified fact
    (or a verified *absence* of evidence) and cites only references present in the
    snapshot. No LLM, no invented values."""
    valid_refs = valid_reference_set(snapshot)
    telemetry = _telemetry_rows(snapshot)
    findings: list[dict[str, Any]] = []

    def _refs(*candidates: str) -> list[str]:
        return [c for c in candidates if c in valid_refs]

    def _telemetry_refs() -> list[str]:
        return [f"telemetry:{r['telemetry_id']}" for r in telemetry if r.get('telemetry_id')]

    def add(finding_type: str, title: str, description: str, refs: list[str], *,
            state: str = VERIFIED_FACT, value: Any = None) -> None:
        findings.append({
            'finding_id': f'F-{len(findings) + 1:02d}',
            'finding_type': finding_type,
            'title': title,
            'description': description,
            'verification_state': state,
            'evidence_refs': refs,
            'value': value,
        })

    alert = snapshot.get('alert') or {}
    alert_id = alert.get('alert_id')
    if alert_id:
        add('source_alert', 'Linked source alert',
            f"Incident is linked to source alert {alert_id} (severity {alert.get('severity') or 'unknown'}).",
            _refs(f'alert:{alert_id}'))

    rule = snapshot.get('rule') or {}
    if rule.get('rule_id'):
        add('detection_rule', 'Originating detection rule',
            f"Detection rule {rule.get('name') or rule['rule_id']} produced the originating signal.",
            _refs(f"rule:{rule['rule_id']}"))

    target = snapshot.get('target') or {}
    if target.get('target_id') or target.get('asset_id'):
        chain = target.get('chain_id')
        add('impacted_asset', 'Impacted monitored asset',
            'Impacted monitored '
            + (f"asset {target.get('asset_id')}" if target.get('asset_id') else f"target {target.get('target_id')}")
            + (f" on chain {chain}." if chain is not None else '.'),
            _refs(f"target:{target.get('target_id')}", f"asset:{target.get('asset_id')}"),
            value={'chain_id': chain, 'asset_type': target.get('asset_type')})

    if telemetry:
        add('correlated_telemetry', 'Correlated telemetry events',
            f"{len(telemetry)} telemetry event(s) correlated to this incident.",
            _telemetry_refs(), value=len(telemetry))

        tx = _distinct_tx_hashes(telemetry)
        if tx:
            add('distinct_transactions', 'Distinct on-chain transactions',
                f"{len(tx)} distinct on-chain transaction(s) observed.",
                _telemetry_refs(), value=len(tx))

        addrs = _distinct_addresses(telemetry)
        if addrs:
            add('distinct_addresses', 'Distinct wallet addresses',
                f"{len(addrs)} distinct wallet address(es) involved in the observed activity.",
                _telemetry_refs(), value=len(addrs))

        blocks = _distinct_blocks(telemetry)
        if blocks:
            span = f"block {blocks[0]}" if len(blocks) == 1 else f"blocks {blocks[0]}–{blocks[-1]}"
            add('block_range', 'Observed block range',
                f"Activity observed at {span}.", _telemetry_refs(),
                value={'min': blocks[0], 'max': blocks[-1]})

        stamps = _observed_timestamps(telemetry)
        if stamps:
            window = stamps[0] if len(stamps) == 1 else f"{stamps[0]} to {stamps[-1]}"
            add('observation_window', 'Observation window',
                f"Earliest→latest observation: {window}.", _telemetry_refs(),
                value={'earliest': stamps[0], 'latest': stamps[-1]})

    # Explicit, verified evidence gaps. A gap is a *verified absence* — it is shown
    # as "Not available", never silently treated as safe.
    for gap in (snapshot.get('missing_evidence') or []):
        if not isinstance(gap, dict):
            continue
        add('evidence_gap', 'Evidence gap',
            str(gap.get('description') or 'Required evidence is not available.'),
            [], value={'code': gap.get('code'), 'required_for': gap.get('required_for')})

    return findings


# ---------------------------------------------------------------------------
# Versioned local ruleset matching (deterministic, evidence-linked)
# ---------------------------------------------------------------------------
def match_rules(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    """Match the snapshot against the versioned local technical ruleset. Every hit
    is a *potential* rule match linked to the evidence that satisfied it — never a
    legal or compliance judgment."""
    valid_refs = valid_reference_set(snapshot)
    telemetry = _telemetry_rows(snapshot)
    tx = _distinct_tx_hashes(telemetry)
    addrs = _distinct_addresses(telemetry)
    target = snapshot.get('target') or {}
    target_addr = str(target.get('address') or '').lower()
    matches: list[dict[str, Any]] = []

    def telemetry_refs() -> list[str]:
        return [f"telemetry:{r['telemetry_id']}" for r in telemetry
                if r.get('telemetry_id') and f"telemetry:{r['telemetry_id']}" in valid_refs]

    def add(rule_id: str, name: str, rationale: str, refs: list[str], *, match_state: str = 'potential') -> None:
        matches.append({
            'framework': FORENSIC_RULESET_NAME,
            'framework_version': FORENSIC_RULESET_VERSION,
            'rule_id': rule_id,
            'rule_name': name,
            'match_rationale': rationale,
            'evidence_refs': refs,
            'match_state': match_state,
            'verification_state': RULE_MATCH,
        })

    # RWA-FR-001 — monitored counterparty transfer: a transfer touched the monitored
    # target's own address.
    if target_addr and telemetry and target_addr in set(addrs):
        add('RWA-FR-001', 'Monitored counterparty transfer',
            'An observed transfer involved the monitored target address.',
            telemetry_refs())

    # RWA-FR-002 — large value transfer (bounded default threshold). Only matches on
    # a real numeric value; nothing is fabricated when value is absent.
    for r in telemetry:
        v = _to_float(r.get('value'))
        if v is not None and v >= LARGE_TRANSFER_DEFAULT_THRESHOLD and r.get('telemetry_id'):
            ref = f"telemetry:{r['telemetry_id']}"
            add('RWA-FR-002', 'Large value transfer',
                f'Observed transfer value {v:g} meets or exceeds the {LARGE_TRANSFER_DEFAULT_THRESHOLD:g} review threshold.',
                [ref] if ref in valid_refs else [])
            break

    # RWA-FR-003 — ambiguous transfer identity: more than one distinct candidate tx.
    if snapshot.get('telemetry_ambiguous') or len(tx) > 1:
        add('RWA-FR-003', 'Ambiguous transfer identity',
            f'{len(tx)} distinct candidate transactions resolved; the canonical transfer is ambiguous and was not guessed.',
            telemetry_refs())

    # RWA-FR-004 — unresolved telemetry grounding: no telemetry could be resolved.
    if not telemetry:
        add('RWA-FR-004', 'Unresolved telemetry grounding',
            'No canonical telemetry event resolved for the incident; factual transfer findings cannot be grounded.',
            [])

    # RWA-FR-005 — multi-address fan-out.
    if len(addrs) > 2:
        add('RWA-FR-005', 'Multi-address fan-out',
            f'{len(addrs)} distinct addresses participate in the observed activity.',
            telemetry_refs())

    # RWA-FR-006 — non-live evidence present: any simulator/replay telemetry must not
    # be treated as live customer evidence.
    if any(str(r.get('evidence_source') or '').lower() in {'simulator', 'demo', 'replay'} for r in telemetry):
        add('RWA-FR-006', 'Non-live evidence present',
            'Simulator/replay telemetry is present; it must not be treated as live customer evidence.',
            telemetry_refs())

    return matches


# ---------------------------------------------------------------------------
# Deterministic confidence + evidence coverage
# ---------------------------------------------------------------------------
# Canonical evidence dimensions we expect a fully-grounded wallet/transfer incident
# to carry. Coverage = present / expected.
COVERAGE_DIMENSIONS = (
    ('source_alert', 'Linked source alert'),
    ('detection_rule', 'Originating detection rule'),
    ('target_context', 'Monitored target / asset context'),
    ('telemetry_event', 'Correlated telemetry event'),
    ('transaction_hash', 'Canonical transaction hash'),
    ('block_number', 'Confirmed block number'),
    ('timestamp', 'Observed timestamp'),
    ('chain_id', 'Chain identifier'),
)


def _dimension_present(snapshot: dict[str, Any]) -> dict[str, bool]:
    telemetry = _telemetry_rows(snapshot)
    alert = snapshot.get('alert') or {}
    rule = snapshot.get('rule') or {}
    target = snapshot.get('target') or {}
    return {
        'source_alert': bool(alert.get('alert_id')),
        'detection_rule': bool(rule.get('rule_id')),
        'target_context': bool(target.get('target_id') or target.get('asset_id')),
        'telemetry_event': bool(telemetry),
        'transaction_hash': bool(_distinct_tx_hashes(telemetry)),
        'block_number': bool(_distinct_blocks(telemetry)),
        'timestamp': bool(_observed_timestamps(telemetry)),
        'chain_id': target.get('chain_id') is not None
        or any(r.get('chain_id') is not None for r in telemetry),
    }


def compute_coverage(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Evidence coverage as the fraction of canonical evidence dimensions present."""
    present = _dimension_present(snapshot)
    dims = [
        {'key': key, 'label': label, 'present': bool(present.get(key))}
        for key, label in COVERAGE_DIMENSIONS
    ]
    present_count = sum(1 for d in dims if d['present'])
    coverage = _clamp01(present_count / len(COVERAGE_DIMENSIONS)) if COVERAGE_DIMENSIONS else 0.0
    return {
        'coverage': coverage,
        'present_count': present_count,
        'expected_count': len(COVERAGE_DIMENSIONS),
        'dimensions': dims,
        'missing': [d['label'] for d in dims if not d['present']],
    }


def compute_confidence(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Deterministic investigation confidence in [0,1] with recorded factors.

    The LLM never sets this. Confidence rises with independently corroborated,
    canonical on-chain evidence and falls with missing, conflicting, or non-live
    evidence. The result is clamped and carries a human-readable factor list and an
    explanation so the UI can say WHY it is high/medium/low.
    """
    telemetry = _telemetry_rows(snapshot)
    factors: list[dict[str, Any]] = []
    score = 0.30  # neutral prior before evidence-based adjustments

    def factor(label: str, effect: str, weight: float, detail: str) -> None:
        nonlocal score
        score += weight
        factors.append({'factor': label, 'effect': effect, 'weight': round(weight, 3), 'detail': detail})

    if telemetry:
        factor('Telemetry present', 'increase', 0.15,
               f'{len(telemetry)} correlated telemetry event(s) grounds the incident.')
    else:
        factor('No telemetry', 'decrease', -0.20,
               'No canonical telemetry event resolved; factual grounding is missing.')

    if _distinct_tx_hashes(telemetry):
        factor('Canonical transaction hash', 'increase', 0.20,
               'At least one canonical on-chain transaction hash is present.')
    if _distinct_blocks(telemetry):
        factor('Confirmed block data', 'increase', 0.10,
               'Confirmed block number(s) present for the observed activity.')

    provider_observations = snapshot.get('provider_observations')
    independent = len(provider_observations) if isinstance(provider_observations, list) else 0
    if independent >= 2:
        factor('Independent corroboration', 'increase', 0.10,
               f'{independent} independent provider observations corroborate the evidence.')

    detection_confidence = _detection_confidence(snapshot)
    if detection_confidence is not None:
        weight = round(0.15 * detection_confidence, 3)
        factor('Detection confidence', 'increase', weight,
               f'Upstream detection confidence {detection_confidence:.2f} scaled into the score.')

    missing = snapshot.get('missing_evidence') or []
    if missing:
        weight = round(-0.10 * min(len(missing), 3), 3)
        factor('Missing required evidence', 'decrease', weight,
               f'{len(missing)} required-evidence gap(s) lower confidence.')

    if snapshot.get('telemetry_ambiguous') or len(_distinct_tx_hashes(telemetry)) > 1:
        factor('Conflicting evidence', 'decrease', -0.15,
               'Multiple distinct candidate transactions conflict on the canonical transfer.')

    if any(str(r.get('evidence_source') or '').lower() in {'simulator', 'demo', 'replay'} for r in telemetry):
        factor('Non-live evidence', 'decrease', -0.10,
               'Simulator/replay evidence reduces confidence in a live-incident conclusion.')

    # Round to the persisted precision (NUMERIC(4,3)) so the band boundary is exact and
    # not subject to binary floating-point drift (e.g. 0.30+0.15+0.20+0.10 == 0.749…).
    confidence = round(_clamp01(score), 3)
    band = confidence_band(confidence)
    explanation = _confidence_explanation(band, factors)
    return {
        'confidence': confidence,
        'band': band,
        'factors': factors,
        'explanation': explanation,
        'calc_version': CALC_VERSION,
    }


def _detection_confidence(snapshot: dict[str, Any]) -> float | None:
    """Best-effort upstream detection confidence carried on the alert payload, if
    the snapshot preserved it. Returns a clamped [0,1] float or None."""
    alert = snapshot.get('alert') or {}
    for source in (alert, snapshot.get('detection') or {}):
        if isinstance(source, dict) and source.get('confidence') is not None:
            v = _to_float(source.get('confidence'))
            if v is not None:
                return _clamp01(v)
    return None


def _confidence_explanation(band: str, factors: list[dict[str, Any]]) -> str:
    ups = [f['factor'] for f in factors if f['effect'] == 'increase']
    downs = [f['factor'] for f in factors if f['effect'] == 'decrease']
    parts = [f'Confidence is {band}.']
    if ups:
        parts.append('Supported by: ' + ', '.join(ups) + '.')
    if downs:
        parts.append('Reduced by: ' + ', '.join(downs) + '.')
    return ' '.join(parts)


# ---------------------------------------------------------------------------
# Workflow stages (state derived from persisted facts)
# ---------------------------------------------------------------------------
def derive_workflow_stages(
    snapshot: dict[str, Any],
    *,
    triage_status: str | None,
    recommendation_count: int,
    report_generated: bool,
) -> list[dict[str, Any]]:
    """Map the seven canonical stages to a truthful state from persisted facts.

    The state is never inferred only in the browser: ``triage_status`` is the AI
    triage job's persisted status, ``recommendation_count`` and ``report_generated``
    come from persisted rows.
    """
    telemetry = _telemetry_rows(snapshot)
    alert = snapshot.get('alert') or {}
    has_alert = bool(alert.get('alert_id'))
    has_evidence = bool(telemetry)
    incomplete = bool(snapshot.get('evidence_incomplete'))

    # AI analysis stage state mapped from the triage job status.
    analysis_state = {
        None: 'pending',
        'not_requested': 'pending',
        'queued': 'queued',
        'running': 'in_progress',
        'completed': 'completed',
        'completed_with_warnings': 'completed',
        'failed': 'failed',
        'validation_failed': 'failed',
        'budget_blocked': 'degraded',
        'disabled': 'skipped',
        'unavailable': 'skipped',
        'cancelled': 'skipped',
    }.get(triage_status, 'pending')

    states = {
        'detection': 'completed' if has_alert else 'pending',
        'triage': 'completed' if has_alert else 'pending',
        'evidence_collection': ('completed' if has_evidence and not incomplete
                                else 'degraded' if has_evidence and incomplete
                                else 'pending'),
        'correlation': 'completed' if has_evidence else 'pending',
        'analysis': analysis_state,
        'recommendation': 'completed' if recommendation_count > 0 else 'pending',
        'report': 'completed' if report_generated else 'pending',
    }

    stages: list[dict[str, Any]] = []
    for key, label in WORKFLOW_STAGES:
        stages.append({'stage': key, 'label': label, 'state': states[key]})
    return stages


# ---------------------------------------------------------------------------
# Assemble the full deterministic analysis
# ---------------------------------------------------------------------------
def build_analysis(
    snapshot: dict[str, Any],
    *,
    triage_status: str | None = None,
    recommendation_count: int = 0,
    report_generated: bool = False,
) -> dict[str, Any]:
    """Assemble the complete deterministic forensic analysis for a snapshot.

    Pure and deterministic: identical input snapshot -> identical output (so a page
    refresh never changes it). ``status`` is ``degraded`` when the snapshot is
    incomplete, else ``completed``.
    """
    findings = derive_findings(snapshot)
    rule_matches = match_rules(snapshot)
    confidence = compute_confidence(snapshot)
    coverage = compute_coverage(snapshot)
    workflow = derive_workflow_stages(
        snapshot,
        triage_status=triage_status,
        recommendation_count=recommendation_count,
        report_generated=report_generated,
    )
    incomplete_reasons = snapshot.get('incomplete_reasons') or []
    degraded = bool(snapshot.get('evidence_incomplete'))

    verified_facts = [f for f in findings if f['verification_state'] == VERIFIED_FACT
                      and f['finding_type'] != 'evidence_gap']
    return {
        'calc_version': CALC_VERSION,
        'status': 'degraded' if degraded else 'completed',
        'degraded_reason': ('; '.join(str(r) for r in incomplete_reasons) or None) if degraded else None,
        'findings': findings,
        'rule_matches': rule_matches,
        'confidence': confidence['confidence'],
        'confidence_band': confidence['band'],
        'confidence_factors': confidence['factors'],
        'confidence_explanation': confidence['explanation'],
        'evidence_coverage': coverage['coverage'],
        'coverage_detail': coverage,
        'workflow_stages': workflow,
        'ruleset': {'name': FORENSIC_RULESET_NAME, 'version': FORENSIC_RULESET_VERSION},
        'counts': {
            'findings': len(findings),
            'verified_facts': len(verified_facts),
            'rule_matches': len(rule_matches),
            'evidence_gaps': sum(1 for f in findings if f['finding_type'] == 'evidence_gap'),
        },
    }


# ---------------------------------------------------------------------------
# Schema readiness + incident reference numbering (DB-facing)
# ---------------------------------------------------------------------------
def forensic_schema_ready(connection: Any) -> bool:
    """True when migration 0135's tables exist. Fail-closed on any probe error."""
    try:
        for table in REQUIRED_TABLES:
            row = connection.execute(
                'SELECT to_regclass(%s) IS NOT NULL AS present', (f'public.{table}',)
            ).fetchone()
            if not bool((row or {}).get('present')):
                return False
        return True
    except Exception:  # pragma: no cover - any failure is treated as not-ready
        return False


def next_incident_reference(connection: Any, *, workspace_id: str, year: int) -> str:
    """Allocate the next INC-YYYY-NNN reference for a workspace/year.

    Concurrency-safe: a single atomic UPSERT ... RETURNING increments a per
    (workspace, year) counter, so two incidents opening at once never collide and no
    number is reused. This intentionally avoids COUNT(*)+1.
    """
    row = connection.execute(
        '''
        INSERT INTO incident_reference_counters (workspace_id, year, counter, updated_at)
        VALUES (%s, %s, 1, NOW())
        ON CONFLICT (workspace_id, year) DO UPDATE
            SET counter = incident_reference_counters.counter + 1, updated_at = NOW()
        RETURNING counter
        ''',
        (workspace_id, int(year)),
    ).fetchone()
    counter = int((row or {}).get('counter') or 1)
    return f'INC-{int(year)}-{counter:03d}'


def assign_incident_reference(connection: Any, *, workspace_id: str, incident_id: str, now: Any) -> str | None:
    """Idempotently assign a human reference to an incident that lacks one.

    Returns the existing reference if already set (never re-numbers). Best-effort and
    tolerant of the migration not being applied yet."""
    if not forensic_schema_ready(connection):
        return None
    existing = connection.execute(
        'SELECT reference FROM incidents WHERE id = %s AND workspace_id = %s',
        (incident_id, workspace_id),
    ).fetchone()
    if existing is None:
        return None
    if existing.get('reference'):
        return str(existing['reference'])
    year = getattr(now, 'year', None) or pilot.utc_now().year
    reference = next_incident_reference(connection, workspace_id=workspace_id, year=year)
    # Only set if still NULL (idempotent under concurrency); the unique index guards
    # against a duplicate reference.
    connection.execute(
        'UPDATE incidents SET reference = %s, updated_at = NOW() WHERE id = %s AND workspace_id = %s AND reference IS NULL',
        (reference, incident_id, workspace_id),
    )
    row = connection.execute(
        'SELECT reference FROM incidents WHERE id = %s AND workspace_id = %s',
        (incident_id, workspace_id),
    ).fetchone()
    return str((row or {}).get('reference') or reference)


# ---------------------------------------------------------------------------
# Persisted analysis upsert
# ---------------------------------------------------------------------------
def _persist_analysis(
    connection: Any,
    *,
    workspace_id: str,
    incident_id: str,
    snapshot_id: str | None,
    snapshot_hash: str,
    analysis: dict[str, Any],
) -> None:
    """Idempotent upsert keyed by (incident, snapshot_hash, calc_version)."""
    connection.execute(
        '''
        INSERT INTO incident_forensic_analyses (
            id, workspace_id, incident_id, evidence_snapshot_id, snapshot_hash, calc_version,
            status, confidence, confidence_band, evidence_coverage, findings_count, rule_match_count,
            analysis_json, degraded_reason, created_at, updated_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, NOW(), NOW())
        ON CONFLICT (incident_id, snapshot_hash, calc_version) DO UPDATE SET
            status = EXCLUDED.status,
            confidence = EXCLUDED.confidence,
            confidence_band = EXCLUDED.confidence_band,
            evidence_coverage = EXCLUDED.evidence_coverage,
            findings_count = EXCLUDED.findings_count,
            rule_match_count = EXCLUDED.rule_match_count,
            analysis_json = EXCLUDED.analysis_json,
            degraded_reason = EXCLUDED.degraded_reason,
            evidence_snapshot_id = COALESCE(EXCLUDED.evidence_snapshot_id, incident_forensic_analyses.evidence_snapshot_id),
            updated_at = NOW()
        ''',
        (
            str(uuid.uuid4()), workspace_id, incident_id, snapshot_id, snapshot_hash, analysis['calc_version'],
            analysis['status'], analysis['confidence'], analysis['confidence_band'], analysis['evidence_coverage'],
            int(analysis['counts']['findings']), int(analysis['counts']['rule_matches']),
            pilot._json_dumps(analysis), analysis.get('degraded_reason'),
        ),
    )


# ---------------------------------------------------------------------------
# Snapshot resolution (reuse ai_triage, prefer the persisted immutable snapshot)
# ---------------------------------------------------------------------------
def _load_latest_snapshot(connection: Any, *, workspace_id: str, incident_id: str) -> dict[str, Any] | None:
    row = connection.execute(
        '''
        SELECT id, snapshot_hash, snapshot_json
        FROM incident_evidence_snapshots
        WHERE incident_id = %s AND workspace_id = %s
        ORDER BY created_at DESC LIMIT 1
        ''',
        (incident_id, workspace_id),
    ).fetchone()
    if row is None:
        return None
    return {'id': str(row.get('id')), 'snapshot_hash': row.get('snapshot_hash'),
            'snapshot': row.get('snapshot_json') or {}}


def _build_and_store_snapshot(connection: Any, *, workspace_id: str, incident_id: str) -> dict[str, Any]:
    assembled = ai_triage.build_evidence_snapshot(connection, workspace_id=workspace_id, incident_id=incident_id)
    stored = ai_triage.store_evidence_snapshot(connection, workspace_id=workspace_id, incident_id=incident_id, assembled=assembled)
    return {'id': stored['id'], 'snapshot_hash': stored['snapshot_hash'], 'snapshot': assembled['snapshot']}


def _triage_status(connection: Any, *, workspace_id: str, incident_id: str) -> str | None:
    if not ai_triage.ai_triage_schema_ready(connection):
        return None
    row = connection.execute(
        'SELECT status FROM ai_triage_jobs WHERE incident_id = %s AND workspace_id = %s ORDER BY created_at DESC LIMIT 1',
        (incident_id, workspace_id),
    ).fetchone()
    return str(row['status']) if row and row.get('status') else None


def _recommendation_count(connection: Any, *, workspace_id: str, incident_id: str) -> int:
    if not ai_triage.ai_triage_schema_ready(connection):
        return 0
    row = connection.execute(
        'SELECT COUNT(*) AS n FROM ai_recommendations WHERE incident_id = %s AND workspace_id = %s',
        (incident_id, workspace_id),
    ).fetchone()
    return int((row or {}).get('n') or 0)


def _report_generated(connection: Any, *, workspace_id: str, incident_id: str) -> bool:
    """A forensic report exists once a completed AI triage report is available OR a
    persisted deterministic analysis has been marked report-generated. Deterministic
    completion alone does not claim a report; the report is generated explicitly."""
    if ai_triage.ai_triage_schema_ready(connection):
        row = connection.execute(
            '''
            SELECT 1 FROM ai_triage_jobs
            WHERE incident_id = %s AND workspace_id = %s AND status IN ('completed', 'completed_with_warnings')
            LIMIT 1
            ''',
            (incident_id, workspace_id),
        ).fetchone()
        if row is not None:
            return True
    return False


def _incident_header(connection: Any, *, workspace_id: str, incident_id: str) -> dict[str, Any]:
    row = connection.execute(
        '''
        SELECT id, reference, title, severity, status, workflow_status, summary, target_id,
               source_alert_id, created_at, updated_at
        FROM incidents WHERE id = %s AND workspace_id = %s
        ''',
        (incident_id, workspace_id),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Incident not found.')
    return dict(row)


# ---------------------------------------------------------------------------
# Route implementations (workspace-scoped, authenticated, audited)
# ---------------------------------------------------------------------------
def _unavailable(incident_id: str) -> dict[str, Any]:
    return {
        'status': 'unavailable',
        'schema_ready': False,
        'incident_id': str(incident_id),
        'message': f'Forensic investigation storage is not initialized. Apply database migration {FORENSIC_SCHEMA_MIGRATION}.',
        'label': 'Deterministic forensic analysis — evidence-grounded.',
    }


def _serialize_header(header: dict[str, Any]) -> dict[str, Any]:
    def _iso(v: Any) -> Any:
        return v.isoformat() if hasattr(v, 'isoformat') else v
    reference = header.get('reference') or f"INC-{str(header.get('id'))[:8]}"
    return {
        'incident_id': str(header.get('id')),
        'reference': reference,
        'title': header.get('title') or 'Untitled incident',
        'severity': header.get('severity'),
        'status': header.get('workflow_status') or header.get('status'),
        'summary': header.get('summary'),
        'target_id': str(header['target_id']) if header.get('target_id') else None,
        'source_alert_id': str(header['source_alert_id']) if header.get('source_alert_id') else None,
        'risk_score': _to_float(header.get('risk_score')),
        'detected_at': _iso(header.get('created_at')),
        'updated_at': _iso(header.get('updated_at')),
    }


def get_investigation(incident_id: str, request: Any) -> dict[str, Any]:
    """Read the deterministic forensic investigation overview for one incident.

    Builds and persists the analysis if missing (idempotent by snapshot hash), lazily
    assigns a human INC-YYYY-NNN reference, and returns the incident header, the
    deterministic findings / rule matches / confidence / coverage / workflow, and the
    AI triage status summary. Workspace-scoped; 404 for a foreign/absent incident.
    """
    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user = pilot.authenticate_with_connection(connection, request)
        workspace_context = pilot.resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        workspace_id = workspace_context['workspace_id']
        if not forensic_schema_ready(connection):
            return _unavailable(incident_id)

        header = _incident_header(connection, workspace_id=workspace_id, incident_id=incident_id)
        now = pilot.utc_now()
        reference = assign_incident_reference(connection, workspace_id=workspace_id, incident_id=incident_id, now=now)
        if reference:
            header['reference'] = reference

        snap = _load_latest_snapshot(connection, workspace_id=workspace_id, incident_id=incident_id)
        if snap is None:
            snap = _build_and_store_snapshot(connection, workspace_id=workspace_id, incident_id=incident_id)

        triage_status = _triage_status(connection, workspace_id=workspace_id, incident_id=incident_id)
        rec_count = _recommendation_count(connection, workspace_id=workspace_id, incident_id=incident_id)
        report_generated = _report_generated(connection, workspace_id=workspace_id, incident_id=incident_id)

        analysis = build_analysis(
            snap['snapshot'], triage_status=triage_status,
            recommendation_count=rec_count, report_generated=report_generated,
        )
        _persist_analysis(
            connection, workspace_id=workspace_id, incident_id=incident_id,
            snapshot_id=snap.get('id'), snapshot_hash=str(snap['snapshot_hash']), analysis=analysis,
        )
        connection.commit()

        evidence = build_evidence_rows(snap['snapshot'])
        return {
            'status': analysis['status'],
            'schema_ready': True,
            'incident': _serialize_header(header),
            'snapshot_hash': snap['snapshot_hash'],
            'analysis': analysis,
            'evidence': evidence,
            'ai_triage': {
                'status': triage_status or 'not_requested',
                'recommendation_count': rec_count,
                'report_available': report_generated,
            },
            'linked': {
                'alerts': 1 if header.get('source_alert_id') else 0,
                'evidence_records': evidence['total'],
                'recommendations': rec_count,
            },
            'label': 'Deterministic forensic analysis — evidence-grounded.',
        }


def get_workflow(incident_id: str, request: Any) -> dict[str, Any]:
    """Return only the persisted workflow stage states for one incident."""
    overview = get_investigation(incident_id, request)
    if overview.get('status') == 'unavailable':
        return {'status': 'unavailable', 'incident_id': str(incident_id), 'stages': [],
                'message': overview.get('message')}
    return {
        'status': overview['status'],
        'incident_id': str(incident_id),
        'stages': overview['analysis']['workflow_stages'],
    }


def rerun_investigation(incident_id: str, request: Any) -> dict[str, Any]:
    """Re-run the deterministic investigation from a FRESH evidence snapshot and,
    when AI triage is enabled and schema-ready, hand off to the AI narrative job.

    Never executes a response action. Idempotent w.r.t. the AI job (``request_triage``
    reuses an in-flight job). Audited.
    """
    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user = pilot.authenticate_with_connection(connection, request)
        workspace_context = pilot.resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        workspace_id = workspace_context['workspace_id']
        if not forensic_schema_ready(connection):
            return _unavailable(incident_id)

        header = _incident_header(connection, workspace_id=workspace_id, incident_id=incident_id)
        now = pilot.utc_now()
        assign_incident_reference(connection, workspace_id=workspace_id, incident_id=incident_id, now=now)

        snap = _build_and_store_snapshot(connection, workspace_id=workspace_id, incident_id=incident_id)
        triage_status = _triage_status(connection, workspace_id=workspace_id, incident_id=incident_id)
        rec_count = _recommendation_count(connection, workspace_id=workspace_id, incident_id=incident_id)
        analysis = build_analysis(
            snap['snapshot'], triage_status=triage_status, recommendation_count=rec_count,
            report_generated=_report_generated(connection, workspace_id=workspace_id, incident_id=incident_id),
        )
        _persist_analysis(
            connection, workspace_id=workspace_id, incident_id=incident_id,
            snapshot_id=snap.get('id'), snapshot_hash=str(snap['snapshot_hash']), analysis=analysis,
        )
        pilot.append_incident_timeline_event(
            connection, workspace_id=workspace_id, incident_id=incident_id,
            event_type='incident.forensic_investigation.rerun',
            message='Deterministic forensic investigation re-run from a fresh evidence snapshot.',
            actor_user_id=user['id'],
            metadata={'snapshot_hash': snap['snapshot_hash'], 'calc_version': analysis['calc_version']},
        )
        try:
            pilot.log_audit(
                connection, action='incident.forensic_investigation.rerun', entity_type='incident',
                entity_id=str(incident_id), request=request, user_id=user['id'], workspace_id=workspace_id,
                metadata={'snapshot_hash': snap['snapshot_hash'], 'calc_version': analysis['calc_version']},
            )
        except Exception:  # pragma: no cover - audit must never break the operation
            logger.warning('event=forensic_rerun_audit_failed incident_id=%s', incident_id)
        connection.commit()

    # Hand off the AI narrative outside the deterministic transaction. This never
    # executes a fund-moving/destructive action — it only queues an analysis job.
    ai_handoff: dict[str, Any] = {'requested': False}
    try:
        with pilot.pg_connection() as connection:
            if ai_triage.ai_triage_schema_ready(connection):
                ai_handoff = {'requested': True, 'result': ai_triage.request_triage(incident_id, request)}
    except HTTPException as exc:
        ai_handoff = {'requested': True, 'error': str(exc.detail)}
    except Exception:  # pragma: no cover - AI handoff must not fail the re-run
        ai_handoff = {'requested': True, 'error': 'ai_handoff_failed'}

    overview = get_investigation(incident_id, request)
    overview['rerun'] = {'snapshot_hash': snap['snapshot_hash'], 'ai_handoff': ai_handoff}
    return overview


def generate_report(incident_id: str, request: Any) -> dict[str, Any]:
    """Generate a persisted, cited forensic report for the incident.

    The report combines the deterministic forensic analysis (always available, every
    statement citing persisted evidence) with the AI narrative when a completed AI
    triage result exists. It does not overwrite prior AI report generations (those are
    versioned by the AI triage result lineage) and it does not execute any response
    action. Screen 9 later packages the cryptographic export.
    """
    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user = pilot.authenticate_with_connection(connection, request)
        workspace_context = pilot.resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        workspace_id = workspace_context['workspace_id']
        if not forensic_schema_ready(connection):
            return _unavailable(incident_id)

        header = _incident_header(connection, workspace_id=workspace_id, incident_id=incident_id)
        now = pilot.utc_now()
        reference = assign_incident_reference(connection, workspace_id=workspace_id, incident_id=incident_id, now=now)
        if reference:
            header['reference'] = reference

        snap = _load_latest_snapshot(connection, workspace_id=workspace_id, incident_id=incident_id)
        if snap is None:
            snap = _build_and_store_snapshot(connection, workspace_id=workspace_id, incident_id=incident_id)

        triage_status = _triage_status(connection, workspace_id=workspace_id, incident_id=incident_id)
        rec_count = _recommendation_count(connection, workspace_id=workspace_id, incident_id=incident_id)
        report_generated = _report_generated(connection, workspace_id=workspace_id, incident_id=incident_id)
        analysis = build_analysis(
            snap['snapshot'], triage_status=triage_status, recommendation_count=rec_count,
            report_generated=report_generated,
        )
        _persist_analysis(
            connection, workspace_id=workspace_id, incident_id=incident_id,
            snapshot_id=snap.get('id'), snapshot_hash=str(snap['snapshot_hash']), analysis=analysis,
        )
        report = build_report(
            header=_serialize_header(header), snapshot=snap['snapshot'],
            snapshot_hash=str(snap['snapshot_hash']), analysis=analysis,
        )
        pilot.append_incident_timeline_event(
            connection, workspace_id=workspace_id, incident_id=incident_id,
            event_type='incident.forensic_report.generated',
            message='Forensic investigation report generated.', actor_user_id=user['id'],
            metadata={'snapshot_hash': snap['snapshot_hash'], 'sections': list(report['sections'].keys())},
        )
        try:
            pilot.log_audit(
                connection, action='incident.forensic_report.generated', entity_type='incident',
                entity_id=str(incident_id), request=request, user_id=user['id'], workspace_id=workspace_id,
                metadata={'snapshot_hash': snap['snapshot_hash']},
            )
        except Exception:  # pragma: no cover - audit must never break the operation
            logger.warning('event=forensic_report_audit_failed incident_id=%s', incident_id)
        connection.commit()

    ai_report = None
    try:
        with pilot.pg_connection() as connection:
            if ai_triage.ai_triage_schema_ready(connection):
                candidate = ai_triage.get_report(incident_id, request)
                if candidate.get('status') == 'available':
                    ai_report = candidate
    except Exception:  # pragma: no cover - AI section is optional
        ai_report = None

    return {
        'status': 'available',
        'incident_id': str(incident_id),
        'report': report,
        'ai_report': ai_report,
        'label': 'Deterministic forensic analysis — evidence-grounded.',
    }


def build_report(*, header: dict[str, Any], snapshot: dict[str, Any], snapshot_hash: str,
                 analysis: dict[str, Any]) -> dict[str, Any]:
    """Assemble the structured, cited forensic report body. Every factual statement
    traces to persisted evidence references carried on the findings/rule matches."""
    verified = [f for f in analysis['findings'] if f['verification_state'] == VERIFIED_FACT
                and f['finding_type'] != 'evidence_gap']
    gaps = [f for f in analysis['findings'] if f['finding_type'] == 'evidence_gap']
    all_refs = sorted({ref for f in analysis['findings'] for ref in f.get('evidence_refs', [])}
                      | {ref for m in analysis['rule_matches'] for ref in m.get('evidence_refs', [])})
    recommended = _recommended_next_step(analysis)
    return {
        'snapshot_hash': snapshot_hash,
        'calc_version': analysis['calc_version'],
        'sections': {
            'incident_identity': {
                'reference': header.get('reference'),
                'incident_id': header.get('incident_id'),
                'title': header.get('title'),
                'severity': header.get('severity'),
                'status': header.get('status'),
                'detected_at': header.get('detected_at'),
            },
            'executive_summary': {
                'text': (f"{len(verified)} verified fact(s) and {len(analysis['rule_matches'])} potential rule "
                         f"match(es) were derived deterministically from the immutable evidence snapshot. "
                         f"Investigation confidence is {analysis['confidence_band']} "
                         f"({analysis['confidence']:.2f}); evidence coverage is "
                         f"{round(analysis['evidence_coverage'] * 100)}%."),
            },
            'detection_origin': {
                'alert_ref': next((r for r in all_refs if r.startswith('alert:')), None),
                'rule_ref': next((r for r in all_refs if r.startswith('rule:')), None),
            },
            'impacted_asset': {
                'target_ref': next((r for r in all_refs if r.startswith('target:')), None),
                'asset_ref': next((r for r in all_refs if r.startswith('asset:')), None),
            },
            'timeline': [
                {'title': f['title'], 'value': f.get('value'), 'evidence_refs': f['evidence_refs']}
                for f in analysis['findings']
                if f['finding_type'] in ('observation_window', 'block_range')
            ],
            'verified_findings': verified,
            'evidence_inventory': all_refs,
            'potential_rule_matches': analysis['rule_matches'],
            'confidence_and_limitations': {
                'confidence': analysis['confidence'],
                'band': analysis['confidence_band'],
                'explanation': analysis['confidence_explanation'],
                'evidence_coverage': analysis['evidence_coverage'],
                'missing_evidence': [g.get('description') for g in gaps],
            },
            'recommended_next_step': recommended,
            'evidence_citations': all_refs,
        },
    }


def _recommended_next_step(analysis: dict[str, Any]) -> dict[str, Any]:
    """A NON-EXECUTING recommendation for the next investigation/response step. It is
    always a recommendation routed to the approval-controlled Screen 8 workflow — never
    an executed action."""
    if analysis['status'] == 'degraded':
        return {'action': 'collect_evidence',
                'label': 'Collect additional evidence to close the identified evidence gaps before response.',
                'executes': False}
    if analysis['rule_matches']:
        return {'action': 'recommend_response',
                'label': 'Hand off to the approval-controlled response workflow for review (no action is executed here).',
                'executes': False}
    return {'action': 'continue_monitoring',
            'label': 'Continue monitoring; deterministic evidence does not yet warrant a response recommendation.',
            'executes': False}
