"""Deterministic SHA-256 Merkle tree over the artifacts of an evidence package.

Why a Merkle root at all
------------------------
The evidence manifest already carries a SHA-256 per artifact plus a canonical
``manifest_sha256`` over the whole manifest body. That proves *the manifest*
describes exactly these bytes. The Merkle root adds an artifact-set commitment
that is independent of manifest field ordering, key naming and metadata: one
32-byte value that changes if ANY packaged artifact changes, is added, removed
or reordered — and that an auditor can recompute from nothing but the list of
``(path, sha256)`` pairs, without trusting our manifest serializer.

The scheme (``decoda-merkle-v1``) — the exact, documented contract
-----------------------------------------------------------------
Hash function
    SHA-256 throughout, on raw bytes. Digests are compared/stored as lowercase
    hex; the tree itself is built over the raw 32-byte digests.

Leaf encoding (domain-separated)
    ``leaf = SHA256(b'\\x00' + utf8(path) + b'\\x1f' + ascii(lower(sha256_hex)))``

    The ``0x00`` prefix separates the leaf domain from the internal-node domain
    (``0x01``), so a 64-byte concatenation of two child digests can never be
    reinterpreted as a leaf preimage (the classic second-preimage attack on
    naive Merkle constructions). ``0x1f`` (ASCII unit separator) delimits the
    path from the digest so ``("ab", "cd…")`` and ``("a", "bcd…")`` cannot
    collide. The path is the artifact's LOGICAL manifest path (e.g.
    ``alerts.json``) — never a storage key, never a filesystem path — so the
    root is independent of where the bytes happen to live.

    Note that the leaf commits to the artifact's CONTENT HASH, not to the
    artifact bytes directly. The manifest's per-file SHA-256 is recomputed from
    the real stored bytes during verification, so a modified artifact fails the
    hash check first and, because its digest feeds this leaf, the root as well.

Canonical leaf ordering
    Leaves are sorted by the UTF-8 BYTES of the logical path, ascending
    (``sorted(..., key=lambda e: e.path.encode('utf-8'))``). This is a total
    order over a set of unique paths, so the root is a pure function of the
    artifact SET — never of database row order, dict insertion order, storage
    listing order, or locale collation. A duplicate path fails CLOSED
    (:class:`MerkleConstructionError`) rather than silently producing a root
    whose meaning depends on which duplicate won.

Internal nodes
    ``node = SHA256(b'\\x01' + left_digest + right_digest)`` over the raw
    32-byte child digests.

Odd node counts
    A level with an odd number of nodes PROMOTES its last node unchanged to the
    next level. It is deliberately NOT duplicated (Bitcoin's approach), because
    duplicating the final node makes two distinct leaf multisets share a root
    (CVE-2012-2459). Promotion keeps the map from artifact set to root
    injective for our fixed, deterministically-ordered leaf sets.

Empty and single-artifact packages
    An EMPTY artifact set has NO root: :func:`compute_merkle_root` returns
    ``None``. It never returns ``SHA256(b'')`` or any other placeholder that
    could be displayed as a real commitment to nothing. A single artifact's root
    IS its leaf hash.

No time, no randomness
    Nothing in this module reads the clock, a random source, a database or any
    environment state. The same ``(path, sha256)`` set always produces the same
    root, in any process, on any host, forever — which is exactly what makes a
    stored root re-checkable years later.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

#: Stable identifier for this construction. Persisted into the manifest so a
#: future scheme change is a NEW identifier and never a silent reinterpretation
#: of an old root.
MERKLE_SCHEME = 'decoda-merkle-v1'

#: The hash algorithm name recorded alongside every root.
HASH_ALGORITHM = 'SHA-256'

_LEAF_PREFIX = b'\x00'
_NODE_PREFIX = b'\x01'
_FIELD_SEPARATOR = b'\x1f'

_HEX_DIGITS = frozenset('0123456789abcdef')


class MerkleConstructionError(ValueError):
    """A Merkle tree could not be built from the supplied artifact entries.

    Carries a SAFE, structural reason code (``duplicate_path``,
    ``invalid_digest``, ``empty_path``) and the offending logical PATH only —
    never artifact contents. Raised instead of returning a root that would be
    ambiguous or meaningless.
    """

    def __init__(self, *, reason: str, path: str | None = None) -> None:
        self.reason = reason
        self.path = path
        super().__init__(f'{reason}' + (f':{path}' if path else ''))


@dataclass(frozen=True)
class MerkleLeaf:
    """One artifact's commitment: its logical manifest path and content digest."""

    path: str
    sha256: str

    @property
    def leaf_hash_hex(self) -> str:
        return leaf_hash(self.path, self.sha256).hex()


@dataclass(frozen=True)
class MerkleTree:
    """A built tree: its root, its canonical leaves, and the leaf count."""

    root: str | None
    leaves: tuple[MerkleLeaf, ...]
    scheme: str = MERKLE_SCHEME
    hash_algorithm: str = HASH_ALGORITHM

    @property
    def leaf_count(self) -> int:
        return len(self.leaves)

    def as_dict(self) -> dict[str, Any]:
        """Non-secret description of the tree, safe for manifests and API responses."""
        return {
            'merkle_root': self.root,
            'merkle_scheme': self.scheme,
            'hash_algorithm': self.hash_algorithm,
            'leaf_count': self.leaf_count,
        }


def _normalize_digest(value: Any, *, path: str) -> str:
    """Lowercase, validated 64-char hex SHA-256, or fail closed.

    A digest that is not a real SHA-256 hex string can never be folded into a
    root — doing so would produce a hex value that LOOKS like a commitment while
    committing to nothing checkable.
    """
    digest = str(value or '').strip().lower()
    if digest.startswith('sha256:'):
        digest = digest[len('sha256:'):]
    if len(digest) != 64 or not set(digest) <= _HEX_DIGITS:
        raise MerkleConstructionError(reason='invalid_digest', path=path)
    return digest


def leaf_preimage(path: str, sha256_hex: str) -> bytes:
    """The exact bytes hashed to produce a leaf. See the module docstring."""
    clean_path = str(path or '')
    if not clean_path:
        raise MerkleConstructionError(reason='empty_path', path=None)
    digest = _normalize_digest(sha256_hex, path=clean_path)
    return _LEAF_PREFIX + clean_path.encode('utf-8') + _FIELD_SEPARATOR + digest.encode('ascii')


def leaf_hash(path: str, sha256_hex: str) -> bytes:
    """SHA-256 of the domain-separated leaf preimage (raw 32 bytes)."""
    return hashlib.sha256(leaf_preimage(path, sha256_hex)).digest()


def node_hash(left: bytes, right: bytes) -> bytes:
    """SHA-256 of the domain-separated internal-node preimage (raw 32 bytes)."""
    return hashlib.sha256(_NODE_PREFIX + left + right).digest()


def canonical_leaves(entries: Iterable[tuple[str, str]]) -> tuple[MerkleLeaf, ...]:
    """Validate and canonically ORDER the artifact entries.

    Ordering is by the UTF-8 bytes of the logical path, ascending — the single
    documented leaf order. Duplicate paths fail closed.
    """
    seen: set[str] = set()
    leaves: list[MerkleLeaf] = []
    for raw_path, raw_digest in entries:
        path = str(raw_path or '')
        if not path:
            raise MerkleConstructionError(reason='empty_path', path=None)
        if path in seen:
            raise MerkleConstructionError(reason='duplicate_path', path=path)
        seen.add(path)
        leaves.append(MerkleLeaf(path=path, sha256=_normalize_digest(raw_digest, path=path)))
    leaves.sort(key=lambda leaf: leaf.path.encode('utf-8'))
    return tuple(leaves)


def build_merkle_tree(entries: Iterable[tuple[str, str]]) -> MerkleTree:
    """Build the deterministic tree over ``(logical_path, sha256_hex)`` entries."""
    leaves = canonical_leaves(entries)
    if not leaves:
        # No artifacts => no commitment. Never a placeholder digest.
        return MerkleTree(root=None, leaves=())
    level: list[bytes] = [leaf_hash(leaf.path, leaf.sha256) for leaf in leaves]
    while len(level) > 1:
        nxt: list[bytes] = []
        for index in range(0, len(level) - 1, 2):
            nxt.append(node_hash(level[index], level[index + 1]))
        if len(level) % 2 == 1:
            # Odd tail is PROMOTED unchanged (never duplicated).
            nxt.append(level[-1])
        level = nxt
    return MerkleTree(root=level[0].hex(), leaves=leaves)


def compute_merkle_root(entries: Iterable[tuple[str, str]]) -> str | None:
    """Lowercase-hex Merkle root, or ``None`` for an empty artifact set."""
    return build_merkle_tree(entries).root


def manifest_file_entries(files: Sequence[Any] | None) -> list[tuple[str, str]]:
    """Extract ``(path, sha256)`` pairs from a manifest ``files`` list.

    Entries without a path or without a digest are skipped by the CALLER's
    contract only when the manifest is legacy; here a malformed entry is passed
    straight to the validator so an unhashed artifact can never be silently
    excluded from the root (which would make the root describe a subset while
    claiming to describe the package).
    """
    entries: list[tuple[str, str]] = []
    for entry in files or []:
        if not isinstance(entry, dict):
            raise MerkleConstructionError(reason='invalid_digest', path=None)
        entries.append((str(entry.get('path') or ''), str(entry.get('sha256') or '')))
    return entries


def merkle_root_for_manifest(manifest: dict[str, Any] | None) -> str | None:
    """Recompute the root a manifest's ``files`` list commits to.

    This is the verification-side counterpart of the build-side root: it reads
    ONLY the manifest's declared artifact paths + digests, so comparing it to
    the manifest's stored ``merkle_root`` proves the stored root really does
    describe the artifact set the manifest lists. Verification separately
    recomputes each artifact's SHA-256 from real stored bytes, which is what
    ties the whole chain back to the evidence itself.
    """
    if not isinstance(manifest, dict):
        return None
    files = manifest.get('files')
    if not isinstance(files, list) or not files:
        return None
    return compute_merkle_root(manifest_file_entries(files))
