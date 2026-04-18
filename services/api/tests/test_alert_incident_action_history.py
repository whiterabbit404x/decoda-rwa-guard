from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

from fastapi.testclient import TestClient

from services.api.app import main as api_main
from services.api.app import pilot


def test_escalate_alert_to_incident_updates_bidirectional_links_and_history(monkeypatch):
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
            normalized = ' '.join(str(statement).split())
            executed.append((normalized, params))
            if 'SELECT id, target_id, analysis_run_id, title, severity, summary, detection_id FROM alerts' in normalized:
                return _Result({'id': 'alert-1', 'target_id': 'target-1', 'analysis_run_id': 'run-1', 'title': 'Large transfer', 'severity': 'high', 'summary': 'Escalate me', 'detection_id': 'det-1'})
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

    request = SimpleNamespace(headers={'x-workspace-id': 'ws-1'})
    response = pilot.escalate_alert_to_incident('alert-1', {'title': 'Escalated'}, request)

    assert response['alert_id'] == 'alert-1'
    assert response['incident_id']

    assert any('UPDATE alerts SET incident_id = %s::uuid' in statement for statement, _ in executed)
    history_rows = [params for statement, params in executed if 'INSERT INTO action_history' in statement]
    assert len(history_rows) == 2
    assert any(params[6] == 'alert.escalated_to_incident' for params in history_rows)
    assert any(params[6] == 'incident.created_from_alert' for params in history_rows)


def test_patch_handlers_write_action_history(monkeypatch):
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
            if 'SELECT id FROM alerts WHERE id = %s AND workspace_id = %s' in normalized:
                return _Result({'id': 'alert-1'})
            if 'SELECT id, timeline, workflow_status, assignee_user_id, resolution_note FROM incidents' in normalized:
                return _Result({'id': 'inc-1', 'timeline': []})
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
    pilot.patch_alert('alert-1', {'status': 'investigating', 'assigned_to': '11111111-1111-1111-1111-111111111111', 'incident_id': '22222222-2222-2222-2222-222222222222'}, request)
    pilot.patch_incident('inc-1', {'status': 'in_progress', 'owner': '33333333-3333-3333-3333-333333333333', 'source_alert_id': '44444444-4444-4444-4444-444444444444'}, request)

    history_rows = [params for statement, params in executed if 'INSERT INTO action_history' in statement]
    assert len(history_rows) >= 2
    assert any(params[6] == 'alert.investigating' for params in history_rows)
    assert any(params[6] == 'incident.investigating' for params in history_rows)
    assert any('UPDATE alerts SET incident_id = %s::uuid' in statement for statement, _ in executed)


def test_history_actions_endpoint_and_linked_ids_in_payloads(monkeypatch):
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
            normalized = ' '.join(str(statement).split())
            if 'FROM action_history' in normalized:
                return _Result(rows=[{'id': 'h-1', 'object_type': 'alert', 'object_id': 'alert-1', 'action_type': 'alert.escalated_to_incident'}])
            if 'FROM alerts a' in normalized:
                return _Result(rows=[{'id': 'alert-1', 'detection_id': 'det-1', 'incident_id': 'inc-1', 'assigned_to': 'user-2', 'evidence_summary': 'summary'}])
            if 'FROM incidents i' in normalized:
                return _Result(rows=[{'id': 'inc-1', 'source_alert_id': 'alert-1', 'status': 'open', 'workflow_status': 'open'}])
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

    history_response = client.get('/history/actions?object_type=alert&object_id=alert-1', headers={'authorization': 'Bearer token', 'x-workspace-id': 'ws-1'})
    alerts_response = client.get('/alerts', headers={'authorization': 'Bearer token', 'x-workspace-id': 'ws-1'})
    incidents_response = client.get('/incidents', headers={'authorization': 'Bearer token', 'x-workspace-id': 'ws-1'})

    assert history_response.status_code == 200
    assert history_response.json()['history'][0]['action_type'] == 'alert.escalated_to_incident'
    assert alerts_response.json()['alerts'][0]['incident_id'] == 'inc-1'
    assert incidents_response.json()['incidents'][0]['source_alert_id'] == 'alert-1'
