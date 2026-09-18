"""Attributable, auditable Decoda staff access to customer information.

Why this module exists
----------------------
Internal staff (``users.is_internal_admin`` or the exact-address deployment
allowlist) can read and change tenant-level facts about a customer through the
founder console. Before this module the WRITES were audited and the READS were
not, and every staff row was stored with ``workspace_id = NULL`` so the customer
could never see that it happened.

One chokepoint, two rows
------------------------
:func:`record_staff_access` is the single place that records staff access. Every
staff surface calls it AFTER ``require_internal_admin`` has already succeeded, so
a refused or anonymous request can never produce an access record. It writes:

  1. the **internal record** — ``workspace_id = NULL``, ``user_id`` = the staff
     account, plus the staff source IP and request id that ``pilot.log_audit``
     captures. This is the Decoda-internal answer to WHO/WHAT/WHEN/WHY.

  2. the **customer mirror** — one row per workspace of the organization that was
     accessed, written with ``user_id = NULL`` and no request (so no staff IP is
     stored against the customer's chain) and metadata built from a CLOSED
     allowlist of scalars. This is what the customer sees in their own workspace
     audit history.

Both rows go through ``pilot.log_audit``, so both are hash-chained, sealed, and
append-only on exactly the infrastructure the rest of the product already uses.
The mirror is a real member of the workspace's own chain, which is why it is a
separate row rather than a query-time widening of the customer's audit view: a
row belonging to the ``NULL`` chain, rendered inside a workspace, would break
that workspace's offline chain verification and would escape workspace retention.

What the customer mirror deliberately never carries
---------------------------------------------------
The staff member's email, name, or user id; the staff source IP; internal notes;
another tenant's identifiers; any request body; any credential. The mirror's
metadata is assembled here from named arguments only — no caller can widen it by
passing a dict through, because there is no such parameter.

What is NOT mirrored, and why
-----------------------------
Cross-tenant reads (the customer directory, the unfiltered feedback roadmap view,
the Pilot request queue) produce ONE internal record carrying a result count.
They are not mirrored to any customer: a directory read is not an access to one
customer's information, and fanning one request out to every tenant's audit
history would be an audit-event explosion that buries the events that do matter.
:data:`CUSTOMER_VISIBLE_ACTIONS` is the exact, testable list of what a customer
can see.

Fail-closed
-----------
If the access cannot be recorded, the access does not happen: this module raises
and the endpoint fails rather than serving customer information off the record.
"""

from __future__ import annotations

import logging
from typing import Any, Mapping, Sequence

logger = logging.getLogger(__name__)


# ── actor vocabulary (Phase 7 wording) ───────────────────────────────────────
#: A member of the customer's own workspace.
ACTOR_TYPE_WORKSPACE_MEMBER = 'workspace_member'
#: Authorized Decoda personnel acting through the internal console.
ACTOR_TYPE_DECODA_STAFF = 'decoda_staff'
#: A Decoda worker or scheduled job that names itself as one.
ACTOR_TYPE_AUTOMATED_SERVICE = 'automated_service'
#: No human and no self-identified service — the platform itself.
ACTOR_TYPE_SYSTEM = 'system'

ACTOR_TYPES: tuple[str, ...] = (
    ACTOR_TYPE_WORKSPACE_MEMBER,
    ACTOR_TYPE_DECODA_STAFF,
    ACTOR_TYPE_AUTOMATED_SERVICE,
    ACTOR_TYPE_SYSTEM,
)

#: What the customer sees instead of an internal employee address. Product policy
#: does not publish internal staff identities to customers; the identity is held
#: in the internal record, which is where an investigation reads it from.
STAFF_ACTOR_DISPLAY = 'Decoda staff'

ACTOR_TYPE_LABELS: dict[str, str] = {
    ACTOR_TYPE_WORKSPACE_MEMBER: 'Workspace member',
    ACTOR_TYPE_DECODA_STAFF: STAFF_ACTOR_DISPLAY,
    ACTOR_TYPE_AUTOMATED_SERVICE: 'Automated service',
    ACTOR_TYPE_SYSTEM: 'System',
}

# ── access mode ──────────────────────────────────────────────────────────────
ACCESS_READ = 'read'
ACCESS_WRITE = 'write'
ACCESS_MODES: tuple[str, ...] = (ACCESS_READ, ACCESS_WRITE)

ACCESS_MODE_LABELS: dict[str, str] = {
    ACCESS_READ: 'Read only',
    ACCESS_WRITE: 'Change',
}


# ── event vocabulary ─────────────────────────────────────────────────────────
# One entry per capability that actually exists on this repository's staff
# surface. Nothing here is aspirational: an action that names a route Decoda does
# not have would be a claim the audit trail cannot keep.
ACTION_CUSTOMER_LIST_VIEWED = 'staff.customer_list_viewed'
ACTION_CUSTOMER_DETAIL_VIEWED = 'staff.customer_detail_viewed'
ACTION_FEEDBACK_VIEWED = 'staff.feedback_viewed'
ACTION_PILOT_REQUEST_QUEUE_VIEWED = 'staff.pilot_request_queue_viewed'
ACTION_PLAN_CHANGED = 'staff.plan_changed'
ACTION_STATUS_CHANGED = 'staff.status_changed'
ACTION_PILOT_DEADLINE_CHANGED = 'staff.pilot_deadline_changed'

STAFF_ACTIONS: tuple[str, ...] = (
    ACTION_CUSTOMER_LIST_VIEWED,
    ACTION_CUSTOMER_DETAIL_VIEWED,
    ACTION_FEEDBACK_VIEWED,
    ACTION_PILOT_REQUEST_QUEUE_VIEWED,
    ACTION_PLAN_CHANGED,
    ACTION_STATUS_CHANGED,
    ACTION_PILOT_DEADLINE_CHANGED,
)

#: The staff events a customer can see in their own workspace audit history.
#: Everything else is a cross-tenant or pre-tenant read that belongs to exactly
#: one customer's history no more than it belongs to another's.
CUSTOMER_VISIBLE_ACTIONS: frozenset[str] = frozenset({
    ACTION_CUSTOMER_DETAIL_VIEWED,
    ACTION_FEEDBACK_VIEWED,
    ACTION_PLAN_CHANGED,
    ACTION_STATUS_CHANGED,
    ACTION_PILOT_DEADLINE_CHANGED,
})

#: Plain-language sentences for the customer's audit view. Written for the person
#: reading their own history, and deliberately narrower than the internal record:
#: "support details" is what the console shows, not "everything about you".
CUSTOMER_ACTION_SUMMARIES: dict[str, str] = {
    ACTION_CUSTOMER_DETAIL_VIEWED: 'Decoda staff viewed organization support details',
    ACTION_FEEDBACK_VIEWED: 'Decoda staff viewed evaluation feedback submitted by this organization',
    ACTION_PLAN_CHANGED: 'Decoda staff changed the organization plan',
    ACTION_STATUS_CHANGED: 'Decoda staff changed the organization status',
    ACTION_PILOT_DEADLINE_CHANGED: 'Decoda staff changed the Pilot end date',
}


# ── access reasons (Phase 8) ─────────────────────────────────────────────────
REASON_SUPPORT_CASE = 'support_case'
REASON_CUSTOMER_REQUEST = 'customer_request'
REASON_SECURITY_INVESTIGATION = 'security_investigation'
REASON_BILLING_ACCOUNT = 'billing_account'
REASON_PILOT_MANAGEMENT = 'pilot_management'

STAFF_ACCESS_REASONS: tuple[str, ...] = (
    REASON_SUPPORT_CASE,
    REASON_CUSTOMER_REQUEST,
    REASON_SECURITY_INVESTIGATION,
    REASON_BILLING_ACCOUNT,
    REASON_PILOT_MANAGEMENT,
)

#: Header a staff client may send to state WHY. Coded, never free-form, so the
#: value that reaches the customer's audit history cannot carry a credential or
#: an internal note.
REASON_HEADER = 'x-decoda-access-reason'
#: Optional bounded free text for the INTERNAL record only (a ticket reference,
#: usually). Never mirrored to a customer.
REASON_NOTE_HEADER = 'x-decoda-access-note'
REASON_NOTE_MAX_CHARS = 200

CODE_INVALID_ACCESS_REASON = 'INVALID_STAFF_ACCESS_REASON'

#: No current operation grants exceptional access to tenant data — staff cannot
#: read telemetry, incidents, or evidence without ordinary workspace membership —
#: so no current operation REQUIRES a reason. Any future mechanism that does
#: grant exceptional tenant access belongs in this set, and the chokepoint will
#: then refuse to record the access without one.
REASON_REQUIRED_ACTIONS: frozenset[str] = frozenset()

#: A staff console can carry many workspaces for one tenant. The mirror is capped
#: so a single read cannot write an unbounded number of rows; when the cap bites,
#: the internal record says so rather than silently under-reporting.
MAX_MIRROR_WORKSPACES = 100

#: Shapes that must never reach an audit row. The metadata this module writes is
#: built from named scalars, so this is a belt-and-braces assertion rather than
#: the control itself.
_FORBIDDEN_METADATA_KEYS: frozenset[str] = frozenset({
    'password', 'password_hash', 'totp_secret', 'mfa_secret', 'recovery_code',
    'recovery_codes', 'private_key', 'seed_phrase', 'mnemonic', 'api_secret',
    'client_secret', 'bearer_token', 'token', 'access_token', 'refresh_token',
    'webhook_secret', 'signing_key', 'authorization', 'cookie', 'session_token',
    'evidence', 'evidence_payload', 'telemetry', 'telemetry_payload',
    'request_body', 'payload', 'internal_note',
})

class StaffAccessNotRecorded(RuntimeError):
    """The access could not be written to the audit trail, so it must not happen."""


def _http_error(status_code: int, detail: dict[str, Any]) -> Exception:
    from services.api.app import pilot  # local import: pilot imports organizations

    return pilot.HTTPException(status_code=status_code, detail=detail)


# ── request-supplied reason ──────────────────────────────────────────────────
def reason_from_request(request: Any) -> tuple[str | None, str | None]:
    """The coded reason and internal note this staff request declared, validated.

    Both are optional. A reason that is not a member of the published vocabulary
    is REFUSED with 400 rather than recorded as free text: a field that accepts
    anything documents nothing. A note that is over-long or shaped like a
    credential is refused for the same reason the feedback form refuses one.
    """
    headers = getattr(request, 'headers', None)
    if headers is None:
        return None, None
    raw_reason = str(headers.get(REASON_HEADER) or '').strip().lower()
    reason: str | None = None
    if raw_reason:
        if raw_reason not in STAFF_ACCESS_REASONS:
            raise _http_error(
                400,
                {
                    'code': CODE_INVALID_ACCESS_REASON,
                    'message': (
                        f'{REASON_HEADER} must be one of: {", ".join(STAFF_ACCESS_REASONS)}.'
                    ),
                },
            )
        reason = raw_reason
    raw_note = str(headers.get(REASON_NOTE_HEADER) or '').strip()
    note: str | None = None
    if raw_note:
        from services.api.app import organizations as org_service

        if len(raw_note) > REASON_NOTE_MAX_CHARS or org_service.looks_like_secret(raw_note):
            raise _http_error(
                400,
                {
                    'code': CODE_INVALID_ACCESS_REASON,
                    'message': (
                        f'{REASON_NOTE_HEADER} must be at most {REASON_NOTE_MAX_CHARS} '
                        'characters and must not contain a credential.'
                    ),
                },
            )
        note = raw_note
    return reason, note


# ── the chokepoint ───────────────────────────────────────────────────────────
def record_staff_access(
    connection: Any,
    *,
    actor_user_id: str,
    action: str,
    access_type: str,
    object_type: str,
    organization_id: str | None = None,
    workspace_id: str | None = None,
    object_id: str | None = None,
    reason: str | None = None,
    reason_note: str | None = None,
    request: Any = None,
    result_count: int | None = None,
    object_categories: Sequence[str] | None = None,
    change: Mapping[str, Any] | None = None,
    existing_internal_record_id: str | None = None,
) -> dict[str, Any]:
    """Record one Decoda staff access to customer information. Call AFTER authorization.

    ``actor_user_id`` is the authenticated staff account resolved server-side by
    ``organizations.require_internal_admin``; there is no parameter on any staff
    route through which a caller could name a different actor.

    ``change`` carries the before/after facts of a WRITE (plan, status, Pilot end
    date). Its values are stringified scalars and it is the only caller-shaped
    input that reaches the customer mirror — which is why the keys are checked
    against :data:`_FORBIDDEN_METADATA_KEYS` and the values are flattened to
    strings, so a dict of raw request state cannot travel through it.

    ``existing_internal_record_id`` is for the lifecycle WRITES that this product
    has always audited: their internal row is already written, with richer
    before/after detail than this module would invent, so passing its id records
    ONLY the customer mirror and links it to that row. Writing a second internal
    row for the same action would be duplication, not evidence.

    Returns ``{'record_id', 'customer_visible', 'mirrored_workspace_ids'}``.
    """
    from services.api.app import pilot  # local import: pilot imports organizations

    if action not in STAFF_ACTIONS:
        raise StaffAccessNotRecorded(f'unknown staff access action: {action!r}')
    if access_type not in ACCESS_MODES:
        raise StaffAccessNotRecorded(f'unknown staff access mode: {access_type!r}')
    actor = str(actor_user_id or '').strip()
    if not actor:
        raise StaffAccessNotRecorded('staff access requires a server-resolved actor')
    if reason is not None and reason not in STAFF_ACCESS_REASONS:
        raise StaffAccessNotRecorded(f'unknown staff access reason: {reason!r}')
    if action in REASON_REQUIRED_ACTIONS and not reason:
        raise StaffAccessNotRecorded(f'{action} requires a declared access reason')
    if existing_internal_record_id and action not in CUSTOMER_VISIBLE_ACTIONS:
        raise StaffAccessNotRecorded(
            f'{action} has no customer mirror, so it cannot reuse an internal record'
        )

    safe_change = _safe_change(change)
    categories = [str(item) for item in (object_categories or ()) if str(item).strip()]
    organization = str(organization_id).strip() if organization_id else None
    entity_id = str(object_id or organization or 'directory')

    request_id = None
    headers = getattr(request, 'headers', None)
    if headers is not None:
        request_id = str(headers.get('x-request-id') or '').strip() or None

    internal_metadata: dict[str, Any] = {
        'actor': STAFF_ACTOR_DISPLAY,
        'actor_type': ACTOR_TYPE_DECODA_STAFF,
        'actor_user_id': actor,
        'staff_event': action,
        'access_mode': access_type,
        'object_type': object_type,
        'organization_id': organization,
        'workspace_id': str(workspace_id) if workspace_id else None,
        'object_categories': categories,
        'reason': reason,
        'reason_supplied': bool(reason),
        'result': 'success',
        'customer_visible': action in CUSTOMER_VISIBLE_ACTIONS,
    }
    if result_count is not None:
        internal_metadata['result_count'] = int(result_count)
    if reason_note:
        # Internal only. Never copied into the mirror below.
        internal_metadata['reason_note'] = str(reason_note)[:REASON_NOTE_MAX_CHARS]
    if safe_change:
        internal_metadata['change'] = safe_change
    # Belt and braces: the keys above are fixed, so this only ever fires on a
    # future edit that adds one it should not.
    _assert_no_forbidden_keys(internal_metadata)

    if existing_internal_record_id:
        record_id: str | None = str(existing_internal_record_id)
    else:
        record_id = pilot.log_audit(
            connection,
            action=action,
            entity_type=object_type,
            entity_id=entity_id,
            request=request,
            user_id=actor,
            workspace_id=None,
            metadata=internal_metadata,
        )

    mirrored: list[str] = []
    if action in CUSTOMER_VISIBLE_ACTIONS and organization:
        mirrored = _mirror_to_customer(
            connection,
            action=action,
            access_type=access_type,
            object_type=object_type,
            organization_id=organization,
            workspace_id=str(workspace_id) if workspace_id else None,
            reason=reason,
            change=safe_change,
            correlation_id=request_id,
            staff_access_record_id=record_id,
        )

    logger.info(
        'staff_access_recorded action=%s access_mode=%s actor_user_id=%s '
        'organization_id=%s mirrored_workspaces=%d reason_supplied=%s',
        action, access_type, actor, organization, len(mirrored), bool(reason),
    )
    return {
        'record_id': record_id,
        'customer_visible': bool(mirrored),
        'mirrored_workspace_ids': mirrored,
    }


def _mirror_to_customer(
    connection: Any,
    *,
    action: str,
    access_type: str,
    object_type: str,
    organization_id: str,
    workspace_id: str | None,
    reason: str | None,
    change: dict[str, str],
    correlation_id: str | None,
    staff_access_record_id: str | None,
) -> list[str]:
    """Write the customer-safe row into each workspace of THIS organization.

    Tenant isolation is structural: the workspace list is read from
    ``workspaces.organization_id`` for the one organization that was accessed, so
    a mirror can only ever land in a workspace the accessed organization owns.
    Nothing here reads a workspace id from a request.
    """
    from services.api.app import pilot  # local import: pilot imports organizations

    workspace_ids = _organization_workspace_ids(connection, organization_id)
    if workspace_id:
        if workspace_id not in workspace_ids:
            # Fail closed rather than fanning out: a workspace that does not
            # belong to the accessed organization is a scope mistake, and
            # writing the row anywhere else would be a cross-tenant leak.
            raise StaffAccessNotRecorded(
                'workspace does not belong to the accessed organization'
            )
        workspace_ids = [workspace_id]
    truncated = len(workspace_ids) > MAX_MIRROR_WORKSPACES
    if truncated:
        logger.warning(
            'staff_access_mirror_truncated organization_id=%s workspaces=%d cap=%d',
            organization_id, len(workspace_ids), MAX_MIRROR_WORKSPACES,
        )
        workspace_ids = workspace_ids[:MAX_MIRROR_WORKSPACES]

    written: list[str] = []
    for target in workspace_ids:
        metadata: dict[str, Any] = {
            # Read by the customer audit view to render "Decoda staff" instead of
            # a bare user id — and there IS no user id on this row.
            'actor': STAFF_ACTOR_DISPLAY,
            'actor_type': ACTOR_TYPE_DECODA_STAFF,
            'access_mode': access_type,
            'staff_event': action,
            'summary': CUSTOMER_ACTION_SUMMARIES.get(action, STAFF_ACTOR_DISPLAY),
            'object_type': object_type,
            'organization_id': organization_id,
            'result': 'success',
            'reason': reason,
            # Opaque ids only: they let a customer question tie back to the
            # internal record without disclosing anything it contains.
            'correlation_id': correlation_id,
            'staff_access_record_id': staff_access_record_id,
        }
        if change:
            metadata['change'] = change
        _assert_no_forbidden_keys(metadata)
        pilot.log_audit(
            connection,
            action=action,
            entity_type=object_type,
            entity_id=organization_id,
            # No request: the staff member's IP address must not be written into
            # the customer's audit chain, and log_audit reads it from here.
            request=None,
            # No user id: the customer is not shown which employee it was, and a
            # NULL here also keeps the change log from resolving an internal
            # address out of the users table.
            user_id=None,
            workspace_id=target,
            metadata=metadata,
        )
        written.append(target)
    return written


def _organization_workspace_ids(connection: Any, organization_id: str) -> list[str]:
    rows = connection.execute(
        'SELECT id FROM workspaces WHERE organization_id = %s ORDER BY created_at ASC',
        (str(organization_id),),
    ).fetchall()
    return [str(dict(row)['id']) for row in (rows or [])]


def _safe_change(change: Mapping[str, Any] | None) -> dict[str, str]:
    """Flatten a before/after description to bounded strings under safe keys."""
    if not isinstance(change, Mapping):
        return {}
    safe: dict[str, str] = {}
    for key, value in change.items():
        name = str(key).strip().lower()
        if not name or name in _FORBIDDEN_METADATA_KEYS:
            raise StaffAccessNotRecorded(f'refused staff access metadata key: {name!r}')
        if value is None:
            safe[name] = ''
            continue
        if isinstance(value, (str, int, float, bool)):
            safe[name] = str(value)[:200]
            continue
        raise StaffAccessNotRecorded(f'staff access change values must be scalars: {name!r}')
    return safe


def _assert_no_forbidden_keys(metadata: Mapping[str, Any]) -> None:
    for key, value in metadata.items():
        if str(key).strip().lower() in _FORBIDDEN_METADATA_KEYS:
            raise StaffAccessNotRecorded(f'refused staff access metadata key: {key!r}')
        if isinstance(value, Mapping):
            _assert_no_forbidden_keys(value)


# ── reading the trail back ───────────────────────────────────────────────────
def audit_actor_type(metadata: Mapping[str, Any] | None, user_id: Any) -> str:
    """Which kind of actor an audit row belongs to.

    Stated by the writer where the writer knows (staff access, and any worker
    that names itself), and otherwise derived from the row: a row with a user id
    is a member of the workspace that owns the row, and a row without one is the
    platform. Never guesses "Decoda staff" — that label is only ever worn by a
    row this module wrote.
    """
    meta = metadata if isinstance(metadata, Mapping) else {}
    declared = str(meta.get('actor_type') or '').strip().lower()
    if declared in ACTOR_TYPES:
        return declared
    if user_id:
        return ACTOR_TYPE_WORKSPACE_MEMBER
    return ACTOR_TYPE_SYSTEM


def is_staff_event(metadata: Mapping[str, Any] | None, action: Any = None) -> bool:
    """Whether this audit row records Decoda staff access."""
    meta = metadata if isinstance(metadata, Mapping) else {}
    if str(meta.get('actor_type') or '') == ACTOR_TYPE_DECODA_STAFF:
        return True
    return str(action or '') in STAFF_ACTIONS


def describe_staff_event(metadata: Mapping[str, Any] | None, action: Any = None) -> str | None:
    """The customer-facing sentence for a staff row, or None for anything else."""
    meta = metadata if isinstance(metadata, Mapping) else {}
    if not is_staff_event(meta, action):
        return None
    summary = str(meta.get('summary') or '').strip()
    if summary:
        return summary
    return CUSTOMER_ACTION_SUMMARIES.get(str(meta.get('staff_event') or action or ''))
