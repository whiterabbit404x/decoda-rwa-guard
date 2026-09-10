"""Screen 9 — deterministic Merkle tree over an evidence package's artifacts.

These lock the properties the whole verifiable-package claim rests on:

  A. The same evidence set ALWAYS produces the same root.
  B. A different database / iteration order produces the SAME root (canonical
     leaf ordering, never row order).
  C. Any change to any artifact digest changes the root.
  D. Adding, removing or renaming an artifact changes the root.
  E. Odd leaf counts are handled deterministically, WITHOUT the duplicate-last-leaf
     ambiguity that lets two different artifact sets share a root.
  F. An empty artifact set has NO root — never a placeholder digest.
  G. Domain separation: an internal node preimage can never be read as a leaf.
"""
from __future__ import annotations

import hashlib

import pytest

from services.api.app import evidence_merkle


def _digest(seed: str) -> str:
    return hashlib.sha256(seed.encode('utf-8')).hexdigest()


ARTIFACTS = [
    ('alerts.json', _digest('alerts')),
    ('audit_log.json', _digest('audit')),
    ('detections.json', _digest('detections')),
    ('incidents.json', _digest('incidents')),
    ('policy_evaluations.json', _digest('policy')),
    ('response_actions.json', _digest('actions')),
    ('summary.json', _digest('summary')),
]


# ── A. Determinism ───────────────────────────────────────────────────────────

def test_same_evidence_set_always_produces_the_same_root():
    first = evidence_merkle.compute_merkle_root(ARTIFACTS)
    second = evidence_merkle.compute_merkle_root(list(ARTIFACTS))
    assert first == second
    assert first is not None
    assert len(first) == 64 and set(first) <= set('0123456789abcdef')


def test_root_is_stable_across_repeated_builds():
    roots = {evidence_merkle.compute_merkle_root(ARTIFACTS) for _ in range(25)}
    assert len(roots) == 1


# ── B. Ordering independence (never database row order) ──────────────────────

def test_different_database_ordering_produces_the_same_root():
    """A package must not depend on the order rows came back from Postgres."""
    canonical = evidence_merkle.compute_merkle_root(ARTIFACTS)
    reversed_order = evidence_merkle.compute_merkle_root(list(reversed(ARTIFACTS)))
    shuffled = evidence_merkle.compute_merkle_root(
        [ARTIFACTS[3], ARTIFACTS[0], ARTIFACTS[6], ARTIFACTS[1], ARTIFACTS[5], ARTIFACTS[2], ARTIFACTS[4]]
    )
    assert canonical == reversed_order == shuffled


def test_canonical_leaf_order_is_by_utf8_path_bytes():
    leaves = evidence_merkle.canonical_leaves(list(reversed(ARTIFACTS)))
    paths = [leaf.path for leaf in leaves]
    assert paths == sorted(paths, key=lambda p: p.encode('utf-8'))


def test_leaf_ordering_is_byte_order_not_locale_collation():
    entries = [('B.json', _digest('b')), ('a.json', _digest('a')), ('Z.json', _digest('z'))]
    leaves = evidence_merkle.canonical_leaves(entries)
    # Byte order puts uppercase before lowercase; a locale collation would not.
    assert [leaf.path for leaf in leaves] == ['B.json', 'Z.json', 'a.json']


# ── C/D. Sensitivity to any change in the artifact set ───────────────────────

def test_changing_one_artifact_digest_changes_the_root():
    tampered = list(ARTIFACTS)
    tampered[2] = (tampered[2][0], _digest('detections-TAMPERED'))
    assert evidence_merkle.compute_merkle_root(tampered) != evidence_merkle.compute_merkle_root(ARTIFACTS)


def test_removing_an_artifact_changes_the_root():
    assert evidence_merkle.compute_merkle_root(ARTIFACTS[:-1]) != evidence_merkle.compute_merkle_root(ARTIFACTS)


def test_adding_an_artifact_changes_the_root():
    extended = [*ARTIFACTS, ('telemetry_events.json', _digest('telemetry'))]
    assert evidence_merkle.compute_merkle_root(extended) != evidence_merkle.compute_merkle_root(ARTIFACTS)


def test_renaming_an_artifact_changes_the_root():
    """The leaf commits to the PATH as well as the digest."""
    renamed = [(('renamed.json' if path == 'alerts.json' else path), digest) for path, digest in ARTIFACTS]
    assert evidence_merkle.compute_merkle_root(renamed) != evidence_merkle.compute_merkle_root(ARTIFACTS)


def test_swapping_two_artifact_digests_changes_the_root():
    swapped = list(ARTIFACTS)
    swapped[0] = (ARTIFACTS[0][0], ARTIFACTS[1][1])
    swapped[1] = (ARTIFACTS[1][0], ARTIFACTS[0][1])
    assert evidence_merkle.compute_merkle_root(swapped) != evidence_merkle.compute_merkle_root(ARTIFACTS)


# ── E. Odd leaf counts ───────────────────────────────────────────────────────

def test_odd_leaf_count_promotes_rather_than_duplicates_the_last_node():
    """Guards the CVE-2012-2459-style ambiguity.

    With Bitcoin's duplicate-the-last-leaf rule, a 3-leaf set and the 4-leaf set
    formed by repeating its last leaf collapse to the SAME root. Promotion keeps
    them distinct.
    """
    three = ARTIFACTS[:3]
    duplicated_tail = [*three, ('duplicate.json', three[-1][1])]
    assert evidence_merkle.compute_merkle_root(three) != evidence_merkle.compute_merkle_root(duplicated_tail)


@pytest.mark.parametrize('count', [1, 2, 3, 4, 5, 6, 7])
def test_every_leaf_count_yields_a_stable_single_root(count):
    subset = ARTIFACTS[:count]
    root = evidence_merkle.compute_merkle_root(subset)
    assert root is not None
    assert root == evidence_merkle.compute_merkle_root(list(reversed(subset)))


def test_single_artifact_root_is_its_leaf_hash():
    entry = ARTIFACTS[0]
    root = evidence_merkle.compute_merkle_root([entry])
    assert root == evidence_merkle.leaf_hash(entry[0], entry[1]).hex()


# ── F. Empty set has no root ─────────────────────────────────────────────────

def test_empty_artifact_set_has_no_root():
    """Never SHA256(b'') or any other value that could display as a commitment."""
    assert evidence_merkle.compute_merkle_root([]) is None
    assert evidence_merkle.build_merkle_tree([]).leaf_count == 0
    assert evidence_merkle.merkle_root_for_manifest({'files': []}) is None


# ── G. Leaf encoding + domain separation ─────────────────────────────────────

def test_leaf_preimage_matches_the_documented_encoding():
    path, digest = ARTIFACTS[0]
    expected = b'\x00' + path.encode('utf-8') + b'\x1f' + digest.encode('ascii')
    assert evidence_merkle.leaf_preimage(path, digest) == expected
    assert evidence_merkle.leaf_hash(path, digest) == hashlib.sha256(expected).digest()


def test_node_preimage_is_domain_separated_from_leaves():
    left = evidence_merkle.leaf_hash(*ARTIFACTS[0])
    right = evidence_merkle.leaf_hash(*ARTIFACTS[1])
    assert evidence_merkle.node_hash(left, right) == hashlib.sha256(b'\x01' + left + right).digest()
    # A node preimage starts with 0x01 and a leaf preimage with 0x00, so a
    # 64-byte child concatenation can never be reinterpreted as a leaf.
    assert evidence_merkle.leaf_preimage(*ARTIFACTS[0])[:1] == b'\x00'


def test_field_separator_prevents_path_digest_boundary_collisions():
    shared = _digest('x')
    a = evidence_merkle.leaf_hash('ab', shared)
    b = evidence_merkle.leaf_hash('a', shared)
    assert a != b


# ── Fail-closed construction ─────────────────────────────────────────────────

def test_duplicate_path_fails_closed():
    with pytest.raises(evidence_merkle.MerkleConstructionError) as exc:
        evidence_merkle.compute_merkle_root([('a.json', _digest('1')), ('a.json', _digest('2'))])
    assert exc.value.reason == 'duplicate_path'


def test_missing_digest_fails_closed_rather_than_silently_excluding_an_artifact():
    with pytest.raises(evidence_merkle.MerkleConstructionError) as exc:
        evidence_merkle.compute_merkle_root([('a.json', '')])
    assert exc.value.reason == 'invalid_digest'


def test_non_sha256_digest_fails_closed():
    with pytest.raises(evidence_merkle.MerkleConstructionError):
        evidence_merkle.compute_merkle_root([('a.json', 'not-a-hash')])


def test_empty_path_fails_closed():
    with pytest.raises(evidence_merkle.MerkleConstructionError) as exc:
        evidence_merkle.compute_merkle_root([('', _digest('x'))])
    assert exc.value.reason == 'empty_path'


def test_digest_is_normalized_to_lowercase_hex():
    upper = [(path, digest.upper()) for path, digest in ARTIFACTS]
    assert evidence_merkle.compute_merkle_root(upper) == evidence_merkle.compute_merkle_root(ARTIFACTS)


def test_manifest_root_recomputes_from_the_manifest_file_list():
    manifest = {
        'files': [
            {'path': path, 'sha256': digest, 'size_bytes': 10}
            for path, digest in ARTIFACTS
        ],
    }
    assert evidence_merkle.merkle_root_for_manifest(manifest) == evidence_merkle.compute_merkle_root(ARTIFACTS)


def test_construction_never_reads_the_clock_or_random_state():
    """A root computed today must recompute identically years later."""
    import random
    import time

    baseline = evidence_merkle.compute_merkle_root(ARTIFACTS)
    random.seed(1234)
    time.sleep(0)
    assert evidence_merkle.compute_merkle_root(ARTIFACTS) == baseline
