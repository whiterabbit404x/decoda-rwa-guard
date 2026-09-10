"""Tests for GET /exports/{export_id}/download backend endpoint.

Verifies:
1. Package from another workspace is rejected (404).
2. Non-completed package returns 409.
3. Existing R2 object is streamed back with correct headers.
4. Missing R2 object returns JSON 404 with error=evidence_object_not_found.
5. No secrets are logged during download.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from services.api.app import pilot


# ── Helpers ───────────────────────────────────────────────────────────────────

class _FakeStorage:
    backend_name = 's3'

    def __init__(self, content: bytes = b'{"rows":[]}', *, missing: bool = False):
        self._content = content
        self._missing = missing
        self.read_calls: list[str] = []

    def write_bytes(self, *, object_key: str, content: bytes) -> str:
        return object_key

    def read_bytes(self, *, object_key: str) -> bytes:
        self.read_calls.append(object_key)
        if self._missing:
            raise FileNotFoundError(f'No object at key {object_key}')
        return self._content

    def get_object_size(self, *, object_key: str) -> int | None:
        return len(self._content) if not self._missing else None

    def delete_bytes(self, *, object_key: str) -> None:
        pass

    def object_lock_status(self) -> dict:
        return {}


def _make_export_row(
    export_id: str = 'pkg-1',
    workspace_id: str = 'ws-1',
    status: str = 'completed',
    fmt: str = 'json',
    storage_object_key: str | None = None,
) -> dict:
    return {
        'id': export_id,
        'workspace_id': workspace_id,
        'format': fmt,
        'status': status,
        'storage_object_key': storage_object_key or f'{workspace_id}/{export_id}.{fmt}',
    }


class _DownloadConnection:
    """Simulates DB for get_export_artifact_content.

    Returns the given row when queried with matching export_id + workspace_id,
    None otherwise (cross-workspace rejection).

    Download is gated server-side by the ``evidence.export`` permission, so this
    fake also answers the RBAC lookup (``workspace_role_permissions``) and the
    workspace auth policy, and records the audit write the download emits.
    """

    def __init__(
        self,
        row: dict | None,
        *,
        target_export_id: str = 'pkg-1',
        target_workspace_id: str = 'ws-1',
        permission_row: dict | None = None,
    ):
        self._row = row
        self._target_export_id = target_export_id
        self._target_workspace_id = target_workspace_id
        self._permission_row = permission_row
        self.audit_actions: list[str] = []

    def execute(self, stmt, params=None):
        params = params or ()
        normalized = ' '.join(str(stmt).split())
        if 'FROM workspace_role_permissions' in normalized:
            return _Row(self._permission_row)
        if 'FROM workspace_auth_policies' in normalized:
            return _Row(None)
        if 'INSERT INTO audit_logs' in normalized:
            self.audit_actions.append(str(params[3]) if len(params) > 3 else '')
            return _Row(None)
        if 'FROM audit_logs' in normalized:
            return _Row(None)
        export_id = params[0] if len(params) > 0 else None
        workspace_id = params[1] if len(params) > 1 else None
        match = (
            export_id == self._target_export_id
            and workspace_id == self._target_workspace_id
        )
        return _Row(self._row if match else None)

    def commit(self):
        pass


class _Row:
    def __init__(self, row: dict | None):
        self._row = row

    def fetchone(self):
        return self._row


def _fake_request(workspace_id: str = 'ws-1') -> SimpleNamespace:
    return SimpleNamespace(headers={'x-workspace-id': workspace_id})


def _monkeypatch_download(
    monkeypatch,
    db_row: dict | None,
    *,
    storage: _FakeStorage | None = None,
    requester_workspace_id: str = 'ws-1',
    target_workspace_id: str = 'ws-1',
    target_export_id: str = 'pkg-1',
    role: str = 'admin',
    permission_row: dict | None = None,
) -> _DownloadConnection:
    """Wire the download handler onto fakes and return the connection.

    ``role`` drives the server-side ``evidence.export`` check: admin/owner/analyst
    are granted by the role default, viewer is denied. ``permission_row`` supplies
    an explicit workspace_role_permissions override when a test needs one.
    """
    conn = _DownloadConnection(
        db_row,
        target_export_id=target_export_id,
        target_workspace_id=target_workspace_id,
        permission_row=permission_row,
    )

    @contextmanager
    def _fake_pg():
        yield conn

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_: {'id': 'user-1'})
    monkeypatch.setattr(
        pilot,
        'resolve_workspace',
        lambda *_: {'workspace_id': requester_workspace_id, 'role': role},
    )
    if storage is not None:
        monkeypatch.setattr(pilot, 'load_export_storage', lambda: storage)
    return conn


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_rejects_package_from_another_workspace(monkeypatch):
    """get_export_artifact_content must return 404 when package belongs to a different workspace."""
    row = _make_export_row(export_id='pkg-1', workspace_id='ws-other')
    # Requester is ws-1 but the DB row is scoped to ws-other — the SQL WHERE
    # filters by both id AND workspace_id so the connection returns None.
    _monkeypatch_download(
        monkeypatch,
        row,
        requester_workspace_id='ws-1',
        target_workspace_id='ws-other',
        target_export_id='pkg-1',
    )
    storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: storage)

    req = _fake_request(workspace_id='ws-1')
    with pytest.raises(HTTPException) as exc_info:
        pilot.get_export_artifact_content('pkg-1', req)

    assert exc_info.value.status_code == 404
    assert storage.read_calls == [], 'Storage must not be read for cross-workspace request'


def test_rejects_non_completed_package(monkeypatch):
    """get_export_artifact_content must return 409 when the package is not yet completed."""
    row = _make_export_row(status='queued')
    _monkeypatch_download(monkeypatch, row)
    storage = _FakeStorage()
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: storage)

    req = _fake_request()
    with pytest.raises(HTTPException) as exc_info:
        pilot.get_export_artifact_content('pkg-1', req)

    assert exc_info.value.status_code == 409
    assert storage.read_calls == [], 'Storage must not be read for non-completed package'


def test_streams_existing_r2_object_with_correct_content(monkeypatch):
    """get_export_artifact_content must return the stored bytes and correct filename."""
    artifact = b'{"rows": [{"key": "value"}]}'
    row = _make_export_row(storage_object_key='ws-1/pkg-1.json')
    storage = _FakeStorage(content=artifact)
    _monkeypatch_download(monkeypatch, row, storage=storage)

    req = _fake_request()
    content, filename = pilot.get_export_artifact_content('pkg-1', req)

    assert content == artifact
    assert filename == 'pkg-1.json'
    assert 'ws-1/pkg-1.json' in storage.read_calls


def test_missing_r2_object_returns_evidence_object_not_found(monkeypatch):
    """Missing R2 object must raise 404 with error=evidence_object_not_found."""
    row = _make_export_row()
    storage = _FakeStorage(missing=True)
    _monkeypatch_download(monkeypatch, row, storage=storage)

    req = _fake_request()
    with pytest.raises(HTTPException) as exc_info:
        pilot.get_export_artifact_content('pkg-1', req)

    exc = exc_info.value
    assert exc.status_code == 404
    detail = exc.detail
    assert isinstance(detail, dict), 'detail must be a dict with error code'
    assert detail.get('error') == 'evidence_object_not_found'


def test_missing_object_not_returned_as_frontend_404(monkeypatch):
    """A missing R2 object must not cause a plain string detail — frontend must get a JSON body."""
    row = _make_export_row()
    storage = _FakeStorage(missing=True)
    _monkeypatch_download(monkeypatch, row, storage=storage)

    req = _fake_request()
    with pytest.raises(HTTPException) as exc_info:
        pilot.get_export_artifact_content('pkg-1', req)

    exc = exc_info.value
    assert exc.status_code == 404
    assert not isinstance(exc.detail, str), (
        'detail must not be a plain string; it must be a dict so the proxy can return structured JSON'
    )


def test_no_secrets_logged_during_download(monkeypatch, caplog):
    """Download must not log storage credentials, object keys with signing data, or auth tokens."""
    artifact = b'{"rows": []}'
    row = _make_export_row(storage_object_key='ws-1/pkg-1.json')
    storage = _FakeStorage(content=artifact)
    _monkeypatch_download(monkeypatch, row, storage=storage)

    req = _fake_request()
    with caplog.at_level(logging.DEBUG):
        pilot.get_export_artifact_content('pkg-1', req)

    log_text = caplog.text.lower()
    assert 'secret' not in log_text
    assert 'credential' not in log_text
    assert 'access_key' not in log_text


def test_download_uses_storage_object_key_from_db(monkeypatch):
    """get_export_artifact_content must use storage_object_key from DB, not reconstruct it."""
    artifact = b'{"bundle": true}'
    custom_key = 'custom-prefix/pkg-1.json'
    row = _make_export_row(storage_object_key=custom_key)
    storage = _FakeStorage(content=artifact)
    _monkeypatch_download(monkeypatch, row, storage=storage)

    req = _fake_request()
    pilot.get_export_artifact_content('pkg-1', req)

    assert custom_key in storage.read_calls, 'Must use storage_object_key from DB row'


def test_download_falls_back_to_default_key_when_storage_object_key_null(monkeypatch):
    """Falls back to {workspace_id}/{export_id}.{format} when storage_object_key is NULL."""
    artifact = b'{"rows": []}'
    row = _make_export_row()
    row['storage_object_key'] = None
    storage = _FakeStorage(content=artifact)
    _monkeypatch_download(monkeypatch, row, storage=storage)

    req = _fake_request()
    pilot.get_export_artifact_content('pkg-1', req)

    assert 'ws-1/pkg-1.json' in storage.read_calls, 'Must fall back to {workspace_id}/{export_id}.{format}'


# ── Server-side export authorization (Screen 9) ───────────────────────────────
# Download authorization is enforced by the API, never only by hiding a button.
# A viewer whose UI would not show the action must still be refused when the
# handler is called directly (a stale token, a scripted client, a curl).

def test_viewer_without_export_permission_cannot_download_package(monkeypatch):
    """A role without evidence.export is refused 403 and never reaches storage."""
    row = _make_export_row()
    storage = _FakeStorage(content=b'{"rows": []}')
    _monkeypatch_download(monkeypatch, row, storage=storage, role='viewer')

    with pytest.raises(HTTPException) as exc_info:
        pilot.get_export_artifact_content('pkg-1', _fake_request())

    exc = exc_info.value
    assert exc.status_code == 403
    assert isinstance(exc.detail, dict)
    assert exc.detail.get('code') == 'PERMISSION_DENIED'
    assert exc.detail.get('permission') == 'evidence.export'
    assert storage.read_calls == [], 'Storage must never be read without export permission'


def test_explicit_permission_revocation_blocks_download_for_an_admin(monkeypatch):
    """An explicit workspace_role_permissions denial overrides the role default."""
    row = _make_export_row()
    storage = _FakeStorage(content=b'{"rows": []}')
    _monkeypatch_download(
        monkeypatch, row, storage=storage, role='admin', permission_row={'granted': False},
    )

    with pytest.raises(HTTPException) as exc_info:
        pilot.get_export_artifact_content('pkg-1', _fake_request())

    assert exc_info.value.status_code == 403
    assert storage.read_calls == []


def test_viewer_without_export_permission_cannot_download_manifest(monkeypatch):
    """The manifest names every artifact and its digest — it is gated identically."""
    row = _make_export_row()
    storage = _FakeStorage(content=b'{"rows": []}')
    _monkeypatch_download(monkeypatch, row, storage=storage, role='viewer')

    with pytest.raises(HTTPException) as exc_info:
        pilot.get_evidence_package_manifest('pkg-1', _fake_request())

    assert exc_info.value.status_code == 403
    assert storage.read_calls == []


def test_successful_download_records_an_audit_event(monkeypatch):
    """Evidence leaving the platform is always attributable."""
    row = _make_export_row(storage_object_key='ws-1/pkg-1.json')
    storage = _FakeStorage(content=b'{"rows": []}')
    conn = _monkeypatch_download(monkeypatch, row, storage=storage)

    pilot.get_export_artifact_content('pkg-1', _fake_request())

    assert 'evidence_package_downloaded' in conn.audit_actions


def test_refused_download_records_no_audit_event(monkeypatch):
    """A denied download must not write a download event — nothing was downloaded."""
    row = _make_export_row()
    storage = _FakeStorage(content=b'{"rows": []}')
    conn = _monkeypatch_download(monkeypatch, row, storage=storage, role='viewer')

    with pytest.raises(HTTPException):
        pilot.get_export_artifact_content('pkg-1', _fake_request())

    assert conn.audit_actions == []
