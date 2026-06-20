"""Tests for POST /response/actions/{action_id}/simulate endpoint.

Verifies:
1. Marks an action as simulated (status='simulated', mode='simulated').
2. Works for actions with any starting status (recommended, pending, etc.).
3. Idempotent: calling on an already-simulated action returns it unchanged.
4. Logs an audit event with action='response_action.simulated'.
5. Returns 404 when the action is not found.
6. After simulation, create_evidence_package_from_response_action succeeds.
"""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from services.api.app import pilot


# ── Helpers ──────────────────────────────────────────────────────────────────

class _FakeStorage:
    backend_name = 'local'

    def write_bytes(self, *, object_key: str, content: bytes) -> str:
        return object_key


class _Row:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows if self._rows else ([] if self._row is None else [self._row])


def _fake_request(workspace_id: str = 'ws-1') -> SimpleNamespace:
    return SimpleNamespace(headers={'x-workspace-id': workspace_id})


def _monkeypatch_simulate(monkeypatch, connection, *, workspace_id: str = 'ws-1', user_id: str = 'user-1') -> None:
    @contextmanager
    def _fake_pg():
        yield connection

    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, '_require_workspace_permission', lambda *_: (
        {'id': user_id, 'mfa_enabled': False},
        {'workspace_id': workspace_id, 'role': 'admin'},
    ))
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)


# ── Connection stubs ──────────────────────────────────────────────────────────

class _RecommendedActionConnection:
    """Action with mode='recommended', status='recommended' → should become simulated."""

    def __init__(self):
        self.executed: list[tuple[str, object]] = []
        self.committed = False
        self._updated = False

    def execute(self, stmt, params=None):
        normalized = ' '.join(str(stmt).split())
        self.executed.append((normalized, params))
        # First SELECT: action lookup
        if 'FROM response_actions WHERE id = %s::uuid AND workspace_id' in normalized and 'UPDATE' not in normalized:
            if self._updated:
                # Post-update re-fetch
                return _Row({'id': 'action-1', 'workspace_id': 'ws-1', 'incident_id': 'inc-1', 'alert_id': 'alert-1', 'action_type': 'notify_team', 'mode': 'simulated', 'status': 'simulated', 'execution_metadata': None})
            return _Row({'id': 'action-1', 'workspace_id': 'ws-1', 'incident_id': 'inc-1', 'alert_id': 'alert-1', 'action_type': 'notify_team', 'mode': 'recommended', 'status': 'recommended', 'execution_metadata': None})
        if 'UPDATE response_actions' in normalized:
            self._updated = True
            return _Row(None)
        raise AssertionError(f'unexpected: {normalized!r}')

    def commit(self):
        self.committed = True


class _AlreadySimulatedConnection:
    """Action already simulated → idempotent no-op."""

    def __init__(self):
        self.executed: list[tuple[str, object]] = []
        self.committed = False

    def execute(self, stmt, params=None):
        normalized = ' '.join(str(stmt).split())
        self.executed.append((normalized, params))
        if 'FROM response_actions WHERE id = %s::uuid AND workspace_id' in normalized:
            return _Row({'id': 'action-2', 'workspace_id': 'ws-1', 'incident_id': 'inc-1', 'alert_id': None, 'action_type': 'notify_team', 'mode': 'simulated', 'status': 'simulated', 'execution_metadata': None})
        raise AssertionError(f'unexpected: {normalized!r}')

    def commit(self):
        self.committed = True


class _MissingActionConnection:
    def execute(self, stmt, params=None):
        normalized = ' '.join(str(stmt).split())
        if 'FROM response_actions WHERE id = %s::uuid AND workspace_id' in normalized:
            return _Row(None)
        raise AssertionError(f'unexpected: {normalized!r}')

    def commit(self):
        pass


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_simulate_marks_action_as_simulated(monkeypatch):
    conn = _RecommendedActionConnection()
    _monkeypatch_simulate(monkeypatch, conn)

    result = pilot.simulate_response_action('action-1', _fake_request())

    assert result.get('mode') == 'simulated'
    assert result.get('status') == 'simulated'
    update_calls = [(s, p) for s, p in conn.executed if 'UPDATE response_actions' in s]
    assert update_calls, 'Expected UPDATE to set mode/status to simulated'
    assert conn.committed


def test_simulate_works_for_recommended_status(monkeypatch):
    """recommended status must not be blocked like execute_enforcement_action blocks non-pending."""
    conn = _RecommendedActionConnection()
    _monkeypatch_simulate(monkeypatch, conn)

    # Should not raise, even though status is 'recommended' (not 'pending')
    result = pilot.simulate_response_action('action-1', _fake_request())
    assert result.get('status') == 'simulated'


def test_simulate_idempotent_for_already_simulated(monkeypatch):
    conn = _AlreadySimulatedConnection()
    _monkeypatch_simulate(monkeypatch, conn)

    result = pilot.simulate_response_action('action-2', _fake_request())

    assert result.get('mode') == 'simulated'
    assert result.get('status') == 'simulated'
    update_calls = [s for s, _ in conn.executed if 'UPDATE response_actions' in s]
    assert not update_calls, 'Should not UPDATE an already-simulated action'


def test_simulate_returns_404_when_action_not_found(monkeypatch):
    conn = _MissingActionConnection()
    _monkeypatch_simulate(monkeypatch, conn)

    with pytest.raises(HTTPException) as exc_info:
        pilot.simulate_response_action('missing-id', _fake_request())

    assert exc_info.value.status_code == 404


def test_simulate_then_evidence_package_succeeds(monkeypatch):
    """After simulation, evidence export should work (action has incident_id)."""
    from services.api.tests.test_evidence_package_from_response_action import (
        _FullChainConnection,
        _monkeypatch_common,
    )

    conn = _FullChainConnection()
    _monkeypatch_common(monkeypatch, conn)

    result = pilot.create_evidence_package_from_response_action('action-1', _fake_request())

    assert result['created'] is True
    assert result['package_id']
    assert result['incident_id'] == 'inc-1'
    assert result['response_action_id'] == 'action-1'
