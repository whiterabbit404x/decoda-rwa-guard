"""Request handlers for organization plan, usage, feedback, and founder admin.

Contract
--------
Customer-facing (organization derived from the authenticated session only):
  * ``GET  /account/plan``       plan, lifecycle, usage, entitlements
  * ``POST /account/feedback``   one pilot feedback row

Internal founder admin (``users.is_internal_admin`` or the exact-address
deployment allowlist — never a workspace role, never a request parameter):
  * ``GET   /admin/customers``                        every organization
  * ``GET   /admin/customers/{id}``                   one organization in detail
  * ``POST  /admin/customers/{id}/extend-evaluation`` push the deadline out
  * ``POST  /admin/customers/{id}/status``            suspend / reactivate / expire
  * ``POST  /admin/customers/{id}/plan``              change plan in place
  * ``GET   /admin/feedback``                         evaluator feedback

What these handlers deliberately do NOT do
------------------------------------------
No customer endpoint accepts an ``organization_id``. The tenant is resolved from
the session's workspace membership, so a browser cannot select whose plan or
usage it reads. The admin endpoints DO address an organization by id — that is
their purpose — and every one of them authorizes internal staff BEFORE reading
anything, so a customer receives 403 and no organization data.

Nothing here can grant internal admin: there is no write path to
``users.is_internal_admin`` in the API at all.
"""

from __future__ import annotations

import logging
from typing import Any

from services.api.app import entitlements as ent
from services.api.app import organizations as org_service
from services.api.app import pilot

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

    The organization, workspace, and user stamped on the row all come from the
    authenticated session. A body that names a different tenant changes nothing.
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
        recorded = org_service.record_feedback(
            connection,
            organization_id=str(organization['id']),
            workspace_id=workspace_id,
            user_id=str(user['id']),
            feedback_type=body.get('feedback_type'),
            message=body.get('message'),
            context=body.get('context'),
        )
        # The audit row records THAT feedback was submitted and of what kind. The
        # message body is deliberately not copied into it: security feedback stays
        # in one internal-only table rather than being duplicated into a log that
        # more surfaces read.
        pilot.log_audit(
            connection,
            action='organization.feedback_submitted',
            entity_type='organization_feedback',
            entity_id=recorded['id'],
            request=request,
            user_id=str(user['id']),
            workspace_id=workspace_id,
            metadata={
                'organization_id': str(organization['id']),
                'feedback_type': recorded['feedback_type'],
            },
        )
        connection.commit()
        return {'submitted': True, 'id': recorded['id'], 'feedback_type': recorded['feedback_type']}


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


def list_admin_feedback(request: Any, organization_id: str | None = None, limit: int = 100) -> dict[str, Any]:
    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        org_service.require_internal_admin(connection, request)
        _require_tenancy_schema(connection)
        items = org_service.list_feedback(connection, organization_id=organization_id, limit=limit)
        return {'feedback': items, 'count': len(items)}
