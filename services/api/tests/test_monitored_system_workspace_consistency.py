from __future__ import annotations

import logging
from contextlib import contextmanager

import pytest
from fastapi import HTTPException

from services.api.app import pilot


class _Conn:
    def __init__(self):
        self.commits = 0

    def commit(self):
        self.commits += 1


@contextmanager
def _fake_pg(conn: _Conn):
    yield conn


class _Request:
    def __init__(self, workspace_id: str = 'ws-1'):
        self.headers = {'authorization': 'Bearer token', 'x-workspace-id': workspace_id}


def test_reconcile_workspace_returns_queryable_rows_and_count_matches(monkeypatch):
    conn = _Conn()
    request = _Request('ws-1')
    rows = [
        {'id': 'ms-1', 'workspace_id': 'ws-1', 'target_id': 't-1', 'asset_id': 'a-1'},
        {'id': 'ms-2', 'workspace_id': 'ws-1', 'target_id': 't-2', 'asset_id': 'a-2'},
    ]

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda *_a, **_k: ({'id': 'user-1'}, {'workspace_id': 'ws-1', 'workspace': {'id': 'ws-1'}}))
    monkeypatch.setattr(
        pilot,
        'reconcile_enabled_targets_monitored_systems',
        lambda *_a, **_k: {'targets_scanned': 2, 'created_or_updated': 2, 'repaired_monitored_system_ids': ['ms-1', 'ms-2']},
    )
    monkeypatch.setattr(pilot, 'list_workspace_monitored_system_rows', lambda *_a, **_k: rows)
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)

    result = pilot.reconcile_workspace_monitored_systems(request)

    assert result['monitored_systems_count'] == len(rows)
    assert len(result['systems']) == len(rows)
    assert result['reconcile']['created_or_updated'] == len(rows)
    assert result['diagnostics']['post_reconcile_monitored_systems_count'] == len(rows)
    assert result['diagnostics']['post_reconcile_monitored_system_ids'] == ['ms-1', 'ms-2']


def test_list_and_reconcile_resolve_the_same_workspace(monkeypatch):
    conn = _Conn()
    request = _Request('ws-7')
    resolved_workspace_headers: list[str | None] = []

    def fake_resolve_workspace(_connection, _user_id, requested_workspace_id=None):
        resolved_workspace_headers.append(requested_workspace_id)
        return {'workspace_id': 'ws-7', 'role': 'owner', 'workspace': {'id': 'ws-7', 'name': 'Workspace 7', 'slug': 'workspace-7'}}

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_a, **_k: {'id': 'user-7'})
    monkeypatch.setattr(pilot, 'resolve_workspace', fake_resolve_workspace)
    monkeypatch.setattr(pilot, 'reconcile_enabled_targets_monitored_systems', lambda *_a, **_k: {'targets_scanned': 0, 'created_or_updated': 0})
    monkeypatch.setattr(pilot, 'list_workspace_monitored_system_rows', lambda *_a, **_k: [])
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)

    listed = pilot.list_monitored_systems(request)
    repaired = pilot.reconcile_workspace_monitored_systems(request)

    assert listed['workspace']['id'] == repaired['workspace']['id'] == 'ws-7'
    assert resolved_workspace_headers == ['ws-7', 'ws-7']


def test_reconcile_workspace_returns_structured_error_when_audit_log_fails(monkeypatch, caplog: pytest.LogCaptureFixture):
    conn = _Conn()
    request = _Request('ws-1')

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda *_a, **_k: ({'id': 'user-1'}, {'workspace_id': 'ws-1', 'workspace': {'id': 'ws-1'}}))
    monkeypatch.setattr(pilot, 'reconcile_enabled_targets_monitored_systems', lambda *_a, **_k: {'targets_scanned': 1, 'created_or_updated': 1})
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError('audit insert failed')))

    with caplog.at_level(logging.ERROR):
        with pytest.raises(HTTPException) as exc:
            pilot.reconcile_workspace_monitored_systems(request)

    assert exc.value.status_code == 500
    assert exc.value.detail['code'] == 'monitoring_reconcile_failed'
    assert exc.value.detail['stage'] == 'audit_log'
    assert exc.value.detail['debug_error_type'] == 'RuntimeError'
    assert 'audit insert failed' in exc.value.detail['debug_error_message']
    assert 'monitoring_reconcile_failed stage=audit_log' in caplog.text


def test_reconcile_workspace_returns_structured_error_when_reconcile_targets_fails(monkeypatch):
    conn = _Conn()
    request = _Request('ws-1')

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda *_a, **_k: ({'id': 'user-1'}, {'workspace_id': 'ws-1', 'workspace': {'id': 'ws-1'}}))
    monkeypatch.setattr(pilot, 'reconcile_enabled_targets_monitored_systems', lambda *_a, **_k: (_ for _ in ()).throw(ValueError('upsert violated unique constraint')))

    with pytest.raises(HTTPException) as exc:
        pilot.reconcile_workspace_monitored_systems(request)

    assert exc.value.status_code == 500
    assert exc.value.detail['code'] == 'monitoring_reconcile_failed'
    assert exc.value.detail['stage'] == 'reconcile_targets'
    assert exc.value.detail['debug_error_type'] == 'ValueError'
    assert 'upsert violated unique constraint' in exc.value.detail['debug_error_message']
