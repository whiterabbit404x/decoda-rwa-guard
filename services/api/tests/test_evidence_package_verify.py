"""Integration tests for verify_evidence_package + get_evidence_package_manifest.

Drives the real handlers with a fake DB connection and fake storage (the same
pattern used across the suite). Proves the stored-artifact round trip: a clean
package verifies, a tampered package is flagged Integrity Failed, and every
verification writes an append-only audit event.
"""
from __future__ import annotations

import json
from contextlib import contextmanager

import pytest

from services.api.app import pilot
from services.api.app.evidence_signing import build_evidence_manifest, seal_manifest

WS_ID = 'ws-verify-1'
USER_ID = 'user-verify-1'
PKG_ID = 'pkg-verify-1'


class _Result:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row

    def fetchall(self):
        if self._row is None:
            return []
        return self._row if isinstance(self._row, list) else [self._row]


def _signed_bundle_bytes(*, tamper: bool = False) -> bytes:
    files = {
        'summary.json': {'export_id': PKG_ID, 'incident_id': 'inc-1', 'schema_version': '1.1'},
        'alerts.json': [{'id': 'alert-1', 'severity': 'high'}],
        'incidents.json': [{'id': 'inc-1', 'status': 'contained'}],
        'response_actions.json': [{'id': 'act-1', 'status': 'executed'}],
        'audit_log.json': [{'id': 'aud-1', 'action': 'incident.opened'}],
    }
    manifest, _ = build_evidence_manifest(
        export_id=PKG_ID, export_type='proof_bundle', workspace_id=WS_ID,
        generated_at='2026-01-01T00:00:00Z', generated_by_user_id=USER_ID,
        source_resource_type='incident', source_resource_id='inc-1',
        storage_backend='local', file_values=files,
    )
    seal = seal_manifest(manifest)
    bundle = {**files, 'manifest.json': manifest, 'seal.json': seal}
    if tamper:
        # Mutate a file AFTER hashing so its recomputed SHA-256 no longer matches.
        bundle['alerts.json'] = [{'id': 'alert-1', 'severity': 'critical', 'injected': True}]
    return json.dumps({'rows': [bundle]}).encode('utf-8')


class _FakeStorage:
    backend_name = 'local'

    def __init__(self, content: bytes):
        self._content = content

    def read_bytes(self, *, object_key: str) -> bytes:
        return self._content

    def get_object_size(self, *, object_key: str):
        return len(self._content)


class _Conn:
    def __init__(self):
        self.filter_patches = []

    def execute(self, statement, params=None):
        n = ' '.join(str(statement).split())
        if 'FROM export_jobs WHERE id = %s AND workspace_id = %s' in n:
            return _Result({
                'id': PKG_ID, 'workspace_id': WS_ID, 'export_type': 'proof_bundle',
                'format': 'json', 'status': 'completed',
                'storage_object_key': f'{WS_ID}/{PKG_ID}.json', 'filters': {},
            })
        if 'UPDATE export_jobs SET filters' in n:
            self.filter_patches.append(params)
            return _Result(None)
        return _Result(None)

    def commit(self):
        pass


def _bootstrap(monkeypatch, conn, storage):
    @contextmanager
    def _fake_pg():
        yield conn

    audit_calls = []

    def _capture_audit(_conn, *, action, entity_type, entity_id, request, user_id, workspace_id, metadata=None):
        audit_calls.append({'action': action, 'entity_id': entity_id, 'metadata': metadata or {}})

    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, '_require_workspace_permission',
                        lambda *_a, **_k: ({'id': USER_ID}, {'workspace_id': WS_ID}))
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_a, **_k: {'id': USER_ID})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda *_a, **_k: {'workspace_id': WS_ID})
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: storage)
    monkeypatch.setattr(pilot, 'log_audit', _capture_audit)
    return audit_calls


class _Req:
    headers = {'x-workspace-id': WS_ID}
    query_params: dict = {}
    client = None


def test_verify_clean_package_is_verified_and_audited(monkeypatch):
    conn = _Conn()
    storage = _FakeStorage(_signed_bundle_bytes(tamper=False))
    audit_calls = _bootstrap(monkeypatch, conn, storage)

    result = pilot.verify_evidence_package(PKG_ID, _Req())

    assert result['integrity_status'] == 'verified'
    assert result['verification']['valid'] is True
    assert result['verification']['files_failed'] == []
    # The result was persisted back to the package.
    assert conn.filter_patches, 'verification result must be persisted'
    # An append-only audit event was recorded with the passing action.
    assert any(c['action'] == 'evidence_package_verification_passed' for c in audit_calls)


def test_verify_tampered_package_is_integrity_failed_and_audited(monkeypatch):
    conn = _Conn()
    storage = _FakeStorage(_signed_bundle_bytes(tamper=True))
    audit_calls = _bootstrap(monkeypatch, conn, storage)

    result = pilot.verify_evidence_package(PKG_ID, _Req())

    assert result['integrity_status'] == 'integrity_failed'
    assert result['verification']['valid'] is False
    assert 'alerts.json' in result['verification']['files_failed']
    assert any(c['action'] == 'evidence_package_verification_failed' for c in audit_calls)


def test_manifest_download_returns_manifest_and_audits(monkeypatch):
    conn = _Conn()
    storage = _FakeStorage(_signed_bundle_bytes(tamper=False))
    audit_calls = _bootstrap(monkeypatch, conn, storage)

    content, filename = pilot.get_evidence_package_manifest(PKG_ID, _Req())

    manifest = json.loads(content)
    assert 'files' in manifest and manifest['files']
    assert manifest.get('manifest_sha256')
    assert filename.endswith('.json')
    assert any(c['action'] == 'evidence_manifest_downloaded' for c in audit_calls)


def test_verify_missing_package_raises_not_found(monkeypatch):
    class _EmptyConn(_Conn):
        def execute(self, statement, params=None):
            n = ' '.join(str(statement).split())
            if 'FROM export_jobs WHERE id = %s AND workspace_id = %s' in n:
                return _Result(None)
            return _Result(None)

    conn = _EmptyConn()
    storage = _FakeStorage(_signed_bundle_bytes())
    _bootstrap(monkeypatch, conn, storage)

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        pilot.verify_evidence_package(PKG_ID, _Req())
    assert exc.value.status_code == 404


# ── list_exports enrichment (integrity_status, superseded, metrics) ───────────

import datetime as _dt


class _ListConn:
    """Two proof_bundle packages for the same incident, different creation times."""

    def __init__(self):
        older = {
            'id': 'pkg-older', 'workspace_id': WS_ID, 'export_type': 'proof_bundle',
            'format': 'json', 'status': 'completed', 'output_path': 'x',
            'storage_backend': 'local', 'storage_object_key': f'{WS_ID}/pkg-older.json',
            'error_message': None,
            'filters': {'incident_id': 'inc-1', 'completeness_score': 88, 'files_hashed': 8, 'integrity_hash': 'abc'},
            'size_bytes': 100, 'created_at': _dt.datetime(2026, 1, 1), 'updated_at': _dt.datetime(2026, 1, 1),
        }
        newer = {
            'id': 'pkg-newer', 'workspace_id': WS_ID, 'export_type': 'proof_bundle',
            'format': 'json', 'status': 'completed', 'output_path': 'x',
            'storage_backend': 'local', 'storage_object_key': f'{WS_ID}/pkg-newer.json',
            'error_message': None,
            'filters': {'incident_id': 'inc-1', 'completeness_score': 96, 'files_hashed': 8, 'integrity_hash': 'def',
                        'verification': {'valid': True}},
            'size_bytes': 120, 'created_at': _dt.datetime(2026, 1, 2), 'updated_at': _dt.datetime(2026, 1, 2),
        }
        self._rows = [newer, older]

    def execute(self, statement, params=None):
        n = ' '.join(str(statement).split())
        if 'FROM export_jobs' in n and 'ORDER BY created_at DESC' in n:
            return _Result(self._rows)
        return _Result(None)

    def commit(self):
        pass


def test_list_exports_flags_superseded_and_returns_metrics(monkeypatch):
    conn = _ListConn()

    @contextmanager
    def _fake_pg():
        yield conn

    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_a, **_k: {'id': USER_ID})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda *_a, **_k: {'workspace_id': WS_ID})

    result = pilot.list_exports(_Req())
    exports = {e['id']: e for e in result['exports']}
    assert exports['pkg-older']['superseded'] is True
    assert exports['pkg-newer']['superseded'] is False
    # Newer has a passing verification recorded → verified.
    assert exports['pkg-newer']['integrity_status'] == 'verified'
    metrics = result['metrics']
    assert metrics['total_packages'] == 2
    assert metrics['incidents_covered'] == 1
    assert metrics['total_size_bytes'] == 220
    assert metrics['verified'] == 1
    assert metrics['files_hashed'] == 16


class _DetailConn:
    def __init__(self):
        self._row = {
            'id': PKG_ID, 'workspace_id': WS_ID, 'export_type': 'proof_bundle',
            'format': 'json', 'status': 'completed', 'output_path': 'x',
            'storage_backend': 'local', 'storage_object_key': f'{WS_ID}/{PKG_ID}.json',
            'error_message': None,
            'filters': {'incident_id': 'inc-1', 'integrity_hash': 'abc'},
            'size_bytes': 100, 'created_at': _dt.datetime(2026, 1, 1), 'updated_at': _dt.datetime(2026, 1, 1),
        }

    def execute(self, statement, params=None):
        n = ' '.join(str(statement).split())
        if 'SELECT * FROM export_jobs WHERE id = %s AND workspace_id = %s' in n:
            return _Result(self._row)
        # Superseded check → no newer package.
        if "export_type = 'proof_bundle'" in n and 'created_at >' in n:
            return _Result(None)
        return _Result(None)

    def commit(self):
        pass


def test_get_export_returns_files_and_integrity(monkeypatch):
    conn = _DetailConn()
    storage = _FakeStorage(_signed_bundle_bytes(tamper=False))

    @contextmanager
    def _fake_pg():
        yield conn

    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_a, **_k: {'id': USER_ID})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda *_a, **_k: {'workspace_id': WS_ID})
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: storage)

    result = pilot.get_export(PKG_ID, _Req())
    export = result['export']
    # Files come from the signed manifest (5 evidence files).
    assert len(export['files']) == 5
    assert all(f['sha256'] for f in export['files'])
    # No verification recorded yet → hash_generated, not verified.
    assert export['integrity_status'] == 'hash_generated'
    assert export['manifest_download_url'] == f'/exports/{PKG_ID}/manifest'
    assert export['storage_available'] is True
