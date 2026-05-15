"""Tests for detection → alert → incident → response_action chain linkage."""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

from services.api.app import pilot


class _Result:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


def _bootstrap(monkeypatch, connection):
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(connection))
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_: {'id': 'user-1'})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda *_: {'workspace_id': 'ws-1'})


@contextmanager
def _fake_pg(connection):
    yield connection


# ── get_alert: linked_action_id lookup ────────────────────────────

class _AlertWithActionConn:
    def __init__(self):
        self.calls: list[tuple[str, object]] = []

    def execute(self, query, params=None):
        normalized = ' '.join(str(query).split())
        self.calls.append((normalized, params))
        if 'SELECT * FROM alerts WHERE id = %s AND workspace_id = %s' in normalized:
            return _Result(row={
                'id': 'alert-1', 'workspace_id': 'ws-1', 'detection_id': 'det-1',
                'incident_id': 'inc-1', 'alert_type': 'transfer_anomaly',
                'title': 'Large transfer', 'severity': 'high', 'status': 'open',
                'source_service': 'threat', 'summary': 'summary', 'payload': {},
                'target_id': None, 'module_key': None, 'acknowledged_at': None,
                'acknowledged_by_user_id': None, 'resolved_at': None,
                'resolved_by_user_id': None, 'findings': None, 'assigned_to': None,
                'evidence_summary': None, 'source_alert_id': None,
                'detection_event_id': None, 'detection_event_workspace_id': None,
                'created_at': '2026-05-10T10:00:00Z',
            })
        if 'SELECT id, event_type, details, created_at FROM alert_events' in normalized:
            return _Result(rows=[])
        if 'FROM evidence WHERE alert_id' in normalized:
            return _Result(rows=[])
        if 'SELECT id FROM response_actions' in normalized:
            return _Result(row={'id': 'action-abc'})
        return _Result()


def test_get_alert_returns_linked_action_id_when_response_action_exists(monkeypatch):
    conn = _AlertWithActionConn()
    _bootstrap(monkeypatch, conn)
    request = SimpleNamespace(headers={'x-workspace-id': 'ws-1'})

    result = pilot.get_alert('alert-1', request)

    alert = result['alert']
    assert alert['linked_action_id'] == 'action-abc'
    assert alert['chain_linked_ids']['action_id'] == 'action-abc'
    assert alert['chain_linked_ids']['detection_id'] == 'det-1'
    assert alert['chain_linked_ids']['alert_id'] == 'alert-1'
    assert alert['chain_linked_ids']['incident_id'] == 'inc-1'


class _AlertNoActionConn(_AlertWithActionConn):
    def execute(self, query, params=None):
        normalized = ' '.join(str(query).split())
        self.calls.append((normalized, params))
        if 'SELECT * FROM alerts WHERE id = %s AND workspace_id = %s' in normalized:
            return _Result(row={
                'id': 'alert-2', 'workspace_id': 'ws-1', 'detection_id': None,
                'incident_id': None, 'alert_type': 'transfer_anomaly',
                'title': 'No action alert', 'severity': 'low', 'status': 'open',
                'source_service': 'threat', 'summary': 'summary', 'payload': {},
                'target_id': None, 'module_key': None, 'acknowledged_at': None,
                'acknowledged_by_user_id': None, 'resolved_at': None,
                'resolved_by_user_id': None, 'findings': None, 'assigned_to': None,
                'evidence_summary': None, 'source_alert_id': None,
                'detection_event_id': None, 'detection_event_workspace_id': None,
                'created_at': '2026-05-10T10:00:00Z',
            })
        if 'SELECT id, event_type, details, created_at FROM alert_events' in normalized:
            return _Result(rows=[])
        if 'FROM evidence WHERE alert_id' in normalized:
            return _Result(rows=[])
        if 'SELECT id FROM response_actions' in normalized:
            return _Result(row=None)
        return _Result()


def test_get_alert_linked_action_id_is_null_when_no_action_exists(monkeypatch):
    conn = _AlertNoActionConn()
    _bootstrap(monkeypatch, conn)
    request = SimpleNamespace(headers={'x-workspace-id': 'ws-1'})

    result = pilot.get_alert('alert-2', request)

    alert = result['alert']
    assert alert['linked_action_id'] is None
    assert alert['chain_linked_ids']['action_id'] is None


def test_get_alert_response_action_query_is_workspace_scoped(monkeypatch):
    conn = _AlertWithActionConn()
    _bootstrap(monkeypatch, conn)
    request = SimpleNamespace(headers={'x-workspace-id': 'ws-1'})

    pilot.get_alert('alert-1', request)

    action_queries = [(q, p) for q, p in conn.calls if 'SELECT id FROM response_actions' in q]
    assert len(action_queries) == 1
    query, params = action_queries[0]
    assert 'workspace_id = %s' in query
    assert params[0] == 'ws-1'


# ── _response_action_payload: chain_linked_ids ────────────────────

def test_response_action_payload_includes_chain_linked_ids():
    action = {
        'id': 'ra-1',
        'mode': 'simulated',
        'status': 'pending',
        'incident_id': 'inc-1',
        'alert_id': 'alert-1',
        'execution_metadata': {
            'chain_linked_ids': {
                'detection_id': 'det-1',
                'alert_id': 'alert-1',
                'incident_id': 'inc-1',
            },
        },
    }
    result = pilot._response_action_payload(action)

    assert result['chain_linked_ids']['alert_id'] == 'alert-1'
    assert result['chain_linked_ids']['incident_id'] == 'inc-1'
    assert result['chain_linked_ids']['action_id'] == 'ra-1'
    assert result['chain_linked_ids']['detection_id'] == 'det-1'


def test_response_action_payload_chain_linked_ids_null_when_no_links():
    action = {
        'id': 'ra-2',
        'mode': 'simulated',
        'status': 'pending',
        'incident_id': None,
        'alert_id': None,
        'execution_metadata': {},
    }
    result = pilot._response_action_payload(action)

    assert result['chain_linked_ids']['alert_id'] is None
    assert result['chain_linked_ids']['incident_id'] is None
    assert result['chain_linked_ids']['action_id'] == 'ra-2'
    assert result['chain_linked_ids']['detection_id'] is None


def test_response_action_payload_uses_row_ids_over_metadata():
    action = {
        'id': 'ra-3',
        'mode': 'live',
        'status': 'pending',
        'incident_id': 'inc-row',
        'alert_id': 'alert-row',
        'execution_metadata': {
            'chain_linked_ids': {
                'incident_id': 'inc-meta',
                'alert_id': 'alert-meta',
            },
        },
    }
    result = pilot._response_action_payload(action)

    assert result['chain_linked_ids']['alert_id'] == 'alert-row'
    assert result['chain_linked_ids']['incident_id'] == 'inc-row'


# ── Cross-workspace isolation ──────────────────────────────────────

def test_get_alert_404_for_wrong_workspace(monkeypatch):
    class _WrongWsConn:
        def execute(self, query, params=None):
            normalized = ' '.join(str(query).split())
            if 'SELECT * FROM alerts WHERE id = %s AND workspace_id = %s' in normalized:
                return _Result(row=None)
            return _Result()

    conn = _WrongWsConn()
    _bootstrap(monkeypatch, conn)
    request = SimpleNamespace(headers={'x-workspace-id': 'ws-2'})

    try:
        pilot.get_alert('alert-1', request)
        assert False, 'Expected 404'
    except Exception as exc:
        assert '404' in str(exc) or 'not found' in str(exc).lower()


def test_list_enforcement_actions_query_is_workspace_scoped(monkeypatch):
    executed: list[tuple[str, object]] = []

    class _Conn:
        def execute(self, query, params=None):
            normalized = ' '.join(str(query).split())
            executed.append((normalized, params))
            return _Result(rows=[])

    conn = _Conn()
    _bootstrap(monkeypatch, conn)
    request = SimpleNamespace(headers={'x-workspace-id': 'ws-1'})

    pilot.list_enforcement_actions(request)

    action_queries = [(q, p) for q, p in executed if 'FROM response_actions' in q]
    assert len(action_queries) >= 1
    query, params = action_queries[0]
    assert 'workspace_id = %s' in query
    assert params[0] == 'ws-1'


# ── Missing link truthfulness ──────────────────────────────────────

def test_response_action_payload_does_not_invent_detection_id():
    action = {
        'id': 'ra-4',
        'mode': 'simulated',
        'status': 'pending',
        'incident_id': 'inc-1',
        'alert_id': 'alert-1',
        'execution_metadata': {
            'chain_linked_ids': {},
        },
    }
    result = pilot._response_action_payload(action)

    assert result['chain_linked_ids']['detection_id'] is None


def test_get_alert_missing_detection_id_is_null_not_invented(monkeypatch):
    conn = _AlertNoActionConn()
    _bootstrap(monkeypatch, conn)
    request = SimpleNamespace(headers={'x-workspace-id': 'ws-1'})

    result = pilot.get_alert('alert-2', request)

    alert = result['alert']
    assert alert['chain_linked_ids']['detection_id'] is None
    assert alert['linked_detection_id'] is None
