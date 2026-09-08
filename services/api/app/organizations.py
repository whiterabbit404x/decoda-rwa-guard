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

FEEDBACK_TYPES: tuple[str, ...] = (
    'security',
    'detection_accuracy',
    'usability',
    'missing_feature',
    'integration',
    'other',
)

FEEDBACK_MAX_MESSAGE_CHARS = 4000

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
        'entitlements': ent.get_entitlements(organization),
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


def record_feedback(
    connection: Any,
    *,
    organization_id: str,
    workspace_id: str | None,
    user_id: str | None,
    feedback_type: str,
    message: str,
    context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Store one pilot feedback row. Internal-visibility only."""
    kind = str(feedback_type or '').strip().lower()
    if kind not in FEEDBACK_TYPES:
        raise _http_error(
            400,
            {'code': 'INVALID_FEEDBACK_TYPE', 'message': f'feedback_type must be one of {", ".join(FEEDBACK_TYPES)}.'},
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
        raise _http_error(
            400,
            {
                'code': 'FEEDBACK_CONTAINS_SECRET',
                'message': (
                    'This message looks like it contains a private key, seed phrase, or other '
                    'credential, so it was not submitted. Remove the secret and try again.'
                ),
            },
        )
    feedback_id = str(uuid.uuid4())
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
            _json_dumps(sanitize_feedback_context(context)),
        ),
    )
    return {'id': feedback_id, 'feedback_type': kind}


_FEEDBACK_CONTEXT_KEYS = ('page', 'incident_id', 'alert_id')


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


def list_feedback(
    connection: Any, *, organization_id: str | None = None, limit: int = 100,
) -> list[dict[str, Any]]:
    """Feedback rows for the internal console, newest first."""
    bounded = max(1, min(int(limit or 100), 500))
    if organization_id:
        rows = connection.execute(
            '''
            SELECT f.id, f.organization_id, f.workspace_id, f.user_id, f.feedback_type,
                   f.message, f.context, f.created_at, o.name AS organization_name, u.email AS user_email
            FROM organization_feedback f
            JOIN organizations o ON o.id = f.organization_id
            LEFT JOIN users u ON u.id = f.user_id
            WHERE f.organization_id = %s
            ORDER BY f.created_at DESC
            LIMIT %s
            ''',
            (str(organization_id), bounded),
        ).fetchall()
    else:
        rows = connection.execute(
            '''
            SELECT f.id, f.organization_id, f.workspace_id, f.user_id, f.feedback_type,
                   f.message, f.context, f.created_at, o.name AS organization_name, u.email AS user_email
            FROM organization_feedback f
            JOIN organizations o ON o.id = f.organization_id
            LEFT JOIN users u ON u.id = f.user_id
            ORDER BY f.created_at DESC
            LIMIT %s
            ''',
            (bounded,),
        ).fetchall()
    return [
        {
            'id': str(item['id']),
            'organization_id': str(item['organization_id']),
            'organization_name': item.get('organization_name'),
            'workspace_id': str(item['workspace_id']) if item.get('workspace_id') else None,
            'user_email': item.get('user_email'),
            'feedback_type': item.get('feedback_type'),
            'message': item.get('message'),
            'context': item.get('context') or {},
            'created_at': _iso(item.get('created_at')),
        }
        for item in (dict(row) for row in (rows or []))
    ]


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
