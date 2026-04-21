from __future__ import annotations

import logging
from contextlib import contextmanager

import pytest
from fastapi import HTTPException

from services.api.app import pilot


class _Conn:
    def __init__(self):
        self.commits = 0
        self.eligible_targets = [{'id': 't-1', 'asset_id': 'a-1', 'target_type': 'wallet'}]
        self.broken_links: list[dict[str, str]] = []
        self.mismatched_links: list[dict[str, str]] = []
        self.valid_link_rows = [{'id': 'ms-1', 'target_id': 't-1', 'asset_id': 'a-1'}]

    def commit(self):
        self.commits += 1

    def execute(self, query, _params=None):
        normalized = ' '.join(str(query).split())
        if 'SELECT t.id, t.asset_id, t.target_type FROM targets t JOIN assets a' in normalized:
            return _Rows(self.eligible_targets)
        if 'SELECT t.id, t.asset_id FROM targets t LEFT JOIN assets a' in normalized and 'a.id IS NULL' in normalized:
            return _Rows(self.broken_links)
        if 'SELECT ms.id, ms.target_id, ms.asset_id AS monitored_asset_id, t.asset_id AS target_asset_id' in normalized:
            return _Rows(self.mismatched_links)
        if 'SELECT ms.id, ms.target_id, ms.asset_id FROM monitored_systems ms JOIN targets t' in normalized and 'ms.asset_id = t.asset_id' in normalized:
            return _Rows(self.valid_link_rows)
        return _Rows([])


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


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


def test_reconcile_workspace_sets_reconcile_targets_stage_when_target_ensure_raises(monkeypatch):
    conn = _Conn()
    request = _Request('ws-1')

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda *_a, **_k: ({'id': 'user-1'}, {'workspace_id': 'ws-1', 'workspace': {'id': 'ws-1'}}))
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)

    def _raise_from_target_ensure(_connection, *, workspace_id=None):
        raise RuntimeError(f'failed ensure for workspace {workspace_id}')

    monkeypatch.setattr(pilot, 'reconcile_enabled_targets_monitored_systems', _raise_from_target_ensure)

    with pytest.raises(HTTPException) as exc:
        pilot.reconcile_workspace_monitored_systems(request)

    assert exc.value.status_code == 500
    assert exc.value.detail['code'] == 'monitoring_reconcile_failed'
    assert exc.value.detail['stage'] == 'reconcile_targets'
    assert exc.value.detail['debug_error_type'] == 'RuntimeError'
    assert 'failed ensure for workspace ws-1' in exc.value.detail['debug_error_message']


def test_reconcile_workspace_returns_structured_error_when_list_rows_fails(monkeypatch):
    conn = _Conn()
    request = _Request('ws-1')

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda *_a, **_k: ({'id': 'user-1'}, {'workspace_id': 'ws-1', 'workspace': {'id': 'ws-1'}}))
    monkeypatch.setattr(pilot, 'reconcile_enabled_targets_monitored_systems', lambda *_a, **_k: {'targets_scanned': 1, 'created_or_updated': 1})
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)
    monkeypatch.setattr(pilot, 'list_workspace_monitored_system_rows', lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError('list failed')))

    with pytest.raises(HTTPException) as exc:
        pilot.reconcile_workspace_monitored_systems(request)

    assert exc.value.status_code == 500
    assert exc.value.detail['code'] == 'monitoring_reconcile_failed'
    assert exc.value.detail['stage'] == 'list_rows'
    assert exc.value.detail['debug_error_type'] == 'RuntimeError'
    assert 'list failed' in exc.value.detail['debug_error_message']


def test_get_monitored_systems_remains_queryable_after_reconcile(monkeypatch):
    conn = _Conn()
    request = _Request('ws-9')
    rows = [{'id': 'ms-9', 'workspace_id': 'ws-9', 'target_id': 't-9', 'asset_id': 'a-9'}]

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda *_a, **_k: ({'id': 'user-9'}, {'workspace_id': 'ws-9', 'workspace': {'id': 'ws-9'}}))
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_a, **_k: {'id': 'user-9'})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda *_a, **_k: {'workspace_id': 'ws-9', 'workspace': {'id': 'ws-9'}, 'role': 'owner'})
    monkeypatch.setattr(pilot, 'reconcile_enabled_targets_monitored_systems', lambda *_a, **_k: {'targets_scanned': 1, 'created_or_updated': 1})
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)
    monkeypatch.setattr(pilot, 'list_workspace_monitored_system_rows', lambda *_a, **_k: rows)

    reconcile_payload = pilot.reconcile_workspace_monitored_systems(request)
    listed_payload = pilot.list_monitored_systems(request)

    assert conn.commits == 1
    assert reconcile_payload['systems'][0]['id'] == 'ms-9'
    assert listed_payload['systems'][0]['id'] == 'ms-9'


def test_reconcile_workspace_requires_eligible_targets(monkeypatch):
    conn = _Conn()
    conn.eligible_targets = []
    request = _Request('ws-2')

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda *_a, **_k: ({'id': 'user-2'}, {'workspace_id': 'ws-2', 'workspace': {'id': 'ws-2'}}))

    with pytest.raises(HTTPException) as exc:
        pilot.reconcile_workspace_monitored_systems(request)

    assert exc.value.status_code == 500
    assert exc.value.detail['stage'] == 'verify_eligible_targets'


def test_reconcile_workspace_validates_runtime_debug_assertions(monkeypatch):
    conn = _Conn()
    conn.valid_link_rows = []
    request = _Request('ws-3')

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda *_a, **_k: ({'id': 'user-3'}, {'workspace_id': 'ws-3', 'workspace': {'id': 'ws-3'}}))
    monkeypatch.setattr(pilot, 'reconcile_enabled_targets_monitored_systems', lambda *_a, **_k: {'targets_scanned': 1, 'created_or_updated': 1})
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)
    monkeypatch.setattr(pilot, 'list_workspace_monitored_system_rows', lambda *_a, **_k: [])

    with pytest.raises(HTTPException) as exc:
        pilot.reconcile_workspace_monitored_systems(request)

    assert exc.value.status_code == 500
    assert exc.value.detail['stage'] == 'runtime_debug_assertions'


def test_reconcile_workspace_returns_success_state_and_reconcile_id(monkeypatch):
    conn = _Conn()
    request = _Request('ws-1')
    rows = [{'id': 'ms-1', 'workspace_id': 'ws-1', 'target_id': 't-1', 'asset_id': 'a-1'}]

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda *_a, **_k: ({'id': 'user-1'}, {'workspace_id': 'ws-1', 'workspace': {'id': 'ws-1'}}))
    monkeypatch.setattr(
        pilot,
        'reconcile_enabled_targets_monitored_systems',
        lambda *_a, **_k: {
            'targets_scanned': 1,
            'created_or_updated': 1,
            'invalid_reasons': {},
            'invalid_target_details': [],
            'skipped_reasons': {},
            'skipped_target_details': [],
            'repaired_monitored_system_ids': ['ms-1'],
        },
    )
    monkeypatch.setattr(pilot, 'list_workspace_monitored_system_rows', lambda *_a, **_k: rows)
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)

    result = pilot.reconcile_workspace_monitored_systems(request)

    assert result['state'] == 'success'
    assert isinstance(result['reconcile_id'], str)
    assert result['reconcile']['created_or_updated'] == 1


def test_reconcile_workspace_returns_no_op_with_reasons_when_unresolved(monkeypatch):
    conn = _Conn()
    request = _Request('ws-1')

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda *_a, **_k: ({'id': 'user-1'}, {'workspace_id': 'ws-1', 'workspace': {'id': 'ws-1'}}))
    monkeypatch.setattr(
        pilot,
        'reconcile_enabled_targets_monitored_systems',
        lambda *_a, **_k: {
            'targets_scanned': 1,
            'created_or_updated': 0,
            'invalid_reasons': {'missing_asset': 1},
            'invalid_target_details': [{'target_id': 't-1', 'code': 'missing_asset', 'reason': 'target missing asset'}],
            'skipped_reasons': {},
            'skipped_target_details': [],
            'repaired_monitored_system_ids': [],
        },
    )
    monkeypatch.setattr(pilot, 'list_workspace_monitored_system_rows', lambda *_a, **_k: [])
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)

    result = pilot.reconcile_workspace_monitored_systems(request)

    assert result['state'] == 'no_op_with_reasons'
    assert result['reconcile']['invalid_target_details'][0]['code'] == 'missing_asset'
    assert result['reconcile']['invalid_target_details'][0]['reason'] == 'target missing asset'


def test_reconcile_workspace_idempotency_guard_returns_no_op_while_inflight(monkeypatch):
    conn = _Conn()
    request = _Request('ws-1')
    key = 'ws-1:user-1'
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _fake_pg(conn))
    monkeypatch.setattr(pilot, '_require_workspace_admin', lambda *_a, **_k: ({'id': 'user-1'}, {'workspace_id': 'ws-1', 'workspace': {'id': 'ws-1'}}))

    pilot._workspace_reconcile_inflight[key] = {'reconcile_id': 'rid-existing'}
    try:
        result = pilot.reconcile_workspace_monitored_systems(request)
    finally:
        pilot._workspace_reconcile_inflight.pop(key, None)

    assert result['state'] == 'no_op_with_reasons'
    assert result['reconcile_id'] == 'rid-existing'
    assert result['reconcile']['skipped_target_details'][0]['code'] == 'reconcile_already_in_progress'
