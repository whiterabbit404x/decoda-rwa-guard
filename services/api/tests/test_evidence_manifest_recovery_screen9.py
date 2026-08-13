"""Screen 9 — Manifest-Missing recovery flow regression tests.

These lock in the recovery flow for the EV-2026-004 production state: a completed
evidence package built as a proof bundle whose canonical manifest was never
persisted (Hash Verification = Manifest Missing, Verify Integrity disabled). The
recovery endpoint reads the package's EXACT stored bytes, computes a SHA-256 per
file, builds a deterministic manifest, seals it, writes it back WITHOUT changing
any evidence file, reads it back to confirm, and only then persists the canonical
manifest reference so Verify Integrity / Download Manifest become available.

The tests exercise the pure recovery-manifest builder, the allowed-action gating,
and the POST /exports/{id}/manifest endpoint against the project's fake-DB +
read/write fake-storage harness (no live DB, no network, no LLM). They prove the
recovery uses exact stored bytes, is deterministic and idempotent, persists the
manifest reference, never changes completeness, keeps verified/immutable packages
immutable, denies cross-workspace access, and fails closed (state stays
Manifest Missing) on a generation failure.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
from contextlib import contextmanager

import pytest

from services.api.app import pilot
from services.api.app import evidence_completeness as ec
from services.api.app.evidence_signing import (
    build_recovery_manifest,
    build_evidence_manifest,
    seal_manifest,
    canonical_json,
)

WS_ID = 'ws-recovery-1'
OTHER_WS_ID = 'ws-recovery-2'
USER_ID = 'user-recovery-1'
PKG_ID = 'pkg-recovery-1'
OBJECT_KEY = f'{WS_ID}/{PKG_ID}.json'

# The 8 files a proof bundle hashes (see _generate_export_artifact). A partial
# (40%) package still stores all of its collected files — completeness is about
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


def _sha_of(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


def _bundle_bytes(files: dict, *, include_manifest: bool) -> bytes:
    """Serialize a stored package artifact exactly as _generate_export_artifact does:
    ``{'rows': [ {<files>[, 'manifest.json', 'seal.json']} ]}``.
    ``include_manifest=False`` reproduces the EV-2026-004 artifact (no manifest)."""
    bundle = dict(files)
    if include_manifest:
        manifest, _ = build_evidence_manifest(
            export_id=PKG_ID, export_type='proof_bundle', workspace_id=WS_ID,
            generated_at='2026-01-01T00:00:00Z', generated_by_user_id=USER_ID,
            source_resource_type='incident', source_resource_id='inc-1',
            storage_backend='local', file_values=files,
        )
        bundle['manifest.json'] = manifest
        bundle['seal.json'] = seal_manifest(manifest)
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


class _RWStorage:
    """Round-trip fake storage: writes persist and are returned on later reads."""

    backend_name = 'local'

    def __init__(self, initial: dict[str, bytes] | None = None):
        self._store: dict[str, bytes] = dict(initial or {})
        self.writes = 0

    def write_bytes(self, *, object_key: str, content: bytes) -> str:
        self._store[object_key] = content
        self.writes += 1
        return object_key

    def read_bytes(self, *, object_key: str) -> bytes:
        if object_key not in self._store:
            raise FileNotFoundError(object_key)
        return self._store[object_key]

    def get_object_size(self, *, object_key: str):
        return len(self._store[object_key]) if object_key in self._store else None


class _WriteDropStorage(_RWStorage):
    """Storage whose writes silently vanish — models a failed persist so the
    read-back never sees the manifest and generation must fail closed."""

    def write_bytes(self, *, object_key: str, content: bytes) -> str:
        self.writes += 1
        return object_key


class _RecoveryConn:
    """export_jobs connection returning one package row + capturing filter patches."""

    def __init__(self, *, filters=None, status='completed', row_workspace=WS_ID, found=True):
        self._filters = filters if filters is not None else {}
        self._status = status
        self._row_workspace = row_workspace
        self._found = found
        self.filter_patches: list = []

    def _row(self):
        return {
            'id': PKG_ID, 'workspace_id': self._row_workspace, 'export_type': 'proof_bundle',
            'format': 'json', 'status': self._status, 'output_path': 'x',
            'storage_backend': 'local', 'storage_object_key': OBJECT_KEY,
            'error_message': None, 'filters': dict(self._filters),
            'size_bytes': 100, 'package_number': 'EV-2026-004',
            'requested_by_user_id': USER_ID,
            'created_at': '2026-01-01T00:00:00Z', 'updated_at': '2026-01-01T00:00:00Z',
        }

    def execute(self, statement, params=None):
        n = ' '.join(str(statement).split())
        if 'SELECT * FROM export_jobs WHERE id = %s AND workspace_id = %s' in n:
            # Workspace-scoped lookup: a package in another workspace resolves to None.
            return _Result(self._row() if self._found else None)
        if 'FROM export_jobs WHERE id = %s AND workspace_id = %s' in n and 'SELECT *' not in n:
            return _Result({
                'id': PKG_ID, 'workspace_id': self._row_workspace, 'export_type': 'proof_bundle',
                'format': 'json', 'status': self._status,
                'storage_object_key': OBJECT_KEY, 'filters': dict(self._filters),
            } if self._found else None)
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


# Persisted facts on a SUCCESSFUL manifest embed (integrity_hash == manifest_sha256).
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


def _ev_package() -> dict:
    return {'export_type': 'proof_bundle', 'status': 'completed', **_PERSISTED_EV_2026_004}


def _seed_missing_storage() -> _RWStorage:
    return _RWStorage({OBJECT_KEY: _bundle_bytes(_PARTIAL_FILES, include_manifest=False)})


# ── 1. Manifest Missing disables Verify Integrity ────────────────────────────

def test_manifest_missing_disables_verify_integrity():
    actions = ec.get_package_allowed_actions(_ev_package(), can_export=True)
    assert actions['verify'] is False
    assert actions['download_manifest'] is False
    assert actions['copy_hash'] is False


# ── 2. Manifest Missing exposes Generate Manifest ────────────────────────────

def test_manifest_missing_exposes_generate_manifest():
    actions = ec.get_package_allowed_actions(_ev_package(), can_export=True)
    assert actions['generate_manifest'] is True


def test_generate_manifest_requires_export_permission():
    # No evidence.export permission → recovery is not offered.
    actions = ec.get_package_allowed_actions(_ev_package(), can_export=False)
    assert actions['generate_manifest'] is False


def test_generate_manifest_not_offered_once_manifest_exists():
    manifest, _ = build_evidence_manifest(
        export_id=PKG_ID, export_type='proof_bundle', workspace_id=WS_ID,
        generated_at='2026-01-01T00:00:00Z', generated_by_user_id=USER_ID,
        source_resource_type='incident', source_resource_id='inc-1',
        storage_backend='local', file_values=_PARTIAL_FILES,
    )
    pkg = {'export_type': 'proof_bundle', 'status': 'completed', **_persisted_ok(manifest)}
    actions = ec.get_package_allowed_actions(pkg, can_export=True)
    assert actions['generate_manifest'] is False
    assert actions['verify'] is True


# ── 3. Generate Manifest uses the EXACT stored package/file bytes ────────────

def test_generate_uses_exact_stored_file_bytes(monkeypatch):
    storage = _seed_missing_storage()
    _bootstrap(monkeypatch, _RecoveryConn(filters=dict(_PERSISTED_EV_2026_004)), storage)
    pilot.generate_evidence_package_manifest(PKG_ID, _Req())

    stored = json.loads(storage.read_bytes(object_key=OBJECT_KEY))
    bundle = stored['rows'][0]
    manifest = bundle['manifest.json']
    by_path = {e['path']: e for e in manifest['files']}
    # Every file's manifest SHA-256 is computed from the exact stored file bytes.
    for path, value in _PARTIAL_FILES.items():
        assert by_path[path]['sha256'] == _sha_of(value)
        assert by_path[path]['size_bytes'] == len(canonical_json(value))


# ── 4. Every included file receives a SHA-256 ────────────────────────────────

def test_every_included_file_receives_sha256():
    manifest, _ = build_recovery_manifest(
        export_id=PKG_ID, export_type='proof_bundle', workspace_id=WS_ID,
        generated_at='2026-01-01T00:00:00Z', generated_by_user_id=USER_ID,
        incident_id='inc-1', storage_backend='local', file_values=_PARTIAL_FILES,
        file_source_types=pilot._PACKAGE_FILE_SOURCE_TYPES,
    )
    assert len(manifest['files']) == len(_PARTIAL_FILES)
    for entry in manifest['files']:
        assert entry['sha256'] and len(entry['sha256']) == 64
        assert entry['size_bytes'] > 0
        assert entry['media_type'] == 'application/json'
        assert entry['source_record_type']


# ── 5. Deterministic input generates the same manifest / hash ────────────────

def test_deterministic_input_generates_same_manifest_hash():
    kwargs = dict(
        export_id=PKG_ID, export_type='proof_bundle', workspace_id=WS_ID,
        generated_at='2026-01-01T00:00:00Z', generated_by_user_id=USER_ID,
        incident_id='inc-1', storage_backend='local', file_values=_PARTIAL_FILES,
        package_number='EV-2026-004', file_source_types=pilot._PACKAGE_FILE_SOURCE_TYPES,
        completeness_score=40,
    )
    m1, _ = build_recovery_manifest(**kwargs)
    m2, _ = build_recovery_manifest(**kwargs)
    assert m1 == m2
    assert m1['manifest_sha256'] == m2['manifest_sha256']
    # The manifest hash is over the body WITHOUT its own hash field.
    body = {k: v for k, v in m1.items() if k != 'manifest_sha256'}
    assert m1['manifest_sha256'] == hashlib.sha256(canonical_json(body)).hexdigest()


# ── 6. Repeated recovery is idempotent ───────────────────────────────────────

def test_repeated_recovery_is_idempotent(monkeypatch):
    storage = _seed_missing_storage()
    _bootstrap(monkeypatch, _RecoveryConn(filters=dict(_PERSISTED_EV_2026_004)), storage)

    first = pilot.generate_evidence_package_manifest(PKG_ID, _Req())
    assert first['generated'] is True
    writes_after_first = storage.writes

    second = pilot.generate_evidence_package_manifest(PKG_ID, _Req())
    # Second call is a no-op: same manifest returned, nothing rewritten.
    assert second['generated'] is False
    assert second['manifest_sha256'] == first['manifest_sha256']
    assert storage.writes == writes_after_first


# ── 7. Manifest metadata is persisted (completeness is NOT) ──────────────────

def test_manifest_metadata_persisted_without_completeness(monkeypatch):
    conn = _RecoveryConn(filters=dict(_PERSISTED_EV_2026_004))
    _bootstrap(monkeypatch, conn, _seed_missing_storage())
    result = pilot.generate_evidence_package_manifest(PKG_ID, _Req())

    assert conn.filter_patches, 'the manifest reference must be persisted'
    patch = json.loads(conn.filter_patches[0][0])
    assert set(patch.keys()) == {
        'integrity_hash', 'manifest_sha256', 'manifest_file_count',
        'manifest_size_bytes', 'manifest_generated_at', 'manifest_recovered',
    }
    assert patch['manifest_sha256'] == result['manifest_sha256']
    assert patch['integrity_hash'] == result['manifest_sha256']
    assert patch['manifest_file_count'] == len(_PARTIAL_FILES)
    assert patch['manifest_size_bytes'] > 0
    # A recovered manifest never writes a completeness score.
    assert 'completeness_score' not in patch


# ── 8. Manifest is retrievable after persistence ─────────────────────────────

def test_manifest_retrievable_after_persistence(monkeypatch):
    storage = _seed_missing_storage()
    _bootstrap(monkeypatch, _RecoveryConn(filters=dict(_PERSISTED_EV_2026_004)), storage)
    result = pilot.generate_evidence_package_manifest(PKG_ID, _Req())
    assert result['integrity_status'] == 'hash_generated'
    assert result['manifest_reference']['retrievable'] is True
    assert result['files_hashed'] == len(_PARTIAL_FILES)

    resolved = pilot._resolve_package_manifest({
        'id': PKG_ID, 'workspace_id': WS_ID, 'export_type': 'proof_bundle', 'format': 'json',
        'storage_object_key': OBJECT_KEY, 'filters': dict(_PERSISTED_EV_2026_004),
    })
    assert resolved['reference'].retrievable is True
    assert resolved['reference'].sha256 == result['manifest_sha256']


# ── 9. Download Manifest uses the same generated bytes; the package verifies ──

def test_download_and_verify_use_generated_manifest(monkeypatch):
    storage = _seed_missing_storage()
    _bootstrap(monkeypatch, _RecoveryConn(filters=dict(_PERSISTED_EV_2026_004)), storage)
    gen = pilot.generate_evidence_package_manifest(PKG_ID, _Req())

    _bootstrap(monkeypatch, _RecoveryConn(filters=dict(_PERSISTED_EV_2026_004)), storage)
    download_bytes, filename = pilot.get_evidence_package_manifest(PKG_ID, _Req())
    assert filename.endswith('.json')
    downloaded = json.loads(download_bytes)
    assert download_bytes == pilot._canonical_manifest_bytes(downloaded)
    assert downloaded['manifest_sha256'] == gen['manifest_sha256']

    _bootstrap(monkeypatch, _RecoveryConn(filters=dict(_PERSISTED_EV_2026_004)), storage)
    verify = pilot.verify_evidence_package(PKG_ID, _Req())
    assert verify['verification']['valid'] is True
    assert verify['verification']['manifest_sha256'] == gen['manifest_sha256']


# ── 10. SHA-256 column shows the persisted authoritative hash after recovery ──

def test_detail_sha256_shows_generated_hash(monkeypatch):
    storage = _seed_missing_storage()
    _bootstrap(monkeypatch, _RecoveryConn(filters=dict(_PERSISTED_EV_2026_004)), storage)
    gen = pilot.generate_evidence_package_manifest(PKG_ID, _Req())

    _bootstrap(monkeypatch, _RecoveryConn(filters=dict(_PERSISTED_EV_2026_004)), storage)
    export = pilot.get_export(PKG_ID, _Req())['export']
    assert export['integrity_status'] == 'hash_generated'
    assert export['manifest_sha256'] == gen['manifest_sha256']
    assert export['hash_state'] == 'present'
    assert export['allowed_actions']['generate_manifest'] is False


# ── 11. Verify Integrity becomes enabled only after successful generation ─────

def test_verify_enabled_only_after_generation(monkeypatch):
    storage = _seed_missing_storage()
    # Before recovery: manifest_missing → verify disabled, generate offered.
    _bootstrap(monkeypatch, _RecoveryConn(filters=dict(_PERSISTED_EV_2026_004)), storage)
    before = pilot.get_export(PKG_ID, _Req())['export']
    assert before['integrity_status'] == 'manifest_missing'
    assert before['allowed_actions']['verify'] is False
    assert before['allowed_actions']['generate_manifest'] is True

    _bootstrap(monkeypatch, _RecoveryConn(filters=dict(_PERSISTED_EV_2026_004)), storage)
    result = pilot.generate_evidence_package_manifest(PKG_ID, _Req())
    assert result['allowed_actions']['verify'] is True
    assert result['allowed_actions']['generate_manifest'] is False

    # And the reloaded detail agrees.
    _bootstrap(monkeypatch, _RecoveryConn(filters=dict(_PERSISTED_EV_2026_004)), storage)
    after = pilot.get_export(PKG_ID, _Req())['export']
    assert after['allowed_actions']['verify'] is True


# ── 12. A 40% partial package generates a valid manifest ─────────────────────

def test_partial_40pct_package_generates_valid_manifest(monkeypatch):
    storage = _seed_missing_storage()
    _bootstrap(monkeypatch, _RecoveryConn(filters=dict(_PERSISTED_EV_2026_004)), storage)
    pilot.generate_evidence_package_manifest(PKG_ID, _Req())

    _bootstrap(monkeypatch, _RecoveryConn(filters=dict(_PERSISTED_EV_2026_004)), storage)
    verify = pilot.verify_evidence_package(PKG_ID, _Req())
    assert verify['integrity_status'] == 'verified'
    assert verify['verification']['files_verified'] == len(_PARTIAL_FILES)


# ── 13. Manifest generation does not increase evidence completeness ──────────

def test_generation_does_not_increase_completeness(monkeypatch):
    storage = _seed_missing_storage()
    _bootstrap(monkeypatch, _RecoveryConn(filters=dict(_PERSISTED_EV_2026_004)), storage)
    pilot.generate_evidence_package_manifest(PKG_ID, _Req())

    _bootstrap(monkeypatch, _RecoveryConn(filters=dict(_PERSISTED_EV_2026_004)), storage)
    export = pilot.get_export(PKG_ID, _Req())['export']
    # Still a 40% Critical package — hashes existing does not raise completeness.
    assert export['completeness_score'] == 40
    assert (export['completeness'] or {}).get('score') == 40


# ── 14. Verified / immutable historical packages cannot be mutated ───────────

def test_verified_package_not_mutated(monkeypatch):
    manifest, _ = build_evidence_manifest(
        export_id=PKG_ID, export_type='proof_bundle', workspace_id=WS_ID,
        generated_at='2026-01-01T00:00:00Z', generated_by_user_id=USER_ID,
        source_resource_type='incident', source_resource_id='inc-1',
        storage_backend='local', file_values=_PARTIAL_FILES,
    )
    verified_filters = {
        **_persisted_ok(manifest),
        'verification': {'valid': True, 'verified_at': '2026-02-01T00:00:00Z'},
    }
    storage = _RWStorage({OBJECT_KEY: _bundle_bytes(_PARTIAL_FILES, include_manifest=True)})
    conn = _RecoveryConn(filters=verified_filters)
    _bootstrap(monkeypatch, conn, storage)
    result = pilot.generate_evidence_package_manifest(PKG_ID, _Req())
    # Idempotent no-op: a package that already carries a manifest is never rewritten.
    assert result['generated'] is False
    assert result['integrity_status'] == 'verified'
    assert storage.writes == 0
    assert conn.filter_patches == []


def test_present_but_unretrievable_manifest_is_not_rewritten(monkeypatch):
    # A stored manifest whose file list is not fully hashed is preserved, not
    # silently overwritten — a superseding package is required instead.
    manifest, _ = build_evidence_manifest(
        export_id=PKG_ID, export_type='proof_bundle', workspace_id=WS_ID,
        generated_at='2026-01-01T00:00:00Z', generated_by_user_id=USER_ID,
        source_resource_type='incident', source_resource_id='inc-1',
        storage_backend='local', file_values=_PARTIAL_FILES,
    )
    manifest['files'][0].pop('sha256', None)  # corrupt: a file with no hash
    bundle = dict(_PARTIAL_FILES)
    bundle['manifest.json'] = manifest
    bundle['seal.json'] = seal_manifest(manifest)
    storage = _RWStorage({OBJECT_KEY: json.dumps({'rows': [bundle]}).encode('utf-8')})
    conn = _RecoveryConn(filters=dict(_PERSISTED_EV_2026_004))
    _bootstrap(monkeypatch, conn, storage)
    with pytest.raises(pilot.HTTPException) as exc:
        pilot.generate_evidence_package_manifest(PKG_ID, _Req())
    assert exc.value.status_code == 409
    assert exc.value.detail['error'] == 'PACKAGE_MANIFEST_IMMUTABLE'
    assert storage.writes == 0


# ── 15. Cross-workspace recovery is denied ───────────────────────────────────

def test_cross_workspace_recovery_denied(monkeypatch):
    # The workspace-scoped lookup finds nothing for a package in another workspace.
    conn = _RecoveryConn(filters=dict(_PERSISTED_EV_2026_004), found=False)
    _bootstrap(monkeypatch, conn, _seed_missing_storage())
    with pytest.raises(pilot.HTTPException) as exc:
        pilot.generate_evidence_package_manifest(PKG_ID, _Req())
    assert exc.value.status_code == 404
    assert exc.value.detail['error'] == 'PACKAGE_NOT_FOUND'
    assert conn.filter_patches == []


# ── 16. Manifest generation failure leaves state as Manifest Missing ─────────

def test_generation_failure_leaves_manifest_missing(monkeypatch):
    # Storage whose write is dropped → read-back never sees a manifest → fail closed.
    storage = _WriteDropStorage({OBJECT_KEY: _bundle_bytes(_PARTIAL_FILES, include_manifest=False)})
    conn = _RecoveryConn(filters=dict(_PERSISTED_EV_2026_004))
    _bootstrap(monkeypatch, conn, storage)
    with pytest.raises(pilot.HTTPException) as exc:
        pilot.generate_evidence_package_manifest(PKG_ID, _Req())
    assert exc.value.status_code == 500
    assert exc.value.detail['error'] == 'MANIFEST_GENERATION_FAILED'
    # No manifest reference persisted — the package stays Manifest Missing.
    assert conn.filter_patches == []
    resolved = pilot._resolve_package_manifest({
        'id': PKG_ID, 'workspace_id': WS_ID, 'export_type': 'proof_bundle', 'format': 'json',
        'storage_object_key': OBJECT_KEY, 'filters': dict(_PERSISTED_EV_2026_004),
    })
    assert resolved['reference'].retrievable is False
    pkg = {'export_type': 'proof_bundle', 'status': 'completed', **_PERSISTED_EV_2026_004}
    assert ec.get_evidence_package_display_state(pkg)['integrity_status'] == 'manifest_missing'


def test_generation_rejects_non_completed_package(monkeypatch):
    conn = _RecoveryConn(filters=dict(_PERSISTED_EV_2026_004), status='building')
    _bootstrap(monkeypatch, conn, _seed_missing_storage())
    with pytest.raises(pilot.HTTPException) as exc:
        pilot.generate_evidence_package_manifest(PKG_ID, _Req())
    assert exc.value.status_code == 409
    assert exc.value.detail['error'] == 'PACKAGE_NOT_READY'


# ── 17. Customer UI shows a friendly recovery flow (no raw JSON) ──────────────

def test_panel_exposes_friendly_recovery_ui():
    root = pathlib.Path(__file__).resolve().parents[3]
    panel = (root / 'apps/web/app/evidence-audit-panel.tsx').read_text()
    # Three explicit states replace the single ambiguous "Integrity Status".
    assert 'Generation Status' in panel
    assert 'Hash Verification' in panel
    assert 'Evidence Completeness' in panel
    # Friendly recovery copy (section 8) — never a raw JSON error.
    assert 'Manifest required' in panel
    assert 'Generate Manifest' in panel
    assert 'Generating manifest…' in panel
    assert 'ready for integrity verification' in panel
    assert 'Manifest could not be generated' in panel
    # Customer-safe error copy for the recovery codes.
    assert 'MANIFEST_GENERATION_FAILED' in panel
    # The recovery action is wired to the backend endpoint.
    assert 'generate_manifest' in panel
    # Fallback recovery (Regenerate Package) copy + wiring (section 5).
    assert 'Regenerate Package' in panel
    assert 'regenerate_package' in panel
    assert '/regenerate' in panel
    assert 'superseding' in panel or 'preserved as historical evidence' in panel


# ── 18. Regenerate Package (fallback recovery) allowed-action gating ──────────

def test_manifest_missing_exposes_regenerate_package():
    actions = ec.get_package_allowed_actions(_ev_package(), can_export=True)
    assert actions['regenerate_package'] is True


def test_regenerate_requires_export_permission():
    actions = ec.get_package_allowed_actions(_ev_package(), can_export=False)
    assert actions['regenerate_package'] is False


def test_regenerate_not_offered_once_manifest_exists():
    manifest, _ = build_evidence_manifest(
        export_id=PKG_ID, export_type='proof_bundle', workspace_id=WS_ID,
        generated_at='2026-01-01T00:00:00Z', generated_by_user_id=USER_ID,
        source_resource_type='incident', source_resource_id='inc-1',
        storage_backend='local', file_values=_PARTIAL_FILES,
    )
    pkg = {'export_type': 'proof_bundle', 'status': 'completed', **_persisted_ok(manifest)}
    actions = ec.get_package_allowed_actions(pkg, can_export=True)
    assert actions['regenerate_package'] is False
    # A usable manifest means Verify is enabled instead — no recovery is offered.
    assert actions['verify'] is True


def test_regenerate_not_offered_when_superseded():
    pkg = {**_ev_package(), 'superseded': True}
    actions = ec.get_package_allowed_actions(pkg, can_export=True)
    # A superseded package needs no recovery — neither recovery action is offered.
    assert actions['regenerate_package'] is False
    assert actions['generate_manifest'] is False


# ── 19. Regenerate endpoint: supersede, preserve original, deny cross-workspace ─

def test_regenerate_creates_superseding_package_preserving_original(monkeypatch):
    conn = _RecoveryConn(filters=dict(_PERSISTED_EV_2026_004))
    _bootstrap(monkeypatch, conn, _seed_missing_storage())

    captured: dict = {}

    def _fake_create(payload, request):
        captured['payload'] = payload
        return {
            'package_id': 'pkg-regenerated-2', 'export_job_id': 'pkg-regenerated-2',
            'id': 'pkg-regenerated-2', 'incident_id': payload.get('incident_id'),
            'status': 'completed', 'created': True, 'package_number': 'EV-2026-005',
        }

    monkeypatch.setattr(pilot, 'create_proof_bundle_export', _fake_create)

    result = pilot.regenerate_evidence_package(PKG_ID, _Req())
    # Delegates in supersede mode for the SAME incident, tagging the source package.
    assert captured['payload']['supersede'] is True
    assert captured['payload']['superseded_from'] == PKG_ID
    assert captured['payload']['incident_id'] == 'inc-1'
    # A NEW package id is returned; the original is referenced, never rewritten.
    assert result['package_id'] == 'pkg-regenerated-2'
    assert result['superseded_package_id'] == PKG_ID
    # The source row's stored evidence facts were never mutated by regeneration.
    assert conn.filter_patches == []


def test_regenerate_verified_package_denied(monkeypatch):
    # A package that already carries a retrievable manifest is not eligible for
    # regeneration — verified/usable evidence stays immutable and is not duplicated.
    manifest, _ = build_evidence_manifest(
        export_id=PKG_ID, export_type='proof_bundle', workspace_id=WS_ID,
        generated_at='2026-01-01T00:00:00Z', generated_by_user_id=USER_ID,
        source_resource_type='incident', source_resource_id='inc-1',
        storage_backend='local', file_values=_PARTIAL_FILES,
    )
    verified_filters = {
        **_persisted_ok(manifest),
        'verification': {'valid': True, 'verified_at': '2026-02-01T00:00:00Z'},
    }
    storage = _RWStorage({OBJECT_KEY: _bundle_bytes(_PARTIAL_FILES, include_manifest=True)})
    _bootstrap(monkeypatch, _RecoveryConn(filters=verified_filters), storage)
    monkeypatch.setattr(pilot, 'create_proof_bundle_export',
                        lambda *a, **k: pytest.fail('a usable package must never be regenerated'))
    with pytest.raises(pilot.HTTPException) as exc:
        pilot.regenerate_evidence_package(PKG_ID, _Req())
    assert exc.value.status_code == 409
    assert exc.value.detail['error'] == 'PACKAGE_NOT_ELIGIBLE'


def test_cross_workspace_regenerate_denied(monkeypatch):
    conn = _RecoveryConn(filters=dict(_PERSISTED_EV_2026_004), found=False)
    _bootstrap(monkeypatch, conn, _seed_missing_storage())
    monkeypatch.setattr(pilot, 'create_proof_bundle_export',
                        lambda *a, **k: pytest.fail('cross-workspace regeneration must not delegate'))
    with pytest.raises(pilot.HTTPException) as exc:
        pilot.regenerate_evidence_package(PKG_ID, _Req())
    assert exc.value.status_code == 404
    assert exc.value.detail['error'] == 'PACKAGE_NOT_FOUND'


def test_regenerate_rejects_non_completed_package(monkeypatch):
    conn = _RecoveryConn(filters=dict(_PERSISTED_EV_2026_004), status='building')
    _bootstrap(monkeypatch, conn, _seed_missing_storage())
    monkeypatch.setattr(pilot, 'create_proof_bundle_export',
                        lambda *a, **k: pytest.fail('a non-completed package must not regenerate'))
    with pytest.raises(pilot.HTTPException) as exc:
        pilot.regenerate_evidence_package(PKG_ID, _Req())
    assert exc.value.status_code == 409
    assert exc.value.detail['error'] == 'PACKAGE_NOT_READY'


# ── 20. create_proof_bundle_export supersede flag: new row, original preserved ──

class _CreateConn:
    """export_jobs connection for create_proof_bundle_export, recording whether a
    NEW row was inserted or an existing row was rewritten in place."""

    def __init__(self, existing_id=PKG_ID, existing_status='completed'):
        self._existing_id = existing_id
        self._existing_status = existing_status
        self.inserted_ids: list = []
        self.inserted_filters: list = []
        self.updated_ids: list = []

    def execute(self, statement, params=None):
        n = ' '.join(str(statement).split())
        if 'pg_advisory_xact_lock' in n:
            return _Result(None)
        if 'SELECT id, status, storage_object_key, package_number, filters FROM export_jobs' in n:
            return _Result({
                'id': self._existing_id, 'status': self._existing_status,
                'storage_object_key': OBJECT_KEY, 'package_number': 'EV-2026-004',
                'filters': {'incident_id': 'inc-1'},
            })
        if 'INSERT INTO export_jobs' in n:
            self.inserted_ids.append(params[0])
            self.inserted_filters.append(params[3])
            return _Result(None)
        if 'UPDATE export_jobs SET filters' in n and "status = 'queued'" in n:
            self.updated_ids.append(params[1])
            return _Result(None)
        if 'SELECT status, error_message, package_number FROM export_jobs' in n:
            return _Result({'status': 'completed', 'error_message': None, 'package_number': 'EV-2026-005'})
        return _Result(None)

    def commit(self):
        pass


def _bootstrap_create(monkeypatch, conn, storage):
    @contextmanager
    def _fake_pg():
        yield conn

    monkeypatch.setattr(pilot, 'require_live_mode', lambda *_a, **_k: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, '_require_workspace_permission',
                        lambda *_a, **_k: ({'id': USER_ID}, {'workspace_id': WS_ID}))
    monkeypatch.setattr(pilot, '_workspace_plan', lambda *_a, **_k: {'exports_enabled': True})
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: storage)
    monkeypatch.setattr(pilot, 'log_audit', lambda *a, **k: None)
    monkeypatch.setattr(pilot, '_generate_export_artifact', lambda *a, **k: {})
    # This connection double models only the export_jobs decision path, not the
    # incident's canonical source records, so stub the source fingerprint (its real
    # queries are exercised in test_evidence_source_fingerprint_screen9.py).
    monkeypatch.setattr(
        pilot, '_evidence_source_fingerprint',
        lambda *a, **k: {'fingerprint': 'sha256:stub-fingerprint', 'facts': {}},
    )


def test_supersede_flag_creates_new_row_preserving_original(monkeypatch):
    conn = _CreateConn(existing_id=PKG_ID)
    _bootstrap_create(monkeypatch, conn, _seed_missing_storage())

    result = pilot.create_proof_bundle_export(
        {'incident_id': 'inc-1', 'supersede': True, 'superseded_from': PKG_ID}, _Req())

    # A brand-new row was inserted; the original (Manifest-Missing) row was never
    # reused or rewritten — historical evidence is preserved.
    assert len(conn.inserted_ids) == 1
    assert conn.inserted_ids[0] != PKG_ID
    assert conn.updated_ids == []
    assert result['created'] is True
    assert result['package_id'] == conn.inserted_ids[0]
    # The new row records its regeneration lineage.
    new_filters = json.loads(conn.inserted_filters[0])
    assert new_filters['regenerated'] is True
    assert new_filters['superseded_from'] == PKG_ID


def test_create_without_supersede_reuses_existing(monkeypatch):
    # Contrast: a normal create reuses the completed+retrievable existing package
    # rather than minting a new row (the pre-existing idempotent behavior).
    conn = _CreateConn(existing_id=PKG_ID)
    _bootstrap_create(monkeypatch, conn, _seed_missing_storage())

    result = pilot.create_proof_bundle_export({'incident_id': 'inc-1'}, _Req())
    assert result['created'] is False
    assert conn.inserted_ids == []
    assert conn.updated_ids == []
