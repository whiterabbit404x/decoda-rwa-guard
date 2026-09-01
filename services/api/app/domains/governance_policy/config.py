"""Canonical vocabulary + environment configuration for Governance & Policy.

Single source of truth shared by the engine, the service, the endpoints and the
tests. Follows the repository convention (``_env_*`` helpers with fail-closed
defaults, mirroring domains/operational_integrity/config.py).

Nothing here is customer-facing prose: every constant is a machine key that the
frontend maps to a label, so a decision can never drift between screens.
"""

from __future__ import annotations

import os
from typing import Any

# Stamped onto every decision this lane produces, so an auditor can reproduce a
# verdict under the exact rules that made it.
ENGINE_VERSION = 'governance-policy-engine-v1'

# --------------------------------------------------------------------------
# Operations a policy can govern.
# --------------------------------------------------------------------------
OPERATION_MINT = 'MINT'
OPERATION_BURN = 'BURN'
OPERATION_TRANSFER = 'TRANSFER'
OPERATIONS = (OPERATION_MINT, OPERATION_BURN, OPERATION_TRANSFER)

OPERATION_LABELS: dict[str, str] = {
    OPERATION_MINT: 'Mint',
    OPERATION_BURN: 'Burn',
    OPERATION_TRANSFER: 'Transfer',
}


def normalize_operation(value: Any) -> str | None:
    key = str(value or '').strip().upper()
    return key if key in OPERATIONS else None


# --------------------------------------------------------------------------
# Policy lifecycle status. Only ACTIVE can ever produce an ALLOW.
# --------------------------------------------------------------------------
STATUS_DRAFT = 'DRAFT'
STATUS_ACTIVE = 'ACTIVE'
STATUS_DISABLED = 'DISABLED'
STATUS_ARCHIVED = 'ARCHIVED'
STATUSES = (STATUS_DRAFT, STATUS_ACTIVE, STATUS_DISABLED, STATUS_ARCHIVED)

STATUS_LABELS: dict[str, str] = {
    STATUS_DRAFT: 'Draft',
    STATUS_ACTIVE: 'Active',
    STATUS_DISABLED: 'Disabled',
    STATUS_ARCHIVED: 'Archived',
}


def normalize_status(value: Any) -> str | None:
    key = str(value or '').strip().upper()
    return key if key in STATUSES else None


# --------------------------------------------------------------------------
# Settlement state of the business event backing the operation.
#
# UNKNOWN is deliberately a first-class value and NOT a synonym for MISSING: a
# settlement state the platform could not read is not the same fact as one the
# authoritative source reported absent. Both fail closed, with distinct codes.
# --------------------------------------------------------------------------
SETTLEMENT_CLEARED = 'CLEARED'
SETTLEMENT_PENDING = 'PENDING'
SETTLEMENT_FAILED = 'FAILED'
SETTLEMENT_MISSING = 'MISSING'
SETTLEMENT_STATES = (SETTLEMENT_CLEARED, SETTLEMENT_PENDING, SETTLEMENT_FAILED, SETTLEMENT_MISSING)

SETTLEMENT_LABELS: dict[str, str] = {
    SETTLEMENT_CLEARED: 'Cleared',
    SETTLEMENT_PENDING: 'Pending',
    SETTLEMENT_FAILED: 'Failed',
    SETTLEMENT_MISSING: 'Missing',
}


def normalize_settlement(value: Any) -> str | None:
    key = str(value or '').strip().upper()
    return key if key in SETTLEMENT_STATES else None


# What a policy may DEMAND of the settlement state.
REQUIREMENT_CLEARED = 'CLEARED'
REQUIREMENT_CLEARED_OR_PENDING = 'CLEARED_OR_PENDING'
SETTLEMENT_REQUIREMENTS = (REQUIREMENT_CLEARED, REQUIREMENT_CLEARED_OR_PENDING)

# Which observed settlement states satisfy each requirement. Anything not listed
# fails — including MISSING and any value the platform could not normalize.
SETTLEMENT_SATISFIES: dict[str, frozenset[str]] = {
    REQUIREMENT_CLEARED: frozenset({SETTLEMENT_CLEARED}),
    REQUIREMENT_CLEARED_OR_PENDING: frozenset({SETTLEMENT_CLEARED, SETTLEMENT_PENDING}),
}

SETTLEMENT_REQUIREMENT_LABELS: dict[str, str] = {
    REQUIREMENT_CLEARED: 'CLEARED',
    REQUIREMENT_CLEARED_OR_PENDING: 'CLEARED or PENDING',
}


def normalize_settlement_requirement(value: Any) -> str | None:
    key = str(value or '').strip().upper()
    return key if key in SETTLEMENT_REQUIREMENTS else None


# --------------------------------------------------------------------------
# Business events. The authoritative off-chain record that justifies the
# operation (the Screen 3/5 vocabulary: a mint is justified by a SUBSCRIPTION,
# a burn by a REDEMPTION).
# --------------------------------------------------------------------------
BUSINESS_EVENT_SUBSCRIPTION = 'SUBSCRIPTION'
BUSINESS_EVENT_REDEMPTION = 'REDEMPTION'
BUSINESS_EVENT_TRANSFER_INSTRUCTION = 'TRANSFER_INSTRUCTION'
BUSINESS_EVENTS = (
    BUSINESS_EVENT_SUBSCRIPTION,
    BUSINESS_EVENT_REDEMPTION,
    BUSINESS_EVENT_TRANSFER_INSTRUCTION,
)

BUSINESS_EVENT_LABELS: dict[str, str] = {
    BUSINESS_EVENT_SUBSCRIPTION: 'Subscription',
    BUSINESS_EVENT_REDEMPTION: 'Redemption',
    BUSINESS_EVENT_TRANSFER_INSTRUCTION: 'Transfer Instruction',
}


def normalize_business_event(value: Any) -> str | None:
    key = str(value or '').strip().upper()
    return key if key in BUSINESS_EVENTS else None


# --------------------------------------------------------------------------
# Governance roles a policy can require, and the CANONICAL workspace permission
# that evidences each one.
#
# This is the reuse point for RBAC (§11): a governance role is not a new
# permission system, it is a named requirement satisfied by an existing
# workspace permission. The operator's role is resolved SERVER-SIDE from
# workspace_members; the client never asserts it.
# --------------------------------------------------------------------------
ROLE_SECURITY_LEAD = 'SECURITY_LEAD'
ROLE_TREASURY_OPERATOR = 'TREASURY_OPERATOR'
ROLE_COMPLIANCE_APPROVER = 'COMPLIANCE_APPROVER'
GOVERNANCE_ROLES = (ROLE_SECURITY_LEAD, ROLE_TREASURY_OPERATOR, ROLE_COMPLIANCE_APPROVER)

GOVERNANCE_ROLE_LABELS: dict[str, str] = {
    ROLE_SECURITY_LEAD: 'Security Lead',
    ROLE_TREASURY_OPERATOR: 'Treasury Operator',
    ROLE_COMPLIANCE_APPROVER: 'Compliance Approver',
}

#: The workspace permission that evidences each governance role.
ROLE_PERMISSIONS: dict[str, str] = {
    ROLE_SECURITY_LEAD: 'security.manage',
    ROLE_TREASURY_OPERATOR: 'response.propose',
    ROLE_COMPLIANCE_APPROVER: 'response.approve',
}

#: How each role is satisfied at evaluation time.
#:   'operator'  — the submitting operator must hold the mapped permission.
#:   'approval'  — a separate approval artifact must be present, recorded BY a
#:                 human FOR that role (Screen 8 records one per approver per
#:                 action version). The policy engine has no evidence source for
#:                 such a role on its own, so it leaves it outstanding — which is
#:                 why a policy that names it can only be satisfied by a real
#:                 recorded sign-off, never by the submitter's own permissions.
ROLE_SATISFACTION: dict[str, str] = {
    ROLE_SECURITY_LEAD: 'approval',
    ROLE_TREASURY_OPERATOR: 'operator',
    ROLE_COMPLIANCE_APPROVER: 'approval',
}


def normalize_role(value: Any) -> str | None:
    key = str(value or '').strip().upper()
    return key if key in GOVERNANCE_ROLES else None


# --------------------------------------------------------------------------
# Deterministic reason codes. Stable machine keys — never natural language.
#
# Every DENY carries at least one. ALLOW carries POLICY_SATISFIED so a decision
# is never an empty payload the UI has to interpret.
# --------------------------------------------------------------------------
POLICY_SATISFIED = 'POLICY_SATISFIED'

POLICY_NOT_FOUND = 'POLICY_NOT_FOUND'
POLICY_DISABLED = 'POLICY_DISABLED'
POLICY_NOT_ACTIVE = 'POLICY_NOT_ACTIVE'
OPERATION_MISMATCH = 'OPERATION_MISMATCH'

BUSINESS_EVENT_MISSING = 'BUSINESS_EVENT_MISSING'
BUSINESS_EVENT_MISMATCH = 'BUSINESS_EVENT_MISMATCH'
SETTLEMENT_NOT_CLEARED = 'SETTLEMENT_NOT_CLEARED'
SETTLEMENT_STATE_UNKNOWN = 'SETTLEMENT_STATE_UNKNOWN'
OUTSIDE_ALLOWED_WINDOW = 'OUTSIDE_ALLOWED_WINDOW'
EVALUATION_TIMESTAMP_MISSING = 'EVALUATION_TIMESTAMP_MISSING'
AMOUNT_LIMIT_EXCEEDED = 'AMOUNT_LIMIT_EXCEEDED'
AMOUNT_INVALID = 'AMOUNT_INVALID'
DAILY_TOTAL_UNAVAILABLE = 'DAILY_TOTAL_UNAVAILABLE'
TREASURY_OPERATOR_MISSING = 'TREASURY_OPERATOR_MISSING'
COMPLIANCE_APPROVAL_MISSING = 'COMPLIANCE_APPROVAL_MISSING'
SECURITY_LEAD_APPROVAL_MISSING = 'SECURITY_LEAD_APPROVAL_MISSING'
REQUIRED_ROLE_MISSING = 'REQUIRED_ROLE_MISSING'

REASON_CODES = (
    POLICY_SATISFIED,
    POLICY_NOT_FOUND,
    POLICY_DISABLED,
    POLICY_NOT_ACTIVE,
    OPERATION_MISMATCH,
    BUSINESS_EVENT_MISSING,
    BUSINESS_EVENT_MISMATCH,
    SETTLEMENT_NOT_CLEARED,
    SETTLEMENT_STATE_UNKNOWN,
    OUTSIDE_ALLOWED_WINDOW,
    EVALUATION_TIMESTAMP_MISSING,
    AMOUNT_LIMIT_EXCEEDED,
    AMOUNT_INVALID,
    DAILY_TOTAL_UNAVAILABLE,
    TREASURY_OPERATOR_MISSING,
    COMPLIANCE_APPROVAL_MISSING,
    SECURITY_LEAD_APPROVAL_MISSING,
    REQUIRED_ROLE_MISSING,
)

#: Reason code emitted when a required role is not evidenced. Keeps the two
#: roles the product names first-class while staying extensible.
ROLE_MISSING_REASON: dict[str, str] = {
    ROLE_SECURITY_LEAD: SECURITY_LEAD_APPROVAL_MISSING,
    ROLE_TREASURY_OPERATOR: TREASURY_OPERATOR_MISSING,
    ROLE_COMPLIANCE_APPROVER: COMPLIANCE_APPROVAL_MISSING,
}


def role_missing_reason(role: str) -> str:
    return ROLE_MISSING_REASON.get(str(role or '').strip().upper(), REQUIRED_ROLE_MISSING)


# --------------------------------------------------------------------------
# The deterministic checks, in evaluation order. Named so the UI can render the
# same sequence the engine ran.
# --------------------------------------------------------------------------
CHECK_POLICY_EXISTS = 'policy_exists'
CHECK_POLICY_ACTIVE = 'policy_active'
CHECK_OPERATION_MATCHES = 'operation_matches'
CHECK_BUSINESS_EVENT = 'business_event'
CHECK_SETTLEMENT = 'settlement'
CHECK_ALLOWED_WINDOW = 'allowed_window'
CHECK_DAILY_LIMIT = 'daily_limit'
CHECK_OPERATOR_ROLE = 'operator_role'
CHECK_COMPLIANCE_APPROVAL = 'compliance_approval'

CHECK_ORDER = (
    CHECK_POLICY_EXISTS,
    CHECK_POLICY_ACTIVE,
    CHECK_OPERATION_MATCHES,
    CHECK_BUSINESS_EVENT,
    CHECK_SETTLEMENT,
    CHECK_ALLOWED_WINDOW,
    CHECK_DAILY_LIMIT,
    CHECK_OPERATOR_ROLE,
    CHECK_COMPLIANCE_APPROVAL,
)

CHECK_LABELS: dict[str, str] = {
    CHECK_POLICY_EXISTS: 'Policy exists',
    CHECK_POLICY_ACTIVE: 'Policy is active',
    CHECK_OPERATION_MATCHES: 'Operation matches policy',
    CHECK_BUSINESS_EVENT: 'Required business event',
    CHECK_SETTLEMENT: 'Settlement requirement',
    CHECK_ALLOWED_WINDOW: 'Allowed window (UTC)',
    CHECK_DAILY_LIMIT: 'Daily issuance limit',
    CHECK_OPERATOR_ROLE: 'Required operator role',
    CHECK_COMPLIANCE_APPROVAL: 'Compliance approval',
}

# --------------------------------------------------------------------------
# Decisions.
# --------------------------------------------------------------------------
DECISION_ALLOW = 'ALLOW'
DECISION_DENY = 'DENY'
DECISIONS = (DECISION_ALLOW, DECISION_DENY)

# The only violation outcome the engine implements. Stored on the policy so the
# rendered "On violation" row states the policy's own value rather than a UI
# assumption.
VIOLATION_ACTION_DENY = 'DENY'
VIOLATION_ACTIONS = (VIOLATION_ACTION_DENY,)

# Policy origin — 'demo_seed' rows are labelled in the UI so a seeded policy is
# never presented as customer configuration.
ORIGIN_CUSTOMER = 'customer'
ORIGIN_DEMO_SEED = 'demo_seed'


# --------------------------------------------------------------------------
# Starter templates — the values the Create Policy FORM opens with.
#
# A template is NOT a policy. Nothing here is stored, evaluated, or rendered as
# policy state; it only pre-fills an editable form, and a row appears only when
# an authorized operator submits it. That is why a created policy carries
# origin='customer': a person authored it, template or not.
#
# They live here rather than in the frontend for the same reason the simulator
# vocabulary does — so the UI can never offer a starting point the engine cannot
# evaluate, and so the numbers a customer sees came from the backend.
#
# Only MINT ships with constraints. BURN and TRANSFER open unconstrained, which
# is honest: an unconfigured constraint reads as "not constrained" in Policy
# Details and the author narrows it. A permissive default that LOOKED
# constrained would be the dangerous option.
# --------------------------------------------------------------------------
POLICY_TEMPLATES: dict[str, dict[str, Any]] = {
    OPERATION_MINT: {
        'policy_key': 'POL-MINT-007',
        'name': 'RWA Mint Policy',
        'operation': OPERATION_MINT,
        'status': STATUS_ACTIVE,
        'required_business_event': BUSINESS_EVENT_SUBSCRIPTION,
        'settlement_requirement': REQUIREMENT_CLEARED,
        'allowed_window_utc': {'start': '08:00', 'end': '18:00'},
        'maximum_daily_amount_usd': '10000000.00',
        'required_roles': [ROLE_TREASURY_OPERATOR, ROLE_COMPLIANCE_APPROVER],
        'violation_action': VIOLATION_ACTION_DENY,
    },
    OPERATION_BURN: {
        'policy_key': 'POL-BURN-001',
        'name': 'RWA Burn Policy',
        'operation': OPERATION_BURN,
        'status': STATUS_DRAFT,
        'required_business_event': BUSINESS_EVENT_REDEMPTION,
        'settlement_requirement': None,
        'allowed_window_utc': None,
        'maximum_daily_amount_usd': None,
        'required_roles': [ROLE_TREASURY_OPERATOR],
        'violation_action': VIOLATION_ACTION_DENY,
    },
    OPERATION_TRANSFER: {
        'policy_key': 'POL-XFER-001',
        'name': 'RWA Transfer Policy',
        'operation': OPERATION_TRANSFER,
        'status': STATUS_DRAFT,
        'required_business_event': None,
        'settlement_requirement': None,
        'allowed_window_utc': None,
        'maximum_daily_amount_usd': None,
        'required_roles': [ROLE_TREASURY_OPERATOR],
        'violation_action': VIOLATION_ACTION_DENY,
    },
}


def policy_templates_payload() -> dict[str, dict[str, Any]]:
    """A defensive copy, keyed by operation, for the vocabulary block."""
    return {
        operation: {
            key: (dict(value) if isinstance(value, dict) else list(value) if isinstance(value, list) else value)
            for key, value in template.items()
        }
        for operation, template in POLICY_TEMPLATES.items()
    }


# --------------------------------------------------------------------------
# Environment configuration.
# --------------------------------------------------------------------------
def _env_int(name: str, default: int) -> int:
    try:
        return int(str(os.getenv(name, str(default))).strip())
    except (ValueError, TypeError):
        return default


def _env_flag(name: str, default: bool = False) -> bool:
    return str(os.getenv(name, 'true' if default else 'false')).strip().lower() in {'1', 'true', 'yes', 'on'}


def engine_config() -> dict[str, Any]:
    """Resolve Governance & Policy configuration from the environment."""
    return {
        # Persist a record of every simulation run. On by default: a simulation
        # is a governance action an auditor should be able to see, and the record
        # is stamped simulation=TRUE so it can never be mistaken for enforcement.
        'record_simulations': _env_flag('GOVERNANCE_POLICY_RECORD_SIMULATIONS', default=True),
        # How many policy versions the history view returns.
        'history_limit': max(1, _env_int('GOVERNANCE_POLICY_HISTORY_LIMIT', 50)),
        # How many policies a workspace may hold. A guard rail, not a business
        # limit; exceeding it is a 409, never a silent truncation.
        'max_policies_per_workspace': max(1, _env_int('GOVERNANCE_POLICY_MAX_PER_WORKSPACE', 200)),
        'engine_version': ENGINE_VERSION,
    }


# --------------------------------------------------------------------------
# Permissions (§11). Reuses the canonical workspace permission model; no new
# permission is introduced.
#
#   read / simulate — every authenticated workspace member (viewer and analyst
#                     included) may view a policy and run a READ-ONLY simulation.
#                     Enforced by workspace resolution alone, so tenant isolation
#                     still applies to both.
#   edit            — 'security.manage' (owner/admin by default), the SAME
#                     permission the existing Screen 11 governance writes use.
#                     Enforced on the BACKEND: a disabled button is a courtesy,
#                     not the control.
# --------------------------------------------------------------------------
POLICY_EDIT_PERMISSION = 'security.manage'
