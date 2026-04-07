from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

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


def test_patch_incident_requires_admin_for_assign_and_close(monkeypatch: pytest.MonkeyPatch) -> None:
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
        pilot.patch_incident('inc-1', {'workflow_status': 'closed', 'assignee_user_id': '11111111-1111-1111-1111-111111111111'}, request)
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


def test_admin_can_assign_and_close_incident(monkeypatch: pytest.MonkeyPatch) -> None:
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
        {'workflow_status': 'closed', 'assignee_user_id': '11111111-1111-1111-1111-111111111111', 'resolution_note': 'Contained and closed'},
        request,
    )
    assert result['workflow_status'] == 'closed'
