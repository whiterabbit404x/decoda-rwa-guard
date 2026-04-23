from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from services.api.app import pilot


class _Connection:
    def execute(self, statement, params=None):
        raise AssertionError(f'unexpected query executed: {statement}')


def test_resolve_workspace_accepts_single_uuid_header(monkeypatch: pytest.MonkeyPatch) -> None:
    workspace_id = '11111111-1111-1111-1111-111111111111'

    monkeypatch.setattr(
        pilot,
        '_ensure_membership',
        lambda _connection, _user_id, resolved_workspace_id: {
            'workspace_id': resolved_workspace_id,
            'role': 'owner',
            'name': 'Primary',
            'slug': 'primary',
        },
    )

    context = pilot.resolve_workspace(_Connection(), 'user-1', workspace_id)

    assert context['workspace_id'] == workspace_id
    assert context['workspace']['id'] == workspace_id


def test_resolve_workspace_accepts_csv_header_and_uses_first_uuid(monkeypatch: pytest.MonkeyPatch) -> None:
    first_workspace = '11111111-1111-1111-1111-111111111111'
    second_workspace = '22222222-2222-2222-2222-222222222222'

    seen: dict[str, str] = {}

    def _membership(_connection, _user_id, resolved_workspace_id):
        seen['workspace_id'] = resolved_workspace_id
        return {
            'workspace_id': resolved_workspace_id,
            'role': 'owner',
            'name': 'Primary',
            'slug': 'primary',
        }

    monkeypatch.setattr(pilot, '_ensure_membership', _membership)

    context = pilot.resolve_workspace(_Connection(), 'user-1', f' {first_workspace} , {second_workspace} ')

    assert seen['workspace_id'] == first_workspace
    assert context['workspace_id'] == first_workspace


def test_resolve_workspace_context_for_request_rejects_malformed_workspace_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda _connection, _request: {'id': 'user-1'})

    request = SimpleNamespace(headers={'x-workspace-id': 'not-a-uuid'})

    with pytest.raises(HTTPException) as exc_info:
        pilot.resolve_workspace_context_for_request(_Connection(), request)

    assert exc_info.value.status_code == 400
    assert 'Invalid x-workspace-id header' in exc_info.value.detail
