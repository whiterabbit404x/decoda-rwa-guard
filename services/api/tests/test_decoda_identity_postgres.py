"""Shared Decoda identity for RWA Guard, end to end against real PostgreSQL.

Guard's migrated schema and the Decoda platform schema (vendored migration)
live in the same disposable database; the real FastAPI app is driven through
its HTTP surface exactly as the web BFF drives it. Only WorkOS is faked (a
local RSA signing key and an in-memory session table).

Covers: session exchange and tenant bootstrap, least-privilege membership,
MFA assurance, fail-closed token / session / platform checks, per-request
re-validation (entitlement, membership, revocation), BFF session binding,
tenant isolation, sign-out, reviewed legacy-account linking, and the identity
modes (`legacy` / `dual` / `workos`).

Run with a disposable, EMPTY database:

    DECODA_MIGRATION_TEST_DSN=postgresql://…/scratch \\
      python -m pytest services/api/tests/test_decoda_identity_postgres.py -q

Skipped entirely when that DSN is absent, so the default suite stays hermetic.
"""

from __future__ import annotations

import os
import pathlib
import sys
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

_DSN = os.environ.get('DECODA_MIGRATION_TEST_DSN')


def _real_psycopg():
    module = sys.modules.get('psycopg')
    if module is not None and not hasattr(module, 'rows'):
        for name in [n for n in list(sys.modules) if n == 'psycopg' or n.startswith('psycopg.')]:
            del sys.modules[name]
    return pytest.importorskip('psycopg')


psycopg = _real_psycopg() if _DSN else None

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not _DSN, reason='set DECODA_MIGRATION_TEST_DSN (a disposable/empty PostgreSQL database) to run'),
]

from services.api.tests.decoda_identity_support import (  # noqa: E402
    BFF_SECRET,
    CLIENT_ID,
    IDENTITY_ENV,
    IDENTITY_ENV_NAMES,
    ISSUER,
    OTHER_PRIVATE_KEY,
    FakeSessions,
    ManualClock,
    Platform,
    PlatformOrg,
    PlatformUser,
    StaticKeys,
    drop_platform_schema,
    install_platform_schema,
    mint_token,
    workos_id,
)

_MIGRATIONS = pathlib.Path(__file__).resolve().parents[1] / 'migrations'
_PASSWORD = 'Str0ng!Passw0rd#2026'


@pytest.fixture(scope='module')
def database():
    from psycopg.rows import dict_row

    with psycopg.connect(_DSN, autocommit=True) as connection:
        connection.execute('DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;')
        for path in sorted(_MIGRATIONS.glob('*.sql')):
            connection.execute(path.read_text())
    install_platform_schema(psycopg, _DSN)
    with psycopg.connect(_DSN, autocommit=True, row_factory=dict_row) as connection:
        yield connection
    drop_platform_schema(psycopg, _DSN)
    with psycopg.connect(_DSN, autocommit=True) as connection:
        connection.execute('DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;')


@dataclass
class GuardSession:
    client: Any
    access_token: str
    workos_session_id: str
    user: dict[str, Any]
    csrf: str | None = None

    def headers(self, extra: dict[str, str] | None = None, *, bff: bool = True) -> dict[str, str]:
        headers = {'authorization': f'Bearer {self.access_token}'}
        if bff:
            headers['x-guard-proxy-secret'] = BFF_SECRET
            headers['x-guard-identity-session'] = self.workos_session_id
        headers.update(extra or {})
        return headers

    def get(self, path: str, *, workspace: str | None = None, **kwargs: Any):
        extra = {'x-workspace-id': workspace} if workspace else {}
        return self.client.get(path, headers=self.headers({**extra, **kwargs.pop('headers', {})}), **kwargs)

    def post(self, path: str, json: dict[str, Any] | None = None, *, workspace: str | None = None, **kwargs: Any):
        if self.csrf is None:
            self.csrf = self.client.get('/auth/csrf-token').json()['csrf_token']
        extra = {'x-csrf-token': self.csrf, **({'x-workspace-id': workspace} if workspace else {})}
        return self.client.post(path, json=json or {}, headers=self.headers({**extra, **kwargs.pop('headers', {})}), **kwargs)


@dataclass
class Identity:
    client: Any
    db: Any
    platform: Platform
    sessions: FakeSessions
    keys: StaticKeys
    clock: ManualClock
    directory: Any
    env: Any

    def exchange(self, token: str, *, headers: dict[str, str] | None = None):
        return self.client.post(
            '/auth/identity/exchange', json={'access_token': token}, headers={'x-guard-proxy-secret': BFF_SECRET, **(headers or {})}
        )

    def sign_in(self, user: PlatformUser, org: PlatformOrg | None, *, auth_method: str = 'password', created_at=None, **token_args: Any):
        session_id = self.sessions.add(user.workos_id, auth_method=auth_method, created_at=created_at)
        token = mint_token(user_id=user.workos_id, session_id=session_id, org_id=org.workos_id if org else None, **token_args)
        return session_id, self.exchange(token)

    def session(self, user: PlatformUser, org: PlatformOrg, *, auth_method: str = 'password') -> GuardSession:
        session_id, response = self.sign_in(user, org, auth_method=auth_method)
        assert response.status_code == 200, response.text
        body = response.json()
        return GuardSession(self.client, body['access_token'], session_id, body['user'])

    def guard_org(self, org: PlatformOrg) -> dict[str, Any] | None:
        return self.db.execute('SELECT * FROM organizations WHERE platform_organization_id = %s', (org.id,)).fetchone()

    def guard_user(self, user: PlatformUser) -> dict[str, Any] | None:
        return self.db.execute(
            "SELECT * FROM users WHERE auth_provider = 'workos' AND external_subject = %s", (user.workos_id,)
        ).fetchone()

    def auth_session(self, session: GuardSession) -> dict[str, Any]:
        from services.api.app import pilot

        return self.db.execute(
            'SELECT * FROM auth_sessions WHERE session_token_hash = %s', (pilot._auth_token_hash(session.access_token),)
        ).fetchone()

    def audit(self, *, user_id: str) -> list[str]:
        rows = self.db.execute('SELECT action FROM audit_logs WHERE user_id = %s ORDER BY created_at, id', (user_id,)).fetchall()
        return [row['action'] for row in rows]

    def expire_cache(self) -> None:
        self.clock.advance(16)


@pytest.fixture
def identity(database, monkeypatch):
    from fastapi.testclient import TestClient

    from services.api.app import main as api_main
    from services.api.app.decoda_identity import IdentityServices, set_identity_services
    from services.api.app.decoda_identity.platform import PlatformDirectory
    from services.api.app.decoda_identity.session_gate import bind_organization
    from services.api.app.decoda_identity.tokens import IdentityTokenVerifier

    for name in (*IDENTITY_ENV_NAMES, 'APP_ENV'):
        monkeypatch.delenv(name, raising=False)
    for name, value in {
        'DATABASE_URL': _DSN,
        'LIVE_MODE_ENABLED': 'true',
        'APP_MODE': 'live',
        'EMAIL_PROVIDER': 'console',
        'AUTH_TOKEN_SECRET': 'x' * 48,
        **IDENTITY_ENV,
        'DECODA_PLATFORM_DATABASE_URL': _DSN,
    }.items():
        monkeypatch.setenv(name, value)
    keys, sessions, clock = StaticKeys(), FakeSessions(), ManualClock()
    directory = PlatformDirectory(_DSN, cache_ttl_seconds=15, clock=clock)
    set_identity_services(
        IdentityServices(verifier=IdentityTokenVerifier(keys=keys, client_id=CLIENT_ID, issuer=(ISSUER,)), sessions=sessions, directory=directory)
    )
    yield Identity(
        client=TestClient(api_main.app),
        db=database,
        platform=Platform(psycopg, _DSN),
        sessions=sessions,
        keys=keys,
        clock=clock,
        directory=directory,
        env=monkeypatch,
    )
    set_identity_services(None)
    bind_organization(None)


def _org_with_admin(identity: Identity, **entitlement: Any) -> tuple[PlatformOrg, PlatformUser]:
    org = identity.platform.org(**entitlement)
    admin = identity.platform.user()
    identity.platform.member(org, admin, role='admin')
    return org, admin


def _detail(response) -> dict[str, Any]:
    body = response.json()
    detail = body.get('detail', body)
    return detail if isinstance(detail, dict) else {'message': detail}


def _request(token: str = 'bootstrap') -> SimpleNamespace:
    return SimpleNamespace(
        headers={'authorization': f'Bearer {token}', 'user-agent': 'identity-tests'},
        client=SimpleNamespace(host='127.0.0.1'),
        scope={'path': '/identity-tests', 'type': 'http'},
        method='POST',
        query_params={},
    )


def _legacy_account(identity: Identity, *, email: str | None = None, with_organization: bool = True) -> dict[str, Any]:
    """A pre-existing RWA Guard password account (created while GUARD_IDENTITY_MODE=legacy)."""
    from services.api.app import pilot

    email = email or f'legacy.{uuid.uuid4().hex[:8]}@harbor-trust.test'
    previous = os.environ.get('GUARD_IDENTITY_MODE')
    identity.env.setenv('GUARD_IDENTITY_MODE', 'legacy')
    try:
        created = pilot.signup_user({'email': email, 'password': _PASSWORD, 'full_name': 'Legacy Operator'}, _request())
    finally:
        identity.env.setenv('GUARD_IDENTITY_MODE', previous or 'workos')
    user_id = str(created['user']['id'])
    identity.db.execute('UPDATE users SET email_verified_at = NOW() WHERE id = %s', (user_id,))
    organization_id = None
    if with_organization:
        with pilot.pg_connection() as connection:
            provisioned = pilot.provision_pilot_organization(connection, user_id=user_id, organization_name='Legacy Harbor', request=None)
            connection.commit()
        organization_id = str(provisioned['organization']['id'])
    return {'id': user_id, 'email': email, 'organization_id': organization_id}


# ── Exchange: bootstrap and linking ─────────────────────────────────────────


def test_first_admin_entry_bootstraps_a_linked_pilot_tenant(identity):
    org, admin = _org_with_admin(identity)
    session = identity.session(admin, org)

    guard_org = identity.guard_org(org)
    assert guard_org is not None and guard_org['workos_organization_id'] == org.workos_id and guard_org['plan'] == 'pilot'
    guard_user = identity.guard_user(admin)
    assert guard_user['email'] == admin.email and guard_user['email_verified_at'] is not None
    role = identity.db.execute(
        'SELECT role FROM organization_memberships WHERE organization_id = %s AND user_id = %s', (guard_org['id'], guard_user['id'])
    ).fetchone()
    assert role['role'] == 'owner'

    row = identity.auth_session(session)
    assert row['auth_mode'] == 'workos' and row['workos_session_id'] == session.workos_session_id
    assert row['mfa_verified_at'] is not None and 'idp_mfa' in row['authentication_methods']
    assert row['metadata']['guard_organization_id'] == str(guard_org['id'])
    assert row['metadata']['platform_organization_id'] == org.id
    assert {'auth.signup', 'organization.pilot_created', 'organization.decoda_linked', 'auth.signin'} <= set(
        identity.audit(user_id=str(guard_user['id']))
    )

    me = session.get('/auth/me')
    assert me.status_code == 200, me.text
    user = me.json()['user']
    assert user['identity'] == {'mode': 'workos', 'auth_method': 'workos', 'legacy_password_sunset': None}
    assert user['mfa']['satisfied'] is True and user['mfa']['session_verified'] is True
    assert [m['workspace_id'] for m in user['memberships']] == [user['current_workspace']['id']]
    assert 'access_token' not in me.text


def test_members_join_with_least_privilege_and_roles_are_never_overwritten(identity):
    org, admin = _org_with_admin(identity)
    member = identity.platform.user()
    identity.platform.member(org, member, role='member')

    _, early = identity.sign_in(member, org)
    assert early.status_code == 409 and _detail(early)['code'] == 'ORGANIZATION_NOT_READY'
    assert identity.guard_user(member) is None

    identity.session(admin, org)
    identity.session(member, org)
    guard_org, guard_member = identity.guard_org(org), identity.guard_user(member)
    membership = 'SELECT role FROM organization_memberships WHERE organization_id = %s AND user_id = %s'
    assert identity.db.execute(membership, (guard_org['id'], guard_member['id'])).fetchone()['role'] == 'viewer'
    workspace_roles = identity.db.execute('SELECT role FROM workspace_members WHERE user_id = %s', (guard_member['id'],)).fetchall()
    assert [row['role'] for row in workspace_roles] == ['viewer']

    # A Guard owner promotes the member; the platform never overwrites product RBAC.
    identity.db.execute("UPDATE organization_memberships SET role = 'analyst' WHERE user_id = %s", (guard_member['id'],))
    identity.platform.member(org, member, role='admin')
    identity.session(member, org)
    assert identity.db.execute(membership, (guard_org['id'], guard_member['id'])).fetchone()['role'] == 'analyst'


def test_a_second_platform_admin_becomes_admin_not_owner(identity):
    org, admin = _org_with_admin(identity)
    other_admin = identity.platform.user()
    identity.platform.member(org, other_admin, role='admin')
    identity.session(admin, org)
    identity.session(other_admin, org)
    row = identity.db.execute(
        'SELECT role FROM organization_memberships WHERE user_id = %s', (identity.guard_user(other_admin)['id'],)
    ).fetchone()
    assert row['role'] == 'admin'


@pytest.mark.parametrize(
    ('auth_method', 'attested', 'verified'),
    [('password', True, True), ('sso', True, True), ('passkey', True, True), ('magic_code', True, False), ('password', False, False)],
)
def test_mfa_assurance_depends_on_method_and_attestation(identity, auth_method, attested, verified):
    if not attested:
        identity.env.delenv('DECODA_IDP_MFA_REQUIRED')
    org, admin = _org_with_admin(identity)
    row = identity.auth_session(identity.session(admin, org, auth_method=auth_method))
    assert (row['mfa_verified_at'] is not None) is verified
    assert ('idp_mfa' in row['authentication_methods']) is verified
    assert row['metadata']['identity_auth_method'] == auth_method


def test_authenticated_at_is_the_sign_in_not_the_exchange(identity):
    org, admin = _org_with_admin(identity)
    signed_in_at = int(time.time()) - 3600
    session_id, response = identity.sign_in(admin, org, extra={'auth_time': signed_in_at})
    assert response.status_code == 200
    row = identity.db.execute('SELECT authenticated_at, mfa_verified_at FROM auth_sessions WHERE workos_session_id = %s', (session_id,)).fetchone()
    assert int(row['authenticated_at'].timestamp()) == signed_in_at == int(row['mfa_verified_at'].timestamp())

    # Without auth_time the WorkOS session's own creation time is used.
    created = datetime.now(UTC) - timedelta(minutes=45)
    session_id, response = identity.sign_in(admin, org, created_at=created)
    row = identity.db.execute('SELECT authenticated_at FROM auth_sessions WHERE workos_session_id = %s', (session_id,)).fetchone()
    assert abs(row['authenticated_at'] - created) < timedelta(seconds=1)


def test_re_exchange_supersedes_the_previous_guard_session(identity):
    org, admin = _org_with_admin(identity)
    first = identity.session(admin, org)
    token = mint_token(user_id=admin.workos_id, session_id=first.workos_session_id, org_id=org.workos_id)
    second = identity.exchange(token)
    assert second.status_code == 200
    assert identity.auth_session(first)['revoked_at'] is not None
    assert first.get('/auth/me').status_code == 401


# ── Exchange: fail closed ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ('variant', 'status_code', 'code'),
    [
        ({'expires_in': -120}, 401, 'IDENTITY_SESSION_INVALID'),
        ({'key': OTHER_PRIVATE_KEY}, 401, 'IDENTITY_SESSION_INVALID'),
        ({'issuer': 'https://api.workos.com/user_management/client_OTHER'}, 401, 'IDENTITY_SESSION_INVALID'),
        ({'org': None}, 403, 'ORGANIZATION_REQUIRED'),
    ],
)
def test_invalid_tokens_are_refused(identity, variant, status_code, code):
    org, admin = _org_with_admin(identity)
    session_id = identity.sessions.add(admin.workos_id)
    variant = dict(variant)
    org_id = variant.pop('org', org.workos_id)
    response = identity.exchange(mint_token(user_id=admin.workos_id, session_id=session_id, org_id=org_id, **variant))
    assert response.status_code == status_code and _detail(response)['code'] == code
    assert identity.guard_org(org) is None


@pytest.mark.parametrize('state', ['revoked', 'expired'])
def test_the_workos_session_must_be_live(identity, state):
    org, admin = _org_with_admin(identity)
    session_id = identity.sessions.add(admin.workos_id, status=state)
    response = identity.exchange(mint_token(user_id=admin.workos_id, session_id=session_id, org_id=org.workos_id))
    assert response.status_code == 401 and _detail(response)['code'] == 'IDENTITY_SESSION_INVALID'


def test_impersonated_sessions_are_refused(identity):
    org, admin = _org_with_admin(identity)
    session_id = identity.sessions.add(admin.workos_id, auth_method='impersonation', impersonated=True)
    response = identity.exchange(mint_token(user_id=admin.workos_id, session_id=session_id, org_id=org.workos_id))
    assert response.status_code == 403 and _detail(response)['code'] == 'IMPERSONATION_NOT_ALLOWED'


def test_identity_provider_outage_fails_closed(identity):
    org, admin = _org_with_admin(identity)
    identity.sessions.unavailable = True
    _, response = identity.sign_in(admin, org)
    assert response.status_code == 503 and _detail(response)['code'] == 'IDENTITY_PROVIDER_UNAVAILABLE'
    identity.keys.unavailable = True
    identity.sessions.unavailable = False
    _, response = identity.sign_in(admin, org)
    assert response.status_code == 503


@pytest.mark.parametrize(
    ('setup', 'reason'),
    [
        (lambda p, org, user: p.entitle(org, 'rwa_guard', 'disabled'), 'not_entitled'),
        (lambda p, org, user: p.entitle(org, 'rwa_guard', 'suspended'), 'entitlement_suspended'),
        (lambda p, org, user: p.entitle(org, 'rwa_guard', 'enabled', starts_at='2019-01-01', expires_at='2020-01-01'), 'entitlement_expired'),
        (lambda p, org, user: p.entitle(org, 'rwa_guard', 'enabled', starts_at='2999-01-01'), 'entitlement_not_started'),
        (lambda p, org, user: p.sql("UPDATE platform.organizations SET status = 'suspended' WHERE id = %s", (org.id,)), 'organization_inactive'),
        (lambda p, org, user: p.member(org, user, role='admin', status='inactive'), 'membership_inactive'),
    ],
)
def test_platform_access_is_required(identity, setup, reason):
    org, admin = _org_with_admin(identity)
    setup(identity.platform, org, admin)
    _, response = identity.sign_in(admin, org)
    assert response.status_code == 403
    assert _detail(response)['code'] == 'PRODUCT_ACCESS_DENIED' and _detail(response)['reason'] == reason
    assert identity.guard_org(org) is None and identity.guard_user(admin) is None


def test_a_vault_entitlement_alone_does_not_open_guard(identity):
    org = identity.platform.org(guard=None)
    identity.platform.entitle(org, 'vault', 'enabled')
    admin = identity.platform.user()
    identity.platform.member(org, admin, role='admin')
    _, response = identity.sign_in(admin, org)
    assert response.status_code == 403 and _detail(response)['reason'] == 'not_entitled'


def test_a_non_member_is_refused(identity):
    org, _admin = _org_with_admin(identity)
    stranger = identity.platform.user()
    _, response = identity.sign_in(stranger, org)
    assert response.status_code == 403 and _detail(response)['reason'] == 'no_membership'


def test_a_revoked_workos_session_cannot_be_exchanged(identity):
    org, admin = _org_with_admin(identity)
    session_id = identity.sessions.add(admin.workos_id)
    identity.platform.revoke(admin, session_id)
    response = identity.exchange(mint_token(user_id=admin.workos_id, session_id=session_id, org_id=org.workos_id))
    assert response.status_code == 401


def test_platform_outage_fails_closed(identity):
    from services.api.app.decoda_identity import IdentityServices, get_identity_services, set_identity_services
    from services.api.app.decoda_identity.platform import PlatformDirectory

    org, admin = _org_with_admin(identity)
    session = identity.session(admin, org)
    current = get_identity_services()
    set_identity_services(
        IdentityServices(
            verifier=current.verifier,
            sessions=current.sessions,
            directory=PlatformDirectory('postgresql://nobody:x@127.0.0.1:9/none', connect_timeout_seconds=1),
        )
    )
    _, response = identity.sign_in(admin, org)
    assert response.status_code == 503 and _detail(response)['code'] == 'IDENTITY_DIRECTORY_UNAVAILABLE'
    live = session.get('/auth/me')
    assert live.status_code == 503 and _detail(live)['code'] == 'IDENTITY_DIRECTORY_UNAVAILABLE'
    assert identity.auth_session(session)['revoked_at'] is None


def test_an_email_match_is_a_conflict_not_a_merge(identity):
    legacy = _legacy_account(identity, with_organization=False)
    org = identity.platform.org()
    person = identity.platform.user(email=legacy['email'])
    identity.platform.member(org, person, role='admin')
    _, response = identity.sign_in(person, org)
    assert response.status_code == 409 and _detail(response)['code'] == 'IDENTITY_LINK_CONFLICT'
    row = identity.db.execute('SELECT auth_provider, external_subject FROM users WHERE id = %s', (legacy['id'],)).fetchone()
    assert row['auth_provider'] != 'workos' and row['external_subject'] is None


def test_a_linked_person_whose_organization_is_not_linked_yet_is_held(identity):
    legacy = _legacy_account(identity)
    org = identity.platform.org()
    person = identity.platform.user(email=legacy['email'])
    identity.platform.member(org, person, role='admin')
    identity.platform.legacy_link(org, person, legacy['id'])
    _, response = identity.sign_in(person, org)
    assert response.status_code == 409 and _detail(response)['code'] == 'ORGANIZATION_LINK_REQUIRED'
    assert identity.guard_org(org) is None


def test_a_reviewed_legacy_link_moves_the_account_and_ends_its_password_sessions(identity):
    identity.env.setenv('GUARD_IDENTITY_MODE', 'dual')
    legacy = _legacy_account(identity)
    signin = identity.client.post('/auth/signin', json={'email': legacy['email'], 'password': _PASSWORD})
    assert signin.status_code == 200, signin.text
    password_token = signin.json()['access_token']
    assert identity.client.get('/auth/me', headers={'authorization': f'Bearer {password_token}'}).status_code == 200

    # The reviewed import records the organization mapping and (once the
    # invitation is accepted) the account link on the platform.
    org = identity.platform.org()
    person = identity.platform.user(email=legacy['email'])
    identity.platform.member(org, person, role='admin')
    identity.platform.legacy_organization_link(org, legacy['organization_id'])
    identity.platform.legacy_link(org, person, legacy['id'])

    session = identity.session(person, org)
    assert str(session.user['id']) == legacy['id']
    moved = identity.db.execute('SELECT auth_provider, external_subject FROM users WHERE id = %s', (legacy['id'],)).fetchone()
    assert moved['auth_provider'] == 'workos' and moved['external_subject'] == person.workos_id
    linked_org = identity.guard_org(org)
    assert linked_org['id'] == uuid.UUID(legacy['organization_id']) and linked_org['workos_organization_id'] == org.workos_id
    assert {'identity.user_linked', 'organization.decoda_linked'} <= set(identity.audit(user_id=legacy['id']))
    # The legacy owner stays owner, and no second tenant was created.
    role = identity.db.execute('SELECT role FROM organization_memberships WHERE user_id = %s', (legacy['id'],)).fetchall()
    assert [r['role'] for r in role] == ['owner']
    assert session.user['current_workspace']['id'] == str(
        identity.db.execute('SELECT id FROM workspaces WHERE organization_id = %s', (legacy['organization_id'],)).fetchone()['id']
    )

    # The password session is over, and the password no longer opens the account.
    assert identity.client.get('/auth/me', headers={'authorization': f'Bearer {password_token}'}).status_code == 401
    again = identity.client.post('/auth/signin', json={'email': legacy['email'], 'password': _PASSWORD})
    assert again.status_code == 410 and _detail(again)['code'] == 'DECODA_ACCOUNT_LINKED'
    assert session.get('/auth/me').status_code == 200


def test_a_reviewed_organization_link_admits_new_members_into_the_existing_tenant(identity):
    legacy = _legacy_account(identity)
    org = identity.platform.org()
    newcomer = identity.platform.user()
    identity.platform.member(org, newcomer, role='member')
    identity.platform.legacy_organization_link(org, legacy['organization_id'])

    session = identity.session(newcomer, org)  # a member (not an admin) may enter first: the tenant exists
    assert identity.guard_org(org)['id'] == uuid.UUID(legacy['organization_id'])
    role = identity.db.execute(
        'SELECT role FROM organization_memberships WHERE organization_id = %s AND user_id = %s',
        (legacy['organization_id'], session.user['id']),
    ).fetchone()
    assert role['role'] == 'viewer'
    legacy_owner = identity.db.execute('SELECT auth_provider FROM users WHERE id = %s', (legacy['id'],)).fetchone()
    assert legacy_owner['auth_provider'] != 'workos'  # nobody else's account was touched


@pytest.mark.parametrize('target', ['already_linked', 'missing'])
def test_an_organization_link_that_disagrees_with_guard_is_refused(identity, target):
    legacy = _legacy_account(identity)
    org = identity.platform.org()
    admin = identity.platform.user()
    identity.platform.member(org, admin, role='admin')
    if target == 'already_linked':
        other = identity.platform.org()
        identity.db.execute('UPDATE organizations SET platform_organization_id = %s WHERE id = %s', (other.id, legacy['organization_id']))
        identity.platform.legacy_organization_link(org, legacy['organization_id'])
    else:
        identity.platform.legacy_organization_link(org, str(uuid.uuid4()))
    _, response = identity.sign_in(admin, org)
    assert response.status_code == 409 and _detail(response)['code'] == 'ORGANIZATION_LINK_CONFLICT'
    assert identity.guard_user(admin) is None and identity.guard_org(org) is None


# ── Per-request re-validation ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ('change', 'restore', 'reason'),
    [
        (
            lambda p, org, user: p.entitle(org, 'rwa_guard', 'suspended'),
            lambda p, org, user: p.entitle(org, 'rwa_guard', 'pilot'),
            'entitlement_suspended',
        ),
        (
            lambda p, org, user: p.member(org, user, role='admin', status='inactive'),
            lambda p, org, user: p.member(org, user, role='admin', status='active'),
            'membership_inactive',
        ),
    ],
)
def test_platform_changes_apply_to_live_sessions(identity, change, restore, reason):
    org, admin = _org_with_admin(identity)
    session = identity.session(admin, org)
    assert session.get('/auth/me').status_code == 200
    change(identity.platform, org, admin)
    identity.expire_cache()
    refused = session.get('/auth/me')
    assert refused.status_code == 403 and _detail(refused)['reason'] == reason
    assert identity.auth_session(session)['revoked_at'] is None
    restore(identity.platform, org, admin)
    assert session.get('/auth/me').status_code == 200


def test_grants_are_cached_only_briefly_and_denials_never(identity):
    org, admin = _org_with_admin(identity)
    session = identity.session(admin, org)
    assert session.get('/auth/me').status_code == 200
    identity.platform.entitle(org, 'rwa_guard', 'suspended')
    assert session.get('/auth/me').status_code == 200  # inside the ≤15 s grant cache
    identity.expire_cache()
    assert session.get('/auth/me').status_code == 403
    identity.platform.entitle(org, 'rwa_guard', 'pilot')
    assert session.get('/auth/me').status_code == 200  # the denial was not cached


def test_a_platform_revocation_ends_the_guard_session(identity):
    org, admin = _org_with_admin(identity)
    session = identity.session(admin, org)
    identity.platform.revoke(admin, session.workos_session_id)
    identity.expire_cache()
    assert session.get('/auth/me').status_code == 401
    row = identity.auth_session(session)
    assert row['revoked_at'] is not None and row['metadata']['revoke_reason'] == 'identity_session_revoked'


def test_the_session_is_only_honoured_through_the_bff(identity):
    org, admin = _org_with_admin(identity)
    session = identity.session(admin, org)
    direct = identity.client.get('/auth/me', headers=session.headers(bff=False))
    assert direct.status_code == 401
    assert identity.auth_session(session)['revoked_at'] is None  # a stray request does not end it
    assert session.get('/auth/me').status_code == 200


def test_a_different_authkit_session_orphans_the_guard_session(identity):
    org, admin = _org_with_admin(identity)
    session = identity.session(admin, org)
    other = identity.client.get('/auth/me', headers=session.headers({'x-guard-identity-session': workos_id('session')}))
    assert other.status_code == 401
    assert identity.auth_session(session)['metadata']['revoke_reason'] == 'identity_session_mismatch'
    assert session.get('/auth/me').status_code == 401


def test_sign_out_ends_the_session_even_after_access_was_withdrawn(identity):
    org, admin = _org_with_admin(identity)
    session = identity.session(admin, org)
    identity.platform.entitle(org, 'rwa_guard', 'suspended')
    identity.expire_cache()
    assert session.get('/auth/me').status_code == 403
    out = session.post('/auth/signout')
    assert out.status_code == 200 and out.json() == {'signed_out': True}
    assert identity.auth_session(session)['revoked_at'] is not None
    identity.platform.entitle(org, 'rwa_guard', 'pilot')
    assert session.get('/auth/me').status_code == 401  # restoring access does not revive it


def test_sign_out_of_a_live_session(identity):
    org, admin = _org_with_admin(identity)
    session = identity.session(admin, org)
    assert session.post('/auth/signout').status_code == 200
    assert session.get('/auth/me').status_code == 401


# ── Tenant isolation ────────────────────────────────────────────────────────


def test_a_session_is_confined_to_its_organization(identity):
    org_a, person = _org_with_admin(identity)
    org_b = identity.platform.org()
    identity.platform.member(org_b, person, role='admin')
    session_b = identity.session(person, org_b)
    workspace_b = session_b.user['current_workspace']['id']
    session_a = identity.session(person, org_a)
    workspace_a = session_a.user['current_workspace']['id']
    assert workspace_a != workspace_b

    listed = session_a.get('/workspaces').json()
    assert [w['workspace_id'] for w in listed['workspaces']] == [workspace_a]
    assert session_a.get('/workspace/members', workspace=workspace_a).status_code == 200
    crossed = session_a.get('/workspace/members', workspace=workspace_b)
    assert crossed.status_code == 403 and _detail(crossed)['code'] == 'WORKSPACE_OUTSIDE_ORGANIZATION'
    select = session_a.post('/auth/select-workspace', {'workspace_id': workspace_b})
    assert select.status_code == 403
    # The other organization's session still works for its own workspace only.
    assert session_b.get('/workspace/members', workspace=workspace_b).status_code == 200
    assert session_b.get('/workspace/members', workspace=workspace_a).status_code == 403

    # Losing org B on the platform closes org B's session, not org A's.
    identity.platform.member(org_b, person, role='admin', status='inactive')
    identity.expire_cache()
    assert session_b.get('/auth/me').status_code == 403
    assert session_a.get('/auth/me').status_code == 200


def test_new_workspaces_stay_inside_the_bound_organization(identity):
    from services.api.app import pilot
    from services.api.app.decoda_identity.session_gate import bind_organization

    org_a, person = _org_with_admin(identity)
    org_b = identity.platform.org()
    identity.platform.member(org_b, person, role='admin')
    session_a = identity.session(person, org_a)
    session_b = identity.session(person, org_b)  # moves the user's current workspace into org B
    guard_a, guard_b = identity.guard_org(org_a), identity.guard_org(org_b)
    identity.db.execute(
        """UPDATE organizations SET entitlement_overrides = '{"max_workspaces": 5}'::jsonb WHERE id IN (%s, %s)""",
        (guard_a['id'], guard_b['id']),
    )
    created = session_a.post('/workspaces', {'name': 'Second Harbor workspace'})
    assert created.status_code == 200, created.text
    landed = identity.db.execute("SELECT organization_id FROM workspaces WHERE name = 'Second Harbor workspace'").fetchone()
    assert landed['organization_id'] == guard_a['id']

    # Defence in depth: even with the user's current workspace in org B, an
    # org-A-bound request cannot resolve org B as the owner of a new workspace.
    user_id = str(identity.guard_user(person)['id'])
    identity.db.execute('UPDATE users SET current_workspace_id = %s WHERE id = %s', (session_b.user['current_workspace']['id'], user_id))
    bind_organization(str(guard_a['id']))
    with pilot.pg_connection() as connection:
        with pytest.raises(pilot.HTTPException) as refused:
            pilot._resolve_organization_for_new_workspace(connection, user_id=user_id)
    assert refused.value.status_code == 403 and refused.value.detail['code'] == 'WORKSPACE_OUTSIDE_ORGANIZATION'
    bind_organization(str(guard_b['id']))
    with pilot.pg_connection() as connection:
        assert str(pilot._resolve_organization_for_new_workspace(connection, user_id=user_id)['id']) == str(guard_b['id'])


# ── Identity context and organization switching ─────────────────────────────


def test_identity_context_lists_organizations_and_products_without_provider_ids(identity):
    org_a, person = _org_with_admin(identity)
    org_b = identity.platform.org(guard=None)
    identity.platform.member(org_b, person, role='member')
    session = identity.session(person, org_a)
    response = session.get('/auth/identity/context')
    assert response.status_code == 200, response.text
    body = response.json()
    orgs = {o['id']: o for o in body['organizations']}
    assert orgs[org_a.id]['current'] is True and orgs[org_a.id]['product_access'] == 'granted'
    assert orgs[org_b.id]['current'] is False and orgs[org_b.id]['product_access'] == 'not_entitled'
    products = {p['product']: p for p in body['products']}
    assert products['rwa_guard']['access'] == 'granted' and products['vault']['access'] == 'not_entitled'
    assert 'org_' not in response.text and 'user_' not in response.text


def test_switch_target_requires_membership_and_entitlement(identity):
    org_a, person = _org_with_admin(identity)
    org_b = identity.platform.org()
    identity.platform.member(org_b, person, role='member')
    org_c = identity.platform.org(guard=None)
    identity.platform.member(org_c, person, role='member')
    org_d = identity.platform.org()
    session = identity.session(person, org_a)

    ok = session.post('/auth/identity/switch-target', {'organization_id': org_b.id})
    assert ok.status_code == 200 and ok.json()['workos_organization_id'] == org_b.workos_id
    denied = session.post('/auth/identity/switch-target', {'organization_id': org_c.id})
    assert denied.status_code == 403 and _detail(denied)['reason'] == 'not_entitled'
    foreign = session.post('/auth/identity/switch-target', {'organization_id': org_d.id})
    assert foreign.status_code == 403 and _detail(foreign)['code'] == 'ORGANIZATION_NOT_AVAILABLE'
    no_bff = identity.client.post(
        '/auth/identity/switch-target', json={'organization_id': org_b.id}, headers=session.headers(bff=False)
    )
    assert no_bff.status_code in (401, 403)


# ── Identity modes ──────────────────────────────────────────────────────────


def test_guard_mfa_and_password_reauthentication_are_decoda_managed(identity):
    org, admin = _org_with_admin(identity)
    session = identity.session(admin, org)
    for path, body in (
        ('/auth/mfa/enroll', {}),
        ('/auth/session/step-up', {'code': '123456'}),
        ('/auth/reauthenticate', {'password': _PASSWORD}),
        ('/auth/mfa/disable', {'code': '123456'}),
    ):
        response = session.post(path, body)
        assert response.status_code == 409, (path, response.text)
        assert _detail(response)['code'] == 'DECODA_REAUTHENTICATION_REQUIRED'


def test_password_sessions_end_when_workos_mode_is_switched_on(identity):
    identity.env.setenv('GUARD_IDENTITY_MODE', 'legacy')
    legacy = _legacy_account(identity)
    identity.env.setenv('GUARD_IDENTITY_MODE', 'legacy')
    signin = identity.client.post('/auth/signin', json={'email': legacy['email'], 'password': _PASSWORD})
    assert signin.status_code == 200, signin.text
    token = signin.json()['access_token']
    identity.env.setenv('GUARD_IDENTITY_MODE', 'workos')
    assert identity.client.get('/auth/me', headers={'authorization': f'Bearer {token}'}).status_code == 401
    identity.env.setenv('GUARD_IDENTITY_MODE', 'legacy')
    assert identity.client.get('/auth/me', headers={'authorization': f'Bearer {token}'}).status_code == 401  # revoked, not paused


def test_dual_mode_keeps_unlinked_passwords_until_the_sunset(identity):
    identity.env.setenv('GUARD_IDENTITY_MODE', 'dual')
    legacy = _legacy_account(identity)
    signin = identity.client.post('/auth/signin', json={'email': legacy['email'], 'password': _PASSWORD})
    assert signin.status_code == 200, signin.text
    token = signin.json()['access_token']
    me = identity.client.get('/auth/me', headers={'authorization': f'Bearer {token}'})
    assert me.status_code == 200 and me.json()['user']['identity']['auth_method'] == 'password'
    signup = identity.client.post('/auth/signup', json={'email': 'new@harbor-trust.test', 'password': _PASSWORD})
    assert signup.status_code == 410 and _detail(signup)['code'] == 'SIGN_UP_MOVED'

    identity.env.setenv('GUARD_LEGACY_PASSWORD_SUNSET', (datetime.now(UTC) - timedelta(minutes=1)).isoformat())
    assert identity.client.get('/auth/me', headers={'authorization': f'Bearer {token}'}).status_code == 401
    late = identity.client.post('/auth/signin', json={'email': legacy['email'], 'password': _PASSWORD})
    assert late.status_code == 410 and _detail(late)['code'] == 'LEGACY_AUTH_DISABLED'


def test_decoda_sessions_end_when_identity_is_switched_off(identity):
    org, admin = _org_with_admin(identity)
    session = identity.session(admin, org)
    identity.env.setenv('GUARD_IDENTITY_MODE', 'legacy')
    assert session.get('/auth/me').status_code == 401
    assert identity.auth_session(session)['metadata']['revoke_reason'] == 'identity_mode_changed'


# ── Schema guarantees (migration 0158) ──────────────────────────────────────


def test_a_decoda_session_cannot_exist_without_its_workos_binding(identity):
    org, admin = _org_with_admin(identity)
    session = identity.session(admin, org)
    with pytest.raises(psycopg.errors.CheckViolation):
        identity.db.execute(
            'UPDATE auth_sessions SET workos_session_id = NULL WHERE workos_session_id = %s', (session.workos_session_id,)
        )
    with pytest.raises(psycopg.errors.CheckViolation):
        identity.db.execute("UPDATE users SET external_subject = 'not-a-workos-id' WHERE auth_provider = 'workos'")
    twin = str(uuid.uuid4())
    identity.db.execute("INSERT INTO organizations (id, name, slug) VALUES (%s, 'Twin', %s)", (twin, f'twin-{twin[:8]}'))
    with pytest.raises(psycopg.errors.UniqueViolation):
        identity.db.execute('UPDATE organizations SET workos_organization_id = %s WHERE id = %s', (org.workos_id, twin))
    with pytest.raises(psycopg.errors.UniqueViolation):
        identity.db.execute('UPDATE organizations SET platform_organization_id = %s WHERE id = %s', (org.id, twin))
    with pytest.raises(psycopg.errors.CheckViolation):
        identity.db.execute("UPDATE organizations SET workos_organization_id = 'not-an-org' WHERE id = %s", (twin,))
