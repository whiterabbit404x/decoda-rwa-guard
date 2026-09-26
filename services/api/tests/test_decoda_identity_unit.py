"""Shared Decoda identity for RWA Guard — the parts that need no database.

Configuration that fails closed, the identity-mode gates on every legacy path,
the WorkOS token verifier, the platform access cache, and the per-request
session gate (with the platform and revocation writes faked). The same flows
against a real PostgreSQL + platform schema live in
``test_decoda_identity_postgres.py``.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import jwt as pyjwt
import pytest
from fastapi import HTTPException

from services.api.app import mfa_authorization, pilot
from services.api.app.decoda_identity import config as identity_config
from services.api.app.decoda_identity import session_gate
from services.api.app.decoda_identity.platform import DENIAL_MESSAGES, NO_MEMBERSHIP, PlatformDirectory, PlatformUnavailable, ProductAccess
from services.api.app.decoda_identity.tokens import IdentityKeysUnavailable, IdentityTokenError, IdentityTokenVerifier
from services.api.tests.decoda_identity_support import (
    BFF_SECRET,
    CLIENT_ID,
    IDENTITY_ENV,
    IDENTITY_ENV_NAMES,
    ISSUER,
    KID,
    OTHER_PRIVATE_KEY,
    ManualClock,
    StaticKeys,
    mint_token,
    workos_id,
)

PLATFORM_URL = 'postgresql://decoda_platform_reader@platform-db/decoda'


@pytest.fixture(autouse=True)
def _unbound():
    """The organization binding is request-scoped (a context variable); tests share one context."""
    session_gate.bind_organization(None)
    yield
    session_gate.bind_organization(None)


@pytest.fixture
def env(monkeypatch):
    for name in (*IDENTITY_ENV_NAMES, 'APP_ENV', 'APP_MODE'):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


@pytest.fixture
def workos_env(env):
    for name, value in IDENTITY_ENV.items():
        env.setenv(name, value)
    env.setenv('DECODA_PLATFORM_DATABASE_URL', PLATFORM_URL)
    return env


def _gone_code(fn, *args, **kwargs) -> tuple[int, dict]:
    with pytest.raises(HTTPException) as info:
        fn(*args, **kwargs)
    return info.value.status_code, info.value.detail


# ── Configuration ────────────────────────────────────────────────────────────


def test_legacy_is_the_default_and_needs_no_identity_configuration(env):
    settings = identity_config.load_identity_settings()
    assert settings.mode == 'legacy' and not settings.uses_workos
    assert identity_config.configuration_errors(settings) == []
    assert settings.legacy_passwords_allowed()


def test_unknown_mode_is_a_startup_error(env):
    env.setenv('GUARD_IDENTITY_MODE', 'demo')
    assert identity_config.configuration_errors() == ['GUARD_IDENTITY_MODE must be one of legacy, dual, workos.']


@pytest.mark.parametrize('mode', ['dual', 'workos'])
def test_enabled_identity_without_configuration_is_a_startup_error_in_every_environment(env, mode):
    env.setenv('GUARD_IDENTITY_MODE', mode)
    (error,) = identity_config.configuration_errors()
    for name in ('WORKOS_CLIENT_ID', 'WORKOS_API_KEY', 'DECODA_PLATFORM_DATABASE_URL'):
        assert name in error


def test_development_workos_mode_with_configuration_is_valid(workos_env):
    workos_env.delenv('WORKOS_ISSUER')
    workos_env.delenv('GUARD_BFF_SHARED_SECRET')
    workos_env.delenv('DECODA_IDP_MFA_REQUIRED')
    assert identity_config.configuration_errors() == []


@pytest.mark.parametrize('app_env', ['production', 'staging'])
@pytest.mark.parametrize('missing', ['WORKOS_ISSUER', 'GUARD_BFF_SHARED_SECRET'])
def test_production_requires_issuer_and_bff_secret(workos_env, app_env, missing):
    workos_env.setenv('APP_ENV', app_env)
    workos_env.delenv(missing)
    errors = identity_config.configuration_errors()
    assert any(missing in error for error in errors)


def test_production_requires_the_mfa_attestation_and_a_long_bff_secret(workos_env):
    workos_env.setenv('APP_ENV', 'production')
    workos_env.setenv('DECODA_IDP_MFA_REQUIRED', 'false')
    workos_env.setenv('GUARD_BFF_SHARED_SECRET', 'short')
    errors = ' '.join(identity_config.configuration_errors())
    assert 'DECODA_IDP_MFA_REQUIRED=true' in errors
    assert 'at least 32 characters' in errors


def test_production_dual_mode_requires_a_sunset(workos_env):
    workos_env.setenv('APP_ENV', 'production')
    workos_env.setenv('GUARD_IDENTITY_MODE', 'dual')
    assert any('GUARD_LEGACY_PASSWORD_SUNSET' in error for error in identity_config.configuration_errors())
    workos_env.setenv('GUARD_LEGACY_PASSWORD_SUNSET', '2026-12-31')
    assert identity_config.configuration_errors() == []


def test_an_unparseable_sunset_is_an_error_and_closes_legacy_passwords(workos_env):
    workos_env.setenv('GUARD_IDENTITY_MODE', 'dual')
    workos_env.setenv('GUARD_LEGACY_PASSWORD_SUNSET', 'next tuesday')
    settings = identity_config.load_identity_settings()
    assert 'GUARD_LEGACY_PASSWORD_SUNSET' in ' '.join(identity_config.configuration_errors(settings))
    assert settings.legacy_passwords_allowed() is False


def test_the_sunset_ends_legacy_passwords(workos_env):
    workos_env.setenv('GUARD_IDENTITY_MODE', 'dual')
    workos_env.setenv('GUARD_LEGACY_PASSWORD_SUNSET', '2030-01-01T00:00:00Z')
    settings = identity_config.load_identity_settings()
    assert settings.legacy_passwords_allowed(datetime(2029, 12, 31, tzinfo=UTC))
    assert not settings.legacy_passwords_allowed(datetime(2030, 1, 1, tzinfo=UTC))


def test_access_cache_ttl_is_capped(workos_env):
    workos_env.setenv('DECODA_ACCESS_CACHE_TTL_SECONDS', '3600')
    assert identity_config.load_identity_settings().access_cache_ttl_seconds == 60


def test_runtime_validation_blocks_startup_on_identity_misconfiguration(env):
    env.setenv('GUARD_IDENTITY_MODE', 'workos')
    result = pilot.validate_runtime_configuration()
    assert result['checks']['decoda_identity']['ok'] is False
    assert result['checks']['decoda_identity']['mode'] == 'workos'
    assert any('WORKOS_CLIENT_ID' in error for error in result['errors'])


def test_runtime_validation_reports_legacy_mode_without_blocking(env):
    result = pilot.validate_runtime_configuration()
    assert result['checks']['decoda_identity'] == {'ok': True, 'required': True, 'severity': 'error', 'detail': None, 'mode': 'legacy'}
    assert not any('GUARD_IDENTITY_MODE' in error for error in result['errors'])


# ── Mode gates on the legacy (Guard-local) identity paths ───────────────────


def test_legacy_mode_leaves_every_existing_path_open(env):
    identity_config.require_legacy_password_sign_in()
    identity_config.require_legacy_password_sign_in(auth_provider='workos')
    identity_config.require_new_password_accounts()
    identity_config.require_local_onboarding()
    identity_config.require_local_sign_in_paths()
    identity_config.require_local_mfa(None)


def test_workos_mode_closes_every_legacy_path(workos_env):
    workos_env.setenv('DECODA_WEBSITE_URL', 'https://www.decodasecurity.com/')
    assert _gone_code(identity_config.require_legacy_password_sign_in)[1]['code'] == 'LEGACY_AUTH_DISABLED'
    status_code, detail = _gone_code(identity_config.require_new_password_accounts)
    assert status_code == 410 and detail['code'] == 'SIGN_UP_MOVED'
    assert detail['request_access_url'] == 'https://www.decodasecurity.com/request-pilot?product=rwa_guard'
    assert _gone_code(identity_config.require_local_onboarding)[1]['code'] == 'PILOT_REQUESTS_MOVED'
    assert _gone_code(identity_config.require_local_sign_in_paths)[1]['code'] == 'LEGACY_AUTH_DISABLED'
    status_code, detail = _gone_code(identity_config.require_local_mfa, 'bearer_token')
    assert status_code == 409 and detail['code'] == 'DECODA_REAUTHENTICATION_REQUIRED'


def test_dual_mode_keeps_unlinked_passwords_until_the_sunset(workos_env):
    workos_env.setenv('GUARD_IDENTITY_MODE', 'dual')
    identity_config.require_legacy_password_sign_in(auth_provider='password')
    identity_config.require_local_sign_in_paths()
    identity_config.require_local_mfa('bearer_token')
    assert _gone_code(identity_config.require_legacy_password_sign_in, auth_provider='workos')[1]['code'] == 'DECODA_ACCOUNT_LINKED'
    assert _gone_code(identity_config.require_local_mfa, 'workos')[1]['code'] == 'DECODA_REAUTHENTICATION_REQUIRED'
    # No new password accounts and no local onboarding, even before the sunset.
    assert _gone_code(identity_config.require_new_password_accounts)[1]['code'] == 'SIGN_UP_MOVED'
    assert _gone_code(identity_config.require_local_onboarding)[1]['code'] == 'PILOT_REQUESTS_MOVED'

    workos_env.setenv('GUARD_LEGACY_PASSWORD_SUNSET', (datetime.now(UTC) - timedelta(minutes=1)).isoformat())
    assert _gone_code(identity_config.require_legacy_password_sign_in, auth_provider='password')[1]['code'] == 'LEGACY_AUTH_DISABLED'
    assert _gone_code(identity_config.require_local_sign_in_paths)[1]['code'] == 'LEGACY_AUTH_DISABLED'


@pytest.fixture
def live_client(workos_env):
    """The real app, in live mode, with the shared identity enabled (no database is reached)."""
    from fastapi.testclient import TestClient

    from services.api.app import main as api_main

    workos_env.setenv('LIVE_MODE_ENABLED', 'true')
    workos_env.setenv('DATABASE_URL', 'postgresql://guard:unused@127.0.0.1:9/guard')
    workos_env.setenv('AUTH_TOKEN_SECRET', 'a' * 48)
    return TestClient(api_main.app)


@pytest.mark.parametrize(
    ('path', 'body', 'code'),
    [
        ('/auth/signup', {'email': 'new@harbor.test', 'password': 'Str0ng!Passw0rd#2026', 'full_name': 'New'}, 'SIGN_UP_MOVED'),
        ('/auth/signin', {'email': 'old@harbor.test', 'password': 'whatever-password'}, 'LEGACY_AUTH_DISABLED'),
        ('/auth/mfa/complete-signin', {'mfa_token': 'x' * 32, 'code': '123456'}, 'LEGACY_AUTH_DISABLED'),
        ('/auth/forgot-password', {'email': 'old@harbor.test'}, 'LEGACY_AUTH_DISABLED'),
        ('/auth/reset-password', {'token': 'x' * 32, 'password': 'Str0ng!Passw0rd#2026'}, 'LEGACY_AUTH_DISABLED'),
        ('/auth/reset-password/validate', {'token': 'x' * 32}, 'LEGACY_AUTH_DISABLED'),
        ('/auth/resend-verification', {'email': 'old@harbor.test'}, 'LEGACY_AUTH_DISABLED'),
        ('/auth/verify-email', {'token': 'x' * 32}, 'LEGACY_AUTH_DISABLED'),
        ('/auth/oidc/start', {'workspace': 'harbor', 'redirect_uri': 'https://rwa.decodasecurity.com/cb'}, 'LEGACY_AUTH_DISABLED'),
        ('/auth/oidc/callback', {'state': 'x' * 32, 'code': 'y' * 32}, 'LEGACY_AUTH_DISABLED'),
        ('/pilot-requests', {'email': 'new@harbor.test'}, 'PILOT_REQUESTS_MOVED'),
        ('/pilot-invitations/signup', {'token': 'x' * 32, 'password': 'Str0ng!Passw0rd#2026'}, 'PILOT_REQUESTS_MOVED'),
    ],
)
def test_legacy_endpoints_answer_410_in_workos_mode(live_client, path, body, code):
    response = live_client.post(path, json=body)
    assert response.status_code == 410, response.text
    assert response.json()['detail']['code'] == code


def test_invitation_lookup_answers_410_in_workos_mode(live_client):
    response = live_client.get('/pilot-invitations', params={'token': 'x' * 32})
    assert response.status_code == 410 and response.json()['detail']['code'] == 'PILOT_REQUESTS_MOVED'


def test_exchange_requires_the_bff_secret(live_client):
    token = mint_token(user_id=workos_id('user'), session_id=workos_id('session'), org_id=workos_id('org'))
    response = live_client.post('/auth/identity/exchange', json={'access_token': token})
    assert response.status_code == 403 and response.json()['detail']['code'] == 'BFF_REQUIRED'
    response = live_client.post('/auth/identity/exchange', json={'access_token': token}, headers={'x-guard-proxy-secret': 'wrong'})
    assert response.status_code == 403 and response.json()['detail']['code'] == 'BFF_REQUIRED'


def test_exchange_is_gone_while_identity_is_disabled(live_client, monkeypatch):
    monkeypatch.setenv('GUARD_IDENTITY_MODE', 'legacy')
    response = live_client.post('/auth/identity/exchange', json={'access_token': 'a.b.c'}, headers={'x-guard-proxy-secret': BFF_SECRET})
    assert response.status_code == 410 and response.json()['detail']['code'] == 'IDENTITY_NOT_ENABLED'


def test_exchange_rejects_a_malformed_token_before_any_lookup(live_client):
    response = live_client.post('/auth/identity/exchange', json={'access_token': 'not a jwt'}, headers={'x-guard-proxy-secret': BFF_SECRET})
    assert response.status_code == 401 and response.json()['detail']['code'] == 'IDENTITY_SESSION_INVALID'


# ── Token verifier ──────────────────────────────────────────────────────────


def _verifier(keys: StaticKeys | None = None) -> IdentityTokenVerifier:
    return IdentityTokenVerifier(keys=keys or StaticKeys(), client_id=CLIENT_ID, issuer=(ISSUER,))


def _ids() -> dict[str, str]:
    return {'user_id': workos_id('user'), 'session_id': workos_id('session'), 'org_id': workos_id('org')}


def test_valid_token_yields_claims():
    ids = _ids()
    claims = _verifier().verify(mint_token(**ids, extra={'auth_time': int(time.time()) - 5}))
    assert claims.user_id == ids['user_id'] and claims.session_id == ids['session_id'] and claims.organization_id == ids['org_id']
    assert claims.role == 'member' and claims.auth_time is not None and claims.expires_at > claims.issued_at


@pytest.mark.parametrize(
    ('mutate', 'code'),
    [
        (lambda ids: mint_token(**ids, expires_in=-120), 'IDENTITY_TOKEN_EXPIRED'),
        (lambda ids: mint_token(**ids, issuer='https://api.workos.com/user_management/client_OTHER'), 'IDENTITY_TOKEN_ISSUER'),
        (lambda ids: mint_token(**ids, key=OTHER_PRIVATE_KEY), 'IDENTITY_TOKEN_INVALID'),
        (lambda ids: mint_token(**ids, kid='rotated-away'), 'IDENTITY_TOKEN_KEY_UNKNOWN'),
        (lambda ids: mint_token(**ids, drop=('sid',)), 'IDENTITY_TOKEN_INVALID'),
        (lambda ids: mint_token(**ids, drop=('exp',)), 'IDENTITY_TOKEN_INVALID'),
        (lambda ids: mint_token(**ids, extra={'aud': 'client_SOMEONEELSE'}), 'IDENTITY_TOKEN_AUDIENCE'),
        (lambda ids: mint_token(**{**ids, 'user_id': 'org_NOTAUSER'}), 'IDENTITY_TOKEN_CLAIMS'),
        (lambda ids: mint_token(**{**ids, 'session_id': 'sess-1'}), 'IDENTITY_TOKEN_CLAIMS'),
        (lambda ids: mint_token(**{**ids, 'org_id': 'user_NOTANORG'}), 'IDENTITY_TOKEN_CLAIMS'),
        (lambda ids: 'not-a-jwt', 'IDENTITY_TOKEN_MALFORMED'),
        (lambda ids: 'a.b.c', 'IDENTITY_TOKEN_MALFORMED'),
    ],
)
def test_invalid_tokens_are_rejected(mutate, code):
    with pytest.raises(IdentityTokenError) as info:
        _verifier().verify(mutate(_ids()))
    assert info.value.code == code


def test_symmetric_and_unsigned_algorithms_are_refused_before_key_lookup():
    ids = _ids()
    claims = {'sub': ids['user_id'], 'sid': ids['session_id'], 'iss': ISSUER, 'iat': int(time.time()), 'exp': int(time.time()) + 60}
    hs256 = pyjwt.encode(claims, 'public-key-material-used-as-hmac', algorithm='HS256', headers={'kid': KID})
    unsigned = pyjwt.encode(claims, None, algorithm='none')
    for token in (hs256, unsigned):
        with pytest.raises(IdentityTokenError) as info:
            _verifier().verify(token)
        assert info.value.code == 'IDENTITY_TOKEN_ALGORITHM'


def test_key_outage_is_an_outage_not_an_acceptance():
    keys = StaticKeys()
    keys.unavailable = True
    with pytest.raises(IdentityKeysUnavailable):
        _verifier(keys).verify(mint_token(**_ids()))


# ── Platform access cache ───────────────────────────────────────────────────


class CountingDirectory(PlatformDirectory):
    def __init__(self, answers: list[ProductAccess], clock: ManualClock):
        super().__init__('postgresql://unused', cache_ttl_seconds=15, clock=clock)
        self.answers = answers
        self.queries = 0

    def _query(self, sql, params):
        self.queries += 1
        assert params.get('product') in (None, 'rwa_guard')
        access = self.answers.pop(0)
        if access.access_state == NO_MEMBERSHIP:
            return [{'access_state': None, 'session_revoked': access.session_revoked}]
        return [
            {
                'access_state': access.access_state,
                'session_revoked': access.session_revoked,
                'platform_user_id': 'u',
                'platform_organization_id': access.platform_organization_id or 'o',
                'workos_organization_id': 'org_X',
                'organization_name': 'Org',
                'organization_slug': 'org',
                'organization_role': 'member',
                'user_email': 'a@b.test',
                'user_name': None,
                'entitlement_status': 'enabled',
                'entitlement_plan': 'pilot',
                'entitlement_expires_at': None,
            }
        ]


def _lookup(directory: PlatformDirectory, *, session: str = 'session_A', use_cache: bool = True) -> ProductAccess:
    return directory.product_access(workos_user_id='user_A', workos_organization_id='org_A', workos_session_id=session, use_cache=use_cache)


def test_grants_are_cached_until_the_ttl_expires():
    clock = ManualClock()
    directory = CountingDirectory([ProductAccess('granted'), ProductAccess('granted')], clock)
    assert _lookup(directory).granted and _lookup(directory).granted
    assert directory.queries == 1
    clock.advance(15.1)
    assert _lookup(directory).granted and directory.queries == 2


def test_denials_and_revocations_are_never_cached():
    clock = ManualClock()
    answers = [ProductAccess('not_entitled'), ProductAccess('granted', session_revoked=True), ProductAccess(NO_MEMBERSHIP), ProductAccess('granted')]
    directory = CountingDirectory(answers, clock)
    assert not _lookup(directory).granted
    assert not _lookup(directory).granted
    assert _lookup(directory).access_state == NO_MEMBERSHIP
    assert _lookup(directory).granted
    assert directory.queries == 4


def test_uncached_lookups_bypass_and_refresh_the_cache():
    clock = ManualClock()
    directory = CountingDirectory([ProductAccess('granted'), ProductAccess('entitlement_suspended'), ProductAccess('granted')], clock)
    assert _lookup(directory).granted
    assert not _lookup(directory, use_cache=False).granted
    assert _lookup(directory).granted and directory.queries == 3


def test_an_unreachable_platform_fails_closed():
    directory = PlatformDirectory('postgresql://nobody@127.0.0.1:9/none', connect_timeout_seconds=1)
    with pytest.raises(PlatformUnavailable):
        _lookup(directory)


def test_denial_messages_never_expose_identifiers():
    for message in DENIAL_MESSAGES.values():
        assert 'org_' not in message and 'user_' not in message and 'session_' not in message
    assert ProductAccess('something_new').denial_message == DENIAL_MESSAGES['not_entitled']


# ── Per-request session gate ────────────────────────────────────────────────


class StubDirectory:
    def __init__(self, access: ProductAccess | Exception):
        self.access = access
        self.calls: list[dict] = []

    def product_access(self, **kwargs):
        self.calls.append(kwargs)
        if isinstance(self.access, Exception):
            raise self.access
        return self.access


class StubRequest:
    def __init__(self, headers: dict[str, str]):
        self.headers = headers


GUARD_ORG = '6b0c2f4e-8b7a-4f59-9d53-3f0e5d2c1a10'
PLATFORM_ORG = '0d3c1f2a-7a8b-4c5d-9e0f-112233445566'


def _decoda_session(**overrides):
    session = {
        'auth_mode': 'workos',
        'workos_session_id': 'session_01LIVE',
        'metadata': {
            'workos_user_id': 'user_01MORGAN',
            'workos_organization_id': 'org_01HARBOR',
            'platform_organization_id': PLATFORM_ORG,
            'guard_organization_id': GUARD_ORG,
        },
        'mfa_verified_at': datetime.now(UTC),
        'authentication_methods': ['workos', 'password', 'idp_mfa'],
    }
    session.update(overrides)
    return session


def _bff(session_id: str = 'session_01LIVE', secret: str = BFF_SECRET) -> StubRequest:
    return StubRequest({'x-guard-proxy-secret': secret, 'x-guard-identity-session': session_id})


@pytest.fixture
def gate(workos_env, monkeypatch):
    ended: list[str] = []
    monkeypatch.setattr(session_gate, '_end_session', lambda session_hash, reason: ended.append(reason))
    directory = StubDirectory(ProductAccess('granted', platform_organization_id=PLATFORM_ORG))
    monkeypatch.setattr('services.api.app.decoda_identity.get_directory', lambda: directory)
    return {'ended': ended, 'directory': directory, 'env': workos_env}


def _refusal(session, request) -> tuple[int, object]:
    with pytest.raises(HTTPException) as info:
        session_gate.check_session(session, 'hash', request)
    return info.value.status_code, info.value.detail


def test_a_live_entitled_decoda_session_is_bound_to_its_organization(gate):
    session_gate.check_session(_decoda_session(), 'hash', _bff())
    assert session_gate.bound_guard_organization() == GUARD_ORG
    assert gate['directory'].calls == [
        {'workos_user_id': 'user_01MORGAN', 'workos_organization_id': 'org_01HARBOR', 'workos_session_id': 'session_01LIVE'}
    ]
    session_gate.enforce_workspace_binding(GUARD_ORG)
    with pytest.raises(HTTPException) as info:
        session_gate.enforce_workspace_binding('11111111-2222-3333-4444-555555555555')
    assert info.value.status_code == 403 and info.value.detail['code'] == 'WORKSPACE_OUTSIDE_ORGANIZATION'


def test_a_guard_session_is_unbound_and_passes_in_legacy_and_early_dual(gate):
    gate['env'].setenv('GUARD_IDENTITY_MODE', 'dual')
    session_gate.check_session({'auth_mode': 'bearer_token'}, 'hash', None)
    assert session_gate.bound_guard_organization() is None
    session_gate.enforce_workspace_binding('any-organization')
    gate['env'].setenv('GUARD_IDENTITY_MODE', 'legacy')
    session_gate.check_session({'auth_mode': 'bearer_token'}, 'hash', None)
    assert gate['ended'] == []


def test_guard_sessions_end_in_workos_mode_and_after_the_sunset(gate):
    assert _refusal({'auth_mode': 'bearer_token'}, None)[0] == 401
    gate['env'].setenv('GUARD_IDENTITY_MODE', 'dual')
    gate['env'].setenv('GUARD_LEGACY_PASSWORD_SUNSET', '2000-01-01')
    assert _refusal({'auth_mode': 'bearer_token'}, None)[0] == 401
    assert gate['ended'] == ['identity_mode_changed', 'identity_mode_changed']


def test_decoda_sessions_end_when_identity_is_switched_off(gate):
    gate['env'].setenv('GUARD_IDENTITY_MODE', 'legacy')
    assert _refusal(_decoda_session(), _bff())[0] == 401
    assert gate['ended'] == ['identity_mode_changed']


@pytest.mark.parametrize(
    'request_headers',
    [
        {},
        {'x-guard-identity-session': 'session_01LIVE'},
        {'x-guard-proxy-secret': 'wrong-secret', 'x-guard-identity-session': 'session_01LIVE'},
        {'x-guard-proxy-secret': BFF_SECRET},
    ],
)
def test_requests_not_relayed_by_the_bff_are_refused_without_ending_the_session(gate, request_headers):
    assert _refusal(_decoda_session(), StubRequest(request_headers))[0] == 401
    assert gate['ended'] == [] and gate['directory'].calls == []


def test_a_session_presented_under_another_authkit_session_is_ended(gate):
    assert _refusal(_decoda_session(), _bff('session_01OTHER'))[0] == 401
    assert gate['ended'] == ['identity_session_mismatch']


def test_an_incomplete_binding_is_ended(gate):
    session = _decoda_session(metadata={'workos_user_id': 'user_01MORGAN'})
    assert _refusal(session, _bff())[0] == 401
    assert gate['ended'] == ['identity_binding_incomplete']


def test_a_platform_outage_refuses_the_request(gate):
    gate['directory'].access = PlatformUnavailable('down')
    status_code, detail = _refusal(_decoda_session(), _bff())
    assert status_code == 503 and detail['code'] == 'IDENTITY_DIRECTORY_UNAVAILABLE'
    assert gate['ended'] == []


def test_a_revoked_workos_session_ends_the_guard_session(gate):
    gate['directory'].access = ProductAccess('granted', session_revoked=True, platform_organization_id=PLATFORM_ORG)
    assert _refusal(_decoda_session(), _bff())[0] == 401
    assert gate['ended'] == ['identity_session_revoked']


@pytest.mark.parametrize('state', ['not_entitled', 'entitlement_suspended', 'entitlement_expired', 'membership_inactive', NO_MEMBERSHIP])
def test_withdrawn_access_is_refused_with_the_reason(gate, state):
    gate['directory'].access = ProductAccess(state, platform_organization_id=PLATFORM_ORG)
    status_code, detail = _refusal(_decoda_session(), _bff())
    assert status_code == 403 and detail['code'] == 'PRODUCT_ACCESS_DENIED' and detail['reason'] == state
    assert session_gate.bound_guard_organization() is None


def test_a_platform_organization_mismatch_is_refused(gate):
    gate['directory'].access = ProductAccess('granted', platform_organization_id='99999999-9999-9999-9999-999999999999')
    assert _refusal(_decoda_session(), _bff())[0] == 403


def test_without_a_bff_secret_development_skips_only_the_binding(gate):
    gate['env'].delenv('GUARD_BFF_SHARED_SECRET')
    session_gate.check_session(_decoda_session(), 'hash', StubRequest({}))
    assert session_gate.bound_guard_organization() == GUARD_ORG


# ── MFA facts ───────────────────────────────────────────────────────────────


def test_idp_mfa_counts_only_when_stamped():
    assert session_gate.session_has_idp_mfa(_decoda_session())
    assert not session_gate.session_has_idp_mfa(_decoda_session(mfa_verified_at=None))
    assert not session_gate.session_has_idp_mfa(_decoda_session(authentication_methods=['workos', 'magic_code']))
    assert mfa_authorization.session_completed_mfa(_decoda_session())
    assert not mfa_authorization.session_completed_mfa(_decoda_session(mfa_verified_at=None))


def test_guard_step_up_accepts_idp_mfa_and_still_requires_a_factor(monkeypatch):
    rows = iter(
        [
            _decoda_session(),
            {'mfa_verified_at': datetime.now(UTC), 'authentication_methods': ['workos', 'magic_code']},
            {'mfa_verified_at': datetime.now(UTC), 'authentication_methods': ['password', 'totp']},
        ]
    )
    monkeypatch.setattr(pilot, '_current_session_security', lambda connection, request: next(rows))
    assert pilot._session_mfa_satisfied(None, None) is True
    assert pilot._session_mfa_satisfied(None, None) is False
    assert pilot._session_mfa_satisfied(None, None) is True
