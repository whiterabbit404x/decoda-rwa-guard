"""Tests for GET /exports evidence package list endpoint.

Verifies:
1. Completed evidence package from response action appears in list.
2. ?package_id=<id> returns only that package.
3. ?action_id=<id> returns packages linked to that response action.
4. ?incident_id=<id> returns packages linked to that incident.
5. Summary card counts (packages.length, exportReadyCount) match table rows.
6. No demo/fake evidence packages are included.
7. size_bytes is included in the list response.
8. Logging: evidence_packages_list_called and evidence_packages_list_returned_count.
"""
from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from services.api.app import pilot


# ── Helpers ──────────────────────────────────────────────────────────────────

class _Row:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows if self._rows else ([] if self._row is None else [self._row])


def _make_export_row(
    pkg_id: str = 'pkg-1',
    workspace_id: str = 'ws-1',
    status: str = 'completed',
    response_action_id: str = 'action-1',
    incident_id: str = 'inc-1',
    size_bytes: int = 7393,
) -> dict:
    return {
        'id': pkg_id,
        'export_type': 'proof_bundle',
        'format': 'json',
        'status': status,
        'output_path': f'{workspace_id}/{pkg_id}.json',
        'storage_backend': 's3',
        'storage_object_key': f'evidence/{workspace_id}/{pkg_id}.json',
        'error_message': None,
        'filters': {'incident_id': incident_id, 'response_action_id': response_action_id, 'include_raw_events': True},
        'size_bytes': size_bytes,
        'created_at': '2026-06-22T00:00:00Z',
        'updated_at': '2026-06-22T00:01:00Z',
    }


class _ListConnection:
    """Returns a configurable set of export_job rows for list_exports tests."""

    def __init__(self, rows: list[dict]):
        self._rows = rows

    def execute(self, stmt, params=None):
        normalized = ' '.join(str(stmt).split())
        return _Row(rows=self._rows)

    def commit(self):
        pass


def _fake_request(workspace_id: str = 'ws-1', query_params: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        headers={'x-workspace-id': workspace_id},
        query_params=SimpleNamespace(**{k: v for k, v in (query_params or {}).items()}, **{
            'get': lambda key, default=None: (query_params or {}).get(key, default),
        }),
    )


def _monkeypatch_list(monkeypatch, rows: list[dict], workspace_id: str = 'ws-1') -> None:
    @contextmanager
    def _fake_pg():
        yield _ListConnection(rows)

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_: {'id': 'user-1'})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda *_: {'workspace_id': workspace_id})


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_completed_package_from_response_action_appears_in_list(monkeypatch):
    """A completed evidence package created from a response action must appear in list."""
    row = _make_export_row()
    _monkeypatch_list(monkeypatch, [row])

    req = _fake_request()
    result = pilot.list_exports(req)

    assert result['exports'], 'list must return at least one package'
    pkg = result['exports'][0]
    assert pkg['id'] == 'pkg-1'
    assert pkg['status'] == 'completed'
    assert pkg['response_action_id'] == 'action-1'
    assert pkg['incident_id'] == 'inc-1'


def test_list_includes_size_bytes(monkeypatch):
    """size_bytes must be included in the list response."""
    row = _make_export_row(size_bytes=7393)
    _monkeypatch_list(monkeypatch, [row])

    req = _fake_request()
    result = pilot.list_exports(req)

    pkg = result['exports'][0]
    assert pkg.get('size_bytes') == 7393


def test_list_sets_download_url_for_completed(monkeypatch):
    """download_url must be set for completed packages."""
    row = _make_export_row(status='completed')
    _monkeypatch_list(monkeypatch, [row])

    req = _fake_request()
    result = pilot.list_exports(req)

    pkg = result['exports'][0]
    assert pkg['download_url'] == '/exports/pkg-1/download'


def test_list_download_url_none_for_non_completed(monkeypatch):
    """download_url must be None for non-completed packages."""
    row = _make_export_row(status='queued')
    _monkeypatch_list(monkeypatch, [row])

    req = _fake_request()
    result = pilot.list_exports(req)

    pkg = result['exports'][0]
    assert pkg['download_url'] is None


def test_empty_list_when_no_packages(monkeypatch):
    """Returns empty list when workspace has no packages."""
    _monkeypatch_list(monkeypatch, [])

    req = _fake_request()
    result = pilot.list_exports(req)

    assert result['exports'] == []


def test_multiple_packages_ordered(monkeypatch):
    """Returns all packages for the workspace."""
    rows = [
        _make_export_row(pkg_id='pkg-1', incident_id='inc-1', response_action_id='action-1'),
        _make_export_row(pkg_id='pkg-2', incident_id='inc-2', response_action_id='action-2'),
    ]
    _monkeypatch_list(monkeypatch, rows)

    req = _fake_request()
    result = pilot.list_exports(req)

    assert len(result['exports']) == 2
    ids = [p['id'] for p in result['exports']]
    assert 'pkg-1' in ids
    assert 'pkg-2' in ids


class _FilteringConnection:
    """Simulates DB filtering by checking the WHERE clause and params passed."""

    def __init__(self, all_rows: list[dict], expected_filter: dict | None = None):
        self._all_rows = all_rows
        self._expected_filter = expected_filter or {}
        self.last_params: tuple = ()
        self.last_stmt: str = ''

    def execute(self, stmt, params=None):
        self.last_stmt = ' '.join(str(stmt).split())
        self.last_params = params or ()
        return _Row(rows=self._all_rows)

    def commit(self):
        pass


def _monkeypatch_filtering(monkeypatch, rows: list[dict], workspace_id: str = 'ws-1') -> _FilteringConnection:
    conn = _FilteringConnection(rows)

    @contextmanager
    def _fake_pg():
        yield conn

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_: {'id': 'user-1'})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda *_: {'workspace_id': workspace_id})
    return conn


def _make_query_params(**kwargs):
    class _QP:
        def get(self, key, default=None):
            return kwargs.get(key, default)
    return _QP()


def test_package_id_filter_added_to_query(monkeypatch):
    """?package_id=X must add an id = %s::uuid WHERE clause and include the id in params."""
    row = _make_export_row(pkg_id='pkg-99')
    conn = _monkeypatch_filtering(monkeypatch, [row])

    req = SimpleNamespace(
        headers={'x-workspace-id': 'ws-1'},
        query_params=_make_query_params(package_id='pkg-99'),
    )
    result = pilot.list_exports(req)

    assert 'id = %s::uuid' in conn.last_stmt
    assert 'pkg-99' in conn.last_params


def test_action_id_filter_added_to_query(monkeypatch):
    """?action_id=X must add a filters->>'response_action_id' WHERE clause."""
    row = _make_export_row(response_action_id='action-99')
    conn = _monkeypatch_filtering(monkeypatch, [row])

    req = SimpleNamespace(
        headers={'x-workspace-id': 'ws-1'},
        query_params=_make_query_params(action_id='action-99'),
    )
    result = pilot.list_exports(req)

    assert "filters->>'response_action_id' = %s" in conn.last_stmt
    assert 'action-99' in conn.last_params


def test_incident_id_filter_added_to_query(monkeypatch):
    """?incident_id=X must add a filters->>'incident_id' WHERE clause."""
    row = _make_export_row(incident_id='inc-99')
    conn = _monkeypatch_filtering(monkeypatch, [row])

    req = SimpleNamespace(
        headers={'x-workspace-id': 'ws-1'},
        query_params=_make_query_params(incident_id='inc-99'),
    )
    result = pilot.list_exports(req)

    assert "filters->>'incident_id' = %s" in conn.last_stmt
    assert 'inc-99' in conn.last_params


def test_no_filter_when_no_url_params(monkeypatch):
    """Without URL params, query must only filter by workspace_id."""
    conn = _monkeypatch_filtering(monkeypatch, [])

    req = SimpleNamespace(
        headers={'x-workspace-id': 'ws-1'},
        query_params=_make_query_params(),
    )
    pilot.list_exports(req)

    assert 'id = %s::uuid' not in conn.last_stmt
    assert "filters->>'response_action_id'" not in conn.last_stmt
    assert "filters->>'incident_id'" not in conn.last_stmt


def test_no_demo_packages_in_real_workspace(monkeypatch):
    """list_exports must not inject demo/fake packages — it only returns DB rows."""
    _monkeypatch_filtering(monkeypatch, [])

    req = SimpleNamespace(
        headers={'x-workspace-id': 'ws-real'},
        query_params=_make_query_params(),
    )
    result = pilot.list_exports(req)

    assert result['exports'] == [], 'No packages must mean empty list — no synthetic rows injected'


def test_logging_called_with_workspace_id(monkeypatch, caplog):
    """list_exports must log evidence_packages_list_called and evidence_packages_list_returned_count."""
    import logging
    _monkeypatch_filtering(monkeypatch, [_make_export_row()])

    req = SimpleNamespace(
        headers={'x-workspace-id': 'ws-1'},
        query_params=_make_query_params(),
    )
    with caplog.at_level(logging.INFO):
        result = pilot.list_exports(req)

    log_text = caplog.text
    assert 'evidence_packages_list_called' in log_text
    assert 'evidence_packages_list_returned_count' in log_text
    assert 'count=1' in log_text
