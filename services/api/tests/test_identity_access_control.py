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
