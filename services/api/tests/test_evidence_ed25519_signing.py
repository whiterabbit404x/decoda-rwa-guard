"""Public-key evidence signing: what it proves, and what it refuses to claim.

The single rule these tests enforce: the product may say authenticity is
independently verifiable ONLY when a public-key signature actually verifies.
A valid HMAC never earns that phrase, because the key that checks it can also
forge it.
"""
from __future__ import annotations

import base64
import hashlib
import json

import pytest

from services.api.app import evidence_archive, evidence_ed25519, evidence_manifest_signer
from services.api.app.evidence_manifest_signer import (
    ASSURANCE_PUBLIC_KEY_SIGNATURE,
    ASSURANCE_SHARED_SECRET_HMAC,
    AUTHENTICITY_PUBLIC_KEY,
    AUTHENTICITY_SHARED_SECRET,
    SIGNATURE_INVALID,
    SIGNATURE_UNAVAILABLE,
    SIGNATURE_VALID,
    resolve_manifest_signer,
    signer_status,
)
from services.api.app.evidence_signing import (
    SEAL_SCHEMA_V2,
    build_evidence_manifest,
    canonical_json,
    seal_manifest,
)

TEST_SEED = hashlib.sha256(b'decoda-evidence-ed25519-unit-test-seed').digest()
TEST_KEY_ID = 'decoda-evidence-unit-test'

FILES = {
    'alerts.json': [{'id': 'alert-1', 'severity': 'high'}],
    'audit_log.json': [{'id': 'aud-1', 'action': 'incident.opened'}],
    'incidents.json': [{'id': 'inc-1', 'status': 'contained'}],
}


@pytest.fixture
def hmac_key(monkeypatch):
    monkeypatch.setenv('EXPORT_SIGNING_SECRET', 'unit-test-export-signing-secret-0123456789')
    monkeypatch.delenv('APP_MODE', raising=False)
    monkeypatch.delenv('APP_ENV', raising=False)


@pytest.fixture
def ed25519_key(monkeypatch, hmac_key):
    monkeypatch.setenv('EVIDENCE_SIGNING_ED25519_PRIVATE_KEY', base64.b64encode(TEST_SEED).decode())
    monkeypatch.setenv('EVIDENCE_SIGNING_ED25519_KEY_ID', TEST_KEY_ID)


@pytest.fixture
def no_ed25519_key(monkeypatch, hmac_key):
    monkeypatch.delenv('EVIDENCE_SIGNING_ED25519_PRIVATE_KEY', raising=False)
    monkeypatch.delenv('EVIDENCE_SIGNING_ED25519_PUBLIC_KEYS', raising=False)


def _manifest() -> dict:
    manifest, _ = build_evidence_manifest(
        export_id='pkg-1', export_type='proof_bundle', workspace_id='ws-1',
        generated_at='2026-01-01T00:00:00+00:00', generated_by_user_id='user-1',
        source_resource_type='incident', source_resource_id='inc-1',
        storage_backend='local', file_values=FILES, seal_merkle=True,
        required_artifacts=sorted(FILES), previous_audit_anchor_hash='a' * 64,
    )
    return manifest


# ── Signing ────────────────────────────────────────────────────────────────

def test_seal_carries_both_hmac_and_ed25519_when_key_is_provisioned(ed25519_key):
    seal = seal_manifest(_manifest())
    assert seal['signature_algorithm'] == 'HMAC-SHA256'
    assert seal['signature']  # the legacy field keeps its exact meaning
    assert seal['schema_version'] == SEAL_SCHEMA_V2
    document = seal['signatures'][0]
    assert document['algorithm'] == 'Ed25519'
    assert document['key_id'] == TEST_KEY_ID
    assert document['signed_object'] == 'manifest_sha256'
    assert document['signature_format'] == 'decoda-evidence-signature-v1'


def test_legacy_hmac_only_seal_when_no_ed25519_key(no_ed25519_key):
    """No key provisioned is a truthful state, not a fabricated signature."""
    seal = seal_manifest(_manifest())
    assert 'signatures' not in seal
    assert 'schema_version' not in seal
    assert seal['signature_algorithm'] == 'HMAC-SHA256'


def test_seal_never_contains_private_key_material(ed25519_key):
    seal = seal_manifest(_manifest())
    blob = json.dumps(seal)
    assert base64.b64encode(TEST_SEED).decode() not in blob
    assert TEST_SEED.hex() not in blob
    # The PUBLIC half is not secret, but it is not smuggled into the seal either.
    assert 'private' not in blob.lower()


def test_signature_commits_to_the_whole_manifest(ed25519_key):
    """Any change anywhere in the manifest invalidates the signature."""
    manifest = _manifest()
    seal = seal_manifest(manifest)
    signer = resolve_manifest_signer()
    assert signer.verify(manifest, seal)['status'] == SIGNATURE_VALID

    for field, value in (
        ('workspace_id', 'ws-attacker'),
        ('merkle_root', 'b' * 64),
        ('previous_audit_anchor_hash', 'c' * 64),
        ('source_resource_id', 'inc-other'),
    ):
        tampered = dict(manifest)
        tampered[field] = value
        tampered['manifest_sha256'] = hashlib.sha256(
            canonical_json({k: v for k, v in tampered.items() if k != 'manifest_sha256'})
        ).hexdigest()
        outcome = signer.verify(tampered, seal)
        assert outcome['status'] == SIGNATURE_INVALID, field
        assert outcome['authenticity']['independently_verifiable'] is False


def test_tampered_file_list_invalidates_the_signature(ed25519_key):
    manifest = _manifest()
    seal = seal_manifest(manifest)
    tampered = json.loads(json.dumps(manifest))
    tampered['files'][0]['sha256'] = 'd' * 64
    tampered['manifest_sha256'] = hashlib.sha256(
        canonical_json({k: v for k, v in tampered.items() if k != 'manifest_sha256'})
    ).hexdigest()
    assert resolve_manifest_signer().verify(tampered, seal)['status'] == SIGNATURE_INVALID


# ── Authenticity reporting ─────────────────────────────────────────────────

def test_public_key_seal_reports_independently_verifiable(ed25519_key):
    manifest = _manifest()
    outcome = resolve_manifest_signer().verify(manifest, seal_manifest(manifest))
    assert outcome['valid'] is True
    assert outcome['authenticity'] == {
        'method': AUTHENTICITY_PUBLIC_KEY,
        'status': 'verified',
        'independently_verifiable': True,
        'algorithm': 'Ed25519',
        'key_id': TEST_KEY_ID,
        'detail': (
            "Signed with Decoda's Ed25519 evidence key and verifiable offline "
            'with the published public key.'
        ),
    }


def test_valid_hmac_alone_is_never_independently_verifiable(no_ed25519_key):
    """The core truthfulness rule of this change."""
    manifest = _manifest()
    seal = seal_manifest(manifest)
    outcome = resolve_manifest_signer().verify(manifest, seal)
    # The HMAC itself verifies — Decoda holds the secret.
    assert outcome['status'] == SIGNATURE_VALID
    # …and that still does not establish authenticity to anyone else.
    assert outcome['authenticity']['method'] == AUTHENTICITY_SHARED_SECRET
    assert outcome['authenticity']['independently_verifiable'] is False
    assert 'forge' not in outcome['authenticity']['detail']  # phrased for customers
    assert 'cannot be independently verified' in outcome['authenticity']['detail']


def test_unknown_public_key_id_is_unavailable_not_invalid(ed25519_key, monkeypatch):
    """"We cannot check it" must never be rendered as "it was tampered with"."""
    manifest = _manifest()
    seal = seal_manifest(manifest)
    seal['signatures'][0]['key_id'] = 'a-key-this-deployment-never-published'
    outcome = resolve_manifest_signer().verify(manifest, seal)
    assert outcome['public_key_signature']['status'] == SIGNATURE_UNAVAILABLE
    assert outcome['authenticity']['status'] == 'unavailable'
    assert outcome['authenticity']['independently_verifiable'] is False


def test_corrupted_public_signature_fails_even_though_hmac_is_valid(ed25519_key):
    manifest = _manifest()
    seal = seal_manifest(manifest)
    raw = bytearray(base64.b64decode(seal['signatures'][0]['signature']))
    raw[0] ^= 0xFF
    seal['signatures'][0]['signature'] = base64.b64encode(bytes(raw)).decode()
    outcome = resolve_manifest_signer().verify(manifest, seal)
    assert outcome['status'] == SIGNATURE_INVALID
    assert outcome['valid'] is False


def test_signer_identity_reports_public_key_capability_truthfully(ed25519_key):
    identity = resolve_manifest_signer().identity
    assert identity.public_key_signing is True
    assert identity.public_key_algorithm == 'Ed25519'
    assert identity.public_key_id == TEST_KEY_ID
    assert identity.assurance == ASSURANCE_PUBLIC_KEY_SIGNATURE
    # Ed25519 does not make the key hardware-custodied. The product must not
    # start claiming HSM/KMS because a public-key signature exists.
    assert identity.hardware_backed is False
    assert signer_status()['hsm_backed'] is False
    assert signer_status()['public_key_signing_available'] is True


def test_signer_identity_without_ed25519_key(no_ed25519_key):
    identity = resolve_manifest_signer().identity
    assert identity.public_key_signing is False
    assert identity.assurance == ASSURANCE_SHARED_SECRET_HMAC
    assert signer_status()['public_key_signing_available'] is False


# ── Keyring and rotation ───────────────────────────────────────────────────

def test_public_keyring_contains_public_material_only(ed25519_key):
    keyring = evidence_ed25519.public_keyring()
    assert keyring['schema_version'] == 1
    assert keyring['keys'][0]['key_id'] == TEST_KEY_ID
    assert keyring['keys'][0]['status'] == 'active'
    blob = json.dumps(keyring)
    assert base64.b64encode(TEST_SEED).decode() not in blob
    assert TEST_SEED.hex() not in blob
    # The published key is genuinely the public half of the signing key.
    public = base64.b64decode(keyring['keys'][0]['public_key'])
    assert len(public) == 32
    assert public == evidence_ed25519.load_signing_key().public_key_bytes()


def test_retired_public_keys_stay_published_for_historical_evidence(ed25519_key, monkeypatch):
    retired = base64.b64encode(b'\x07' * 32).decode()
    monkeypatch.setenv('EVIDENCE_SIGNING_ED25519_PUBLIC_KEYS', f'decoda-evidence-2025-01:{retired}:retired')
    keyring = evidence_ed25519.public_keyring()
    by_id = {entry['key_id']: entry for entry in keyring['keys']}
    assert by_id[TEST_KEY_ID]['status'] == 'active'
    assert by_id['decoda-evidence-2025-01']['status'] == 'retired'
    assert evidence_ed25519.public_key_for('decoda-evidence-2025-01') == retired


def test_malformed_pinned_public_key_is_dropped_not_published(ed25519_key, monkeypatch):
    monkeypatch.setenv('EVIDENCE_SIGNING_ED25519_PUBLIC_KEYS', 'bad-key:not-base64!!!:retired')
    assert evidence_ed25519.public_key_for('bad-key') is None


def test_keyring_is_empty_without_a_provisioned_key(no_ed25519_key):
    assert evidence_ed25519.public_keyring()['keys'] == []
    assert evidence_ed25519.signing_available() is False
    assert evidence_ed25519.sign_manifest_digest('a' * 64) is None


def test_no_builtin_development_keypair_exists(no_ed25519_key):
    """A signing key in source would be forgeable by anyone who can read it."""
    assert evidence_ed25519.load_signing_key() is None


# ── Archive ────────────────────────────────────────────────────────────────

def test_archive_bundles_public_keyring_and_verify_instructions(ed25519_key):
    import zipfile
    import io

    manifest = _manifest()
    seal = seal_manifest(manifest)
    archive_bytes = evidence_archive.build_evidence_archive(
        package_id='pkg-1', package_number='EV-2026-001', manifest=manifest, seal=seal,
        file_values=FILES, verification={'status': 'VERIFIED'},
        signer=resolve_manifest_signer().identity.as_dict(), summary=None,
        generated_at='2026-01-01T00:00:00+00:00',
        public_keyring=evidence_ed25519.public_keyring(),
    )
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        names = set(archive.namelist())
        assert 'EV-2026-001/seal.json' in names
        assert 'EV-2026-001/manifest.sig' in names
        assert 'EV-2026-001/VERIFY.md' in names
        assert 'EV-2026-001/verification/decoda-evidence-keys.json' in names
        # seal.json and manifest.sig are ONE document under two names.
        assert archive.read('EV-2026-001/seal.json') == archive.read('EV-2026-001/manifest.sig')
        keyring = json.loads(archive.read('EV-2026-001/verification/decoda-evidence-keys.json'))
        assert 'does not by itself establish trust' in keyring['trust_note']
        verify_md = archive.read('EV-2026-001/VERIFY.md').decode()
        assert 'decoda_evidence_verifier' in verify_md
        assert 'does NOT prove' in verify_md
        # No private material anywhere in the archive.
        assert base64.b64encode(TEST_SEED) not in archive_bytes
        assert TEST_SEED.hex().encode() not in archive_bytes


def test_well_known_endpoint_publishes_public_keys_and_no_secret(ed25519_key):
    """The published keyring must be fetchable without a login and carry no secret."""
    from services.api.app.main import well_known_evidence_keys

    response = well_known_evidence_keys()
    assert response.media_type == 'application/json'
    body = response.body.decode('utf-8')
    payload = json.loads(body)
    assert payload['keys'][0]['key_id'] == TEST_KEY_ID
    assert payload['keys'][0]['algorithm'] == 'Ed25519'
    # No private material, and no shared secret, on a public endpoint.
    assert base64.b64encode(TEST_SEED).decode() not in body
    assert TEST_SEED.hex() not in body
    assert 'unit-test-export-signing-secret' not in body


def test_well_known_endpoint_publishes_nothing_when_no_key_is_provisioned(no_ed25519_key):
    from services.api.app.main import well_known_evidence_keys

    payload = json.loads(well_known_evidence_keys().body.decode('utf-8'))
    # An empty list, never a placeholder key that would read as a trust anchor.
    assert payload['keys'] == []


def test_archive_omits_keyring_when_none_is_provisioned(no_ed25519_key):
    import io
    import zipfile

    manifest = _manifest()
    archive_bytes = evidence_archive.build_evidence_archive(
        package_id='pkg-1', package_number='EV-2026-002', manifest=manifest,
        seal=seal_manifest(manifest), file_values=FILES, verification=None,
        signer=resolve_manifest_signer().identity.as_dict(), summary=None,
        generated_at='2026-01-01T00:00:00+00:00', public_keyring=None,
    )
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        # Never a placeholder or empty keyring that could read as a trust anchor.
        assert 'EV-2026-002/verification/decoda-evidence-keys.json' not in archive.namelist()
