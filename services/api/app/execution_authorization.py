"""Canonical server-side authorization for PRODUCTION blockchain execution.

ONE definition of "may this tenant cause a production state change on chain",
callable from the API request path, the execution service, and a background
worker alike. Nothing that can sign, broadcast, propose, or otherwise reach a
write-capable provider may form its own opinion.

The invariant this module exists to hold
----------------------------------------
A **Pilot** workspace is observe / investigate / simulate / recommend / evidence
only. It may monitor contracts, read telemetry, detect threats, raise alerts and
incidents, run investigations, generate AI recommendations, simulate policies and
playbooks, approve or reject recommendations, export evidence and read audit
logs. It may NOT sign or broadcast a transaction, call a state-changing contract
method, pause/unpause, freeze/unfreeze, move funds, mint/burn, change ownership,
run automated remediation, invoke a production write-capable integration, or load
a transaction signer.

That holds when the frontend is bypassed, when the API is called directly, when a
worker picks up a job, when a queue message is inserted by hand, when an AI agent
recommends a run, when a stale client calls an old endpoint, and when the caller
is a Founder or Admin — because the decision is made HERE, from the tenant's own
canonical plan row, and nobody's role is an input to it.

Why this module and not ``entitlements``
----------------------------------------
``entitlements`` is pure: it maps an ``organizations`` row to what a plan allows.
This module is the ENFORCEMENT layer over it — it resolves the tenant from a
workspace id, fails closed on a read it could not complete, and raises one
structured refusal. It imports no FastAPI and no ``pilot``, so a worker can call
it without pulling in the request stack.

Defense in depth, as actually wired
-----------------------------------
    request / API   → pilot.execute_enforcement_action   (this module, FIRST)
                      pilot._enforce_execution_gate      (gate + this module)
                      pilot.require_governance_action_…  (the compliance
                                                          gateway routes, which
                                                          submit a freeze/pause
                                                          the gate never sees)
    service layer   → pilot.execute_enforcement_action   (this module)
    provider call   → pilot._propose_safe_transaction    (this module, via the
                      pilot._submit_freeze_wallet_…       authorization token)
    signer material → never loaded: the provider boundary refuses first

The last of those is the one that makes the invariant structural rather than
procedural: the two functions that contact a production provider REQUIRE an
``ExecutionAuthorization`` issued by this module for that exact workspace and
action, so a future caller — a worker, a queue consumer, a script, an agent —
cannot reach a provider by forgetting a check. There is no argument it can pass
that skips the tenant read.

Fail-closed rules
-----------------
  * An entitlement that could not be READ is not permission to execute.
  * A workspace with no organization is not permission to execute.
  * An unknown plan normalises to the strictest plan (``entitlements``).
  * A Founder/Admin role is not an input and cannot override the lock.
  * Only a deployment that has not yet run the tenancy migration is exempt —
    there is no organization plan to consult there, and every pre-existing
    workspace-level gate still applies.

Never logged here: private keys, seed phrases, raw signing secrets, bearer
tokens. ``blocked_audit_metadata`` returns machine facts only.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from services.api.app import entitlements as ent
from services.api.app import organizations as organization_service

logger = logging.getLogger(__name__)


# ── Machine-readable refusal code ────────────────────────────────────────────
#: The ONE code a caller matches on. Stable wire contract; never natural
#: language, and never varied by which reason closed the lock (the reason is a
#: separate field so the sentence can differ without the code moving).
CODE_PILOT_EXECUTION_DISABLED = 'PILOT_EXECUTION_DISABLED'

#: HTTP status for the refusal. 403: the caller is authenticated and its request
#: is well formed — the tenant is simply not permitted to do this, and no retry,
#: approval, or role change will alter that.
HTTP_FORBIDDEN = 403


# ── Why the lock is closed ───────────────────────────────────────────────────
# The same vocabulary ``pilot.plan_execution_lock`` has always published, kept
# here so the API, the worker path and the audit record cannot drift.
REASON_PLAN_RECOMMEND_ONLY = 'plan_recommend_only'
REASON_EVALUATION_EXPIRED = 'evaluation_expired'
REASON_ORGANIZATION_SUSPENDED = 'organization_suspended'
REASON_ORGANIZATION_NOT_LINKED = 'organization_not_linked'
REASON_ENTITLEMENT_UNAVAILABLE = 'entitlement_unavailable'
#: Not a lock: the deployment predates the tenancy migration, so there is no
#: organization plan to consult and this rule does not participate.
REASON_SCHEMA_NOT_MIGRATED = 'tenancy_schema_not_migrated'

LOCK_REASONS: tuple[str, ...] = (
    REASON_PLAN_RECOMMEND_ONLY,
    REASON_EVALUATION_EXPIRED,
    REASON_ORGANIZATION_SUSPENDED,
    REASON_ORGANIZATION_NOT_LINKED,
    REASON_ENTITLEMENT_UNAVAILABLE,
)

#: The suggested user-safe sentence, per reason. A recommend-only plan and an
#: ended evaluation both stop a live run, but they have different remedies, so
#: only one of them says "upgrade".
REASON_MESSAGES: dict[str, str] = {
    REASON_PLAN_RECOMMEND_ONLY: (
        'Production execution is disabled for Pilot workspaces. Pilot mode supports '
        'monitoring, investigation, simulation, recommendations, and evidence only.'
    ),
    REASON_EVALUATION_EXPIRED: (
        'Your Pilot evaluation has ended, so this action cannot be executed against '
        'production. Your existing actions and evidence remain available; upgrade to '
        'Scale to resume.'
    ),
    REASON_ORGANIZATION_SUSPENDED: (
        'This organization is suspended, so this action cannot be executed against '
        'production. Existing records remain available; contact Decoda to reactivate it.'
    ),
    REASON_ORGANIZATION_NOT_LINKED: (
        'Production execution is disabled because this workspace is not linked to an '
        'organization, so no plan entitlement could be established for it.'
    ),
    REASON_ENTITLEMENT_UNAVAILABLE: (
        'Production execution is disabled because the plan entitlement for this '
        'workspace could not be read. Execution stays closed until it can be.'
    ),
}

#: Fallback sentence for a reason this map does not carry. Never invents a
#: friendlier claim than the strictest one.
DEFAULT_MESSAGE = REASON_MESSAGES[REASON_PLAN_RECOMMEND_ONLY]


def reason_message(reason: Any) -> str:
    return REASON_MESSAGES.get(str(reason or '').strip(), DEFAULT_MESSAGE)


# ── Where the attempt came from ──────────────────────────────────────────────
# Recorded on the audit event so "someone clicked execute" and "a queue message
# reached a worker" are distinguishable after the fact.
SOURCE_API = 'api'
SOURCE_SERVICE = 'service'
SOURCE_PROVIDER = 'provider'
SOURCE_WORKER = 'worker'
SOURCE_QUEUE = 'queue'
SOURCE_AGENT = 'agent'
SOURCE_SCRIPT = 'script'
SOURCES: tuple[str, ...] = (
    SOURCE_API, SOURCE_SERVICE, SOURCE_PROVIDER, SOURCE_WORKER,
    SOURCE_QUEUE, SOURCE_AGENT, SOURCE_SCRIPT,
)

#: The audit / security event name for a refused attempt.
AUDIT_EVENT_BLOCKED = 'pilot_execution_blocked'


def normalize_source(value: Any) -> str:
    """Canonical source key. An unrecognised source is reported as itself rather
    than mapped onto a friendlier one — an audit record must not rename where an
    attempt actually came from."""
    key = str(value or '').strip().lower()
    return key or SOURCE_SERVICE


# ── The refusal ──────────────────────────────────────────────────────────────
class ExecutionForbidden(Exception):
    """Raised when a tenant may not cause a production state change.

    Framework-free on purpose: a worker, a queue consumer, or a script catches
    exactly this. The API layer converts it with :func:`as_http_exception`.
    """

    status_code = HTTP_FORBIDDEN
    code = CODE_PILOT_EXECUTION_DISABLED

    def __init__(
        self,
        *,
        reason: str,
        message: str | None = None,
        plan: str | None = None,
        lifecycle_state: str | None = None,
        workspace_id: str | None = None,
        action_id: str | None = None,
        action_type: str | None = None,
        source: str = SOURCE_SERVICE,
    ) -> None:
        self.reason = str(reason or REASON_PLAN_RECOMMEND_ONLY)
        self.message = message or reason_message(self.reason)
        self.plan = plan
        self.lifecycle_state = lifecycle_state
        self.workspace_id = workspace_id
        self.action_id = action_id
        self.action_type = action_type
        self.source = normalize_source(source)
        super().__init__(self.message)

    def as_dict(self) -> dict[str, Any]:
        """The wire body. ``code`` is the contract; everything else is context."""
        return {
            'code': self.code,
            'message': self.message,
            'reason': self.reason,
            'plan': self.plan,
            'lifecycle_state': self.lifecycle_state,
            'action_id': self.action_id,
            'action_type': self.action_type,
            'source': self.source,
        }


def as_http_exception(error: ExecutionForbidden) -> Exception:
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


# ── The grant ────────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class ExecutionAuthorization:
    """Proof that THIS workspace was authorized for THIS action, just now.

    Carried from the authorization point down to the provider call so the
    provider boundary can verify rather than trust.

    The workspace binding is absolute: a grant from another tenant can never be
    replayed into this one. The action binding holds whenever BOTH sides name an
    action — a grant minted for action A cannot stand in for action B — and is
    skipped when either side has no action id, which is the case for a caller
    that authorizes a workspace rather than a specific record. That is a
    deliberate looseness, not a gap: a grant only exists because the tenant read
    already succeeded, so the weakest thing it can authorize is still "a run this
    workspace is entitled to make".
    """

    workspace_id: str
    plan: str | None
    source: str
    action_id: str | None = None
    action_type: str | None = None
    granted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def matches(self, *, workspace_id: str, action_id: str | None = None) -> bool:
        if str(self.workspace_id) != str(workspace_id):
            return False
        if action_id is None or self.action_id is None:
            return True
        return str(self.action_id) == str(action_id)


# ── The decision ─────────────────────────────────────────────────────────────
def decide(organization: Mapping[str, Any] | None, *, schema_state: str) -> dict[str, Any]:
    """Whether this tenant may execute against production. PURE.

    Takes the tenancy schema state and an ``organizations`` row (or ``None``) and
    returns ``{'allowed', 'reason', 'plan', 'lifecycle_state'}``. Performs no
    database access and consults no caller identity, so the same answer is
    reached from the API, a worker, and a test.
    """
    if schema_state == organization_service.SCHEMA_UNKNOWN:
        # The probe itself failed. That is not evidence the schema is absent, and
        # an entitlement we could not read is not permission to execute.
        return {'allowed': False, 'reason': REASON_ENTITLEMENT_UNAVAILABLE,
                'plan': None, 'lifecycle_state': None}
    if schema_state == organization_service.SCHEMA_ABSENT:
        return {'allowed': True, 'reason': REASON_SCHEMA_NOT_MIGRATED,
                'plan': None, 'lifecycle_state': None}
    if organization is None:
        return {'allowed': False, 'reason': REASON_ORGANIZATION_NOT_LINKED,
                'plan': None, 'lifecycle_state': None}
    # EFFECTIVE entitlements: an expired evaluation or a suspended tenant has
    # execution withdrawn even where the plan row still lists it, and the reason
    # names which of the two closed the lock so an operator is not sent to
    # upgrade a plan that is not the obstacle.
    entitlements = ent.effective_entitlements(organization)
    allowed = ent.has_entitlement(entitlements, ent.FEATURE_AUTOMATIC_EXECUTION)
    lifecycle = ent.lifecycle_state(organization)
    if allowed:
        reason = None
    elif lifecycle == ent.LIFECYCLE_EXPIRED_PILOT:
        reason = REASON_EVALUATION_EXPIRED
    elif lifecycle == ent.LIFECYCLE_SUSPENDED:
        reason = REASON_ORGANIZATION_SUSPENDED
    else:
        reason = REASON_PLAN_RECOMMEND_ONLY
    return {
        'allowed': bool(allowed),
        'reason': reason,
        'plan': ent.normalize_plan(organization.get('plan')),
        'lifecycle_state': lifecycle,
    }


def resolve_execution_lock(
    connection: Any, workspace_id: str, *, cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Read the tenant and decide. ``{'locked', 'reason', 'plan'}``.

    The one database-backed entry point. Fails closed on any read error: a fact
    we could not look up is not an authorization. ``cache`` is a per-request dict
    so one handler resolving several actions reads the tenant once.
    """
    cache_key = f'execution_lock:{workspace_id}'
    if cache is not None and cache_key in cache:
        return cache[cache_key]
    try:
        schema_state = organization_service.tenancy_schema_state(connection)
        organization = (
            organization_service.organization_for_workspace(connection, workspace_id)
            if schema_state == organization_service.SCHEMA_READY else None
        )
        decision = decide(organization, schema_state=schema_state)
        result = {
            'locked': not decision['allowed'],
            'reason': decision['reason'],
            'plan': decision['plan'],
        }
    except Exception:
        logger.warning('execution_lock_read_failed workspace_id=%s', workspace_id, exc_info=True)
        result = {'locked': True, 'reason': REASON_ENTITLEMENT_UNAVAILABLE, 'plan': None}
    if cache is not None:
        cache[cache_key] = result
    return result


# ── What counts as production execution ──────────────────────────────────────
#: The action ``mode`` that submits against production. 'recommended' and
#: 'simulated' contact nothing, so they are never gated here — that is the whole
#: point of Pilot being a usable evaluation rather than a disabled product.
MODE_LIVE = 'live'


def is_production_execution(action: Mapping[str, Any] | None) -> bool:
    """Whether RUNNING this action would change production state.

    Read from the persisted action row's canonical ``mode``, never from a
    request body. A missing or unreadable mode is NOT treated as live: the
    provider boundary (:func:`assert_provider_call_allowed`) is unconditional, so
    nothing depends on this function to catch a mislabelled row.
    """
    return str((action or {}).get('mode') or '').strip().lower() == MODE_LIVE


# ── The guards ───────────────────────────────────────────────────────────────
def assert_execution_allowed(
    connection: Any,
    *,
    workspace_id: str,
    action: Mapping[str, Any] | None = None,
    source: str = SOURCE_SERVICE,
    cache: dict[str, Any] | None = None,
) -> ExecutionAuthorization:
    """Authorize a production execution for one workspace, or refuse.

    Raises :class:`ExecutionForbidden` when the tenant's plan does not include
    production execution. Returns the grant to carry to the provider call.

    No caller identity is consulted: a Founder or Admin gets the same answer as
    an analyst, because the restriction belongs to the TENANT, not to the seat.
    """
    lock = resolve_execution_lock(connection, workspace_id, cache=cache)
    action_map = dict(action or {})
    action_id = str(action_map.get('id') or '') or None
    action_type = str(action_map.get('action_type') or '') or None
    if lock['locked']:
        raise ExecutionForbidden(
            reason=str(lock['reason'] or REASON_PLAN_RECOMMEND_ONLY),
            plan=lock['plan'],
            workspace_id=str(workspace_id),
            action_id=action_id,
            action_type=action_type,
            source=source,
        )
    return ExecutionAuthorization(
        workspace_id=str(workspace_id),
        plan=lock['plan'],
        source=normalize_source(source),
        action_id=action_id,
        action_type=action_type,
    )


def assert_provider_call_allowed(
    authorization: ExecutionAuthorization | None,
    *,
    workspace_id: str,
    action_id: str | None = None,
    action_type: str | None = None,
    provider: str,
) -> ExecutionAuthorization:
    """The last gate, immediately before a write-capable provider is contacted.

    Unconditional by design: reaching this point MEANS production is about to be
    touched, so there is no ``mode`` to consult and no benefit of the doubt. A
    missing grant, a grant for a different tenant, or a grant for a different
    action is refused exactly like a Pilot plan is.

    This is what keeps a signer from ever being loaded on a Pilot workspace: the
    refusal happens before the provider payload — and therefore before the
    signing key — is assembled.
    """
    if authorization is None or not authorization.matches(
        workspace_id=str(workspace_id), action_id=action_id,
    ):
        logger.warning(
            'provider_call_blocked_unauthorized provider=%s workspace_id=%s action_id=%s',
            provider, workspace_id, action_id,
        )
        raise ExecutionForbidden(
            reason=REASON_ENTITLEMENT_UNAVAILABLE,
            message=(
                'Production execution was refused: this provider call carries no verified '
                'plan authorization for the workspace and action it names.'
            ),
            workspace_id=str(workspace_id),
            action_id=action_id,
            action_type=action_type,
            source=SOURCE_PROVIDER,
        )
    return authorization


# ── Audit ────────────────────────────────────────────────────────────────────
def blocked_audit_metadata(
    error: ExecutionForbidden,
    *,
    actor_id: str | None = None,
    actor_type: str = 'user',
    request_id: str | None = None,
    occurred_at: str | None = None,
) -> dict[str, Any]:
    """Safe metadata for a ``pilot_execution_blocked`` security event.

    Machine facts only — no signing material, no seed phrase, no bearer token,
    no request body. Every value here is either an identifier the product already
    stores or a key from this module's own closed vocabulary.
    """
    return {
        'event_type': AUDIT_EVENT_BLOCKED,
        'code': error.code,
        'workspace_id': error.workspace_id,
        'actor_type': actor_type,
        'actor_id': actor_id,
        'action_id': error.action_id,
        'action_type': error.action_type,
        'source': error.source,
        'reason': error.reason,
        'plan': error.plan,
        'lifecycle_state': error.lifecycle_state,
        'request_id': request_id,
        'occurred_at': occurred_at or datetime.now(timezone.utc).isoformat(),
    }
