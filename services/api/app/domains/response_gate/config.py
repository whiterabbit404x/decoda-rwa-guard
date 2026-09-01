"""Canonical vocabulary for the Screen 8 deterministic execution gate.

Single source of truth shared by the engine, the service, the DTO builders and
the tests. Follows the repository convention (machine keys only; the frontend
maps a key to a label, so a decision can never drift between screens).

The two authority constants are re-exported from Screen 11's schemas rather than
redeclared, so Screen 8 and Screen 11 can never state a different authority.
"""

from __future__ import annotations

import os
from typing import Any

from services.api.app.domains.governance_policy import config as gpc
from services.api.app.domains.governance_policy.schemas import (
    AI_AUTHORITY as _POLICY_AI_AUTHORITY,
    DECISION_AUTHORITY as _POLICY_DECISION_AUTHORITY,
)

# Stamped onto every gate this lane produces, so an auditor can reproduce a
# verdict under the exact rules that made it.
GATE_VERSION = 'response-execution-gate-v1'

# --------------------------------------------------------------------------
# The architecture principle, as two constants.
#
# These are the SAME strings Screen 11 publishes (governance_policy.schemas), so
# the trust boundary reads identically wherever it is rendered. AI may recommend;
# deterministic code decides.
# --------------------------------------------------------------------------
AI_AUTHORITY_MODE = 'RECOMMEND_ONLY'
EXECUTION_AUTHORITY_MODE = 'DETERMINISTIC_POLICY_ENGINE'

AI_AUTHORITY = _POLICY_AI_AUTHORITY                 # 'Recommend only'
EXECUTION_AUTHORITY = _POLICY_DECISION_AUTHORITY    # 'Deterministic Policy Engine'

#: What an AI layer may and may not do on Screen 8. Rendered verbatim by the AI
#: Playbook Execution Agent panel, and asserted by the tests, so the boundary is
#: stated by the backend rather than by UI copy.
AI_PERMITTED = (
    'recommend_playbook',
    'explain_recommendation',
    'summarize_evidence',
    'explain_policy_result',
    'suggest_next_step',
)
AI_PROHIBITED = (
    'change_policy_result',
    'satisfy_quorum',
    'create_approval',
    'unlock_execution',
    'execute_action',
    'mutate_authoritative_state',
)

# --------------------------------------------------------------------------
# Gate decisions. A gate is AUTHORIZED only when every deterministic condition
# is satisfied; DENIED when policy itself said DENY; LOCKED for anything else.
# --------------------------------------------------------------------------
GATE_AUTHORIZED = 'AUTHORIZED'
GATE_LOCKED = 'LOCKED'
GATE_DENIED = 'DENIED'
GATE_DECISIONS = (GATE_AUTHORIZED, GATE_LOCKED, GATE_DENIED)

GATE_DECISION_LABELS: dict[str, str] = {
    GATE_AUTHORIZED: 'Execution Authorized',
    GATE_LOCKED: 'Execution Locked',
    GATE_DENIED: 'Execution Denied by Policy',
}

# --------------------------------------------------------------------------
# Policy decision as Screen 8 reports it.
#
# ALLOW / DENY are Screen 11's own verdicts, reflected verbatim — Screen 8 never
# recalculates one. The two remaining values are HONEST states, never rendered as
# ALLOW: NOT_APPLICABLE means no policy governs this action; NOT_EVALUATED means
# a policy applies but no enforcement decision was recorded for it.
# --------------------------------------------------------------------------
POLICY_ALLOW = gpc.DECISION_ALLOW
POLICY_DENY = gpc.DECISION_DENY
POLICY_NOT_APPLICABLE = 'NOT_APPLICABLE'
POLICY_NOT_EVALUATED = 'NOT_EVALUATED'
POLICY_DECISIONS = (POLICY_ALLOW, POLICY_DENY, POLICY_NOT_APPLICABLE, POLICY_NOT_EVALUATED)

POLICY_DECISION_LABELS: dict[str, str] = {
    POLICY_ALLOW: 'Allow',
    POLICY_DENY: 'Deny',
    POLICY_NOT_APPLICABLE: 'No policy applies',
    POLICY_NOT_EVALUATED: 'Not evaluated',
}

# --------------------------------------------------------------------------
# Deterministic reason codes. Stable machine keys — never natural language.
#
# Every LOCKED / DENIED gate carries at least one. An AUTHORIZED gate carries
# EXECUTION_AUTHORIZED so a gate is never an empty payload the UI must interpret.
# --------------------------------------------------------------------------
EXECUTION_AUTHORIZED = 'EXECUTION_AUTHORIZED'

POLICY_DENIED = 'POLICY_DENIED'
POLICY_VERSION_MISMATCH = 'POLICY_VERSION_MISMATCH'
POLICY_EVALUATION_MISSING = 'POLICY_EVALUATION_MISSING'
HUMAN_QUORUM_INCOMPLETE = 'HUMAN_QUORUM_INCOMPLETE'
REQUIRED_ROLE_MISSING = 'REQUIRED_ROLE_MISSING'
APPROVAL_REJECTED = 'APPROVAL_REJECTED'
ACTION_EXPIRED = 'ACTION_EXPIRED'
ACTION_ALREADY_EXECUTED = 'ACTION_ALREADY_EXECUTED'
ACTION_CANCELLED = 'ACTION_CANCELLED'
INCIDENT_CLOSED = 'INCIDENT_CLOSED'
EXECUTION_AUTHORITY_MISSING = 'EXECUTION_AUTHORITY_MISSING'
RBAC_FORBIDDEN = 'RBAC_FORBIDDEN'
EXECUTION_ADAPTER_NOT_CONFIGURED = 'EXECUTION_ADAPTER_NOT_CONFIGURED'
#: A canonical fact the gate needs could not be READ (a failed query, an outage).
#: Deliberately distinct from a fact that is absent: "we could not look" is not
#: the same answer as "there is none", and neither one is an authorization.
GATE_FACTS_UNAVAILABLE = 'GATE_FACTS_UNAVAILABLE'

REASON_CODES = (
    EXECUTION_AUTHORIZED,
    POLICY_DENIED,
    POLICY_VERSION_MISMATCH,
    POLICY_EVALUATION_MISSING,
    HUMAN_QUORUM_INCOMPLETE,
    REQUIRED_ROLE_MISSING,
    APPROVAL_REJECTED,
    ACTION_EXPIRED,
    ACTION_ALREADY_EXECUTED,
    ACTION_CANCELLED,
    INCIDENT_CLOSED,
    EXECUTION_AUTHORITY_MISSING,
    RBAC_FORBIDDEN,
    EXECUTION_ADAPTER_NOT_CONFIGURED,
    GATE_FACTS_UNAVAILABLE,
)

#: Operator-facing sentence for each code. The KEY is authoritative; this map is
#: presentation only, and lives here so Screen 8 and an export render the same
#: sentence for the same code.
REASON_LABELS: dict[str, str] = {
    EXECUTION_AUTHORIZED: 'Deterministic policy checks passed and the human quorum is satisfied.',
    POLICY_DENIED: 'The deterministic policy engine returned DENY for this operation.',
    POLICY_VERSION_MISMATCH: 'The governing policy changed after this evaluation; re-evaluate before executing.',
    POLICY_EVALUATION_MISSING: 'A policy governs this action but no enforcement evaluation was recorded.',
    HUMAN_QUORUM_INCOMPLETE: 'The required human approval quorum has not been collected.',
    REQUIRED_ROLE_MISSING: 'A required approver role has not signed off.',
    APPROVAL_REJECTED: 'An approver rejected this action.',
    ACTION_EXPIRED: 'This action passed its authorization window and must be re-proposed.',
    ACTION_ALREADY_EXECUTED: 'This action has already been executed.',
    ACTION_CANCELLED: 'This action was cancelled or rolled back.',
    INCIDENT_CLOSED: 'The linked incident is closed, so no response may be executed against it.',
    EXECUTION_AUTHORITY_MISSING: 'No deterministic execution authority is available for this action.',
    RBAC_FORBIDDEN: 'Your workspace role does not permit executing response actions.',
    EXECUTION_ADAPTER_NOT_CONFIGURED: 'No execution adapter is configured; this action is dry-run only.',
    GATE_FACTS_UNAVAILABLE: 'A required authorization fact could not be read, so the gate stays closed.',
}


def reason_label(code: str) -> str:
    """Operator-facing sentence for a reason code. Never invents one for an
    unknown key — an unrecognised code is reported as itself."""
    key = str(code or '').strip().upper()
    return REASON_LABELS.get(key, key)


# --------------------------------------------------------------------------
# Approver roles.
#
# Screen 8 does NOT invent a role vocabulary. A required approver role is a
# Screen 11 GOVERNANCE role (config.GOVERNANCE_ROLES), satisfied by an approver
# who holds the workspace permission that evidences it. The resolution is
# server-side from workspace_members; the client never asserts a role.
# --------------------------------------------------------------------------
APPROVER_ROLES = gpc.GOVERNANCE_ROLES
APPROVER_ROLE_LABELS = gpc.GOVERNANCE_ROLE_LABELS
APPROVER_ROLE_PERMISSIONS = gpc.ROLE_PERMISSIONS


def normalize_approver_role(value: Any) -> str | None:
    """Canonical approver-role key, or None when the value is not one."""
    return gpc.normalize_role(value)


def approver_role_label(role: Any) -> str:
    key = str(role or '').strip().upper()
    return APPROVER_ROLE_LABELS.get(key, key.replace('_', ' ').title())


# --------------------------------------------------------------------------
# Incident states in which a response may still be executed.
#
# A closed incident is a terminal fact: executing a containment action against
# it would act on a finished investigation. 'resolved' and 'suppressed' are the
# canonical closed states in pilot.update_incident_status.
# --------------------------------------------------------------------------
INCIDENT_CLOSED_STATES = frozenset({'resolved', 'suppressed', 'closed'})


def incident_state_allows_response(status_value: Any) -> bool:
    """True when an incident in this state may still receive a response.

    An UNKNOWN/absent state is permissive on purpose: many response actions are
    alert-scoped and carry no incident at all, and the gate must not invent an
    incident that does not exist. A state that IS read and IS closed blocks.
    """
    key = str(status_value or '').strip().lower()
    if not key:
        return True
    return key not in INCIDENT_CLOSED_STATES


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on'}


def live_execution_configured() -> bool:
    """Whether a real execution adapter is configured for this deployment.

    Read from the SAME flag the existing executor honours, so Screen 8 can never
    report an adapter the execution path does not have.
    """
    return _env_flag('LIVE_ACTION_EXECUTION_ENABLED', default=False)
