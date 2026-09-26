"""What a signed-in Decoda user can switch to: organizations and products.

Browsers only ever see platform organization ids (internal UUIDs); the WorkOS
organization id needed to re-scope a sign-in is resolved server-side, and only
for an organization the platform confirms the user is an active member of AND
that is entitled to RWA Guard.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException, status

from .platform import DENIAL_MESSAGES, PlatformUnavailable
from .session_gate import _metadata


def _identity_session(connection: Any, request: Any) -> dict[str, Any]:
    from services.api.app import pilot

    row = connection.execute(
        'SELECT auth_mode, workos_session_id, metadata FROM auth_sessions WHERE session_token_hash = %s AND revoked_at IS NULL',
        (pilot._current_session_hash(request),),
    ).fetchone()
    meta = _metadata(dict(row)) if row else {}
    if row is None or row['auth_mode'] != 'workos' or not meta.get('workos_user_id') or not meta.get('workos_organization_id'):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={'code': 'IDENTITY_SESSION_REQUIRED', 'message': 'Sign in with your Decoda account to use this feature.'},
        )
    return meta


def _directory_call(fn):
    try:
        return fn()
    except PlatformUnavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={'code': 'IDENTITY_DIRECTORY_UNAVAILABLE', 'message': 'Decoda sign-in is temporarily unavailable. Please try again shortly.'},
        ) from None


def identity_context(request: Any) -> dict[str, Any]:
    from services.api.app import pilot

    from . import get_directory

    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        pilot.authenticate_with_connection(connection, request)
        meta = _identity_session(connection, request)
    directory = get_directory()
    organizations = [
        {
            'id': str(org['platform_organization_id']),
            'name': org['organization_name'],
            'slug': org['organization_slug'],
            'role': org['organization_role'],
            'product_access': org['product_access_state'],
            'current': org['workos_organization_id'] == meta['workos_organization_id'],
        }
        for org in _directory_call(lambda: directory.user_organizations(meta['workos_user_id']))
    ]
    products = [
        {
            'product': product['product'],
            'name': product['product_name'],
            'availability': product['product_availability'],
            'access': product['access_state'],
            'entitlement_status': product['entitlement_status'],
        }
        for product in _directory_call(lambda: directory.products_for(meta['workos_user_id'], meta['workos_organization_id']))
    ]
    return {'organizations': organizations, 'products': products}


def switch_target(payload: dict[str, Any], request: Any) -> dict[str, Any]:
    from services.api.app import pilot

    from . import get_directory
    from .exchange import require_bff

    require_bff(request)
    try:
        target = str(uuid.UUID(str((payload or {}).get('organization_id') or '')))
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail={'code': 'VALIDATION_FAILED', 'message': 'Choose an organization.'}
        ) from None
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        pilot.authenticate_with_connection(connection, request)
        meta = _identity_session(connection, request)
    for org in _directory_call(lambda: get_directory().user_organizations(meta['workos_user_id'])):
        if str(org['platform_organization_id']) != target:
            continue
        if org['product_access_state'] != 'granted':
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    'code': 'PRODUCT_ACCESS_DENIED',
                    'message': DENIAL_MESSAGES.get(org['product_access_state'], DENIAL_MESSAGES['not_entitled']),
                    'reason': org['product_access_state'],
                },
            )
        return {'workos_organization_id': org['workos_organization_id'], 'name': org['organization_name']}
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={'code': 'ORGANIZATION_NOT_AVAILABLE', 'message': 'You are not an active member of that organization.'},
    )
