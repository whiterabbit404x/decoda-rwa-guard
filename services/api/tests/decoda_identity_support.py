"""Test doubles and fixtures for the shared Decoda identity (RWA Guard).

Only the two things that live outside our systems are faked:
  * WorkOS signing keys  — a local RSA key pair; tokens are real RS256 JWTs and
                           go through the production verifier unchanged;
  * WorkOS session API   — an in-memory table of sessions.

The Decoda platform is NOT faked in the PostgreSQL tests: the platform schema
(vendored migration, ``fixtures/decoda_platform_0001.sql``) is created in the
test database and Guard reads its real ``platform_api`` views.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from services.api.app.decoda_identity.workos_api import IdentityProviderUnavailable, SessionFacts

CLIENT_ID = 'client_01GUARDTEST'
ISSUER = f'https://api.workos.com/user_management/{CLIENT_ID}'
KID = 'sso_oidc_key_pair_01GUARD'
BFF_SECRET = 'test-guard-bff-shared-secret-0123456789abcdef'
PLATFORM_SQL = (
    Path(__file__).parent / 'fixtures' / 'decoda_platform_0001.sql',
    Path(__file__).parent / 'fixtures' / 'decoda_platform_0002.sql',
)

PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)

IDENTITY_ENV = {
    'GUARD_IDENTITY_MODE': 'workos',
    'WORKOS_CLIENT_ID': CLIENT_ID,
    'WORKOS_API_KEY': 'sk_test_' + 'x' * 24,
    'WORKOS_ISSUER': ISSUER,
    'DECODA_IDP_MFA_REQUIRED': 'true',
    'GUARD_BFF_SHARED_SECRET': BFF_SECRET,
}

IDENTITY_ENV_NAMES = (
    *IDENTITY_ENV,
    'DECODA_PLATFORM_DATABASE_URL',
    'GUARD_LEGACY_PASSWORD_SUNSET',
    'DECODA_ACCESS_CACHE_TTL_SECONDS',
    'DECODA_WEBSITE_URL',
)


def workos_id(prefix: str) -> str:
    return f'{prefix}_{uuid.uuid4().hex[:24].upper()}'


class StaticKeys:
    """Stands in for PyJWKClient: one known key id."""

    def __init__(self) -> None:
        self.unavailable = False

    def get_signing_key_from_jwt(self, token: str) -> Any:
        if self.unavailable:
            raise jwt.PyJWKClientConnectionError('JWKS endpoint unreachable')
        header = jwt.get_unverified_header(token)
        if header.get('kid') != KID:
            raise jwt.PyJWKClientError('Unable to find a signing key that matches')
        return jwt.PyJWK.from_dict(
            {**jwt.algorithms.RSAAlgorithm.to_jwk(PRIVATE_KEY.public_key(), as_dict=True), 'kid': KID, 'alg': 'RS256', 'use': 'sig'}
        )


def mint_token(
    *,
    user_id: str,
    session_id: str,
    org_id: str | None,
    issuer: str = ISSUER,
    expires_in: int = 300,
    key: Any = None,
    kid: str = KID,
    extra: dict[str, Any] | None = None,
    drop: tuple[str, ...] = (),
) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        'iss': issuer,
        'sub': user_id,
        'sid': session_id,
        'iat': now,
        'exp': now + expires_in,
        'jti': uuid.uuid4().hex,
    }
    if org_id is not None:
        claims['org_id'] = org_id
        claims['role'] = 'member'
    claims.update(extra or {})
    for name in drop:
        claims.pop(name, None)
    return jwt.encode(claims, key or PRIVATE_KEY, algorithm='RS256', headers={'kid': kid})


@dataclass
class FakeSessions:
    sessions: dict[str, SessionFacts] = field(default_factory=dict)
    unavailable: bool = False
    calls: list[tuple[str, str]] = field(default_factory=list)

    def add(
        self,
        user_id: str,
        *,
        auth_method: str = 'password',
        status: str = 'active',
        impersonated: bool = False,
        created_at: datetime | None = None,
    ) -> str:
        session_id = workos_id('session')
        self.sessions[session_id] = SessionFacts(
            session_id=session_id,
            user_id=user_id,
            status=status,
            auth_method=auth_method,
            impersonated=impersonated,
            organization_id=None,
            created_at=created_at,
        )
        return session_id

    def find_session(self, user_id: str, session_id: str) -> SessionFacts | None:
        self.calls.append((user_id, session_id))
        if self.unavailable:
            raise IdentityProviderUnavailable('WorkOS unreachable')
        facts = self.sessions.get(session_id)
        return facts if facts is not None and facts.user_id == user_id else None


class ManualClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# ── Platform fixture (real schema, real views) ──────────────────────────────


def install_platform_schema(psycopg: Any, url: str) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute('DROP SCHEMA IF EXISTS platform_api CASCADE')
        conn.execute('DROP SCHEMA IF EXISTS platform CASCADE')
        for path in PLATFORM_SQL:
            conn.execute(path.read_text(encoding='utf-8'))


def drop_platform_schema(psycopg: Any, url: str) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute('DROP SCHEMA IF EXISTS platform_api CASCADE')
        conn.execute('DROP SCHEMA IF EXISTS platform CASCADE')


@dataclass
class PlatformOrg:
    id: str
    workos_id: str
    name: str
    slug: str


@dataclass
class PlatformUser:
    id: str
    workos_id: str
    email: str


class Platform:
    """Writes platform rows the way the website's provisioning/webhooks would."""

    def __init__(self, psycopg: Any, url: str):
        self.psycopg = psycopg
        self.url = url

    def sql(self, query: str, params: tuple | list = ()) -> list[dict[str, Any]]:
        from psycopg.rows import dict_row

        with self.psycopg.connect(self.url, autocommit=True, row_factory=dict_row) as conn:
            cursor = conn.execute(query, params)
            return list(cursor.fetchall()) if cursor.description else []

    def org(self, name: str | None = None, *, status: str = 'active', guard: str | None = 'pilot', **entitlement: Any) -> PlatformOrg:
        suffix = uuid.uuid4().hex[:8]
        name = name or f'Harbor Trust {suffix}'
        slug = f'harbor-trust-{suffix}'
        row = self.sql(
            'INSERT INTO platform.organizations (workos_organization_id, name, slug, status) VALUES (%s, %s, %s, %s) RETURNING id',
            (workos_id('org'), name, slug, status),
        )[0]
        org = self.sql('SELECT id, workos_organization_id, name, slug FROM platform.organizations WHERE id = %s', (row['id'],))[0]
        result = PlatformOrg(id=str(org['id']), workos_id=org['workos_organization_id'], name=org['name'], slug=org['slug'])
        if guard is not None:
            self.entitle(result, 'rwa_guard', guard, **entitlement)
        return result

    def entitle(self, org: PlatformOrg, product: str, status: str, *, starts_at: str | None = None, expires_at: str | None = None) -> None:
        self.sql(
            """
            INSERT INTO platform.organization_product_entitlements (organization_id, product, status, starts_at, expires_at)
            VALUES (%s, %s, %s, COALESCE(%s::timestamptz, now()), %s::timestamptz)
            ON CONFLICT (organization_id, product)
            DO UPDATE SET status = EXCLUDED.status, starts_at = EXCLUDED.starts_at, expires_at = EXCLUDED.expires_at, updated_at = now()
            """,
            (org.id, product, status, starts_at, expires_at),
        )

    def user(self, *, email: str | None = None, first: str = 'Morgan', last: str = 'Diaz', status: str = 'active') -> PlatformUser:
        email = email or f'morgan.{uuid.uuid4().hex[:10]}@harbor-trust.test'
        row = self.sql(
            'INSERT INTO platform.users (workos_user_id, email, email_verified, first_name, last_name, status) '
            'VALUES (%s, %s, TRUE, %s, %s, %s) RETURNING id, workos_user_id, email',
            (workos_id('user'), email.lower(), first, last, status),
        )[0]
        return PlatformUser(id=str(row['id']), workos_id=row['workos_user_id'], email=row['email'])

    def member(self, org: PlatformOrg, user: PlatformUser, *, role: str = 'member', status: str = 'active') -> None:
        self.sql(
            """
            INSERT INTO platform.organization_memberships (organization_id, user_id, workos_membership_id, role, status, source)
            VALUES (%s, %s, %s, %s, %s, 'invitation')
            ON CONFLICT (organization_id, user_id) DO UPDATE SET role = EXCLUDED.role, status = EXCLUDED.status, updated_at = now()
            """,
            (org.id, user.id, workos_id('om'), role, status),
        )

    def revoke(self, user: PlatformUser, session_id: str) -> None:
        self.sql(
            "INSERT INTO platform.workos_session_revocations (workos_session_id, workos_user_id, revoked_at, source) VALUES (%s, %s, now(), 'webhook')",
            (session_id, user.workos_id),
        )

    def legacy_organization_link(self, org: PlatformOrg, legacy_organization_id: str) -> None:
        self.sql(
            "INSERT INTO platform.legacy_organization_links (product, legacy_organization_id, organization_id, manifest_digest) "
            "VALUES ('rwa_guard', %s, %s, %s)",
            (legacy_organization_id, org.id, 'a' * 64),
        )

    def legacy_link(self, org: PlatformOrg, user: PlatformUser, legacy_user_id: str) -> None:
        self.sql(
            """
            INSERT INTO platform.legacy_identity_links (product, legacy_user_id, email, organization_id, workos_user_id, status, manifest_digest)
            VALUES ('rwa_guard', %s, %s, %s, %s, 'linked', %s)
            """,
            (legacy_user_id, user.email, org.id, user.workos_id, 'a' * 64),
        )
