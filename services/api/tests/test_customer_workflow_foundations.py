from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from services.api.app import main as api_main
from services.api.app import pilot


def test_validate_target_payload_accepts_workspace_target_shape() -> None:
    payload = {
        'name': 'Treasury Settlement Router',
        'target_type': 'contract',
        'chain_network': 'ethereum-mainnet',
        'contract_identifier': '0xabc',
        'wallet_address': '0x1111111111111111111111111111111111111111',
        'tags': ['treasury', 'critical'],
        'severity_preference': 'high',
        'enabled': True,
    }
    validated = pilot._validate_target_payload(payload)
    assert validated['name'] == 'Treasury Settlement Router'
    assert validated['target_type'] == 'contract'
    assert validated['tags'] == ['treasury', 'critical']


def test_validate_target_payload_rejects_invalid_wallet_address() -> None:
    with pytest.raises(HTTPException):
        pilot._validate_target_payload({
            'name': 'Bad wallet',
            'target_type': 'wallet',
            'chain_network': 'ethereum-mainnet',
            'wallet_address': 'not-an-address',
        })


def test_templates_are_onboarding_only_catalog() -> None:
    payload = pilot.list_templates()
    assert payload['templates']
    assert all('module' in template for template in payload['templates'])


def test_patch_incident_requires_admin_for_assign_and_resolve(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Result:
        def __init__(self, row=None):
            self._row = row

        def fetchone(self):
            return self._row

    class _Connection:
        def execute(self, statement, params=None):
            if 'SELECT id, timeline, workflow_status, assignee_user_id, resolution_note FROM incidents' in statement:
                return _Result({'id': 'inc-1', 'timeline': []})
            return _Result()

        def commit(self):
            return None

    @contextmanager
    def _fake_pg():
        yield _Connection()

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda connection: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda connection, request: (_ for _ in ()).throw(HTTPException(status_code=403, detail='forbidden')))

    request = SimpleNamespace(headers={'x-workspace-id': 'ws-1'}, client=SimpleNamespace(host='127.0.0.1'))
    with pytest.raises(HTTPException) as exc:
        pilot.patch_incident('inc-1', {'workflow_status': 'resolved', 'assignee_user_id': '11111111-1111-1111-1111-111111111111'}, request)
    assert exc.value.status_code == 403


def test_append_timeline_note_writes_incident_audit_record(monkeypatch: pytest.MonkeyPatch) -> None:
    executed: list[tuple[str, object]] = []

    class _Result:
        def __init__(self, row=None):
            self._row = row

        def fetchone(self):
            return self._row

    class _Connection:
        def execute(self, statement, params=None):
            executed.append((statement, params))
            if 'SELECT id FROM incidents' in statement:
                return _Result({'id': 'inc-2'})
            return _Result()

        def commit(self):
            return None

    @contextmanager
    def _fake_pg():
        yield _Connection()

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda connection: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda connection, request: ({'id': 'user-1'}, {'workspace_id': 'ws-1'}))

    request = SimpleNamespace(headers={'x-workspace-id': 'ws-1', 'x-request-id': 'req-1'}, client=SimpleNamespace(host='10.0.0.2'))
    payload = {'message': 'Investigation opened'}
    result = pilot.append_incident_timeline_note('inc-2', payload, request)

    assert result['event_type'] == 'note'
    audit_calls = [params for statement, params in executed if 'INSERT INTO audit_logs' in statement]
    assert audit_calls
    assert 'incident.timeline_note_added' in audit_calls[0][3]


def test_admin_can_assign_and_resolve_incident(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Result:
        def __init__(self, row=None):
            self._row = row

        def fetchone(self):
            return self._row

    class _Connection:
        def execute(self, statement, params=None):
            if 'SELECT id, timeline, workflow_status, assignee_user_id, resolution_note FROM incidents' in statement:
                return _Result({'id': 'inc-1', 'timeline': []})
            return _Result()

        def commit(self):
            return None

    @contextmanager
    def _fake_pg():
        yield _Connection()

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda connection: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda connection, request: ({'id': 'admin-1'}, {'workspace_id': 'ws-1'}))

    request = SimpleNamespace(headers={'x-workspace-id': 'ws-1', 'x-request-id': 'req-2'}, client=SimpleNamespace(host='127.0.0.1'))
    result = pilot.patch_incident(
        'inc-1',
        {'workflow_status': 'resolved', 'assignee_user_id': '11111111-1111-1111-1111-111111111111', 'resolution_note': 'Contained and closed'},
        request,
    )
    assert result['workflow_status'] == 'resolved'


def test_incidents_route_open_filter_is_schema_compatible_without_resolved_at(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Result:
        def __init__(self, row=None, rows=None):
            self._row = row or {}
            self._rows = rows or []

        def fetchone(self):
            return self._row

        def fetchall(self):
            return self._rows

    class _Connection:
        def execute(self, statement, params=None):
            normalized = ' '.join(str(statement).split())
            if 'SELECT i.id, i.event_type, i.title, i.severity, i.status, i.workflow_status' in normalized:
                if 'resolved_at' in normalized:
                    raise RuntimeError('resolved_at column does not exist')
                return _Result(rows=[{'id': 'inc-1', 'workflow_status': 'open', 'status': 'open', 'title': 'Open incident'}])
            return _Result()

    @contextmanager
    def _fake_pg():
        yield _Connection()

    client = TestClient(api_main.app)
    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_a, **_k: {'id': 'user-1'})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda *_a, **_k: {'workspace_id': 'ws-1', 'workspace': {'id': 'ws-1'}})

    response = client.get('/incidents?status_value=open', headers={'authorization': 'Bearer token', 'x-workspace-id': 'ws-1'})
    assert response.status_code == 200
    payload = response.json()
    assert len(payload['incidents']) == 1
    assert payload['incidents'][0]['workflow_status'] == 'open'


def test_create_asset_insert_placeholder_count_matches_params(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Result:
        def __init__(self, row=None):
            self._row = row

        def fetchone(self):
            return self._row

    class _Connection:
        def execute(self, statement, params=None):
            normalized = ' '.join(str(statement).split())
            if 'SELECT COUNT(*) AS count FROM assets' in normalized:
                return _Result({'count': 0})
            if 'SELECT id FROM assets WHERE workspace_id = %s' in normalized:
                return _Result(None)
            if 'INSERT INTO assets (' in normalized:
                placeholder_count = normalized.count('%s')
                assert params is not None
                assert placeholder_count == len(params)
            return _Result()

        def commit(self):
            return None

    @contextmanager
    def _fake_pg():
        yield _Connection()

    client = TestClient(api_main.app)
    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda *_a, **_k: ({'id': 'user-1'}, {'workspace_id': 'ws-1'}))
    monkeypatch.setattr(pilot, '_workspace_plan', lambda *_a, **_k: {'max_targets': 10})
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)

    response = client.post(
        '/assets',
        headers={'authorization': 'Bearer token', 'x-workspace-id': 'ws-1'},
        json={
            'name': 'Treasury Wallet',
            'asset_type': 'wallet',
            'chain_network': 'ethereum-mainnet',
            'identifier': '0x1111111111111111111111111111111111111111',
            'enabled': True,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload['name'] == 'Treasury Wallet'


def test_patch_incident_rejects_cross_workspace_source_alert_id(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Result:
        def __init__(self, row=None):
            self._row = row

        def fetchone(self):
            return self._row

    class _Connection:
        def execute(self, statement, params=None):
            normalized = ' '.join(str(statement).split())
            if 'SELECT id, timeline, workflow_status, assignee_user_id, resolution_note FROM incidents' in normalized:
                return _Result({'id': 'inc-1', 'timeline': []})
            if 'SELECT id FROM alerts WHERE id = %s AND workspace_id = %s' in normalized:
                return _Result(None)
            return _Result()

        def commit(self):
            return None

    @contextmanager
    def _fake_pg():
        yield _Connection()

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda connection: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda connection, request: ({'id': 'admin-1'}, {'workspace_id': 'ws-1'}))

    request = SimpleNamespace(headers={'x-workspace-id': 'ws-1', 'x-request-id': 'req-2'}, client=SimpleNamespace(host='127.0.0.1'))
    with pytest.raises(HTTPException) as exc:
        pilot.patch_incident('inc-1', {'workflow_status': 'open', 'source_alert_id': 'alert-x'}, request)
    assert exc.value.status_code == 404
    assert exc.value.detail == 'Alert not found.'
