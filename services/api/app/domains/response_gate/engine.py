"""The deterministic execution gate.

    evaluate_gate(inputs) -> ExecutionGate

This function is the ONLY code in the product that decides whether a response
action may execute.

Trust boundary
--------------
This module imports ``config`` and the standard library. It has no database
handle, no HTTP client, no provider registry, no clock it did not receive, and
no import path that reaches ``ai_providers``. An LLM cannot participate in the
decision even by accident: ``GateInputs`` has no field an AI layer could write,
so there is no seam through which a model could reach a verdict.

Determinism
-----------
Same inputs -> same decision, same reason codes, in the same order.

Fail-closed
-----------
Every path that cannot establish a fact leaves the gate LOCKED with an explicit
reason code. There is no branch in this file that reaches ``can_execute = True``
from missing, unreadable, or malformed input.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from services.api.app.domains.response_gate import config as rgc


#: Who the action NAMES as the authority for its human approval requirement.
#:   workspace_approvers   — named operators recording decisions on Screen 8.
#:   delegated_governance  — an external governance module is named as the
#:                           authority for this execution path.
#:
#: Naming an authority is not evidence FROM it. This deployment has no
#: verifiable delegation record — no signed attestation, no persisted signer
#: set, no identity that could be attributed in an audit — so
#: ``delegated_governance`` can never substitute for the approvals a numeric
#: quorum requires. It is reported on the gate so an operator can see WHICH
#: authority the action claims, and the quorum is still collected from real
#: humans. See ``_quorum_met``.
QUORUM_AUTHORITY_WORKSPACE = 'workspace_approvers'
QUORUM_AUTHORITY_DELEGATED = 'delegated_governance'
QUORUM_AUTHORITY_LABELS: dict[str, str] = {
    QUORUM_AUTHORITY_WORKSPACE: 'Workspace approvers',
    QUORUM_AUTHORITY_DELEGATED: 'Delegated governance authority',
}


def _utc(moment: Any) -> Optional[datetime]:
    """Parse a timestamp to an aware UTC datetime. None when unusable."""
    if moment is None:
        return None
    if isinstance(moment, datetime):
        return moment if moment.tzinfo is not None else moment.replace(tzinfo=timezone.utc)
    text = str(moment).strip()
    if not text:
        return None
    if text.endswith('Z'):
        text = f'{text[:-1]}+00:00'
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class ApprovalRecord:
    """One persisted human approval decision, as the gate reads it.

    ``role`` is the GOVERNANCE role the decision was cast for, verified
    server-side when it was recorded. ``None`` means the decision predates
    role-scoped approval and therefore counts toward the numeric quorum only.
    """

    approver_user_id: str
    decision: str
    role: Optional[str] = None
    approver_label: Optional[str] = None
    decided_at: Optional[str] = None

    @property
    def approved(self) -> bool:
        return str(self.decision or '').strip().lower() == 'approved'

    @property
    def rejected(self) -> bool:
        return str(self.decision or '').strip().lower() == 'rejected'


@dataclass(frozen=True)
class GateInputs:
    """Already-resolved facts the gate reasons over.

    Every field is a canonical backend fact. There is deliberately no field for
    an AI recommendation, an AI confidence, or any frontend-supplied state.
    """

    action_id: str
    #: Screen 11's verdict, reflected verbatim. Never recalculated here.
    policy_decision: str = rgc.POLICY_NOT_APPLICABLE
    #: HOW the enforcement evaluation behind ``policy_decision`` matched THIS
    #: action, resolved server-side from the persisted row (see
    #: ``response_gate.config`` and ``service.evaluation_match_provenance``).
    #: Only ACTION_SPECIFIC names one action; the rest matched a SHARED incident,
    #: asset or event id and therefore belong to some other action's decision.
    #: Never client-supplied — the gate publishes it, nothing asserts it.
    policy_match_provenance: str = rgc.MATCH_NONE
    policy_reason_codes: tuple[str, ...] = ()
    policy_id: Optional[str] = None
    policy_key: Optional[str] = None
    policy_version: Optional[int] = None
    #: The version the governing policy carries NOW. A difference means the
    #: evaluation was produced under superseded rules.
    policy_current_version: Optional[int] = None
    evaluation_id: Optional[str] = None
    evaluated_at: Optional[str] = None

    #: Approver roles the governing policy names (Screen 11 ``required_roles``,
    #: falling back to ``required_approvals`` on a pre-0149 evaluation record).
    #: Every one needs a persisted human decision before this action may execute.
    required_roles: tuple[str, ...] = ()
    #: Every persisted decision for THIS action version.
    approvals: tuple[ApprovalRecord, ...] = ()
    #: Numeric quorum from the action's own approval policy.
    required_quorum: int = 0
    #: Whether a human approval is required for this action AT ALL — the canonical
    #: lifecycle fact (``response_action_lifecycle.requires_approval``), derived
    #: from the deterministic playbook profile. When it is False and no policy
    #: role is outstanding there is NO quorum, and the gate reports quorum 0 so
    #: the UI can never print "0 / 1" beside "Requires Approval: No". A policy
    #: that names approver roles still binds: it raises the requirement, and this
    #: flag can never lower it.
    approval_required: bool = True
    #: The canonical lifecycle approval status when no approval rows exist yet
    #: ('approved' / 'pending' / 'rejected' / 'not_required').
    lifecycle_approval_status: str = 'not_required'
    #: Set when this action's authorization is deliberately DELEGATED to an
    #: external deterministic authority (a governance module with its own signer
    #: quorum) rather than collected from workspace approvers. Named, never
    #: implied: the gate reports WHICH authority satisfied the quorum, so a
    #: delegated approval is never displayed as a workspace operator's sign-off.
    quorum_authority: str = QUORUM_AUTHORITY_WORKSPACE

    action_status: str = 'pending'
    execution_status: str = 'not_started'
    rejected: bool = False
    cancelled: bool = False
    expires_at: Optional[str] = None
    now: Optional[datetime] = None

    incident_id: Optional[str] = None
    incident_status: Optional[str] = None

    #: RBAC, resolved server-side from workspace_members. Never client-asserted.
    requester_authorized: bool = True
    requester_permission_reason: Optional[str] = None

    #: A deterministic execution path exists for this action/mode.
    execution_authority_available: bool = True
    #: A real execution adapter is configured for this deployment.
    execution_adapter_configured: bool = False
    #: Whether RUNNING this action would contact an external provider, so a
    #: configured adapter is a precondition for the run. False for a simulation,
    #: a recommendation, or a manual-only containment request: those contact
    #: nothing, and an absent adapter must not block them.
    execution_adapter_required: bool = False
    execution_adapter_label: Optional[str] = None

    #: Canonical facts the caller tried to read and could NOT (a failed query, a
    #: database outage). Deliberately distinct from a fact that came back absent:
    #: "we could not look" is not the same answer as "there is none". Any entry
    #: here closes the gate, so an outage can never widen authorization.
    unreadable_facts: tuple[str, ...] = ()

    #: Canonical lifecycle identifiers, carried through so the gate is traceable
    #: to the same event object every other screen uses.
    chain: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionGate:
    """The authoritative execution-gate result.

    Produced ONLY by ``evaluate_gate``. ``can_execute`` is the single fact the
    execute command and the UI both read; neither re-derives it.
    """

    decision: str
    can_execute: bool
    reason_codes: tuple[str, ...]
    policy_decision: str
    required_quorum: int
    approvals_collected: int
    #: Whether ANY human approval is required. False means required_quorum is 0
    #: and no roster row exists — the two can never disagree.
    approval_required: bool
    required_roles: tuple[str, ...]
    satisfied_roles: tuple[str, ...]
    missing_roles: tuple[str, ...]
    approvers: tuple[dict[str, Any], ...]
    #: The AUTHORIZATION verdict on its own — policy, quorum, RBAC, lifecycle.
    #: Deliberately separate from ``decision``: an action can be fully authorized
    #: and still not runnable because this deployment has no adapter to run it
    #: with. Collapsing the two would either hide a missing adapter behind an
    #: AUTHORIZED badge or misreport a capability gap as a policy refusal.
    authorization_decision: str = rgc.GATE_LOCKED
    #: Whether an execution attempt could actually be carried out here: either the
    #: action contacts no external provider, or the adapter it needs is configured.
    execution_ready: bool = False
    #: Whether running this action would contact an external provider at all.
    execution_adapter_required: bool = False
    quorum_authority: str = QUORUM_AUTHORITY_WORKSPACE
    #: HOW the policy evaluation behind this gate matched the action. Reported for
    #: audit, so a reader can tell an action's OWN verdict from a sibling's that
    #: was refused. An output only: nothing reads it back as an authorization.
    policy_match_provenance: str = rgc.MATCH_NONE
    policy_id: Optional[str] = None
    policy_key: Optional[str] = None
    policy_version: Optional[int] = None
    evaluation_id: Optional[str] = None
    evaluated_at: Optional[str] = None
    incident_id: Optional[str] = None
    expires_at: Optional[str] = None
    execution_adapter_configured: bool = False
    execution_adapter_label: Optional[str] = None
    chain: dict[str, Any] = field(default_factory=dict)
    gate_version: str = rgc.GATE_VERSION

    @property
    def locked(self) -> bool:
        return not self.can_execute

    def as_dict(self) -> dict[str, Any]:
        """The canonical wire representation consumed by Screen 8.

        It names the decision, whether execution is permitted, the deterministic
        reason codes, the policy verdict that fed it, and the quorum still
        outstanding — plus the two authority constants, so the trust boundary is
        stated by the backend rather than by UI copy.
        """
        return {
            'decision': self.decision,
            'decision_label': rgc.GATE_DECISION_LABELS.get(self.decision, self.decision),
            'can_execute': bool(self.can_execute),
            # POLICY AUTHORIZATION is not EXECUTION CAPABILITY. Both are on the
            # wire, separately, so a missing adapter is never rendered as a policy
            # refusal and an authorization is never rendered as a run that could
            # actually happen.
            'authorization_decision': self.authorization_decision,
            'authorization_decision_label': rgc.GATE_DECISION_LABELS.get(
                self.authorization_decision, self.authorization_decision,
            ),
            'execution_ready': bool(self.execution_ready),
            'execution_adapter_required': bool(self.execution_adapter_required),
            'policy_decision': self.policy_decision,
            'policy_decision_label': rgc.POLICY_DECISION_LABELS.get(
                self.policy_decision, self.policy_decision,
            ),
            # Audit metadata, never an authorization input. The gate STATES how
            # its evaluation matched; no caller may assert it back.
            'policy_match_provenance': self.policy_match_provenance,
            'policy_match_provenance_label': rgc.MATCH_PROVENANCE_LABELS.get(
                self.policy_match_provenance, self.policy_match_provenance,
            ),
            'required_quorum': int(self.required_quorum),
            'approvals_collected': int(self.approvals_collected),
            'approval_required': bool(self.approval_required),
            'required_roles': list(self.required_roles),
            'satisfied_roles': list(self.satisfied_roles),
            'missing_roles': list(self.missing_roles),
            'missing_role_labels': [rgc.approver_role_label(r) for r in self.missing_roles],
            'approvers': [dict(a) for a in self.approvers],
            'quorum_authority': self.quorum_authority,
            'quorum_authority_label': QUORUM_AUTHORITY_LABELS.get(
                self.quorum_authority, self.quorum_authority,
            ),
            'reason_codes': list(self.reason_codes),
            'reasons': [
                {'code': code, 'label': rgc.reason_label(code)} for code in self.reason_codes
            ],
            'policy_id': self.policy_id,
            'policy_key': self.policy_key,
            'policy_version': self.policy_version,
            'evaluation_id': self.evaluation_id,
            'evaluated_at': self.evaluated_at,
            'incident_id': self.incident_id,
            'expires_at': self.expires_at,
            'execution_adapter_configured': bool(self.execution_adapter_configured),
            'execution_adapter_label': self.execution_adapter_label,
            'chain_linked_ids': dict(self.chain or {}),
            # The architecture principle, stated by the backend on every gate.
            'ai_authority': rgc.AI_AUTHORITY,
            'ai_authority_mode': rgc.AI_AUTHORITY_MODE,
            'execution_authority': rgc.EXECUTION_AUTHORITY,
            'execution_authority_mode': rgc.EXECUTION_AUTHORITY_MODE,
            'ai_permitted': list(rgc.AI_PERMITTED),
            'ai_prohibited': list(rgc.AI_PROHIBITED),
            'gate_version': self.gate_version,
        }


def _quorum_facts(inputs: GateInputs) -> tuple[int, int, tuple[str, ...], tuple[str, ...], tuple[dict[str, Any], ...]]:
    """Resolve (required_quorum, approvals_collected, satisfied, missing, approvers).

    A role is satisfied only by an APPROVED decision that was recorded FOR that
    role. Because one approver may record at most one decision per action version
    (enforced by the approval store's unique index), a single person can never
    cover two required roles.
    """
    required_roles = tuple(
        r for r in (rgc.normalize_approver_role(role) for role in inputs.required_roles) if r
    )
    approved_ids: list[str] = []
    satisfied: list[str] = []
    approvers: list[dict[str, Any]] = []
    for record in inputs.approvals:
        role = rgc.normalize_approver_role(record.role)
        approvers.append({
            'role': role,
            'role_label': rgc.approver_role_label(role) if role else None,
            'approver_user_id': record.approver_user_id or None,
            'approver': record.approver_label,
            'decision': str(record.decision or '').strip().lower(),
            'decided_at': record.decided_at,
        })
        if not record.approved:
            continue
        approver_id = str(record.approver_user_id or '')
        if approver_id and approver_id not in approved_ids:
            approved_ids.append(approver_id)
        if role and role in required_roles and role not in satisfied:
            satisfied.append(role)
    missing = tuple(role for role in required_roles if role not in satisfied)

    # The binding quorum is whichever requirement is larger: the roles the policy
    # demands, or the action's own numeric quorum. Never smaller than either.
    #
    # The numeric quorum applies ONLY when the action's canonical lifecycle says a
    # human approval is required. An action whose profile requires none has no
    # numeric quorum to report, so the gate never publishes "0 / 1" beside a
    # "Requires Approval: No" — the exact contradiction this composition removes.
    # A policy that names approver roles still binds: len(required_roles) is
    # applied unconditionally, so a policy can RAISE the requirement and the
    # lifecycle flag can never lower it.
    numeric_quorum = max(0, int(inputs.required_quorum or 0)) if inputs.approval_required else 0
    required_quorum = max(numeric_quorum, len(required_roles))
    return required_quorum, len(approved_ids), tuple(satisfied), missing, tuple(approvers)


def _quorum_met(
    inputs: GateInputs, *, required_quorum: int, collected: int, missing_roles: tuple[str, ...],
) -> bool:
    """Whether the human approval requirement is satisfied.

    Precedence, highest first:
      1. Any required approver role still missing -> not met.
      2. A positive numeric quorum -> that many VALID approvals must exist.
      3. No quorum at all -> nothing is outstanding.

    There is no authority that can substitute for those approvals. A
    ``quorum_authority`` of ``delegated_governance`` NAMES an external governance
    module; it is not a signed attestation from one, carries no approver identity,
    and cannot be attributed in an audit — so it satisfies nothing here. (It
    previously short-circuited this function, which made 0-of-2 approvals report
    AUTHORIZED.) Until a verifiable delegation record exists, such an action
    collects the same human approvals as any other and fails closed meanwhile.
    """
    if missing_roles:
        return False
    if required_quorum <= 0:
        # No role is outstanding and no numeric quorum applies: there is nothing
        # left for a human to satisfy. Reported as approval_required=False, so the
        # UI states "no approval quorum" rather than an empty progress fraction.
        return True
    if inputs.approvals:
        return collected >= required_quorum
    status_value = str(inputs.lifecycle_approval_status or '').strip().lower()
    if status_value in {'not_required', 'approved'}:
        # No approval ROWS exist, so the canonical lifecycle status is the only
        # record of a decision. It is a human decision either way — the
        # pre-existing single-approver path writes it — never a delegated claim.
        return True
    return False


def evaluate_gate(inputs: GateInputs) -> ExecutionGate:
    """Decide whether ONE response action may execute.

    Reason codes accumulate in a fixed order so two identical inputs always
    produce an identical payload. Terminal facts (executed / cancelled /
    rejected / expired) are reported first because they are not recoverable by
    collecting another approval.
    """
    reason_codes: list[str] = []
    now = inputs.now or datetime.now(timezone.utc)

    required_quorum, collected, satisfied, missing_roles, approvers = _quorum_facts(inputs)

    # ── Terminal facts ────────────────────────────────────────────────────
    status_value = str(inputs.action_status or '').strip().lower()
    execution_status = str(inputs.execution_status or '').strip().lower()
    already_executed = execution_status == 'executed' or status_value == 'executed'
    cancelled = bool(inputs.cancelled) or status_value in {'canceled', 'cancelled'}
    if already_executed:
        reason_codes.append(rgc.ACTION_ALREADY_EXECUTED)
    if cancelled:
        reason_codes.append(rgc.ACTION_CANCELLED)
    if inputs.rejected or str(inputs.lifecycle_approval_status or '').strip().lower() == 'rejected':
        reason_codes.append(rgc.APPROVAL_REJECTED)

    expires_at = _utc(inputs.expires_at)
    expired = bool(expires_at and expires_at <= now)
    if expired:
        reason_codes.append(rgc.ACTION_EXPIRED)

    # ── Deterministic policy verdict (Screen 11, reflected verbatim) ───────
    policy_decision = str(inputs.policy_decision or rgc.POLICY_NOT_APPLICABLE).strip().upper()
    if policy_decision not in rgc.POLICY_DECISIONS:
        # An unreadable verdict is not an ALLOW.
        policy_decision = rgc.POLICY_NOT_EVALUATED
    # An ALLOW may only come from an evaluation reached FOR THIS ACTION. The
    # service already declines to adopt a borrowed one, and this is the same rule
    # applied independently at the point can_execute is decided — so an ALLOW
    # assembled by any other caller, or reached through a lookup that later grows
    # a new shared identifier, still cannot authorize an action it never examined.
    # Fail-closed: an unrecognized or absent provenance is treated as shared.
    if policy_decision == rgc.POLICY_ALLOW and not rgc.match_is_action_specific(
        inputs.policy_match_provenance
    ):
        policy_decision = rgc.POLICY_NOT_EVALUATED
        if rgc.POLICY_EVALUATION_NOT_ACTION_SPECIFIC not in reason_codes:
            reason_codes.append(rgc.POLICY_EVALUATION_NOT_ACTION_SPECIFIC)
    if policy_decision == rgc.POLICY_DENY:
        reason_codes.append(rgc.POLICY_DENIED)
        for code in inputs.policy_reason_codes:
            key = str(code or '').strip().upper()
            if key and key not in reason_codes:
                reason_codes.append(key)
    elif policy_decision == rgc.POLICY_NOT_EVALUATED:
        reason_codes.append(rgc.POLICY_EVALUATION_MISSING)
        # Why there is no evaluation, when the caller established one — a
        # sibling's verdict was available and was refused.
        for code in inputs.policy_reason_codes:
            key = str(code or '').strip().upper()
            if key and key not in reason_codes:
                reason_codes.append(key)
    version_mismatch = (
        policy_decision in {rgc.POLICY_ALLOW, rgc.POLICY_DENY}
        and inputs.policy_version is not None
        and inputs.policy_current_version is not None
        and int(inputs.policy_version) != int(inputs.policy_current_version)
    )
    if version_mismatch:
        reason_codes.append(rgc.POLICY_VERSION_MISMATCH)
    policy_satisfied = (
        policy_decision in {rgc.POLICY_ALLOW, rgc.POLICY_NOT_APPLICABLE} and not version_mismatch
    )

    # ── Human quorum ──────────────────────────────────────────────────────
    quorum_met = _quorum_met(
        inputs, required_quorum=required_quorum, collected=collected, missing_roles=missing_roles,
    )
    # An action that NAMES an external governance authority for a quorum it has
    # not collected is reported as exactly that. The code is additive: the quorum
    # is still incomplete below, and this only says WHY the claimed substitute did
    # not close it.
    delegated_claim_unverified = (
        str(inputs.quorum_authority or '') == QUORUM_AUTHORITY_DELEGATED
        and required_quorum > 0
        and not quorum_met
    )
    # An unmet quorum ALWAYS reports HUMAN_QUORUM_INCOMPLETE — it is the fact that
    # keeps the lock closed. REQUIRED_ROLE_MISSING is added on top when the gate can
    # also name WHICH role is outstanding; it refines the reason, it never replaces
    # it. (Previously the two were mutually exclusive, so a role-scoped quorum
    # reported only the specific code and the general one was unreachable — an
    # export or an alert filtering on HUMAN_QUORUM_INCOMPLETE silently missed it.)
    if not quorum_met:
        reason_codes.append(rgc.HUMAN_QUORUM_INCOMPLETE)
    if delegated_claim_unverified:
        reason_codes.append(rgc.DELEGATED_AUTHORITY_NOT_VERIFIED)
    if missing_roles:
        reason_codes.append(rgc.REQUIRED_ROLE_MISSING)

    # ── Context ───────────────────────────────────────────────────────────
    if not rgc.incident_state_allows_response(inputs.incident_status):
        reason_codes.append(rgc.INCIDENT_CLOSED)
        incident_allows = False
    else:
        incident_allows = True

    if not inputs.requester_authorized:
        reason_codes.append(rgc.RBAC_FORBIDDEN)
    if not inputs.execution_authority_available:
        reason_codes.append(rgc.EXECUTION_AUTHORITY_MISSING)
    facts_unavailable = bool(inputs.unreadable_facts)
    if facts_unavailable:
        reason_codes.append(rgc.GATE_FACTS_UNAVAILABLE)
    if not inputs.execution_adapter_configured:
        # Never fatal to AUTHORIZATION: §18 requires the missing adapter be stated
        # truthfully rather than presented as a submitted transaction. It IS fatal
        # to CAPABILITY when running this action would have to call a provider —
        # see execution_ready below.
        reason_codes.append(rgc.EXECUTION_ADAPTER_NOT_CONFIGURED)

    # ── Authorization: may this action run at all? ─────────────────────────
    # Policy, quorum, RBAC, lifecycle and incident state. Nothing about whether
    # this DEPLOYMENT happens to be able to carry the run out.
    authorized = bool(
        not facts_unavailable
        and policy_satisfied
        and quorum_met
        and inputs.requester_authorized
        and inputs.execution_authority_available
        and not expired
        and not cancelled
        and not already_executed
        and not inputs.rejected
        and incident_allows
    )

    # ── Capability: could the run actually be carried out here? ────────────
    # An action that contacts no external provider is always ready. One that
    # WOULD contact a provider is ready only when an adapter is configured — and
    # when it is not, `can_execute` is False so neither the UI nor the execute
    # command can attempt a run that would have nothing to run against.
    execution_ready = bool(
        (not inputs.execution_adapter_required) or inputs.execution_adapter_configured
    )
    can_execute = bool(authorized and execution_ready)

    if authorized:
        authorization_decision = rgc.GATE_AUTHORIZED
    elif policy_decision == rgc.POLICY_DENY:
        authorization_decision = rgc.GATE_DENIED
    else:
        authorization_decision = rgc.GATE_LOCKED

    if can_execute:
        decision = rgc.GATE_AUTHORIZED
        # An authorized gate reports the authorization plus any advisory code
        # (today only the adapter state), never a bare empty payload.
        reason_codes = [rgc.EXECUTION_AUTHORIZED] + reason_codes
    elif authorization_decision == rgc.GATE_DENIED:
        decision = rgc.GATE_DENIED
    else:
        # Includes the authorized-but-not-runnable case: the authorization is
        # preserved verbatim on `authorization_decision`, and the reason codes
        # already name EXECUTION_ADAPTER_NOT_CONFIGURED as what closed the gate.
        decision = rgc.GATE_LOCKED

    return ExecutionGate(
        decision=decision,
        can_execute=can_execute,
        authorization_decision=authorization_decision,
        execution_ready=execution_ready,
        execution_adapter_required=bool(inputs.execution_adapter_required),
        reason_codes=tuple(reason_codes),
        policy_decision=policy_decision,
        policy_match_provenance=str(inputs.policy_match_provenance or rgc.MATCH_NONE),
        required_quorum=required_quorum,
        approvals_collected=collected,
        # Authoritative: a policy role list makes approval required even when the
        # action's own profile did not, so the roster and the quorum always agree.
        approval_required=bool(required_quorum > 0),
        required_roles=tuple(
            r for r in (rgc.normalize_approver_role(role) for role in inputs.required_roles) if r
        ),
        satisfied_roles=satisfied,
        missing_roles=missing_roles,
        approvers=approvers,
        quorum_authority=str(inputs.quorum_authority or QUORUM_AUTHORITY_WORKSPACE),
        policy_id=inputs.policy_id,
        policy_key=inputs.policy_key,
        policy_version=inputs.policy_version,
        evaluation_id=inputs.evaluation_id,
        evaluated_at=inputs.evaluated_at,
        incident_id=inputs.incident_id,
        expires_at=inputs.expires_at,
        execution_adapter_configured=bool(inputs.execution_adapter_configured),
        execution_adapter_label=inputs.execution_adapter_label,
        chain=dict(inputs.chain or {}),
    )
