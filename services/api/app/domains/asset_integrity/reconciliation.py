"""Deterministic RWA reconciliation engine (pure — no DB, no network, no AI).

This module is the single source of truth for Screen 3's Integrity verdict. It
computes the supply variance, runs the authorization matcher over authoritative
issuance/redemption records, and maps the outcome to an explicit machine-readable
status + reason code and a deterministic severity.

Design rules encoded here:

  * Missing or stale authoritative data is NEVER an anomaly. An upstream failure
    resolves to SOURCE_UNAVAILABLE / MISSING_AUTHORITATIVE_DATA /
    STALE_AUTHORITATIVE_DATA — never UNEXPLAINED_VARIANCE. A monitoring product
    that reports "unauthorized issuance" because its transfer-agent feed timed
    out is worse than one that reports nothing.

  * A cryptographically valid transaction is not an authorization. The matcher
    requires a real authoritative record: matching asset, matching operation,
    matching amount, matching business reference (when the chain event carries
    one), completed settlement, and an allowed time window.

  * All arithmetic is integer/Decimal on base units. Never float.

Nothing in this module reads the clock; the caller passes ``now``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Optional, Sequence

# --------------------------------------------------------------------------
# Result statuses — explicit, machine-readable, fail-closed.
# --------------------------------------------------------------------------
RECONCILED = 'RECONCILED'
AUTHORIZED_VARIANCE = 'AUTHORIZED_VARIANCE'
UNEXPLAINED_VARIANCE = 'UNEXPLAINED_VARIANCE'
STALE_AUTHORITATIVE_DATA = 'STALE_AUTHORITATIVE_DATA'
MISSING_AUTHORITATIVE_DATA = 'MISSING_AUTHORITATIVE_DATA'
INSUFFICIENT_EVIDENCE = 'INSUFFICIENT_EVIDENCE'
SOURCE_UNAVAILABLE = 'SOURCE_UNAVAILABLE'

RECONCILIATION_STATUSES = (
    RECONCILED,
    AUTHORIZED_VARIANCE,
    UNEXPLAINED_VARIANCE,
    STALE_AUTHORITATIVE_DATA,
    MISSING_AUTHORITATIVE_DATA,
    INSUFFICIENT_EVIDENCE,
    SOURCE_UNAVAILABLE,
)

# Statuses that assert an operational-integrity ANOMALY (a real, evidenced
# problem with the asset). Everything else is either healthy or a truthful
# statement that truth could not be established — which must never be styled or
# reported as an anomaly, and must never be styled or reported as healthy.
ANOMALY_STATUSES = frozenset({UNEXPLAINED_VARIANCE})

# Statuses that mean "we could not establish truth". Fail-closed: not healthy,
# not an anomaly.
INDETERMINATE_STATUSES = frozenset({
    STALE_AUTHORITATIVE_DATA,
    MISSING_AUTHORITATIVE_DATA,
    INSUFFICIENT_EVIDENCE,
    SOURCE_UNAVAILABLE,
})

# --------------------------------------------------------------------------
# Reason codes — the deterministic "why". Never AI-generated.
# --------------------------------------------------------------------------
MATCHED_AUTHORIZED_ISSUANCE = 'MATCHED_AUTHORIZED_ISSUANCE'
MATCHED_AUTHORIZED_REDEMPTION = 'MATCHED_AUTHORIZED_REDEMPTION'
NO_MATCHING_AUTHORIZED_ISSUANCE = 'NO_MATCHING_AUTHORIZED_ISSUANCE'
NO_MATCHING_AUTHORIZED_REDEMPTION = 'NO_MATCHING_AUTHORIZED_REDEMPTION'
AMOUNT_MISMATCH = 'AMOUNT_MISMATCH'
SETTLEMENT_NOT_COMPLETE = 'SETTLEMENT_NOT_COMPLETE'
REFERENCE_MISMATCH = 'REFERENCE_MISMATCH'
OUTSIDE_AUTHORIZED_WINDOW = 'OUTSIDE_AUTHORIZED_WINDOW'
AUTHORITATIVE_SOURCE_STALE = 'AUTHORITATIVE_SOURCE_STALE'
AUTHORITATIVE_SOURCE_MISSING = 'AUTHORITATIVE_SOURCE_MISSING'
AUTHORITATIVE_SOURCE_UNAVAILABLE = 'AUTHORITATIVE_SOURCE_UNAVAILABLE'
ONCHAIN_OBSERVATION_MISSING = 'ONCHAIN_OBSERVATION_MISSING'
ONCHAIN_OBSERVATION_STALE = 'ONCHAIN_OBSERVATION_STALE'
SUPPLY_MATCHES_AUTHORITATIVE_STATE = 'SUPPLY_MATCHES_AUTHORITATIVE_STATE'

REASON_CODES = (
    MATCHED_AUTHORIZED_ISSUANCE,
    MATCHED_AUTHORIZED_REDEMPTION,
    NO_MATCHING_AUTHORIZED_ISSUANCE,
    NO_MATCHING_AUTHORIZED_REDEMPTION,
    AMOUNT_MISMATCH,
    SETTLEMENT_NOT_COMPLETE,
    REFERENCE_MISMATCH,
    OUTSIDE_AUTHORIZED_WINDOW,
    AUTHORITATIVE_SOURCE_STALE,
    AUTHORITATIVE_SOURCE_MISSING,
    AUTHORITATIVE_SOURCE_UNAVAILABLE,
    ONCHAIN_OBSERVATION_MISSING,
    ONCHAIN_OBSERVATION_STALE,
    SUPPLY_MATCHES_AUTHORITATIVE_STATE,
)

# Matcher outcomes.
MATCH = 'MATCH'
NO_MATCH = 'NO_MATCH'
MATCH_INSUFFICIENT_DATA = 'INSUFFICIENT_DATA'

# Settlement states accepted as "complete" by the matcher. Anything else (or an
# unknown value) is treated as incomplete — fail-closed.
_SETTLED_STATES = frozenset({'settled', 'cleared', 'complete', 'completed', 'final', 'finalized'})


def is_settled(settlement_state: Any) -> bool:
    return str(settlement_state or '').strip().lower() in _SETTLED_STATES


def to_units(value: Any) -> Optional[Decimal]:
    """Parse a base-unit supply value as an exact integer Decimal (never float)."""
    if value is None or value == '':
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not parsed.is_finite():
        return None
    return parsed


def _norm_reference(value: Any) -> str:
    return str(value or '').strip().upper()


def _epoch(ts: Any) -> Optional[float]:
    """Seconds since epoch for a datetime; None for anything else."""
    try:
        return ts.timestamp()
    except (AttributeError, TypeError, ValueError, OSError):
        return None


def age_seconds(observed_at: Any, now: Any) -> Optional[int]:
    a, b = _epoch(observed_at), _epoch(now)
    if a is None or b is None:
        return None
    return int(max(0.0, b - a))


# --------------------------------------------------------------------------
# Inputs
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class OnChainObservation:
    """What the chain said. ``total_supply`` is in base units."""

    total_supply: Optional[Decimal] = None
    observed_at: Any = None
    block_number: Optional[int] = None
    tx_hash: Optional[str] = None
    # The most recent supply-changing event, when one is known.
    last_delta: Optional[Decimal] = None
    last_delta_operation: Optional[str] = None  # 'mint' | 'burn'
    last_delta_at: Any = None
    external_reference: Optional[str] = None
    provider_type: str = 'unknown'
    evidence_source: str = 'live'
    available: bool = True


@dataclass(frozen=True)
class AuthoritativeState:
    """The expected operational/business state from the system of record."""

    expected_total_supply: Optional[Decimal] = None
    observed_at: Any = None
    settlement_state: Optional[str] = None
    source_name: Optional[str] = None
    external_reference: Optional[str] = None
    evidence_source: str = 'live'
    # 'reported' | 'unavailable' | 'error' | 'missing'
    source_status: str = 'reported'


@dataclass(frozen=True)
class AuthorizedIssuance:
    """An authorization record from the authoritative source."""

    id: Optional[str] = None
    operation: str = 'mint'
    amount: Optional[Decimal] = None
    settlement_state: str = 'pending'
    external_reference: Optional[str] = None
    authorized_at: Any = None
    effective_from: Any = None
    effective_until: Any = None
    source_name: Optional[str] = None
    evidence_source: str = 'live'


@dataclass(frozen=True)
class ReconciliationRules:
    """The rule/configuration a snapshot is evaluated under. Stamped onto the
    result so an auditor can reproduce the verdict."""

    rule_id: str = 'RP-17'
    rule_version: int = 4
    # Authoritative data older than this is STALE (not an anomaly).
    authoritative_stale_seconds: int = 3600
    # On-chain observation older than this cannot support a verdict.
    onchain_stale_seconds: int = 3600
    # Absolute base-unit tolerance treated as no variance (rounding/dust).
    variance_tolerance_units: Decimal = Decimal('0')
    # How far before/after the on-chain event an authorization may sit.
    match_window_seconds: int = 86400

    def as_config(self) -> dict[str, Any]:
        return {
            'authoritative_stale_seconds': int(self.authoritative_stale_seconds),
            'onchain_stale_seconds': int(self.onchain_stale_seconds),
            'variance_tolerance_units': str(self.variance_tolerance_units),
            'match_window_seconds': int(self.match_window_seconds),
        }


@dataclass(frozen=True)
class MatchResult:
    outcome: str
    reason_code: Optional[str] = None
    matched: Optional[AuthorizedIssuance] = None
    candidates_considered: int = 0
    rejections: tuple[dict[str, Any], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            'outcome': self.outcome,
            'reason_code': self.reason_code,
            'matched_reference': self.matched.external_reference if self.matched else None,
            'matched_amount': str(self.matched.amount) if (self.matched and self.matched.amount is not None) else None,
            'candidates_considered': self.candidates_considered,
            'rejections': list(self.rejections),
        }


@dataclass
class ReconciliationResult:
    status: str
    reason_code: str
    variance_units: Optional[Decimal]
    observed_supply: Optional[Decimal]
    expected_supply: Optional[Decimal]
    severity: str
    rule_id: str
    rule_version: int
    rule_config: dict[str, Any]
    match: MatchResult
    matched_issuance_id: Optional[str] = None
    onchain_age_seconds: Optional[int] = None
    authoritative_age_seconds: Optional[int] = None
    data_gaps: list[str] = field(default_factory=list)

    @property
    def is_anomaly(self) -> bool:
        return self.status in ANOMALY_STATUSES

    @property
    def is_indeterminate(self) -> bool:
        return self.status in INDETERMINATE_STATUSES

    @property
    def is_healthy(self) -> bool:
        return self.status in (RECONCILED, AUTHORIZED_VARIANCE)


# --------------------------------------------------------------------------
# Deterministic matcher
# --------------------------------------------------------------------------
def match_authorization(
    *,
    operation: Optional[str],
    amount: Optional[Decimal],
    event_reference: Optional[str],
    event_at: Any,
    candidates: Sequence[AuthorizedIssuance],
    rules: ReconciliationRules,
) -> MatchResult:
    """Find an authoritative record that authorizes an observed supply change.

    Every check below must pass. A near-miss records WHY it was rejected so the
    reason code is specific (AMOUNT_MISMATCH beats a bare "no match"), and a
    cryptographically valid transaction with no complete, in-window,
    amount-matching, reference-matching record is NOT authorized.
    """
    op = str(operation or '').strip().lower()
    if op not in ('mint', 'burn') or amount is None:
        return MatchResult(outcome=MATCH_INSUFFICIENT_DATA, reason_code=None, candidates_considered=0)

    magnitude = abs(amount)
    wanted_reference = _norm_reference(event_reference)
    event_epoch = _epoch(event_at)
    window = max(0, int(rules.match_window_seconds))

    considered = 0
    rejections: list[dict[str, Any]] = []
    # Rejection reasons ranked most→least specific, so the reported reason code
    # describes the closest candidate rather than the last one examined.
    priority = {
        SETTLEMENT_NOT_COMPLETE: 4,
        REFERENCE_MISMATCH: 3,
        OUTSIDE_AUTHORIZED_WINDOW: 2,
        AMOUNT_MISMATCH: 1,
    }
    best_reason: Optional[str] = None

    def note(reason: str, candidate: AuthorizedIssuance) -> None:
        nonlocal best_reason
        rejections.append({'reason_code': reason, 'reference': candidate.external_reference})
        if best_reason is None or priority.get(reason, 0) > priority.get(best_reason, 0):
            best_reason = reason

    for candidate in candidates:
        if str(candidate.operation or '').strip().lower() != op:
            continue  # different operation entirely — not a near miss
        considered += 1

        candidate_amount = to_units(candidate.amount)
        if candidate_amount is None or abs(candidate_amount) != magnitude:
            note(AMOUNT_MISMATCH, candidate)
            continue

        if wanted_reference and _norm_reference(candidate.external_reference) != wanted_reference:
            note(REFERENCE_MISMATCH, candidate)
            continue

        if not is_settled(candidate.settlement_state):
            note(SETTLEMENT_NOT_COMPLETE, candidate)
            continue

        if event_epoch is not None and not _within_window(candidate, event_epoch, window):
            note(OUTSIDE_AUTHORIZED_WINDOW, candidate)
            continue

        return MatchResult(
            outcome=MATCH,
            reason_code=MATCHED_AUTHORIZED_ISSUANCE if op == 'mint' else MATCHED_AUTHORIZED_REDEMPTION,
            matched=candidate,
            candidates_considered=considered,
            rejections=tuple(rejections),
        )

    fallback = NO_MATCHING_AUTHORIZED_ISSUANCE if op == 'mint' else NO_MATCHING_AUTHORIZED_REDEMPTION
    return MatchResult(
        outcome=NO_MATCH,
        reason_code=best_reason or fallback,
        candidates_considered=considered,
        rejections=tuple(rejections),
    )


def _within_window(candidate: AuthorizedIssuance, event_epoch: float, window: int) -> bool:
    """An explicit effective_from/effective_until always wins; otherwise the
    authorization must sit within ``window`` seconds of the on-chain event."""
    start = _epoch(candidate.effective_from)
    end = _epoch(candidate.effective_until)
    if start is not None or end is not None:
        if start is not None and event_epoch < start:
            return False
        if end is not None and event_epoch > end:
            return False
        return True
    authorized = _epoch(candidate.authorized_at)
    if authorized is None:
        return True  # no temporal evidence either way — do not reject on time
    return abs(event_epoch - authorized) <= window


# --------------------------------------------------------------------------
# Deterministic severity
# --------------------------------------------------------------------------
def compute_severity(
    *,
    status: str,
    reason_code: str,
    variance_units: Optional[Decimal],
    expected_supply: Optional[Decimal],
    operation: Optional[str],
) -> str:
    """Severity for a reconciliation result. Deterministic — never AI-decided.

    Only a real, evidenced anomaly carries high/critical. An indeterminate
    result (stale/missing/unavailable source) is a data-quality problem: it is
    reported as medium so it is visible and actionable, never as critical (which
    would assert an integrity breach we cannot evidence).
    """
    if status in (RECONCILED, AUTHORIZED_VARIANCE):
        return 'low'
    if status in INDETERMINATE_STATUSES:
        return 'medium'
    if status != UNEXPLAINED_VARIANCE:
        return 'medium'

    # Unexplained variance. An unauthorized mint (supply created with no
    # authorization) is the most severe case in the RWA threat model.
    unauthorized_issuance = (
        str(operation or '').strip().lower() == 'mint'
        or (variance_units is not None and variance_units > 0)
    )
    magnitude_ratio = None
    if variance_units is not None and expected_supply not in (None, Decimal('0')):
        try:
            magnitude_ratio = abs(variance_units) / abs(expected_supply)
        except (InvalidOperation, ZeroDivisionError):
            magnitude_ratio = None

    if unauthorized_issuance and reason_code in (
        NO_MATCHING_AUTHORIZED_ISSUANCE,
        NO_MATCHING_AUTHORIZED_REDEMPTION,
        REFERENCE_MISMATCH,
    ):
        return 'critical'
    if unauthorized_issuance:
        return 'critical' if (magnitude_ratio is not None and magnitude_ratio >= Decimal('0.01')) else 'high'
    return 'high'


# --------------------------------------------------------------------------
# The engine
# --------------------------------------------------------------------------
def evaluate(
    *,
    onchain: Optional[OnChainObservation],
    authoritative: Optional[AuthoritativeState],
    authorizations: Sequence[AuthorizedIssuance] = (),
    rules: Optional[ReconciliationRules] = None,
    now: Any = None,
) -> ReconciliationResult:
    """Reconcile an on-chain observation against authoritative business state.

    Order matters and is deliberately fail-closed: every way of NOT having
    trustworthy inputs is resolved before any variance is interpreted, so an
    upstream failure can never be reported as an unauthorized issuance.
    """
    rules = rules or ReconciliationRules()
    data_gaps: list[str] = []
    empty_match = MatchResult(outcome=MATCH_INSUFFICIENT_DATA)

    def result(status: str, reason_code: str, **kw: Any) -> ReconciliationResult:
        variance = kw.pop('variance_units', None)
        observed = kw.pop('observed_supply', None)
        expected = kw.pop('expected_supply', None)
        match = kw.pop('match', empty_match)
        operation = kw.pop('operation', None)
        return ReconciliationResult(
            status=status,
            reason_code=reason_code,
            variance_units=variance,
            observed_supply=observed,
            expected_supply=expected,
            severity=compute_severity(
                status=status, reason_code=reason_code, variance_units=variance,
                expected_supply=expected, operation=operation,
            ),
            rule_id=rules.rule_id,
            rule_version=int(rules.rule_version),
            rule_config=rules.as_config(),
            match=match,
            matched_issuance_id=(match.matched.id if match.matched else None),
            onchain_age_seconds=kw.pop('onchain_age_seconds', None),
            authoritative_age_seconds=kw.pop('authoritative_age_seconds', None),
            data_gaps=list(data_gaps),
        )

    # 1. On-chain observation must exist and be usable.
    if onchain is None or not onchain.available or onchain.total_supply is None:
        data_gaps.append('No on-chain supply observation is stored for this asset.')
        return result(INSUFFICIENT_EVIDENCE, ONCHAIN_OBSERVATION_MISSING)

    observed_supply = to_units(onchain.total_supply)
    if observed_supply is None:
        data_gaps.append('The stored on-chain supply observation is not a usable number.')
        return result(INSUFFICIENT_EVIDENCE, ONCHAIN_OBSERVATION_MISSING)

    onchain_age = age_seconds(onchain.observed_at, now)
    if onchain_age is not None and onchain_age > max(0, int(rules.onchain_stale_seconds)):
        data_gaps.append('The on-chain observation is older than the configured freshness threshold.')
        return result(
            INSUFFICIENT_EVIDENCE, ONCHAIN_OBSERVATION_STALE,
            observed_supply=observed_supply, onchain_age_seconds=onchain_age,
        )

    # 2. Authoritative source failure is NEVER a variance.
    if authoritative is None or str(authoritative.source_status or '').strip().lower() == 'missing':
        data_gaps.append('No authoritative off-chain state is configured or recorded for this asset.')
        return result(
            MISSING_AUTHORITATIVE_DATA, AUTHORITATIVE_SOURCE_MISSING,
            observed_supply=observed_supply, onchain_age_seconds=onchain_age,
        )

    source_status = str(authoritative.source_status or '').strip().lower()
    if source_status in ('unavailable', 'error'):
        data_gaps.append('The authoritative source did not return a usable state on its last attempt.')
        return result(
            SOURCE_UNAVAILABLE, AUTHORITATIVE_SOURCE_UNAVAILABLE,
            observed_supply=observed_supply, onchain_age_seconds=onchain_age,
        )

    expected_supply = to_units(authoritative.expected_total_supply)
    if expected_supply is None:
        data_gaps.append('The authoritative source reported no expected supply value.')
        return result(
            MISSING_AUTHORITATIVE_DATA, AUTHORITATIVE_SOURCE_MISSING,
            observed_supply=observed_supply, onchain_age_seconds=onchain_age,
        )

    authoritative_age = age_seconds(authoritative.observed_at, now)
    if authoritative_age is not None and authoritative_age > max(0, int(rules.authoritative_stale_seconds)):
        data_gaps.append('The authoritative state is older than the configured freshness threshold.')
        return result(
            STALE_AUTHORITATIVE_DATA, AUTHORITATIVE_SOURCE_STALE,
            observed_supply=observed_supply, expected_supply=expected_supply,
            variance_units=observed_supply - expected_supply,
            onchain_age_seconds=onchain_age, authoritative_age_seconds=authoritative_age,
        )

    # 3. Both sides are trustworthy — now the variance means something.
    variance_units = observed_supply - expected_supply
    tolerance = abs(to_units(rules.variance_tolerance_units) or Decimal('0'))
    ages = {'onchain_age_seconds': onchain_age, 'authoritative_age_seconds': authoritative_age}

    if abs(variance_units) <= tolerance:
        return result(
            RECONCILED, SUPPLY_MATCHES_AUTHORITATIVE_STATE,
            observed_supply=observed_supply, expected_supply=expected_supply,
            variance_units=variance_units, **ages,
        )

    # 4. A variance exists. Is the supply change authorized?
    operation = str(onchain.last_delta_operation or '').strip().lower() or None
    delta = to_units(onchain.last_delta)
    if operation is None or delta is None:
        # Infer the operation from the variance direction so a variance is still
        # matched when no discrete event was captured.
        operation = 'mint' if variance_units > 0 else 'burn'
        delta = abs(variance_units)

    match = match_authorization(
        operation=operation,
        amount=delta,
        event_reference=onchain.external_reference,
        event_at=onchain.last_delta_at or onchain.observed_at,
        candidates=authorizations,
        rules=rules,
    )

    if match.outcome == MATCH:
        return result(
            AUTHORIZED_VARIANCE, match.reason_code or MATCHED_AUTHORIZED_ISSUANCE,
            observed_supply=observed_supply, expected_supply=expected_supply,
            variance_units=variance_units, match=match, operation=operation, **ages,
        )

    if match.outcome == MATCH_INSUFFICIENT_DATA:
        data_gaps.append('The observed supply change could not be resolved to a mint or burn operation.')
        return result(
            INSUFFICIENT_EVIDENCE, ONCHAIN_OBSERVATION_MISSING,
            observed_supply=observed_supply, expected_supply=expected_supply,
            variance_units=variance_units, match=match, **ages,
        )

    fallback = NO_MATCHING_AUTHORIZED_ISSUANCE if operation == 'mint' else NO_MATCHING_AUTHORIZED_REDEMPTION
    return result(
        UNEXPLAINED_VARIANCE, match.reason_code or fallback,
        observed_supply=observed_supply, expected_supply=expected_supply,
        variance_units=variance_units, match=match, operation=operation, **ages,
    )
