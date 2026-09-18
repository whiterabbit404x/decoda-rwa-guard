"""Offline verification of a Decoda RWA Guard evidence package.

This module is DELIBERATELY standalone. It imports nothing from the Decoda
application: no FastAPI, no database driver, no Redis, no AWS SDK, no Decoda
settings, no Decoda environment variables and no Decoda secret. It reads one ZIP
file and, optionally, one public keyring file. Nothing else. It never writes,
never extracts to disk, never opens a socket, and cannot sign anything — a
compromise of this verifier yields no ability to forge evidence.

What it proves, stated exactly
------------------------------
INTEGRITY
    Every artifact in the package still hashes to the SHA-256 the manifest
    records, at the size it records; the manifest still hashes to its own
    ``manifest_sha256``; and the Merkle root recomputes from the artifact set.
    Proven from the package alone — no key of any kind.

AUTHENTICITY
    The manifest digest was signed by the holder of Decoda's Ed25519 private
    signing key, checked here with PUBLIC key material only.

    Reported ``unavailable`` — never ``verified`` — when the package carries
    only the legacy shared-secret HMAC seal, when no keyring was supplied, or
    when the keyring does not contain the key the signature names. A third party
    cannot verify an HMAC seal without being handed a secret that would also let
    them forge one, so an HMAC-only package is NEVER reported as authentic here.

AUDIT LINKAGE
    Whether the manifest records an anchor into Decoda's server-side audit
    chain. ``present`` means the value is there and is covered by the manifest
    hash and signature. It is never reported ``verified``: verifying the chain
    itself needs the chain, which a single package does not contain.

These three are reported SEPARATELY and are never collapsed into one badge.

What it does not prove
----------------------
That the underlying events were truthful when ingested, that a chain or RPC
provider reported honestly, that an off-chain system was correct, or that
Decoda's whole historical audit chain is intact.

Untrusted input
---------------
The package is treated as hostile: path traversal, absolute paths, symlinks,
duplicate names, decompression bombs, oversized manifests, duplicate JSON keys
and malformed archives are all rejected before any content is used.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import zipfile
from dataclasses import dataclass, field
from typing import Any, Iterable

__all__ = [
    'VerificationReport',
    'canonical_json',
    'compute_merkle_root',
    'leaf_hash',
    'load_keyring',
    'signing_payload',
    'verify_package',
]

# ── The format contract, restated here so this file is self-sufficient ──────

#: Domain separation tag for an evidence manifest signature.
SIGNING_DOMAIN = b'DECODA-EVIDENCE-MANIFEST-V1'
#: Signature document layout this verifier understands.
SIGNATURE_FORMAT = 'decoda-evidence-signature-v1'
#: Merkle construction identifier recorded in every schema-2.0 manifest.
MERKLE_SCHEME = 'decoda-merkle-v1'
#: Keyring document schema this verifier understands.
KEYRING_SCHEMA_VERSION = 1

_LEAF_PREFIX = b'\x00'
_NODE_PREFIX = b'\x01'
_FIELD_SEPARATOR = b'\x1f'

# ── Resource limits. A verifier that can be DoSed by its input is not safe ──

MAX_ENTRIES = 10_000
MAX_TOTAL_UNCOMPRESSED = 512 * 1024 * 1024
MAX_MEMBER_UNCOMPRESSED = 128 * 1024 * 1024
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_KEYRING_BYTES = 1024 * 1024
#: Compression-ratio ceiling, applied only above a floor where a high ratio is
#: meaningful (small JSON legitimately compresses very well).
MAX_COMPRESSION_RATIO = 1_000
_RATIO_FLOOR_BYTES = 64 * 1024

#: Archive members that are package furniture rather than manifest-listed
#: evidence. Anything under ``artifacts/`` that is NOT in the manifest is an
#: integrity failure; anything else unrecognized is reported but not fatal,
#: because the manifest makes no claim about it.
_KNOWN_NON_ARTIFACT_FILES = frozenset({
    'manifest.json', 'manifest.sig', 'seal.json', 'verification.json', 'VERIFY.md',
    'reports/investigation.md', 'reports/investigation.pdf',
    'verification/README.txt', 'verification/signing-key.json',
    'verification/decoda-evidence-keys.json',
})

_HEX_DIGITS = frozenset('0123456789abcdef')


class PackageError(Exception):
    """The package could not be read at all. Distinct from "failed verification"."""


# ── Canonical JSON — byte-identical to the generator's serializer ───────────

def canonical_json(value: Any) -> bytes:
    """Deterministic JSON bytes, exactly as Decoda's generator produces them.

    ``sort_keys=True`` (recursive), compact ``(',', ':')`` separators,
    ``ensure_ascii=True``, UTF-8, no trailing newline and no BOM.

    ``ensure_ascii`` is TRUE. This is not a stylistic choice to re-decide here:
    it is what ``services/api/app/evidence_signing.canonical_json`` emits, and a
    single differing byte changes every digest. The cross-implementation test
    vectors pin it.
    """
    return json.dumps(
        value, sort_keys=True, separators=(',', ':'), ensure_ascii=True,
    ).encode('utf-8')


def _no_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """JSON object hook that REJECTS duplicate keys.

    Python's default keeps the last duplicate silently. A manifest with two
    ``merkle_root`` keys would then verify against whichever one the parser
    happened to keep while a different reader saw the other — so it is refused.
    """
    seen: set[str] = set()
    for key, _ in pairs:
        if key in seen:
            raise PackageError(f'duplicate JSON key in document: {key!r}')
        seen.add(key)
    return dict(pairs)


def _parse_json(payload: bytes, *, what: str) -> Any:
    try:
        return json.loads(payload.decode('utf-8'), object_pairs_hook=_no_duplicate_keys)
    except UnicodeDecodeError as exc:
        raise PackageError(f'{what} is not valid UTF-8') from exc
    except json.JSONDecodeError as exc:
        raise PackageError(f'{what} is not valid JSON: {exc.msg}') from exc


# ── Merkle — reimplemented from the published spec, not imported ────────────

def _normalize_digest(value: Any) -> str | None:
    digest = str(value or '').strip().lower()
    if digest.startswith('sha256:'):
        digest = digest[len('sha256:'):]
    if len(digest) != 64 or not set(digest) <= _HEX_DIGITS:
        return None
    return digest


def leaf_hash(path: str, sha256_hex: str) -> bytes:
    """``SHA256(0x00 || utf8(path) || 0x1F || ascii(lower(sha256_hex)))``.

    The ``0x00`` prefix separates the leaf domain from the ``0x01`` internal-node
    domain, so a 64-byte pair of child digests can never be reinterpreted as a
    leaf preimage. The ``0x1F`` unit separator stops ``("ab", "cd…")`` and
    ``("a", "bcd…")`` colliding.
    """
    digest = _normalize_digest(sha256_hex)
    if digest is None:
        raise PackageError(f'invalid SHA-256 digest for artifact {path!r}')
    return hashlib.sha256(
        _LEAF_PREFIX + str(path).encode('utf-8') + _FIELD_SEPARATOR + digest.encode('ascii')
    ).digest()


def compute_merkle_root(entries: Iterable[tuple[str, str]]) -> str | None:
    """Merkle root over ``(logical_path, sha256_hex)``, or ``None`` when empty.

    Leaves are sorted by the UTF-8 BYTES of the path, ascending. A level with an
    odd node count PROMOTES its last node unchanged — it is NOT duplicated,
    because duplication makes two distinct leaf multisets share a root
    (CVE-2012-2459). An empty artifact set has no root at all, never a
    placeholder digest.
    """
    seen: set[str] = set()
    leaves: list[tuple[str, str]] = []
    for path, digest in entries:
        path = str(path or '')
        if not path:
            raise PackageError('manifest contains an artifact entry with no path')
        if path in seen:
            raise PackageError(f'manifest contains duplicate artifact path {path!r}')
        seen.add(path)
        leaves.append((path, digest))
    if not leaves:
        return None
    leaves.sort(key=lambda item: item[0].encode('utf-8'))
    level = [leaf_hash(path, digest) for path, digest in leaves]
    while len(level) > 1:
        nxt: list[bytes] = []
        for index in range(0, len(level) - 1, 2):
            nxt.append(hashlib.sha256(_NODE_PREFIX + level[index] + level[index + 1]).digest())
        if len(level) % 2 == 1:
            nxt.append(level[-1])
        level = nxt
    return level[0].hex()


# ── Signature payload ──────────────────────────────────────────────────────

def signing_payload(manifest_sha256: str) -> bytes:
    """``DECODA-EVIDENCE-MANIFEST-V1`` || ``0x00`` || raw 32-byte manifest digest.

    Fixed 60 bytes. No JSON is signed, so there is no serializer to agree on and
    no delimiter to confuse. The digest passed in must be the one THIS verifier
    recomputed from the manifest bytes — never the value the seal claims.
    """
    digest = _normalize_digest(manifest_sha256)
    if digest is None:
        raise PackageError('manifest_sha256 is not a valid SHA-256 digest')
    return SIGNING_DOMAIN + b'\x00' + bytes.fromhex(digest)


# ── Safe archive access ────────────────────────────────────────────────────

def _reject_unsafe_name(name: str) -> None:
    """Refuse any entry name that could escape a directory if ever extracted.

    This verifier never extracts, but it must not normalize a hostile name into
    a safe-looking one either: a manifest path that only *appears* to match an
    archive entry would let a crafted package pass while a different reader saw
    different bytes.
    """
    if not name or name.endswith('/'):
        return
    if '\\' in name:
        raise PackageError(f'unsafe archive entry (backslash): {name!r}')
    if name.startswith('/') or name.startswith('~'):
        raise PackageError(f'unsafe archive entry (absolute path): {name!r}')
    if len(name) > 1 and name[1] == ':':
        raise PackageError(f'unsafe archive entry (drive letter): {name!r}')
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in name):
        raise PackageError('unsafe archive entry (control characters in name)')
    parts = name.split('/')
    if any(part in {'.', '..'} for part in parts):
        raise PackageError(f'unsafe archive entry (path traversal): {name!r}')


@dataclass
class _Archive:
    """A validated view of the ZIP. Never extracts; hashes are streamed."""

    zipfile: zipfile.ZipFile
    infos: dict[str, zipfile.ZipInfo]
    root: str
    budget: int = MAX_TOTAL_UNCOMPRESSED

    def read(self, name: str, *, limit: int = MAX_MEMBER_UNCOMPRESSED) -> bytes:
        payload = self._stream(name, limit=limit)[1]
        if payload is None:  # pragma: no cover - defensive
            raise PackageError(f'could not read archive entry {name!r}')
        return payload

    def sha256_and_size(self, name: str) -> tuple[str, int]:
        digest, _ = self._stream(name, limit=MAX_MEMBER_UNCOMPRESSED, collect=False)
        return digest

    def _stream(
        self, name: str, *, limit: int, collect: bool = True,
    ) -> tuple[tuple[str, int], bytes | None]:
        info = self.infos[name]
        # A declared size is a claim by the attacker, so it is checked BEFORE
        # decompression and the real byte count is checked again while reading.
        if info.file_size > limit:
            raise PackageError(f'archive entry {name!r} declares an oversized payload')
        if (
            info.compress_size > _RATIO_FLOOR_BYTES
            and info.file_size > info.compress_size * MAX_COMPRESSION_RATIO
        ):
            raise PackageError(f'archive entry {name!r} has an implausible compression ratio')
        digest = hashlib.sha256()
        total = 0
        chunks: list[bytes] = []
        try:
            with self.zipfile.open(info, 'r') as handle:
                while True:
                    chunk = handle.read(262_144)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > limit:
                        raise PackageError(f'archive entry {name!r} exceeded its size limit while reading')
                    self.budget -= len(chunk)
                    if self.budget < 0:
                        raise PackageError('package exceeded the total uncompressed size limit')
                    digest.update(chunk)
                    if collect:
                        chunks.append(chunk)
        except (zipfile.BadZipFile, EOFError, OSError) as exc:
            raise PackageError(f'archive entry {name!r} could not be decompressed: {exc}') from exc
        return (digest.hexdigest(), total), (b''.join(chunks) if collect else None)


def _open_archive(path: str) -> _Archive:
    if not os.path.isfile(path):
        raise PackageError(f'no such file: {path}')
    try:
        archive = zipfile.ZipFile(path, 'r')
    except zipfile.BadZipFile as exc:
        raise PackageError(f'not a readable ZIP archive: {exc}') from exc
    except OSError as exc:
        raise PackageError(f'could not open {path}: {exc}') from exc

    names = archive.namelist()
    if len(names) > MAX_ENTRIES:
        archive.close()
        raise PackageError(f'package contains too many entries ({len(names)})')
    if len(names) != len(set(names)):
        archive.close()
        duplicates = sorted({name for name in names if names.count(name) > 1})
        raise PackageError(f'package contains duplicate entry names: {duplicates[:5]}')

    infos: dict[str, zipfile.ZipInfo] = {}
    declared_total = 0
    for info in archive.infolist():
        try:
            _reject_unsafe_name(info.filename)
        except PackageError:
            archive.close()
            raise
        if info.filename.endswith('/'):
            continue
        # High 4 bits of the UNIX mode in external_attr: 0xA000 marks a symlink.
        if (info.external_attr >> 16) & 0xF000 == 0xA000:
            archive.close()
            raise PackageError(f'package contains a symbolic link entry: {info.filename!r}')
        # Bomb defence at OPEN time, not read time. An oversized or absurdly
        # compressed member is refused even when nothing would have read it —
        # an entry that is never read is exactly where one would be hidden.
        if info.file_size > MAX_MEMBER_UNCOMPRESSED:
            archive.close()
            raise PackageError(f'archive entry {info.filename!r} declares an oversized payload')
        if (
            info.compress_size > _RATIO_FLOOR_BYTES
            and info.file_size > info.compress_size * MAX_COMPRESSION_RATIO
        ):
            archive.close()
            raise PackageError(f'archive entry {info.filename!r} has an implausible compression ratio')
        declared_total += info.file_size
        infos[info.filename] = info
    if declared_total > MAX_TOTAL_UNCOMPRESSED:
        archive.close()
        raise PackageError('package declares more uncompressed data than the verifier will process')

    roots = {
        name.rsplit('/manifest.json', 1)[0]
        for name in infos
        if name == 'manifest.json' or name.endswith('/manifest.json')
    }
    roots = {root for root in roots if '/' not in root}
    if not roots:
        archive.close()
        raise PackageError('package does not contain a manifest.json')
    if len(roots) > 1:
        archive.close()
        raise PackageError(f'package contains more than one manifest.json: {sorted(roots)}')
    return _Archive(zipfile=archive, infos=infos, root=roots.pop())


def _entry(archive: _Archive, relative: str) -> str:
    return f'{archive.root}/{relative}' if archive.root else relative


# ── Keyring ────────────────────────────────────────────────────────────────

def load_keyring(path: str) -> dict[str, dict[str, Any]]:
    """Read a public verification keyring into ``{key_id: entry}``.

    Accepts the published keyring document, or a bare list of key entries. A
    malformed or oversized file raises rather than silently yielding an empty
    keyring, because "no keys" and "unreadable keys" must not both quietly
    become "authenticity unavailable".
    """
    if not os.path.isfile(path):
        raise PackageError(f'no such keyring file: {path}')
    if os.path.getsize(path) > MAX_KEYRING_BYTES:
        raise PackageError('keyring file is implausibly large')
    with open(path, 'rb') as handle:
        document = _parse_json(handle.read(), what='keyring')
    if isinstance(document, dict):
        schema = document.get('schema_version', KEYRING_SCHEMA_VERSION)
        if not isinstance(schema, int) or schema > KEYRING_SCHEMA_VERSION:
            raise PackageError(f'unsupported keyring schema_version: {schema!r}')
        entries = document.get('keys')
    else:
        entries = document
    if not isinstance(entries, list):
        raise PackageError('keyring does not contain a "keys" list')
    keys: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise PackageError('keyring contains a malformed key entry')
        key_id = str(entry.get('key_id') or '').strip()
        material = str(entry.get('public_key') or '').strip()
        if not key_id or not material:
            raise PackageError('keyring contains a key entry without key_id/public_key')
        if key_id in keys:
            raise PackageError(f'keyring contains duplicate key_id {key_id!r}')
        keys[key_id] = entry
    return keys


def _decode_public_key(material: str) -> bytes:
    """Decode a keyring public key to its raw 32 bytes.

    Accepts base64 of the raw key, or a PEM SubjectPublicKeyInfo block.
    """
    text = str(material or '').strip()
    if text.startswith('-----BEGIN'):
        from cryptography.hazmat.primitives import serialization

        public = serialization.load_pem_public_key(text.encode('ascii'))
        return public.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    try:
        raw = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise PackageError('public key is not valid base64') from exc
    if len(raw) != 32:
        raise PackageError(f'Ed25519 public key must be 32 bytes (got {len(raw)})')
    return raw


def _verify_ed25519(public_key: bytes, signature: bytes, payload: bytes) -> bool:
    """Verify with a maintained library. This module never implements Ed25519."""
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric import ed25519

    try:
        ed25519.Ed25519PublicKey.from_public_bytes(public_key).verify(signature, payload)
    except (InvalidSignature, ValueError):
        return False
    return True


# ── Report ─────────────────────────────────────────────────────────────────

INTEGRITY_VERIFIED = 'verified'
INTEGRITY_FAILED = 'failed'

AUTHENTICITY_VERIFIED = 'verified'
AUTHENTICITY_UNAVAILABLE = 'unavailable'
AUTHENTICITY_FAILED = 'failed'

LINKAGE_PRESENT = 'present'
LINKAGE_VERIFIED = 'verified'
LINKAGE_UNAVAILABLE = 'unavailable'


@dataclass
class VerificationReport:
    """The verdict. Integrity, authenticity and audit linkage stay separate."""

    package_path: str
    package_id: str | None = None
    package_number: str | None = None
    manifest_schema_version: str | None = None

    integrity: str = INTEGRITY_FAILED
    authenticity: str = AUTHENTICITY_UNAVAILABLE
    audit_linkage: str = LINKAGE_UNAVAILABLE

    manifest_parsed: bool = False
    manifest_hash_valid: bool | None = None
    manifest_sha256: str | None = None
    merkle_root_valid: bool | None = None
    merkle_root: str | None = None

    files_total: int = 0
    files_verified: int = 0
    files_failed: int = 0
    files_missing: int = 0
    files_unexpected: int = 0

    #: Logical paths only — never file contents.
    failed_paths: list[str] = field(default_factory=list)
    missing_paths: list[str] = field(default_factory=list)
    unexpected_paths: list[str] = field(default_factory=list)
    extra_paths: list[str] = field(default_factory=list)

    seal_present: bool = False
    seal_schema_version: int | None = None
    signature_algorithm: str | None = None
    signature_key_id: str | None = None
    key_source: str = 'none'
    authenticity_reason: str | None = None
    audit_anchor: str | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Fully verified: integrity AND independently-verified authenticity."""
        return self.integrity == INTEGRITY_VERIFIED and self.authenticity == AUTHENTICITY_VERIFIED

    @property
    def failed(self) -> bool:
        """Something actively did not verify. Distinct from "could not check"."""
        return self.integrity == INTEGRITY_FAILED or self.authenticity == AUTHENTICITY_FAILED

    def as_json(self) -> dict[str, Any]:
        """Machine-readable result. Carries no evidence content — paths only."""
        return {
            'package_id': self.package_id,
            'package_number': self.package_number,
            'manifest_schema_version': self.manifest_schema_version,
            'integrity': self.integrity,
            'authenticity': self.authenticity,
            'audit_linkage': self.audit_linkage,
            'manifest': 'verified' if self.manifest_hash_valid else (
                'failed' if self.manifest_hash_valid is False else 'unavailable'
            ),
            'manifest_sha256': self.manifest_sha256,
            'merkle_root': 'verified' if self.merkle_root_valid else (
                'failed' if self.merkle_root_valid is False else 'unavailable'
            ),
            'merkle_root_value': self.merkle_root,
            'files': {
                'total': self.files_total,
                'verified': self.files_verified,
                'failed': self.files_failed,
                'missing': self.files_missing,
                'unexpected': self.files_unexpected,
                'failed_paths': self.failed_paths,
                'missing_paths': self.missing_paths,
                'unexpected_paths': self.unexpected_paths,
            },
            'signature': {
                'present': self.seal_present,
                'seal_schema_version': self.seal_schema_version,
                'algorithm': self.signature_algorithm,
                'key_id': self.signature_key_id,
                'key_source': self.key_source,
                'reason': self.authenticity_reason,
            },
            'audit_anchor_present': bool(self.audit_anchor),
            'errors': self.errors,
        }


# ── Verification ───────────────────────────────────────────────────────────

def verify_package(
    package_path: str,
    *,
    keyring_path: str | None = None,
    public_key: str | None = None,
    public_key_id: str | None = None,
    allow_bundled_keyring: bool = False,
) -> VerificationReport:
    """Verify one evidence package offline and return the report.

    ``keyring_path`` / ``public_key`` supply EXTERNALLY trusted key material.
    ``allow_bundled_keyring`` opts in to the keyring carried inside the package —
    off by default, and reported as ``key_source='bundled'`` when used, because a
    key shipped inside the package it verifies proves nothing on its own.

    Raises :class:`PackageError` only when the package cannot be read at all.
    A package that reads but does not verify returns a report, never an
    exception: "unreadable" and "invalid" are different answers.
    """
    report = VerificationReport(package_path=package_path)
    archive = _open_archive(package_path)
    try:
        _verify_into(
            archive, report,
            keyring_path=keyring_path,
            public_key=public_key,
            public_key_id=public_key_id,
            allow_bundled_keyring=allow_bundled_keyring,
        )
    finally:
        archive.zipfile.close()
    return report


def _verify_into(
    archive: _Archive,
    report: VerificationReport,
    *,
    keyring_path: str | None,
    public_key: str | None,
    public_key_id: str | None,
    allow_bundled_keyring: bool,
) -> None:
    report.package_number = archive.root or None

    # ── 1. Manifest ────────────────────────────────────────────────────────
    manifest_entry = _entry(archive, 'manifest.json')
    manifest_bytes = archive.read(manifest_entry, limit=MAX_MANIFEST_BYTES)
    manifest = _parse_json(manifest_bytes, what='manifest.json')
    if not isinstance(manifest, dict):
        raise PackageError('manifest.json is not a JSON object')
    report.manifest_parsed = True
    report.package_id = str(manifest.get('package_id') or manifest.get('export_id') or '') or None
    report.manifest_schema_version = str(
        manifest.get('schema_version') or manifest.get('manifest_version') or ''
    ) or None

    declared_manifest_sha = str(manifest.get('manifest_sha256') or '').strip().lower()
    computed_manifest_sha = hashlib.sha256(
        canonical_json({k: v for k, v in manifest.items() if k != 'manifest_sha256'})
    ).hexdigest()
    report.manifest_sha256 = computed_manifest_sha
    report.manifest_hash_valid = bool(declared_manifest_sha) and declared_manifest_sha == computed_manifest_sha
    if not report.manifest_hash_valid:
        report.errors.append(
            'manifest_sha256 does not match the canonical hash of the manifest body'
            if declared_manifest_sha else 'manifest.json carries no manifest_sha256'
        )

    # ── 2. Per-file hashes, streamed straight out of the archive ───────────
    files = manifest.get('files')
    if not isinstance(files, list):
        raise PackageError('manifest.json has no "files" list')

    prefix = _entry(archive, 'artifacts/')
    archive_artifacts = {name for name in archive.infos if name.startswith(prefix)}
    matched_entries: set[str] = set()
    seen_paths: set[str] = set()
    merkle_entries: list[tuple[str, str]] = []

    for raw_entry in files:
        if not isinstance(raw_entry, dict):
            raise PackageError('manifest "files" contains a non-object entry')
        logical_path = str(raw_entry.get('path') or '')
        if not logical_path:
            raise PackageError('manifest "files" contains an entry with no path')
        if logical_path in seen_paths:
            raise PackageError(f'manifest lists duplicate artifact path {logical_path!r}')
        seen_paths.add(logical_path)
        _reject_unsafe_name(logical_path)
        report.files_total += 1
        merkle_entries.append((logical_path, str(raw_entry.get('sha256') or '')))

        # The archive files artifacts under artifacts/<domain>/<logical path>.
        # Matching by SUFFIX keeps this verifier independent of Decoda's domain
        # table; requiring EXACTLY ONE match means a package cannot ship two
        # candidate files for one manifest entry and have the verifier choose.
        candidates = sorted(
            name for name in archive_artifacts
            if name == f'{prefix}{logical_path}' or name.endswith(f'/{logical_path}')
        )
        if len(candidates) > 1:
            report.files_failed += 1
            report.failed_paths.append(logical_path)
            report.errors.append(f'{logical_path}: more than one archive entry matches this manifest path')
            matched_entries.update(candidates)
            continue
        if not candidates:
            report.files_missing += 1
            report.missing_paths.append(logical_path)
            continue

        name = candidates[0]
        matched_entries.add(name)
        actual_sha, actual_size = archive.sha256_and_size(name)
        expected_sha = _normalize_digest(raw_entry.get('sha256'))
        expected_size = raw_entry.get('size_bytes')
        if expected_sha is None:
            report.files_failed += 1
            report.failed_paths.append(logical_path)
            report.errors.append(f'{logical_path}: manifest entry has no valid SHA-256')
            continue
        if actual_sha != expected_sha:
            report.files_failed += 1
            report.failed_paths.append(logical_path)
            report.errors.append(f'{logical_path}: SHA-256 mismatch')
            continue
        if isinstance(expected_size, int) and not isinstance(expected_size, bool) and expected_size != actual_size:
            report.files_failed += 1
            report.failed_paths.append(logical_path)
            report.errors.append(f'{logical_path}: size mismatch (expected {expected_size}, found {actual_size})')
            continue
        report.files_verified += 1

    unexpected = sorted(archive_artifacts - matched_entries)
    report.files_unexpected = len(unexpected)
    report.unexpected_paths = [name[len(prefix):] for name in unexpected]
    for path in report.unexpected_paths:
        report.errors.append(f'{path}: present under artifacts/ but not listed in the manifest')

    known = {_entry(archive, name) for name in _KNOWN_NON_ARTIFACT_FILES}
    report.extra_paths = sorted(
        name for name in archive.infos
        if name not in archive_artifacts and name not in known
    )

    # ── 3. Merkle root ─────────────────────────────────────────────────────
    declared_root = str(manifest.get('merkle_root') or '').strip().lower() or None
    scheme = str(manifest.get('merkle_scheme') or '').strip()
    if declared_root is None:
        # A schema-1.0 manifest never sealed a root. Nothing to check, and the
        # absence is NOT a failure — but it is also not a pass, so it stays None.
        report.merkle_root_valid = None
    elif scheme and scheme != MERKLE_SCHEME:
        report.merkle_root_valid = None
        report.errors.append(f'unknown Merkle scheme {scheme!r}; the root could not be recomputed')
    else:
        computed_root = compute_merkle_root(merkle_entries)
        report.merkle_root = computed_root
        report.merkle_root_valid = computed_root is not None and computed_root == declared_root
        if not report.merkle_root_valid:
            report.errors.append('the recomputed Merkle root does not match the sealed merkle_root')

    # ── 4. Integrity verdict ───────────────────────────────────────────────
    report.integrity = INTEGRITY_VERIFIED if (
        report.manifest_hash_valid
        and report.files_total > 0
        and report.files_failed == 0
        and report.files_missing == 0
        and report.files_unexpected == 0
        and report.merkle_root_valid is not False
    ) else INTEGRITY_FAILED

    # ── 5. Audit linkage ───────────────────────────────────────────────────
    anchor = str(manifest.get('previous_audit_anchor_hash') or '').strip()
    report.audit_anchor = anchor or None
    # PRESENT, never VERIFIED: the anchor is covered by the manifest hash and the
    # signature, so it is tamper-evident — but proving the chain it points into
    # requires that chain, which a single package does not carry.
    report.audit_linkage = LINKAGE_PRESENT if anchor else LINKAGE_UNAVAILABLE

    # ── 6. Authenticity ────────────────────────────────────────────────────
    _verify_authenticity(
        archive, report, manifest_sha256=computed_manifest_sha,
        keyring_path=keyring_path, public_key=public_key, public_key_id=public_key_id,
        allow_bundled_keyring=allow_bundled_keyring,
    )


def _read_seal(archive: _Archive) -> dict[str, Any] | None:
    """The seal document, from ``seal.json`` or the equivalent ``manifest.sig``."""
    for candidate in ('seal.json', 'manifest.sig'):
        name = _entry(archive, candidate)
        if name in archive.infos:
            document = _parse_json(archive.read(name, limit=MAX_MANIFEST_BYTES), what=candidate)
            if not isinstance(document, dict):
                raise PackageError(f'{candidate} is not a JSON object')
            return document
    return None


def _resolve_keys(
    archive: _Archive,
    *,
    keyring_path: str | None,
    public_key: str | None,
    public_key_id: str | None,
    allow_bundled_keyring: bool,
) -> tuple[dict[str, dict[str, Any]], str]:
    """Key material to verify against, and where it came from.

    External sources win. The keyring bundled inside the package is used ONLY
    when explicitly allowed, and the source is always reported so an auditor can
    see which trust they actually exercised.
    """
    if public_key:
        key_id = public_key_id or '<--public-key>'
        return {key_id: {'key_id': key_id, 'public_key': public_key, 'status': 'pinned'}}, 'public-key'
    if keyring_path:
        return load_keyring(keyring_path), 'keyring'
    if allow_bundled_keyring:
        name = _entry(archive, 'verification/decoda-evidence-keys.json')
        if name in archive.infos:
            document = _parse_json(
                archive.read(name, limit=MAX_KEYRING_BYTES), what='bundled keyring',
            )
            entries = document.get('keys') if isinstance(document, dict) else None
            keys = {
                str(entry.get('key_id')): entry
                for entry in (entries or [])
                if isinstance(entry, dict) and entry.get('key_id') and entry.get('public_key')
            }
            if keys:
                return keys, 'bundled'
    return {}, 'none'


def _verify_authenticity(
    archive: _Archive,
    report: VerificationReport,
    *,
    manifest_sha256: str,
    keyring_path: str | None,
    public_key: str | None,
    public_key_id: str | None,
    allow_bundled_keyring: bool,
) -> None:
    seal = _read_seal(archive)
    if seal is None:
        report.authenticity = AUTHENTICITY_UNAVAILABLE
        report.authenticity_reason = 'This package carries no seal, so there is no signature to check.'
        return

    report.seal_present = True
    schema = seal.get('schema_version')
    report.seal_schema_version = schema if isinstance(schema, int) else None
    legacy_algorithm = str(seal.get('signature_algorithm') or '').strip() or None

    documents = seal.get('signatures')
    documents = [entry for entry in documents if isinstance(entry, dict)] if isinstance(documents, list) else []
    if not documents:
        # Legacy, HMAC-only. Verifying it would require Decoda's shared secret —
        # which would also let the holder FORGE it. So it is never reported as
        # authentic here, however well-formed it is.
        report.authenticity = AUTHENTICITY_UNAVAILABLE
        report.signature_algorithm = legacy_algorithm
        report.signature_key_id = str(seal.get('key_id') or '') or None
        report.authenticity_reason = (
            f'Legacy {legacy_algorithm or "shared-secret"} seal. Its key is a shared secret held by '
            'Decoda, so a third party cannot verify it without also being able to forge it.'
        )
        return

    document = documents[0]
    report.signature_algorithm = str(document.get('algorithm') or '') or None
    report.signature_key_id = str(document.get('key_id') or '') or None

    signature_format = str(document.get('signature_format') or '')
    if signature_format and signature_format != SIGNATURE_FORMAT:
        report.authenticity = AUTHENTICITY_UNAVAILABLE
        report.authenticity_reason = f'Unsupported signature format {signature_format!r}.'
        return
    if report.signature_algorithm != 'Ed25519':
        report.authenticity = AUTHENTICITY_UNAVAILABLE
        report.authenticity_reason = f'Unsupported signature algorithm {report.signature_algorithm!r}.'
        return
    signed_object = str(document.get('signed_object') or 'manifest_sha256')
    if signed_object != 'manifest_sha256':
        report.authenticity = AUTHENTICITY_UNAVAILABLE
        report.authenticity_reason = f'Unsupported signed object {signed_object!r}.'
        return

    keys, source = _resolve_keys(
        archive,
        keyring_path=keyring_path, public_key=public_key, public_key_id=public_key_id,
        allow_bundled_keyring=allow_bundled_keyring,
    )
    report.key_source = source
    if not keys:
        report.authenticity = AUTHENTICITY_UNAVAILABLE
        report.authenticity_reason = (
            'No public verification key was supplied. Pass --keyring or --public-key with a key '
            'you obtained from Decoda independently of this package.'
        )
        return

    entry = keys.get(report.signature_key_id or '')
    if entry is None:
        report.authenticity = AUTHENTICITY_UNAVAILABLE
        report.authenticity_reason = (
            f'Key id {report.signature_key_id!r} is not in the supplied keyring. '
            'A retired key must stay published for historical evidence to remain verifiable.'
        )
        return

    try:
        key_bytes = _decode_public_key(str(entry.get('public_key') or ''))
        # validate=True so a signature with junk in it is reported as MALFORMED
        # rather than silently decoding to garbage that then "fails to verify".
        signature = base64.b64decode(str(document.get('signature') or '').strip(), validate=True)
        payload = signing_payload(manifest_sha256)
    except (PackageError, binascii.Error, ValueError) as exc:
        # A malformed signature or key fails CLOSED as "failed", not
        # "unavailable": the document claims to carry a signature and does not.
        report.authenticity = AUTHENTICITY_FAILED
        report.authenticity_reason = f'The signature or key material is malformed: {exc}'
        report.errors.append('signature or public key could not be decoded')
        return
    except ImportError:
        report.authenticity = AUTHENTICITY_UNAVAILABLE
        report.authenticity_reason = (
            'The "cryptography" package is required to check Ed25519 signatures. '
            'Install it with: pip install cryptography'
        )
        return

    try:
        ok = _verify_ed25519(key_bytes, signature, payload)
    except ImportError:
        report.authenticity = AUTHENTICITY_UNAVAILABLE
        report.authenticity_reason = (
            'The "cryptography" package is required to check Ed25519 signatures. '
            'Install it with: pip install cryptography'
        )
        return
    if ok:
        report.authenticity = AUTHENTICITY_VERIFIED
        report.authenticity_reason = None
    else:
        report.authenticity = AUTHENTICITY_FAILED
        report.authenticity_reason = 'The Ed25519 signature does not verify against this key.'
        report.errors.append('manifest signature did not verify')
