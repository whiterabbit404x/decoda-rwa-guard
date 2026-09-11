"""Centralized plan entitlements for organization-scoped tenants.

ONE definition of what a plan may do, so no screen, endpoint, or worker has to
carry its own ``if plan == 'pilot'`` branch. Callers ask two questions:

    get_entitlements(organization)          what this tenant is allowed to do
    enforce_resource_creation(...)          may this tenant create one more?

Everything here is PURE: it reads a plain mapping (an ``organizations`` row) and
returns plain data. It performs no database access and imports nothing from
``pilot``, so it is safe to import from the API, the workers, and the tests
alike. Database access lives in ``services.api.app.organizations``.

Truthfulness rules this module keeps
------------------------------------
  * ``days_remaining`` exists ONLY for a plan that actually has an evaluation
    window. A Scale or Enterprise tenant reports ``evaluation = None`` rather
    than a misleading countdown.
  * An UNKNOWN plan is treated as the most restrictive plan, never the most
    permissive: a row whose plan cannot be recognised fails closed onto Pilot.
  * A limit of ``None`` means unlimited and is reported as ``None`` — never as a
    large number the UI would render as a real cap.
  * Overrides may only widen or narrow KNOWN keys with the right type. An
    override is deployment/internal-admin state read from the database; it is
    never accepted from a browser (see ``organizations.py``).

Public pricing this table must not contradict (apps/web/app/pricing-plans.ts):
    Pilot        1 workspace   ·  5 monitored contracts  ·  10 evidence packages
    Scale        3 workspaces  ·  25 monitored contracts ·  unlimited evidence
    Enterprise   custom
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

try:  # fastapi is stubbed in the offline test runner
    from fastapi import HTTPException, status
except Exception:  # pragma: no cover - exercised only without fastapi installed
    HTTPException = None  # type: ignore[assignment]
    status = None  # type: ignore[assignment]


# ── Plans ────────────────────────────────────────────────────────────────────
PLAN_PILOT = 'pilot'
PLAN_SCALE = 'scale'
PLAN_ENTERPRISE = 'enterprise'
PLANS: tuple[str, ...] = (PLAN_PILOT, PLAN_SCALE, PLAN_ENTERPRISE)

PLAN_LABELS: dict[str, str] = {
    PLAN_PILOT: 'Pilot',
    PLAN_SCALE: 'Scale',
    PLAN_ENTERPRISE: 'Enterprise',
}

#: The plan an unrecognised value falls back to. Fail closed, never open.
DEFAULT_PLAN = PLAN_PILOT

#: Plans that run a time-boxed evaluation. Only these ever report a countdown.
EVALUATION_PLANS: frozenset[str] = frozenset({PLAN_PILOT})


# ── Organization status ──────────────────────────────────────────────────────
STATUS_ACTIVE = 'active'
STATUS_SUSPENDED = 'suspended'
STATUS_EXPIRED = 'expired'
STATUSES: tuple[str, ...] = (STATUS_ACTIVE, STATUS_SUSPENDED, STATUS_EXPIRED)


# ── Lifecycle states (Phase 5) ───────────────────────────────────────────────
LIFECYCLE_ACTIVE_PILOT = 'ACTIVE_PILOT'
LIFECYCLE_EXPIRED_PILOT = 'EXPIRED_PILOT'
LIFECYCLE_SUSPENDED = 'SUSPENDED'
LIFECYCLE_ACTIVE_SCALE = 'ACTIVE_SCALE'
LIFECYCLE_ENTERPRISE = 'ENTERPRISE'

LIFECYCLE_LABELS: dict[str, str] = {
    LIFECYCLE_ACTIVE_PILOT: 'Pilot evaluation active',
    LIFECYCLE_EXPIRED_PILOT: 'Pilot evaluation expired',
    LIFECYCLE_SUSPENDED: 'Organization suspended',
    LIFECYCLE_ACTIVE_SCALE: 'Scale subscription active',
    LIFECYCLE_ENTERPRISE: 'Enterprise agreement active',
}


# ── Limit keys ───────────────────────────────────────────────────────────────
LIMIT_WORKSPACES = 'max_workspaces'
LIMIT_MONITORED_CONTRACTS = 'max_monitored_contracts'
LIMIT_MONITORING_TARGETS = 'max_monitoring_targets'
LIMIT_EVIDENCE_PACKAGES = 'max_evidence_packages'

LIMIT_KEYS: tuple[str, ...] = (
    LIMIT_WORKSPACES,
    LIMIT_MONITORED_CONTRACTS,
    LIMIT_MONITORING_TARGETS,
    LIMIT_EVIDENCE_PACKAGES,
)

#: Resource name reported in a PLAN_LIMIT_REACHED body, per limit key. The
#: resource name is the machine key the frontend maps to a sentence.
LIMIT_RESOURCES: dict[str, str] = {
    LIMIT_WORKSPACES: 'workspaces',
    LIMIT_MONITORED_CONTRACTS: 'monitored_contracts',
    LIMIT_MONITORING_TARGETS: 'monitoring_targets',
    LIMIT_EVIDENCE_PACKAGES: 'evidence_packages',
}


# ── Feature keys ─────────────────────────────────────────────────────────────
FEATURE_THREAT_MONITORING = 'threat_monitoring'
FEATURE_AI_INVESTIGATION = 'ai_investigation'
FEATURE_RESPONSE_RECOMMENDATIONS = 'response_recommendations'
#: Autonomous / production execution of a response action. Pilot is RECOMMEND
#: ONLY: the deterministic execution gate refuses a live run without it.
FEATURE_AUTOMATIC_EXECUTION = 'automatic_execution'
FEATURE_EVIDENCE_EXPORT = 'evidence_export'
FEATURE_INCIDENT_PLAYBOOKS = 'incident_playbooks'
FEATURE_CUSTOM_INTEGRATIONS = 'custom_integrations'
FEATURE_CUSTOM_EVIDENCE_TEMPLATES = 'custom_evidence_templates'
FEATURE_MULTI_NETWORK = 'multi_network'
FEATURE_PRIORITY_ROUTING = 'priority_routing'

FEATURE_KEYS: tuple[str, ...] = (
    FEATURE_THREAT_MONITORING,
    FEATURE_AI_INVESTIGATION,
    FEATURE_RESPONSE_RECOMMENDATIONS,
    FEATURE_AUTOMATIC_EXECUTION,
    FEATURE_EVIDENCE_EXPORT,
    FEATURE_INCIDENT_PLAYBOOKS,
    FEATURE_CUSTOM_INTEGRATIONS,
    FEATURE_CUSTOM_EVIDENCE_TEMPLATES,
    FEATURE_MULTI_NETWORK,
    FEATURE_PRIORITY_ROUTING,
)

#: Capabilities an EXPIRED evaluation or a SUSPENDED tenant may no longer
#: INITIATE. This is the list of things that start new billable or compute work
#: — a monitoring cycle, an AI investigation, a playbook run, a fresh evidence
#: package, a production execution. It is deliberately NOT a list of things the
#: customer can see: every asset, alert, incident, investigation and evidence
#: package they already produced stays readable, which is what makes "your
#: evaluation data remains available" a true statement rather than a slogan.
LIFECYCLE_RESTRICTED_FEATURES: frozenset[str] = frozenset({
    FEATURE_THREAT_MONITORING,
    FEATURE_AI_INVESTIGATION,
    FEATURE_RESPONSE_RECOMMENDATIONS,
    FEATURE_INCIDENT_PLAYBOOKS,
    FEATURE_EVIDENCE_EXPORT,
    FEATURE_AUTOMATIC_EXECUTION,
})

#: Feature keys the table carries but NO code path currently reads. They are
#: recorded here rather than quietly advertised: a key in this set describes a
#: commercial intention, not an enforced control, so nothing may present it to a
#: customer as a capability their plan withholds or grants. Removing a key from
#: this set is what "we shipped the gate" looks like.
UNENFORCED_FEATURES: frozenset[str] = frozenset({
    FEATURE_CUSTOM_INTEGRATIONS,
    FEATURE_CUSTOM_EVIDENCE_TEMPLATES,
    FEATURE_MULTI_NETWORK,
    FEATURE_PRIORITY_ROUTING,
})


# ── Plan table ───────────────────────────────────────────────────────────────
# `None` means unlimited. Keep aligned with apps/web/app/pricing-plans.ts.
#
# max_monitoring_targets is a DERIVED operational bound, not a published price
# term: a monitored contract is registered as an asset, and each asset may carry
# several monitoring targets (contract, wallet, oracle). Bounding targets is what
# actually bounds RPC/QuickNode consumption (Phase 10), so the ceiling is set at
# two targets per contract on the metered plans and left unlimited on Enterprise.
_PLAN_TABLE: dict[str, dict[str, Any]] = {
    PLAN_PILOT: {
        LIMIT_WORKSPACES: 1,
        LIMIT_MONITORED_CONTRACTS: 5,
        LIMIT_MONITORING_TARGETS: 10,
        LIMIT_EVIDENCE_PACKAGES: 10,
        FEATURE_THREAT_MONITORING: True,
        FEATURE_AI_INVESTIGATION: True,
        FEATURE_RESPONSE_RECOMMENDATIONS: True,
        FEATURE_AUTOMATIC_EXECUTION: False,
        FEATURE_EVIDENCE_EXPORT: True,
        # An evaluation that cannot open a playbook cannot evaluate incident
        # response. Playbooks are ON for an ACTIVE Pilot and are withdrawn when
        # the window closes — see LIFECYCLE_RESTRICTED_FEATURES.
        FEATURE_INCIDENT_PLAYBOOKS: True,
        FEATURE_CUSTOM_INTEGRATIONS: False,
        FEATURE_CUSTOM_EVIDENCE_TEMPLATES: False,
        FEATURE_MULTI_NETWORK: False,
        FEATURE_PRIORITY_ROUTING: False,
    },
    PLAN_SCALE: {
        LIMIT_WORKSPACES: 3,
        LIMIT_MONITORED_CONTRACTS: 25,
        LIMIT_MONITORING_TARGETS: 50,
        LIMIT_EVIDENCE_PACKAGES: None,
        FEATURE_THREAT_MONITORING: True,
        FEATURE_AI_INVESTIGATION: True,
        FEATURE_RESPONSE_RECOMMENDATIONS: True,
        # Deliberately OFF unless an Enterprise agreement turns it on: the
        # product's stated principle is AI recommends, a deterministic policy
        # engine decides, and a human authorizes.
        FEATURE_AUTOMATIC_EXECUTION: False,
        FEATURE_EVIDENCE_EXPORT: True,
        FEATURE_INCIDENT_PLAYBOOKS: True,
        FEATURE_CUSTOM_INTEGRATIONS: False,
        FEATURE_CUSTOM_EVIDENCE_TEMPLATES: False,
        FEATURE_MULTI_NETWORK: False,
        FEATURE_PRIORITY_ROUTING: True,
    },
    PLAN_ENTERPRISE: {
        LIMIT_WORKSPACES: None,
        LIMIT_MONITORED_CONTRACTS: None,
        LIMIT_MONITORING_TARGETS: None,
        LIMIT_EVIDENCE_PACKAGES: None,
        FEATURE_THREAT_MONITORING: True,
        FEATURE_AI_INVESTIGATION: True,
        FEATURE_RESPONSE_RECOMMENDATIONS: True,
        # Still OFF by default. An Enterprise tenant that has signed off on
        # autonomous execution gets it through an explicit, audited
        # entitlement_overrides entry — never merely by being Enterprise.
        FEATURE_AUTOMATIC_EXECUTION: False,
        FEATURE_EVIDENCE_EXPORT: True,
        FEATURE_INCIDENT_PLAYBOOKS: True,
        FEATURE_CUSTOM_INTEGRATIONS: True,
        FEATURE_CUSTOM_EVIDENCE_TEMPLATES: True,
        FEATURE_MULTI_NETWORK: True,
        FEATURE_PRIORITY_ROUTING: True,
    },
}


# ── Evaluation duration (Phase 5) ────────────────────────────────────────────
#: The ONE place the default evaluation length is written down. Nothing else in
#: the codebase may hard-code "30".
DEFAULT_EVALUATION_DAYS = 30
EVALUATION_DAYS_ENV = 'PILOT_EVALUATION_DAYS'
MIN_EVALUATION_DAYS = 1
MAX_EVALUATION_DAYS = 365
#: Upper bound on a single "extend pilot" action from the founder admin.
MAX_EVALUATION_EXTENSION_DAYS = 365


def evaluation_days() -> int:
    """Configured evaluation length in days, clamped to a sane range."""
    raw = (os.getenv(EVALUATION_DAYS_ENV) or '').strip()
    if not raw:
        return DEFAULT_EVALUATION_DAYS
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_EVALUATION_DAYS
    return max(MIN_EVALUATION_DAYS, min(MAX_EVALUATION_DAYS, parsed))


def evaluation_window(started_at: datetime | None = None) -> tuple[datetime, datetime]:
    """(started_at, expires_at) for a new evaluation, using the configured length."""
    start = started_at or datetime.now(timezone.utc)
    return start, start + timedelta(days=evaluation_days())


# ── Error codes ──────────────────────────────────────────────────────────────
CODE_PLAN_LIMIT_REACHED = 'PLAN_LIMIT_REACHED'
CODE_PLAN_ENTITLEMENT_REQUIRED = 'PLAN_ENTITLEMENT_REQUIRED'
CODE_EVALUATION_EXPIRED = 'PLAN_EVALUATION_EXPIRED'
CODE_ORGANIZATION_SUSPENDED = 'ORGANIZATION_SUSPENDED'
CODE_ORGANIZATION_CONTEXT_MISSING = 'ORGANIZATION_CONTEXT_MISSING'


# ── Normalisation ────────────────────────────────────────────────────────────
def normalize_plan(value: Any) -> str:
    """Canonical plan key. An unrecognised plan falls back to the strictest one."""
    key = str(value or '').strip().lower()
    return key if key in PLANS else DEFAULT_PLAN


def normalize_status(value: Any) -> str:
    """Canonical status. An unrecognised status is treated as suspended.

    Fail closed: a row whose status cannot be read must not be granted the
    permissions of an active tenant.
    """
    key = str(value or '').strip().lower()
    if not key:
        return STATUS_ACTIVE
    return key if key in STATUSES else STATUS_SUSPENDED


def plan_label(plan: Any) -> str:
    return PLAN_LABELS.get(normalize_plan(plan), PLAN_LABELS[DEFAULT_PLAN])


def _as_datetime(value: Any) -> datetime | None:
    """Best-effort UTC datetime from a row value. Never raises."""
    if value is None or value == '':
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


#: Sentinel for "this override value cannot be used". Needed because ``None``
#: is itself a legitimate limit (unlimited) and cannot double as an error.
_INVALID = object()


def _coerce_limit(value: Any) -> int | None | object:
    """Coerce an override value into a limit, or return ``_INVALID``.

    ``None`` is a legitimate limit (unlimited), so an unusable value cannot be
    signalled with ``None`` — it is signalled with the ``_INVALID`` sentinel and
    the override is discarded.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return _INVALID
    if isinstance(value, int):
        return value if value >= 0 else _INVALID
    if isinstance(value, str) and value.strip().lstrip('-').isdigit():
        parsed = int(value.strip())
        return parsed if parsed >= 0 else _INVALID
    return _INVALID


def normalize_overrides(raw: Any) -> dict[str, Any]:
    """Keep only known keys with usable types. Unknown keys are dropped.

    An override is internal state (a migration backfill or an audited founder
    admin write), so this function's job is type safety, not authorization.
    """
    if not isinstance(raw, Mapping):
        return {}
    cleaned: dict[str, Any] = {}
    for key, value in raw.items():
        name = str(key)
        if name in LIMIT_KEYS:
            coerced = _coerce_limit(value)
            if coerced is not _INVALID:
                cleaned[name] = coerced
        elif name in FEATURE_KEYS:
            if isinstance(value, bool):
                cleaned[name] = value
    return cleaned


# ── Entitlements ─────────────────────────────────────────────────────────────
def plan_entitlements(plan: Any) -> dict[str, Any]:
    """The unmodified table entry for a plan (a copy; callers may mutate it)."""
    return dict(_PLAN_TABLE[normalize_plan(plan)])


def get_entitlements(organization: Mapping[str, Any] | None) -> dict[str, Any]:
    """Effective entitlements for one organization.

    Shape is flat — ``{'max_monitored_contracts': 5, 'ai_investigation': True, …}``
    — so a caller reads one key rather than walking a nested tree. A missing
    organization yields the strictest plan's entitlements, so an unresolved
    tenant can never be handed Scale or Enterprise capability by accident.
    """
    org = organization or {}
    entitlements = plan_entitlements(org.get('plan'))
    entitlements.update(normalize_overrides(org.get('entitlement_overrides')))
    # Derived convenience flag used by the usage UI. Recomputed AFTER overrides
    # so an override that lifts the cap is reflected here too.
    entitlements['evidence_unlimited'] = entitlements.get(LIMIT_EVIDENCE_PACKAGES) is None
    return entitlements


def limit_for(entitlements: Mapping[str, Any], limit_key: str) -> int | None:
    """The numeric limit for a key, or ``None`` for unlimited."""
    if limit_key not in LIMIT_KEYS:
        raise ValueError(f'Unknown entitlement limit key: {limit_key!r}')
    value = entitlements.get(limit_key)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):  # pragma: no cover - normalize_overrides guards
        return None


def has_entitlement(entitlements: Mapping[str, Any], feature_key: str) -> bool:
    """Whether a feature is enabled. Unknown or absent features are OFF."""
    if feature_key not in FEATURE_KEYS:
        raise ValueError(f'Unknown entitlement feature key: {feature_key!r}')
    return bool(entitlements.get(feature_key))


# ── Lifecycle (Phase 5) ──────────────────────────────────────────────────────
def evaluation_expired(organization: Mapping[str, Any] | None, *, now: datetime | None = None) -> bool:
    """Whether this organization's evaluation window has passed.

    Only an evaluation plan can expire, and only when an expiry is actually
    recorded. An organization with no ``evaluation_expires_at`` is not expired —
    a missing date is not evidence of expiry, and inventing one would revoke
    access the tenant was never told about.
    """
    org = organization or {}
    if normalize_plan(org.get('plan')) not in EVALUATION_PLANS:
        return False
    if normalize_status(org.get('status')) == STATUS_EXPIRED:
        return True
    expires_at = _as_datetime(org.get('evaluation_expires_at'))
    if expires_at is None:
        return False
    return (now or datetime.now(timezone.utc)) >= expires_at


def days_remaining(organization: Mapping[str, Any] | None, *, now: datetime | None = None) -> int | None:
    """Whole days left in the evaluation, floored at 0. ``None`` when N/A.

    Returns ``None`` for any plan that has no evaluation window and for an
    evaluation with no recorded expiry, so a countdown is never rendered for a
    tenant that does not have one.
    """
    org = organization or {}
    if normalize_plan(org.get('plan')) not in EVALUATION_PLANS:
        return None
    expires_at = _as_datetime(org.get('evaluation_expires_at'))
    if expires_at is None:
        return None
    delta = expires_at - (now or datetime.now(timezone.utc))
    if delta.total_seconds() <= 0:
        return 0
    return max(0, int(delta.total_seconds() // 86400))


def lifecycle_state(organization: Mapping[str, Any] | None, *, now: datetime | None = None) -> str:
    """Canonical lifecycle state. Suspension outranks every other state."""
    org = organization or {}
    status_value = normalize_status(org.get('status'))
    if status_value == STATUS_SUSPENDED:
        return LIFECYCLE_SUSPENDED
    plan = normalize_plan(org.get('plan'))
    if plan == PLAN_ENTERPRISE:
        return LIFECYCLE_ENTERPRISE
    if plan == PLAN_SCALE:
        return LIFECYCLE_ACTIVE_SCALE
    return LIFECYCLE_EXPIRED_PILOT if evaluation_expired(org, now=now) else LIFECYCLE_ACTIVE_PILOT


def monitoring_allowed(organization: Mapping[str, Any] | None, *, now: datetime | None = None) -> bool:
    """Whether this tenant may consume monitoring (RPC/QuickNode) resources.

    A suspended or expired tenant keeps every record it has; it just stops
    initiating new expensive work.
    """
    return lifecycle_state(organization, now=now) not in {LIFECYCLE_SUSPENDED, LIFECYCLE_EXPIRED_PILOT}


def provisioning_allowed(organization: Mapping[str, Any] | None, *, now: datetime | None = None) -> bool:
    """Whether this tenant may create new billable/expensive resources."""
    return monitoring_allowed(organization, now=now)


def effective_entitlements(
    organization: Mapping[str, Any] | None, *, now: datetime | None = None
) -> dict[str, Any]:
    """Entitlements as they apply RIGHT NOW — plan table plus lifecycle.

    The difference from ``get_entitlements`` is the whole Pilot model:

        ACTIVE Pilot    evaluates the production security workflows — monitoring,
                        detection, AI investigation, playbooks, recommendations,
                        evidence — bounded by 30 days, 1 workspace, 5 contracts,
                        10 evidence packages, and recommend-only execution.
        EXPIRED Pilot   keeps every record and loses the ability to start new
                        expensive work, until the organization upgrades.

    Because one function answers both, a screen cannot render "Upgrade required"
    at a feature an active evaluator is supposed to be testing, and cannot render
    a feature as available once the window has closed.

    Limits are untouched: an expired evaluation is not re-scoped, it is stopped.
    """
    entitlements = get_entitlements(organization)
    if monitoring_allowed(organization, now=now):
        return entitlements
    for feature in LIFECYCLE_RESTRICTED_FEATURES:
        entitlements[feature] = False
    return entitlements


def lifecycle_blocked_reason(
    organization: Mapping[str, Any] | None, *, now: datetime | None = None
) -> tuple[str, str] | None:
    """``(code, message)`` when provisioning is blocked, else ``None``."""
    state = lifecycle_state(organization, now=now)
    if state == LIFECYCLE_SUSPENDED:
        return (
            CODE_ORGANIZATION_SUSPENDED,
            'This organization is suspended. Existing records remain available; '
            'contact Decoda to reactivate it.',
        )
    if state == LIFECYCLE_EXPIRED_PILOT:
        return (
            CODE_EVALUATION_EXPIRED,
            'Your Pilot evaluation has ended. Your data is preserved and remains '
            'viewable; upgrade to Scale to resume adding monitoring coverage.',
        )
    return None


# ── Enforcement helpers ──────────────────────────────────────────────────────
def _http_error(status_code: int, detail: dict[str, Any]) -> Exception:
    if HTTPException is None:  # pragma: no cover - only without fastapi installed
        raise RuntimeError('fastapi is required to raise entitlement errors')
    return HTTPException(status_code=status_code, detail=detail)


def plan_limit_error(*, resource: str, limit: int, current: int, plan: str) -> Exception:
    """The canonical PLAN_LIMIT_REACHED response body (HTTP 403)."""
    return _http_error(
        403,
        {
            'code': CODE_PLAN_LIMIT_REACHED,
            'resource': resource,
            'limit': limit,
            'current': current,
            'plan': normalize_plan(plan),
            'message': (
                f'Your {plan_label(plan)} plan supports up to {limit} '
                f'{resource.replace("_", " ")}.'
            ),
        },
    )


def entitlement_required_error(*, entitlement: str, plan: str, message: str | None = None) -> Exception:
    """The canonical PLAN_ENTITLEMENT_REQUIRED response body (HTTP 403)."""
    return _http_error(
        403,
        {
            'code': CODE_PLAN_ENTITLEMENT_REQUIRED,
            'entitlement': entitlement,
            'plan': normalize_plan(plan),
            'message': message
            or f'{entitlement.replace("_", " ").capitalize()} is not available on the {plan_label(plan)} plan.',
        },
    )


def lifecycle_error(*, code: str, message: str, plan: str, lifecycle: str) -> Exception:
    """The canonical expired/suspended response body (HTTP 403)."""
    return _http_error(
        403,
        {'code': code, 'message': message, 'plan': normalize_plan(plan), 'lifecycle_state': lifecycle},
    )


def require_lifecycle_active(
    organization: Mapping[str, Any] | None, *, now: datetime | None = None
) -> None:
    """Raise 403 when the tenant may not initiate new expensive work."""
    blocked = lifecycle_blocked_reason(organization, now=now)
    if blocked is None:
        return
    code, message = blocked
    raise lifecycle_error(
        code=code,
        message=message,
        plan=(organization or {}).get('plan'),
        lifecycle=lifecycle_state(organization, now=now),
    )


def require_entitlement(
    organization: Mapping[str, Any] | None,
    feature_key: str,
    *,
    entitlements: Mapping[str, Any] | None = None,
    message: str | None = None,
    now: datetime | None = None,
) -> None:
    """Raise 403 unless this tenant holds ``feature_key`` right now.

    Resolves against ``effective_entitlements`` — plan AND lifecycle — so an
    expired evaluation is refused a capability its plan row still lists. When the
    lifecycle is what withdrew it, the caller is told THAT, because "your
    evaluation ended" and "your plan does not include this" have different
    remedies and only one of them is upgrading.
    """
    effective = entitlements if entitlements is not None else effective_entitlements(organization, now=now)
    if has_entitlement(effective, feature_key):
        return
    if feature_key in LIFECYCLE_RESTRICTED_FEATURES and has_entitlement(
        get_entitlements(organization), feature_key,
    ):
        require_lifecycle_active(organization, now=now)
    raise entitlement_required_error(
        entitlement=feature_key, plan=(organization or {}).get('plan'), message=message,
    )


def check_limit(
    organization: Mapping[str, Any] | None,
    limit_key: str,
    current: int,
    *,
    entitlements: Mapping[str, Any] | None = None,
) -> None:
    """Raise 403 PLAN_LIMIT_REACHED when one more would exceed the plan limit.

    ``current`` is the count that ALREADY exists, counted server-side. The check
    is ``current >= limit`` because the caller is about to add one.
    """
    effective = entitlements if entitlements is not None else get_entitlements(organization)
    limit = limit_for(effective, limit_key)
    if limit is None:
        return
    if int(current) < limit:
        return
    raise plan_limit_error(
        resource=LIMIT_RESOURCES[limit_key],
        limit=limit,
        current=int(current),
        plan=(organization or {}).get('plan'),
    )


def enforce_resource_creation(
    organization: Mapping[str, Any] | None,
    limit_key: str,
    current: int,
    *,
    entitlements: Mapping[str, Any] | None = None,
    now: datetime | None = None,
) -> None:
    """Lifecycle gate + plan limit, in the order a creation endpoint needs them.

    The lifecycle check comes FIRST so an expired or suspended tenant is told the
    truthful reason ("your evaluation ended") rather than a limit message that
    would imply upgrading the count is what unblocks them.
    """
    require_lifecycle_active(organization, now=now)
    check_limit(organization, limit_key, current, entitlements=entitlements)


# ── Wire shapes ──────────────────────────────────────────────────────────────
def evaluation_payload(
    organization: Mapping[str, Any] | None, *, now: datetime | None = None
) -> dict[str, Any] | None:
    """The ``evaluation`` block of GET /account/plan, or ``None``.

    ``None`` for any plan without an evaluation window — the frontend must not be
    handed a countdown it would render for a Scale or Enterprise tenant.
    """
    org = organization or {}
    if normalize_plan(org.get('plan')) not in EVALUATION_PLANS:
        return None
    started_at = _as_datetime(org.get('evaluation_started_at'))
    expires_at = _as_datetime(org.get('evaluation_expires_at'))
    return {
        'started_at': started_at.isoformat() if started_at else None,
        'expires_at': expires_at.isoformat() if expires_at else None,
        'days_remaining': days_remaining(org, now=now),
        'expired': evaluation_expired(org, now=now),
    }


def usage_entry(current: int, limit: int | None) -> dict[str, Any]:
    """One ``{'current': n, 'limit': n|None}`` usage row."""
    return {'current': int(current), 'limit': limit}


# ── The authoritative capability matrix ──────────────────────────────────────
# ONE table answering "what can each plan do", derived from ``_PLAN_TABLE`` and
# the lifecycle rules above rather than written out a second time. Docs, tests,
# and the pricing page all check themselves against this, so a capability cannot
# be promised in copy while the engine withholds it — or withheld in copy while
# the engine grants it.
MATRIX_ACTIVE_PILOT = 'active_pilot'
MATRIX_EXPIRED_PILOT = 'expired_pilot'
MATRIX_SCALE = 'scale'
MATRIX_ENTERPRISE = 'enterprise'
MATRIX_COLUMNS: tuple[str, ...] = (
    MATRIX_ACTIVE_PILOT, MATRIX_EXPIRED_PILOT, MATRIX_SCALE, MATRIX_ENTERPRISE,
)

#: Where a row's answer comes from. A row that no code reads says so.
SOURCE_FEATURE = 'feature'        # a feature entitlement decides it
SOURCE_LIMIT = 'limit'            # a numeric plan limit decides it
SOURCE_LIFECYCLE = 'lifecycle'    # not plan-gated; the evaluation window decides it
SOURCE_ALWAYS = 'always'          # every tenant has it, expired ones included
SOURCE_AGREEMENT = 'agreement'    # a commercial term, with no software control

#: Rendered cell values. Plain words so a doc, a test, and a person read the same
#: thing.
MATRIX_YES = 'YES'
MATRIX_NO = 'NO'
MATRIX_UNLIMITED = 'UNLIMITED'
MATRIX_CUSTOM = 'CUSTOM'

CAPABILITY_ROWS: tuple[dict[str, Any], ...] = (
    {'capability': 'Core dashboard', 'source': SOURCE_ALWAYS, 'key': None},
    {'capability': 'Monitoring', 'source': SOURCE_LIFECYCLE, 'key': None},
    {'capability': 'Threat detection', 'source': SOURCE_FEATURE, 'key': FEATURE_THREAT_MONITORING},
    {'capability': 'Alerts', 'source': SOURCE_ALWAYS, 'key': None},
    {'capability': 'Incidents', 'source': SOURCE_ALWAYS, 'key': None},
    {'capability': 'AI investigation', 'source': SOURCE_FEATURE, 'key': FEATURE_AI_INVESTIGATION},
    {'capability': 'Incident playbooks', 'source': SOURCE_FEATURE, 'key': FEATURE_INCIDENT_PLAYBOOKS},
    {
        'capability': 'Response recommendations',
        'source': SOURCE_FEATURE,
        'key': FEATURE_RESPONSE_RECOMMENDATIONS,
    },
    {'capability': 'Evidence workflows', 'source': SOURCE_LIFECYCLE, 'key': None},
    {'capability': 'Audit-ready exports', 'source': SOURCE_FEATURE, 'key': FEATURE_EVIDENCE_EXPORT},
    {'capability': 'Workspaces', 'source': SOURCE_LIMIT, 'key': LIMIT_WORKSPACES},
    {'capability': 'Contracts', 'source': SOURCE_LIMIT, 'key': LIMIT_MONITORED_CONTRACTS},
    {'capability': 'Evidence packages', 'source': SOURCE_LIMIT, 'key': LIMIT_EVIDENCE_PACKAGES},
    {
        'capability': 'Automatic production execution',
        'source': SOURCE_FEATURE,
        'key': FEATURE_AUTOMATIC_EXECUTION,
    },
    {'capability': 'Multi-network', 'source': SOURCE_FEATURE, 'key': FEATURE_MULTI_NETWORK},
    {'capability': 'Custom evidence templates', 'source': SOURCE_FEATURE, 'key': FEATURE_CUSTOM_EVIDENCE_TEMPLATES},
    {'capability': 'Custom integrations', 'source': SOURCE_FEATURE, 'key': FEATURE_CUSTOM_INTEGRATIONS},
    {'capability': 'Priority alert routing', 'source': SOURCE_FEATURE, 'key': FEATURE_PRIORITY_ROUTING},
    {'capability': 'Custom SLA', 'source': SOURCE_AGREEMENT, 'key': None},
)


def _matrix_organizations(now: datetime) -> dict[str, dict[str, Any]]:
    """One synthetic organization per matrix column, so every cell is computed."""
    return {
        MATRIX_ACTIVE_PILOT: {
            'plan': PLAN_PILOT, 'status': STATUS_ACTIVE,
            'evaluation_expires_at': now + timedelta(days=1),
        },
        MATRIX_EXPIRED_PILOT: {
            'plan': PLAN_PILOT, 'status': STATUS_ACTIVE,
            'evaluation_expires_at': now - timedelta(days=1),
        },
        MATRIX_SCALE: {'plan': PLAN_SCALE, 'status': STATUS_ACTIVE},
        MATRIX_ENTERPRISE: {'plan': PLAN_ENTERPRISE, 'status': STATUS_ACTIVE},
    }


def _matrix_cell(row: Mapping[str, Any], organization: Mapping[str, Any], now: datetime) -> str:
    source = row['source']
    if source == SOURCE_ALWAYS:
        return MATRIX_YES
    if source == SOURCE_AGREEMENT:
        return MATRIX_YES if normalize_plan(organization.get('plan')) == PLAN_ENTERPRISE else MATRIX_NO
    if source == SOURCE_LIFECYCLE:
        return MATRIX_YES if monitoring_allowed(organization, now=now) else MATRIX_NO
    entitlements = effective_entitlements(organization, now=now)
    if source == SOURCE_FEATURE:
        return MATRIX_YES if has_entitlement(entitlements, str(row['key'])) else MATRIX_NO
    limit = limit_for(entitlements, str(row['key']))
    if limit is None:
        return MATRIX_CUSTOM if normalize_plan(organization.get('plan')) == PLAN_ENTERPRISE else MATRIX_UNLIMITED
    return str(limit)


def capability_matrix(*, now: datetime | None = None) -> list[dict[str, Any]]:
    """The capability matrix as rows: capability, source, and one cell per column.

    Computed, never transcribed. ``enforced`` is False for a row whose entitlement
    key no code path reads — the honest signal that the row states an intention
    rather than a control.
    """
    moment = now or datetime.now(timezone.utc)
    organizations = _matrix_organizations(moment)
    rows: list[dict[str, Any]] = []
    for row in CAPABILITY_ROWS:
        key = row['key']
        rows.append({
            'capability': row['capability'],
            'source': row['source'],
            'key': key,
            'enforced': not (row['source'] == SOURCE_FEATURE and key in UNENFORCED_FEATURES),
            **{column: _matrix_cell(row, organizations[column], moment) for column in MATRIX_COLUMNS},
        })
    return rows
