"""Approval-only Pilot access: requests, review, invitations, and activation.

The rule this module exists to keep
-----------------------------------
Finding the product URL is not authorization to evaluate Decoda against live
assets. Authentication and Pilot access are two different facts:

    authenticated = true          proves someone controls an email address
    pilot_access  = true          proves Decoda internal staff approved them

Nothing here is granted by a request body. The applicant supplies contact and
context fields only; every decision — approval, plan, organization, role,
expiry — is server state written by an internal admin or derived from the one
configured default.

The state machine (deliberately small)
--------------------------------------
    pending    the public form was submitted; nothing was provisioned
    approved   internal staff approved it; an invitation token exists
    invited    the invitation email was delivered
    rejected   internal staff declined it; no invitation can ever be issued
    activated  the approved person accepted; their organization now exists
    expired    the invitation window closed before it was accepted

``approved`` and ``invited`` are kept apart so the console can say "Approved —
invitation not sent" truthfully when email delivery fails, instead of implying
the applicant received something they did not.

Invitation tokens
-----------------
Cryptographically random (``secrets.token_urlsafe(32)``), single-use,
time-limited, and stored ONLY as a keyed hash — the same construction
``auth_tokens`` uses, so there is one hashing story in the codebase. The
plaintext exists in the approval response and the invitation email; a database
dump cannot reconstruct it. A token is invalid after use, after expiry, after
rejection, and after the request leaves an invitable state.

Schema availability
-------------------
Every entry point is guarded by ``pilot_requests_schema_ready``. A deployment
whose API rolled out ahead of migration 0151 reports the condition rather than
inventing an answer: the public form returns 503 "not accepting requests yet"
and the admin console shows the same, which is truthful and fails closed.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from services.api.app import entitlements as ent
from services.api.app import organizations as organization_service

logger = logging.getLogger(__name__)


# ── statuses ─────────────────────────────────────────────────────────────────
STATUS_PENDING = 'pending'
STATUS_APPROVED = 'approved'
STATUS_INVITED = 'invited'
STATUS_REJECTED = 'rejected'
STATUS_ACTIVATED = 'activated'
STATUS_EXPIRED = 'expired'

PILOT_REQUEST_STATUSES: tuple[str, ...] = (
    STATUS_PENDING, STATUS_APPROVED, STATUS_INVITED, STATUS_REJECTED, STATUS_ACTIVATED, STATUS_EXPIRED,
)

#: A request in one of these states already occupies the applicant's address. A
#: second submission returns the existing row instead of creating a duplicate.
OPEN_STATUSES: tuple[str, ...] = (STATUS_PENDING, STATUS_APPROVED, STATUS_INVITED)

#: States from which an invitation may still be accepted.
INVITABLE_STATUSES: tuple[str, ...] = (STATUS_APPROVED, STATUS_INVITED)


# ── invitation lifetime ──────────────────────────────────────────────────────
INVITATION_TTL_HOURS_ENV = 'PILOT_INVITATION_TTL_HOURS'
DEFAULT_INVITATION_TTL_HOURS = 168  # 7 days
MIN_INVITATION_TTL_HOURS = 1
MAX_INVITATION_TTL_HOURS = 720  # 30 days


def invitation_ttl_hours() -> int:
    """Configured invitation lifetime in hours, clamped to a sane range."""
    raw = (os.getenv(INVITATION_TTL_HOURS_ENV) or '').strip()
    if not raw:
        return DEFAULT_INVITATION_TTL_HOURS
    try:
        parsed = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_INVITATION_TTL_HOURS
    return max(MIN_INVITATION_TTL_HOURS, min(MAX_INVITATION_TTL_HOURS, parsed))


# ── error codes ──────────────────────────────────────────────────────────────
CODE_PILOT_REQUESTS_UNAVAILABLE = 'PILOT_REQUESTS_UNAVAILABLE'
CODE_PILOT_REQUEST_INVALID = 'PILOT_REQUEST_INVALID'
CODE_PILOT_REQUEST_SECRET_DETECTED = 'PILOT_REQUEST_SECRET_DETECTED'
CODE_PILOT_REQUEST_NOT_FOUND = 'PILOT_REQUEST_NOT_FOUND'
CODE_PILOT_REQUEST_NOT_PENDING = 'PILOT_REQUEST_NOT_PENDING'
CODE_INVITATION_INVALID = 'PILOT_INVITATION_INVALID'
CODE_INVITATION_EXPIRED = 'PILOT_INVITATION_EXPIRED'
CODE_INVITATION_USED = 'PILOT_INVITATION_ALREADY_ACCEPTED'
CODE_INVITATION_EMAIL_MISMATCH = 'PILOT_INVITATION_EMAIL_MISMATCH'
CODE_PILOT_ACCESS_REQUIRED = 'PILOT_ACCESS_REQUIRED'


# ── field limits and vocabulary ──────────────────────────────────────────────
MAX_EMAIL_CHARS = 254
MAX_COMPANY_CHARS = 200
MAX_ROLE_CHARS = 120
MAX_WEBSITE_CHARS = 300
MAX_USE_CASE_CHARS = 2000
MAX_INTERNAL_NOTE_CHARS = 2000

#: Suggested use cases for the public form. The field is free text — an
#: applicant whose situation is not on the list must still be able to describe
#: it — so this list steers the UI, it does not constrain the value.
SUGGESTED_USE_CASES: tuple[str, ...] = (
    'tokenized treasury monitoring',
    'stablecoin / RWA operations',
    'tokenization platform',
    'custodian / issuer monitoring',
    'security evaluation',
    'other',
)

#: Rendered under the free-text fields on the public form and asserted by tests.
SECRET_WARNING_TEXT = (
    'Do not include private keys, credentials, seed phrases, or other secrets.'
)

_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')

PILOT_REQUEST_TABLES: tuple[str, ...] = ('pilot_requests',)

_REQUEST_COLUMNS = (
    'id, email, company_name, role, company_website, use_case, status, requested_at, '
    'reviewed_at, reviewed_by_user_id, approved_at, rejected_at, internal_note, '
    'invitation_token_hash, invitation_expires_at, invitation_sent_at, '
    'invitation_accepted_at, invitation_delivery_error, organization_id, created_at, updated_at'
)


# ── small helpers ────────────────────────────────────────────────────────────
def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _row_dict(row: Any) -> dict[str, Any] | None:
    return None if row is None else dict(row)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, 'isoformat') else str(value)


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _http_error(status_code: int, detail: dict[str, Any]) -> Exception:
    try:
        from fastapi import HTTPException
    except Exception:  # pragma: no cover - only without fastapi installed
        return RuntimeError(str(detail))
    return HTTPException(status_code=status_code, detail=detail)


def normalize_email(value: Any) -> str:
    """Trim + lower-case, the same normalisation the auth model uses.

    Comparison against an invitation is done on this normalised form, so
    ``Security@Company.com`` and ``security@company.com`` are one address — and
    ``attacker@gmail.com`` is not.
    """
    return str(value or '').strip().lower()


def token_hash(raw_token: str) -> str:
    """Keyed SHA-256 of an invitation token — the auth_tokens construction.

    Imported lazily so this module stays importable by tests and workers that do
    not load the API surface.
    """
    from services.api.app import pilot  # local import: pilot imports this module

    return pilot._auth_token_hash(str(raw_token))


# ── schema availability ──────────────────────────────────────────────────────
def pilot_requests_schema_ready(connection: Any) -> bool:
    """True only when migration 0151's table is definitively present.

    A failed probe is NOT ready: "we could not look" is not "it is there", and
    the surfaces that depend on it must refuse rather than guess.
    """
    try:
        row = _row_dict(
            connection.execute(
                '''
                SELECT COUNT(*) AS table_count
                FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = ANY(%s)
                ''',
                (list(PILOT_REQUEST_TABLES),),
            ).fetchone()
        )
    except Exception:
        logger.warning('pilot_requests_schema_probe_failed result=unknown', exc_info=True)
        return False
    return int((row or {}).get('table_count') or 0) >= len(PILOT_REQUEST_TABLES)


def require_schema(connection: Any) -> None:
    if pilot_requests_schema_ready(connection):
        return
    raise _http_error(
        503,
        {
            'code': CODE_PILOT_REQUESTS_UNAVAILABLE,
            'message': 'Pilot evaluation requests are not available on this deployment yet.',
        },
    )


# ── public submission ────────────────────────────────────────────────────────
def _clean_text(value: Any, *, field: str, max_chars: int, required: bool = True) -> str:
    text = ' '.join(str(value or '').split()) if value is not None else ''
    text = text.strip()
    if not text:
        if required:
            raise _http_error(
                400, {'code': CODE_PILOT_REQUEST_INVALID, 'message': f'{field} is required.'},
            )
        return ''
    if len(text) > max_chars:
        raise _http_error(
            400,
            {'code': CODE_PILOT_REQUEST_INVALID, 'message': f'{field} must be {max_chars} characters or fewer.'},
        )
    return text


def validate_submission(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalise and validate the public form. Returns only server-safe fields.

    Anything the body says beyond these five fields is dropped here — there is
    no path by which a submission can name an organization, a plan, a role in
    an organization, or an entitlement.
    """
    body = payload if isinstance(payload, Mapping) else {}
    email = normalize_email(body.get('email'))
    if not email or len(email) > MAX_EMAIL_CHARS or not _EMAIL_RE.fullmatch(email):
        raise _http_error(
            400, {'code': CODE_PILOT_REQUEST_INVALID, 'message': 'A valid work email address is required.'},
        )
    fields = {
        'email': email,
        'company_name': _clean_text(body.get('company_name'), field='company_name', max_chars=MAX_COMPANY_CHARS),
        'role': _clean_text(body.get('role'), field='role', max_chars=MAX_ROLE_CHARS),
        'company_website': _clean_text(
            body.get('company_website'), field='company_website', max_chars=MAX_WEBSITE_CHARS, required=False,
        ) or None,
        # Preserve the applicant's line breaks: a use case is prose, not a label.
        'use_case': _clean_use_case(body.get('use_case')),
    }
    # A credential must not reach the database at all, so there is nothing to
    # leak later from the internal console or an audit row. Refuse, don't redact.
    for key in ('company_name', 'role', 'company_website', 'use_case'):
        value = fields.get(key)
        if value and organization_service.looks_like_secret(value):
            raise _http_error(
                400,
                {
                    'code': CODE_PILOT_REQUEST_SECRET_DETECTED,
                    'message': (
                        'That looks like a private key, credential, or seed phrase. '
                        + SECRET_WARNING_TEXT
                    ),
                },
            )
    return fields


def _clean_use_case(value: Any) -> str:
    text = str(value or '').strip()
    if not text:
        raise _http_error(
            400, {'code': CODE_PILOT_REQUEST_INVALID, 'message': 'use_case is required.'},
        )
    if len(text) > MAX_USE_CASE_CHARS:
        raise _http_error(
            400,
            {
                'code': CODE_PILOT_REQUEST_INVALID,
                'message': f'use_case must be {MAX_USE_CASE_CHARS} characters or fewer.',
            },
        )
    return text


def get_request(connection: Any, request_id: str) -> dict[str, Any] | None:
    return _row_dict(
        connection.execute(
            f'SELECT {_REQUEST_COLUMNS} FROM pilot_requests WHERE id = %s', (str(request_id),),
        ).fetchone()
    )


def open_request_for_email(connection: Any, email: str) -> dict[str, Any] | None:
    """The applicant's current OPEN request, if any (pending/approved/invited)."""
    return _row_dict(
        connection.execute(
            f'''
            SELECT {_REQUEST_COLUMNS} FROM pilot_requests
            WHERE email = %s AND status = ANY(%s)
            ORDER BY requested_at DESC
            LIMIT 1
            ''',
            (normalize_email(email), list(OPEN_STATUSES)),
        ).fetchone()
    )


def submit_request(
    connection: Any, *, fields: Mapping[str, Any], source_ip: str | None = None,
) -> dict[str, Any]:
    """Create one pending Pilot request, or return the applicant's existing one.

    Provisions NOTHING: no organization, no workspace, no entitlement, no
    monitoring, no background job. A pending row is a queue entry for a human.

    Duplicate handling is idempotent by design. A second submission from an
    address that already has an open request returns ``duplicate=True`` and the
    existing row rather than stacking rows, so the form cannot be used to
    amplify mail or flood the review queue.
    """
    email = normalize_email(fields['email'])
    existing = open_request_for_email(connection, email)
    if existing is not None:
        return {'duplicate': True, 'request': existing}
    request_id = str(uuid.uuid4())
    connection.execute(
        '''
        INSERT INTO pilot_requests (
            id, email, company_name, role, company_website, use_case,
            status, requested_at, source_ip, created_at, updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, NOW(), %s, NOW(), NOW())
        ON CONFLICT DO NOTHING
        ''',
        (
            request_id,
            email,
            fields['company_name'],
            fields['role'],
            fields.get('company_website'),
            fields['use_case'],
            STATUS_PENDING,
            source_ip,
        ),
    )
    created = get_request(connection, request_id)
    if created is None:
        # The partial unique index refused a second open row for this address —
        # a concurrent duplicate. Report it as the duplicate it is.
        concurrent = open_request_for_email(connection, email)
        if concurrent is not None:
            return {'duplicate': True, 'request': concurrent}
        raise _http_error(
            500,
            {'code': CODE_PILOT_REQUEST_INVALID, 'message': 'The request could not be recorded. Please try again.'},
        )
    return {'duplicate': False, 'request': created}


# ── internal review ──────────────────────────────────────────────────────────
def list_requests(
    connection: Any, *, status: str | None = None, limit: int = 100, offset: int = 0,
) -> list[dict[str, Any]]:
    """Every Pilot request, newest first. Internal-admin surfaces only."""
    safe_limit = max(1, min(int(limit or 100), 500))
    safe_offset = max(0, int(offset or 0))
    status_filter = str(status or '').strip().lower() or None
    if status_filter and status_filter not in PILOT_REQUEST_STATUSES:
        raise _http_error(
            400, {'code': CODE_PILOT_REQUEST_INVALID, 'message': 'Unknown status filter.'},
        )
    rows = connection.execute(
        f'''
        SELECT {_REQUEST_COLUMNS}
        FROM pilot_requests
        WHERE (%s::text IS NULL OR status = %s)
        ORDER BY requested_at DESC
        LIMIT %s OFFSET %s
        ''',
        (status_filter, status_filter, safe_limit, safe_offset),
    ).fetchall()
    now = _utc_now()
    return [admin_view(dict(row), now=now) for row in rows]


def admin_view(row: Mapping[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    """One request as the internal console renders it.

    The invitation token hash is never included. ``invitation_expired`` is
    derived rather than stored, so a row whose window has closed cannot still
    look invitable in the console while the acceptance path refuses it.
    """
    moment = now or _utc_now()
    expires_at = _as_datetime(row.get('invitation_expires_at'))
    status_value = str(row.get('status') or '')
    invitation_expired = bool(
        expires_at is not None
        and expires_at <= moment
        and status_value in INVITABLE_STATUSES
    )
    return {
        'id': str(row.get('id')),
        'email': row.get('email'),
        'company_name': row.get('company_name'),
        'role': row.get('role'),
        'company_website': row.get('company_website'),
        'use_case': row.get('use_case'),
        'status': status_value,
        'requested_at': _iso(row.get('requested_at')),
        'reviewed_at': _iso(row.get('reviewed_at')),
        'approved_at': _iso(row.get('approved_at')),
        'rejected_at': _iso(row.get('rejected_at')),
        'internal_note': row.get('internal_note'),
        'invitation_expires_at': _iso(expires_at),
        'invitation_sent_at': _iso(row.get('invitation_sent_at')),
        'invitation_accepted_at': _iso(row.get('invitation_accepted_at')),
        'invitation_delivery_error': row.get('invitation_delivery_error'),
        'invitation_expired': invitation_expired,
        # True only when the approval succeeded and the email did not. The
        # console renders this as "Approved — invitation not sent" instead of
        # implying the applicant received a link.
        'invitation_not_sent': bool(status_value == STATUS_APPROVED and row.get('invitation_sent_at') is None),
        'organization_id': str(row['organization_id']) if row.get('organization_id') else None,
    }


def approve_request(
    connection: Any, *, request_id: str, reviewer_user_id: str, now: datetime | None = None,
) -> tuple[dict[str, Any], str]:
    """Approve a pending request and mint its single-use invitation.

    Returns ``(row, raw_token)``. The raw token is returned to the CALLER so it
    can be mailed; only its hash is written. Approving is not activation: no
    organization, workspace, or monitoring exists until the approved person
    accepts.
    """
    existing = get_request(connection, request_id)
    if existing is None:
        raise _http_error(
            404, {'code': CODE_PILOT_REQUEST_NOT_FOUND, 'message': 'Pilot request not found.'},
        )
    status_value = str(existing.get('status') or '')
    if status_value not in (STATUS_PENDING, STATUS_APPROVED, STATUS_INVITED, STATUS_EXPIRED):
        raise _http_error(
            409,
            {
                'code': CODE_PILOT_REQUEST_NOT_PENDING,
                'message': f'A {status_value} request cannot be approved.',
            },
        )
    moment = now or _utc_now()
    raw_token = secrets.token_urlsafe(32)
    expires_at = moment + timedelta(hours=invitation_ttl_hours())
    connection.execute(
        '''
        UPDATE pilot_requests
        SET status = %s,
            reviewed_at = %s,
            reviewed_by_user_id = %s,
            approved_at = COALESCE(approved_at, %s),
            rejected_at = NULL,
            invitation_token_hash = %s,
            invitation_expires_at = %s,
            invitation_sent_at = NULL,
            invitation_delivery_error = NULL,
            updated_at = NOW()
        WHERE id = %s
        ''',
        (
            STATUS_APPROVED, moment, str(reviewer_user_id), moment,
            token_hash(raw_token), expires_at, str(request_id),
        ),
    )
    updated = get_request(connection, request_id)
    if updated is None:  # pragma: no cover - only on a concurrent delete
        raise _http_error(
            404, {'code': CODE_PILOT_REQUEST_NOT_FOUND, 'message': 'Pilot request not found.'},
        )
    return updated, raw_token


def mark_invitation_sent(connection: Any, *, request_id: str) -> dict[str, Any] | None:
    connection.execute(
        '''
        UPDATE pilot_requests
        SET status = %s, invitation_sent_at = NOW(), invitation_delivery_error = NULL, updated_at = NOW()
        WHERE id = %s AND status = %s
        ''',
        (STATUS_INVITED, str(request_id), STATUS_APPROVED),
    )
    return get_request(connection, request_id)


def mark_invitation_failed(connection: Any, *, request_id: str, error: str) -> dict[str, Any] | None:
    """Record that the approval stands but the email did not go out.

    The request stays ``approved`` (not ``invited``), because claiming an
    invitation was sent when it was not is exactly the kind of comfortable lie
    the console must never tell. The token remains valid so a retry can send the
    same link.
    """
    connection.execute(
        '''
        UPDATE pilot_requests
        SET invitation_sent_at = NULL, invitation_delivery_error = %s, updated_at = NOW()
        WHERE id = %s
        ''',
        (str(error)[:500], str(request_id)),
    )
    return get_request(connection, request_id)


def reject_request(
    connection: Any,
    *,
    request_id: str,
    reviewer_user_id: str,
    internal_note: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Decline a request. Clears any invitation so a minted token dies with it."""
    existing = get_request(connection, request_id)
    if existing is None:
        raise _http_error(
            404, {'code': CODE_PILOT_REQUEST_NOT_FOUND, 'message': 'Pilot request not found.'},
        )
    if str(existing.get('status') or '') == STATUS_ACTIVATED:
        raise _http_error(
            409,
            {
                'code': CODE_PILOT_REQUEST_NOT_PENDING,
                'message': 'An activated request cannot be rejected. Suspend the organization instead.',
            },
        )
    note = _clean_text(
        internal_note, field='internal_note', max_chars=MAX_INTERNAL_NOTE_CHARS, required=False,
    ) or None
    moment = now or _utc_now()
    connection.execute(
        '''
        UPDATE pilot_requests
        SET status = %s,
            reviewed_at = %s,
            reviewed_by_user_id = %s,
            rejected_at = %s,
            approved_at = NULL,
            internal_note = COALESCE(%s, internal_note),
            invitation_token_hash = NULL,
            invitation_expires_at = NULL,
            invitation_sent_at = NULL,
            invitation_delivery_error = NULL,
            updated_at = NOW()
        WHERE id = %s
        ''',
        (STATUS_REJECTED, moment, str(reviewer_user_id), moment, note, str(request_id)),
    )
    updated = get_request(connection, request_id)
    if updated is None:  # pragma: no cover - only on a concurrent delete
        raise _http_error(
            404, {'code': CODE_PILOT_REQUEST_NOT_FOUND, 'message': 'Pilot request not found.'},
        )
    return updated


# ── invitation resolution ────────────────────────────────────────────────────
def find_by_token(connection: Any, raw_token: str) -> dict[str, Any] | None:
    """The request an invitation token belongs to, looked up by HASH.

    The plaintext token is never compared against a stored plaintext, because no
    plaintext is stored.
    """
    token = str(raw_token or '').strip()
    if not token:
        return None
    return _row_dict(
        connection.execute(
            f'SELECT {_REQUEST_COLUMNS} FROM pilot_requests WHERE invitation_token_hash = %s',
            (token_hash(token),),
        ).fetchone()
    )


def invitation_problem(row: Mapping[str, Any] | None, *, now: datetime | None = None) -> tuple[str, str] | None:
    """``(code, message)`` describing why this invitation cannot be used, or None.

    One place decides validity, so the lookup endpoint and the acceptance
    endpoint can never disagree about whether a link still works.
    """
    if row is None:
        return (CODE_INVITATION_INVALID, 'This invitation link is not valid.')
    status_value = str(row.get('status') or '')
    if status_value == STATUS_ACTIVATED:
        return (CODE_INVITATION_USED, 'This invitation has already been used.')
    if status_value == STATUS_REJECTED:
        return (CODE_INVITATION_INVALID, 'This invitation link is not valid.')
    if status_value not in INVITABLE_STATUSES:
        return (CODE_INVITATION_EXPIRED, 'This invitation has expired.')
    expires_at = _as_datetime(row.get('invitation_expires_at'))
    if expires_at is None or expires_at <= (now or _utc_now()):
        return (CODE_INVITATION_EXPIRED, 'This invitation has expired.')
    return None


def expire_invitation(connection: Any, *, request_id: str) -> None:
    """Retire an invitation whose window closed, so its token stops resolving."""
    connection.execute(
        '''
        UPDATE pilot_requests
        SET status = %s, invitation_token_hash = NULL, updated_at = NOW()
        WHERE id = %s AND status = ANY(%s)
        ''',
        (STATUS_EXPIRED, str(request_id), list(INVITABLE_STATUSES)),
    )


def mark_activated(
    connection: Any, *, request_id: str, organization_id: str, now: datetime | None = None,
) -> dict[str, Any] | None:
    """Consume the invitation and bind the request to the organization it made.

    Clearing ``invitation_token_hash`` in the same statement is what makes the
    token single-use: after this, the link resolves to nothing. The UPDATE is
    conditional on the row still being invitable, so two concurrent acceptances
    cannot both succeed.
    """
    moment = now or _utc_now()
    connection.execute(
        '''
        UPDATE pilot_requests
        SET status = %s,
            organization_id = %s,
            invitation_accepted_at = %s,
            invitation_token_hash = NULL,
            updated_at = NOW()
        WHERE id = %s AND status = ANY(%s)
        ''',
        (STATUS_ACTIVATED, str(organization_id), moment, str(request_id), list(INVITABLE_STATUSES)),
    )
    return get_request(connection, request_id)


# ── customer-facing access state ─────────────────────────────────────────────
ACCESS_ACTIVE = 'active'
ACCESS_PENDING_REVIEW = 'pending_review'
ACCESS_INVITED = 'invited'
ACCESS_REJECTED = 'rejected'
ACCESS_NONE = 'none'


def access_state_for_user(
    connection: Any, *, user_id: str, email: str, now: datetime | None = None,
) -> dict[str, Any]:
    """Whether this authenticated account has Pilot access, and if not, why.

    Authentication alone answers none of this. The states are kept apart on
    purpose so the product can say "under review" to someone who applied and
    "access required" to someone who never did — instead of showing both an
    empty dashboard that looks like a healthy tenant with no data.
    """
    membership_count = 0
    try:
        row = _row_dict(
            connection.execute(
                'SELECT COUNT(*) AS count FROM organization_memberships WHERE user_id = %s',
                (str(user_id),),
            ).fetchone()
        )
        membership_count = int((row or {}).get('count') or 0)
    except Exception:
        # Pre-0150 deployment, or an unreadable probe. Fall back to workspace
        # membership, which is the pre-tenancy notion of "has access".
        logger.warning('pilot_access_membership_probe_failed user_id=%s', user_id, exc_info=True)
        try:
            row = _row_dict(
                connection.execute(
                    'SELECT COUNT(*) AS count FROM workspace_members WHERE user_id = %s',
                    (str(user_id),),
                ).fetchone()
            )
            membership_count = int((row or {}).get('count') or 0)
        except Exception:
            membership_count = 0
    if membership_count > 0:
        return {'state': ACCESS_ACTIVE, 'has_access': True, 'request': None}

    if not pilot_requests_schema_ready(connection):
        return {'state': ACCESS_NONE, 'has_access': False, 'request': None}

    row = _row_dict(
        connection.execute(
            f'''
            SELECT {_REQUEST_COLUMNS} FROM pilot_requests
            WHERE email = %s
            ORDER BY requested_at DESC
            LIMIT 1
            ''',
            (normalize_email(email),),
        ).fetchone()
    )
    if row is None:
        return {'state': ACCESS_NONE, 'has_access': False, 'request': None}
    status_value = str(row.get('status') or '')
    # Only the applicant's OWN status is reported, and only the fields they
    # already know: no internal note, no reviewer, no token.
    summary = {
        'status': status_value,
        'company_name': row.get('company_name'),
        'requested_at': _iso(row.get('requested_at')),
    }
    if status_value == STATUS_PENDING:
        return {'state': ACCESS_PENDING_REVIEW, 'has_access': False, 'request': summary}
    if status_value in INVITABLE_STATUSES:
        problem = invitation_problem(row, now=now)
        if problem is None:
            return {'state': ACCESS_INVITED, 'has_access': False, 'request': summary}
        return {'state': ACCESS_NONE, 'has_access': False, 'request': summary}
    if status_value == STATUS_REJECTED:
        # The applicant is told their request is not active. The internal note is
        # never included: it is written for Decoda staff, not for the applicant.
        return {'state': ACCESS_REJECTED, 'has_access': False, 'request': summary}
    return {'state': ACCESS_NONE, 'has_access': False, 'request': summary}


def public_request_summary(row: Mapping[str, Any]) -> dict[str, Any]:
    """The subset of a request that is safe to return to the applicant."""
    return {
        'id': str(row.get('id')),
        'status': str(row.get('status') or ''),
        'email': row.get('email'),
        'company_name': row.get('company_name'),
        'requested_at': _iso(row.get('requested_at')),
    }


def evaluation_days() -> int:
    """The one configured Pilot evaluation length, re-exported for callers."""
    return ent.evaluation_days()
