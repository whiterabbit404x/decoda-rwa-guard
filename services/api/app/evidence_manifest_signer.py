"""Evidence manifest signing abstraction — with TRUTHFUL signer assurance.

This module does not introduce a second crypto path. It is a thin, explicit
interface over the existing, already-hardened primitives in
``evidence_signing`` (``seal_manifest`` / ``verify_bundle`` / ``canonical_json``)
so that Screen 9 can answer three questions that must never be conflated:

    1. Was the manifest SIGNED at all?
    2. Does the signature VERIFY against the trusted key?
    3. What KIND of signing authority produced it?

What is signed, exactly
-----------------------
The signature covers ``canonical_json(manifest)`` — the deterministic UTF-8
JSON of the COMPLETE manifest object, sorted keys, compact separators,
``ensure_ascii``, INCLUDING the manifest's own ``manifest_sha256`` field. Since
the manifest embeds every artifact's SHA-256, the artifact count, the hash
algorithm, the Merkle root and the policy snapshot, all of those are covered
transitively: change any one of them and the signature no longer verifies.

Signer providers actually available in this codebase
----------------------------------------------------
``env``
    Key material from ``EXPORT_SIGNING_SECRET`` / ``EVIDENCE_SIGNING_SECRET``.
    Local and legacy deployments.
``aws_secrets_manager``
    Key material from AWS Secrets Manager, version-addressable so historical
    evidence stays verifiable across rotation.
``dev_fallback``
    A hard-coded non-production test secret, permitted only outside
    production/staging. Anything it seals is explicitly marked NOT valid for
    evidentiary purposes.

**None of these is an HSM- or KMS-backed signer.** All three are shared-secret
HMAC-SHA256: the key material is fetched into application memory and the MAC is
computed in-process. That is a real, tamper-EVIDENT seal for anyone who holds
the verification key, but it is NOT hardware-custodied, NOT non-repudiable to a
third party, and NOT an asymmetric signature. :attr:`SignerIdentity.hardware_backed`
is therefore ``False`` for every provider this build can construct, and
:func:`signer_status` reports that verbatim. The UI must render the reported
assurance, never the word "HSM", unless a future provider sets
``hardware_backed=True`` because the signing operation genuinely happens inside
a KMS/HSM boundary.

Adding a KMS/HSM provider later means implementing :class:`EvidenceManifestSigner`
with ``provider='aws_kms'`` (or similar), ``algorithm='RSASSA-PSS-SHA256'`` /
``'ECDSA-P256-SHA256'``, ``hardware_backed=True`` and a public-key reference —
without touching manifest construction, verification flow or the UI contract.

Public-key authenticity (Ed25519)
---------------------------------
Independent of hardware custody — and the reason this module reports two
separate facts — a seal may ALSO carry an Ed25519 signature produced by
:mod:`services.api.app.evidence_ed25519`. That signature is what a customer,
auditor or regulator can check offline with nothing but Decoda's PUBLIC
verification key, using ``tools/decoda_evidence_verifier``.

The distinction this module refuses to blur:

``signature verified``
    Some seal on this package re-derived correctly against a key THIS
    deployment holds. For an HMAC seal that key is the shared secret, so the
    check proves the package is unaltered — to us.
``authenticity independently verifiable``
    A third party holding only PUBLIC key material can prove the manifest was
    signed by the holder of Decoda's private signing key. ONLY a verified
    Ed25519 signature establishes this. It is reported in the ``authenticity``
    block of :meth:`EvidenceManifestSigner.verify`, and an HMAC-only package
    never sets it however cleanly its MAC verifies.
"""
from __future__ import annotations

import hmac
import logging
from dataclasses import dataclass
from typing import Any

from services.api.app import evidence_signing
from services.api.app.managed_keys import (
    managed_key_enforcement_mode,
    managed_key_provider,
)

_log = logging.getLogger(__name__)

#: The shared-secret algorithm every seal has always carried.
SIGNATURE_ALGORITHM = 'HMAC-SHA256'

#: Assurance classes. These are what the product is allowed to CLAIM.
ASSURANCE_SHARED_SECRET_HMAC = 'shared_secret_hmac'
#: An asymmetric signature a third party can verify with PUBLIC key material
#: alone. This is the only assurance class that licenses the product to say
#: authenticity is independently verifiable.
ASSURANCE_PUBLIC_KEY_SIGNATURE = 'public_key_signature'
ASSURANCE_HARDWARE_BACKED = 'hardware_backed_signature'

#: How a seal's authenticity can be established, and by whom.
AUTHENTICITY_PUBLIC_KEY = 'public_key'
AUTHENTICITY_SHARED_SECRET = 'shared_secret'
AUTHENTICITY_NONE = 'none'

#: Where the private/secret key material lives while signing happens.
CUSTODY_APPLICATION_MEMORY = 'application_memory'
CUSTODY_HARDWARE_MODULE = 'hardware_security_module'

#: Verification outcomes, kept separate so "we cannot check" is never reported
#: as "the signature is bad" and vice versa.
SIGNATURE_VALID = 'valid'
SIGNATURE_INVALID = 'invalid'
SIGNATURE_UNAVAILABLE = 'unavailable'
SIGNATURE_ABSENT = 'absent'

_HUMAN_ASSURANCE_LABELS = {
    ASSURANCE_SHARED_SECRET_HMAC: 'Shared-secret HMAC (software)',
    ASSURANCE_PUBLIC_KEY_SIGNATURE: 'Ed25519 public-key signature (software key)',
    ASSURANCE_HARDWARE_BACKED: 'Hardware-backed signature',
}


def _verification_key(*, version: str | None) -> bytes | None:
    """The key a seal must be checked against — resolved EXACTLY as signing resolves it.

    ``evidence_signing`` signs with the managed/environment key when one is
    configured and, OUTSIDE production/staging only, with the documented
    development fallback. Verification has to mirror that or a locally-sealed
    package could never be re-verified in the environment that sealed it, and the
    product would report ``SIGNATURE_UNAVAILABLE`` for a seal it had just made.

    The fallback is strictly non-production: in production/staging a missing
    managed key yields ``None`` and the signature is reported UNVERIFIABLE, which
    fails closed. A seal produced with the dev fallback stays truthfully labelled
    — the seal carries its ``DEV_MODE_TEST_SECRET`` warning and
    :attr:`SignerIdentity.production_grade` is False — so "verifiable in dev"
    never becomes "valid as evidence".
    """
    secret = evidence_signing._get_signing_secret(version=version)
    if secret is not None:
        return secret
    if evidence_signing._is_production_like():
        return None
    return evidence_signing._DEV_FALLBACK_SECRET


@dataclass(frozen=True)
class SignerIdentity:
    """Non-secret description of the signing authority behind a seal.

    Every field here is safe to persist in a manifest, return from an API and
    render in customer-facing UI. Key MATERIAL never appears in this object.
    """

    provider: str
    algorithm: str
    key_id: str
    key_version: str
    #: True ONLY when the signing operation happens inside a KMS/HSM boundary.
    #: Every provider in this build is in-process HMAC, so this is False.
    hardware_backed: bool
    assurance: str
    key_custody: str
    #: True when the key is a real configured production/staging key (not the
    #: dev fallback and not a known-weak value).
    production_grade: bool
    #: Customer-safe caveat, or None. Rendered verbatim by the UI.
    warning: str | None = None
    #: True ONLY when this signer additionally produces an asymmetric signature
    #: a third party can verify with PUBLIC key material alone. This is the one
    #: flag that licenses the phrase "independently verifiable authenticity".
    public_key_signing: bool = False
    #: The public-key algorithm and key id for new signatures, when available.
    public_key_algorithm: str | None = None
    public_key_id: str | None = None

    @property
    def assurance_label(self) -> str:
        return _HUMAN_ASSURANCE_LABELS.get(self.assurance, self.assurance)

    def as_dict(self) -> dict[str, Any]:
        return {
            'provider': self.provider,
            'algorithm': self.algorithm,
            'key_id': self.key_id,
            'key_version': self.key_version,
            'hardware_backed': self.hardware_backed,
            'assurance': self.assurance,
            'assurance_label': self.assurance_label,
            'key_custody': self.key_custody,
            'production_grade': self.production_grade,
            'warning': self.warning,
            'public_key_signing': self.public_key_signing,
            'public_key_algorithm': self.public_key_algorithm,
            'public_key_id': self.public_key_id,
        }


class EvidenceManifestSigner:
    """Signs and verifies an evidence manifest.

    ``sign`` produces the seal document that is embedded in the package as
    ``seal.json`` (and exported as ``manifest.sig``). ``verify`` re-derives the
    MAC over the canonical manifest bytes and reports a THREE-valued outcome —
    valid / invalid / unavailable — because a missing verification key is a
    different fact from a bad signature and must never be shown as tampering.
    """

    def __init__(self, identity: SignerIdentity) -> None:
        self._identity = identity

    # -- identity --------------------------------------------------------
    @property
    def identity(self) -> SignerIdentity:
        return self._identity

    @property
    def provider(self) -> str:
        return self._identity.provider

    @property
    def algorithm(self) -> str:
        return self._identity.algorithm

    @property
    def key_id(self) -> str:
        return self._identity.key_id

    # -- operations ------------------------------------------------------
    def sign(self, manifest: dict[str, Any]) -> dict[str, Any]:
        """Seal ``manifest``. Raises RuntimeError when signing is unavailable.

        Delegates to :func:`evidence_signing.seal_manifest` so there is exactly
        ONE implementation of "what bytes get MACed with which key".
        """
        seal = evidence_signing.seal_manifest(manifest)
        # Carry the truthful assurance block INTO the seal so a downloaded
        # manifest.sig states what kind of authority produced it, and an offline
        # auditor is never left to assume hardware custody.
        seal['signer'] = self._identity.as_dict()
        return seal

    def verify(self, manifest: dict[str, Any], seal: dict[str, Any] | None) -> dict[str, Any]:
        """Verify a seal over ``manifest``.

        Returns ``{'status', 'valid', 'reason', 'key_id', 'provider', 'algorithm'}``
        where ``status`` is one of ``valid`` / ``invalid`` / ``unavailable`` /
        ``absent`` and ``valid`` is ``True`` only for ``valid``.

        A schema-2 seal carries BOTH an HMAC and an Ed25519 signature. Both are
        checked and the strongest available truth is reported, but never
        optimistically: if EITHER layer is present and does not verify, the whole
        seal is ``invalid``. A public-key signature that verifies is enough to
        reach ``valid`` even when the HMAC key has rotated out of reach, because
        it is the strictly stronger proof.

        The result additionally carries an ``authenticity`` block stating WHO can
        establish authenticity from this seal. Only a verified Ed25519 signature
        sets ``independently_verifiable``: an HMAC seal is checkable here solely
        because Decoda holds the secret, and a party who holds that secret could
        also forge the seal — so it can never establish authenticity to a third
        party, however cleanly it verifies.
        """
        base = {
            'key_id': str((seal or {}).get('key_id') or '') or None,
            'key_version': str((seal or {}).get('key_version') or '') or None,
            'provider': str((seal or {}).get('key_provider') or '') or None,
            'algorithm': str((seal or {}).get('signature_algorithm') or '') or None,
        }
        public = _verify_public_key_signature(manifest, seal)
        base['authenticity'] = _authenticity_block(public)
        base['public_key_signature'] = public

        if not isinstance(seal, dict) or not str(seal.get('signature') or '').strip():
            return {**base, 'status': SIGNATURE_ABSENT, 'valid': False, 'reason': 'no_signature_in_package'}

        # A tampered or wrong-key public signature is tampering evidence in its
        # own right and is reported as such regardless of the HMAC outcome.
        if public['status'] == SIGNATURE_INVALID:
            return {
                **base, 'status': SIGNATURE_INVALID, 'valid': False,
                'reason': public.get('reason') or 'public_key_signature_mismatch',
            }

        declared_algorithm = str(seal.get('signature_algorithm') or '').strip()
        if declared_algorithm and declared_algorithm != SIGNATURE_ALGORITHM:
            # A seal produced by an algorithm this build cannot check is
            # UNVERIFIABLE, never "invalid" (which would imply tampering) —
            # unless the public-key layer already proved it genuine.
            if public['status'] == SIGNATURE_VALID:
                return {**base, 'status': SIGNATURE_VALID, 'valid': True, 'reason': None}
            return {
                **base, 'status': SIGNATURE_UNAVAILABLE, 'valid': False,
                'reason': 'unsupported_signature_algorithm',
            }

        secret = _verification_key(version=str(seal.get('key_version') or '') or None)
        if secret is None:
            if public['status'] == SIGNATURE_VALID:
                return {**base, 'status': SIGNATURE_VALID, 'valid': True, 'reason': None}
            return {
                **base, 'status': SIGNATURE_UNAVAILABLE, 'valid': False,
                'reason': 'verification_key_unavailable',
            }
        canonical = evidence_signing.canonical_json(manifest)
        expected = hmac.new(secret, canonical, 'sha256').hexdigest()
        actual = str(seal.get('signature') or '')
        if hmac.compare_digest(expected.encode(), actual.encode()):
            return {**base, 'status': SIGNATURE_VALID, 'valid': True, 'reason': None}
        return {**base, 'status': SIGNATURE_INVALID, 'valid': False, 'reason': 'signature_mismatch'}


def public_key_signatures(seal: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The Ed25519 signature documents a seal carries, if any.

    Schema-1 (legacy, HMAC-only) seals have none, which is a truthful fact about
    the package rather than an error. Never raises on a malformed seal.
    """
    if not isinstance(seal, dict):
        return []
    entries = seal.get('signatures')
    if not isinstance(entries, list):
        return []
    return [entry for entry in entries if isinstance(entry, dict)]


def _verify_public_key_signature(
    manifest: dict[str, Any],
    seal: dict[str, Any] | None,
) -> dict[str, Any]:
    """Check the seal's Ed25519 signature against the PUBLISHED public key.

    Uses public key material only — this path cannot sign, and a compromise of
    it cannot forge evidence.

    ``absent``      the seal carries no public-key signature (every legacy
                    package, and any deployment with no Ed25519 key provisioned).
    ``unavailable`` a signature is present but this deployment does not publish
                    the key it names, so it could not be checked here. That is
                    NOT tampering, and an offline auditor holding the right
                    keyring may still verify it.
    ``invalid``     the signature is present and does NOT verify.
    ``valid``       the signature verifies against the published public key.

    The digest it verifies against is RECOMPUTED from the manifest body, never
    read from the seal's own ``signed_manifest_sha256`` — a seal that names its
    own digest could otherwise authenticate a manifest it does not describe.
    """
    entries = public_key_signatures(seal)
    if not entries:
        return {'status': SIGNATURE_ABSENT, 'valid': False, 'reason': 'no_public_key_signature',
                'algorithm': None, 'key_id': None}
    document = entries[0]
    key_id = str(document.get('key_id') or '') or None
    algorithm = str(document.get('algorithm') or '') or None
    computed_digest = evidence_signing._sha256_hex(
        evidence_signing.canonical_json({k: v for k, v in manifest.items() if k != 'manifest_sha256'})
    )
    try:
        from services.api.app import evidence_ed25519

        if algorithm != evidence_ed25519.ALGORITHM:
            return {'status': SIGNATURE_UNAVAILABLE, 'valid': False,
                    'reason': 'unsupported_public_key_algorithm',
                    'algorithm': algorithm, 'key_id': key_id}
        public_key = evidence_ed25519.public_key_for(key_id or '')
        if not public_key:
            return {'status': SIGNATURE_UNAVAILABLE, 'valid': False,
                    'reason': 'public_key_not_published', 'algorithm': algorithm, 'key_id': key_id}
        ok = evidence_ed25519.verify_signature_document(document, computed_digest, public_key)
    except Exception:  # noqa: BLE001 - a verification-side fault is never "tampered"
        _log.exception('evidence_public_key_verification_error key_id=%s', key_id)
        return {'status': SIGNATURE_UNAVAILABLE, 'valid': False,
                'reason': 'public_key_verification_error', 'algorithm': algorithm, 'key_id': key_id}
    if ok:
        return {'status': SIGNATURE_VALID, 'valid': True, 'reason': None,
                'algorithm': algorithm, 'key_id': key_id}
    return {'status': SIGNATURE_INVALID, 'valid': False, 'reason': 'public_key_signature_mismatch',
            'algorithm': algorithm, 'key_id': key_id}


def _authenticity_block(public: dict[str, Any]) -> dict[str, Any]:
    """Who can establish authenticity from this seal, stated without euphemism.

    ``independently_verifiable`` is True ONLY for a verified public-key
    signature. A valid HMAC never sets it: Decoda holds that secret, so Decoda
    could have produced any seal it checks, and a customer cannot be given the
    secret without being given the ability to forge.
    """
    if public['status'] == SIGNATURE_VALID:
        return {
            'method': AUTHENTICITY_PUBLIC_KEY,
            'status': 'verified',
            'independently_verifiable': True,
            'algorithm': public.get('algorithm'),
            'key_id': public.get('key_id'),
            'detail': 'Signed with Decoda\'s Ed25519 evidence key and verifiable offline with the published public key.',
        }
    if public['status'] == SIGNATURE_INVALID:
        return {
            'method': AUTHENTICITY_PUBLIC_KEY,
            'status': 'failed',
            'independently_verifiable': False,
            'algorithm': public.get('algorithm'),
            'key_id': public.get('key_id'),
            'detail': 'The public-key signature on this package does not verify.',
        }
    if public['status'] == SIGNATURE_UNAVAILABLE:
        return {
            'method': AUTHENTICITY_PUBLIC_KEY,
            'status': 'unavailable',
            'independently_verifiable': False,
            'algorithm': public.get('algorithm'),
            'key_id': public.get('key_id'),
            'detail': 'This package carries a public-key signature that could not be checked here.',
        }
    return {
        'method': AUTHENTICITY_SHARED_SECRET,
        'status': 'unavailable',
        'independently_verifiable': False,
        'algorithm': SIGNATURE_ALGORITHM,
        'key_id': None,
        'detail': (
            'This package carries only a shared-secret HMAC seal. It is tamper-evident to Decoda, '
            'but its authenticity cannot be independently verified by a third party.'
        ),
    }


def _identity_from_environment() -> SignerIdentity:
    """Resolve the ACTIVE signer identity from configuration only. No secrets."""
    key_status = evidence_signing.signing_key_status()
    configured = bool(key_status.get('configured'))
    strong = bool(key_status.get('strong'))
    provider = managed_key_provider() if configured else 'dev_fallback'
    if configured:
        key_id = str(key_status.get('key_id') or 'env-default')
        key_version = str(key_status.get('key_version') or 'env-current')
    else:
        key_id = 'decoda-evidence-dev-fallback'
        key_version = 'dev-fallback'

    warning: str | None = None
    if not configured:
        warning = (
            'Development fallback signing key. Seals produced with it are NOT valid '
            'for regulatory, legal, or evidentiary purposes.'
        )
    elif not strong:
        warning = str(key_status.get('error') or 'A known-weak evidence signing key is configured.')

    # Public-key signing is reported from what is actually PROVISIONED, never
    # from the fact that this build supports it.
    public_key_signing = False
    public_key_algorithm: str | None = None
    public_key_id: str | None = None
    try:
        from services.api.app import evidence_ed25519

        if evidence_ed25519.signing_available():
            public_key_signing = True
            public_key_algorithm = evidence_ed25519.ALGORITHM
            public_key_id = evidence_ed25519.signing_key_id()
    except Exception:  # noqa: BLE001 - an unusable key is reported as "not available"
        _log.warning('evidence_ed25519_identity_unavailable')

    return SignerIdentity(
        provider=provider,
        algorithm=SIGNATURE_ALGORITHM,
        key_id=key_id,
        key_version=key_version,
        # Signing happens in-process with fetched key material for BOTH the HMAC
        # seal and the Ed25519 signature. Ed25519 makes authenticity
        # independently verifiable; it does not make the key hardware-custodied,
        # so this stays False and the UI must never print "HSM" from it.
        hardware_backed=False,
        assurance=ASSURANCE_PUBLIC_KEY_SIGNATURE if public_key_signing else ASSURANCE_SHARED_SECRET_HMAC,
        key_custody=CUSTODY_APPLICATION_MEMORY,
        production_grade=configured and strong,
        warning=warning,
        public_key_signing=public_key_signing,
        public_key_algorithm=public_key_algorithm,
        public_key_id=public_key_id,
    )


def resolve_manifest_signer() -> EvidenceManifestSigner:
    """The signer for the current environment."""
    return EvidenceManifestSigner(_identity_from_environment())


def signer_status() -> dict[str, Any]:
    """Truthful, non-secret signer readiness for API responses and health checks.

    ``signing_available`` says a seal can be PRODUCED. ``hardware_backed`` says
    whether the product may claim HSM/KMS custody — it is False for every
    provider in this build. ``enforcement`` mirrors the managed-key policy so an
    operator can see why a provider is or is not permitted.
    """
    identity = _identity_from_environment()
    return {
        **identity.as_dict(),
        'signing_available': evidence_signing.signing_available(),
        'enforcement': managed_key_enforcement_mode(),
        # Explicit so no caller has to infer it from `hardware_backed`.
        'hsm_backed': False,
        'kms_backed': False,
        # Whether NEW packages can be given a signature a third party can verify
        # with public key material alone.
        'public_key_signing_available': identity.public_key_signing,
    }
