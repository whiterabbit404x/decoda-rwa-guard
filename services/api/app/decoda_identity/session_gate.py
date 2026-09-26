"""Per-request checks for Guard sessions under the shared Decoda identity.

Called from ``pilot._validate_session`` for every authenticated request, after
Guard's own checks (token signature, session row, expiry, revocation list,
session version) have passed:

* A Decoda (``auth_mode='workos'``) session is honoured only
  - while shared identity is enabled;
  - on requests the web BFF forwards for the SAME live AuthKit session it was
    issued to (when a BFF secret is configured — always in production);
  - while the Decoda platform confirms the WorkOS session is not revoked and
    the user is an active member of an active organization entitled to
    RWA Guard (grants cached ≤15 s; denials and revocations never cached; a
    platform outage refuses the request).
  Such a session is bound to one Guard organization: workspaces outside it are
  refused for the rest of the request (see ``bound_guard_organization``).

* A Guard-issued session (password, TOTP, workspace OIDC, invitation) is
  refused — and revoked — once the mode no longer allows it (`workos` mode, or
  `dual` mode after the legacy-password sunset).
"""

from __future__ import annotations

import hmac
import json
import logging
from contextvars import ContextVar
from typing import Any

from fastapi import HTTPException, status

from .config import load_identity_settings
from .platform import PlatformUnavailable

logger = logging.getLogger(__name__)

_BOUND_ORGANIZATION: ContextVar[str | None] = ContextVar('decoda_bound_guard_organization', default=None)

SESSION_ENDED = 'Your session ended. Sign in with Decoda to continue.'


def bound_guard_organization() -> str | None:
    """The Guard organization this request's Decoda session is bound to, if any."""
    return _BOUND_ORGANIZATION.get()


def _metadata(session: dict[str, Any]) -> dict[str, Any]:
    raw = session.get('metadata')
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else {}
        except ValueError:
            return {}
    return {}


def _end_session(session_hash: str, reason: str) -> None:
    """Revoke the session on its OWN connection: the refusal that follows rolls back
    the caller's transaction, which must neither undo the revocation nor be
    committed half-way by it."""
    from services.api.app import pilot

    try:
        with pilot.pg_connection() as own:
            own.execute(
                '''UPDATE auth_sessions
                   SET revoked_at = NOW(), updated_at = NOW(),
                       metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object('revoke_reason', %s::text)
                   WHERE session_token_hash = %s AND revoked_at IS NULL''',
                (reason, session_hash),
            )
            own.commit()
    except Exception:
        logger.warning('decoda_identity_session_revoke_failed reason=%s', reason, exc_info=True)
    pilot._blacklist_session_token(session_hash, pilot.SESSION_TTL_HOURS * 3600)


def _unauthorized() -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=SESSION_ENDED)


def check_session(session: dict[str, Any], session_hash: str, request: Any | None) -> None:
    """Raise 401/403/503 when this session may not be used; otherwise bind the request."""
    _BOUND_ORGANIZATION.set(None)
    settings = load_identity_settings()
    auth_mode = str(session.get('auth_mode') or '')

    if auth_mode != 'workos':
        if settings.mode == 'workos' or (settings.mode == 'dual' and not settings.legacy_passwords_allowed()):
            _end_session(session_hash, 'identity_mode_changed')
            raise _unauthorized()
        return

    if not settings.uses_workos:
        _end_session(session_hash, 'identity_mode_changed')
        raise _unauthorized()

    workos_session_id = str(session.get('workos_session_id') or '')
    if settings.bff_shared_secret:
        headers = getattr(request, 'headers', None)
        provided = headers.get('x-guard-proxy-secret', '') if headers is not None else ''
        bound = headers.get('x-guard-identity-session', '') if headers is not None else ''
        if not provided or not hmac.compare_digest(settings.bff_shared_secret.encode(), provided.encode()) or not bound:
            raise _unauthorized()
        if not hmac.compare_digest(bound.encode(), workos_session_id.encode()):
            # Another sign-in now owns this browser: the old Guard session is orphaned.
            _end_session(session_hash, 'identity_session_mismatch')
            raise _unauthorized()

    meta = _metadata(session)
    workos_user_id = str(meta.get('workos_user_id') or '')
    workos_organization_id = str(meta.get('workos_organization_id') or '')
    guard_organization_id = str(meta.get('guard_organization_id') or '')
    if not (workos_session_id and workos_user_id and workos_organization_id and guard_organization_id):
        _end_session(session_hash, 'identity_binding_incomplete')
        raise _unauthorized()

    from . import get_directory

    try:
        access = get_directory().product_access(
            workos_user_id=workos_user_id,
            workos_organization_id=workos_organization_id,
            workos_session_id=workos_session_id,
        )
    except PlatformUnavailable:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={'code': 'IDENTITY_DIRECTORY_UNAVAILABLE', 'message': 'Decoda sign-in is temporarily unavailable. Please try again shortly.'},
        ) from None
    if access.session_revoked:
        _end_session(session_hash, 'identity_session_revoked')
        raise _unauthorized()
    if not access.granted:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={'code': 'PRODUCT_ACCESS_DENIED', 'message': access.denial_message, 'reason': access.access_state},
        )
    if access.platform_organization_id != str(meta.get('platform_organization_id') or ''):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={'code': 'PRODUCT_ACCESS_DENIED', 'message': 'This organization is linked inconsistently. Contact Decoda support.'},
        )
    _BOUND_ORGANIZATION.set(guard_organization_id)


def bind_organization(guard_organization_id: str | None) -> None:
    """Bind the rest of this request to one Guard organization (session exchange)."""
    _BOUND_ORGANIZATION.set(guard_organization_id)


def enforce_workspace_binding(workspace_organization_id: Any) -> None:
    """Refuse a workspace outside the organization this Decoda session is bound to."""
    bound = bound_guard_organization()
    if bound is None:
        return
    if str(workspace_organization_id or '') != bound:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                'code': 'WORKSPACE_OUTSIDE_ORGANIZATION',
                'message': 'That workspace belongs to another organization. Switch organization with Decoda to open it.',
            },
        )


def session_has_idp_mfa(session: dict[str, Any] | None) -> bool:
    """Whether this session completed MFA at the identity provider (attested, assured method)."""
    if not session or session.get('mfa_verified_at') is None:
        return False
    methods = session.get('authentication_methods')
    return isinstance(methods, list) and 'idp_mfa' in {str(m) for m in methods}
