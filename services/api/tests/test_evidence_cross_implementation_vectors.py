"""The backend must agree, byte for byte, with the standalone offline verifier.

Both sides assert against ONE committed vector file
(``tools/decoda_evidence_verifier/testvectors/evidence-test-vectors.json``).
Neither side generates it from the other, so this is a real cross-implementation
check rather than a tautology: if the backend's canonical JSON, artifact hashing,
manifest hashing, Merkle construction or signing payload ever drifts from the
published contract, already-exported evidence silently stops verifying in the
field — and this suite fails first.
"""
from __future__ import annotations

import base64
import hashlib
import json
import pathlib

import pytest

from services.api.app import evidence_ed25519, evidence_merkle
from services.api.app.evidence_signing import build_evidence_manifest, canonical_json

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
VECTOR_PATH = REPO_ROOT / 'tools' / 'decoda_evidence_verifier' / 'testvectors' / 'evidence-test-vectors.json'
VECTORS = json.loads(VECTOR_PATH.read_text(encoding='utf-8'))

TEST_SEED = hashlib.sha256(b'decoda-evidence-test-vector-seed-v1').digest()


def test_vector_file_is_present_and_versioned():
    assert VECTOR_PATH.is_file()
    assert VECTORS['schema_version'] == 1


def test_backend_canonical_json_matches_vectors():
    """``ensure_ascii=True``, sorted keys, compact separators — pinned, not assumed."""
    for case in VECTORS['canonical_json']['cases']:
        produced = canonical_json(case['value'])
        assert produced.decode('utf-8') == case['bytes_utf8']
        assert hashlib.sha256(produced).hexdigest() == case['sha256']


def test_backend_file_digests_match_vectors():
    for entry in VECTORS['file_entries']:
        payload = canonical_json(VECTORS['file_values'][entry['path']])
        assert hashlib.sha256(payload).hexdigest() == entry['sha256']
        assert len(payload) == entry['size_bytes']


def test_backend_merkle_leaves_and_root_match_vectors():
    assert evidence_merkle.MERKLE_SCHEME == VECTORS['merkle']['scheme']
    for leaf in VECTORS['merkle']['leaves']:
        assert evidence_merkle.leaf_hash(leaf['path'], leaf['sha256']).hex() == leaf['leaf_hash']
    assert evidence_merkle.compute_merkle_root(
        [(entry['path'], entry['sha256']) for entry in VECTORS['file_entries']]
    ) == VECTORS['merkle']['root']
    for case in VECTORS['merkle']['extra_cases']:
        assert evidence_merkle.compute_merkle_root(
            [tuple(pair) for pair in case['entries']]
        ) == case['root']


def test_backend_manifest_builder_reproduces_the_vector_manifest(monkeypatch):
    """The generator, not just the serializer, must produce the pinned bytes."""
    monkeypatch.setenv('EXPORT_SIGNING_SECRET', 'x' * 48)
    expected = VECTORS['manifest']
    manifest, file_bytes = build_evidence_manifest(
        export_id=expected['export_id'],
        export_type=expected['export_type'],
        workspace_id=expected['workspace_id'],
        generated_at=expected['generated_at'],
        generated_by_user_id=expected['generated_by_user_id'],
        source_resource_type=expected['source_resource_type'],
        source_resource_id=expected['source_resource_id'],
        storage_backend=expected['storage_backend'],
        file_values=VECTORS['file_values'],
        previous_audit_anchor_hash=expected['previous_audit_anchor_hash'],
        seal_merkle=True,
        policy_snapshot=expected['policy_snapshot'],
        required_artifacts=expected['required_artifacts'],
    )
    # The vector stores the manifest BODY (the bytes that get hashed) and the
    # resulting digest separately, exactly as the hash is defined.
    assert {k: v for k, v in manifest.items() if k != 'manifest_sha256'} == expected
    assert manifest['manifest_sha256'] == VECTORS['manifest_sha256']
    for path, payload in file_bytes.items():
        assert payload == canonical_json(VECTORS['file_values'][path])


def test_backend_manifest_hash_matches_vectors():
    body = {k: v for k, v in VECTORS['manifest'].items() if k != 'manifest_sha256'}
    assert canonical_json(body).decode('utf-8') == VECTORS['manifest_canonical_utf8']
    assert hashlib.sha256(canonical_json(body)).hexdigest() == VECTORS['manifest_sha256']


def test_backend_signing_payload_matches_vectors():
    """The exact 60 bytes Ed25519 covers. A drift here breaks every signature."""
    signature_vector = VECTORS['signature']
    assert evidence_ed25519.SIGNING_DOMAIN == signature_vector['signing_domain']
    assert evidence_ed25519.SIGNATURE_FORMAT == signature_vector['signature_format']
    assert evidence_ed25519.SIGNED_OBJECT == signature_vector['signed_object']
    payload = evidence_ed25519.signing_payload(VECTORS['manifest_sha256'])
    assert payload.hex() == signature_vector['signed_payload_hex']
    assert payload == (
        b'DECODA-EVIDENCE-MANIFEST-V1\x00' + bytes.fromhex(VECTORS['manifest_sha256'])
    )
    assert len(payload) == 60


def test_backend_reproduces_the_vector_signature(monkeypatch):
    """Ed25519 is deterministic, so the signature bytes themselves are pinned."""
    monkeypatch.setenv('EVIDENCE_SIGNING_ED25519_PRIVATE_KEY', base64.b64encode(TEST_SEED).decode())
    monkeypatch.setenv('EVIDENCE_SIGNING_ED25519_KEY_ID', VECTORS['signature']['key_id'])
    document = evidence_ed25519.sign_manifest_digest(VECTORS['manifest_sha256'])
    assert document is not None
    assert document['signature'] == VECTORS['signature']['signature']
    assert document['algorithm'] == 'Ed25519'
    assert document['key_id'] == VECTORS['signature']['key_id']


def test_backend_verifies_the_vector_signature_with_the_public_key_only():
    assert evidence_ed25519.verify_signature_document(
        {
            'algorithm': 'Ed25519',
            'signature_format': VECTORS['signature']['signature_format'],
            'signed_object': 'manifest_sha256',
            'signature': VECTORS['signature']['signature'],
        },
        VECTORS['manifest_sha256'],
        VECTORS['signature']['public_key'],
    ) is True


@pytest.mark.parametrize('bad_digest', ['0' * 64, 'f' * 63, '', 'not-hex' * 9])
def test_backend_rejects_a_signature_over_the_wrong_digest(bad_digest):
    assert evidence_ed25519.verify_signature_document(
        {
            'algorithm': 'Ed25519',
            'signature_format': VECTORS['signature']['signature_format'],
            'signed_object': 'manifest_sha256',
            'signature': VECTORS['signature']['signature'],
        },
        bad_digest,
        VECTORS['signature']['public_key'],
    ) is False
