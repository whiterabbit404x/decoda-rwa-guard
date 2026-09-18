"""Regenerate ``evidence-test-vectors.json``.

The vectors are the CONTRACT between three independent implementations:

  * ``services/api/app/evidence_signing.py``  (backend generation)
  * ``services/api/app/evidence_verification.py`` (backend verification)
  * ``tools/decoda_evidence_verifier/verifier.py`` (standalone offline verifier)

Each side asserts against these bytes in its own test suite, so a change to any
serializer, hash or signing payload that is not intentional shows up as a
failing vector rather than as evidence that silently stops verifying.

Run it only when the format intentionally changes, and review the diff as a
format change:

    python tools/decoda_evidence_verifier/testvectors/generate.py

TEST KEY: the Ed25519 keypair here is DERIVED DETERMINISTICALLY from a published
constant, so no key material is stored in the repository and nothing about it is
secret. It exists to pin the signature format. It is NOT a Decoda signing key,
and anything it signs is a test fixture, never evidence.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from decoda_evidence_verifier.verifier import (  # noqa: E402
    canonical_json, compute_merkle_root, leaf_hash, signing_payload,
)

#: Published, non-secret derivation input for the test keypair.
TEST_SEED_LABEL = b'decoda-evidence-test-vector-seed-v1'
TEST_KEY_ID = 'decoda-evidence-test-0001'


def test_seed() -> bytes:
    return hashlib.sha256(TEST_SEED_LABEL).digest()


def build() -> dict:
    import base64

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    private = ed25519.Ed25519PrivateKey.from_private_bytes(test_seed())
    public_raw = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw, format=serialization.PublicFormat.Raw,
    )

    # A file value chosen to exercise the canonicalization rules that matter:
    # key ordering, compact separators, non-ASCII escaping (ensure_ascii=True),
    # nested structures and an empty container.
    file_values = {
        'alerts.json': [{'severity': 'high', 'id': 'alert-1', 'note': 'café — München'}],
        'audit_log.json': [],
        'incidents.json': [{'id': 'inc-1', 'status': 'contained', 'nested': {'b': 2, 'a': 1}}],
        'summary.json': {'export_id': 'pkg-vector-1', 'incident_id': 'inc-1', 'count': 3},
    }
    file_bytes = {path: canonical_json(value) for path, value in file_values.items()}
    # The per-file shape a schema-2.0 manifest carries: digest, byte length and
    # provenance. Byte-identical to what ``build_evidence_manifest`` emits for
    # these same inputs, which the backend vector test asserts directly.
    file_entries = [
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

    merkle_entries = [(entry['path'], entry['sha256']) for entry in file_entries]
    merkle_root = compute_merkle_root(merkle_entries)

    manifest = {
        'manifest_version': '1.0',
        'schema_version': '2.0',
        'export_id': 'pkg-vector-1',
        'export_type': 'proof_bundle',
        'workspace_id': 'ws-vector-1',
        'generated_at': '2026-01-01T00:00:00+00:00',
        'generated_by_user_id': 'user-vector-1',
        'source_resource_type': 'incident',
        'source_resource_id': 'inc-1',
        'storage_backend': 'local',
        'files': file_entries,
        'hash_algorithm': 'SHA-256',
        'artifact_count': len(file_entries),
        'merkle_root': merkle_root,
        'merkle_scheme': 'decoda-merkle-v1',
        'policy_snapshot': {'present': False, 'reason': 'No policy evaluation was recorded for this incident.'},
        'required_artifacts': sorted(file_bytes),
        'previous_audit_anchor_hash': 'a' * 64,
    }
    manifest_canonical = canonical_json(manifest)
    manifest_sha256 = hashlib.sha256(manifest_canonical).hexdigest()
    payload = signing_payload(manifest_sha256)
    signature = private.sign(payload)

    return {
        'schema_version': 1,
        'description': (
            'Cross-implementation test vectors for Decoda evidence packages. Asserted by the '
            'backend generation tests, the backend verification tests and the standalone '
            'offline verifier tests, so all three provably agree byte for byte.'
        ),
        'canonical_json': {
            'rules': "json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('utf-8')",
            'cases': [
                {'value': value, 'bytes_utf8': canonical_json(value).decode('utf-8'),
                 'sha256': hashlib.sha256(canonical_json(value)).hexdigest()}
                for value in (
                    {},
                    {'b': 1, 'a': 2},
                    {'unicode': 'café — München'},
                    [1, 2.5, True, False, None],
                    {'nested': {'z': [{'y': 1}], 'a': {}}},
                )
            ],
        },
        'file_values': file_values,
        'file_entries': file_entries,
        'merkle': {
            'scheme': 'decoda-merkle-v1',
            'leaf_rule': "SHA256(0x00 || utf8(path) || 0x1F || ascii(lower(sha256_hex)))",
            'node_rule': 'SHA256(0x01 || left || right)',
            'order': 'leaves sorted ascending by the UTF-8 bytes of path',
            'odd_rule': 'an unpaired last node is promoted unchanged, never duplicated',
            'leaves': [
                {'path': path, 'sha256': digest, 'leaf_hash': leaf_hash(path, digest).hex()}
                for path, digest in sorted(merkle_entries, key=lambda item: item[0].encode('utf-8'))
            ],
            'root': merkle_root,
            'extra_cases': [
                {'entries': [], 'root': compute_merkle_root([])},
                {'entries': [['a.json', '11' * 32]], 'root': compute_merkle_root([('a.json', '11' * 32)])},
                {'entries': [['a.json', '11' * 32], ['b.json', '22' * 32], ['c.json', '33' * 32]],
                 'root': compute_merkle_root([('a.json', '11' * 32), ('b.json', '22' * 32), ('c.json', '33' * 32)])},
            ],
        },
        'manifest': manifest,
        'manifest_canonical_utf8': manifest_canonical.decode('utf-8'),
        'manifest_sha256': manifest_sha256,
        'signature': {
            'algorithm': 'Ed25519',
            'signature_format': 'decoda-evidence-signature-v1',
            'signed_object': 'manifest_sha256',
            'signing_domain': 'DECODA-EVIDENCE-MANIFEST-V1',
            'signed_payload_rule': "b'DECODA-EVIDENCE-MANIFEST-V1' + b'\\x00' + bytes.fromhex(manifest_sha256)",
            'signed_payload_hex': payload.hex(),
            'key_id': TEST_KEY_ID,
            'test_key_derivation': (
                'Ed25519 seed = SHA-256(b"decoda-evidence-test-vector-seed-v1"). PUBLISHED, NON-SECRET, '
                'TEST ONLY. Not a Decoda signing key; anything it signs is a fixture, never evidence.'
            ),
            'public_key': base64.b64encode(public_raw).decode('ascii'),
            'signature': base64.b64encode(signature).decode('ascii'),
        },
    }


if __name__ == '__main__':
    target = pathlib.Path(__file__).with_name('evidence-test-vectors.json')
    target.write_text(json.dumps(build(), indent=2, sort_keys=True, ensure_ascii=False) + '\n', encoding='utf-8')
    print(f'wrote {target}')
