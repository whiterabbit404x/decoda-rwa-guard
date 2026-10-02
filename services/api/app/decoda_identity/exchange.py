"""Session exchange: a verified WorkOS sign-in becomes a Guard session.

Called by the Guard web BFF right after the AuthKit callback (and again after
an organization switch or a Decoda re-authentication). The BFF forwards the
WorkOS access token; nothing it says about the user is trusted. In order:

  1. the token's signature, issuer, expiry and claims are verified (JWKS);
  2. the token must be scoped to an organization;
  3. WorkOS confirms the session is still active, and how it was established
     (a session WorkOS marks as impersonated is always refused: RWA Guard has
     no impersonation, and no setting changes that);
  4. the Decoda platform confirms — uncached — that the user is an active
     member of an active organization entitled to RWA Guard, and that the
     session has not been revoked;
  5. Guard's own records are linked or created (below);
  6. any earlier Guard session for the same WorkOS session is revoked and a
     new one is issued, bound to that WorkOS session and organization.

Linking rules (Guard keeps its internal UUIDs; WorkOS ids are unique mappings):
  * user: matched by (auth_provider='workos', external_subject=<WorkOS user>);
    an existing password account is linked ONLY through an operator-reviewed
    legacy link — an email match alone is a conflict, never a merge;
  * organization: matched by platform organization id. An existing Guard
    organization is linked ONLY through the platform's reviewed legacy
    organization link (written by the reviewed manifest import) — never by
    name, domain or membership. Otherwise a platform organization with no Guard
    tenant is provisioned through Guard's own Pilot provisioning (the same code
    the invitation flow uses), and only when a platform ADMIN enters first, so
    the first owner is someone the platform made an admin. A person migrating a
    legacy account into an organization whose Guard tenant is not linked yet is
    held (409) — a twin tenant is never created;
  * membership: the platform admin becomes Owner of a tenant that has none,
    otherwise Admin; everyone else Viewer (least privilege). Guard roles are
    product RBAC and are never overwritten afterwards.

MFA: the session counts as MFA-verified only when the operator attests that
the WorkOS environment enforces MFA (``DECODA_IDP_MFA_REQUIRED``) AND the
session was established by a method that attestation covers. Its
``authenticated_at`` is the moment the person actually authenticated
(``auth_time``, else the WorkOS session's creation) — never the exchange time —
so a silent re-exchange cannot refresh a step-up.
"""

from __future__ import annotations

import logging
import re
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, status

from .config import load_identity_settings
from .platform import PlatformUnavailable, ProductAccess
from .session_gate import bind_organization
from .tokens import IdentityKeysUnavailable, IdentityTokenError
from .workos_api import IdentityProviderUnavailable

logger = logging.getLogger(__name__)

# Methods an MFA-enforcing WorkOS environment covers: password sign-ins get the
# enforced second factor, passkeys are multi-factor by design, and SSO
# delegates MFA to the organization's own identity provider.
MFA_ASSURED_METHODS = frozenset({'password', 'passkey', 'sso'})

_JWT_SHAPE = re.compile(r'^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$')


def _error(status_code: int, code: str, message: str, **extra: Any) -> HTTPException:
    return HTTPException(status_code=status_code, detail={'code': code, 'message': message, **extra})


def _session_invalid() -> HTTPException:
    return _error(status.HTTP_401_UNAUTHORIZED, 'IDENTITY_SESSION_INVALID', 'Your Decoda sign-in is no longer valid. Please sign in again.')


def _unavailable() -> HTTPException:
    return _error(status.HTTP_503_SERVICE_UNAVAILABLE, 'IDENTITY_PROVIDER_UNAVAILABLE', 'Decoda sign-in is temporarily unavailable. Please try again shortly.')


def require_bff(request: Any) -> None:
    """Session exchange is for the Guard web BFF only (shared secret; required in production)."""
    import hmac

    shared = load_identity_settings().bff_shared_secret
    if not shared:
        return
    provided = request.headers.get('x-guard-proxy-secret', '') if request is not None else ''
    if not provided or not hmac.compare_digest(shared.encode(), provided.encode()):
        raise _error(status.HTTP_403_FORBIDDEN, 'BFF_REQUIRED', 'This endpoint is only available to the RWA Guard web application.')


def _authenticated_at(claims_auth_time: int | None, session_created_at: datetime | None) -> datetime:
    now = datetime.now(UTC)
    if claims_auth_time:
        return min(now, datetime.fromtimestamp(int(claims_auth_time), tz=UTC))
    if session_created_at is not None:
        created = session_created_at if session_created_at.tzinfo else session_created_at.replace(tzinfo=UTC)
        return min(now, created)
    # Unknown: treat as long ago, so no step-up window is opened by this exchange.
    return datetime.fromtimestamp(0, tz=UTC)


# ── Guard records ────────────────────────────────────────────────────────────


def _find_organization(connection: Any, access: ProductAccess) -> dict[str, Any] | None:
    row = connection.execute(
        'SELECT id, name, platform_organization_id, workos_organization_id FROM organizations WHERE platform_organization_id = %s FOR UPDATE',
        (access.platform_organization_id,),
    ).fetchone()
    if row is None:
        twin = connection.execute('SELECT id FROM organizations WHERE workos_organization_id = %s', (access.workos_organization_id,)).fetchone()
        if twin is not None:
            raise _error(status.HTTP_409_CONFLICT, 'ORGANIZATION_LINK_CONFLICT', 'This organization is linked inconsistently. Contact Decoda support.')
        return None
    row = dict(row)
    if row['workos_organization_id'] is None:
        connection.execute('UPDATE organizations SET workos_organization_id = %s, updated_at = NOW() WHERE id = %s', (access.workos_organization_id, row['id']))
        row['workos_organization_id'] = access.workos_organization_id
    elif row['workos_organization_id'] != access.workos_organization_id:
        raise _error(status.HTTP_409_CONFLICT, 'ORGANIZATION_LINK_CONFLICT', 'This organization is linked inconsistently. Contact Decoda support.')
    return row


def _link_legacy_organization(
    connection: Any, *, access: ProductAccess, legacy_organization_id: str, user_id: str, request: Any
) -> dict[str, Any]:
    """Link the Guard organization a reviewed import mapped to this platform organization."""
    from services.api.app import pilot

    try:
        target = str(uuid.UUID(legacy_organization_id))
    except ValueError:
        target = None
    row = (
        connection.execute(
            'SELECT id, name, platform_organization_id, workos_organization_id FROM organizations WHERE id = %s FOR UPDATE', (target,)
        ).fetchone()
        if target
        else None
    )
    if row is None or row['platform_organization_id'] is not None or row['workos_organization_id'] not in (None, access.workos_organization_id):
        # Missing, or already linked elsewhere: the reviewed mapping and Guard disagree.
        logger.warning('decoda_identity_legacy_organization_conflict legacy_organization_id=%s', legacy_organization_id)
        raise _error(status.HTTP_409_CONFLICT, 'ORGANIZATION_LINK_CONFLICT', 'This organization is linked inconsistently. Contact Decoda support.')
    connection.execute(
        'UPDATE organizations SET platform_organization_id = %s, workos_organization_id = %s, updated_at = NOW() WHERE id = %s',
        (access.platform_organization_id, access.workos_organization_id, row['id']),
    )
    pilot.log_audit(
        connection,
        action='organization.decoda_linked',
        entity_type='organization',
        entity_id=str(row['id']),
        request=request,
        user_id=user_id,
        workspace_id=None,
        metadata={'source': 'legacy_organization_link', 'platform_organization_id': access.platform_organization_id},
    )
    linked = dict(row)
    linked.update(platform_organization_id=access.platform_organization_id, workos_organization_id=access.workos_organization_id)
    return linked


def _ensure_user(
    connection: Any, *, workos_user_id: str, access: ProductAccess, legacy_link: dict[str, Any] | None, request: Any
) -> tuple[dict[str, Any], str]:
    from services.api.app import pilot

    user = connection.execute(
        "SELECT id, email, session_version, suspended_at FROM users WHERE auth_provider = 'workos' AND external_subject = %s FOR UPDATE",
        (workos_user_id,),
    ).fetchone()
    if user is not None:
        return dict(user), 'existing'

    if legacy_link is not None:
        legacy = connection.execute(
            'SELECT id, email, session_version, suspended_at, auth_provider FROM users WHERE id::text = %s FOR UPDATE',
            (str(legacy_link['legacy_user_id']),),
        ).fetchone()
        if legacy is None or legacy['auth_provider'] == 'workos':
            raise _error(
                status.HTTP_409_CONFLICT,
                'IDENTITY_LINK_CONFLICT',
                'Your existing RWA Guard account could not be linked automatically. Contact Decoda support to complete the migration.',
            )
        # Linking ends every session the password account still holds (a new
        # session version voids their tokens): from here on it signs in with Decoda.
        connection.execute(
            "UPDATE users SET auth_provider = 'workos', external_subject = %s, session_version = session_version + 1, updated_at = NOW() WHERE id = %s",
            (workos_user_id, legacy['id']),
        )
        connection.execute(
            '''
            UPDATE auth_sessions SET revoked_at = NOW(), updated_at = NOW(),
                   metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object('revoke_reason', 'identity_linked')
            WHERE user_id = %s AND revoked_at IS NULL
            ''',
            (legacy['id'],),
        )
        pilot.log_audit(
            connection,
            action='identity.user_linked',
            entity_type='user',
            entity_id=str(legacy['id']),
            request=request,
            user_id=str(legacy['id']),
            workspace_id=None,
            metadata={'source': 'legacy_identity_link', 'previous_auth_provider': legacy['auth_provider']},
        )
        linked = dict(legacy)
        linked['session_version'] = int(linked.get('session_version') or 1) + 1
        return linked, 'linked'

    email = (access.user_email or '').strip().lower()
    if not email:
        raise _error(status.HTTP_403_FORBIDDEN, 'IDENTITY_PROFILE_INCOMPLETE', 'Your Decoda account has no verified email address.')
    if connection.execute('SELECT 1 FROM users WHERE lower(email) = %s', (email,)).fetchone() is not None:
        # Same address, different identity: never merged automatically.
        raise _error(
            status.HTTP_409_CONFLICT,
            'IDENTITY_LINK_CONFLICT',
            'An existing RWA Guard account uses this email address and has not been linked to your Decoda account. '
            'Contact Decoda support to link it.',
        )
    user_id = str(uuid.uuid4())
    full_name = (access.user_name or '').strip() or email.split('@', 1)[0]
    connection.execute(
        '''
        INSERT INTO users (id, email, password_hash, full_name, current_workspace_id, email_verified_at,
                           auth_provider, external_subject, session_version, created_at, updated_at)
        VALUES (%s, %s, %s, %s, NULL, NOW(), 'workos', %s, 1, NOW(), NOW())
        ''',
        # An unusable random password, exactly like workspace-OIDC accounts: no
        # one knows it, and password sign-in refuses Decoda-linked accounts anyway.
        (user_id, email, pilot.hash_password(secrets.token_urlsafe(48)), full_name[:200], workos_user_id),
    )
    pilot.log_audit(
        connection,
        action='auth.signup',
        entity_type='user',
        entity_id=user_id,
        request=request,
        user_id=user_id,
        workspace_id=None,
        metadata={'source': 'decoda_identity', 'email_verified_by': 'decoda_identity', 'pilot_access_granted': False},
    )
    return {'id': user_id, 'email': email, 'session_version': 1, 'suspended_at': None}, 'created'


def _org_workspaces(connection: Any, organization_id: str) -> list[str]:
    rows = connection.execute('SELECT id FROM workspaces WHERE organization_id = %s ORDER BY created_at, id', (organization_id,)).fetchall()
    return [str(row['id']) for row in rows]


def _ensure_membership(connection: Any, *, organization_id: str, user_id: str, access: ProductAccess, request: Any) -> None:
    from services.api.app import organizations as organization_service
    from services.api.app import pilot

    if organization_service.membership_role(connection, organization_id=organization_id, user_id=user_id) is not None:
        return
    if access.organization_role == 'admin':
        has_owner = connection.execute(
            "SELECT 1 FROM organization_memberships WHERE organization_id = %s AND role = 'owner'", (organization_id,)
        ).fetchone()
        role = 'admin' if has_owner else 'owner'
    else:
        role = 'viewer'
    organization_service.upsert_membership(connection, organization_id=organization_id, user_id=user_id, role=role)
    for workspace_id in _org_workspaces(connection, organization_id):
        connection.execute(
            '''
            INSERT INTO workspace_members (id, workspace_id, user_id, role, created_at)
            VALUES (%s, %s, %s, %s, NOW())
            ON CONFLICT (workspace_id, user_id) DO NOTHING
            ''',
            (str(uuid.uuid4()), workspace_id, user_id, role),
        )
    pilot.log_audit(
        connection,
        action='member.provisioned',
        entity_type='organization',
        entity_id=organization_id,
        request=request,
        user_id=user_id,
        workspace_id=None,
        metadata={'source': 'decoda_identity', 'role': role, 'platform_role': access.organization_role},
    )


def _session_workspace(connection: Any, *, organization_id: str, user_id: str) -> str | None:
    """The user's current workspace if it belongs to this organization, else their first one in it."""
    current = connection.execute('SELECT current_workspace_id FROM users WHERE id = %s', (user_id,)).fetchone()
    rows = connection.execute(
        '''
        SELECT w.id FROM workspace_members wm JOIN workspaces w ON w.id = wm.workspace_id
        WHERE wm.user_id = %s AND w.organization_id = %s
        ORDER BY w.created_at, w.id
        ''',
        (user_id, organization_id),
    ).fetchall()
    member_of = [str(row['id']) for row in rows]
    if not member_of:
        return None
    current_id = str(current['current_workspace_id']) if current and current['current_workspace_id'] else None
    return current_id if current_id in member_of else member_of[0]


# ── The exchange ─────────────────────────────────────────────────────────────


def exchange_identity(payload: dict[str, Any], request: Any) -> dict[str, Any]:
    from services.api.app import pilot

    from . import get_identity_services

    settings = load_identity_settings()
    if not settings.uses_workos:
        raise _error(status.HTTP_410_GONE, 'IDENTITY_NOT_ENABLED', 'Decoda sign-in is not enabled for this RWA Guard deployment.')
    require_bff(request)
    access_token = str((payload or {}).get('access_token') or '')
    if len(access_token) > 8192 or not _JWT_SHAPE.match(access_token):
        raise _session_invalid()

    services = get_identity_services()
    try:
        claims = services.verifier.verify(access_token)
    except IdentityTokenError as exc:
        logger.info('decoda_identity_exchange_refused reason=%s', exc.code)
        raise _session_invalid() from None
    except IdentityKeysUnavailable:
        raise _unavailable() from None
    if not claims.organization_id:
        raise _error(status.HTTP_403_FORBIDDEN, 'ORGANIZATION_REQUIRED', 'Choose an organization to continue.')

    try:
        facts = services.sessions.find_session(claims.user_id, claims.session_id)
    except IdentityProviderUnavailable:
        raise _unavailable() from None
    if facts is None or not facts.active or facts.user_id != claims.user_id:
        logger.info('decoda_identity_exchange_refused reason=session_inactive')
        raise _session_invalid()
    if facts.impersonated:
        raise _error(status.HTTP_403_FORBIDDEN, 'IMPERSONATION_NOT_ALLOWED', 'Impersonated sessions cannot open Decoda RWA Guard.')

    try:
        access = services.directory.product_access(
            workos_user_id=claims.user_id,
            workos_organization_id=claims.organization_id,
            workos_session_id=claims.session_id,
            use_cache=False,
        )
        legacy_link = services.directory.legacy_link(claims.user_id) if access.granted else None
    except PlatformUnavailable:
        raise _error(
            status.HTTP_503_SERVICE_UNAVAILABLE, 'IDENTITY_DIRECTORY_UNAVAILABLE', 'Decoda sign-in is temporarily unavailable. Please try again shortly.'
        ) from None
    if access.session_revoked:
        raise _session_invalid()
    if not access.granted:
        logger.info('decoda_identity_exchange_refused reason=%s', access.access_state)
        raise _error(status.HTTP_403_FORBIDDEN, 'PRODUCT_ACCESS_DENIED', access.denial_message, reason=access.access_state)

    mfa_verified = settings.idp_mfa_required and facts.auth_method in MFA_ASSURED_METHODS and not facts.impersonated
    authenticated_at = _authenticated_at(claims.auth_time, facts.created_at)
    methods = ['workos', facts.auth_method] + (['idp_mfa'] if mfa_verified else [])

    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        # Serialize first entries into the same organization (provisioning race).
        connection.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))', (f'guard.identity.org:{access.platform_organization_id}',))
        organization = _find_organization(connection, access)
        legacy_organization_id = None
        if organization is None:
            try:
                legacy_organization_id = services.directory.legacy_organization(access.platform_organization_id)
            except PlatformUnavailable:
                raise _error(
                    status.HTTP_503_SERVICE_UNAVAILABLE,
                    'IDENTITY_DIRECTORY_UNAVAILABLE',
                    'Decoda sign-in is temporarily unavailable. Please try again shortly.',
                ) from None
        if (
            organization is None
            and legacy_organization_id is None
            and legacy_link is not None
            and str(legacy_link.get('platform_organization_id')) == access.platform_organization_id
        ):
            raise _error(
                status.HTTP_409_CONFLICT,
                'ORGANIZATION_LINK_REQUIRED',
                'Your organization is being migrated to Decoda sign-in. Contact Decoda support to finish linking it.',
            )
        if organization is None and legacy_organization_id is None and access.organization_role != 'admin':
            raise _error(
                status.HTTP_409_CONFLICT,
                'ORGANIZATION_NOT_READY',
                "Your organization's Decoda administrator must open RWA Guard first to set it up.",
            )

        user, resolution = _ensure_user(connection, workos_user_id=claims.user_id, access=access, legacy_link=legacy_link, request=request)
        user_id = str(user['id'])
        if user.get('suspended_at'):
            raise _error(status.HTTP_403_FORBIDDEN, 'USER_DISABLED', 'Your RWA Guard access is disabled. Contact your organization administrator.')

        if organization is None and legacy_organization_id is not None:
            organization = _link_legacy_organization(
                connection, access=access, legacy_organization_id=legacy_organization_id, user_id=user_id, request=request
            )
        if organization is None:
            provisioned = pilot.provision_pilot_organization(
                connection, user_id=user_id, organization_name=access.organization_name or 'Organization', request=request
            )
            organization_id = str(provisioned['organization']['id'])
            connection.execute(
                'UPDATE organizations SET platform_organization_id = %s, workos_organization_id = %s, updated_at = NOW() WHERE id = %s',
                (access.platform_organization_id, access.workos_organization_id, organization_id),
            )
            pilot.log_audit(
                connection,
                action='organization.decoda_linked',
                entity_type='organization',
                entity_id=organization_id,
                request=request,
                user_id=user_id,
                workspace_id=provisioned['workspace_id'],
                metadata={'source': 'decoda_identity', 'platform_organization_id': access.platform_organization_id},
            )
        else:
            organization_id = str(organization['id'])
            _ensure_membership(connection, organization_id=organization_id, user_id=user_id, access=access, request=request)

        workspace_id = _session_workspace(connection, organization_id=organization_id, user_id=user_id)
        if workspace_id is None:
            raise _error(
                status.HTTP_403_FORBIDDEN,
                'NO_WORKSPACE_ACCESS',
                'You are a member of this organization but of none of its RWA Guard workspaces. Ask an owner to add you.',
            )
        connection.execute(
            'UPDATE users SET current_workspace_id = %s, last_sign_in_at = NOW(), updated_at = NOW() WHERE id = %s',
            (workspace_id, user_id),
        )

        token = pilot.create_access_token(user_id, int(user.get('session_version') or 1))
        pilot._store_session(connection, user_id, token, workspace_id, request=request)
        token_hash = pilot._auth_token_hash(token)
        metadata = {
            'workos_user_id': claims.user_id,
            'workos_organization_id': access.workos_organization_id,
            'platform_organization_id': access.platform_organization_id,
            'guard_organization_id': organization_id,
            'identity_auth_method': facts.auth_method,
        }
        connection.execute(
            '''
            UPDATE auth_sessions
            SET auth_mode = 'workos', workos_session_id = %s, authenticated_at = %s,
                mfa_verified_at = %s, authentication_methods = %s::jsonb, metadata = %s::jsonb, updated_at = NOW()
            WHERE session_token_hash = %s
            ''',
            (
                claims.session_id,
                authenticated_at,
                authenticated_at if mfa_verified else None,
                pilot._json_dumps(methods),
                pilot._json_dumps(metadata),
                token_hash,
            ),
        )
        superseded = connection.execute(
            '''
            UPDATE auth_sessions SET revoked_at = NOW(), updated_at = NOW(),
                   metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object('revoke_reason', 'superseded')
            WHERE workos_session_id = %s AND session_token_hash <> %s AND revoked_at IS NULL
            RETURNING session_token_hash
            ''',
            (claims.session_id, token_hash),
        ).fetchall()
        pilot.log_audit(
            connection,
            action='auth.signin',
            entity_type='user',
            entity_id=user_id,
            request=request,
            user_id=user_id,
            workspace_id=workspace_id,
            metadata={
                'method': 'decoda_identity',
                'identity_auth_method': facts.auth_method,
                'mfa_verified': mfa_verified,
                'resolution': resolution,
                'superseded_sessions': len(superseded),
            },
        )
        connection.commit()
        bind_organization(organization_id)
        hydrated = pilot.build_user_response(connection, user_id)
        connection.commit()
    for row in superseded:
        pilot._blacklist_session_token(str(row['session_token_hash']), pilot.SESSION_TTL_HOURS * 3600)
    services.directory.invalidate(workos_session_id=claims.session_id)
    return {'access_token': token, 'token_type': 'bearer', 'user': hydrated}
