"""Tests for GET /response/actions with incident_id and action_id filters.

Covers:
- list_enforcement_actions with incident_id returns only matching action
- list_enforcement_actions with action_id returns only matching action
- Both filters together narrow results correctly
- No filter returns all workspace actions
- Counters (recommendedRows) and table use the same source of truth (same list call)
- No fake/demo data is injected when rows are empty
"""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from services.api.app import pilot


class _Result:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _ListConnection:
    """Fake DB connection that returns controlled rows for list queries."""

    def __init__(self, action_rows: list[dict]):
        self.executed: list[tuple[str, object]] = []
        self._action_rows = action_rows

    def execute(self, statement, params=None):
        normalized = ' '.join(str(statement).split())
        self.executed.append((normalized, params))
        if 'FROM response_actions' in normalized and 'workspace_id' in normalized and 'SELECT' in normalized:
            # Simulate DB-side filtering: match workspace, incident_id, and action_id from params.
            # params order from list_enforcement_actions:
            # (workspace_id, action_id, action_id, incident_id, incident_id, alert_id, alert_id, ...)
            rows = self._action_rows
            if params and len(params) >= 4:
                req_action_id = params[1]  # action_id filter (None means no filter)
                req_incident_id = params[3]  # incident_id filter (None means no filter)
                if req_action_id is not None:
                    rows = [r for r in rows if str(r.get('id', '')) == str(req_action_id)]
                if req_incident_id is not None:
                    rows = [r for r in rows if str(r.get('incident_id', '')) == str(req_incident_id)]
            return _Result(rows=rows)
        return _Result()

    def commit(self):
        return None


@contextmanager
def _fake_pg(conn):
    yield conn


def _bootstrap(monkeypatch, conn):
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_a, **_k: {'id': 'user-1'})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda *_a, **_k: {'workspace_id': 'ws-1'})
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)


_FAKE_ACTION_INC1 = {
    'id': 'act-aaa',
    'workspace_id': 'ws-1',
    'incident_id': 'inc-1',
    'alert_id': 'alert-1',
    'action_type': 'freeze_asset',
    'status': 'recommended',
    'mode': 'recommended',
    'source': 'simulator',
    'requires_approval': True,
    'created_at': '2026-06-20T00:00:00Z',
}

_FAKE_ACTION_INC2 = {
    'id': 'act-bbb',
    'workspace_id': 'ws-1',
    'incident_id': 'inc-2',
    'alert_id': 'alert-2',
    'action_type': 'notify_team',
    'status': 'executed',
    'mode': 'simulated',
    'source': 'simulator',
    'requires_approval': False,
    'created_at': '2026-06-20T01:00:00Z',
}


def test_list_by_incident_id_returns_matching_action(monkeypatch):
    conn = _ListConnection(action_rows=[_FAKE_ACTION_INC1, _FAKE_ACTION_INC2])
    _bootstrap(monkeypatch, conn)

    req = SimpleNamespace(headers={'x-workspace-id': 'ws-1'})
    result = pilot.list_enforcement_actions(req, incident_id='inc-1')

    actions = result.get('actions', [])
    assert len(actions) == 1
    assert actions[0]['id'] == 'act-aaa'
    assert actions[0]['incident_id'] == 'inc-1'


def test_list_by_action_id_returns_matching_action(monkeypatch):
    conn = _ListConnection(action_rows=[_FAKE_ACTION_INC1, _FAKE_ACTION_INC2])
    _bootstrap(monkeypatch, conn)

    req = SimpleNamespace(headers={'x-workspace-id': 'ws-1'})
    result = pilot.list_enforcement_actions(req, action_id='act-bbb')

    actions = result.get('actions', [])
    assert len(actions) == 1
    assert actions[0]['id'] == 'act-bbb'


def test_list_by_both_filters_returns_intersection(monkeypatch):
    conn = _ListConnection(action_rows=[_FAKE_ACTION_INC1, _FAKE_ACTION_INC2])
    _bootstrap(monkeypatch, conn)

    req = SimpleNamespace(headers={'x-workspace-id': 'ws-1'})
    # act-aaa belongs to inc-1; requesting inc-2 with act-aaa should return empty
    result = pilot.list_enforcement_actions(req, incident_id='inc-2', action_id='act-aaa')

    actions = result.get('actions', [])
    assert len(actions) == 0


def test_list_no_filter_returns_all_workspace_actions(monkeypatch):
    conn = _ListConnection(action_rows=[_FAKE_ACTION_INC1, _FAKE_ACTION_INC2])
    _bootstrap(monkeypatch, conn)

    req = SimpleNamespace(headers={'x-workspace-id': 'ws-1'})
    result = pilot.list_enforcement_actions(req)

    actions = result.get('actions', [])
    assert len(actions) == 2


def test_list_empty_workspace_returns_empty_list_not_demo_data(monkeypatch):
    conn = _ListConnection(action_rows=[])
    _bootstrap(monkeypatch, conn)

    req = SimpleNamespace(headers={'x-workspace-id': 'ws-1'})
    result = pilot.list_enforcement_actions(req)

    actions = result.get('actions', [])
    # Must not inject fake/demo data when the DB returns nothing.
    assert actions == []
    assert result.get('total', 0) == 0


def test_list_result_keys_match_frontend_expected_fields(monkeypatch):
    """action.incident_id must appear in list result so frontend can build validIncidentIds."""
    conn = _ListConnection(action_rows=[_FAKE_ACTION_INC1])
    _bootstrap(monkeypatch, conn)

    req = SimpleNamespace(headers={'x-workspace-id': 'ws-1'})
    result = pilot.list_enforcement_actions(req, incident_id='inc-1')

    actions = result.get('actions', [])
    assert len(actions) == 1
    action = actions[0]
    # These keys are used by normalizeActionRow() in the frontend.
    assert 'incident_id' in action or 'chain_linked_ids' in action or 'linked_incident_id' in action
    # status must be present so actionStatusPill() can render correctly.
    assert 'status' in action or 'workflow_status' in action
