"""Public-key (Ed25519) authenticity for evidence manifests.

Why this module exists
----------------------
The pre-existing seal is HMAC-SHA256 over the canonical manifest bytes. That is
a real tamper-evident seal, but it is verifiable ONLY by someone who holds the
same secret — and anyone who holds it can also FORGE a seal. So it can never be
handed to a customer, auditor or regulator: "independently verifiable" and
"shared secret" are mutually exclusive.

Ed25519 splits those roles. Decoda holds the private signing key; the public
verification key is published. A third party with the public key alone can prove
a manifest was signed by the holder of Decoda's signing key, and can prove
nothing about how to forge one.

This module does NOT implement Ed25519. It uses ``cryptography``'s audited
implementation (already a pinned dependency of this service).

Exactly what is signed
----------------------
A domain-separated commitment to the manifest digest, and nothing else::

    payload = b'DECODA-EVIDENCE-MANIFEST-V1\\x00' + bytes.fromhex(manifest_sha256)

28 ASCII bytes of domain tag, one NUL separator, then the RAW 32 bytes of the
manifest digest — 60 bytes total, fixed length, no JSON, no ambiguity. The
domain tag means a Decoda evidence signature can never be replayed as a
signature over some other Decoda object, and the fixed length means no
length-extension or delimiter confusion is possible.

``manifest_sha256`` is itself SHA-256 over the canonical JSON of the whole
manifest body (minus that field), and the manifest body carries every artifact
digest, the Merkle root, the policy snapshot and the audit anchor. So signing
those 60 bytes transitively commits to the entire package — provided the
verifier RECOMPUTES ``manifest_sha256`` from the manifest bytes it actually
holds rather than trusting the field. Every verifier here and in
``tools/decoda_evidence_verifier`` does exactly that.

Key material
------------
The private key is a 32-byte Ed25519 seed loaded through
:mod:`services.api.app.managed_keys` under the ``EVIDENCE_SIGNING_ED25519``
purpose, so production reads it from the configured managed secret provider
(AWS Secrets Manager) and development may use an environment variable. It is
read only at signing time, never logged, never returned through an API, never
written into an evidence package and never available to the offline verifier.
:func:`public_keyring` derives the PUBLIC half and is the only thing published.

Rotation
--------
Each signature records the ``key_id`` it was made with. The published keyring
carries every key that has ever signed, each with a ``status``
(``active`` / ``retired``). A retired PUBLIC key is never removed, because
historical evidence must stay verifiable forever; retiring only means it is no
longer used for new signatures.
"""
from __future__ import annotations

import base64
import binascii
import logging
import os
from dataclasses import dataclass
from typing import Any

from services.api.app.managed_keys import load_managed_key

_log = logging.getLogger(__name__)

#: Managed-key purpose for the Ed25519 evidence signing seed.
KEY_PURPOSE = 'EVIDENCE_SIGNING_ED25519'

#: The signature algorithm this module produces and verifies.
ALGORITHM = 'Ed25519'

#: Stable identifier for the signature document layout. A future change to WHAT
#: is signed is a NEW format string, never a silent reinterpretation of this one.
SIGNATURE_FORMAT = 'decoda-evidence-signature-v1'
SIGNATURE_FORMAT_VERSION = 1

#: The object the signature commits to, named explicitly inside the document.
SIGNED_OBJECT = 'manifest_sha256'

#: Domain separation tag. See the module docstring for the exact payload.
SIGNING_DOMAIN = 'DECODA-EVIDENCE-MANIFEST-V1'
_SIGNING_DOMAIN_BYTES = SIGNING_DOMAIN.encode('ascii') + b'\x00'

#: Ed25519 raw key sizes, per RFC 8032.
_SEED_BYTES = 32
_PUBLIC_KEY_BYTES = 32

#: Keyring document schema.
KEYRING_SCHEMA_VERSION = 1

KEY_STATUS_ACTIVE = 'active'
KEY_STATUS_RETIRED = 'retired'


class EvidenceSigningKeyError(RuntimeError):
    """Ed25519 signing material is missing or unusable. Never carries key bytes."""


def signing_payload(manifest_sha256: str) -> bytes:
    """The EXACT bytes an Ed25519 evidence signature covers.

    ``DECODA-EVIDENCE-MANIFEST-V1`` || ``0x00`` || raw 32-byte manifest digest.

    Raises :class:`ValueError` if the digest is not a 64-character lowercase-able
    hex SHA-256, so a signature can never be produced over a malformed or
    truncated commitment.
    """
    digest = str(manifest_sha256 or '').strip().lower()
    if len(digest) != 64:
        raise ValueError('manifest_sha256 must be a 64-character hex SHA-256 digest')
    try:
        raw = bytes.fromhex(digest)
    except ValueError as exc:
        raise ValueError('manifest_sha256 must be hexadecimal') from exc
    return _SIGNING_DOMAIN_BYTES + raw


def _decode_key_bytes(value: str | bytes, *, expected: int) -> bytes:
    """Decode base64 (or raw bytes) key material to exactly ``expected`` bytes.

    Never logs or echoes the value — a decode failure reports only the length
    mismatch, never the material.
    """
    if isinstance(value, (bytes, bytearray)):
        raw = bytes(value)
        # Managed providers hand back the stored string as UTF-8 bytes; a stored
        # base64 seed therefore arrives here as its ASCII encoding.
        if len(raw) != expected:
            try:
                raw = base64.b64decode(raw.strip(), validate=True)
            except (binascii.Error, ValueError) as exc:
                raise EvidenceSigningKeyError(
                    f'Ed25519 key material must be {expected} raw bytes or base64 of them.'
                ) from exc
    else:
        try:
            raw = base64.b64decode(str(value).strip(), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise EvidenceSigningKeyError('Ed25519 key material must be valid base64.') from exc
    if len(raw) != expected:
        raise EvidenceSigningKeyError(
            f'Ed25519 key material must decode to exactly {expected} bytes (got {len(raw)}).'
        )
    return raw


def _ed25519_module():
    """The audited Ed25519 implementation. Never a hand-rolled one."""
    try:
        from cryptography.hazmat.primitives.asymmetric import ed25519
    except ImportError as exc:  # pragma: no cover - dependency is pinned
        raise EvidenceSigningKeyError(
            'The cryptography package is required for Ed25519 evidence signing.'
        ) from exc
    return ed25519


@dataclass(frozen=True)
class Ed25519SigningKey:
    """A loaded private signing key. ``seed`` never leaves this process."""

    key_id: str
    provider: str
    version: str
    seed: bytes

    def public_key_bytes(self) -> bytes:
        ed25519 = _ed25519_module()
        private = ed25519.Ed25519PrivateKey.from_private_bytes(self.seed)
        from cryptography.hazmat.primitives import serialization

        return private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )

    def sign(self, payload: bytes) -> bytes:
        ed25519 = _ed25519_module()
        return ed25519.Ed25519PrivateKey.from_private_bytes(self.seed).sign(payload)


def signing_key_id() -> str:
    """The configured key identifier for NEW signatures."""
    configured = os.getenv('EVIDENCE_SIGNING_ED25519_KEY_ID', '').strip()
    return configured or 'decoda-evidence-ed25519'


def load_signing_key() -> Ed25519SigningKey | None:
    """Load the private signing key, or ``None`` when none is provisioned.

    ``None`` is a legitimate, truthful state: a deployment that has not yet
    provisioned an Ed25519 key seals with HMAC only, and every surface then
    reports authenticity as NOT independently verifiable. It never fabricates a
    key, and there is deliberately no development fallback keypair — a
    hard-coded signing key would make every "signed by Decoda" claim forgeable
    by anyone holding this source.
    """
    try:
        managed = load_managed_key(KEY_PURPOSE)
    except RuntimeError:
        return None
    try:
        seed = _decode_key_bytes(managed.material, expected=_SEED_BYTES)
    except EvidenceSigningKeyError:
        # A provisioned-but-unusable key is an operator error worth surfacing,
        # but it must never be logged with its material.
        _log.error('evidence_ed25519_key_unusable key_id=%s provider=%s', managed.key_id, managed.provider)
        raise
    return Ed25519SigningKey(
        key_id=signing_key_id(),
        provider=managed.provider,
        version=managed.version,
        seed=seed,
    )


def signing_available() -> bool:
    """True when a usable Ed25519 private key is provisioned."""
    try:
        return load_signing_key() is not None
    except EvidenceSigningKeyError:
        return False


def sign_manifest_digest(manifest_sha256: str, key: Ed25519SigningKey | None = None) -> dict[str, Any] | None:
    """Produce the Ed25519 signature document for one manifest digest.

    Returns ``None`` when no signing key is provisioned — the caller then emits
    an HMAC-only seal and reports authenticity truthfully. Never raises for the
    "not provisioned" case, because that is configuration, not failure.
    """
    signing_key = key or load_signing_key()
    if signing_key is None:
        return None
    payload = signing_payload(manifest_sha256)
    signature = signing_key.sign(payload)
    return {
        'signature_format': SIGNATURE_FORMAT,
        'signature_format_version': SIGNATURE_FORMAT_VERSION,
        'algorithm': ALGORITHM,
        'key_id': signing_key.key_id,
        'signed_object': SIGNED_OBJECT,
        'signing_domain': SIGNING_DOMAIN,
        'signed_manifest_sha256': str(manifest_sha256 or '').strip().lower(),
        'signature': base64.b64encode(signature).decode('ascii'),
        'public_key_verifiable': True,
    }


def verify_signature_document(
    document: dict[str, Any] | None,
    manifest_sha256: str,
    public_key_b64: str | bytes,
) -> bool:
    """Verify one Ed25519 signature document against a PUBLIC key.

    Takes public key material only — this function cannot sign, and no caller of
    it ever holds private material. Returns ``False`` for every malformed input
    rather than raising, so a corrupt document fails closed as "invalid".
    """
    if not isinstance(document, dict):
        return False
    if str(document.get('algorithm') or '') != ALGORITHM:
        return False
    if str(document.get('signature_format') or '') != SIGNATURE_FORMAT:
        return False
    if str(document.get('signed_object') or SIGNED_OBJECT) != SIGNED_OBJECT:
        return False
    try:
        payload = signing_payload(manifest_sha256)
        signature = base64.b64decode(str(document.get('signature') or '').strip(), validate=True)
        public_bytes = _decode_key_bytes(public_key_b64, expected=_PUBLIC_KEY_BYTES)
    except (ValueError, binascii.Error, EvidenceSigningKeyError):
        return False
    ed25519 = _ed25519_module()
    try:
        from cryptography.exceptions import InvalidSignature

        ed25519.Ed25519PublicKey.from_public_bytes(public_bytes).verify(signature, payload)
    except InvalidSignature:
        return False
    except ValueError:
        return False
    return True


def _keyring_from_environment() -> list[dict[str, Any]]:
    """Additional PUBLIC keys an operator pins into the published keyring.

    ``EVIDENCE_SIGNING_ED25519_PUBLIC_KEYS`` is a comma-separated list of
    ``key_id:base64_public_key[:status]`` entries. This is how a RETIRED key
    stays published after its private half is destroyed — historical evidence
    must remain verifiable forever, so a public key is never dropped merely
    because it no longer signs.
    """
    raw = os.getenv('EVIDENCE_SIGNING_ED25519_PUBLIC_KEYS', '').strip()
    if not raw:
        return []
    keys: list[dict[str, Any]] = []
    for item in raw.split(','):
        parts = [part.strip() for part in item.split(':')]
        if len(parts) < 2 or not parts[0] or not parts[1]:
            continue
        status = parts[2].lower() if len(parts) > 2 and parts[2] else KEY_STATUS_RETIRED
        if status not in {KEY_STATUS_ACTIVE, KEY_STATUS_RETIRED}:
            status = KEY_STATUS_RETIRED
        try:
            _decode_key_bytes(parts[1], expected=_PUBLIC_KEY_BYTES)
        except EvidenceSigningKeyError:
            # A malformed pinned key is dropped rather than published as if it
            # were usable: a keyring entry nobody can verify against is worse
            # than an absent one.
            _log.warning('evidence_ed25519_public_key_ignored key_id=%s reason=malformed', parts[0])
            continue
        keys.append({
            'key_id': parts[0],
            'algorithm': ALGORITHM,
            'public_key': parts[1],
            'status': status,
        })
    return keys


def public_keyring() -> dict[str, Any]:
    """The publishable verification keyring. Contains PUBLIC material only.

    Safe to serve unauthenticated, to bundle beside an evidence package and to
    pin locally. The private seed is never read into this document — only the
    derived public half of the active key, plus any pinned retired keys.
    """
    keys: list[dict[str, Any]] = []
    try:
        signing_key = load_signing_key()
    except EvidenceSigningKeyError:
        signing_key = None
    if signing_key is not None:
        keys.append({
            'key_id': signing_key.key_id,
            'algorithm': ALGORITHM,
            'public_key': base64.b64encode(signing_key.public_key_bytes()).decode('ascii'),
            'status': KEY_STATUS_ACTIVE,
        })
    active_ids = {key['key_id'] for key in keys}
    for entry in _keyring_from_environment():
        if entry['key_id'] in active_ids:
            continue
        keys.append(entry)
    return {
        'schema_version': KEYRING_SCHEMA_VERSION,
        'issuer': 'Decoda RWA Guard',
        'signature_format': SIGNATURE_FORMAT,
        'signed_object': SIGNED_OBJECT,
        'signing_domain': SIGNING_DOMAIN,
        'keys': keys,
        'note': (
            'Public Ed25519 verification keys for Decoda evidence packages. Retired keys are '
            'retained so historical evidence stays verifiable. This document contains no '
            'private key material.'
        ),
    }


def public_key_for(key_id: str) -> str | None:
    """The published PUBLIC key for one ``key_id``, or ``None`` if unknown."""
    wanted = str(key_id or '').strip()
    if not wanted:
        return None
    for entry in public_keyring()['keys']:
        if entry.get('key_id') == wanted:
            return str(entry.get('public_key') or '') or None
    return None
