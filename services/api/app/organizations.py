"""Organization (tenant) resolution, usage accounting, and lifecycle writes.

The layer between authentication and the entitlement engine:

    auth → workspace context → THIS MODULE → entitlements → resource service → db

Where the organization context comes from
-----------------------------------------
ALWAYS from the session-resolved workspace: ``workspaces.organization_id`` for
the workspace the authenticated user is a member of. An ``organization_id`` in a
query string, a request body, or a header is never read here and never trusted.
The only endpoints that address an organization by id are the internal founder
admin endpoints, which authorize on ``users.is_internal_admin`` first.

Schema availability
-------------------
Every read is guarded by ``tenancy_schema_ready``. A deployment whose API rolled
out ahead of migration 0150 reports ``available = False`` and keeps its existing
behaviour rather than crashing or inventing a tenant. Enforcement callers treat
an unavailable context as "no organization-level rule to apply" — which is not a
weakening, because every pre-existing workspace-level check still runs. The
condition is reported truthfully by ``GET /account/plan`` instead of being
rendered as a healthy Pilot.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

from services.api.app import entitlements as ent

try:  # fastapi is stubbed in the offline test runner
    from fastapi import HTTPException, status
except Exception:  # pragma: no cover - exercised only without fastapi installed
    HTTPException = None  # type: ignore[assignment]
    status = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

TENANCY_TABLES: tuple[str, ...] = ('organizations', 'organization_memberships')

ORGANIZATION_ROLES: tuple[str, ...] = ('owner', 'admin', 'analyst', 'viewer')

#: workspace role → organization role. Mirrors pilot.ROLE_CANONICAL_MAP so the
#: two vocabularies cannot drift.
WORKSPACE_ROLE_TO_ORG_ROLE: dict[str, str] = {
    'owner': 'owner',
    'workspace_owner': 'owner',
    'admin': 'admin',
    'workspace_admin': 'admin',
    'analyst': 'analyst',
    'workspace_member': 'analyst',
    'viewer': 'viewer',
}

#: The discovery vocabulary, shared with apps/web/app/plan-feedback.ts. Widened
#: by migration 0153; every value migration 0150 allowed is still here, so no
#: stored row was invalidated. 'missing_feature' carries the "Missing capability"
#: label rather than gaining a near-duplicate sibling — two values meaning one
#: thing would split the roadmap signal the founder console counts.
FEEDBACK_TYPES: tuple[str, ...] = (
    'security',
    'detection_accuracy',
    'false_positive',
    'missed_detection',
    'investigation',
    'incident_response',
    'evidence_audit',
    'integration',
    'policy_controls',
    'usability',
    'missing_feature',
    'other',
)

#: How deep the submission was. One table, three depths — see migration 0153.
FEEDBACK_MODE_QUICK = 'quick'
FEEDBACK_MODE_DETAILED = 'detailed'
FEEDBACK_MODE_END_OF_PILOT = 'end_of_pilot'
FEEDBACK_MODES: tuple[str, ...] = (
    FEEDBACK_MODE_QUICK,
    FEEDBACK_MODE_DETAILED,
    FEEDBACK_MODE_END_OF_PILOT,
)

FEEDBACK_SEVERITIES: tuple[str, ...] = ('critical', 'high', 'medium', 'low')
FEEDBACK_PRODUCTION_BLOCKERS: tuple[str, ...] = ('yes', 'no', 'not_sure')
FEEDBACK_CONTINUE_INTENTS: tuple[str, ...] = ('yes', 'maybe', 'no')

FEEDBACK_MAX_MESSAGE_CHARS = 4000

#: The free-text discovery answers, in the order the form asks them. Every one is
#: OPTIONAL at this layer: the form decides what it insists on, and a half-filled
#: submission is worth more than a refused one. Each is length-capped and
#: secret-scanned exactly like ``message`` — a credential pasted into "how do you
#: handle this today?" is no less a credential than one pasted into "what
#: happened?".
FEEDBACK_NARRATIVE_FIELDS: tuple[str, ...] = (
    'goal_or_task',
    'security_problem',
    'current_workaround',
    'where_decoda_helped',
    'missing_or_difficult',
    'deployment_requirement',
    'paid_capability',
)

#: Columns added by migration 0153. Probed before a detailed submission is
#: accepted so an API that rolled out ahead of its migration REFUSES the deeper
#: form with a truthful reason, rather than accepting the customer's answers and
#: discarding the half the database cannot store.
FEEDBACK_DETAIL_COLUMNS: tuple[str, ...] = (
    'feedback_mode',
    'severity',
    *FEEDBACK_NARRATIVE_FIELDS,
    'production_blocker',
    'contact_permission',
    'continue_intent',
    'pilot_day',
)

#: Exact email addresses that may reach the founder/internal admin surface even
#: without the database flag, so a fresh deployment can be bootstrapped. Wildcard
#: and domain-level entries are rejected outright — see
#: ``internal_admin_email_allowlist``.
INTERNAL_ADMIN_EMAILS_ENV = 'DECODA_INTERNAL_ADMIN_EMAILS'

CODE_INTERNAL_ADMIN_REQUIRED = 'INTERNAL_ADMIN_REQUIRED'
CODE_ORGANIZATION_NOT_FOUND = 'ORGANIZATION_NOT_FOUND'

_SLUG_RE = re.compile(r'[^a-z0-9]+')


# ── small local utilities (kept free of any pilot import) ────────────────────
def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, default=str)


def _slugify(value: str) -> str:
    slug = _SLUG_RE.sub('-', str(value or '').strip().lower()).strip('-')
    return slug or 'organization'


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, 'isoformat') else str(value)


def _email_or_none(value: Any) -> str | None:
    """A usable address, or None.

    A blank or whitespace-only column is reported as ABSENT rather than as an
    empty string: the console renders "no contact on record" for None, and an
    empty cell that looks like a resolved person would be a quieter lie.
    """
    text = str(value).strip() if value is not None else ''
    return text or None


def _row_dict(row: Any) -> dict[str, Any] | None:
    if row is None:
        return None
    return dict(row)


def _count(connection: Any, sql: str, params: tuple[Any, ...]) -> int:
    row = connection.execute(sql, params).fetchone()
    return int((row or {}).get('count') or 0)


# ── schema readiness ─────────────────────────────────────────────────────────
#: The tenancy schema is READY, definitively ABSENT, or UNKNOWN because the probe
#: itself failed. The three are kept apart deliberately: "we could not look" is
#: not "there is none", and a security decision that fails closed must be able to
#: tell them apart. Treating an unreadable probe as ABSENT would silently disable
#: every organization-level rule during a database incident.
SCHEMA_READY = 'ready'
SCHEMA_ABSENT = 'absent'
SCHEMA_UNKNOWN = 'unknown'


def tenancy_schema_state(connection: Any) -> str:
    """Whether migration 0150's tables and the workspace link column exist."""
    try:
        row = connection.execute(
            '''
            SELECT
                (SELECT COUNT(*) FROM information_schema.tables
                  WHERE table_schema = 'public' AND table_name = ANY(%s)) AS table_count,
                (SELECT COUNT(*) FROM information_schema.columns
                  WHERE table_schema = 'public' AND table_name = 'workspaces'
                    AND column_name = 'organization_id') AS link_count
            ''',
            (list(TENANCY_TABLES),),
        ).fetchone()
    except Exception:
        logger.warning('tenancy_schema_probe_failed result=unknown', exc_info=True)
        return SCHEMA_UNKNOWN
    data = _row_dict(row) or {}
    ready = (
        int(data.get('table_count') or 0) >= len(TENANCY_TABLES)
        and int(data.get('link_count') or 0) >= 1
    )
    return SCHEMA_READY if ready else SCHEMA_ABSENT


def tenancy_schema_ready(connection: Any) -> bool:
    """True only when the tenancy schema is definitively present.

    A failed probe is NOT ready. Callers that merely degrade gracefully (the plan
    overlay, provisioning) can use this; callers making a security decision must
    use ``tenancy_schema_state`` so they can fail closed on UNKNOWN rather than
    behaving as though the schema were simply absent.
    """
    return tenancy_schema_state(connection) == SCHEMA_READY


# ── organization reads ───────────────────────────────────────────────────────
_ORGANIZATION_COLUMNS = (
    'id, name, slug, plan, status, evaluation_started_at, evaluation_expires_at, '
    'entitlement_overrides, created_at, updated_at'
)


def get_organization(connection: Any, organization_id: str) -> dict[str, Any] | None:
    row = connection.execute(
        f'SELECT {_ORGANIZATION_COLUMNS} FROM organizations WHERE id = %s',
        (str(organization_id),),
    ).fetchone()
    return _row_dict(row)


def organization_for_workspace(connection: Any, workspace_id: str) -> dict[str, Any] | None:
    """The organization that OWNS this workspace, or ``None`` when unlinked."""
    row = connection.execute(
        f'''
        SELECT {', '.join('o.' + col.strip() for col in _ORGANIZATION_COLUMNS.split(','))}
        FROM workspaces w
        JOIN organizations o ON o.id = w.organization_id
        WHERE w.id = %s
        ''',
        (str(workspace_id),),
    ).fetchone()
    return _row_dict(row)


def _unique_slug(connection: Any, base: str) -> str:
    slug = _slugify(base)
    candidate = slug
    suffix = 1
    while connection.execute('SELECT 1 FROM organizations WHERE slug = %s', (candidate,)).fetchone() is not None:
        suffix += 1
        candidate = f'{slug}-{suffix}'
    return candidate


def create_organization(
    connection: Any,
    *,
    name: str,
    plan: str = ent.PLAN_PILOT,
    status_value: str = ent.STATUS_ACTIVE,
    organization_id: str | None = None,
    start_evaluation: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Insert one organization. Starts an evaluation window for evaluation plans.

    The evaluation length comes from ``entitlements.evaluation_days()`` — the one
    configured default — so no caller ever writes a hard-coded duration.
    """
    plan_key = ent.normalize_plan(plan)
    status_key = ent.normalize_status(status_value)
    org_id = str(organization_id or uuid.uuid4())
    started_at: datetime | None = None
    expires_at: datetime | None = None
    if start_evaluation and plan_key in ent.EVALUATION_PLANS:
        started_at, expires_at = ent.evaluation_window(now or _utc_now())
    connection.execute(
        '''
        INSERT INTO organizations (
            id, name, slug, plan, status, evaluation_started_at, evaluation_expires_at,
            entitlement_overrides, created_at, updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, '{}'::jsonb, NOW(), NOW())
        ON CONFLICT (id) DO NOTHING
        ''',
        (org_id, name, _unique_slug(connection, name), plan_key, status_key, started_at, expires_at),
    )
    created = get_organization(connection, org_id)
    if created is None:  # pragma: no cover - only on a concurrent delete
        raise _http_error(500, {'code': 'ORGANIZATION_CREATE_FAILED', 'message': 'Organization could not be created.'})
    return created


def upsert_membership(
    connection: Any, *, organization_id: str, user_id: str, role: str = 'owner',
) -> None:
    """Record (or upgrade) one user's membership of an organization."""
    normalized = WORKSPACE_ROLE_TO_ORG_ROLE.get(str(role or '').strip().lower(), 'analyst')
    connection.execute(
        '''
        INSERT INTO organization_memberships (id, organization_id, user_id, role, created_at, updated_at)
        VALUES (%s, %s, %s, %s, NOW(), NOW())
        ON CONFLICT (organization_id, user_id) DO UPDATE SET role = EXCLUDED.role, updated_at = NOW()
        ''',
        (str(uuid.uuid4()), str(organization_id), str(user_id), normalized),
    )


def membership_role(connection: Any, *, organization_id: str, user_id: str) -> str | None:
    row = connection.execute(
        'SELECT role FROM organization_memberships WHERE organization_id = %s AND user_id = %s',
        (str(organization_id), str(user_id)),
    ).fetchone()
    data = _row_dict(row)
    return str(data['role']) if data else None


def attach_workspace_to_organization(
    connection: Any, *, workspace_id: str, organization_id: str,
) -> None:
    """Link a workspace to its owning organization. Never re-points a linked one."""
    connection.execute(
        'UPDATE workspaces SET organization_id = %s WHERE id = %s AND organization_id IS NULL',
        (str(organization_id), str(workspace_id)),
    )


def ensure_organization_for_workspace(
    connection: Any,
    *,
    workspace_id: str,
    workspace_name: str | None = None,
    owner_user_id: str | None = None,
) -> dict[str, Any] | None:
    """Return the workspace's organization, creating a deterministic one if absent.

    A workspace can reach this function unlinked only when it was written by an
    API process that predates migration 0150 (a rolling deploy). The organization
    id is derived from the workspace id, exactly as the migration's backfill does,
    so the heal is idempotent and can never mint a second tenant for the same
    workspace. Writes are left for the caller's transaction to commit.
    """
    existing = organization_for_workspace(connection, workspace_id)
    if existing is not None:
        return existing
    workspace = _row_dict(
        connection.execute(
            'SELECT id, name, slug, created_by_user_id FROM workspaces WHERE id = %s',
            (str(workspace_id),),
        ).fetchone()
    )
    if workspace is None:
        return None
    organization = create_organization(
        connection,
        name=str(workspace_name or workspace.get('name') or 'Organization'),
        plan=ent.PLAN_PILOT,
        organization_id=str(workspace['id']),
        # A workspace that predates the tenancy schema is grandfathered: it is not
        # given a retroactive evaluation deadline it could already have missed.
        start_evaluation=False,
    )
    attach_workspace_to_organization(
        connection, workspace_id=str(workspace['id']), organization_id=str(organization['id']),
    )
    creator = owner_user_id or workspace.get('created_by_user_id')
    if creator:
        upsert_membership(connection, organization_id=str(organization['id']), user_id=str(creator), role='owner')
    logger.info(
        'organization_healed_for_unlinked_workspace workspace_id=%s organization_id=%s',
        workspace_id, organization['id'],
    )
    return organization


# ── the context every enforcement caller resolves ────────────────────────────
def resolve_context(
    connection: Any,
    workspace_id: str,
    *,
    heal: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Organization context for a session-resolved workspace.

    Returns ``{'available', 'organization', 'entitlements', 'lifecycle_state',
    'reason'}``. ``available`` is False only when the tenancy schema is not yet
    migrated or the workspace could not be resolved at all — states the caller
    reports truthfully rather than papering over with a default plan.

    ``entitlements`` are EFFECTIVE entitlements: plan table plus lifecycle. An
    active Pilot therefore reports the evaluation workflows it is meant to be
    testing, and an expired one reports them withdrawn, from the same field —
    which is what lets one screen render both states without a plan branch.
    """
    if not workspace_id:
        return _unavailable('workspace_context_missing')
    if not tenancy_schema_ready(connection):
        return _unavailable('tenancy_schema_not_migrated')
    try:
        organization = organization_for_workspace(connection, workspace_id)
        if organization is None and heal:
            organization = ensure_organization_for_workspace(connection, workspace_id=workspace_id)
    except Exception:
        logger.warning('organization_context_read_failed workspace_id=%s', workspace_id, exc_info=True)
        return _unavailable('organization_lookup_failed')
    if organization is None:
        return _unavailable('organization_not_linked')
    return {
        'available': True,
        'reason': None,
        'organization': organization,
        'entitlements': ent.effective_entitlements(organization, now=now),
        'lifecycle_state': ent.lifecycle_state(organization, now=now),
    }


def _unavailable(reason: str) -> dict[str, Any]:
    return {
        'available': False,
        'reason': reason,
        'organization': None,
        'entitlements': None,
        'lifecycle_state': None,
    }


# ── worker due-selection ─────────────────────────────────────────────────────
#: The ONE SQL statement of "this tenant may not consume budget right now",
#: written as a NOT EXISTS so a worker can append it to an existing query without
#: touching any of its joins. Mirrors ``entitlements.monitoring_allowed``: a
#: suspended organization, or a Pilot whose recorded evaluation deadline has
#: passed.
#:
#: Two absences are deliberately NOT exclusions. A workspace with no organization
#: link predates migration 0150, and silently stopping its work would be a far
#: worse failure than briefly not applying the newer rule to it — the API heals
#: the link on the tenant's next request. And a Pilot with no recorded deadline is
#: grandfathered: a missing date is not evidence of expiry.
_INACTIVE_TENANT_EXCLUSION_TEMPLATE = """
              AND NOT EXISTS (
                  SELECT 1
                  FROM workspaces tenant_ws
                  JOIN organizations tenant_org ON tenant_org.id = tenant_ws.organization_id
                  WHERE tenant_ws.id = {workspace_column}
                    AND (
                        tenant_org.status <> 'active'
                        OR (
                            tenant_org.plan = 'pilot'
                            AND tenant_org.evaluation_expires_at IS NOT NULL
                            AND tenant_org.evaluation_expires_at <= NOW()
                        )
                    )
              )
"""


def inactive_tenant_exclusion_sql(workspace_column: str) -> str:
    """The exclusion clause for one query, bound to its workspace-id expression."""
    return _INACTIVE_TENANT_EXCLUSION_TEMPLATE.format(workspace_column=workspace_column)


def tenant_exclusion_schema_ready(connection: Any) -> bool:
    """Whether ``organizations`` and the workspace link column both exist.

    Fails CLOSED on the clause, not on the work: a probe that errors returns
    False, so the worker keeps running without the newer filter rather than
    stopping every tenant's monitoring on an information_schema hiccup.
    """
    try:
        row = connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM information_schema.tables
                  WHERE table_schema = 'public' AND table_name = 'organizations') AS org_table,
                (SELECT COUNT(*) FROM information_schema.columns
                  WHERE table_schema = 'public' AND table_name = 'workspaces'
                    AND column_name = 'organization_id') AS link_column
            """,
        ).fetchone()
    except Exception:
        logger.warning('organization_monitoring_filter_probe_failed action=filter_not_applied')
        return False
    data = dict(row or {})
    return int(data.get('org_table') or 0) >= 1 and int(data.get('link_column') or 0) >= 1


def worker_tenant_exclusion_sql(connection: Any, workspace_column: str) -> str:
    """The exclusion clause, or '' when the tenancy schema is not migrated."""
    if not tenant_exclusion_schema_ready(connection):
        return ''
    return inactive_tenant_exclusion_sql(workspace_column)


# ── usage accounting ─────────────────────────────────────────────────────────
def count_workspaces(connection: Any, organization_id: str) -> int:
    return _count(
        connection,
        'SELECT COUNT(*) AS count FROM workspaces WHERE organization_id = %s',
        (str(organization_id),),
    )


def count_monitored_contracts(connection: Any, organization_id: str) -> int:
    """Registered RWA contracts (assets) across every workspace in the tenant."""
    return _count(
        connection,
        '''
        SELECT COUNT(*) AS count
        FROM assets a
        JOIN workspaces w ON w.id = a.workspace_id
        WHERE w.organization_id = %s AND a.deleted_at IS NULL
        ''',
        (str(organization_id),),
    )


def count_monitoring_targets(connection: Any, organization_id: str) -> int:
    return _count(
        connection,
        '''
        SELECT COUNT(*) AS count
        FROM targets t
        JOIN workspaces w ON w.id = t.workspace_id
        WHERE w.organization_id = %s AND t.deleted_at IS NULL
        ''',
        (str(organization_id),),
    )


def count_evidence_packages(connection: Any, organization_id: str) -> int:
    """Evidence packages = proof-bundle export jobs, the customer-facing artifact."""
    return _count(
        connection,
        '''
        SELECT COUNT(*) AS count
        FROM export_jobs e
        JOIN workspaces w ON w.id = e.workspace_id
        WHERE w.organization_id = %s AND e.export_type = 'proof_bundle'
        ''',
        (str(organization_id),),
    )


_USAGE_COUNTERS = {
    ent.LIMIT_WORKSPACES: count_workspaces,
    ent.LIMIT_MONITORED_CONTRACTS: count_monitored_contracts,
    ent.LIMIT_MONITORING_TARGETS: count_monitoring_targets,
    ent.LIMIT_EVIDENCE_PACKAGES: count_evidence_packages,
}


def current_usage(connection: Any, organization_id: str, limit_key: str) -> int:
    counter = _USAGE_COUNTERS.get(limit_key)
    if counter is None:
        raise ValueError(f'No usage counter for entitlement limit {limit_key!r}')
    return counter(connection, organization_id)


def usage_summary(connection: Any, organization: Mapping[str, Any]) -> dict[str, Any]:
    """The ``usage`` block of GET /account/plan, one row per metered limit."""
    entitlements = ent.get_entitlements(organization)
    organization_id = str(organization['id'])
    return {
        ent.LIMIT_RESOURCES[key]: ent.usage_entry(
            current_usage(connection, organization_id, key), ent.limit_for(entitlements, key),
        )
        for key in ent.LIMIT_KEYS
    }


# ── enforcement entry points used by resource services ───────────────────────
def enforce_creation(
    connection: Any,
    context: Mapping[str, Any],
    limit_key: str,
    *,
    now: datetime | None = None,
) -> None:
    """Lifecycle + plan-limit gate for creating one more of ``limit_key``.

    A context that is not ``available`` applies no organization rule: every
    pre-existing workspace-level check still runs, so this is never weaker than
    the behaviour before migration 0150 — it just cannot add the newer rule on a
    deployment that has not migrated yet.
    """
    if not context.get('available'):
        return
    organization = context.get('organization') or {}
    ent.enforce_resource_creation(
        organization,
        limit_key,
        current_usage(connection, str(organization['id']), limit_key),
        entitlements=context.get('entitlements'),
        now=now,
    )


def enforce_lifecycle_active(context: Mapping[str, Any], *, now: datetime | None = None) -> None:
    """Refuse new expensive work for a suspended or expired tenant."""
    if not context.get('available'):
        return
    ent.require_lifecycle_active(context.get('organization') or {}, now=now)


def enforce_entitlement(context: Mapping[str, Any], feature_key: str, *, message: str | None = None) -> None:
    """Refuse an operation this tenant's plan does not include."""
    if not context.get('available'):
        return
    ent.require_entitlement(
        context.get('organization') or {},
        feature_key,
        entitlements=context.get('entitlements'),
        message=message,
    )


# ── internal (founder) admin authorization ───────────────────────────────────
def internal_admin_email_allowlist() -> frozenset[str]:
    """Exact internal-staff addresses from the deployment environment.

    Bootstraps the founder admin surface before anyone holds the database flag.
    Entries are normalised to lowercase and validated: an entry without exactly
    one ``@``, or containing a wildcard character, is DISCARDED with a warning
    rather than widened into a domain-level grant. There is deliberately no
    syntax that grants "everyone at a domain".
    """
    raw = (os.getenv(INTERNAL_ADMIN_EMAILS_ENV) or '').strip()
    if not raw:
        return frozenset()
    allowed: set[str] = set()
    for entry in raw.split(','):
        candidate = entry.strip().lower()
        if not candidate:
            continue
        if any(ch in candidate for ch in '*%?') or candidate.count('@') != 1 or candidate.startswith('@'):
            logger.warning(
                'internal_admin_allowlist_entry_rejected reason=not_an_exact_address env=%s',
                INTERNAL_ADMIN_EMAILS_ENV,
            )
            continue
        allowed.add(candidate)
    return frozenset(allowed)


def is_internal_admin(connection: Any, user_id: str) -> bool:
    """Whether this user holds internal staff access.

    Two independent sources, both server-side: the ``users.is_internal_admin``
    column and the exact-address deployment allowlist. Neither can be influenced
    by a request parameter, a header, a body field, or a workspace role.

    A flag that could not be READ is not a grant. On a deployment whose API is
    running ahead of migration 0150 the column does not exist yet, and on any
    other read failure the answer is unknown — both report False, so the failure
    mode is a refusal rather than an accidental internal-admin session.
    """
    try:
        row = _row_dict(
            connection.execute(
                'SELECT email, is_internal_admin FROM users WHERE id = %s', (str(user_id),),
            ).fetchone()
        )
    except Exception:
        logger.warning('internal_admin_read_failed user_id=%s', user_id, exc_info=True)
        return False
    if row is None:
        return False
    if bool(row.get('is_internal_admin')):
        return True
    email = str(row.get('email') or '').strip().lower()
    return bool(email) and email in internal_admin_email_allowlist()


def require_internal_admin(connection: Any, request: Any) -> dict[str, Any]:
    """Authenticate, then require internal staff access. Raises 403 otherwise.

    A customer receives 403 (not 404): the route's existence is not a secret, and
    an authenticated customer learning that an internal console exists discloses
    nothing about another tenant. What they never receive is any organization
    data.
    """
    from services.api.app import pilot  # local import: pilot imports this module

    user = pilot.authenticate_with_connection(connection, request)
    if not is_internal_admin(connection, str(user['id'])):
        logger.warning('internal_admin_denied user_id=%s', user.get('id'))
        raise _http_error(
            403,
            {
                'code': CODE_INTERNAL_ADMIN_REQUIRED,
                'message': 'This area is restricted to Decoda internal staff.',
            },
        )
    return user


# ── lifecycle writes (founder admin) ─────────────────────────────────────────
def _require_organization(connection: Any, organization_id: str) -> dict[str, Any]:
    organization = get_organization(connection, organization_id)
    if organization is None:
        raise _http_error(
            404, {'code': CODE_ORGANIZATION_NOT_FOUND, 'message': 'Organization not found.'},
        )
    return organization


def extend_evaluation(
    connection: Any, *, organization_id: str, days: int, now: datetime | None = None,
) -> dict[str, Any]:
    """Push an evaluation deadline out by ``days``, reactivating if it had expired.

    Extends from whichever is later — the current expiry or now — so extending an
    already-expired evaluation grants the full requested window rather than a
    deadline still in the past.
    """
    if not isinstance(days, int) or isinstance(days, bool) or days < 1 or days > ent.MAX_EVALUATION_EXTENSION_DAYS:
        raise _http_error(
            400,
            {
                'code': 'INVALID_EVALUATION_EXTENSION',
                'message': f'days must be an integer between 1 and {ent.MAX_EVALUATION_EXTENSION_DAYS}.',
            },
        )
    organization = _require_organization(connection, organization_id)
    moment = now or _utc_now()
    current_expiry = organization.get('evaluation_expires_at')
    if hasattr(current_expiry, 'tzinfo') and current_expiry is not None:
        base = current_expiry if current_expiry.tzinfo else current_expiry.replace(tzinfo=timezone.utc)
        base = max(base, moment)
    else:
        base = moment
    new_expiry = base + timedelta(days=int(days))
    connection.execute(
        '''
        UPDATE organizations
        SET evaluation_expires_at = %s,
            evaluation_started_at = COALESCE(evaluation_started_at, %s),
            status = CASE WHEN status = %s THEN %s ELSE status END,
            updated_at = NOW()
        WHERE id = %s
        ''',
        (new_expiry, moment, ent.STATUS_EXPIRED, ent.STATUS_ACTIVE, str(organization_id)),
    )
    return _require_organization(connection, organization_id)


def set_status(connection: Any, *, organization_id: str, status_value: str) -> dict[str, Any]:
    """Suspend, reactivate, or mark expired. Never touches customer records."""
    key = str(status_value or '').strip().lower()
    if key not in ent.STATUSES:
        raise _http_error(
            400,
            {'code': 'INVALID_ORGANIZATION_STATUS', 'message': f'status must be one of {", ".join(ent.STATUSES)}.'},
        )
    _require_organization(connection, organization_id)
    connection.execute(
        'UPDATE organizations SET status = %s, updated_at = NOW() WHERE id = %s',
        (key, str(organization_id)),
    )
    return _require_organization(connection, organization_id)


def set_plan(
    connection: Any, *, organization_id: str, plan: str, now: datetime | None = None,
) -> dict[str, Any]:
    """Change an organization's plan in place.

    Pilot → Scale is exactly this one row update: no new account, no new
    workspace, no data migration. Every asset, alert, incident, evidence package,
    and investigation stays where it is and simply becomes governed by the new
    plan's limits on the next request.

    Moving OFF an evaluation plan clears the evaluation window, so a Scale tenant
    can never render a "days remaining" countdown. Moving ONTO Pilot starts a
    fresh window using the configured evaluation length.
    """
    key = str(plan or '').strip().lower()
    if key not in ent.PLANS:
        raise _http_error(
            400, {'code': 'INVALID_PLAN', 'message': f'plan must be one of {", ".join(ent.PLANS)}.'},
        )
    organization = _require_organization(connection, organization_id)
    previous_plan = ent.normalize_plan(organization.get('plan'))
    if key in ent.EVALUATION_PLANS:
        if previous_plan in ent.EVALUATION_PLANS:
            started_at = organization.get('evaluation_started_at')
            expires_at = organization.get('evaluation_expires_at')
        else:
            started_at, expires_at = ent.evaluation_window(now or _utc_now())
        connection.execute(
            '''
            UPDATE organizations
            SET plan = %s, evaluation_started_at = %s, evaluation_expires_at = %s, updated_at = NOW()
            WHERE id = %s
            ''',
            (key, started_at, expires_at, str(organization_id)),
        )
    else:
        connection.execute(
            '''
            UPDATE organizations
            SET plan = %s,
                evaluation_started_at = NULL,
                evaluation_expires_at = NULL,
                status = CASE WHEN status = %s THEN %s ELSE status END,
                updated_at = NOW()
            WHERE id = %s
            ''',
            (key, ent.STATUS_EXPIRED, ent.STATUS_ACTIVE, str(organization_id)),
        )
    return _require_organization(connection, organization_id)


# ── pilot feedback (Phase 8) ─────────────────────────────────────────────────
SECRET_HINT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r'0x[a-fA-F0-9]{64}'),                    # raw 32-byte private key
    re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----'),   # PEM private key block
)

#: A mnemonic is the whole message: exactly a BIP-39 word count of bare
#: lowercase words with no punctuation. Deliberately NOT "12 or more short
#: words anywhere in the text" — that shape matches ordinary prose, and
#: refusing a real bug report because it ran long would be the worse failure.
_MNEMONIC_WORD_COUNTS = frozenset({12, 15, 18, 21, 24})
_MNEMONIC_RE = re.compile(r'^[a-z]{3,8}(?: [a-z]{3,8})+$')


def looks_like_secret(message: str) -> bool:
    """Whether the text contains something shaped like a credential.

    Used to REFUSE the submission rather than to store and redact it: feedback
    that carries a key must not reach the database at all, so there is nothing to
    leak later from the internal console or an audit row.
    """
    text = str(message or '')
    if any(pattern.search(text) for pattern in SECRET_HINT_PATTERNS):
        return True
    collapsed = ' '.join(text.strip().lower().split())
    return bool(
        _MNEMONIC_RE.fullmatch(collapsed)
        and len(collapsed.split(' ')) in _MNEMONIC_WORD_COUNTS
    )


def feedback_detail_schema_state(connection: Any) -> str:
    """Whether migration 0153's discovery columns exist. READY / ABSENT / UNKNOWN.

    Kept separate from ``tenancy_schema_state`` because the two migrations roll
    out independently: a deployment can have tenancy but not yet the discovery
    columns, and the quick form must keep working throughout.
    """
    try:
        row = connection.execute(
            """
            SELECT COUNT(*) AS column_count FROM information_schema.columns
             WHERE table_schema = 'public' AND table_name = 'organization_feedback'
               AND column_name = ANY(%s)
            """,
            (list(FEEDBACK_DETAIL_COLUMNS),),
        ).fetchone()
    except Exception:
        logger.warning('feedback_detail_schema_probe_failed result=unknown', exc_info=True)
        return SCHEMA_UNKNOWN
    data = _row_dict(row) or {}
    ready = int(data.get('column_count') or 0) >= len(FEEDBACK_DETAIL_COLUMNS)
    return SCHEMA_READY if ready else SCHEMA_ABSENT


def feedback_detail_schema_ready(connection: Any) -> bool:
    """True only when the discovery columns are definitively present."""
    return feedback_detail_schema_state(connection) == SCHEMA_READY


def _choice(value: Any, allowed: tuple[str, ...], *, field: str, code: str) -> str | None:
    """One value from a closed vocabulary, or None when not supplied.

    An unrecognised value is REFUSED rather than coerced to a default. Silently
    storing 'medium' for a severity the customer never chose would put a number
    in the founder's priority list that no one said.
    """
    text = str(value or '').strip().lower()
    if not text:
        return None
    if text not in allowed:
        raise _http_error(
            400,
            {'code': code, 'message': f'{field} must be one of {", ".join(allowed)}.'},
        )
    return text


#: Affirmative spellings accepted for the contact-permission checkbox. Anything
#: else — including the STRING 'false', which is truthy in Python — is read as
#: "no". Permission to contact a customer about their security feedback is
#: granted explicitly or not at all; bool('false') granting it would put a "Yes"
#: in the founder console that the customer never gave.
_AFFIRMATIVE = frozenset({'true', '1', 'yes', 'y', 'on'})


def _permission(value: Any) -> bool:
    """Whether the customer explicitly granted contact permission."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in _AFFIRMATIVE
    return False


#: A date or timestamp the founder console may filter on. Bounded here so a
#: malformed value is refused as a 400 naming the field, rather than reaching
#: PostgreSQL and surfacing as a 500 the founder cannot act on.
_FILTER_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2})?(\.\d+)?(Z|[+-]\d{2}:?\d{2})?)?$')


def _date_filter(value: Any, *, field: str) -> str | None:
    """One ISO-8601 date/timestamp filter value, or None when not supplied."""
    text = str(value or '').strip()
    if not text:
        return None
    if not _FILTER_DATE_RE.match(text):
        raise _http_error(
            400,
            {
                'code': 'INVALID_FEEDBACK_DATE_FILTER',
                'message': f'{field} must be an ISO-8601 date such as 2026-06-01.',
            },
        )
    return text


def _narrative(value: Any, *, field: str) -> str | None:
    """One bounded, secret-scanned free-text answer, or None when left blank."""
    text = str(value or '').strip()
    if not text:
        return None
    if len(text) > FEEDBACK_MAX_MESSAGE_CHARS:
        raise _http_error(
            400,
            {
                'code': 'FEEDBACK_MESSAGE_TOO_LONG',
                'message': f'{field} must be {FEEDBACK_MAX_MESSAGE_CHARS} characters or fewer.',
            },
        )
    if looks_like_secret(text):
        raise _http_error(400, _FEEDBACK_SECRET_DETAIL)
    return text


_FEEDBACK_SECRET_DETAIL = {
    'code': 'FEEDBACK_CONTAINS_SECRET',
    'message': (
        'This message looks like it contains a private key, seed phrase, or other '
        'credential, so it was not submitted. Remove the secret and try again.'
    ),
}


def record_feedback(
    connection: Any,
    *,
    organization_id: str,
    workspace_id: str | None,
    user_id: str | None,
    feedback_type: str,
    message: str,
    context: Mapping[str, Any] | None = None,
    feedback_mode: str = FEEDBACK_MODE_QUICK,
    severity: Any = None,
    production_blocker: Any = None,
    continue_intent: Any = None,
    contact_permission: Any = False,
    pilot_day: int | None = None,
    narratives: Mapping[str, Any] | None = None,
    detail_schema_ready: bool | None = None,
) -> dict[str, Any]:
    """Store one pilot feedback row. Internal-visibility only.

    ``feedback_mode`` selects the depth; everything past it is optional and is
    written only when migration 0153's columns are present. A deployment without
    them still accepts quick feedback unchanged, and refuses the deeper forms
    outright rather than accepting answers it would drop on the floor.
    """
    kind = str(feedback_type or '').strip().lower()
    if kind not in FEEDBACK_TYPES:
        raise _http_error(
            400,
            {'code': 'INVALID_FEEDBACK_TYPE', 'message': f'feedback_type must be one of {", ".join(FEEDBACK_TYPES)}.'},
        )
    mode = str(feedback_mode or FEEDBACK_MODE_QUICK).strip().lower()
    if mode not in FEEDBACK_MODES:
        raise _http_error(
            400,
            {'code': 'INVALID_FEEDBACK_MODE', 'message': f'feedback_mode must be one of {", ".join(FEEDBACK_MODES)}.'},
        )
    body = str(message or '').strip()
    if not body:
        raise _http_error(400, {'code': 'FEEDBACK_MESSAGE_REQUIRED', 'message': 'message is required.'})
    if len(body) > FEEDBACK_MAX_MESSAGE_CHARS:
        raise _http_error(
            400,
            {
                'code': 'FEEDBACK_MESSAGE_TOO_LONG',
                'message': f'message must be {FEEDBACK_MAX_MESSAGE_CHARS} characters or fewer.',
            },
        )
    if looks_like_secret(body):
        raise _http_error(400, _FEEDBACK_SECRET_DETAIL)

    severity_value = _choice(
        severity, FEEDBACK_SEVERITIES, field='severity', code='INVALID_FEEDBACK_SEVERITY',
    )
    blocker_value = _choice(
        production_blocker, FEEDBACK_PRODUCTION_BLOCKERS,
        field='production_blocker', code='INVALID_PRODUCTION_BLOCKER',
    )
    continue_value = _choice(
        continue_intent, FEEDBACK_CONTINUE_INTENTS,
        field='continue_intent', code='INVALID_CONTINUE_INTENT',
    )
    supplied = narratives if isinstance(narratives, Mapping) else {}
    answers = {
        field: _narrative(supplied.get(field), field=field) for field in FEEDBACK_NARRATIVE_FIELDS
    }
    contact_ok = _permission(contact_permission)

    detail_ready = (
        feedback_detail_schema_ready(connection) if detail_schema_ready is None else bool(detail_schema_ready)
    )
    if not detail_ready:
        # Fail closed, and say why. The alternative — accepting the submission and
        # writing only the columns that exist — would tell the customer their
        # answers were recorded while silently discarding most of them.
        if (
            mode != FEEDBACK_MODE_QUICK
            or severity_value
            or blocker_value
            or continue_value
            or contact_ok
            or any(answers.values())
        ):
            raise _http_error(
                503,
                {
                    'code': 'FEEDBACK_DETAIL_UNAVAILABLE',
                    'message': (
                        'Detailed Pilot feedback is unavailable until this deployment finishes '
                        'migrating. Quick feedback still works.'
                    ),
                },
            )

    feedback_id = str(uuid.uuid4())
    stored_context = sanitize_feedback_context(context)
    if detail_ready:
        connection.execute(
            '''
            INSERT INTO organization_feedback (
                id, organization_id, workspace_id, user_id, feedback_type, message, context, created_at,
                feedback_mode, severity, goal_or_task, security_problem, current_workaround,
                where_decoda_helped, missing_or_difficult, production_blocker, deployment_requirement,
                contact_permission, continue_intent, paid_capability, pilot_day
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, NOW(),
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ''',
            (
                feedback_id,
                str(organization_id),
                str(workspace_id) if workspace_id else None,
                str(user_id) if user_id else None,
                kind,
                body,
                _json_dumps(stored_context),
                mode,
                severity_value,
                answers['goal_or_task'],
                answers['security_problem'],
                answers['current_workaround'],
                answers['where_decoda_helped'],
                answers['missing_or_difficult'],
                blocker_value,
                answers['deployment_requirement'],
                contact_ok,
                continue_value,
                answers['paid_capability'],
                int(pilot_day) if pilot_day is not None else None,
            ),
        )
    else:
        connection.execute(
            '''
            INSERT INTO organization_feedback (
                id, organization_id, workspace_id, user_id, feedback_type, message, context, created_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, NOW())
            ''',
            (
                feedback_id,
                str(organization_id),
                str(workspace_id) if workspace_id else None,
                str(user_id) if user_id else None,
                kind,
                body,
                _json_dumps(stored_context),
            ),
        )
    return {'id': feedback_id, 'feedback_type': kind, 'feedback_mode': mode}


_FEEDBACK_CONTEXT_KEYS = ('page', 'incident_id', 'alert_id', 'asset_id')

#: The contextual ids the form may carry, and the workspace-scoped table each one
#: must be found in before it is stored.
_FEEDBACK_CONTEXT_ENTITIES: tuple[tuple[str, str], ...] = (
    ('incident_id', 'incidents'),
    ('alert_id', 'alerts'),
    ('asset_id', 'assets'),
)


def sanitize_feedback_context(context: Mapping[str, Any] | None) -> dict[str, Any]:
    """Keep only the small allowlist of context fields, as bounded strings."""
    if not isinstance(context, Mapping):
        return {}
    cleaned: dict[str, Any] = {}
    for key in _FEEDBACK_CONTEXT_KEYS:
        value = context.get(key)
        if value in (None, ''):
            continue
        cleaned[key] = str(value)[:200]
    return cleaned


def resolve_feedback_context(
    connection: Any, context: Mapping[str, Any] | None, *, organization_id: str,
) -> dict[str, Any]:
    """The allowlisted context, with every entity id VERIFIED against this tenant.

    An incident/alert/asset id is stored only when it is found in a workspace
    this organization owns. Anything else — another tenant's id, a stale id, a
    malformed one — is DROPPED rather than recorded: an unverifiable id rendered
    next to a customer's words in the founder console would read as evidence that
    this feedback is about that incident, which nothing established.

    ``page`` is not an entity reference and is kept as the bounded string it is.
    """
    cleaned = sanitize_feedback_context(context)
    for key, table in _FEEDBACK_CONTEXT_ENTITIES:
        candidate = cleaned.get(key)
        if not candidate:
            continue
        if not _belongs_to_organization(connection, table, candidate, organization_id):
            logger.info(
                'feedback_context_id_dropped field=%s organization_id=%s reason=not_in_tenant',
                key, organization_id,
            )
            cleaned.pop(key, None)
    return cleaned


def _belongs_to_organization(connection: Any, table: str, entity_id: str, organization_id: str) -> bool:
    """Whether one workspace-scoped row is owned by this organization.

    ``table`` is never caller-supplied — it comes from the module-level
    ``_FEEDBACK_CONTEXT_ENTITIES`` tuple — and the ids are bound as parameters.
    A probe that raises (bad uuid text, a table this deployment has not migrated)
    answers False, so the id is dropped: fail closed.
    """
    try:
        row = connection.execute(
            f'''
            SELECT 1 AS found
              FROM {table} e
              JOIN workspaces w ON w.id = e.workspace_id
             WHERE e.id = %s AND w.organization_id = %s
             LIMIT 1
            ''',
            (str(entity_id), str(organization_id)),
        ).fetchone()
    except Exception:
        logger.warning('feedback_context_probe_failed table=%s result=dropped', table, exc_info=True)
        return False
    return bool(_row_dict(row))


def pilot_day_for(organization: Mapping[str, Any] | None, *, now: datetime | None = None) -> int | None:
    """Which day of the evaluation this is, 1-based. ``None`` when there is none.

    Stored ON the row rather than computed at read time: "they said this on day
    3" is a fact about when it was said, and re-deriving it later from a window
    that may since have been extended would quietly restate it.
    """
    org = organization or {}
    started_at = org.get('evaluation_started_at')
    if started_at is None:
        return None
    if not hasattr(started_at, 'tzinfo'):
        return None
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    delta = (now or _utc_now()) - started_at
    if delta.total_seconds() < 0:
        return None
    return int(delta.total_seconds() // 86400) + 1


# ── founder admin reads ──────────────────────────────────────────────────────
#: Membership roles that outrank plain membership when the founder console picks
#: ONE contact per organization, best first. Every other role in
#: ``ORGANIZATION_ROLES`` ranks equal and is separated by join order alone, so
#: 'analyst' never silently outranks 'viewer'.
PRIMARY_CONTACT_ROLE_PRIORITY: tuple[str, ...] = ('owner', 'admin')

_PRIMARY_CONTACT_ROLE_RANK = ' '.join(
    f"WHEN '{role}' THEN {rank}" for rank, role in enumerate(PRIMARY_CONTACT_ROLE_PRIORITY)
)

#: One correlated subquery evaluated inside the single listing query — NOT a
#: second round trip per tenant. idx_organization_memberships_org_created narrows
#: it to one organization's handful of rows before the ranking below sorts them.
#: It is derived from PRIMARY_CONTACT_ROLE_PRIORITY so the rule has one home.
#:
#: The tie-break (membership created_at, then user_id) is what makes the choice
#: DETERMINISTIC. Two owners enrolled in the same transaction must resolve to the
#: same address on every refresh; a column that named a different person each
#: time it was read would be worse than no column at all.
_PRIMARY_CONTACT_EMAIL_SQL = f"""(
                   SELECT u.email FROM organization_memberships m
                     JOIN users u ON u.id = m.user_id
                    WHERE m.organization_id = o.id
                    ORDER BY CASE m.role {_PRIMARY_CONTACT_ROLE_RANK}
                                  ELSE {len(PRIMARY_CONTACT_ROLE_PRIORITY)} END,
                             m.created_at ASC, m.user_id ASC
                    LIMIT 1
               )"""


def list_customer_organizations(
    connection: Any, *, limit: int = 100, offset: int = 0, now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Every organization with its plan, lifecycle, usage, last activity, contact.

    One query with correlated aggregates rather than N+1 per-organization counts,
    because the console lists every tenant at once. The primary contact is chosen
    by the same query for the same reason — see ``_PRIMARY_CONTACT_EMAIL_SQL``.

    Cross-organization by design: this is the internal founder read, and every
    caller of it authorizes internal staff first. It is reached from no
    customer-facing endpoint.
    """
    bounded_limit = max(1, min(int(limit or 100), 500))
    bounded_offset = max(0, int(offset or 0))
    rows = connection.execute(
        f'''
        SELECT o.id, o.name, o.slug, o.plan, o.status,
               o.evaluation_started_at, o.evaluation_expires_at, o.entitlement_overrides,
               o.created_at, o.updated_at,
               {_PRIMARY_CONTACT_EMAIL_SQL} AS primary_contact_email,
               (SELECT COUNT(*) FROM workspaces w WHERE w.organization_id = o.id) AS workspace_count,
               (SELECT COUNT(*) FROM organization_memberships m WHERE m.organization_id = o.id) AS member_count,
               (SELECT COUNT(*) FROM assets a JOIN workspaces w ON w.id = a.workspace_id
                 WHERE w.organization_id = o.id AND a.deleted_at IS NULL) AS contract_count,
               (SELECT COUNT(*) FROM targets t JOIN workspaces w ON w.id = t.workspace_id
                 WHERE w.organization_id = o.id AND t.deleted_at IS NULL) AS target_count,
               (SELECT COUNT(*) FROM export_jobs e JOIN workspaces w ON w.id = e.workspace_id
                 WHERE w.organization_id = o.id AND e.export_type = 'proof_bundle') AS evidence_count,
               (SELECT MAX(al.created_at) FROM audit_logs al JOIN workspaces w ON w.id = al.workspace_id
                 WHERE w.organization_id = o.id) AS last_activity_at,
               (SELECT COUNT(*) FROM organization_feedback f WHERE f.organization_id = o.id) AS feedback_count
        FROM organizations o
        ORDER BY o.created_at DESC
        LIMIT %s OFFSET %s
        ''',
        (bounded_limit, bounded_offset),
    ).fetchall()
    return [_customer_row(dict(row), now=now) for row in (rows or [])]


def _customer_row(row: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    entitlements = ent.get_entitlements(row)
    return {
        'id': str(row['id']),
        'name': row.get('name'),
        'slug': row.get('slug'),
        'plan': ent.normalize_plan(row.get('plan')),
        'status': ent.normalize_status(row.get('status')),
        'lifecycle_state': ent.lifecycle_state(row, now=now),
        'evaluation': ent.evaluation_payload(row, now=now),
        'created_at': _iso(row.get('created_at')),
        'last_activity_at': _iso(row.get('last_activity_at')),
        # The ONLY personal field on this row, and only ever an address: it tells
        # internal staff which human to contact about a tenant. No credential,
        # session, or authentication column is read by the query above.
        'primary_contact_email': _email_or_none(row.get('primary_contact_email')),
        'members': int(row.get('member_count') or 0),
        'feedback_count': int(row.get('feedback_count') or 0),
        'usage': {
            'workspaces': ent.usage_entry(
                int(row.get('workspace_count') or 0), ent.limit_for(entitlements, ent.LIMIT_WORKSPACES),
            ),
            'monitored_contracts': ent.usage_entry(
                int(row.get('contract_count') or 0), ent.limit_for(entitlements, ent.LIMIT_MONITORED_CONTRACTS),
            ),
            'monitoring_targets': ent.usage_entry(
                int(row.get('target_count') or 0), ent.limit_for(entitlements, ent.LIMIT_MONITORING_TARGETS),
            ),
            'evidence_packages': ent.usage_entry(
                int(row.get('evidence_count') or 0), ent.limit_for(entitlements, ent.LIMIT_EVIDENCE_PACKAGES),
            ),
        },
    }


#: Columns the founder console reads for every row. Selected explicitly (never
#: ``SELECT *``) so a column added later cannot reach the internal API by
#: accident — the same reason the customer-facing reads name their columns.
_FEEDBACK_BASE_COLUMNS = (
    'f.id, f.organization_id, f.workspace_id, f.user_id, f.feedback_type, '
    'f.message, f.context, f.created_at'
)

_FEEDBACK_DETAIL_COLUMNS_SQL = (
    'f.feedback_mode, f.severity, f.goal_or_task, f.security_problem, f.current_workaround, '
    'f.where_decoda_helped, f.missing_or_difficult, f.production_blocker, '
    'f.deployment_requirement, f.contact_permission, f.continue_intent, '
    'f.paid_capability, f.pilot_day'
)


def list_feedback(
    connection: Any,
    *,
    organization_id: str | None = None,
    limit: int = 100,
    feedback_type: str | None = None,
    severity: str | None = None,
    production_blocker: str | None = None,
    feedback_mode: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> list[dict[str, Any]]:
    """Feedback rows for the internal console, newest first.

    Every filter is OPTIONAL and applied in SQL against a bound parameter. An
    unrecognised filter value is refused by ``_choice`` rather than ignored: a
    console that silently drops a filter shows the founder a list they believe is
    narrowed and is not.

    Cross-organization when ``organization_id`` is None — this is the internal
    founder read, and every caller authorizes internal staff before reaching it.
    """
    bounded = max(1, min(int(limit or 100), 500))
    detail_ready = feedback_detail_schema_ready(connection)
    columns = _FEEDBACK_BASE_COLUMNS + (f', {_FEEDBACK_DETAIL_COLUMNS_SQL}' if detail_ready else '')

    clauses: list[str] = []
    params: list[Any] = []
    if organization_id:
        clauses.append('f.organization_id = %s')
        params.append(str(organization_id))
    kind = _choice(feedback_type, FEEDBACK_TYPES, field='feedback_type', code='INVALID_FEEDBACK_TYPE')
    if kind:
        clauses.append('f.feedback_type = %s')
        params.append(kind)
    if detail_ready:
        level = _choice(severity, FEEDBACK_SEVERITIES, field='severity', code='INVALID_FEEDBACK_SEVERITY')
        if level:
            clauses.append('f.severity = %s')
            params.append(level)
        blocker = _choice(
            production_blocker, FEEDBACK_PRODUCTION_BLOCKERS,
            field='production_blocker', code='INVALID_PRODUCTION_BLOCKER',
        )
        if blocker:
            clauses.append('f.production_blocker = %s')
            params.append(blocker)
        mode = _choice(feedback_mode, FEEDBACK_MODES, field='feedback_mode', code='INVALID_FEEDBACK_MODE')
        if mode:
            clauses.append('f.feedback_mode = %s')
            params.append(mode)
    since_value = _date_filter(since, field='since')
    if since_value:
        clauses.append('f.created_at >= %s')
        params.append(since_value)
    until_value = _date_filter(until, field='until')
    if until_value:
        clauses.append('f.created_at <= %s')
        params.append(until_value)

    where = f' WHERE {" AND ".join(clauses)}' if clauses else ''
    params.append(bounded)
    rows = connection.execute(
        f'''
        SELECT {columns}, o.name AS organization_name, o.plan AS organization_plan,
               u.email AS user_email
        FROM organization_feedback f
        JOIN organizations o ON o.id = f.organization_id
        LEFT JOIN users u ON u.id = f.user_id{where}
        ORDER BY f.created_at DESC
        LIMIT %s
        ''',
        tuple(params),
    ).fetchall()
    return [_feedback_row(dict(row), detail_ready=detail_ready) for row in (rows or [])]


def _feedback_row(item: dict[str, Any], *, detail_ready: bool) -> dict[str, Any]:
    """One wire row.

    Before migration 0153 the discovery fields are reported as None rather than
    as an empty answer: "this deployment cannot store that yet" is not "the
    customer left it blank", and the console renders the two differently.
    """
    row: dict[str, Any] = {
        'id': str(item['id']),
        'organization_id': str(item['organization_id']),
        'organization_name': item.get('organization_name'),
        'organization_plan': item.get('organization_plan'),
        'workspace_id': str(item['workspace_id']) if item.get('workspace_id') else None,
        'user_email': item.get('user_email'),
        'feedback_type': item.get('feedback_type'),
        'message': item.get('message'),
        'context': item.get('context') or {},
        'created_at': _iso(item.get('created_at')),
        'detail_available': detail_ready,
    }
    row['feedback_mode'] = item.get('feedback_mode') if detail_ready else None
    row['severity'] = item.get('severity') if detail_ready else None
    row['production_blocker'] = item.get('production_blocker') if detail_ready else None
    row['continue_intent'] = item.get('continue_intent') if detail_ready else None
    row['contact_permission'] = bool(item.get('contact_permission')) if detail_ready else None
    row['pilot_day'] = (
        int(item['pilot_day']) if detail_ready and item.get('pilot_day') is not None else None
    )
    for field in FEEDBACK_NARRATIVE_FIELDS:
        row[field] = item.get(field) if detail_ready else None
    return row


#: The founder's four standing roadmap questions, each a labelled predicate over
#: the same rows the listing returns. Counted in ONE pass over the filtered list
#: rather than by four more queries — the console has the rows in hand, and a
#: separate query could report a total the visible list does not support.
def feedback_summary(items: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Repeated-pain-point counters over the rows the console is showing."""
    rows = list(items)
    return {
        'total': len(rows),
        'production_blockers': sum(1 for row in rows if row.get('production_blocker') == 'yes'),
        'high_or_critical': sum(1 for row in rows if row.get('severity') in ('critical', 'high')),
        'missing_capability': sum(1 for row in rows if row.get('feedback_type') == 'missing_feature'),
        'detection_issues': sum(
            1 for row in rows
            if row.get('feedback_type') in ('detection_accuracy', 'false_positive', 'missed_detection')
        ),
    }


def organization_members(connection: Any, organization_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        '''
        SELECT m.user_id, m.role, m.created_at, u.email, u.full_name, u.last_sign_in_at
        FROM organization_memberships m
        JOIN users u ON u.id = m.user_id
        WHERE m.organization_id = %s
        ORDER BY m.created_at ASC
        ''',
        (str(organization_id),),
    ).fetchall()
    return [
        {
            'user_id': str(item['user_id']),
            'email': item.get('email'),
            'full_name': item.get('full_name'),
            'role': item.get('role'),
            'joined_at': _iso(item.get('created_at')),
            'last_sign_in_at': _iso(item.get('last_sign_in_at')),
        }
        for item in (dict(row) for row in (rows or []))
    ]


def organization_workspaces(connection: Any, organization_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        '''
        SELECT id, name, slug, created_at
        FROM workspaces
        WHERE organization_id = %s
        ORDER BY created_at ASC
        ''',
        (str(organization_id),),
    ).fetchall()
    return [
        {
            'id': str(item['id']),
            'name': item.get('name'),
            'slug': item.get('slug'),
            'created_at': _iso(item.get('created_at')),
        }
        for item in (dict(row) for row in (rows or []))
    ]


# ── errors ───────────────────────────────────────────────────────────────────
def _http_error(status_code: int, detail: dict[str, Any]) -> Exception:
    if HTTPException is None:  # pragma: no cover - only without fastapi installed
        raise RuntimeError('fastapi is required to raise organization errors')
    return HTTPException(status_code=status_code, detail=detail)
