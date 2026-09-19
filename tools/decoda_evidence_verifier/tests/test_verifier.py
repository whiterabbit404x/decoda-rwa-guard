"""Offline verifier tests: the happy path, the vectors, and 16 ways to fail.

Every corruption case here asserts the verifier FAILS CLOSED — it never reports
``verified`` for a package it could not fully check, and it never reports
``failed`` for something it merely could not check.

The fixtures are built with the verifier's OWN primitives, so this suite needs
no Decoda application code. Cross-implementation agreement with the backend is
asserted separately, from the shared vectors, by
``services/api/tests/test_evidence_cross_implementation_vectors.py``.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import pathlib
import zipfile

import pytest

from decoda_evidence_verifier import cli
from decoda_evidence_verifier.verifier import (
    AUTHENTICITY_FAILED,
    AUTHENTICITY_UNAVAILABLE,
    AUTHENTICITY_VERIFIED,
    INTEGRITY_FAILED,
    INTEGRITY_VERIFIED,
    LINKAGE_PRESENT,
    PackageError,
    canonical_json,
    compute_merkle_root,
    leaf_hash,
    signing_payload,
    verify_package,
)

VECTORS = json.loads(
    (pathlib.Path(__file__).resolve().parents[1] / 'testvectors' / 'evidence-test-vectors.json')
    .read_text(encoding='utf-8')
)

ROOT = 'EV-2026-TEST'
KEY_ID = VECTORS['signature']['key_id']


def _test_private_key():
    from cryptography.hazmat.primitives.asymmetric import ed25519

    seed = hashlib.sha256(b'decoda-evidence-test-vector-seed-v1').digest()
    return ed25519.Ed25519PrivateKey.from_private_bytes(seed)


def _keyring_file(tmp_path: pathlib.Path, *, public_key: str | None = None, key_id: str = KEY_ID) -> str:
    path = tmp_path / 'keys.json'
    path.write_text(json.dumps({
        'schema_version': 1,
        'keys': [{
            'key_id': key_id,
            'algorithm': 'Ed25519',
            'public_key': public_key or VECTORS['signature']['public_key'],
            'status': 'active',
        }],
    }))
    return str(path)


def _build_manifest(file_bytes: dict[str, bytes]) -> dict:
    entries = [
        {
            'path': path,
            'sha256': hashlib.sha256(file_bytes[path]).hexdigest(),
            'size_bytes': len(file_bytes[path]),
            'media_type': 'application/json',
            'domain': 'PACKAGE',
            'source_record_type': 'evidence',
        }
        for path in sorted(file_bytes)
    ]
    manifest = {
        'manifest_version': '1.0',
        'schema_version': '2.0',
        'export_id': 'pkg-test-1',
        'export_type': 'proof_bundle',
        'workspace_id': 'ws-test-1',
        'generated_at': '2026-01-01T00:00:00+00:00',
        'generated_by_user_id': 'user-test-1',
        'source_resource_type': 'incident',
        'source_resource_id': 'inc-1',
        'storage_backend': 'local',
        'files': entries,
        'hash_algorithm': 'SHA-256',
        'artifact_count': len(entries),
        'merkle_root': compute_merkle_root([(e['path'], e['sha256']) for e in entries]),
        'merkle_scheme': 'decoda-merkle-v1',
        'previous_audit_anchor_hash': 'a' * 64,
    }
    manifest['manifest_sha256'] = hashlib.sha256(canonical_json(manifest)).hexdigest()
    return manifest


def _seal_for(manifest: dict, *, ed25519_signature: bool = True) -> dict:
    seal = {
        'signature_algorithm': 'HMAC-SHA256',
        'key_id': 'env-default',
        'key_version': 'env-current',
        'key_provider': 'env',
        'signed_manifest_sha256': manifest['manifest_sha256'],
        'signature': 'f' * 64,
        'signed_at': manifest['generated_at'],
    }
    if ed25519_signature:
        signature = _test_private_key().sign(signing_payload(manifest['manifest_sha256']))
        seal['schema_version'] = 2
        seal['signatures'] = [{
            'signature_format': 'decoda-evidence-signature-v1',
            'signature_format_version': 1,
            'algorithm': 'Ed25519',
            'key_id': KEY_ID,
            'signed_object': 'manifest_sha256',
            'signing_domain': 'DECODA-EVIDENCE-MANIFEST-V1',
            'signed_manifest_sha256': manifest['manifest_sha256'],
            'signature': base64.b64encode(signature).decode('ascii'),
            'public_key_verifiable': True,
        }]
    return seal


DEFAULT_FILES = {
    'alerts.json': canonical_json([{'id': 'alert-1', 'severity': 'high'}]),
    'audit_log.json': canonical_json([]),
    'incidents.json': canonical_json([{'id': 'inc-1', 'status': 'contained'}]),
}


def _write_package(
    path: pathlib.Path,
    *,
    file_bytes: dict[str, bytes] | None = None,
    manifest: dict | None = None,
    seal: dict | None = None,
    ed25519_signature: bool = True,
    extra_entries: dict[str, bytes] | None = None,
    drop_files: tuple[str, ...] = (),
    raw_names: dict[str, bytes] | None = None,
) -> str:
    file_bytes = dict(DEFAULT_FILES if file_bytes is None else file_bytes)
    manifest = manifest if manifest is not None else _build_manifest(file_bytes)
    seal = seal if seal is not None else _seal_for(manifest, ed25519_signature=ed25519_signature)

    entries: dict[str, bytes] = {
        f'{ROOT}/manifest.json': json.dumps(manifest, indent=2, sort_keys=True).encode('utf-8'),
        f'{ROOT}/seal.json': json.dumps(seal, indent=2, sort_keys=True).encode('utf-8'),
        f'{ROOT}/verification.json': b'{"status": "VERIFIED"}',
        f'{ROOT}/reports/investigation.md': b'# report\n',
        f'{ROOT}/verification/README.txt': b'how to verify\n',
    }
    for logical, payload in file_bytes.items():
        if logical in drop_files:
            continue
        entries[f'{ROOT}/artifacts/package/{logical}'] = payload
    entries.update(extra_entries or {})
    entries.update(raw_names or {})

    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in sorted(entries):
            info = zipfile.ZipInfo(filename=name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, entries[name])
    return str(path)


# ── Happy path ──────────────────────────────────────────────────────────────

def test_valid_package_verifies_integrity_and_authenticity(tmp_path):
    package = _write_package(tmp_path / 'ok.zip')
    report = verify_package(package, keyring_path=_keyring_file(tmp_path))
    assert report.integrity == INTEGRITY_VERIFIED
    assert report.authenticity == AUTHENTICITY_VERIFIED
    assert report.audit_linkage == LINKAGE_PRESENT
    assert (report.files_verified, report.files_total) == (3, 3)
    assert report.files_failed == report.files_missing == report.files_unexpected == 0
    assert report.manifest_hash_valid is True
    assert report.merkle_root_valid is True
    assert report.signature_key_id == KEY_ID
    assert report.key_source == 'keyring'
    assert report.errors == []
    assert report.ok is True


def test_verification_needs_no_network_database_or_decoda_secret(tmp_path, monkeypatch):
    """A verification run must not depend on any Decoda environment or socket."""
    import socket

    for name in ('EXPORT_SIGNING_SECRET', 'EVIDENCE_SIGNING_SECRET',
                 'EVIDENCE_SIGNING_ED25519_PRIVATE_KEY', 'DATABASE_URL', 'REDIS_URL'):
        monkeypatch.delenv(name, raising=False)

    def _no_network(*args, **kwargs):
        raise AssertionError('the offline verifier must never open a socket')

    monkeypatch.setattr(socket, 'socket', _no_network)
    monkeypatch.setattr(socket, 'create_connection', _no_network)

    package = _write_package(tmp_path / 'offline.zip')
    report = verify_package(package, keyring_path=_keyring_file(tmp_path))
    assert report.ok is True


def test_verifier_imports_no_decoda_application_module():
    """The verifier must not reach into the app — that is the whole point."""
    import decoda_evidence_verifier.cli as cli_module
    import decoda_evidence_verifier.verifier as verifier_module

    for module in (verifier_module, cli_module):
        source = pathlib.Path(module.__file__).read_text(encoding='utf-8')
        for forbidden in ('services.api', 'fastapi', 'psycopg', 'redis', 'boto3', 'sqlalchemy'):
            assert f'import {forbidden}' not in source
            assert f'from {forbidden}' not in source


def test_verifier_exposes_no_signing_capability():
    """A verifier compromise must not enable forgery."""
    source = pathlib.Path(
        __import__('decoda_evidence_verifier.verifier', fromlist=['x']).__file__
    ).read_text(encoding='utf-8')
    assert 'Ed25519PrivateKey' not in source
    assert 'private_bytes' not in source
    assert '.sign(' not in source


# ── Cross-implementation vectors ────────────────────────────────────────────

def test_canonical_json_matches_vectors():
    for case in VECTORS['canonical_json']['cases']:
        assert canonical_json(case['value']).decode('utf-8') == case['bytes_utf8']
        assert hashlib.sha256(canonical_json(case['value'])).hexdigest() == case['sha256']


def test_file_digests_and_merkle_match_vectors():
    for entry in VECTORS['file_entries']:
        payload = canonical_json(VECTORS['file_values'][entry['path']])
        assert hashlib.sha256(payload).hexdigest() == entry['sha256']
        assert len(payload) == entry['size_bytes']
    for leaf in VECTORS['merkle']['leaves']:
        assert leaf_hash(leaf['path'], leaf['sha256']).hex() == leaf['leaf_hash']
    assert compute_merkle_root(
        [(e['path'], e['sha256']) for e in VECTORS['file_entries']]
    ) == VECTORS['merkle']['root']
    for case in VECTORS['merkle']['extra_cases']:
        assert compute_merkle_root([tuple(pair) for pair in case['entries']]) == case['root']


def test_manifest_hash_and_signed_payload_match_vectors():
    manifest = {k: v for k, v in VECTORS['manifest'].items() if k != 'manifest_sha256'}
    assert canonical_json(manifest).decode('utf-8') == VECTORS['manifest_canonical_utf8']
    assert hashlib.sha256(canonical_json(manifest)).hexdigest() == VECTORS['manifest_sha256']
    assert signing_payload(VECTORS['manifest_sha256']).hex() == VECTORS['signature']['signed_payload_hex']


# ── Negative tests 1-16: every one must fail closed ─────────────────────────

def test_1_one_file_byte_changed(tmp_path):
    tampered = dict(DEFAULT_FILES)
    tampered['alerts.json'] = canonical_json([{'id': 'alert-1', 'severity': 'low'}])
    manifest = _build_manifest(DEFAULT_FILES)
    package = _write_package(tmp_path / 'p.zip', file_bytes=tampered, manifest=manifest)
    report = verify_package(package, keyring_path=_keyring_file(tmp_path))
    assert report.integrity == INTEGRITY_FAILED
    assert report.files_failed == 1
    assert report.failed_paths == ['alerts.json']
    # The signature still verifies: it covers the MANIFEST, and the manifest is
    # unchanged. The package is still rejected, because integrity failed.
    assert report.authenticity == AUTHENTICITY_VERIFIED
    assert report.ok is False


def test_2_file_removed(tmp_path):
    package = _write_package(tmp_path / 'p.zip', drop_files=('incidents.json',))
    report = verify_package(package, keyring_path=_keyring_file(tmp_path))
    assert report.integrity == INTEGRITY_FAILED
    assert report.files_missing == 1
    assert report.missing_paths == ['incidents.json']


def test_3_unexpected_file_added(tmp_path):
    package = _write_package(
        tmp_path / 'p.zip',
        extra_entries={f'{ROOT}/artifacts/package/smuggled.json': b'{"planted":true}'},
    )
    report = verify_package(package, keyring_path=_keyring_file(tmp_path))
    assert report.integrity == INTEGRITY_FAILED
    assert report.files_unexpected == 1
    assert report.unexpected_paths == ['package/smuggled.json']


def test_4_manifest_body_changed(tmp_path):
    manifest = _build_manifest(DEFAULT_FILES)
    manifest['workspace_id'] = 'ws-attacker'
    package = _write_package(tmp_path / 'p.zip', manifest=manifest)
    report = verify_package(package, keyring_path=_keyring_file(tmp_path))
    assert report.integrity == INTEGRITY_FAILED
    assert report.manifest_hash_valid is False


def test_5_manifest_hash_changed(tmp_path):
    manifest = _build_manifest(DEFAULT_FILES)
    manifest['manifest_sha256'] = 'b' * 64
    package = _write_package(tmp_path / 'p.zip', manifest=manifest)
    report = verify_package(package, keyring_path=_keyring_file(tmp_path))
    assert report.integrity == INTEGRITY_FAILED
    assert report.manifest_hash_valid is False


def test_6_merkle_root_changed(tmp_path):
    manifest = _build_manifest(DEFAULT_FILES)
    manifest['merkle_root'] = 'c' * 64
    manifest['manifest_sha256'] = hashlib.sha256(
        canonical_json({k: v for k, v in manifest.items() if k != 'manifest_sha256'})
    ).hexdigest()
    package = _write_package(tmp_path / 'p.zip', manifest=manifest)
    report = verify_package(package, keyring_path=_keyring_file(tmp_path))
    assert report.integrity == INTEGRITY_FAILED
    assert report.merkle_root_valid is False
    # The manifest re-hashes cleanly, so only the Merkle commitment catches this.
    assert report.manifest_hash_valid is True


def test_7_signature_byte_changed(tmp_path):
    manifest = _build_manifest(DEFAULT_FILES)
    seal = _seal_for(manifest)
    raw = bytearray(base64.b64decode(seal['signatures'][0]['signature']))
    raw[0] ^= 0x01
    seal['signatures'][0]['signature'] = base64.b64encode(bytes(raw)).decode('ascii')
    package = _write_package(tmp_path / 'p.zip', manifest=manifest, seal=seal)
    report = verify_package(package, keyring_path=_keyring_file(tmp_path))
    assert report.authenticity == AUTHENTICITY_FAILED
    assert report.integrity == INTEGRITY_VERIFIED
    assert report.ok is False


def test_8_wrong_public_key(tmp_path):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    other = ed25519.Ed25519PrivateKey.from_private_bytes(b'\x11' * 32).public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw,
    )
    package = _write_package(tmp_path / 'p.zip')
    report = verify_package(
        package, keyring_path=_keyring_file(tmp_path, public_key=base64.b64encode(other).decode()),
    )
    assert report.authenticity == AUTHENTICITY_FAILED


def test_9_unknown_key_id(tmp_path):
    package = _write_package(tmp_path / 'p.zip')
    report = verify_package(package, keyring_path=_keyring_file(tmp_path, key_id='some-other-key'))
    # NOT "failed": an unknown key means we could not check, not that it is bad.
    assert report.authenticity == AUTHENTICITY_UNAVAILABLE
    assert 'not in the supplied keyring' in (report.authenticity_reason or '')


def test_10_duplicate_zip_filename(tmp_path):
    package = tmp_path / 'p.zip'
    _write_package(package)
    with zipfile.ZipFile(package, 'a', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f'{ROOT}/artifacts/package/alerts.json', b'{"second":true}')
    with pytest.raises(PackageError, match='duplicate entry names'):
        verify_package(str(package), keyring_path=_keyring_file(tmp_path))


@pytest.mark.parametrize('hostile', ['../../etc/passwd', '/etc/passwd', 'a/../../b.json'])
def test_11_traversal_path_rejected(tmp_path, hostile):
    package = _write_package(tmp_path / 'p.zip', raw_names={hostile: b'{}'})
    with pytest.raises(PackageError, match='unsafe archive entry'):
        verify_package(package, keyring_path=_keyring_file(tmp_path))


def test_12_malformed_zip(tmp_path):
    package = tmp_path / 'broken.zip'
    package.write_bytes(b'this is definitely not a zip archive')
    with pytest.raises(PackageError, match='not a readable ZIP archive'):
        verify_package(str(package))


def test_13_truncated_package(tmp_path):
    package = tmp_path / 'p.zip'
    _write_package(package)
    data = package.read_bytes()
    package.write_bytes(data[: len(data) // 2])
    with pytest.raises(PackageError):
        verify_package(str(package), keyring_path=_keyring_file(tmp_path))


def test_14_bad_base64_signature(tmp_path):
    manifest = _build_manifest(DEFAULT_FILES)
    seal = _seal_for(manifest)
    seal['signatures'][0]['signature'] = 'not-valid-base64!!!!'
    package = _write_package(tmp_path / 'p.zip', manifest=manifest, seal=seal)
    report = verify_package(package, keyring_path=_keyring_file(tmp_path))
    assert report.authenticity == AUTHENTICITY_FAILED


def test_15_unsupported_algorithm(tmp_path):
    manifest = _build_manifest(DEFAULT_FILES)
    seal = _seal_for(manifest)
    seal['signatures'][0]['algorithm'] = 'RSA-PSS-SHA256'
    package = _write_package(tmp_path / 'p.zip', manifest=manifest, seal=seal)
    report = verify_package(package, keyring_path=_keyring_file(tmp_path))
    assert report.authenticity == AUTHENTICITY_UNAVAILABLE
    assert 'Unsupported signature algorithm' in (report.authenticity_reason or '')


def test_16_unsupported_schema_version(tmp_path):
    package = _write_package(tmp_path / 'p.zip')
    keyring = tmp_path / 'future-keys.json'
    keyring.write_text(json.dumps({'schema_version': 99, 'keys': []}))
    with pytest.raises(PackageError, match='unsupported keyring schema_version'):
        verify_package(package, keyring_path=str(keyring))


# ── Additional hardening ────────────────────────────────────────────────────

def test_duplicate_manifest_entry_rejected(tmp_path):
    manifest = _build_manifest(DEFAULT_FILES)
    manifest['files'] = manifest['files'] + [copy.deepcopy(manifest['files'][0])]
    package = _write_package(tmp_path / 'p.zip', manifest=manifest)
    with pytest.raises(PackageError, match='duplicate artifact path'):
        verify_package(package, keyring_path=_keyring_file(tmp_path))


def test_duplicate_json_key_in_manifest_rejected(tmp_path):
    package = tmp_path / 'p.zip'
    _write_package(package)
    manifest = json.loads(zipfile.ZipFile(package).read(f'{ROOT}/manifest.json'))
    raw = json.dumps(manifest)
    hostile = raw[:-1] + ', "merkle_root": "' + 'd' * 64 + '"}'
    rebuilt = tmp_path / 'dupkey.zip'
    with zipfile.ZipFile(package) as src, zipfile.ZipFile(rebuilt, 'w') as dst:
        for name in src.namelist():
            dst.writestr(name, hostile.encode() if name.endswith('manifest.json') else src.read(name))
    with pytest.raises(PackageError, match='duplicate JSON key'):
        verify_package(str(rebuilt), keyring_path=_keyring_file(tmp_path))


def test_size_mismatch_is_caught_even_when_digest_field_matches(tmp_path):
    manifest = _build_manifest(DEFAULT_FILES)
    manifest['files'][0]['size_bytes'] = 99999
    manifest['manifest_sha256'] = hashlib.sha256(
        canonical_json({k: v for k, v in manifest.items() if k != 'manifest_sha256'})
    ).hexdigest()
    package = _write_package(tmp_path / 'p.zip', manifest=manifest)
    report = verify_package(package, keyring_path=_keyring_file(tmp_path))
    assert report.integrity == INTEGRITY_FAILED
    assert any('size mismatch' in error for error in report.errors)


def test_symlink_entry_rejected(tmp_path):
    package = tmp_path / 'p.zip'
    _write_package(package)
    with zipfile.ZipFile(package, 'a') as archive:
        info = zipfile.ZipInfo(f'{ROOT}/artifacts/package/link.json')
        info.external_attr = (0xA1FF << 16)
        archive.writestr(info, '/etc/passwd')
    with pytest.raises(PackageError, match='symbolic link'):
        verify_package(str(package), keyring_path=_keyring_file(tmp_path))


def test_decompression_bomb_rejected(tmp_path):
    package = tmp_path / 'bomb.zip'
    _write_package(package)
    with zipfile.ZipFile(package, 'a', compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f'{ROOT}/artifacts/package/bomb.json', b'\x00' * (200 * 1024 * 1024))
    with pytest.raises(PackageError):
        verify_package(str(package), keyring_path=_keyring_file(tmp_path))


def test_package_without_manifest_is_unusable(tmp_path):
    package = tmp_path / 'p.zip'
    with zipfile.ZipFile(package, 'w') as archive:
        archive.writestr('something.txt', b'hello')
    with pytest.raises(PackageError, match='does not contain a manifest.json'):
        verify_package(str(package))


# ── Legacy (HMAC-only) packages ─────────────────────────────────────────────

def test_legacy_hmac_package_is_integrity_verified_but_not_authentic(tmp_path):
    package = _write_package(tmp_path / 'legacy.zip', ed25519_signature=False)
    report = verify_package(package, keyring_path=_keyring_file(tmp_path))
    assert report.integrity == INTEGRITY_VERIFIED
    assert report.authenticity == AUTHENTICITY_UNAVAILABLE
    assert report.authenticity != AUTHENTICITY_VERIFIED
    assert report.signature_algorithm == 'HMAC-SHA256'
    assert 'forge' in (report.authenticity_reason or '')
    assert report.ok is False


def test_legacy_schema_1_manifest_without_merkle_still_checks_hashes(tmp_path):
    manifest = _build_manifest(DEFAULT_FILES)
    for key in ('schema_version', 'merkle_root', 'merkle_scheme', 'hash_algorithm', 'artifact_count'):
        manifest.pop(key, None)
    for entry in manifest['files']:
        for key in ('media_type', 'domain', 'source_record_type'):
            entry.pop(key, None)
    manifest['manifest_sha256'] = hashlib.sha256(
        canonical_json({k: v for k, v in manifest.items() if k != 'manifest_sha256'})
    ).hexdigest()
    package = _write_package(tmp_path / 'legacy1.zip', manifest=manifest, ed25519_signature=False)
    report = verify_package(package, keyring_path=_keyring_file(tmp_path))
    assert report.integrity == INTEGRITY_VERIFIED
    assert report.merkle_root_valid is None  # never sealed; not a pass and not a failure
    assert report.authenticity == AUTHENTICITY_UNAVAILABLE


# ── Key sources and rotation ────────────────────────────────────────────────

def test_no_keyring_means_authenticity_unavailable_not_verified(tmp_path):
    package = _write_package(tmp_path / 'p.zip')
    report = verify_package(package)
    assert report.integrity == INTEGRITY_VERIFIED
    assert report.authenticity == AUTHENTICITY_UNAVAILABLE
    assert report.key_source == 'none'


def test_bundled_keyring_is_not_trusted_by_default(tmp_path):
    package = _write_package(
        tmp_path / 'p.zip',
        extra_entries={
            f'{ROOT}/verification/decoda-evidence-keys.json': json.dumps({
                'schema_version': 1,
                'keys': [{'key_id': KEY_ID, 'algorithm': 'Ed25519',
                          'public_key': VECTORS['signature']['public_key'], 'status': 'active'}],
            }).encode(),
        },
    )
    assert verify_package(package).authenticity == AUTHENTICITY_UNAVAILABLE
    opted_in = verify_package(package, allow_bundled_keyring=True)
    assert opted_in.authenticity == AUTHENTICITY_VERIFIED
    # The source is always reported, so an auditor sees which trust they used.
    assert opted_in.key_source == 'bundled'


def test_retired_key_still_verifies_historical_evidence(tmp_path):
    package = _write_package(tmp_path / 'p.zip')
    keyring = tmp_path / 'rotated.json'
    keyring.write_text(json.dumps({
        'schema_version': 1,
        'keys': [
            {'key_id': 'decoda-evidence-2027-06', 'algorithm': 'Ed25519',
             'public_key': base64.b64encode(b'\x02' * 32).decode(), 'status': 'active'},
            {'key_id': KEY_ID, 'algorithm': 'Ed25519',
             'public_key': VECTORS['signature']['public_key'], 'status': 'retired'},
        ],
    }))
    report = verify_package(package, keyring_path=str(keyring))
    assert report.authenticity == AUTHENTICITY_VERIFIED
    assert report.signature_key_id == KEY_ID


def test_single_public_key_argument(tmp_path):
    package = _write_package(tmp_path / 'p.zip')
    report = verify_package(
        package, public_key=VECTORS['signature']['public_key'], public_key_id=KEY_ID,
    )
    assert report.authenticity == AUTHENTICITY_VERIFIED
    assert report.key_source == 'public-key'


# ── CLI contract ────────────────────────────────────────────────────────────

def test_cli_exit_codes_and_json(tmp_path, capsys):
    good = _write_package(tmp_path / 'good.zip')
    keyring = _keyring_file(tmp_path)

    assert cli.main(['verify', good, '--keyring', keyring]) == cli.EXIT_OK
    assert 'VERIFIED' in capsys.readouterr().out

    assert cli.main(['verify', good, '--json']) == cli.EXIT_NOT_INDEPENDENTLY_VERIFIABLE
    payload = json.loads(capsys.readouterr().out)
    assert payload['integrity'] == 'verified'
    assert payload['authenticity'] == 'unavailable'
    assert payload['files'] == {
        'total': 3, 'verified': 3, 'failed': 0, 'missing': 0, 'unexpected': 0,
        'failed_paths': [], 'missing_paths': [], 'unexpected_paths': [],
    }

    assert cli.main(['verify', good, '--integrity-only']) == cli.EXIT_OK
    capsys.readouterr()

    tampered = _write_package(tmp_path / 'bad.zip', drop_files=('alerts.json',))
    assert cli.main(['verify', tampered, '--keyring', keyring]) == cli.EXIT_FAILED
    capsys.readouterr()

    assert cli.main(['verify', str(tmp_path / 'missing.zip')]) == cli.EXIT_UNUSABLE
    capsys.readouterr()

    assert cli.main(['inspect', good, '--keyring', keyring]) == cli.EXIT_OK
    assert 'Decoda Evidence Verifier' in capsys.readouterr().out


def test_cli_output_never_prints_evidence_content(tmp_path, capsys):
    secret = 'SUPER-SECRET-WALLET-0xdeadbeef'
    files = {'alerts.json': canonical_json([{'id': 'alert-1', 'wallet': secret}])}
    tampered = dict(files)
    tampered['alerts.json'] = canonical_json([{'id': 'alert-1', 'wallet': secret + 'X'}])
    package = _write_package(
        tmp_path / 'p.zip', file_bytes=tampered, manifest=_build_manifest(files),
    )
    cli.main(['verify', package, '--keyring', _keyring_file(tmp_path)])
    output = capsys.readouterr()
    assert secret not in output.out
    assert secret not in output.err
