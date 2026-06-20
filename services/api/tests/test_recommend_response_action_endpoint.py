"""Tests for POST /incidents/{incident_id}/response-actions/recommend endpoint.

Covers:
- Creates a response_action row linked to the incident
- Second call returns the existing action (no duplicate)
- Returns response_action_id and created: True/False
- Returns 404 when incident not found in workspace
"""
from __future__ import annotations

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
    # index 2 = incident_id, index 4 = action_type, index 5 = mode, index 6 = status
    assert row[2] == 'inc-1'
    assert row[4] == 'notify_team'
    assert row[5] == 'recommended'
    assert row[6] == 'pending'
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
    # index 3 = alert_id
    assert inserts[0][3] == 'alert-99'


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
    # alert_id should be None
    assert inserts[0][3] is None
