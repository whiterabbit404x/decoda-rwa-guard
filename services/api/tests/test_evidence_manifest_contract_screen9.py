"""Screen 9 — canonical evidence-manifest contract regression tests.

These lock in the fix for the EV-2026-004 production failure: a package showed
"Hash Generated" and offered Verify Integrity / Download Manifest, but the backend
verification returned PACKAGE_NOT_READY ("Package has no manifest to verify")
because the stored artifact had no retrievable manifest. The root cause was that
the read-path selectors inferred manifest existence from a files-hashed count
(or a persisted integrity_hash) instead of a single canonical manifest
reference resolved against the stored bytes.

The tests exercise the canonical resolver plus the verify / download / detail /
list handlers with the project's fake-DB + fake-storage harness (no live DB,
no network, no LLM). They prove the manifest is resolved in ONE place and that
Download Manifest and Verify Integrity operate on identical persisted bytes.
"""
from __future__ import annotations

import json
import pathlib
from contextlib import contextmanager

import pytest

from services.api.app import pilot
from services.api.app import evidence_completeness as ec
from services.api.app.evidence_signing import (
    build_evidence_manifest,
    seal_manifest,
    canonical_json,
)

WS_ID = 'ws-manifest-1'
USER_ID = 'user-manifest-1'
PKG_ID = 'pkg-manifest-1'

# The 8 files a proof bundle hashes (see _generate_export_artifact). A partial
# (40%) package still hashes all of its collected files — completeness is about
# how many evidence categories are present, not about the manifest.
_PARTIAL_FILES = {
    'summary.json': {'export_id': PKG_ID, 'incident_id': 'inc-1', 'completeness': {'score': 40, 'status': 'critical'}},
    'alerts.json': [{'id': 'alert-1', 'severity': 'high'}],
    'incidents.json': [{'id': 'inc-1', 'status': 'open'}],
    'detections.json': [{'id': 'det-1'}],
    'response_actions.json': [],
    'audit_log.json': [{'id': 'aud-1', 'action': 'incident.opened'}],
    'evidence.json': [{'id': 'ev-1'}],
    'detection_metrics.json': [{'id': 'dm-1'}],
}


def _build_manifest(files: dict) -> dict:
    manifest, _ = build_evidence_manifest(
        export_id=PKG_ID, export_type='proof_bundle', workspace_id=WS_ID,
        generated_at='2026-01-01T00:00:00Z', generated_by_user_id=USER_ID,
        source_resource_type='incident', source_resource_id='inc-1',
        storage_backend='local', file_values=files,
    )
    return manifest


def _bundle_bytes(files: dict, *, include_manifest: bool = True, tamper: bool = False) -> bytes:
    """Serialize a stored package artifact exactly as _generate_export_artifact does:
    ``{'rows': [ {<files>, 'manifest.json': ..., 'seal.json': ...} ]}``.
    ``include_manifest=False`` reproduces the EV-2026-004 artifact (no manifest)."""
    bundle = dict(files)
    if include_manifest:
        manifest = _build_manifest(files)
        bundle['manifest.json'] = manifest
        bundle['seal.json'] = seal_manifest(manifest)
    if tamper:
        bundle['alerts.json'] = [{'id': 'alert-1', 'severity': 'critical', 'injected': True}]
    return json.dumps({'rows': [bundle]}).encode('utf-8')


class _Result:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row

    def fetchall(self):
        if self._row is None:
            return []
        return self._row if isinstance(self._row, list) else [self._row]


class _FakeStorage:
    backend_name = 'local'

    def __init__(self, content: bytes | None):
        self._content = content

    def read_bytes(self, *, object_key: str) -> bytes:
        if self._content is None:
            raise FileNotFoundError(object_key)
        return self._content

    def get_object_size(self, *, object_key: str):
        return len(self._content) if self._content is not None else None


class _Conn:
    """Minimal export_jobs connection: one package row + captures filter patches."""

    def __init__(self, *, filters=None, status='completed'):
        self._filters = filters if filters is not None else {}
        self._status = status
        self.filter_patches = []

    def execute(self, statement, params=None):
        n = ' '.join(str(statement).split())
        if 'FROM export_jobs WHERE id = %s AND workspace_id = %s' in n and 'SELECT *' not in n:
            return _Result({
                'id': PKG_ID, 'workspace_id': WS_ID, 'export_type': 'proof_bundle',
                'format': 'json', 'status': self._status,
                'storage_object_key': f'{WS_ID}/{PKG_ID}.json', 'filters': dict(self._filters),
            })
        if 'SELECT * FROM export_jobs WHERE id = %s AND workspace_id = %s' in n:
            return _Result({
                'id': PKG_ID, 'workspace_id': WS_ID, 'export_type': 'proof_bundle',
                'format': 'json', 'status': self._status, 'output_path': 'x',
                'storage_backend': 'local', 'storage_object_key': f'{WS_ID}/{PKG_ID}.json',
                'error_message': None, 'filters': dict(self._filters),
                'size_bytes': 100, 'package_number': 'EV-2026-004',
                'created_at': '2026-01-01T00:00:00Z', 'updated_at': '2026-01-01T00:00:00Z',
            })
        if "export_type = 'proof_bundle'" in n and 'created_at >' in n:
            return _Result(None)  # not superseded
        if 'UPDATE export_jobs SET filters' in n:
            self.filter_patches.append(params)
            return _Result(None)
        return _Result(None)

    def commit(self):
        pass


class _Req:
    headers = {'x-workspace-id': WS_ID}
    query_params: dict = {}
    client = None


def _bootstrap(monkeypatch, conn, storage):
    @contextmanager
    def _fake_pg():
        yield conn

    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, '_require_workspace_permission',
                        lambda *_a, **_k: ({'id': USER_ID}, {'workspace_id': WS_ID}))
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_a, **_k: {'id': USER_ID})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda *_a, **_k: {'workspace_id': WS_ID, 'role': 'analyst'})
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: storage)
    monkeypatch.setattr(pilot, 'log_audit', lambda *a, **k: None)
    monkeypatch.setattr(pilot, '_workspace_permission_granted', lambda *a, **k: True)


# A package's persisted facts, as _generate_export_artifact writes them on a
# SUCCESSFUL manifest embed (integrity_hash == manifest_sha256 + reference fields).
def _persisted_ok(manifest: dict) -> dict:
    return {
        'incident_id': 'inc-1',
        'completeness_score': 40,
        'files_hashed': 8,
        'integrity_hash': manifest['manifest_sha256'],
        'manifest_sha256': manifest['manifest_sha256'],
        'manifest_file_count': len(manifest['files']),
        'manifest_size_bytes': len(canonical_json(manifest)),
    }


# The EV-2026-004 persisted facts: files_hashed + completeness recorded, but the
# manifest was never embedded, so NO integrity_hash / manifest_sha256.
_PERSISTED_EV_2026_004 = {
    'incident_id': 'inc-1',
    'completeness_score': 40,
    'files_hashed': 8,
}


# ── 1. Creation persists the canonical manifest reference ─────────────────────

def test_creation_persists_canonical_manifest_reference():
    manifest = _build_manifest(_PARTIAL_FILES)
    package = {'export_type': 'proof_bundle', 'status': 'completed',
               'storage_object_key': f'{WS_ID}/{PKG_ID}.json', **_persisted_ok(manifest)}
    ref = ec.resolve_manifest_reference(package)
    assert ref.exists is True
    assert ref.sha256 == manifest['manifest_sha256']
    assert ref.file_count == len(manifest['files'])
    assert ref.size_bytes == len(canonical_json(manifest))
    assert ref.storage_key == f'{WS_ID}/{PKG_ID}.json'


# ── 2. Manifest object is retrievable after creation ──────────────────────────

def test_manifest_object_retrievable_via_storage_reference(monkeypatch):
    manifest = _build_manifest(_PARTIAL_FILES)
    storage = _FakeStorage(_bundle_bytes(_PARTIAL_FILES))
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: storage)
    resolved = pilot._resolve_package_manifest({
        'id': PKG_ID, 'workspace_id': WS_ID, 'export_type': 'proof_bundle', 'format': 'json',
        'storage_object_key': f'{WS_ID}/{PKG_ID}.json', 'filters': _persisted_ok(manifest),
    })
    ref = resolved['reference']
    assert ref.exists is True and ref.retrievable is True
    assert ref.file_count == len(manifest['files'])
    assert ref.source == 'storage'


# ── 3. List API sees the same manifest ────────────────────────────────────────

def test_list_api_sees_manifest_reference(monkeypatch):
    manifest = _build_manifest(_PARTIAL_FILES)
    row = {
        'id': PKG_ID, 'workspace_id': WS_ID, 'export_type': 'proof_bundle', 'format': 'json',
        'status': 'completed', 'output_path': 'x', 'storage_backend': 'local',
        'storage_object_key': f'{WS_ID}/{PKG_ID}.json', 'error_message': None,
        'filters': _persisted_ok(manifest), 'size_bytes': 100,
        'package_number': 'EV-2026-004', 'created_at': '2026-01-01', 'updated_at': '2026-01-01',
    }

    class _ListConn:
        def execute(self, statement, params=None):
            n = ' '.join(str(statement).split())
            if 'FROM export_jobs' in n and 'ORDER BY created_at DESC' in n:
                return _Result([row])
            return _Result(None)

        def commit(self):
            pass

    @contextmanager
    def _pg():
        yield _ListConn()

    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _pg)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_a, **_k: {'id': USER_ID})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda *_a, **_k: {'workspace_id': WS_ID, 'role': 'analyst'})

    pkg = pilot.list_exports(_Req())['exports'][0]
    assert pkg['integrity_status'] == 'hash_generated'
    assert pkg['manifest_reference']['exists'] is True
    assert pkg['allowed_actions']['verify'] is True
    assert pkg['allowed_actions']['download_manifest'] is True


# ── 4. Detail API sees the same manifest ──────────────────────────────────────

def test_detail_api_sees_retrievable_manifest(monkeypatch):
    manifest = _build_manifest(_PARTIAL_FILES)
    conn = _Conn(filters=_persisted_ok(manifest))
    _bootstrap(monkeypatch, conn, _FakeStorage(_bundle_bytes(_PARTIAL_FILES)))
    export = pilot.get_export(PKG_ID, _Req())['export']
    assert export['integrity_status'] == 'hash_generated'
    assert export['manifest_reference']['retrievable'] is True
    assert len(export['files']) == len(manifest['files'])
    assert export['allowed_actions']['verify'] is True
    assert export['allowed_actions']['download_manifest'] is True


# ── 5 & 6. Download Manifest and Verify Integrity use the same stored bytes ────

def test_download_and_verify_use_identical_manifest_bytes(monkeypatch):
    manifest = _build_manifest(_PARTIAL_FILES)
    storage = _FakeStorage(_bundle_bytes(_PARTIAL_FILES))

    conn_dl = _Conn(filters=_persisted_ok(manifest))
    _bootstrap(monkeypatch, conn_dl, storage)
    download_bytes, filename = pilot.get_evidence_package_manifest(PKG_ID, _Req())
    assert filename.endswith('.json')

    conn_v = _Conn(filters=_persisted_ok(manifest))
    _bootstrap(monkeypatch, conn_v, storage)
    result = pilot.verify_evidence_package(PKG_ID, _Req())

    # Download returns the exact canonical manifest bytes that verification checks.
    downloaded_manifest = json.loads(download_bytes)
    assert download_bytes == pilot._canonical_manifest_bytes(downloaded_manifest)
    assert downloaded_manifest['manifest_sha256'] == manifest['manifest_sha256']
    assert result['verification']['manifest_sha256'] == downloaded_manifest['manifest_sha256']
    assert result['verification']['valid'] is True


# ── 7. Hash Generated is impossible without a retrievable manifest ────────────

def test_hash_generated_impossible_without_retrievable_manifest(monkeypatch):
    # Detail on EV-2026-004: artifact has NO manifest, filters lack integrity_hash.
    conn = _Conn(filters=dict(_PERSISTED_EV_2026_004))
    _bootstrap(monkeypatch, conn, _FakeStorage(_bundle_bytes(_PARTIAL_FILES, include_manifest=False)))
    export = pilot.get_export(PKG_ID, _Req())['export']
    assert export['integrity_status'] == 'manifest_missing'
    assert export['integrity_status'] != 'hash_generated'
    assert export['hash_state'] == 'not_generated'
    assert export['files_hashed'] == 0
    assert export['manifest_reference']['retrievable'] is False


# ── 8. Verify action is impossible without a retrievable manifest ─────────────

def test_verify_action_disabled_without_manifest():
    package = {'export_type': 'proof_bundle', 'status': 'completed', **_PERSISTED_EV_2026_004}
    actions = ec.get_package_allowed_actions(package, can_export=True)
    assert actions['verify'] is False
    assert actions['download_manifest'] is False


def test_verify_endpoint_rejects_missing_manifest(monkeypatch):
    from fastapi import HTTPException
    conn = _Conn(filters=dict(_PERSISTED_EV_2026_004))
    _bootstrap(monkeypatch, conn, _FakeStorage(_bundle_bytes(_PARTIAL_FILES, include_manifest=False)))
    with pytest.raises(HTTPException) as exc:
        pilot.verify_evidence_package(PKG_ID, _Req())
    assert exc.value.status_code == 422
    assert exc.value.detail['error'] == 'PACKAGE_NOT_READY'
    # Customer-safe message — no raw manifest internals.
    assert 'retrievable manifest' in exc.value.detail['message']


# ── 9. Table SHA-256 uses the authoritative manifest/package SHA ──────────────

def test_table_sha256_uses_authoritative_manifest_hash():
    manifest = _build_manifest(_PARTIAL_FILES)
    ok_pkg = {'export_type': 'proof_bundle', 'status': 'completed', **_persisted_ok(manifest)}
    # When a manifest exists the canonical SHA is the manifest_sha256 (== integrity_hash).
    assert ec._recorded_manifest_sha256(ok_pkg) == manifest['manifest_sha256']
    assert ec.get_evidence_package_display_state(ok_pkg)['hash_state'] == 'present'
    # EV-2026-004: no recorded manifest SHA → the SHA column is "not generated",
    # and the integrity pill agrees (manifest_missing, never hash_generated).
    ev = {'export_type': 'proof_bundle', 'status': 'completed', **_PERSISTED_EV_2026_004}
    assert ec._recorded_manifest_sha256(ev) is None
    st = ec.get_evidence_package_display_state(ev)
    assert st['hash_state'] == 'not_generated'
    assert st['integrity_status'] == 'manifest_missing'


# ── 10. File hashes alone cannot imply manifest readiness ────────────────────

def test_files_hashed_alone_does_not_imply_manifest():
    # The exact EV-2026-004 metadata: 8 files "hashed", 40% complete, NO manifest.
    package = {'export_type': 'proof_bundle', 'status': 'completed', **_PERSISTED_EV_2026_004}
    state = ec.get_evidence_package_display_state(package)
    assert state['has_hashes'] is False
    assert state['integrity_status'] == 'manifest_missing'
    assert state['files_hashed'] == 0
    assert ec.get_package_allowed_actions(package, can_export=True)['verify'] is False


# ── 11. Manifest SHA alone cannot imply readiness if the object is missing ────

def test_recorded_hash_without_object_is_not_retrievable(monkeypatch):
    manifest = _build_manifest(_PARTIAL_FILES)
    # Recorded manifest SHA present, but storage cannot produce the object.
    storage = _FakeStorage(None)  # read_bytes → FileNotFoundError
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: storage)
    resolved = pilot._resolve_package_manifest({
        'id': PKG_ID, 'workspace_id': WS_ID, 'export_type': 'proof_bundle', 'format': 'json',
        'storage_object_key': f'{WS_ID}/{PKG_ID}.json', 'filters': _persisted_ok(manifest),
    })
    ref = resolved['reference']
    assert ref.exists is True          # a manifest WAS recorded
    assert ref.retrievable is False    # ...but its bytes cannot be produced
    assert resolved['storage_error'] == 'object_not_found'


def test_verify_endpoint_rejects_when_object_missing(monkeypatch):
    from fastapi import HTTPException
    manifest = _build_manifest(_PARTIAL_FILES)
    conn = _Conn(filters=_persisted_ok(manifest))
    _bootstrap(monkeypatch, conn, _FakeStorage(None))
    with pytest.raises(HTTPException) as exc:
        pilot.verify_evidence_package(PKG_ID, _Req())
    assert exc.value.status_code == 404
    assert exc.value.detail['error'] == 'PACKAGE_STORAGE_UNAVAILABLE'


# ── 12. A partial 40% evidence package can still verify successfully ──────────

def test_partial_40pct_package_verifies_successfully(monkeypatch):
    manifest = _build_manifest(_PARTIAL_FILES)
    conn = _Conn(filters=_persisted_ok(manifest))
    _bootstrap(monkeypatch, conn, _FakeStorage(_bundle_bytes(_PARTIAL_FILES)))
    result = pilot.verify_evidence_package(PKG_ID, _Req())
    assert result['integrity_status'] == 'verified'
    assert result['verification']['valid'] is True
    assert result['verification']['files_verified'] == len(manifest['files'])


# ── 13. Successful verification leaves completeness at 40% ────────────────────

def test_verification_does_not_change_completeness(monkeypatch):
    manifest = _build_manifest(_PARTIAL_FILES)
    conn = _Conn(filters=_persisted_ok(manifest))
    _bootstrap(monkeypatch, conn, _FakeStorage(_bundle_bytes(_PARTIAL_FILES)))
    pilot.verify_evidence_package(PKG_ID, _Req())
    # The only persisted patch is verification/integrity_status/verified_at —
    # completeness_score is never mutated by verification.
    assert conn.filter_patches, 'verification result must be persisted'
    patch_json = conn.filter_patches[0][0]
    patch = json.loads(patch_json)
    assert 'completeness_score' not in patch
    assert set(patch.keys()) == {'verification', 'integrity_status', 'verified_at'}
    assert patch['integrity_status'] == 'verified'


# ── 14. Failed manifest retrieval produces Manifest Missing / PACKAGE_NOT_READY ─

def test_failed_retrieval_surfaces_manifest_missing(monkeypatch):
    # Display state: manifest_missing (non-exportable, not verified).
    package = {'export_type': 'proof_bundle', 'status': 'completed', **_PERSISTED_EV_2026_004}
    state = ec.get_evidence_package_display_state(package)
    assert state['is_manifest_missing'] is True
    assert state['is_export_ready'] is False
    assert state['integrity_status'] in ec._NON_EXPORTABLE_INTEGRITY


# ── 15. No raw JSON error is displayed in the customer UI ─────────────────────

def test_panel_replaces_raw_json_verification_error():
    root = pathlib.Path(__file__).resolve().parents[3]
    panel = (root / 'apps/web/app/evidence-audit-panel.tsx').read_text()
    # The friendly-error helper and its PACKAGE_NOT_READY copy exist.
    assert 'friendlyPackageError' in panel
    assert 'Package is not ready for verification' in panel
    assert 'does not have a retrievable manifest' in panel
    # Technical codes are only shown in an expandable diagnostics disclosure.
    assert 'Technical details' in panel
    assert 'setDiagnostics' in panel
    # The old raw-JSON error rendering is gone.
    assert 'Verification failed: ${detail}' not in panel
    assert 'JSON.stringify(body.detail)' not in panel
