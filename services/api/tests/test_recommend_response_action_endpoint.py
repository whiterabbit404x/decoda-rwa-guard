"""Tests for POST /incidents/{incident_id}/response-actions/recommend endpoint.

Covers:
- Creates a response_action row linked to the incident
- Second call returns the existing action (no duplicate)
- Returns response_action_id and created: True/False
- Returns 404 when incident not found in workspace
- Regression: INSERT uses named %(name)s params so placeholder/param count cannot mismatch
"""
from __future__ import annotations

import inspect
import re
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from services.api.app import pilot


class _Result:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _TrackingConnection:
    def __init__(self, incident_row=None, existing_action_row=None):
        self.executed: list[tuple[str, object]] = []
        self._incident_row = incident_row
        self._existing_action_row = existing_action_row
        self.committed = False

    def execute(self, statement, params=None):
        normalized = ' '.join(str(statement).split())
        self.executed.append((normalized, params))
        if 'FROM incidents WHERE id' in normalized:
            return _Result(self._incident_row)
        if 'FROM response_actions' in normalized and "mode = 'recommended'" in normalized:
            return _Result(self._existing_action_row)
        return _Result()

    def commit(self):
        self.committed = True


@contextmanager
def _fake_pg(connection):
    yield connection


def _bootstrap(monkeypatch, connection):
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(
        pilot, 'pg_connection', lambda: _fake_pg(connection)
    )
    monkeypatch.setattr(
        pilot,
        '_require_workspace_permission',
        lambda *_a, **_k: ({'id': 'user-1'}, {'workspace_id': 'ws-1'}),
    )
    monkeypatch.setattr(pilot, 'write_action_history', lambda *_a, **_k: None)


# ── Test 1: creates a response_action row linked to the incident ──────────────

def test_recommend_creates_response_action(monkeypatch):
    conn = _TrackingConnection(
        incident_row={'id': 'inc-1', 'source_alert_id': 'alert-1'},
        existing_action_row=None,
    )
    _bootstrap(monkeypatch, conn)

    result = pilot.recommend_response_action_for_incident(
        'inc-1', SimpleNamespace(headers={'x-workspace-id': 'ws-1'})
    )

    assert result['incident_id'] == 'inc-1'
    assert result['created'] is True
    assert result['response_action_id']

    inserts = [p for stmt, p in conn.executed if 'INSERT INTO response_actions' in stmt]
    assert len(inserts) == 1
    row = inserts[0]
    assert row['incident_id'] == 'inc-1'
    assert row['action_type'] == 'notify_team'
    assert row['mode'] == 'recommended'
    assert row['status'] == 'pending'
    assert conn.committed


# ── Test 2: second call returns existing action without duplicate ─────────────

def test_recommend_returns_existing_action_no_duplicate(monkeypatch):
    conn = _TrackingConnection(
        incident_row={'id': 'inc-1', 'source_alert_id': 'alert-1'},
        existing_action_row={'id': 'existing-ra-1'},
    )
    _bootstrap(monkeypatch, conn)

    result = pilot.recommend_response_action_for_incident(
        'inc-1', SimpleNamespace(headers={'x-workspace-id': 'ws-1'})
    )

    assert result['response_action_id'] == 'existing-ra-1'
    assert result['incident_id'] == 'inc-1'
    assert result['created'] is False

    inserts = [p for stmt, p in conn.executed if 'INSERT INTO response_actions' in stmt]
    assert len(inserts) == 0


# ── Test 3: returns 404 when incident not found ───────────────────────────────

def test_recommend_returns_404_for_missing_incident(monkeypatch):
    conn = _TrackingConnection(incident_row=None, existing_action_row=None)
    _bootstrap(monkeypatch, conn)

    with pytest.raises(HTTPException) as exc_info:
        pilot.recommend_response_action_for_incident(
            'nonexistent-id', SimpleNamespace(headers={'x-workspace-id': 'ws-1'})
        )

    assert exc_info.value.status_code == 404


# ── Test 4: links alert_id from incident's source_alert_id ───────────────────

def test_recommend_links_alert_id_from_incident(monkeypatch):
    conn = _TrackingConnection(
        incident_row={'id': 'inc-2', 'source_alert_id': 'alert-99'},
        existing_action_row=None,
    )
    _bootstrap(monkeypatch, conn)

    pilot.recommend_response_action_for_incident(
        'inc-2', SimpleNamespace(headers={'x-workspace-id': 'ws-1'})
    )

    inserts = [p for stmt, p in conn.executed if 'INSERT INTO response_actions' in stmt]
    assert len(inserts) == 1
    assert inserts[0]['alert_id'] == 'alert-99'


# ── Test 5: works when incident has no linked alert ───────────────────────────

def test_recommend_works_without_linked_alert(monkeypatch):
    conn = _TrackingConnection(
        incident_row={'id': 'inc-3', 'source_alert_id': None},
        existing_action_row=None,
    )
    _bootstrap(monkeypatch, conn)

    result = pilot.recommend_response_action_for_incident(
        'inc-3', SimpleNamespace(headers={'x-workspace-id': 'ws-1'})
    )

    assert result['created'] is True
    inserts = [p for stmt, p in conn.executed if 'INSERT INTO response_actions' in stmt]
    assert inserts[0]['alert_id'] is None


# ── Test 6: INSERT uses named params — placeholder/param count cannot mismatch ─

def test_recommend_insert_uses_named_params_not_positional(monkeypatch):
    """Regression for psycopg ProgrammingError: 23 placeholders, 22 parameters.

    The INSERT must use %(name)s named params so every column maps to an
    explicit dict key. If the params arg is a dict (not a tuple), psycopg
    validates each key against a named placeholder — count mismatches are
    impossible.
    """
    captured_params: list = []

    class _CapturingConn:
        def execute(self, statement, params=None):
            normalized = ' '.join(str(statement).split())
            if 'INSERT INTO response_actions' in normalized:
                captured_params.append(params)
                return _Result()
            if 'FROM incidents WHERE id' in normalized:
                return _Result({'id': 'inc-p', 'source_alert_id': None})
            if 'FROM response_actions' in normalized and "mode = 'recommended'" in normalized:
                return _Result(None)
            return _Result()

        def commit(self):
            pass

    @contextmanager
    def _fake_pg_named():
        yield _CapturingConn()

    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg_named)
    monkeypatch.setattr(
        pilot,
        '_require_workspace_permission',
        lambda *_a, **_k: ({'id': 'user-p'}, {'workspace_id': 'ws-p'}),
    )
    monkeypatch.setattr(pilot, 'write_action_history', lambda *_a, **_k: None)

    pilot.recommend_response_action_for_incident(
        'inc-p', SimpleNamespace(headers={'x-workspace-id': 'ws-p'})
    )

    assert len(captured_params) == 1, "Expected exactly one INSERT"
    params = captured_params[0]
    assert isinstance(params, dict), (
        f"INSERT params must be a dict (named), got {type(params).__name__}. "
        "Positional tuple params allow placeholder/count mismatches."
    )


# ── Test 7: INSERT params dict has exactly one key per column ─────────────────

def test_recommend_insert_param_dict_keys_match_columns(monkeypatch):
    """Every column in the INSERT must have a matching key in the params dict."""
    captured: list[dict] = []

    class _CapturingConnection:
        def execute(self, statement, params=None):
            normalized = ' '.join(str(statement).split())
            if 'INSERT INTO response_actions' in normalized:
                captured.append(params)
                return _Result()
            if 'FROM incidents WHERE id' in normalized:
                return _Result({'id': 'inc-x', 'source_alert_id': None})
            if 'FROM response_actions' in normalized and "mode = 'recommended'" in normalized:
                return _Result(None)
            return _Result()

        def commit(self):
            pass

    @contextmanager
    def _fake_pg_cap():
        yield _CapturingConnection()

    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg_cap)
    monkeypatch.setattr(
        pilot,
        '_require_workspace_permission',
        lambda *_a, **_k: ({'id': 'user-x'}, {'workspace_id': 'ws-x'}),
    )
    monkeypatch.setattr(pilot, 'write_action_history', lambda *_a, **_k: None)

    pilot.recommend_response_action_for_incident(
        'inc-x', SimpleNamespace(headers={'x-workspace-id': 'ws-x'})
    )

    assert len(captured) == 1, "Expected exactly one INSERT into response_actions"
    params = captured[0]
    assert isinstance(params, dict), f"Params should be a dict, got {type(params)}"
    src = inspect.getsource(pilot.recommend_response_action_for_incident)
    insert_match = re.search(
        r'INSERT INTO response_actions\s*\((.*?)\)\s*VALUES',
        src,
        re.DOTALL,
    )
    assert insert_match, "Could not find INSERT column list in source"
    columns = [c.strip() for c in insert_match.group(1).split(',')]
    assert set(params.keys()) == set(columns), (
        f"Dict keys {sorted(params.keys())} != columns {sorted(columns)}"
    )


# ── Test 8: idempotency — two calls return same action_id ────────────────────

def test_recommend_idempotent_second_call_returns_same_id(monkeypatch):
    """Calling recommend twice for the same incident must reuse the first action."""
    first_action_id: list[str] = []

    class _IdempotentConnection:
        def __init__(self):
            self._incident_row = {'id': 'inc-idem', 'source_alert_id': None}
            self._stored_action: dict | None = None

        def execute(self, statement, params=None):
            normalized = ' '.join(str(statement).split())
            if 'FROM incidents WHERE id' in normalized:
                return _Result(self._incident_row)
            if 'FROM response_actions' in normalized and "mode = 'recommended'" in normalized:
                return _Result(self._stored_action)
            if 'INSERT INTO response_actions' in normalized:
                assert isinstance(params, dict), "INSERT must use dict (named) params"
                action_id = params['id']
                first_action_id.append(action_id)
                self._stored_action = {'id': action_id}
            return _Result()

        def commit(self):
            pass

    conn = _IdempotentConnection()

    @contextmanager
    def _fake_pg_idem():
        yield conn

    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg_idem)
    monkeypatch.setattr(
        pilot,
        '_require_workspace_permission',
        lambda *_a, **_k: ({'id': 'user-i'}, {'workspace_id': 'ws-i'}),
    )
    monkeypatch.setattr(pilot, 'write_action_history', lambda *_a, **_k: None)

    req = SimpleNamespace(headers={'x-workspace-id': 'ws-i'})
    r1 = pilot.recommend_response_action_for_incident('inc-idem', req)
    r2 = pilot.recommend_response_action_for_incident('inc-idem', req)

    assert r1['created'] is True
    assert r2['created'] is False
    assert r1['response_action_id'] == r2['response_action_id']
    assert len(first_action_id) == 1, "INSERT must run exactly once"
