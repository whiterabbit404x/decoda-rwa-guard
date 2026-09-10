"""Screen 9 — GET /exports/{id}/archive: the sealed evidence ZIP endpoint.

Drives the real handler against a fake connection and fake storage (the pattern
used across this suite), so the whole path is exercised: read the stored bundle,
resolve the manifest, RE-VERIFY it against those exact bytes, assemble the
archive, audit the download.

  H. A caller without ``evidence.export`` cannot download the package.
  I. A package from another workspace is never returned.
  J. The archive contains the expected files.
  M. A successful download writes an append-only audit event.
  +  A tampered package still exports, but its verification.json says FAILED —
     the archive never launders a broken package into a verified one.
"""
from __future__ import annotations

import io
import json
import zipfile
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from services.api.app import pilot
from services.api.app import evidence_verification as verification
from services.api.app.evidence_manifest_signer import resolve_manifest_signer
from services.api.app.evidence_signing import build_evidence_manifest

WS = 'ws-archive-endpoint'
OTHER_WS = 'ws-someone-else'
PKG = 'pkg-archive-endpoint'
PKG_NUMBER = 'EV-2026-021'
INCIDENT = 'inc-archive-endpoint'


class _Result:
    def __init__(self, rows=None):
        self._rows = rows or []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


def _bundle_bytes(*, tamper: bool = False) -> bytes:
    values = {
        'summary.json': {'export_id': PKG, 'incident_id': INCIDENT},
        'alerts.json': [{'id': 'alert-1', 'severity': 'critical'}],
        'evidence.json': [{'tx_hash': '0x7a1d'}],
        'policy_evaluations.json': [{'id': 'eval-1', 'decision': 'DENY'}],
    }
    manifest, _ = build_evidence_manifest(
        export_id=PKG, export_type='proof_bundle', workspace_id=WS,
        generated_at='2026-02-01T10:42:20Z', generated_by_user_id='user-1',
        source_resource_type='incident', source_resource_id=INCIDENT,
        storage_backend='local', file_values=values, seal_merkle=True,
        policy_snapshot={
            'present': True, 'policy_key': 'POL-MINT-007', 'policy_version': 7,
            'decision': 'DENY', 'decision_kind': 'enforcement',
            'source': 'governance_policy_versions',
        },
        required_artifacts=sorted(values),
        file_provenance={p: {'domain': 'OPERATIONAL', 'source_record_type': 'evidence'} for p in values},
    )
    seal = resolve_manifest_signer().sign(manifest)
    bundle = {**values, 'manifest.json': manifest, 'seal.json': seal}
    if tamper:
        bundle['alerts.json'] = [{'id': 'alert-1', 'severity': 'low', 'injected': True}]
    return json.dumps({'rows': [bundle]}).encode('utf-8')


class _FakeStorage:
    backend_name = 'local'

    def __init__(self, content: bytes | None = None, *, missing: bool = False):
        self._content = content if content is not None else _bundle_bytes()
        self._missing = missing
        self.read_calls: list[str] = []

    def write_bytes(self, *, object_key: str, content: bytes) -> str:
        return object_key

    def read_bytes(self, *, object_key: str) -> bytes:
        self.read_calls.append(object_key)
        if self._missing:
            raise FileNotFoundError(object_key)
        return self._content

    def get_object_size(self, *, object_key: str) -> int | None:
        return len(self._content)

    def delete_bytes(self, *, object_key: str) -> None:
        pass

    def object_lock_status(self) -> dict:
        return {}


class _ArchiveConnection:
    def __init__(self, *, row: dict | None, permission_row: dict | None = None):
        self._row = row
        self._permission_row = permission_row
        self.audit_actions: list[str] = []
        self.audit_metadata: list[dict] = []

    def execute(self, stmt, params=None):
        params = params or ()
        normalized = ' '.join(str(stmt).split())
        if 'FROM workspace_role_permissions' in normalized:
            return _Result([self._permission_row] if self._permission_row else [])
        if 'FROM workspace_auth_policies' in normalized:
            return _Result([])
        if 'INSERT INTO audit_logs' in normalized:
            self.audit_actions.append(str(params[3]))
            try:
                self.audit_metadata.append(json.loads(params[7]))
            except Exception:
                self.audit_metadata.append({})
            return _Result([])
        if 'FROM audit_logs' in normalized:
            return _Result([])
        if 'FROM export_jobs' in normalized:
            # The handler scopes by (id, workspace_id); the fake honours both.
            if self._row and params[:2] == (PKG, WS):
                return _Result([self._row])
            return _Result([])
        raise AssertionError(f'unexpected query: {normalized!r}')

    def commit(self):
        pass


def _export_row(*, status: str = 'completed') -> dict:
    return {
        'id': PKG,
        'workspace_id': WS,
        'export_type': 'proof_bundle',
        'format': 'json',
        'status': status,
        'storage_object_key': f'{WS}/{PKG}.json',
        'package_number': PKG_NUMBER,
        'filters': {'incident_id': INCIDENT},
    }


def _request(workspace_id: str = WS) -> SimpleNamespace:
    return SimpleNamespace(headers={'x-workspace-id': workspace_id})


def _wire(monkeypatch, connection, storage, *, role: str = 'admin', workspace_id: str = WS):
    @contextmanager
    def _pg():
        yield connection

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _pg)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_: {'id': 'user-1'})
    monkeypatch.setattr(
        pilot, 'resolve_workspace', lambda *_: {'workspace_id': workspace_id, 'role': role},
    )
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: storage)


# ── J. The archive is real and structured ────────────────────────────────────

def test_archive_endpoint_returns_a_structured_zip(monkeypatch):
    connection = _ArchiveConnection(row=_export_row())
    storage = _FakeStorage()
    _wire(monkeypatch, connection, storage)

    content, filename = pilot.download_evidence_package_archive(PKG, _request())

    assert filename == f'{PKG_NUMBER}.zip'
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        names = set(zf.namelist())
        assert f'{PKG_NUMBER}/manifest.json' in names
        assert f'{PKG_NUMBER}/manifest.sig' in names
        assert f'{PKG_NUMBER}/verification.json' in names
        assert f'{PKG_NUMBER}/reports/investigation.md' in names
        assert f'{PKG_NUMBER}/artifacts/operational/alerts.json' in names
        assert f'{PKG_NUMBER}/artifacts/on-chain/evidence.json' in names
        assert f'{PKG_NUMBER}/artifacts/policy/policy_evaluations.json' in names
        result = json.loads(zf.read(f'{PKG_NUMBER}/verification.json'))
    assert result['status'] == verification.STATUS_VERIFIED


def test_archive_verification_is_recomputed_for_this_request(monkeypatch):
    """A tampered package still exports — but never as a verified one."""
    connection = _ArchiveConnection(row=_export_row())
    storage = _FakeStorage(_bundle_bytes(tamper=True))
    _wire(monkeypatch, connection, storage)

    content, _ = pilot.download_evidence_package_archive(PKG, _request())

    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        result = json.loads(zf.read(f'{PKG_NUMBER}/verification.json'))
    assert result['status'] == verification.STATUS_VERIFICATION_FAILED
    assert result['verified'] is False
    assert 'alerts.json' in result['artifact_hashes']['failed_artifact_ids']


# ── H. Export permission is enforced server-side ─────────────────────────────

def test_viewer_cannot_download_the_evidence_archive(monkeypatch):
    connection = _ArchiveConnection(row=_export_row())
    storage = _FakeStorage()
    _wire(monkeypatch, connection, storage, role='viewer')

    with pytest.raises(HTTPException) as exc_info:
        pilot.download_evidence_package_archive(PKG, _request())

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail['code'] == 'PERMISSION_DENIED'
    assert storage.read_calls == [], 'Storage must never be read without export permission'
    assert connection.audit_actions == []


def test_explicit_permission_denial_overrides_the_role_default(monkeypatch):
    connection = _ArchiveConnection(row=_export_row(), permission_row={'granted': False})
    storage = _FakeStorage()
    _wire(monkeypatch, connection, storage, role='owner')

    with pytest.raises(HTTPException) as exc_info:
        pilot.download_evidence_package_archive(PKG, _request())

    assert exc_info.value.status_code == 403


# ── I. Cross-workspace access is denied ──────────────────────────────────────

def test_cross_workspace_package_id_is_never_returned(monkeypatch):
    """A user from Workspace B must not retrieve Workspace A evidence by id."""
    connection = _ArchiveConnection(row=_export_row())
    storage = _FakeStorage()
    _wire(monkeypatch, connection, storage, workspace_id=OTHER_WS)

    with pytest.raises(HTTPException) as exc_info:
        pilot.download_evidence_package_archive(PKG, _request(OTHER_WS))

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail['error'] == 'PACKAGE_NOT_FOUND'
    assert storage.read_calls == []


def test_cross_workspace_denial_does_not_leak_package_existence(monkeypatch):
    connection = _ArchiveConnection(row=_export_row())
    storage = _FakeStorage()
    _wire(monkeypatch, connection, storage, workspace_id=OTHER_WS)

    with pytest.raises(HTTPException) as exc_info:
        pilot.download_evidence_package_archive(PKG, _request(OTHER_WS))

    detail = exc_info.value.detail
    assert PKG_NUMBER not in json.dumps(detail)
    assert INCIDENT not in json.dumps(detail)


# ── Lifecycle gates ──────────────────────────────────────────────────────────

def test_incomplete_package_cannot_be_exported(monkeypatch):
    connection = _ArchiveConnection(row=_export_row(status='queued'))
    storage = _FakeStorage()
    _wire(monkeypatch, connection, storage)

    with pytest.raises(HTTPException) as exc_info:
        pilot.download_evidence_package_archive(PKG, _request())

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail['error'] == 'PACKAGE_NOT_READY'


def test_missing_storage_object_reports_storage_unavailable(monkeypatch):
    connection = _ArchiveConnection(row=_export_row())
    storage = _FakeStorage(missing=True)
    _wire(monkeypatch, connection, storage)

    with pytest.raises(HTTPException) as exc_info:
        pilot.download_evidence_package_archive(PKG, _request())

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail['error'] == 'PACKAGE_STORAGE_UNAVAILABLE'


def test_package_without_a_retrievable_manifest_cannot_be_exported(monkeypatch):
    """An archive with no manifest could not be verified offline, so it is refused."""
    connection = _ArchiveConnection(row=_export_row())
    storage = _FakeStorage(json.dumps({'rows': [{'alerts.json': []}]}).encode('utf-8'))
    _wire(monkeypatch, connection, storage)

    with pytest.raises(HTTPException) as exc_info:
        pilot.download_evidence_package_archive(PKG, _request())

    assert exc_info.value.status_code == 422
    assert exc_info.value.detail['error'] == 'PACKAGE_NOT_READY'


# ── M. Downloads are audited ─────────────────────────────────────────────────

def test_archive_download_records_an_audit_event(monkeypatch):
    connection = _ArchiveConnection(row=_export_row())
    storage = _FakeStorage()
    _wire(monkeypatch, connection, storage)

    pilot.download_evidence_package_archive(PKG, _request())

    assert 'evidence_package_downloaded' in connection.audit_actions
    metadata = connection.audit_metadata[-1]
    assert metadata['artifact'] == 'archive_zip'
    assert metadata['result'] == 'success'
    assert metadata['size_bytes'] > 0


def test_audit_metadata_never_records_signing_material(monkeypatch):
    from services.api.app import evidence_signing

    connection = _ArchiveConnection(row=_export_row())
    storage = _FakeStorage()
    _wire(monkeypatch, connection, storage)

    pilot.download_evidence_package_archive(PKG, _request())

    blob = json.dumps(connection.audit_metadata)
    assert evidence_signing._DEV_FALLBACK_SECRET.decode('utf-8') not in blob
    for marker in ('signing_secret', 'private_key', 'AWS_SECRET_ACCESS_KEY'):
        assert marker not in blob
