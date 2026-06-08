from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from services.api.app import pilot


class Result:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class PermissionConnection:
    def __init__(self, *, overrides=None, policy=None, reauthenticated_at=None):
        self.overrides = overrides or {}
        self.policy = policy
        self.reauthenticated_at = reauthenticated_at

    def execute(self, statement, params=None):
        if 'workspace_role_permissions' in statement:
            workspace_id, role, permission = params
            granted = self.overrides.get((workspace_id, role, permission))
            return Result(None if granted is None else {'granted': granted})
        if 'workspace_auth_policies' in statement:
            return Result(self.policy)
        if 'reauthenticated_at FROM auth_sessions' in statement:
            return Result({'reauthenticated_at': self.reauthenticated_at})
        raise AssertionError(f'Unexpected SQL: {statement}')


def request(workspace_id='workspace-a'):
    return SimpleNamespace(headers={'authorization': 'Bearer session-token', 'x-workspace-id': workspace_id})


@pytest.mark.parametrize(
    ('role', 'permission', 'allowed'),
    [
        (role, permission, permission in pilot.DEFAULT_ROLE_PERMISSIONS[role])
        for role in ('owner', 'admin', 'analyst', 'viewer')
        for permission in sorted(pilot.WORKSPACE_PERMISSIONS)
    ],
)
def test_every_default_role_permission_boundary_is_enforced(monkeypatch, role, permission, allowed):
    connection = PermissionConnection()
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_: {'id': 'user-a', 'mfa_enabled': True})
    monkeypatch.setattr(
        pilot,
        'resolve_workspace',
        lambda *_: {'workspace_id': 'workspace-a', 'role': role, 'workspace': {'id': 'workspace-a'}},
    )

    if allowed:
        user, context = pilot._require_workspace_permission(connection, request(), permission)
        assert user['id'] == 'user-a'
        assert context['workspace_id'] == 'workspace-a'
    else:
        with pytest.raises(HTTPException) as exc_info:
            pilot._require_workspace_permission(connection, request(), permission)
        assert exc_info.value.status_code == 403
        assert exc_info.value.detail['code'] == 'PERMISSION_DENIED'
        assert exc_info.value.detail['permission'] == permission


def test_persisted_permission_denial_overrides_role_default(monkeypatch):
    connection = PermissionConnection(overrides={('workspace-a', 'owner', 'response.execute'): False})
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_: {'id': 'owner-a', 'mfa_enabled': True})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda *_: {'workspace_id': 'workspace-a', 'role': 'owner', 'workspace': {}})

    with pytest.raises(HTTPException) as exc_info:
        pilot._require_workspace_permission(connection, request(), 'response.execute')

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail['code'] == 'PERMISSION_DENIED'


def test_administrative_mfa_policy_blocks_unenrolled_admin(monkeypatch):
    connection = PermissionConnection(policy={'mfa_enforcement': 'administrators', 'reauthentication_minutes': 15})
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_: {'id': 'admin-a', 'mfa_enabled': False})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda *_: {'workspace_id': 'workspace-a', 'role': 'admin', 'workspace': {}})

    with pytest.raises(HTTPException) as exc_info:
        pilot._require_workspace_permission(connection, request(), 'members.manage')

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail['code'] == 'MFA_ENROLLMENT_REQUIRED'


def test_cross_tenant_workspace_header_is_rejected_before_permission_check(monkeypatch):
    connection = PermissionConnection()
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_: {'id': 'user-a', 'mfa_enabled': True})

    def reject_other_workspace(_connection, _user_id, requested_workspace_id):
        assert requested_workspace_id == 'workspace-b'
        raise HTTPException(status_code=404, detail='Workspace membership not found.')

    monkeypatch.setattr(pilot, 'resolve_workspace', reject_other_workspace)

    with pytest.raises(HTTPException) as exc_info:
        pilot._require_workspace_permission(connection, request('workspace-b'), 'evidence.export')

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == 'Workspace membership not found.'


def test_totp_uses_rfc_compatible_base32_secret(monkeypatch):
    monkeypatch.setattr(pilot, 'utc_now', lambda: pilot.datetime(1970, 1, 1, 0, 0, 59, tzinfo=pilot.timezone.utc))
    secret = 'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'
    assert pilot._totp_code(secret, digits=8) == '94287082'
    assert pilot._verify_totp(secret, '287082') is True


def test_scim_token_authentication_is_workspace_scoped(monkeypatch):
    class ScimConnection:
        def execute(self, statement, params=None):
            if 'FROM workspace_scim_tokens' in statement:
                return Result({'id': 'token-a', 'workspace_id': 'workspace-a'})
            if 'UPDATE workspace_scim_tokens' in statement:
                return Result()
            raise AssertionError(statement)

    monkeypatch.setattr(pilot, '_auth_token_hash', lambda value: f'hash:{value}')
    scim_request = SimpleNamespace(headers={'authorization': 'Bearer scim-secret'})
    assert pilot._authenticate_scim(ScimConnection(), scim_request) == 'workspace-a'


def test_identity_management_routes_delegate(monkeypatch):
    from fastapi.testclient import TestClient
    from services.api.app import main as api_main

    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setattr(api_main, 'get_workspace_access_control', lambda request: {'permissions': ['identity.manage']})
    monkeypatch.setattr(api_main, 'update_workspace_auth_policy', lambda payload, request: payload)
    client = TestClient(api_main.app)

    assert client.get('/workspace/access-control').json() == {'permissions': ['identity.manage']}
    assert client.put('/workspace/auth-policy', json={'mfa_enforcement': 'administrators'}).json() == {'mfa_enforcement': 'administrators'}


def test_scim_routes_delegate_without_workspace_session_auth(monkeypatch):
    from fastapi.testclient import TestClient
    from services.api.app import main as api_main

    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setattr(api_main, 'scim_list_users', lambda request, start_index=1, count=100: {'totalResults': 0, 'startIndex': start_index})
    client = TestClient(api_main.app)

    response = client.get('/scim/v2/Users?startIndex=3', headers={'Authorization': 'Bearer scim-token'})
    assert response.status_code == 200
    assert response.json() == {'totalResults': 0, 'startIndex': 3}


def test_session_mfa_requires_server_record_not_step_up_headers(monkeypatch):
    class Connection:
        def execute(self, statement, params=None):
            assert 'mfa_verified_at' in statement
            return Result({'authenticated_at': None, 'reauthenticated_at': None, 'mfa_verified_at': None, 'authentication_methods': []})

    monkeypatch.setattr(pilot, '_auth_token_hash', lambda value: f'hash:{value}')
    forged = SimpleNamespace(headers={
        'authorization': 'Bearer session-token',
        'x-step-up-verified': 'true',
        'x-step-up-authenticated-at': pilot.utc_now().isoformat(),
    })
    with pytest.raises(HTTPException) as exc_info:
        pilot._require_session_mfa(Connection(), forged)
    assert exc_info.value.detail['code'] == 'MFA_CHALLENGE_REQUIRED'


def test_oidc_claim_validation_rejects_unsupported_algorithm(monkeypatch):
    import base64
    import json

    def encoded(value):
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')

    token = f"{encoded({'alg': 'none', 'kid': 'key'})}.{encoded({'nonce': 'expected'})}.signature"
    with pytest.raises(HTTPException) as exc_info:
        pilot._verify_oidc_id_token(token, discovery={'issuer': 'https://idp.example', 'jwks_uri': 'https://idp.example/keys'}, client_id='client', nonce='expected')
    assert exc_info.value.status_code == 401


def test_identity_migration_enforces_append_only_audit_and_oidc_state():
    from pathlib import Path

    migration = Path('services/api/migrations/0100_identity_sod_and_append_only_audit.sql').read_text()
    assert 'oidc_login_states' in migration
    assert 'BEFORE UPDATE OR DELETE ON audit_logs' in migration
    assert "current_setting('app.retention_worker', true)" in migration


def test_oidc_claim_validation_verifies_signature_issuer_audience_expiry_and_nonce(monkeypatch):
    import base64
    import json
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    def b64(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode().rstrip('=')

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = key.public_key().public_numbers()
    jwk = {
        'kid': 'key-1', 'alg': 'RS256', 'kty': 'RSA',
        'n': b64(numbers.n.to_bytes((numbers.n.bit_length() + 7) // 8, 'big')),
        'e': b64(numbers.e.to_bytes((numbers.e.bit_length() + 7) // 8, 'big')),
    }
    now = int(pilot.utc_now().timestamp())
    header = b64(json.dumps({'alg': 'RS256', 'kid': 'key-1'}).encode())
    payload = b64(json.dumps({'iss': 'https://idp.example', 'aud': 'client-1', 'sub': 'subject-1', 'nonce': 'nonce-1', 'iat': now, 'exp': now + 300}).encode())
    signature = key.sign(f'{header}.{payload}'.encode(), padding.PKCS1v15(), hashes.SHA256())
    monkeypatch.setattr(pilot, '_oidc_fetch_json', lambda *_args, **_kwargs: {'keys': [jwk]})

    claims = pilot._verify_oidc_id_token(
        f'{header}.{payload}.{b64(signature)}',
        discovery={'issuer': 'https://idp.example', 'jwks_uri': 'https://idp.example/keys'},
        client_id='client-1', nonce='nonce-1',
    )
    assert claims['sub'] == 'subject-1'
