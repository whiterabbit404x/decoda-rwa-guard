"""Canonical operational-integrity event object and structured check records.

Pure data: no DB, no network, no clock, no AI. Everything in this module is the
DETERMINISTIC output of the matcher, serialized in one shape so the same object
can flow Screen 3 -> Screen 5 -> Screen 11 -> Screen 8 -> Screen 7 -> Screen 9
without being re-derived (and therefore without being able to disagree with
itself).

The check vocabulary is deliberately three-valued:

    PASS     the check ran against real evidence and the evidence satisfied it
    FAIL     the check ran against real evidence and the evidence contradicted it
    UNKNOWN  the check could NOT run — the authoritative source is missing,
             unavailable, or stale

UNKNOWN is not a soft FAIL. Reporting "no authorized issuance" because a
transfer-agent feed timed out would manufacture an integrity breach out of an
outage, so an UNKNOWN check can never produce an anomaly conclusion.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional

# Check outcomes.
PASS = 'PASS'
FAIL = 'FAIL'
UNKNOWN = 'UNKNOWN'
CHECK_STATUSES = (PASS, FAIL, UNKNOWN)

# The canonical check keys rendered by the Operational Integrity Analysis panel,
# in display order.
CHECK_ON_CHAIN_EVENT = 'on_chain_event'
CHECK_TRANSFER_AGENT_MATCH = 'transfer_agent_match'
CHECK_SETTLEMENT_MATCH = 'settlement_match'
CHECK_SIGNER_VALIDITY = 'signer_validity'
CHECK_ORDER = (
    CHECK_ON_CHAIN_EVENT,
    CHECK_TRANSFER_AGENT_MATCH,
    CHECK_SETTLEMENT_MATCH,
    CHECK_SIGNER_VALIDITY,
)

CHECK_LABELS: dict[str, str] = {
    CHECK_ON_CHAIN_EVENT: 'On-Chain Event',
    CHECK_TRANSFER_AGENT_MATCH: 'Transfer-Agent',
    CHECK_SETTLEMENT_MATCH: 'Settlement',
    CHECK_SIGNER_VALIDITY: 'Signer Validity',
}

# Conclusions.
CONCLUSION_OPERATIONALLY_AUTHORIZED = 'OPERATIONALLY_AUTHORIZED'
CONCLUSION_CRITICAL_OPERATIONAL_ANOMALY = 'CRITICAL_OPERATIONAL_ANOMALY'
CONCLUSION_OPERATIONAL_ANOMALY = 'OPERATIONAL_ANOMALY'
CONCLUSION_INDETERMINATE = 'INDETERMINATE'

CONCLUSIONS = (
    CONCLUSION_OPERATIONALLY_AUTHORIZED,
    CONCLUSION_CRITICAL_OPERATIONAL_ANOMALY,
    CONCLUSION_OPERATIONAL_ANOMALY,
    CONCLUSION_INDETERMINATE,
)

# Telemetry stages — what the ingestion path ACTUALLY delivered.
STAGE_PRECONFIRMATION = 'PRECONFIRMATION'
STAGE_CONFIRMED = 'CONFIRMED'
STAGE_FINALIZED = 'FINALIZED'
STAGE_UNKNOWN = 'UNKNOWN'
TELEMETRY_STAGES = (STAGE_PRECONFIRMATION, STAGE_CONFIRMED, STAGE_FINALIZED, STAGE_UNKNOWN)


def _num(value: Any) -> Any:
    """JSON-safe numeric. Base-unit amounts are exact integers; a Decimal is
    never handed to json as a float unless it genuinely has a fraction."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else str(value)
    return value


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        return value.isoformat()
    except AttributeError:
        return str(value)


@dataclass(frozen=True)
class OperationalCheck:
    """One deterministic check. ``status`` is a BACKEND FACT; the frontend
    renders it and never re-decides it."""

    key: str
    status: str
    reason: str
    # Where the evidence for this check came from (a table name, a provider, a
    # chain). Empty when the check could not run.
    source: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            'key': self.key,
            'label': CHECK_LABELS.get(self.key, self.key.replace('_', ' ').title()),
            'status': self.status,
            'reason': self.reason,
            'source': self.source,
        }


def checks_as_dict(checks: dict[str, OperationalCheck]) -> dict[str, Any]:
    """Serialize the check set in canonical display order."""
    return {key: checks[key].as_dict() for key in CHECK_ORDER if key in checks}


def conclusion_from(checks: dict[str, OperationalCheck], severity: str) -> str:
    """Derive the panel conclusion from the checks alone.

    A FAIL anywhere is an anomaly (critical when the deterministic severity says
    so). An UNKNOWN with no FAIL is INDETERMINATE — never authorized, never an
    anomaly. Only an all-PASS set is operationally authorized."""
    statuses = {c.status for c in checks.values()}
    if FAIL in statuses:
        return (
            CONCLUSION_CRITICAL_OPERATIONAL_ANOMALY
            if str(severity or '').lower() == 'critical'
            else CONCLUSION_OPERATIONAL_ANOMALY
        )
    if UNKNOWN in statuses:
        return CONCLUSION_INDETERMINATE
    return CONCLUSION_OPERATIONALLY_AUTHORIZED


@dataclass
class OperationalIntegrityEvent:
    """Decoda's canonical operational-integrity event.

    Field names are shared with the Screen 3 reconciliation payload wherever the
    meaning is identical, so one object survives the whole
    detection -> alert -> incident -> evidence lifecycle.
    """

    workspace_id: str
    asset_id: Optional[str]
    category: str
    detection_type: str
    severity: str
    status: str
    deterministic_reason_code: str
    confidence: Decimal
    checks: dict[str, OperationalCheck]
    conclusion: str

    event_id: Optional[str] = None
    event_type: Optional[str] = None
    chain_id: Optional[int] = None
    operation: Optional[str] = None

    observed_amount: Optional[Decimal] = None
    expected_amount: Optional[Decimal] = None
    variance_amount: Optional[Decimal] = None
    amount_decimals: Optional[int] = None
    amount_unit: Optional[str] = None

    tx_hash: Optional[str] = None
    block_number: Optional[int] = None
    telemetry_source: Optional[str] = None
    telemetry_stage: str = STAGE_UNKNOWN
    telemetry_observed_at: Any = None
    preconfirmation_received_at: Any = None

    external_reference: Optional[str] = None
    matched_authorization_reference: Optional[str] = None
    matcher_version: Optional[str] = None
    evidence_source: str = 'live'
    provenance: dict[str, Any] = field(default_factory=dict)
    first_seen_at: Any = None
    incident_id: Optional[str] = None
    cluster_key: Optional[str] = None

    @property
    def is_anomaly(self) -> bool:
        return self.conclusion in (
            CONCLUSION_CRITICAL_OPERATIONAL_ANOMALY,
            CONCLUSION_OPERATIONAL_ANOMALY,
        )

    @property
    def is_indeterminate(self) -> bool:
        return self.conclusion == CONCLUSION_INDETERMINATE

    def as_dict(self) -> dict[str, Any]:
        """The canonical wire representation consumed by Screen 5 and reused by
        the downstream screens."""
        return {
            'event_id': self.event_id,
            'workspace_id': self.workspace_id,
            'asset_id': self.asset_id,
            'chain_id': self.chain_id,
            'event_type': self.event_type,
            'category': self.category,
            'detection_type': self.detection_type.upper(),
            'severity': str(self.severity or '').upper(),
            'status': str(self.status or '').upper(),
            'operation': self.operation,
            'observed_amount': _num(self.observed_amount),
            'expected_amount': _num(self.expected_amount),
            'variance_amount': _num(self.variance_amount),
            'amount_decimals': self.amount_decimals,
            'amount_unit': self.amount_unit,
            'tx_hash': self.tx_hash,
            'block_number': self.block_number,
            'telemetry_source': self.telemetry_source,
            'telemetry_stage': self.telemetry_stage,
            'telemetry_observed_at': _iso(self.telemetry_observed_at),
            'preconfirmation_received_at': _iso(self.preconfirmation_received_at),
            'first_seen_at': _iso(self.first_seen_at),
            'operational_checks': checks_as_dict(self.checks),
            'conclusion': self.conclusion,
            'deterministic_reason_code': self.deterministic_reason_code,
            'confidence': float(self.confidence),
            'external_reference': self.external_reference,
            'matched_authorization_reference': self.matched_authorization_reference,
            'matcher_version': self.matcher_version,
            'evidence_source': self.evidence_source,
            'provenance': dict(self.provenance or {}),
            'cluster_key': self.cluster_key,
            'incident_id': self.incident_id,
        }


# The deterministic keys an AI layer may never write. Enforced by
# explanation.merge_ai_narrative so a model can never become detection authority.
DETERMINISTIC_FIELDS = frozenset({
    'event_id', 'workspace_id', 'asset_id', 'chain_id', 'category', 'detection_type',
    'severity', 'status', 'operation', 'observed_amount', 'expected_amount',
    'variance_amount', 'amount_decimals', 'amount_unit', 'tx_hash', 'block_number',
    'telemetry_source', 'telemetry_stage', 'telemetry_observed_at',
    'preconfirmation_received_at', 'operational_checks', 'conclusion',
    'deterministic_reason_code', 'confidence', 'external_reference',
    'matched_authorization_reference', 'matcher_version', 'evidence_source',
    'provenance', 'cluster_key', 'incident_id', 'first_seen_at',
})
