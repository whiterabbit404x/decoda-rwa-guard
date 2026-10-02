"""Canonical server-side authorization for HUMAN access to a Pilot workspace.

ONE definition of "must this human have completed multi-factor authentication
before this session may read or write tenant data", callable from the API
request path and from a test without importing FastAPI.

The invariant this module exists to hold
----------------------------------------
Every HUMAN user of a **Pilot** workspace must have MFA enrolled AND must have
completed an MFA challenge on the CURRENT authentication session before any
Pilot business or data API answers them. Until both hold, only the endpoints
needed to complete MFA (and to sign out) are reachable.

That holds when the frontend is bypassed, when the API is called directly with
curl, when a deep link is opened, when the session was issued before MFA became
mandatory, when the caller accepted an invitation a second ago, and when the
caller is a Founder, an Owner, an Admin, an Analyst, a Viewer, or Decoda
internal staff — because the decision is made HERE, from the tenant's own
canonical plan row plus the session's own server-side MFA record, and no role is
an input to it.

Two controls, deliberately separate
-----------------------------------
  * **Login MFA** (this module): did THIS session complete a second factor at
    all? Satisfied once, for the life of the session, by a sign-in challenge, an
    enrollment confirmation, a recovery-code sign-in, an OIDC assertion carrying
    an MFA ``amr``, or a session step-up. It never forces a fresh code per
    request.
  * **Step-up MFA** (``pilot._require_session_mfa`` /
    ``_require_action_approval_session_mfa``): is the session's MFA recent AND
    carried by a factor this operator holds right now? Required additionally for
    response-action approval and execution. Untouched by this module.

Why a plan floor rather than only the workspace policy
------------------------------------------------------
``workspace_auth_policies.mfa_enforcement`` is a CUSTOMER-configurable control
that defaults to ``optional``. A Pilot tenant must not be able to configure its
way out of MFA, so this module computes an EFFECTIVE enforcement: the strongest
of what the customer configured and what the plan floor demands. For Pilot the
floor is ``all_members``; for Scale and Enterprise there is no floor and the
configured policy is preserved exactly as before.

Fail-closed rules
-----------------
  * A tenant plan that could not be READ is treated as Pilot (MFA required).
  * A workspace with no organization link is treated as Pilot — healing such a
    workspace creates a Pilot organization, so this is the same answer the next
    request would reach anyway.
  * A session whose MFA record could not be read has not completed MFA.
  * An unrecognised request path is NOT a bootstrap path.
  * Only a deployment that has not yet run the tenancy migration (0150) is
    exempt from the plan floor: there is no organization plan to consult there,
    and the configurable workspace policy still applies unchanged.

A refused request is recorded as a structured log line plus the
``decoda_pilot_mfa_blocked_total`` metric, not as an ``audit_logs`` row: a
refusal costs the caller nothing to repeat, so writing a hash-chained audit row
per attempt would let an unauthenticated loop flood the audit trail. The MFA
LIFECYCLE events (enrollment, challenge success/failure, recovery use, disable)
are audited, because those are bounded and rate limited.

Never logged here: TOTP secrets, recovery codes, OTP values, bearer tokens or
session hashes. ``PilotMfaRequired.blocked_audit_metadata`` returns machine facts
only.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping

from services.api.app import entitlements as ent
from services.api.app import organizations as organization_service

logger = logging.getLogger(__name__)


# ── Machine-readable refusal codes ───────────────────────────────────────────
#: The account holds no second factor at all. The remedy is enrollment.
CODE_MFA_ENROLLMENT_REQUIRED = 'MFA_ENROLLMENT_REQUIRED'
#: The account is enrolled but THIS session never completed a challenge. The
#: remedy is a challenge, not a second enrollment. Reuses the code the
#: response-action step-up gate has always published, so a client already
#: handling one handles both.
CODE_MFA_CHALLENGE_REQUIRED = 'MFA_CHALLENGE_REQUIRED'

#: 403 rather than 401: the caller IS authenticated and its request is well
#: formed. Re-sending the same bearer token will never help; completing MFA will.
#: A 401 would make every client drop the session and bounce to sign-in, which is
#: exactly the wrong remedy for "enrolled, challenge pending".
HTTP_FORBIDDEN = 403

MESSAGE_ENROLLMENT_REQUIRED = (
    'Multi-factor authentication is required for Pilot access. '
    'Set up an authenticator before accessing this workspace.'
)
MESSAGE_CHALLENGE_REQUIRED = (
    'Multi-factor authentication is required for Pilot access. '
    'Verify your authenticator to continue in this session.'
)


# ── Enforcement vocabulary ───────────────────────────────────────────────────
# The exact values migration 0092 constrains workspace_auth_policies to.
ENFORCEMENT_OPTIONAL = 'optional'
ENFORCEMENT_ADMINISTRATORS = 'administrators'
ENFORCEMENT_ALL_MEMBERS = 'all_members'

#: Ordered weakest → strongest. Combining two policies takes the stronger one,
#: so a plan floor can only ever tighten what the customer configured.
ENFORCEMENT_ORDER: tuple[str, ...] = (
    ENFORCEMENT_OPTIONAL,
    ENFORCEMENT_ADMINISTRATORS,
    ENFORCEMENT_ALL_MEMBERS,
)

#: Roles the ``administrators`` policy covers. Everyone is covered by
#: ``all_members``, which is what the Pilot floor sets.
ADMINISTRATIVE_ROLES: frozenset[str] = frozenset({'owner', 'admin'})


# ── Why a floor applies ──────────────────────────────────────────────────────
REASON_PLAN_PILOT = 'plan_pilot'
REASON_ORGANIZATION_NOT_LINKED = 'organization_not_linked'
REASON_TENANT_UNREADABLE = 'tenant_unreadable'
#: Not a floor: the deployment predates the tenancy migration, so there is no
#: organization plan to consult and this rule does not participate.
REASON_SCHEMA_NOT_MIGRATED = 'tenancy_schema_not_migrated'
#: No floor because the tenant is genuinely on a plan that keeps MFA
#: configurable (Scale / Enterprise).
REASON_PLAN_CONFIGURABLE = 'plan_configurable'

#: Plans whose MFA requirement is NOT negotiable. Pilot evaluations run against
#: live customer assets with Decoda-provisioned infrastructure, so the weakest
#: credential on the tenant is the one that matters.
MANDATORY_MFA_PLANS: frozenset[str] = frozenset({ent.PLAN_PILOT})


def normalize_enforcement(value: Any) -> str:
    """Canonical enforcement key. An unrecognised value normalises to the
    STRONGEST setting, never the weakest: a policy row we cannot interpret is
    not permission to skip MFA."""
    key = str(value or '').strip().lower()
    if key in ENFORCEMENT_ORDER:
        return key
    if not key:
        return ENFORCEMENT_OPTIONAL
    logger.warning('mfa_enforcement_unrecognised value=%s treated=all_members', key)
    return ENFORCEMENT_ALL_MEMBERS


def strongest(*enforcements: Any) -> str:
    """The strictest of several enforcement settings."""
    best = ENFORCEMENT_OPTIONAL
    for candidate in enforcements:
        normalized = normalize_enforcement(candidate)
        if ENFORCEMENT_ORDER.index(normalized) > ENFORCEMENT_ORDER.index(best):
            best = normalized
    return best


def role_is_covered(enforcement: Any, role: Any) -> bool:
    """Whether THIS role must satisfy MFA under THIS enforcement setting.

    ``all_members`` covers every role — Founder, Owner, Admin, Analyst and
    Viewer alike — which is why the Pilot floor sets exactly that. A role that
    cannot be read is treated as covered.
    """
    normalized = normalize_enforcement(enforcement)
    if normalized == ENFORCEMENT_ALL_MEMBERS:
        return True
    if normalized == ENFORCEMENT_ADMINISTRATORS:
        key = str(role or '').strip().lower()
        return (not key) or key in ADMINISTRATIVE_ROLES
    return False


# ── The plan floor decision (PURE) ───────────────────────────────────────────
def decide_plan_floor(
    organization: Mapping[str, Any] | None, *, schema_state: str,
) -> dict[str, Any]:
    """The MFA enforcement floor this TENANT imposes. PURE.

    Takes the tenancy schema state and an ``organizations`` row (or ``None``) and
    returns ``{'enforcement', 'reason', 'plan'}``. Performs no database access
    and consults no caller identity, so the same answer is reached from the API,
    a test, and a future worker.
    """
    if schema_state == organization_service.SCHEMA_UNKNOWN:
        # The probe itself failed. That is not evidence the schema is absent, and
        # a plan we could not read is not permission to skip MFA.
        return {
            'enforcement': ENFORCEMENT_ALL_MEMBERS,
            'reason': REASON_TENANT_UNREADABLE,
            'plan': None,
        }
    if schema_state == organization_service.SCHEMA_ABSENT:
        # Nothing to consult. The configurable workspace policy still applies.
        return {
            'enforcement': ENFORCEMENT_OPTIONAL,
            'reason': REASON_SCHEMA_NOT_MIGRATED,
            'plan': None,
        }
    if organization is None:
        # An unlinked workspace HEALS into a Pilot organization on the tenant's
        # next context resolution, so answering anything weaker here would only
        # be true until that heal ran.
        return {
            'enforcement': ENFORCEMENT_ALL_MEMBERS,
            'reason': REASON_ORGANIZATION_NOT_LINKED,
            'plan': None,
        }
    plan = ent.normalize_plan(organization.get('plan'))
    if plan in MANDATORY_MFA_PLANS:
        return {
            'enforcement': ENFORCEMENT_ALL_MEMBERS,
            'reason': REASON_PLAN_PILOT,
            'plan': plan,
        }
    return {
        'enforcement': ENFORCEMENT_OPTIONAL,
        'reason': REASON_PLAN_CONFIGURABLE,
        'plan': plan,
    }


# ── The plan floor read ──────────────────────────────────────────────────────
def _schema_state(connection: Any, *, cache: dict[str, Any] | None = None) -> str:
    """Tenancy schema state, resolved at most once per request.

    The probe is a catalog query and the answer cannot change inside one
    request, so a caller checking several workspaces pays for it once.
    """
    if cache is None:
        return organization_service.tenancy_schema_state(connection)
    if 'mfa_schema_state' not in cache:
        cache['mfa_schema_state'] = organization_service.tenancy_schema_state(connection)
    return str(cache['mfa_schema_state'])


def resolve_plan_floor(
    connection: Any, workspace_id: str, *, cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read the tenant and decide its MFA floor. ``{'enforcement','reason','plan'}``.

    The one database-backed entry point. Fails closed on any read error: a fact
    we could not look up is not permission to skip MFA. ``cache`` is a
    per-request dict so one request resolving several workspaces reads each
    tenant once.
    """
    workspace_key = str(workspace_id or '')
    if not workspace_key:
        # No workspace means no tenant data to protect on this request. The
        # caller still cannot READ anything: every data route resolves a
        # workspace of its own and refuses without one.
        return {
            'enforcement': ENFORCEMENT_OPTIONAL,
            'reason': REASON_PLAN_CONFIGURABLE,
            'plan': None,
        }
    cache_key = f'mfa_plan_floor:{workspace_key}'
    if cache is not None and cache_key in cache:
        return cache[cache_key]
    try:
        schema_state = _schema_state(connection, cache=cache)
        organization = (
            organization_service.organization_for_workspace(connection, workspace_key)
            if schema_state == organization_service.SCHEMA_READY
            else None
        )
        result = decide_plan_floor(organization, schema_state=schema_state)
    except Exception:
        logger.warning('mfa_plan_floor_read_failed workspace_id=%s', workspace_key, exc_info=True)
        result = {
            'enforcement': ENFORCEMENT_ALL_MEMBERS,
            'reason': REASON_TENANT_UNREADABLE,
            'plan': None,
        }
    if cache is not None:
        cache[cache_key] = result
    return result


# ── Session MFA state ────────────────────────────────────────────────────────
#: Authentication methods that count as a completed SECOND factor when an
#: identity provider (rather than Decoda) performed the challenge. 'totp' and
#: 'recovery_code' are Decoda's own; 'idp_mfa' is a shared Decoda sign-in whose
#: WorkOS environment the operator attests enforces MFA (decoda_identity); the
#: rest are OIDC ``amr`` values.
FEDERATED_MFA_METHODS: frozenset[str] = frozenset({'mfa', 'otp', 'hwk', 'swk', 'webauthn', 'idp_mfa'})
LOCAL_MFA_METHODS: frozenset[str] = frozenset({'totp', 'recovery_code'})


def session_completed_mfa(session: Mapping[str, Any] | None) -> bool:
    """Whether THIS authentication session ever completed a second factor.

    The canonical login-MFA fact, read from ``auth_sessions.mfa_verified_at`` —
    the column stamped by, and only by, sign-in MFA completion, enrollment
    confirmation, a session step-up, and an OIDC assertion whose ``amr`` proved
    MFA. A password-only session (including one created before the account
    enrolled) never carries it.

    Fails CLOSED: a session that could not be read has not completed MFA.
    """
    if not session:
        return False
    if session.get('mfa_verified_at') is None:
        return False
    methods = session.get('authentication_methods')
    if not isinstance(methods, list):
        # The stamp is present but the method list is unreadable. The stamp is
        # only ever written alongside a real factor, so trust it rather than
        # locking out a correctly authenticated operator over a serialization
        # detail.
        return True
    normalized = {str(method).strip().lower() for method in methods}
    if normalized & (LOCAL_MFA_METHODS | FEDERATED_MFA_METHODS):
        return True
    # Stamped, but by no method this module recognises. An enrollment
    # confirmation writes ["password","totp"], so this is an unexpected shape;
    # honour the stamp (it is never written without a factor) and record it.
    logger.info('mfa_session_methods_unrecognised methods=%s', sorted(normalized))
    return True


# ── The refusal ──────────────────────────────────────────────────────────────
class PilotMfaRequired(Exception):
    """Raised when a human must satisfy MFA before this workspace answers.

    Framework-free on purpose, so a non-HTTP caller catches exactly this. The
    API layer converts it with :func:`as_http_exception`.
    """

    status_code = HTTP_FORBIDDEN

    def __init__(
        self,
        *,
        code: str,
        reason: str,
        workspace_id: str | None = None,
        plan: str | None = None,
        enforcement: str | None = None,
        purpose: str | None = None,
        message: str | None = None,
    ) -> None:
        self.code = str(code or CODE_MFA_ENROLLMENT_REQUIRED)
        self.reason = str(reason or REASON_PLAN_PILOT)
        self.workspace_id = workspace_id
        self.plan = plan
        self.enforcement = enforcement
        self.purpose = purpose
        self.message = message or (
            MESSAGE_CHALLENGE_REQUIRED
            if self.code == CODE_MFA_CHALLENGE_REQUIRED
            else MESSAGE_ENROLLMENT_REQUIRED
        )
        super().__init__(self.message)

    @property
    def enrollment_required(self) -> bool:
        return self.code == CODE_MFA_ENROLLMENT_REQUIRED

    def as_dict(self) -> dict[str, Any]:
        """The wire body. ``code`` is the contract; everything else is context.

        Carries no secret, no session hash and no token — only the facts a client
        needs to route the operator to the right remedy.
        """
        return {
            'code': self.code,
            'message': self.message,
            'reason': self.reason,
            'plan': self.plan,
            'enforcement': self.enforcement,
            'mfa_enrollment_required': self.enrollment_required,
            'mfa_challenge_required': self.code == CODE_MFA_CHALLENGE_REQUIRED,
        }

    def blocked_audit_metadata(self) -> dict[str, Any]:
        """Machine facts for the audit record. Never a secret or a credential."""
        return {
            'code': self.code,
            'reason': self.reason,
            'plan': self.plan,
            'enforcement': self.enforcement,
            'purpose': self.purpose,
            'workspace_id': self.workspace_id,
        }


def as_http_exception(error: PilotMfaRequired) -> Exception:
    """The 403 form of a refusal, for a FastAPI handler.

    Falls back to the framework-free exception when FastAPI is not importable
    (the offline test runner, a worker image), so a caller never loses the
    refusal because it could not be dressed up.
    """
    try:  # fastapi is stubbed in the offline test runner
        from fastapi import HTTPException
    except Exception:  # pragma: no cover - only without fastapi installed
        return error
    return HTTPException(status_code=HTTP_FORBIDDEN, detail=error.as_dict())


# ── The decision (PURE) ──────────────────────────────────────────────────────
def decide_access(
    *,
    enforcement: Any,
    role: Any,
    mfa_enrolled: bool,
    session_mfa_completed: bool,
) -> dict[str, Any]:
    """Whether this human may proceed. PURE — no database, no request, no HTTP.

    Returns ``{'allowed', 'code', 'reason'}``. The two refusals are kept
    distinct because their remedies differ: ``MFA_ENROLLMENT_REQUIRED`` means
    "register an authenticator", ``MFA_CHALLENGE_REQUIRED`` means "you already
    have one — use it on this session".
    """
    normalized = normalize_enforcement(enforcement)
    if not role_is_covered(normalized, role):
        return {'allowed': True, 'code': None, 'reason': None}
    if not mfa_enrolled:
        return {
            'allowed': False,
            'code': CODE_MFA_ENROLLMENT_REQUIRED,
            'reason': 'not_enrolled',
        }
    if not session_mfa_completed:
        return {
            'allowed': False,
            'code': CODE_MFA_CHALLENGE_REQUIRED,
            'reason': 'session_not_verified',
        }
    return {'allowed': True, 'code': None, 'reason': None}
