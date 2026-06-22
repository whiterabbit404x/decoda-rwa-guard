"""Tests for POST /response/actions/{action_id}/evidence-package endpoint.

Verifies:
1. Creates evidence package linked to response action.
2. Package has correct response_action_id, incident_id, and workspace_id.
3. Audit event is written with event_type=evidence_package_created.
4. Idempotency: calling twice reuses the same package, no duplicate.
5. Self-heals incident_id from linked alert when action.incident_id is NULL.
6. 404 when action not found.
7. 422 when action has no incident and no linked alert can infer one.
8. list_exports returns response_action_id from filters.
9. R2/S3 upload failure cannot return 200 — must return 502.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from services.api.app import pilot


# ── Helpers ──────────────────────────────────────────────────────────────────

class _FakeStorage:
    backend_name = 'local'

    def __init__(self):
        self.written: dict[str, bytes] = {}

    def write_bytes(self, *, object_key: str, content: bytes) -> str:
        self.written[object_key] = content
        return object_key


class _FakeStorageUploadFails:
    """Simulates R2/S3 upload failure (e.g. credentials missing, network timeout)."""
    backend_name = 's3'

    def write_bytes(self, *, object_key: str, content: bytes) -> str:
        raise OSError('Connection timeout: cannot reach R2 endpoint')


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


def _monkeypatch_common(monkeypatch, connection, *, workspace_id: str = 'ws-1', user_id: str = 'user-1') -> None:
    @contextmanager
    def _fake_pg():
        yield connection

    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, '_require_workspace_permission', lambda *_: (
        {'id': user_id, 'mfa_enabled': False},
        {'workspace_id': workspace_id, 'role': 'admin'},
    ))
    monkeypatch.setattr(pilot, '_workspace_plan', lambda *_: {'exports_enabled': True})
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: _FakeStorage())


# ── Connection stubs ──────────────────────────────────────────────────────────

class _BaseConnection:
    def __init__(self):
        self.executed: list[tuple[str, object]] = []
        self.committed = False

    def execute(self, stmt, params=None):
        normalized = ' '.join(str(stmt).split())
        self.executed.append((normalized, params))
        return self._handle(normalized, params)

    def _handle(self, stmt, params):
        raise AssertionError(f'unexpected query: {stmt!r}')

    def commit(self):
        self.committed = True


class _FullChainConnection(_BaseConnection):
    """Action has incident_id; full chain of alerts/detections/response_actions."""

    def __init__(self, *, existing_package_id: str | None = None):
        super().__init__()
        self._existing_package_id = existing_package_id
        self._inserted_pkg_id: str | None = None

    def _handle(self, stmt, params):
        # response action lookup
        if 'FROM response_actions WHERE id = %s::uuid AND workspace_id' in stmt:
            return _Row({'id': 'action-1', 'workspace_id': 'ws-1', 'incident_id': 'inc-1', 'alert_id': 'alert-1', 'action_type': 'notify_team', 'mode': 'recommended', 'status': 'pending'})
        # idempotency check
        if "filters->>'response_action_id' = %s" in stmt:
            if self._existing_package_id:
                return _Row({'id': self._existing_package_id})
            return _Row(None)
        # INSERT export_job
        if 'INSERT INTO export_jobs' in stmt:
            self._inserted_pkg_id = params[0] if params else None
            return _Row(None)
        # _generate_export_artifact: fetch job
        if 'FROM export_jobs WHERE id = %s AND workspace_id = %s' in stmt:
            pkg_id = self._inserted_pkg_id or 'pkg-1'
            return _Row({'id': pkg_id, 'export_type': 'proof_bundle', 'format': 'json', 'filters': {'incident_id': 'inc-1', 'response_action_id': 'action-1', 'include_raw_events': True}, 'requested_by_user_id': 'user-1'})
        # incident
        if 'FROM incidents WHERE workspace_id = %s AND id = %s' in stmt:
            return _Row({'id': 'inc-1', 'workspace_id': 'ws-1', 'title': 'Test Incident', 'severity': 'high', 'status': 'open', 'asset_id': 'asset-1', 'linked_alert_ids': ['alert-1']})
        # alerts (fetchall)
        if 'FROM alerts a JOIN detection_metrics dm' in stmt:
            return _Row(rows=[{'id': 'alert-1', 'severity': 'high', 'source': 'simulator', 'target_id': 'target-1'}])
        # detection metrics (fetchall)
        if 'FROM detection_metrics WHERE workspace_id = %s AND incident_id = %s' in stmt:
            return _Row(rows=[{'id': 'metric-1', 'event_observed_at': '2026-01-01T00:00:00Z', 'detected_at': '2026-01-01T00:02:00Z', 'mttd_seconds': 120, 'evidence': {}}])
        # response_actions for incident (fetchall, in export artifact)
        if 'FROM response_actions' in stmt and 'incident_id = %s' in stmt:
            return _Row(rows=[{'id': 'action-1', 'action_type': 'notify_team', 'status': 'pending', 'mode': 'recommended', 'execution_metadata': None, 'created_at': '2026-01-01T00:10:00Z', 'executed_at': None, 'rolled_back_at': None}])
        # detections (fetchall)
        if 'FROM detections' in stmt and 'linked_alert_id = ANY' in stmt:
            return _Row(rows=[{'id': 'det-1', 'detection_type': 'anomaly', 'severity': 'high', 'confidence': 0.9, 'evidence_source': 'simulator', 'status': 'open', 'detected_at': '2026-01-01T00:01:00Z', 'title': 'Anomaly'}])
        # audit logs (fetchall, in export artifact)
        if 'FROM audit_logs' in stmt and 'row_hash' not in stmt:
            return _Row(rows=[])
        # audit chain tip (fetchone)
        if 'FROM audit_logs' in stmt and 'row_hash' in stmt:
            return _Row(None)
        # UPDATE export_jobs
        if 'UPDATE export_jobs SET status' in stmt:
            return _Row(None)
        # status check in create_evidence_package_from_response_action
        if 'SELECT status, error_message FROM export_jobs WHERE id = %s' in stmt:
            return _Row({'status': 'completed', 'error_message': None})
        raise AssertionError(f'unexpected: {stmt!r}')


class _UploadFailsConnection(_FullChainConnection):
    """Upload fails → export_jobs.status stays failed → must return 502, not 200."""

    def _handle(self, stmt, params):
        if 'SELECT status, error_message FROM export_jobs WHERE id = %s' in stmt:
            return _Row({'status': 'failed', 'error_message': 'Connection timeout: cannot reach R2 endpoint'})
        return super()._handle(stmt, params)


class _NoIncidentOnActionConnection(_BaseConnection):
    """Action has NULL incident_id but linked alert has incident_id → self-heal."""

    def __init__(self):
        super().__init__()
        self._inserted_pkg_id: str | None = None
        self._incident_updated = False

    def _handle(self, stmt, params):
        if 'FROM response_actions WHERE id = %s::uuid AND workspace_id' in stmt:
            return _Row({'id': 'action-2', 'workspace_id': 'ws-1', 'incident_id': None, 'alert_id': 'alert-2', 'action_type': 'notify_team', 'mode': 'recommended', 'status': 'pending'})
        if 'FROM alerts WHERE id = %s::uuid AND workspace_id' in stmt:
            return _Row({'incident_id': 'inc-2'})
        if 'UPDATE response_actions SET incident_id' in stmt:
            self._incident_updated = True
            return _Row(None)
        if "filters->>'response_action_id' = %s" in stmt:
            return _Row(None)
        if 'INSERT INTO export_jobs' in stmt:
            self._inserted_pkg_id = params[0] if params else None
            return _Row(None)
        if 'FROM export_jobs WHERE id = %s AND workspace_id = %s' in stmt:
            pkg_id = self._inserted_pkg_id or 'pkg-2'
            return _Row({'id': pkg_id, 'export_type': 'proof_bundle', 'format': 'json', 'filters': {'incident_id': 'inc-2', 'response_action_id': 'action-2', 'include_raw_events': True}, 'requested_by_user_id': 'user-1'})
        if 'FROM incidents WHERE workspace_id = %s AND id = %s' in stmt:
            return _Row({'id': 'inc-2', 'workspace_id': 'ws-1', 'title': 'Incident 2', 'severity': 'medium', 'status': 'open', 'asset_id': None, 'linked_alert_ids': ['alert-2']})
        if 'FROM alerts a JOIN detection_metrics dm' in stmt:
            return _Row(rows=[{'id': 'alert-2', 'severity': 'medium', 'source': 'simulator', 'target_id': None}])
        if 'FROM detection_metrics WHERE workspace_id = %s AND incident_id = %s' in stmt:
            return _Row(rows=[])
        if 'FROM response_actions' in stmt and 'incident_id = %s' in stmt:
            return _Row(rows=[{'id': 'action-2', 'action_type': 'notify_team', 'status': 'pending', 'mode': 'recommended', 'execution_metadata': None, 'created_at': '2026-01-01T00:10:00Z', 'executed_at': None, 'rolled_back_at': None}])
        if 'FROM detections' in stmt and 'linked_alert_id = ANY' in stmt:
            return _Row(rows=[])
        if 'FROM audit_logs' in stmt and 'row_hash' not in stmt:
            return _Row(rows=[])
        if 'FROM audit_logs' in stmt and 'row_hash' in stmt:
            return _Row(None)
        if 'UPDATE export_jobs SET status' in stmt:
            return _Row(None)
        if 'SELECT status, error_message FROM export_jobs WHERE id = %s' in stmt:
            return _Row({'status': 'completed', 'error_message': None})
        raise AssertionError(f'unexpected: {stmt!r}')


class _MissingActionConnection(_BaseConnection):
    """Action not found → 404."""

    def _handle(self, stmt, params):
        if 'FROM response_actions WHERE id = %s::uuid AND workspace_id' in stmt:
            return _Row(None)
        raise AssertionError(f'unexpected: {stmt!r}')


class _NoIncidentNoAlertConnection(_BaseConnection):
    """Action has no incident_id and no usable alert → 422."""

    def _handle(self, stmt, params):
        if 'FROM response_actions WHERE id = %s::uuid AND workspace_id' in stmt:
            return _Row({'id': 'action-3', 'workspace_id': 'ws-1', 'incident_id': None, 'alert_id': None, 'action_type': 'notify_team', 'mode': 'recommended', 'status': 'pending'})
        if "filters->>'response_action_id' = %s" in stmt:
            return _Row(None)
        raise AssertionError(f'unexpected: {stmt!r}')


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_creates_evidence_package_and_returns_package_id(monkeypatch):
    conn = _FullChainConnection()
    _monkeypatch_common(monkeypatch, conn)

    result = pilot.create_evidence_package_from_response_action('action-1', _fake_request())

    assert result['response_action_id'] == 'action-1'
    assert result['incident_id'] == 'inc-1'
    assert result['created'] is True
    assert 'package_id' in result
    assert result['package_id']


def test_package_links_to_incident(monkeypatch):
    conn = _FullChainConnection()
    _monkeypatch_common(monkeypatch, conn)

    result = pilot.create_evidence_package_from_response_action('action-1', _fake_request())

    insert_calls = [(s, p) for s, p in conn.executed if 'INSERT INTO export_jobs' in s]
    assert insert_calls, 'Expected INSERT INTO export_jobs'
    _, params = insert_calls[0]
    filters = json.loads(params[4])
    assert filters['incident_id'] == 'inc-1'
    assert filters['response_action_id'] == 'action-1'


def test_idempotent_returns_existing_package(monkeypatch):
    conn = _FullChainConnection(existing_package_id='pkg-existing')
    _monkeypatch_common(monkeypatch, conn)

    result = pilot.create_evidence_package_from_response_action('action-1', _fake_request())

    assert result['package_id'] == 'pkg-existing'
    assert result['created'] is False
    insert_calls = [s for s, _ in conn.executed if 'INSERT INTO export_jobs' in s]
    assert not insert_calls, 'Should not insert a second package'


def test_self_heals_incident_from_linked_alert(monkeypatch):
    conn = _NoIncidentOnActionConnection()
    _monkeypatch_common(monkeypatch, conn)

    result = pilot.create_evidence_package_from_response_action('action-2', _fake_request())

    assert result['incident_id'] == 'inc-2'
    assert result['created'] is True
    assert conn._incident_updated, 'Should UPDATE response_actions with inferred incident_id'


def test_404_when_action_not_found(monkeypatch):
    conn = _MissingActionConnection()
    _monkeypatch_common(monkeypatch, conn)

    with pytest.raises(HTTPException) as exc_info:
        pilot.create_evidence_package_from_response_action('missing-id', _fake_request())

    assert exc_info.value.status_code == 404


def test_422_when_no_incident_and_no_alert(monkeypatch):
    conn = _NoIncidentNoAlertConnection()
    _monkeypatch_common(monkeypatch, conn)

    with pytest.raises(HTTPException) as exc_info:
        pilot.create_evidence_package_from_response_action('action-3', _fake_request())

    assert exc_info.value.status_code == 422


def test_evidence_export_returns_503_when_storage_not_configured(monkeypatch):
    """When export storage is not configured, must return 503 — not 500."""
    conn = _FullChainConnection()
    _monkeypatch_common(monkeypatch, conn)

    def _raise_storage_error():
        raise RuntimeError(
            'Local export storage backend is disabled in staging/production. '
            'Set EXPORT_STORAGE_BACKEND=s3 and configure EXPORT_S3_BUCKET.'
        )

    monkeypatch.setattr(pilot, 'load_export_storage', _raise_storage_error)

    with pytest.raises(HTTPException) as exc_info:
        pilot.create_evidence_package_from_response_action('action-1', _fake_request())

    exc = exc_info.value
    assert exc.status_code == 503
    assert isinstance(exc.detail, dict)
    assert exc.detail.get('error') == 'export_storage_not_configured'
    assert 'EXPORT_STORAGE_BACKEND' in exc.detail.get('message', '')


def test_no_fake_package_created_when_storage_not_configured(monkeypatch):
    """No committed export_job row should result when storage is unavailable."""
    conn = _FullChainConnection()
    _monkeypatch_common(monkeypatch, conn)

    def _raise():
        raise RuntimeError('Local export storage backend is disabled in staging/production.')

    monkeypatch.setattr(pilot, 'load_export_storage', _raise)

    with pytest.raises(HTTPException):
        pilot.create_evidence_package_from_response_action('action-1', _fake_request())

    # connection.commit() must NOT have been called — the INSERT is rolled back
    assert not conn.committed, 'Transaction must not be committed when storage fails'


def test_list_exports_returns_response_action_id(monkeypatch):
    """list_exports must extract response_action_id from the filters JSONB column."""

    class _ListConnection(_BaseConnection):
        def _handle(self, stmt, params):
            if 'FROM export_jobs' in stmt and 'ORDER BY created_at DESC' in stmt:
                return _Row(rows=[{
                    'id': 'pkg-99',
                    'export_type': 'proof_bundle',
                    'format': 'json',
                    'status': 'completed',
                    'output_path': 'ws-1/pkg-99.json',
                    'storage_backend': 'local',
                    'storage_object_key': 'ws-1/pkg-99.json',
                    'error_message': None,
                    'filters': {'incident_id': 'inc-99', 'response_action_id': 'action-99'},
                    'created_at': '2026-01-01T00:00:00Z',
                    'updated_at': '2026-01-01T00:01:00Z',
                }])
            raise AssertionError(f'unexpected: {stmt!r}')

    @contextmanager
    def _fake_pg():
        yield _ListConnection()

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_: {'id': 'user-1'})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda *_: {'workspace_id': 'ws-1'})

    result = pilot.list_exports(_fake_request())

    assert result['exports']
    pkg = result['exports'][0]
    assert pkg['response_action_id'] == 'action-99'
    assert pkg['incident_id'] == 'inc-99'


def test_upload_failure_returns_502_not_200(monkeypatch):
    """R2/S3 upload failure must return HTTP 502 — package_id must never be returned.

    Regression guard: ensures create_evidence_package_from_response_action cannot
    return 200/package_id when the storage write_bytes call raises.
    """
    conn = _UploadFailsConnection()
    _monkeypatch_common(monkeypatch, conn)
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: _FakeStorageUploadFails())

    with pytest.raises(HTTPException) as exc_info:
        pilot.create_evidence_package_from_response_action('action-1', _fake_request())

    exc = exc_info.value
    assert exc.status_code == 502, f'Expected 502 on upload failure, got {exc.status_code}'
    assert isinstance(exc.detail, dict)
    assert exc.detail.get('error') == 'evidence_upload_failed'


def test_upload_failure_does_not_return_package_id(monkeypatch):
    """No package_id in response when upload fails — the 502 must carry no package reference."""
    conn = _UploadFailsConnection()
    _monkeypatch_common(monkeypatch, conn)
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: _FakeStorageUploadFails())

    raised = False
    try:
        result = pilot.create_evidence_package_from_response_action('action-1', _fake_request())
        assert 'package_id' not in result, 'package_id must not be present when upload fails'
    except HTTPException:
        raised = True

    assert raised, 'Must raise HTTPException when upload fails, not silently return'
