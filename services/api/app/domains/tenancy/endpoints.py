"""Request handlers for organization plan, usage, feedback, and founder admin.

Contract
--------
Public (no session at all):
  * ``POST /pilot-requests``     apply for a Pilot evaluation → PENDING
  * ``GET  /pilot-invitations``  describe an invitation to its token holder

Customer-facing (organization derived from the authenticated session only):
  * ``GET  /account/plan``               plan, lifecycle, usage, entitlements
  * ``POST /account/feedback``           one pilot feedback row — quick, detailed,
                                        or the end-of-Pilot review (one table)
  * ``GET  /account/pilot-access``       does THIS account have Pilot access
  * ``POST /pilot-invitations/accept``   activate the approved organization

Internal founder admin (``users.is_internal_admin`` or the exact-address
deployment allowlist — never a workspace role, never a request parameter):
  * ``GET   /admin/customers``                        every organization
  * ``GET   /admin/customers/{id}``                   one organization in detail
  * ``POST  /admin/customers/{id}/extend-evaluation`` push the deadline out
  * ``POST  /admin/customers/{id}/status``            suspend / reactivate / expire
  * ``POST  /admin/customers/{id}/plan``              change plan in place
  * ``GET   /admin/feedback``                         evaluator feedback + roadmap counters
  * ``GET   /admin/pilot-requests``                   the review queue
  * ``POST  /admin/pilot-requests/{id}/approve``      approve + invite
  * ``POST  /admin/pilot-requests/{id}/reject``       decline
  * ``POST  /admin/pilot-requests/{id}/resend-invitation``  re-issue the link

What these handlers deliberately do NOT do
------------------------------------------
No customer endpoint accepts an ``organization_id``. The tenant is resolved from
the session's workspace membership, so a browser cannot select whose plan or
usage it reads. The admin endpoints DO address an organization by id — that is
their purpose — and every one of them authorizes internal staff BEFORE reading
anything, so a customer receives 403 and no organization data.

Nothing here can grant internal admin: there is no write path to
``users.is_internal_admin`` in the API at all. Nor can anything here grant Pilot
access: the public request endpoint only records an application, and the only
route that creates an organization is invitation acceptance, which first proves
internal staff approved the address AND that the authenticated account owns it.

The one personal datum the admin listing carries is each organization's primary
contact ADDRESS — which human to contact about a tenant — resolved from
membership by ``organizations.list_customer_organizations``. No credential,
session, or authentication column is read, and no customer-facing endpoint
returns another organization's contact.
"""

from __future__ import annotations

import logging
from typing import Any

from services.api.app import entitlements as ent
from services.api.app import organizations as org_service
from services.api.app import pilot
from services.api.app import pilot_access

try:  # fastapi is stubbed in the offline test runner
    from fastapi import HTTPException, status
except Exception:  # pragma: no cover
    HTTPException = pilot.HTTPException  # type: ignore[assignment]
    status = pilot.status  # type: ignore[assignment]

logger = logging.getLogger(__name__)

#: Reported by GET /account/plan when the tenancy schema has not been migrated
#: on this deployment. Stated as its own condition rather than rendered as a
#: healthy Pilot: "we cannot tell you your plan" is not "you are on Pilot".
PLAN_STATE_UNAVAILABLE = 'unavailable'
PLAN_STATE_ACTIVE = 'available'


# ── customer-facing ──────────────────────────────────────────────────────────
def get_account_plan(request: Any) -> dict[str, Any]:
    """Plan, lifecycle, usage, and entitlements for the caller's organization."""
    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user = pilot.authenticate_with_connection(connection, request)
        workspace_context = pilot.resolve_workspace(
            connection, user['id'], request.headers.get('x-workspace-id'),
        )
        workspace_id = workspace_context['workspace_id']
        context = pilot.organization_context(connection, workspace_id)
        if not context.get('available'):
            logger.warning(
                'account_plan_unavailable workspace_id=%s reason=%s', workspace_id, context.get('reason'),
            )
            return {
                'state': PLAN_STATE_UNAVAILABLE,
                'reason': context.get('reason'),
                'organization': None,
                'plan': None,
                'status': None,
                'lifecycle_state': None,
                'evaluation': None,
                'usage': None,
                'entitlements': None,
                'workspace': {'id': workspace_id, 'role': workspace_context.get('role')},
            }
        organization = context['organization']
        entitlements = context['entitlements']
        usage = org_service.usage_summary(connection, organization)
        connection.commit()
        return {
            'state': PLAN_STATE_ACTIVE,
            'reason': None,
            'organization': {
                'id': str(organization['id']),
                'name': organization.get('name'),
                'slug': organization.get('slug'),
            },
            'plan': ent.normalize_plan(organization.get('plan')),
            'plan_label': ent.plan_label(organization.get('plan')),
            'status': ent.normalize_status(organization.get('status')),
            'lifecycle_state': context['lifecycle_state'],
            'lifecycle_label': ent.LIFECYCLE_LABELS.get(context['lifecycle_state']),
            'evaluation': ent.evaluation_payload(organization),
            'usage': usage,
            'entitlements': entitlements,
            'workspace': {'id': workspace_id, 'role': workspace_context.get('role')},
        }


def submit_account_feedback(payload: dict[str, Any], request: Any) -> dict[str, Any]:
    """Record one evaluator feedback item against the caller's organization.

    Serves all three depths — quick, detailed, and the end-of-Pilot review — from
    one endpoint, because they write one row to one table. The organization,
    workspace, user, and pilot day stamped on that row all come from the
    authenticated session and the tenant's own evaluation window. A body that
    names a different tenant changes nothing; a contextual incident/alert/asset
    id that belongs to a different tenant is dropped rather than stored.
    """
    pilot.require_live_mode()
    body = payload if isinstance(payload, dict) else {}
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user = pilot.authenticate_with_connection(connection, request)
        workspace_context = pilot.resolve_workspace(
            connection, user['id'], request.headers.get('x-workspace-id'),
        )
        workspace_id = workspace_context['workspace_id']
        context = pilot.organization_context(connection, workspace_id)
        if not context.get('available'):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    'code': ent.CODE_ORGANIZATION_CONTEXT_MISSING,
                    'message': 'Feedback is unavailable until this deployment finishes migrating.',
                },
            )
        organization = context['organization']
        organization_id = str(organization['id'])
        recorded = org_service.record_feedback(
            connection,
            organization_id=organization_id,
            workspace_id=workspace_id,
            user_id=str(user['id']),
            feedback_type=body.get('feedback_type'),
            message=body.get('message'),
            context=org_service.resolve_feedback_context(
                connection, body.get('context'), organization_id=organization_id,
            ),
            feedback_mode=body.get('feedback_mode') or org_service.FEEDBACK_MODE_QUICK,
            severity=body.get('severity'),
            production_blocker=body.get('production_blocker'),
            continue_intent=body.get('continue_intent'),
            contact_permission=body.get('contact_permission'),
            # Derived from the tenant's own evaluation window, never read from the
            # body: "they said this on day 3" is a fact about the organization,
            # and a client-supplied day would be a number the customer could set.
            pilot_day=org_service.pilot_day_for(organization),
            narratives={
                field: body.get(field) for field in org_service.FEEDBACK_NARRATIVE_FIELDS
            },
        )
        # The audit row records THAT feedback was submitted, of what kind and at
        # what depth. No narrative answer is copied into it: security feedback
        # stays in one internal-only table rather than being duplicated into a log
        # that more surfaces read.
        pilot.log_audit(
            connection,
            action='organization.feedback_submitted',
            entity_type='organization_feedback',
            entity_id=recorded['id'],
            request=request,
            user_id=str(user['id']),
            workspace_id=workspace_id,
            metadata={
                'organization_id': organization_id,
                'feedback_type': recorded['feedback_type'],
                'feedback_mode': recorded['feedback_mode'],
            },
        )
        connection.commit()
        return {
            'submitted': True,
            'id': recorded['id'],
            'feedback_type': recorded['feedback_type'],
            'feedback_mode': recorded['feedback_mode'],
        }


# ── founder / internal admin ─────────────────────────────────────────────────
def _organization_detail(connection: Any, organization_id: str) -> dict[str, Any]:
    organization = org_service.get_organization(connection, organization_id)
    if organization is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={'code': org_service.CODE_ORGANIZATION_NOT_FOUND, 'message': 'Organization not found.'},
        )
    entitlements = ent.get_entitlements(organization)
    return {
        'organization': {
            'id': str(organization['id']),
            'name': organization.get('name'),
            'slug': organization.get('slug'),
            'plan': ent.normalize_plan(organization.get('plan')),
            'status': ent.normalize_status(organization.get('status')),
            'lifecycle_state': ent.lifecycle_state(organization),
            'created_at': (
                organization['created_at'].isoformat()
                if hasattr(organization.get('created_at'), 'isoformat')
                else organization.get('created_at')
            ),
        },
        'evaluation': ent.evaluation_payload(organization),
        'entitlements': entitlements,
        'usage': org_service.usage_summary(connection, organization),
        'workspaces': org_service.organization_workspaces(connection, str(organization['id'])),
        'members': org_service.organization_members(connection, str(organization['id'])),
    }


def _require_tenancy_schema(connection: Any) -> None:
    if org_service.tenancy_schema_ready(connection):
        return
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            'code': ent.CODE_ORGANIZATION_CONTEXT_MISSING,
            'message': 'The organization tenancy schema is not available on this deployment yet.',
        },
    )


def list_admin_customers(request: Any, limit: int = 100, offset: int = 0) -> dict[str, Any]:
    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        org_service.require_internal_admin(connection, request)
        _require_tenancy_schema(connection)
        customers = org_service.list_customer_organizations(connection, limit=limit, offset=offset)
        return {'customers': customers, 'count': len(customers)}


def get_admin_customer(organization_id: str, request: Any) -> dict[str, Any]:
    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        org_service.require_internal_admin(connection, request)
        _require_tenancy_schema(connection)
        detail = _organization_detail(connection, organization_id)
        detail['feedback'] = org_service.list_feedback(connection, organization_id=organization_id)
        return detail


def _admin_lifecycle_audit(
    connection: Any,
    request: Any,
    *,
    actor_user_id: str,
    action: str,
    organization: dict[str, Any],
    metadata: dict[str, Any],
) -> None:
    """One audit row per privileged lifecycle action.

    ``workspace_id`` is None: these are TENANT-level actions taken by internal
    staff, not actions inside any one customer workspace, and attributing them to
    a workspace would corrupt that workspace's audit chain.
    """
    pilot.log_audit(
        connection,
        action=action,
        entity_type='organization',
        entity_id=str(organization['id']),
        request=request,
        user_id=actor_user_id,
        workspace_id=None,
        metadata={
            'organization_name': organization.get('name'),
            'plan': ent.normalize_plan(organization.get('plan')),
            'status': ent.normalize_status(organization.get('status')),
            **metadata,
        },
    )


def extend_admin_customer_evaluation(
    organization_id: str, payload: dict[str, Any], request: Any,
) -> dict[str, Any]:
    pilot.require_live_mode()
    body = payload if isinstance(payload, dict) else {}
    raw_days = body.get('days', ent.evaluation_days())
    try:
        days = int(raw_days)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={'code': 'INVALID_EVALUATION_EXTENSION', 'message': 'days must be an integer.'},
        ) from None
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        admin = org_service.require_internal_admin(connection, request)
        _require_tenancy_schema(connection)
        before = org_service.get_organization(connection, organization_id)
        organization = org_service.extend_evaluation(
            connection, organization_id=organization_id, days=days,
        )
        _admin_lifecycle_audit(
            connection, request,
            actor_user_id=str(admin['id']),
            action='organization.evaluation_extended',
            organization=organization,
            metadata={
                'days': days,
                'previous_expires_at': (
                    (before or {}).get('evaluation_expires_at').isoformat()
                    if hasattr((before or {}).get('evaluation_expires_at'), 'isoformat')
                    else (before or {}).get('evaluation_expires_at')
                ),
                'new_expires_at': (
                    organization['evaluation_expires_at'].isoformat()
                    if hasattr(organization.get('evaluation_expires_at'), 'isoformat')
                    else organization.get('evaluation_expires_at')
                ),
            },
        )
        connection.commit()
        return _organization_detail(connection, organization_id)


def set_admin_customer_status(
    organization_id: str, payload: dict[str, Any], request: Any,
) -> dict[str, Any]:
    pilot.require_live_mode()
    body = payload if isinstance(payload, dict) else {}
    requested_status = str(body.get('status') or '').strip().lower()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        admin = org_service.require_internal_admin(connection, request)
        _require_tenancy_schema(connection)
        before = org_service.get_organization(connection, organization_id)
        organization = org_service.set_status(
            connection, organization_id=organization_id, status_value=requested_status,
        )
        _admin_lifecycle_audit(
            connection, request,
            actor_user_id=str(admin['id']),
            action='organization.status_changed',
            organization=organization,
            metadata={
                'previous_status': ent.normalize_status((before or {}).get('status')),
                'new_status': ent.normalize_status(organization.get('status')),
            },
        )
        connection.commit()
        return _organization_detail(connection, organization_id)


def set_admin_customer_plan(
    organization_id: str, payload: dict[str, Any], request: Any,
) -> dict[str, Any]:
    """Change plan in place. Pilot → Scale keeps every existing record."""
    pilot.require_live_mode()
    body = payload if isinstance(payload, dict) else {}
    requested_plan = str(body.get('plan') or '').strip().lower()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        admin = org_service.require_internal_admin(connection, request)
        _require_tenancy_schema(connection)
        before = org_service.get_organization(connection, organization_id)
        organization = org_service.set_plan(
            connection, organization_id=organization_id, plan=requested_plan,
        )
        _admin_lifecycle_audit(
            connection, request,
            actor_user_id=str(admin['id']),
            action='organization.plan_changed',
            organization=organization,
            metadata={
                'previous_plan': ent.normalize_plan((before or {}).get('plan')),
                'new_plan': ent.normalize_plan(organization.get('plan')),
            },
        )
        connection.commit()
        return _organization_detail(connection, organization_id)


def list_admin_feedback(
    request: Any,
    organization_id: str | None = None,
    limit: int = 100,
    feedback_type: str | None = None,
    severity: str | None = None,
    production_blocker: str | None = None,
    feedback_mode: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> dict[str, Any]:
    """Evaluator feedback for the internal console, with roadmap counters.

    ``summary`` counts the SAME rows this call returns, so a counter can never
    claim a pattern the visible list does not contain. It is reported alongside
    ``detail_available`` — before migration 0153 the severity and
    production-blocker counters would be structurally zero, and a bare "0
    production blockers" would read as "nobody is blocked" rather than "this
    deployment cannot record that yet".
    """
    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        org_service.require_internal_admin(connection, request)
        _require_tenancy_schema(connection)
        items = org_service.list_feedback(
            connection,
            organization_id=organization_id,
            limit=limit,
            feedback_type=feedback_type,
            severity=severity,
            production_blocker=production_blocker,
            feedback_mode=feedback_mode,
            since=since,
            until=until,
        )
        return {
            'feedback': items,
            'count': len(items),
            'summary': org_service.feedback_summary(items),
            'detail_available': org_service.feedback_detail_schema_ready(connection),
        }


# ── approval-only Pilot access ───────────────────────────────────────────────
# Three surfaces with three different authorization postures:
#
#   PUBLIC    POST /pilot-requests            unauthenticated; creates a PENDING
#                                             row and provisions nothing
#   INTERNAL  GET/POST /admin/pilot-requests… require_internal_admin FIRST
#   INVITEE   GET  /pilot-invitations         unauthenticated token lookup
#             POST /pilot-invitations/accept  authenticated; email must match
#
# No surface below reads an organization_id, plan, role, or entitlement from the
# caller. Those are server-decided at activation time, in pilot.provision_pilot_
# organization.

def submit_pilot_request(payload: dict[str, Any], request: Any) -> dict[str, Any]:
    """Record one PENDING Pilot evaluation request from the public website.

    What this deliberately does not do: create an organization, create a
    workspace, grant an entitlement, start monitoring, touch an RPC provider, or
    queue a background job. A pending row is a queue entry for a human reviewer,
    and it stays inert until internal staff approve it.

    The response never says whether the address already applied in a way that
    changes the outcome — a duplicate is reported as "already under review",
    which is what the applicant needs to know and reveals nothing about anyone
    else's request.
    """
    pilot.require_live_mode()
    fields = pilot_access.validate_submission(payload)
    client = getattr(request, 'client', None)
    source_ip = client.host if client else None
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        pilot_access.require_schema(connection)
        result = pilot_access.submit_request(connection, fields=fields, source_ip=source_ip)
        record = result['request']
        if not result['duplicate']:
            # The applicant is not an authenticated user, so the audit row has no
            # actor and no workspace. It records the FACT of the submission and
            # its business context — never a credential, and never the free-text
            # use case, which stays in the one table internal staff read.
            pilot.log_audit(
                connection,
                action='pilot_request.submitted',
                entity_type='pilot_request',
                entity_id=str(record['id']),
                request=request,
                user_id=None,
                workspace_id=None,
                metadata={
                    'email': record['email'],
                    'company_name': record['company_name'],
                    'status': record['status'],
                },
            )
        connection.commit()
    logger.info(
        'pilot_request_submitted duplicate=%s status=%s', result['duplicate'], record['status'],
    )
    return {
        'received': True,
        'duplicate': bool(result['duplicate']),
        'status': record['status'],
        'message': (
            'An evaluation request for this email is already under review.'
            if result['duplicate']
            else 'Pilot request received. We will review your request and contact you by email.'
        ),
        'request': pilot_access.public_request_summary(record),
    }


def get_pilot_access_state(request: Any) -> dict[str, Any]:
    """Whether the CALLER has Pilot access, and if not, which state they are in.

    Reported from the caller's own session only. There is no parameter naming a
    user or an email, so this cannot be used to probe whether someone else has
    applied.
    """
    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user = pilot.authenticate_with_connection(connection, request)
        state = pilot_access.access_state_for_user(
            connection, user_id=str(user['id']), email=str(user.get('email') or ''),
        )
        return {
            'state': state['state'],
            'has_access': bool(state['has_access']),
            'request': state['request'],
        }


# ── internal review ──────────────────────────────────────────────────────────
def _require_pilot_request_schema(connection: Any) -> None:
    pilot_access.require_schema(connection)


def list_admin_pilot_requests(
    request: Any, status_filter: str | None = None, limit: int = 100, offset: int = 0,
) -> dict[str, Any]:
    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        org_service.require_internal_admin(connection, request)
        _require_pilot_request_schema(connection)
        requests = pilot_access.list_requests(
            connection, status=status_filter, limit=limit, offset=offset,
        )
        return {'requests': requests, 'count': len(requests)}


def _pilot_request_audit(
    connection: Any,
    request: Any,
    *,
    actor_user_id: str | None,
    action: str,
    record: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> None:
    """One audit row per Pilot-request lifecycle action.

    ``workspace_id`` is None: these are TENANT-level decisions taken before any
    workspace exists. The raw invitation token is never a member of ``metadata``
    — only the fact that one was issued, and when it expires.
    """
    pilot.log_audit(
        connection,
        action=action,
        entity_type='pilot_request',
        entity_id=str(record['id']),
        request=request,
        user_id=actor_user_id,
        workspace_id=None,
        metadata={
            'email': record.get('email'),
            'company_name': record.get('company_name'),
            'status': record.get('status'),
            **(metadata or {}),
        },
    )


def approve_admin_pilot_request(
    request_id: str, payload: dict[str, Any], request: Any,
) -> dict[str, Any]:
    """Approve a request and send its single-use invitation.

    Approving is NOT activation. No organization, workspace, plan, or monitoring
    is created here; the invitation only grants the right to activate, and only
    to the approved address, once, before it expires.

    If email delivery fails the approval still stands and the console shows
    "Approved — invitation not sent" with a retry, because telling the founder
    that a link was delivered when it was not is precisely the kind of quiet
    falsehood this product refuses to render.
    """
    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        admin = org_service.require_internal_admin(connection, request)
        _require_pilot_request_schema(connection)
        record, raw_token = pilot_access.approve_request(
            connection, request_id=request_id, reviewer_user_id=str(admin['id']),
        )
        _pilot_request_audit(
            connection, request,
            actor_user_id=str(admin['id']),
            action='pilot_request.approved',
            record=record,
            metadata={
                'invitation_issued': True,
                'invitation_expires_at': (
                    record['invitation_expires_at'].isoformat()
                    if hasattr(record.get('invitation_expires_at'), 'isoformat')
                    else record.get('invitation_expires_at')
                ),
            },
        )
        delivery_error: str | None = None
        try:
            pilot._dispatch_transactional_email(
                connection,
                to_email=str(record['email']),
                purpose='pilot_invitation',
                token=raw_token,
                request=request,
                context={
                    'company_name': record.get('company_name'),
                    'reference': str(record['id']),
                    'ttl_hours': pilot_access.invitation_ttl_hours(),
                    'evaluation_days': pilot_access.evaluation_days(),
                },
            )
        except Exception as exc:  # delivery is best-effort; the approval is not
            delivery_error = f'{type(exc).__name__}: {exc}'
            logger.warning('pilot_invitation_delivery_failed request_id=%s', record['id'], exc_info=True)
            record = pilot_access.mark_invitation_failed(
                connection, request_id=str(record['id']), error=delivery_error,
            ) or record
            _pilot_request_audit(
                connection, request,
                actor_user_id=str(admin['id']),
                action='pilot_request.invitation_delivery_failed',
                record=record,
                metadata={'error_type': type(exc).__name__},
            )
        else:
            record = pilot_access.mark_invitation_sent(connection, request_id=str(record['id'])) or record
            _pilot_request_audit(
                connection, request,
                actor_user_id=str(admin['id']),
                action='pilot_request.invitation_sent',
                record=record,
                metadata={'delivered': True},
            )
        connection.commit()
        return {
            'request': pilot_access.admin_view(record),
            'invitation_sent': delivery_error is None,
            # The failure reason is internal-console detail. The raw token is
            # NOT returned: it lives in the email, and nowhere else.
            'invitation_error': delivery_error,
        }


def resend_admin_pilot_invitation(request_id: str, request: Any) -> dict[str, Any]:
    """Re-issue and re-send an invitation for an already-approved request.

    A retry mints a FRESH token and a fresh expiry rather than resending the old
    one, so a link that leaked from a failed delivery attempt is dead once the
    replacement is issued.
    """
    return approve_admin_pilot_request(request_id, {}, request)


def reject_admin_pilot_request(
    request_id: str, payload: dict[str, Any], request: Any,
) -> dict[str, Any]:
    """Decline a request. No invitation, no workspace, no monitoring, no cost."""
    pilot.require_live_mode()
    body = payload if isinstance(payload, dict) else {}
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        admin = org_service.require_internal_admin(connection, request)
        _require_pilot_request_schema(connection)
        record = pilot_access.reject_request(
            connection,
            request_id=request_id,
            reviewer_user_id=str(admin['id']),
            internal_note=body.get('internal_note'),
        )
        _pilot_request_audit(
            connection, request,
            actor_user_id=str(admin['id']),
            action='pilot_request.rejected',
            record=record,
            # Whether a note was written is auditable; its text is not copied
            # into a log that more surfaces read.
            metadata={'internal_note_recorded': bool(record.get('internal_note'))},
        )
        connection.commit()
        return {'request': pilot_access.admin_view(record)}


# ── invitation lookup and acceptance ─────────────────────────────────────────
def _invitation_refusal(code: str, message: str) -> Exception:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={'code': code, 'message': message})


def lookup_pilot_invitation(token: str, request: Any) -> dict[str, Any]:
    """Describe an invitation to whoever holds its token.

    The token IS the credential, so holding it is what authorizes this read. The
    response carries only what the recipient already has — the address the
    invitation was mailed to and the company they named — so the page can say
    "sign in as security@company.com" instead of failing mysteriously after
    sign-in. It never carries an organization id, a plan, or an entitlement.

    ``account_exists`` is the fact that decides where an approved person is sent:
    to sign-in if they already have a Decoda account, to invitation-aware signup
    if they do not. It is answered HERE, from the database, rather than guessed
    in a browser — a page cannot know it, and sending everyone to sign-in strands
    every brand-new applicant at "Invalid email or password". It is disclosed only
    for an invitation that still resolves, and only about the address that
    invitation already names, so it is not an oracle for arbitrary addresses.
    """
    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        pilot_access.require_schema(connection)
        record = pilot_access.find_by_token(connection, token)
        problem = pilot_access.invitation_problem(record)
        if problem is not None:
            code, message = problem
            if record is not None and code == pilot_access.CODE_INVITATION_EXPIRED:
                # Retire the token as we discover it has lapsed, so the same link
                # stops resolving from this point on.
                pilot_access.expire_invitation(connection, request_id=str(record['id']))
                _pilot_request_audit(
                    connection, request,
                    actor_user_id=None,
                    action='pilot_request.invitation_expired',
                    record=record,
                )
                connection.commit()
            return {'valid': False, 'code': code, 'message': message, 'invitation': None}
        return {
            'valid': True,
            'code': None,
            'message': None,
            'invitation': {
                'email': record['email'],
                'company_name': record['company_name'],
                'expires_at': (
                    record['invitation_expires_at'].isoformat()
                    if hasattr(record.get('invitation_expires_at'), 'isoformat')
                    else record.get('invitation_expires_at')
                ),
                'evaluation_days': pilot_access.evaluation_days(),
                'status': str(record.get('status') or ''),
                'account_exists': pilot_access.account_exists_for_email(
                    connection, str(record['email']),
                ),
            },
        }


def signup_invited_user(payload: dict[str, Any], request: Any) -> dict[str, Any]:
    """Create the account for an approved invitation, and sign it in.

    The half of the flow that did not exist. An approved applicant who has never
    had a Decoda account had nowhere to go: every route out of the invitation
    email led to sign-in, and sign-in cannot help someone who has no password.

    What the request body is allowed to say is exactly two things — a full name
    and a password. Everything that governs identity or entitlement is read from
    the invitation row the TOKEN resolved to:

        email          the approved address, never a body field
        company_name   the approved company, used later at activation

    So there is no ``email``, ``organization_id``, ``plan``, ``role``, or
    ``is_internal_admin`` a caller could supply that this function would read. An
    invitation approved for ``security@company.com`` creates an account for
    ``security@company.com`` and no other address, whatever the body says.

    This creates an ACCOUNT, not a Pilot. The organization, workspace, plan, and
    evaluation window still come into existence only in
    ``accept_pilot_invitation`` → ``pilot.provision_pilot_organization``, which
    re-validates the same invitation against the now-authenticated session. The
    invitation token is NOT consumed here; acceptance consumes it, exactly once.
    """
    pilot.require_live_mode()
    body = payload if isinstance(payload, dict) else {}
    token = str(body.get('token') or '').strip()
    if not token:
        raise _invitation_refusal(
            pilot_access.CODE_INVITATION_INVALID, 'This invitation link is not valid.',
        )
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        pilot_access.require_schema(connection)
        record = pilot_access.find_by_token(connection, token)
        problem = pilot_access.invitation_problem(record)
        if problem is not None:
            code, message = problem
            if record is not None and code == pilot_access.CODE_INVITATION_EXPIRED:
                pilot_access.expire_invitation(connection, request_id=str(record['id']))
                connection.commit()
            logger.warning('pilot_invitation_signup_refused code=%s', code)
            raise _invitation_refusal(code, message)
        created = pilot.create_invited_account(
            connection,
            email=str(record['email']),
            password=str(body.get('password') or ''),
            full_name=str(body.get('full_name') or ''),
            request=request,
        )
        _pilot_request_audit(
            connection, request,
            actor_user_id=str(created['user_id']),
            action='pilot_request.invited_account_created',
            record=record,
            # The invitation is not spent by this: it is still the thing that
            # must be accepted before any tenant exists.
            metadata={'invitation_accepted': False, 'pilot_access_granted': False},
        )
        connection.commit()
        user_payload = pilot.build_user_response(connection, str(created['user_id']))
    logger.info('pilot_invitation_account_created user_id=%s', created['user_id'])
    return {
        # Shaped like /auth/signin so the existing same-origin auth proxy turns it
        # into the session cookie without a second cookie-writing convention.
        'access_token': created['access_token'],
        'token_type': 'bearer',
        'user': user_payload,
        # Creating the account is not the evaluation. The client still posts to
        # /pilot-invitations/accept, and says so rather than implying a Pilot is
        # already running.
        'invitation_accepted': False,
    }


def accept_pilot_invitation(payload: dict[str, Any], request: Any) -> dict[str, Any]:
    """Activate the approved Pilot organization for the invited person.

    Three facts must all hold, and all are checked server-side:

      1. the token resolves to a request that is approved, unused, and unexpired
      2. the authenticated account has VERIFIED its address, so ownership of it
         is proven rather than merely claimed
      3. that address equals the approved address, under the same normalisation
         the auth model uses

    An invitation approved for ``security@company.com`` therefore cannot be
    accepted by ``attacker@gmail.com``, whatever the request body says. The body
    is read for exactly one field — the token — so there is no organization id,
    plan, role, or internal-admin flag a caller could supply.
    """
    pilot.require_live_mode()
    body = payload if isinstance(payload, dict) else {}
    token = str(body.get('token') or '').strip()
    if not token:
        raise _invitation_refusal(
            pilot_access.CODE_INVITATION_INVALID, 'This invitation link is not valid.',
        )
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user = pilot.authenticate_with_connection(connection, request)
        pilot_access.require_schema(connection)
        record = pilot_access.find_by_token(connection, token)
        problem = pilot_access.invitation_problem(record)
        if problem is not None:
            code, message = problem
            if record is not None and code == pilot_access.CODE_INVITATION_EXPIRED:
                pilot_access.expire_invitation(connection, request_id=str(record['id']))
                connection.commit()
            logger.warning('pilot_invitation_refused code=%s user_id=%s', code, user.get('id'))
            raise _invitation_refusal(code, message)
        # Owning the address is the whole claim being checked, so it must be
        # PROVEN, not asserted. Sign-in already refuses an unverified account;
        # this states the requirement where the grant is made, so the guarantee
        # does not depend on a policy decision made in another module.
        if not user.get('email_verified'):
            logger.warning('pilot_invitation_refused code=email_unverified user_id=%s', user.get('id'))
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    'code': pilot_access.CODE_INVITATION_EMAIL_MISMATCH,
                    'message': (
                        'Verify your email address before accepting this invitation.'
                    ),
                },
            )
        account_email = pilot_access.normalize_email(user.get('email'))
        invited_email = pilot_access.normalize_email(record['email'])
        if not account_email or account_email != invited_email:
            logger.warning(
                'pilot_invitation_email_mismatch request_id=%s user_id=%s', record['id'], user.get('id'),
            )
            # The invited address is NOT echoed back to a non-matching caller:
            # someone holding a leaked link learns nothing about who it was for.
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    'code': pilot_access.CODE_INVITATION_EMAIL_MISMATCH,
                    'message': (
                        'This invitation was issued to a different email address. '
                        'Sign in with the address the invitation was sent to.'
                    ),
                },
            )
        provisioned = pilot.provision_pilot_organization(
            connection,
            user_id=str(user['id']),
            organization_name=str(record['company_name'] or '').strip() or 'Workspace',
            request=request,
        )
        organization = provisioned['organization']
        updated = pilot_access.mark_activated(
            connection, request_id=str(record['id']), organization_id=str(organization['id']),
        )
        if updated is None or str(updated.get('status')) != pilot_access.STATUS_ACTIVATED:
            # Another acceptance won the race. Nothing is committed, so the
            # organization and workspace written above are rolled back with it.
            connection.rollback()
            raise _invitation_refusal(
                pilot_access.CODE_INVITATION_USED, 'This invitation has already been used.',
            )
        _pilot_request_audit(
            connection, request,
            actor_user_id=str(user['id']),
            action='pilot_request.invitation_accepted',
            record=updated,
            metadata={
                'organization_id': str(organization['id']),
                'workspace_id': provisioned['workspace_id'],
                'plan': ent.normalize_plan(organization.get('plan')),
            },
        )
        connection.commit()
        user_payload = pilot.build_user_response(connection, str(user['id']))
        return {
            'activated': True,
            'organization': {
                'id': str(organization['id']),
                'name': organization.get('name'),
                'plan': ent.normalize_plan(organization.get('plan')),
                'status': ent.normalize_status(organization.get('status')),
            },
            'evaluation': ent.evaluation_payload(organization),
            'workspace': {'id': provisioned['workspace_id'], 'name': provisioned['workspace_name']},
            'user': user_payload,
        }
