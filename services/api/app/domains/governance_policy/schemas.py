"""Policy definition, evaluation context, checks, and the PolicyDecision.

Pure data: no DB, no network, no clock, no AI. Everything here is either an
INPUT the service resolved server-side, or the DETERMINISTIC output of the
engine, serialized in one shape so the same object flows

    Screen 11 (policy evaluated) -> Screen 8 (response gated)
                                 -> Screen 7 (incident)
                                 -> Screen 9 (evidence sealed)

without being re-derived, and therefore without being able to disagree with
itself.

The check vocabulary is three-valued:

    PASS            the check ran against real inputs and they satisfied it
    FAIL            the check ran against real inputs and they violated it
    NOT_APPLICABLE  the policy does not impose this constraint at all

There is deliberately no "UNKNOWN" check status here. Screen 5's matcher needs
one because it reconciles against an external source that can be unreachable —
reporting an outage as a breach would manufacture a finding. A policy evaluation
has no such source: the constraints are stored configuration and the inputs are
resolved server-side before the engine runs. So a fact the platform cannot
establish is not an unknown check, it is a VIOLATION of a mandatory requirement,
and it fails closed with its own reason code (SETTLEMENT_STATE_UNKNOWN,
DAILY_TOTAL_UNAVAILABLE, EVALUATION_TIMESTAMP_MISSING). An input the platform
cannot read can never yield ALLOW.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from services.api.app.domains.governance_policy import config as gpc

# Check outcomes.
PASS = 'PASS'
FAIL = 'FAIL'
NOT_APPLICABLE = 'NOT_APPLICABLE'
CHECK_STATUSES = (PASS, FAIL, NOT_APPLICABLE)


def _num(value: Any) -> Any:
    """JSON-safe money. A Decimal never reaches json as a float."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return str(value)
    return value


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        return value.isoformat()
    except AttributeError:
        return str(value)


@dataclass(frozen=True)
class PolicyCheck:
    """One deterministic check. ``status`` is a BACKEND FACT; the frontend
    renders it and never re-decides it."""

    key: str
    status: str
    detail: str
    reason_code: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            'key': self.key,
            'label': gpc.CHECK_LABELS.get(self.key, self.key.replace('_', ' ').title()),
            'status': self.status,
            'detail': self.detail,
            'reason_code': self.reason_code,
        }


@dataclass(frozen=True)
class PolicyDefinition:
    """The stored governance constraints for one operation policy.

    A ``None`` constraint means the policy does not impose it — an authored
    decision, rendered as "Not constrained". It never means "unknown".
    """

    policy_id: str
    policy_key: str
    name: str
    operation: str
    status: str
    version: int
    workspace_id: str = ''
    asset_id: Optional[str] = None
    required_business_event: Optional[str] = None
    settlement_requirement: Optional[str] = None
    allowed_window_start_utc: Optional[str] = None
    allowed_window_end_utc: Optional[str] = None
    maximum_daily_amount_usd: Optional[Decimal] = None
    required_roles: tuple[str, ...] = ()
    violation_action: str = gpc.VIOLATION_ACTION_DENY
    origin: str = gpc.ORIGIN_CUSTOMER
    created_at: Any = None
    updated_at: Any = None
    updated_by: Optional[str] = None

    @property
    def is_active(self) -> bool:
        return self.status == gpc.STATUS_ACTIVE

    @property
    def has_allowed_window(self) -> bool:
        return bool(self.allowed_window_start_utc and self.allowed_window_end_utc)

    def as_dict(self) -> dict[str, Any]:
        """The wire representation of a policy. Values are machine keys; the
        frontend owns every label."""
        return {
            'policy_id': self.policy_id,
            'policy_key': self.policy_key,
            'name': self.name,
            'operation': self.operation,
            'status': self.status,
            'version': self.version,
            'asset_id': self.asset_id,
            'required_business_event': self.required_business_event,
            'settlement_requirement': self.settlement_requirement,
            'allowed_window_utc': (
                {'start': self.allowed_window_start_utc, 'end': self.allowed_window_end_utc}
                if self.has_allowed_window else None
            ),
            'maximum_daily_amount_usd': _num(self.maximum_daily_amount_usd),
            'required_roles': list(self.required_roles),
            'violation_action': self.violation_action,
            'origin': self.origin,
            'created_at': _iso(self.created_at),
            'updated_at': _iso(self.updated_at),
            'updated_by': self.updated_by,
        }


@dataclass(frozen=True)
class EvaluationContext:
    """The operation being judged, as the SERVER resolved it.

    Every field here is either supplied as an operation parameter (amount,
    business event, settlement state) or resolved server-side from canonical
    state (``operator_has_treasury_role``, ``daily_total_usd``). The client never
    asserts a role, a permission, a policy version, or an issuance total — §19.
    """

    operation: Optional[str]
    amount_usd: Optional[Decimal] = None
    operator_id: Optional[str] = None
    #: Resolved from workspace_members + the canonical permission map. None means
    #: the platform could not establish the operator's authority at all.
    operator_has_treasury_role: Optional[bool] = None
    business_event: Optional[str] = None
    settlement_status: Optional[str] = None
    compliance_approval: bool = False
    #: The instant the operation is judged at, in UTC. Required whenever the
    #: policy constrains a time window.
    evaluated_at: Optional[datetime] = None
    #: Amount already permitted under this policy in the current UTC day, from
    #: ENFORCEMENT decisions only. None means the total could not be established.
    daily_total_usd: Optional[Decimal] = None
    #: Canonical lifecycle identifiers, carried through so a policy evaluation
    #: appends to the existing event rather than creating an unrelated object.
    asset_id: Optional[str] = None
    incident_id: Optional[str] = None
    canonical_event_id: Optional[str] = None
    simulation: bool = True

    def as_snapshot(self) -> dict[str, Any]:
        """The input snapshot stored alongside the decision, so the verdict is
        reproducible. Carries no credentials."""
        return {
            'operation': self.operation,
            'amount_usd': _num(self.amount_usd),
            'operator_id': self.operator_id,
            'operator_has_treasury_role': self.operator_has_treasury_role,
            'business_event': self.business_event,
            'settlement_status': self.settlement_status,
            'compliance_approval': bool(self.compliance_approval),
            'evaluated_at': _iso(self.evaluated_at),
            'daily_total_usd': _num(self.daily_total_usd),
            'asset_id': self.asset_id,
            'incident_id': self.incident_id,
            'canonical_event_id': self.canonical_event_id,
            'simulation': bool(self.simulation),
        }


@dataclass(frozen=True)
class PolicyDecision:
    """The authoritative result of a policy evaluation.

    Produced ONLY by engine.evaluate_policy. Every field is deterministic; none
    of them is writable by an AI layer (see DETERMINISTIC_FIELDS below).
    """

    decision: str
    reason_codes: tuple[str, ...]
    checks: tuple[PolicyCheck, ...]
    policy_id: Optional[str]
    policy_key: Optional[str]
    policy_version: Optional[int]
    evaluation_id: str
    evaluated_at: Any
    engine_version: str = gpc.ENGINE_VERSION
    #: The governance roles the policy requires that this context did NOT
    #: evidence — exactly the sign-offs Screen 8 must still collect.
    required_approvals: tuple[str, ...] = ()
    #: Every governance role the policy requires, satisfied or not.
    required_roles: tuple[str, ...] = ()
    operation: Optional[str] = None
    asset_id: Optional[str] = None
    incident_id: Optional[str] = None
    canonical_event_id: Optional[str] = None
    amount_usd: Optional[Decimal] = None
    simulation: bool = True
    violation_action: str = gpc.VIOLATION_ACTION_DENY

    @property
    def is_allowed(self) -> bool:
        return self.decision == gpc.DECISION_ALLOW

    def as_dict(self) -> dict[str, Any]:
        """The canonical wire representation.

        This is the exact object Screen 8's execution gate consumes: it names the
        decision, the reason codes, the policy version that produced them, the
        approvals still outstanding, and the authority that decided.
        """
        return {
            'evaluation_id': self.evaluation_id,
            'policy_id': self.policy_id,
            'policy_key': self.policy_key,
            'policy_version': self.policy_version,
            'decision': self.decision,
            'reason_codes': list(self.reason_codes),
            'required_approvals': list(self.required_approvals),
            'required_roles': list(self.required_roles),
            'approval_permissions': {
                role: gpc.ROLE_PERMISSIONS[role]
                for role in self.required_roles
                if role in gpc.ROLE_PERMISSIONS
            },
            'checks': [c.as_dict() for c in self.checks],
            'operation': self.operation,
            'asset_id': self.asset_id,
            'incident_id': self.incident_id,
            'canonical_event_id': self.canonical_event_id,
            'amount_usd': _num(self.amount_usd),
            'violation_action': self.violation_action,
            'evaluated_at': _iso(self.evaluated_at),
            'engine_version': self.engine_version,
            'simulation': bool(self.simulation),
            # Named on the wire so no consumer has to infer where the verdict
            # came from. Screen 8 renders this as its Execution Authority.
            'decision_authority': DECISION_AUTHORITY,
            'ai_authority': AI_AUTHORITY,
        }


#: What decided, and what AI may do. Rendered verbatim by Screen 11 and Screen 8.
DECISION_AUTHORITY = 'Deterministic Policy Engine'
AI_AUTHORITY = 'Recommend only'

#: The keys an AI layer may never write. Enforced by
#: explanation.merge_ai_explanation, so a model can never become policy authority.
DETERMINISTIC_FIELDS = frozenset({
    'evaluation_id', 'policy_id', 'policy_key', 'policy_version', 'decision',
    'reason_codes', 'required_approvals', 'required_roles', 'approval_permissions',
    'checks', 'operation', 'asset_id', 'incident_id', 'canonical_event_id',
    'amount_usd', 'violation_action', 'evaluated_at', 'engine_version',
    'simulation', 'decision_authority', 'ai_authority',
})


def checks_in_order(checks: dict[str, PolicyCheck]) -> tuple[PolicyCheck, ...]:
    """Serialize the check set in canonical display order."""
    return tuple(checks[key] for key in gpc.CHECK_ORDER if key in checks)
