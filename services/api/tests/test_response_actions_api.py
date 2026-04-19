from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

from services.api.app import pilot


def test_create_response_action_translates_legacy_payload_and_writes_history(monkeypatch):
    executed: list[tuple[str, object]] = []

    class _Result:
        def __init__(self, row=None, rows=None):
            self._row = row
            self._rows = rows or []

        def fetchone(self):
            return self._row

        def fetchall(self):
            return self._rows

    class _Connection:
        def execute(self, statement, params=None):
            executed.append((' '.join(str(statement).split()), params))
            return _Result()

        def commit(self):
            return None

    @contextmanager
    def _fake_pg():
        yield _Connection()

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda *_: ({'id': 'admin-1'}, {'workspace_id': 'ws-1'}))
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)

    request = SimpleNamespace(headers={'x-workspace-id': 'ws-1'})
    payload = {'action_type': 'revoke_erc20_approval', 'dry_run': True, 'params': {'token_contract': '0x1111111111111111111111111111111111111111', 'spender': '0x2222222222222222222222222222222222222222'}}
    response = pilot.create_enforcement_action(payload, request)

    assert response['action_type'] == 'revoke_approval'
    assert response['dry_run'] is True
    insert_calls = [params for statement, params in executed if 'INSERT INTO response_actions' in statement]
    assert insert_calls
    assert insert_calls[0][4] == 'revoke_approval'
    assert insert_calls[0][5] == 'simulated'
    assert insert_calls[0][6] == 'pending'
    history_calls = [params for statement, params in executed if 'INSERT INTO action_history' in statement]
    assert history_calls
    assert any(params[6] == 'response_action.created' for params in history_calls)


def test_execute_response_action_returns_back_compat_dry_run_flag(monkeypatch):
    executed: list[tuple[str, object]] = []

    class _Result:
        def __init__(self, row=None):
            self._row = row

        def fetchone(self):
            return self._row

    class _Connection:
        def execute(self, statement, params=None):
            normalized = ' '.join(str(statement).split())
            executed.append((normalized, params))
            if 'SELECT * FROM response_actions WHERE id = %s AND workspace_id = %s' in normalized:
                return _Result({'id': 'act-1', 'status': 'pending', 'mode': 'simulated', 'action_type': 'notify_team', 'execution_metadata': {}})
            return _Result()

        def commit(self):
            return None

    @contextmanager
    def _fake_pg():
        yield _Connection()

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda *_: ({'id': 'admin-1'}, {'workspace_id': 'ws-1'}))
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)

    request = SimpleNamespace(headers={'x-workspace-id': 'ws-1'})
    response = pilot.execute_enforcement_action('act-1', request)

    assert response['status'] == 'executed'
    assert response['dry_run'] is True
    assert any('UPDATE response_actions SET status = \'executed\'' in statement for statement, _ in executed)


def test_list_response_actions_returns_supported_fields(monkeypatch):
    class _Result:
        def __init__(self, rows=None):
            self._rows = rows or []

        def fetchall(self):
            return self._rows

    class _Connection:
        def execute(self, statement, params=None):
            normalized = ' '.join(str(statement).split())
            if 'FROM response_actions' in normalized:
                return _Result(rows=[{
                    'id': 'act-1',
                    'action_type': 'freeze_wallet',
                    'mode': 'simulated',
                    'status': 'pending',
                    'result_summary': 'Queued',
                    'operator_notes': 'note',
                    'created_at': '2026-01-01T00:00:00Z',
                    'executed_at': None,
                    'incident_id': 'inc-1',
                    'alert_id': 'alert-1',
                }])
            return _Result()

    @contextmanager
    def _fake_pg():
        yield _Connection()

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_: {'id': 'admin-1'})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda *_: {'workspace_id': 'ws-1'})

    request = SimpleNamespace(headers={'x-workspace-id': 'ws-1'})
    response = pilot.list_enforcement_actions(request, incident_id='inc-1')
    action = response['actions'][0]
    assert action['action_type'] == 'freeze_wallet'
    assert action['status'] == 'pending'
    assert action['mode'] == 'simulated'
