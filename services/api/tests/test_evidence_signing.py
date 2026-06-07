"""Tests for evidence_signing module: manifest building, HMAC sealing, verification, audit chaining."""
from __future__ import annotations

import hashlib
import hmac
import json
import pytest

from services.api.app.evidence_signing import (
    build_evidence_manifest,
    canonical_json,
    compute_audit_row_hash,
    seal_manifest,
    signing_available,
    verify_audit_chain,
    verify_bundle,
)

_GENERATED_AT = '2026-01-01T00:00:00+00:00'
_EXPORT_ID = 'test-export-id-001'
_WORKSPACE_ID = 'ws-test-001'


def _sample_file_values() -> dict:
    return {
        'summary.json': {'export_id': _EXPORT_ID, 'status': 'complete'},
        'alerts.json': [{'id': 'alert-1', 'severity': 'high'}],
        'incidents.json': [{'id': 'inc-1', 'title': 'Test Incident'}],
    }


def _build_manifest_and_files(**kwargs):
    defaults = dict(
        export_id=_EXPORT_ID,
        export_type='proof_bundle',
        workspace_id=_WORKSPACE_ID,
        generated_at=_GENERATED_AT,
        generated_by_user_id='user-1',
        source_resource_type='incident',
        source_resource_id='inc-1',
        storage_backend='local',
        file_values=_sample_file_values(),
    )
    defaults.update(kwargs)
    return build_evidence_manifest(**defaults)


# ── canonical_json ──────────────────────────────────────────────────

def test_canonical_json_is_deterministic():
    obj = {'z': 3, 'a': 1, 'b': [2, 1, 3], 'm': {'x': 'y', 'n': 'o'}}
    b1 = canonical_json(obj)
    b2 = canonical_json(obj)
    assert b1 == b2


def test_canonical_json_sorts_keys():
    b = canonical_json({'z': 1, 'a': 2}).decode('utf-8')
    assert b.index('"a"') < b.index('"z"')


def test_canonical_json_compact_separators():
    b = canonical_json({'a': 1, 'b': 2}).decode('utf-8')
    assert ' ' not in b, 'canonical JSON must have no spaces'


# ── build_evidence_manifest ─────────────────────────────────────────

def test_manifest_includes_all_required_fields():
    manifest, _ = _build_manifest_and_files()
    required = {
        'manifest_version', 'export_id', 'export_type', 'workspace_id',
        'generated_at', 'generated_by_user_id', 'source_resource_type',
        'source_resource_id', 'storage_backend', 'files', 'manifest_sha256',
    }
    for f in required:
        assert f in manifest, f'manifest missing required field: {f}'


def test_manifest_file_list_has_correct_hashes():
    file_vals = _sample_file_values()
    manifest, file_bytes_map = build_evidence_manifest(
        export_id=_EXPORT_ID,
        export_type='proof_bundle',
        workspace_id=_WORKSPACE_ID,
        generated_at=_GENERATED_AT,
        generated_by_user_id='user-1',
        source_resource_type='incident',
        source_resource_id='inc-1',
        storage_backend='local',
        file_values=file_vals,
    )
    for entry in manifest['files']:
        path = entry['path']
        expected_sha256 = entry['sha256']
        assert path in file_bytes_map
        actual_sha256 = hashlib.sha256(file_bytes_map[path]).hexdigest()
        assert actual_sha256 == expected_sha256, f'hash mismatch for {path}'


def test_manifest_file_list_is_sorted():
    file_vals = {'z_file.json': {}, 'a_file.json': {}, 'm_file.json': {}}
    manifest, _ = build_evidence_manifest(
        export_id=_EXPORT_ID, export_type='proof_bundle', workspace_id=_WORKSPACE_ID,
        generated_at=_GENERATED_AT, generated_by_user_id=None, source_resource_type='incident',
        source_resource_id='inc-1', storage_backend='local', file_values=file_vals,
    )
    paths = [e['path'] for e in manifest['files']]
    assert paths == sorted(paths), 'manifest files must be in sorted order'


def test_manifest_sha256_is_stable():
    manifest1, _ = _build_manifest_and_files()
    manifest2, _ = _build_manifest_and_files()
    # Same inputs → same manifest_sha256 (the sha256 field itself doesn't regenerate timestamps)
    assert manifest1['manifest_sha256'] == manifest2['manifest_sha256']


def test_manifest_sha256_changes_with_file_change():
    manifest1, _ = _build_manifest_and_files(file_values={'a.json': {'x': 1}})
    manifest2, _ = _build_manifest_and_files(file_values={'a.json': {'x': 2}})
    assert manifest1['manifest_sha256'] != manifest2['manifest_sha256']


def test_manifest_includes_previous_audit_anchor_when_provided():
    anchor = 'abc123deadbeef'
    manifest, _ = _build_manifest_and_files(previous_audit_anchor_hash=anchor)
    assert manifest.get('previous_audit_anchor_hash') == anchor


def test_manifest_workspace_id_is_correct():
    manifest, _ = _build_manifest_and_files(workspace_id='ws-specific-999')
    assert manifest['workspace_id'] == 'ws-specific-999'


# ── seal_manifest ───────────────────────────────────────────────────

def test_seal_has_required_fields(monkeypatch):
    monkeypatch.setenv('APP_MODE', 'local')
    monkeypatch.delenv('EXPORT_SIGNING_SECRET', raising=False)
    monkeypatch.delenv('EVIDENCE_SIGNING_SECRET', raising=False)
    manifest, _ = _build_manifest_and_files()
    seal = seal_manifest(manifest)
    assert seal['signature_algorithm'] == 'HMAC-SHA256'
    assert seal['key_id']
    assert seal['signature']
    assert len(seal['signature']) == 64


def test_seal_dev_mode_includes_warning(monkeypatch):
    monkeypatch.setenv('APP_MODE', 'local')
    monkeypatch.delenv('EXPORT_SIGNING_SECRET', raising=False)
    monkeypatch.delenv('EVIDENCE_SIGNING_SECRET', raising=False)
    manifest, _ = _build_manifest_and_files()
    seal = seal_manifest(manifest)
    assert 'warning' in seal
    assert 'DEV_MODE' in seal['warning']


def test_seal_production_strict_mode_rejects_static_environment_secret(monkeypatch):
    monkeypatch.setenv('APP_MODE', 'production')
    monkeypatch.setenv('MANAGED_KEY_PROVIDER', 'env')
    monkeypatch.setenv('MANAGED_KEY_ENFORCEMENT', 'strict')
    monkeypatch.setenv('EXPORT_SIGNING_SECRET', 'production-signing-secret-32bytesXX')
    manifest, _ = _build_manifest_and_files()
    with pytest.raises(RuntimeError, match='MANAGED_KEY_ENFORCEMENT=strict'):
        seal_manifest(manifest)


def test_seal_production_without_secret_raises(monkeypatch):
    monkeypatch.setenv('APP_MODE', 'production')
    monkeypatch.delenv('EXPORT_SIGNING_SECRET', raising=False)
    monkeypatch.delenv('EVIDENCE_SIGNING_SECRET', raising=False)
    manifest, _ = _build_manifest_and_files()
    with pytest.raises(RuntimeError, match='EXPORT_SIGNING_SECRET'):
        seal_manifest(manifest)
    monkeypatch.setenv('APP_MODE', 'local')


def test_seal_does_not_contain_raw_secret(monkeypatch):
    monkeypatch.setenv('EXPORT_SIGNING_SECRET', 'my-super-secret-key')
    manifest, _ = _build_manifest_and_files()
    seal = seal_manifest(manifest)
    assert 'my-super-secret-key' not in json.dumps(seal)
    monkeypatch.delenv('EXPORT_SIGNING_SECRET', raising=False)


def test_seal_signing_key_id_from_env(monkeypatch):
    monkeypatch.setenv('EXPORT_SIGNING_KEY_ID', 'rotation-key-v2')
    monkeypatch.setenv('APP_MODE', 'local')
    manifest, _ = _build_manifest_and_files()
    seal = seal_manifest(manifest)
    assert seal['key_id'] == 'rotation-key-v2'
    monkeypatch.delenv('EXPORT_SIGNING_KEY_ID', raising=False)


# ── verify_bundle ───────────────────────────────────────────────────

def _make_signed_bundle(secret: bytes = b'test-secret'):
    file_vals = _sample_file_values()
    manifest, file_bytes_map = build_evidence_manifest(
        export_id=_EXPORT_ID, export_type='proof_bundle', workspace_id=_WORKSPACE_ID,
        generated_at=_GENERATED_AT, generated_by_user_id='user-1', source_resource_type='incident',
        source_resource_id='inc-1', storage_backend='local', file_values=file_vals,
    )
    # Compute seal manually with the given secret
    canonical = canonical_json(manifest)
    sig = hmac.new(secret, canonical, 'sha256').hexdigest()
    seal = {'signature_algorithm': 'HMAC-SHA256', 'key_id': 'test', 'signature': sig, 'signed_at': _GENERATED_AT}
    return file_vals, manifest, seal


def test_verify_bundle_clean_passes():
    secret = b'test-secret'
    file_vals, manifest, seal = _make_signed_bundle(secret)
    result = verify_bundle(file_vals, manifest, seal, signing_secret=secret)
    assert result['valid'], f'Clean bundle must verify: {result["errors"]}'
    assert result['errors'] == []


def test_verify_bundle_tampered_file_fails():
    secret = b'test-secret'
    file_vals, manifest, seal = _make_signed_bundle(secret)
    tampered = dict(file_vals)
    tampered['alerts.json'] = [{'id': 'HACKED', 'injected': True}]
    result = verify_bundle(tampered, manifest, seal, signing_secret=secret)
    assert not result['valid']
    assert any('tampered' in e for e in result['errors'])


def test_verify_bundle_missing_file_fails():
    secret = b'test-secret'
    file_vals, manifest, seal = _make_signed_bundle(secret)
    incomplete = {k: v for k, v in file_vals.items() if k != 'alerts.json'}
    result = verify_bundle(incomplete, manifest, seal, signing_secret=secret)
    assert not result['valid']
    assert any('missing' in e for e in result['errors'])


def test_verify_bundle_tampered_manifest_hash_fails():
    secret = b'test-secret'
    file_vals, manifest, seal = _make_signed_bundle(secret)
    tampered_manifest = dict(manifest)
    tampered_manifest['manifest_sha256'] = 'aaaa' + 'b' * 60
    result = verify_bundle(file_vals, tampered_manifest, seal, signing_secret=secret)
    assert not result['valid']
    assert 'manifest_hash_mismatch' in result['errors']


def test_verify_bundle_wrong_secret_fails():
    secret = b'correct-secret'
    file_vals, manifest, seal = _make_signed_bundle(secret)
    result = verify_bundle(file_vals, manifest, seal, signing_secret=b'wrong-secret')
    assert not result['valid']
    assert 'hmac_signature_invalid' in result['errors']


def test_verify_bundle_no_secret_available_fails(monkeypatch):
    monkeypatch.delenv('EXPORT_SIGNING_SECRET', raising=False)
    monkeypatch.delenv('EVIDENCE_SIGNING_SECRET', raising=False)
    file_vals, manifest, seal = _make_signed_bundle()
    result = verify_bundle(file_vals, manifest, seal, signing_secret=None)
    assert not result['valid']
    assert 'signing_secret_not_available' in result['errors']


# ── compute_audit_row_hash ──────────────────────────────────────────

def test_audit_row_hash_is_deterministic():
    h1 = compute_audit_row_hash(
        row_id='r1', workspace_id='ws-1', user_id='u1', action='export.generate',
        entity_type='export_job', entity_id='e1', created_at_iso='2026-01-01T00:00:00+00:00',
        metadata_sha256='abc', previous_row_hash=None,
    )
    h2 = compute_audit_row_hash(
        row_id='r1', workspace_id='ws-1', user_id='u1', action='export.generate',
        entity_type='export_job', entity_id='e1', created_at_iso='2026-01-01T00:00:00+00:00',
        metadata_sha256='abc', previous_row_hash=None,
    )
    assert h1 == h2


def test_audit_row_hash_changes_with_field_change():
    base_args = dict(
        row_id='r1', workspace_id='ws-1', user_id='u1', action='export.generate',
        entity_type='export_job', entity_id='e1', created_at_iso='2026-01-01T00:00:00+00:00',
        metadata_sha256='abc', previous_row_hash=None,
    )
    h1 = compute_audit_row_hash(**base_args)
    h2 = compute_audit_row_hash(**{**base_args, 'action': 'user.signin'})
    assert h1 != h2


def test_audit_row_hash_chains_previous_hash():
    h_first = compute_audit_row_hash(
        row_id='r1', workspace_id='ws-1', user_id='u1', action='user.signin',
        entity_type='user', entity_id='u1', created_at_iso='2026-01-01T00:00:00+00:00',
        metadata_sha256='aaa', previous_row_hash=None,
    )
    h_second = compute_audit_row_hash(
        row_id='r2', workspace_id='ws-1', user_id='u1', action='export.generate',
        entity_type='export_job', entity_id='e1', created_at_iso='2026-01-01T00:01:00+00:00',
        metadata_sha256='bbb', previous_row_hash=h_first,
    )
    assert h_first != h_second


# ── verify_audit_chain ──────────────────────────────────────────────

def _build_chain(n: int) -> list[dict]:
    rows = []
    prev_hash = None
    for i in range(n):
        row_id = f'row-{i}'
        metadata = {'step': i}
        metadata_sha256 = hashlib.sha256(canonical_json(metadata)).hexdigest()
        created_at_iso = f'2026-01-0{i+1}T00:00:00+00:00'
        row_hash = compute_audit_row_hash(
            row_id=row_id, workspace_id='ws-chain', user_id='u1',
            action='test.action', entity_type='test', entity_id='e1',
            created_at_iso=created_at_iso, metadata_sha256=metadata_sha256,
            previous_row_hash=prev_hash,
        )
        rows.append({
            'id': row_id, 'workspace_id': 'ws-chain', 'user_id': 'u1',
            'action': 'test.action', 'entity_type': 'test', 'entity_id': 'e1',
            'created_at': created_at_iso, 'metadata': metadata,
            'row_hash': row_hash, 'previous_row_hash': prev_hash,
        })
        prev_hash = row_hash
    return rows


def test_verify_audit_chain_valid():
    rows = _build_chain(5)
    result = verify_audit_chain(rows)
    assert result['valid'], f'Valid chain should pass: {result["errors"]}'
    assert result['chain_length'] == 5


def test_verify_audit_chain_detects_modified_row():
    rows = _build_chain(5)
    rows[2] = {**rows[2], 'action': 'TAMPERED_ACTION'}  # modify action after hash was computed
    result = verify_audit_chain(rows)
    assert not result['valid']
    assert any('mismatch' in e for e in result['errors'])


def test_verify_audit_chain_detects_chain_break():
    rows = _build_chain(5)
    # Break the chain by changing previous_row_hash on row 3
    rows[3] = {**rows[3], 'previous_row_hash': 'wrong-hash'}
    result = verify_audit_chain(rows)
    assert not result['valid']
    assert any('chain_break' in e for e in result['errors'])


def test_verify_audit_chain_empty_is_valid():
    result = verify_audit_chain([])
    assert result['valid']
    assert result['chain_length'] == 0


def test_verify_audit_chain_single_row():
    rows = _build_chain(1)
    result = verify_audit_chain(rows)
    assert result['valid']
    assert result['chain_length'] == 1
