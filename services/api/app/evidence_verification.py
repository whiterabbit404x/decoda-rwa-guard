"""Backend cryptographic verification of an evidence package (Screen 9).

The rule this module exists to enforce
--------------------------------------
    "VERIFIED" is a CONCLUSION, never an assumption.

Every check below is computed INDEPENDENTLY from the persisted manifest and the
real stored artifact bytes. Nothing is inferred from a package's lifecycle
status, nothing is trusted from the browser, and no failure is collapsed into a
single generic boolean: if one artifact hash mismatches, the result says which
CATEGORY failed and how many artifacts, while the Merkle, signature, policy and
provenance categories report their own independent outcomes.

The seven checks
----------------
``artifact_hashes``      Recompute SHA-256 (and byte length) for every artifact
                         the manifest lists, from the bytes actually stored.
``manifest_hash``        Recompute ``manifest_sha256`` over the canonical
                         manifest body with its own hash field excluded.
``merkle_root``          Rebuild the deterministic Merkle tree from the
                         manifest's ``(path, sha256)`` set and compare with the
                         root the manifest sealed.
``manifest_signature``   Re-derive the MAC over the canonical manifest bytes
                         with the trusted key.
``policy_snapshot``      The incident-time policy version the package preserved
                         is present and internally consistent.
``provenance_chain``     Required provenance metadata exists on the manifest and
                         on every artifact entry.
``required_evidence``    Every artifact the package schema declares mandatory is
                         present in the package.

Check status vocabulary
-----------------------
``passed``          The check ran and succeeded.
``failed``          The check ran and FAILED. Always blocks VERIFIED.
``unavailable``     The check could not be run (e.g. no verification key).
                    Never rendered as a failure and never as a pass.
``not_applicable``  This package's manifest schema never declared the fact, so
                    there is nothing to check. Legacy (v1.0) manifests predate
                    the Merkle root, the policy snapshot and declared required
                    artifacts; reporting those as failures would be untrue.

Overall status precedence (highest first)
-----------------------------------------
``VERIFICATION_FAILED``   any check ``failed`` (tampering / mismatch)
``INCOMPLETE_PACKAGE``    artifacts the manifest lists are MISSING, or a
                          declared-required artifact is absent
``SIGNATURE_UNAVAILABLE`` the signature could not be checked at all
``PARTIALLY_VERIFIED``    everything checkable passed, but at least one check
                          was ``unavailable``
``VERIFIED``              every applicable check ``passed``

``VERIFIED`` therefore requires all of: every artifact hash matching real stored
bytes, the manifest hash recomputing, the sealed Merkle root rebuilding (when
the manifest sealed one), the signature verifying against the trusted key, and
the declared policy/provenance/required-evidence facts holding.
"""
from __future__ import annotations

import hashlib
from typing import Any

from services.api.app import evidence_merkle
from services.api.app.evidence_manifest_signer import (
    EvidenceManifestSigner,
    SIGNATURE_ABSENT,
    SIGNATURE_INVALID,
    SIGNATURE_UNAVAILABLE,
    SIGNATURE_VALID,
    resolve_manifest_signer,
)
from services.api.app.evidence_signing import EvidenceSerializationError, canonical_json

# ── Overall statuses ────────────────────────────────────────────────────────
STATUS_VERIFIED = 'VERIFIED'
STATUS_PARTIALLY_VERIFIED = 'PARTIALLY_VERIFIED'
STATUS_VERIFICATION_FAILED = 'VERIFICATION_FAILED'
STATUS_SIGNATURE_UNAVAILABLE = 'SIGNATURE_UNAVAILABLE'
STATUS_INCOMPLETE_PACKAGE = 'INCOMPLETE_PACKAGE'
#: Transient client-side state; the backend never returns it as a result.
STATUS_VERIFYING = 'VERIFYING'

#: The only status that licenses a green "VERIFIED" affordance anywhere in the
#: product. Anything else must render as a non-green state.
VERIFIED_STATUSES = frozenset({STATUS_VERIFIED})

STATUS_LABELS: dict[str, str] = {
    STATUS_VERIFIED: 'Verified',
    STATUS_PARTIALLY_VERIFIED: 'Partially Verified',
    STATUS_VERIFICATION_FAILED: 'Verification Failed',
    STATUS_SIGNATURE_UNAVAILABLE: 'Signature Unavailable',
    STATUS_INCOMPLETE_PACKAGE: 'Incomplete Package',
    STATUS_VERIFYING: 'Verifying',
}

# ── Check identifiers ───────────────────────────────────────────────────────
CHECK_ARTIFACT_HASHES = 'artifact_hashes'
CHECK_MANIFEST_HASH = 'manifest_hash'
CHECK_MERKLE_ROOT = 'merkle_root'
CHECK_MANIFEST_SIGNATURE = 'manifest_signature'
CHECK_POLICY_SNAPSHOT = 'policy_snapshot'
CHECK_PROVENANCE = 'provenance_chain'
CHECK_REQUIRED_EVIDENCE = 'required_evidence'

#: Render order — mirrors the Screen 9 Package Verification panel.
CHECK_ORDER: tuple[str, ...] = (
    CHECK_ARTIFACT_HASHES,
    CHECK_MERKLE_ROOT,
    CHECK_MANIFEST_HASH,
    CHECK_MANIFEST_SIGNATURE,
    CHECK_POLICY_SNAPSHOT,
    CHECK_PROVENANCE,
    CHECK_REQUIRED_EVIDENCE,
)

# ── Check statuses ──────────────────────────────────────────────────────────
CHECK_PASSED = 'passed'
CHECK_FAILED = 'failed'
CHECK_UNAVAILABLE = 'unavailable'
CHECK_NOT_APPLICABLE = 'not_applicable'

#: Manifest schema version at which the Merkle root, the policy snapshot and the
#: declared required-artifact list became part of the sealed manifest. Manifests
#: below it legitimately have none of those facts.
MANIFEST_SCHEMA_SEALED_V2 = '2.0'

#: Manifest metadata that must be present for provenance to be complete. These
#: are written by ``build_evidence_manifest`` for every manifest version.
_REQUIRED_MANIFEST_FIELDS = (
    'export_id',
    'export_type',
    'workspace_id',
    'generated_at',
    'source_resource_type',
    'source_resource_id',
    'storage_backend',
)


def _manifest_schema_version(manifest: dict[str, Any]) -> str:
    return str(manifest.get('schema_version') or manifest.get('manifest_version') or '1.0').strip() or '1.0'


def _is_sealed_v2(manifest: dict[str, Any]) -> bool:
    """True when the manifest declares the v2 sealed facts (Merkle/policy/required)."""
    try:
        return float(_manifest_schema_version(manifest)) >= float(MANIFEST_SCHEMA_SEALED_V2)
    except (TypeError, ValueError):
        return False


def _check(
    key: str,
    *,
    status: str,
    label: str,
    detail: str | None = None,
    mandatory: bool = True,
    **extra: Any,
) -> dict[str, Any]:
    return {
        'check': key,
        'status': status,
        'label': label,
        'detail': detail,
        'mandatory': mandatory,
        'passed': status == CHECK_PASSED,
        **extra,
    }


# ── Individual checks ───────────────────────────────────────────────────────

def check_artifact_hashes(file_values: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    """Recompute every listed artifact's SHA-256 and byte length from stored bytes.

    An artifact passes only when BOTH its recomputed digest and its byte length
    match the manifest entry. An artifact whose stored value cannot be
    deterministically re-serialized is a FAILURE (it cannot be proven intact),
    never a silent pass.
    """
    entries = manifest.get('files') if isinstance(manifest.get('files'), list) else []
    valid = 0
    failed: list[str] = []
    missing: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            failed.append('<malformed-manifest-entry>')
            continue
        path = str(entry.get('path') or '')
        expected_sha = str(entry.get('sha256') or '').lower()
        expected_size = entry.get('size_bytes')
        if path not in file_values:
            missing.append(path)
            continue
        try:
            artifact_bytes = canonical_json(file_values[path], path=path)
        except EvidenceSerializationError:
            failed.append(path)
            continue
        actual_sha = hashlib.sha256(artifact_bytes).hexdigest()
        size_ok = expected_size is None or len(artifact_bytes) == int(expected_size)
        if actual_sha == expected_sha and size_ok:
            valid += 1
        else:
            failed.append(path)

    total = len(entries)
    if failed:
        status, detail = CHECK_FAILED, f'{len(failed)} artifact hash mismatch(es).'
    elif missing:
        status, detail = CHECK_FAILED, f'{len(missing)} artifact(s) listed in the manifest are missing from the package.'
    elif total == 0:
        status, detail = CHECK_FAILED, 'The manifest lists no artifacts, so there is nothing to verify.'
    else:
        status, detail = CHECK_PASSED, None

    return _check(
        CHECK_ARTIFACT_HASHES,
        status=status,
        label=f'{valid} / {total} artifact hashes valid',
        detail=detail,
        valid=valid,
        total=total,
        failed_artifact_paths=sorted(failed),
        missing_artifact_paths=sorted(missing),
    )


def check_manifest_hash(manifest: dict[str, Any]) -> dict[str, Any]:
    """Recompute ``manifest_sha256`` over the canonical body, hash field excluded."""
    recorded = str(manifest.get('manifest_sha256') or '')
    body = {key: value for key, value in manifest.items() if key != 'manifest_sha256'}
    try:
        computed = hashlib.sha256(canonical_json(body)).hexdigest()
    except EvidenceSerializationError:
        return _check(
            CHECK_MANIFEST_HASH, status=CHECK_FAILED, label='Manifest hash matches',
            detail='The manifest could not be canonically re-serialized.',
            expected=recorded or None, computed=None,
        )
    if not recorded:
        return _check(
            CHECK_MANIFEST_HASH, status=CHECK_FAILED, label='Manifest hash matches',
            detail='The manifest does not record its own SHA-256.',
            expected=None, computed=computed,
        )
    ok = computed == recorded
    return _check(
        CHECK_MANIFEST_HASH,
        status=CHECK_PASSED if ok else CHECK_FAILED,
        label='Manifest hash matches',
        detail=None if ok else 'The manifest hash does not describe the manifest body.',
        expected=recorded, computed=computed,
    )


def check_merkle_root(manifest: dict[str, Any]) -> dict[str, Any]:
    """Rebuild the deterministic Merkle root over the manifest's artifact set.

    A manifest that never sealed a root (schema 1.0) reports ``not_applicable``
    with an explicit reason — reporting it as failed would be untrue, and
    reporting it as passed would claim a commitment that does not exist.
    """
    expected = str(manifest.get('merkle_root') or '').strip().lower() or None
    if not expected:
        return _check(
            CHECK_MERKLE_ROOT,
            status=CHECK_NOT_APPLICABLE,
            label='Merkle root matches',
            detail=(
                f'This package sealed manifest schema {_manifest_schema_version(manifest)}, '
                'which predates the Merkle commitment. Artifact hashes and the manifest '
                'signature still cover every artifact.'
            ),
            mandatory=False, expected=None, computed=None,
        )
    try:
        computed = evidence_merkle.merkle_root_for_manifest(manifest)
    except evidence_merkle.MerkleConstructionError as exc:
        return _check(
            CHECK_MERKLE_ROOT, status=CHECK_FAILED, label='Merkle root matches',
            detail=f'The artifact set could not form a valid Merkle tree ({exc.reason}).',
            expected=expected, computed=None,
        )
    ok = bool(computed) and computed == expected
    return _check(
        CHECK_MERKLE_ROOT,
        status=CHECK_PASSED if ok else CHECK_FAILED,
        label='Merkle root matches',
        detail=None if ok else 'The recomputed Merkle root does not match the sealed root.',
        expected=expected, computed=computed,
        scheme=str(manifest.get('merkle_scheme') or evidence_merkle.MERKLE_SCHEME),
    )


def check_manifest_signature(
    manifest: dict[str, Any],
    seal: dict[str, Any] | None,
    signer: EvidenceManifestSigner,
) -> dict[str, Any]:
    """Verify the seal over the canonical manifest bytes with the trusted key."""
    outcome = signer.verify(manifest, seal)
    mapping = {
        SIGNATURE_VALID: (CHECK_PASSED, None),
        SIGNATURE_INVALID: (CHECK_FAILED, 'The manifest signature does not verify. The manifest changed after signing.'),
        SIGNATURE_UNAVAILABLE: (CHECK_UNAVAILABLE, 'The verification key for this signature is not available, so the signature could not be checked.'),
        SIGNATURE_ABSENT: (CHECK_UNAVAILABLE, 'This package carries no manifest signature.'),
    }
    status, detail = mapping.get(str(outcome.get('status')), (CHECK_UNAVAILABLE, 'Signature state could not be determined.'))
    return _check(
        CHECK_MANIFEST_SIGNATURE,
        status=status,
        label='Manifest signature valid',
        detail=detail,
        signature_state=outcome.get('status'),
        reason=outcome.get('reason'),
        key_id=outcome.get('key_id'),
        key_version=outcome.get('key_version'),
        provider=outcome.get('provider'),
        algorithm=outcome.get('algorithm'),
    )


def check_policy_snapshot(manifest: dict[str, Any]) -> dict[str, Any]:
    """The incident-time policy version the package preserved.

    ``present=false`` with a reason is a legitimate, truthful outcome (the
    incident had no recorded policy evaluation) and is reported as
    ``not_applicable`` — NOT as a failure and NOT as a pass.
    """
    snapshot = manifest.get('policy_snapshot')
    if not isinstance(snapshot, dict):
        if _is_sealed_v2(manifest):
            return _check(
                CHECK_POLICY_SNAPSHOT, status=CHECK_FAILED, label='Policy snapshot present',
                detail='This package schema declares a policy snapshot, but the manifest carries none.',
            )
        return _check(
            CHECK_POLICY_SNAPSHOT, status=CHECK_NOT_APPLICABLE, label='Policy snapshot present',
            detail=f'Manifest schema {_manifest_schema_version(manifest)} predates the sealed policy snapshot.',
            mandatory=False,
        )
    if not snapshot.get('present'):
        return _check(
            CHECK_POLICY_SNAPSHOT, status=CHECK_NOT_APPLICABLE, label='Policy snapshot present',
            detail=str(snapshot.get('reason') or 'No policy evaluation was recorded for this incident.'),
            mandatory=False, policy_key=None, policy_version=None,
        )
    policy_key = str(snapshot.get('policy_key') or '').strip()
    policy_version = snapshot.get('policy_version')
    if not policy_key or policy_version is None:
        return _check(
            CHECK_POLICY_SNAPSHOT, status=CHECK_FAILED, label='Policy snapshot present',
            detail='The sealed policy snapshot is incomplete (missing policy key or version).',
            policy_key=policy_key or None, policy_version=policy_version,
        )
    return _check(
        CHECK_POLICY_SNAPSHOT, status=CHECK_PASSED, label='Policy snapshot present',
        policy_key=policy_key, policy_version=policy_version,
        evaluation_id=snapshot.get('evaluation_id'),
        evaluated_at=snapshot.get('evaluated_at'),
    )


def check_provenance(manifest: dict[str, Any]) -> dict[str, Any]:
    """Required provenance metadata on the manifest and on every artifact entry."""
    missing_fields = [field for field in _REQUIRED_MANIFEST_FIELDS if not str(manifest.get(field) or '').strip()]
    entries = manifest.get('files') if isinstance(manifest.get('files'), list) else []
    incomplete_artifacts: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict):
            incomplete_artifacts.append('<malformed-manifest-entry>')
            continue
        path = str(entry.get('path') or '')
        if not path or not str(entry.get('sha256') or '').strip() or entry.get('size_bytes') is None:
            incomplete_artifacts.append(path or '<unnamed>')
            continue
        if _is_sealed_v2(manifest):
            # v2 manifests additionally declare each artifact's provenance domain
            # and the source record type it was collected from.
            if not str(entry.get('domain') or '').strip() or not str(entry.get('source_record_type') or '').strip():
                incomplete_artifacts.append(path)
    if missing_fields or incomplete_artifacts:
        details: list[str] = []
        if missing_fields:
            details.append(f"manifest fields missing: {', '.join(sorted(missing_fields))}")
        if incomplete_artifacts:
            details.append(f'{len(incomplete_artifacts)} artifact(s) missing provenance metadata')
        return _check(
            CHECK_PROVENANCE, status=CHECK_FAILED, label='Provenance chain complete',
            detail='; '.join(details),
            missing_manifest_fields=sorted(missing_fields),
            incomplete_artifact_paths=sorted(incomplete_artifacts),
        )
    return _check(
        CHECK_PROVENANCE, status=CHECK_PASSED, label='Provenance chain complete',
        missing_manifest_fields=[], incomplete_artifact_paths=[],
    )


def check_required_evidence(manifest: dict[str, Any], file_values: dict[str, Any]) -> dict[str, Any]:
    """Every artifact the package schema declares mandatory is actually present."""
    declared = manifest.get('required_artifacts')
    if not isinstance(declared, list) or not declared:
        return _check(
            CHECK_REQUIRED_EVIDENCE, status=CHECK_NOT_APPLICABLE, label='All required evidence present',
            detail=(
                f'Manifest schema {_manifest_schema_version(manifest)} does not declare a '
                'required-artifact list, so completeness cannot be checked against a schema.'
            ),
            mandatory=False, required=[], missing=[],
        )
    required = [str(item) for item in declared if str(item or '').strip()]
    manifest_paths = {
        str(entry.get('path') or '')
        for entry in (manifest.get('files') or [])
        if isinstance(entry, dict)
    }
    missing = sorted(path for path in required if path not in manifest_paths or path not in file_values)
    if missing:
        return _check(
            CHECK_REQUIRED_EVIDENCE, status=CHECK_FAILED, label='All required evidence present',
            detail=f"{len(missing)} required artifact(s) missing: {', '.join(missing)}",
            required=required, missing=missing,
        )
    return _check(
        CHECK_REQUIRED_EVIDENCE, status=CHECK_PASSED, label='All required evidence present',
        required=required, missing=[],
    )


# ── Orchestration ───────────────────────────────────────────────────────────

def _overall_status(checks: list[dict[str, Any]]) -> str:
    by_key = {check['check']: check for check in checks}
    artifacts = by_key.get(CHECK_ARTIFACT_HASHES, {})
    required = by_key.get(CHECK_REQUIRED_EVIDENCE, {})
    signature = by_key.get(CHECK_MANIFEST_SIGNATURE, {})

    missing_artifacts = list(artifacts.get('missing_artifact_paths') or [])
    failed_artifacts = list(artifacts.get('failed_artifact_paths') or [])

    # 1. Real cryptographic failure — the most serious outcome.
    hard_failures = [
        check for check in checks
        if check['status'] == CHECK_FAILED
        and not (check['check'] == CHECK_ARTIFACT_HASHES and missing_artifacts and not failed_artifacts)
        and not (check['check'] == CHECK_REQUIRED_EVIDENCE)
    ]
    if hard_failures:
        return STATUS_VERIFICATION_FAILED

    # 2. Something the package should contain is absent.
    if missing_artifacts or required.get('status') == CHECK_FAILED:
        return STATUS_INCOMPLETE_PACKAGE

    # 3. The signature could not be checked at all.
    if signature.get('status') == CHECK_UNAVAILABLE:
        return STATUS_SIGNATURE_UNAVAILABLE

    # 4. Everything checkable passed, but something could not be checked.
    if any(check['status'] == CHECK_UNAVAILABLE for check in checks):
        return STATUS_PARTIALLY_VERIFIED

    # 5. Every applicable check passed.
    return STATUS_VERIFIED


def verify_evidence_package_document(
    *,
    manifest: dict[str, Any],
    seal: dict[str, Any] | None,
    file_values: dict[str, Any],
    signer: EvidenceManifestSigner | None = None,
    verified_at: str | None = None,
    verified_by_user_id: str | None = None,
) -> dict[str, Any]:
    """Run every verification check and return the structured result.

    Pure with respect to the database: it takes the manifest, the seal and the
    ACTUAL stored artifact values, and returns a result the caller persists and
    audits. It never mutates its inputs and never reports a status it did not
    compute.
    """
    active_signer = signer or resolve_manifest_signer()
    checks = [
        check_artifact_hashes(file_values, manifest),
        check_merkle_root(manifest),
        check_manifest_hash(manifest),
        check_manifest_signature(manifest, seal, active_signer),
        check_policy_snapshot(manifest),
        check_provenance(manifest),
        check_required_evidence(manifest, file_values),
    ]
    order = {key: index for index, key in enumerate(CHECK_ORDER)}
    checks.sort(key=lambda check: order.get(check['check'], len(order)))

    status = _overall_status(checks)
    by_key = {check['check']: check for check in checks}
    artifacts = by_key[CHECK_ARTIFACT_HASHES]
    merkle = by_key[CHECK_MERKLE_ROOT]
    signature = by_key[CHECK_MANIFEST_SIGNATURE]
    manifest_hash = by_key[CHECK_MANIFEST_HASH]

    return {
        'status': status,
        'status_label': STATUS_LABELS.get(status, status),
        # The ONE boolean the UI may use to render a green VERIFIED affordance.
        'verified': status == STATUS_VERIFIED,
        'verified_at': verified_at,
        'verified_by_user_id': verified_by_user_id,
        'schema_version': _manifest_schema_version(manifest),
        'checks': checks,
        'failed_checks': [check['check'] for check in checks if check['status'] == CHECK_FAILED],
        'unavailable_checks': [check['check'] for check in checks if check['status'] == CHECK_UNAVAILABLE],
        # Flattened category summaries, matching the documented API shape.
        'artifact_hashes': {
            'valid': artifacts['valid'],
            'total': artifacts['total'],
            'failed_artifact_ids': artifacts['failed_artifact_paths'],
            'missing_artifact_ids': artifacts['missing_artifact_paths'],
        },
        'merkle_root': {
            'valid': merkle['status'] == CHECK_PASSED,
            'status': merkle['status'],
            'expected': merkle.get('expected'),
            'computed': merkle.get('computed'),
        },
        'manifest_hash': {
            'valid': manifest_hash['status'] == CHECK_PASSED,
            'expected': manifest_hash.get('expected'),
            'computed': manifest_hash.get('computed'),
        },
        'manifest_signature': {
            'valid': signature['status'] == CHECK_PASSED,
            'state': signature.get('signature_state'),
            'key_id': signature.get('key_id'),
            'provider': signature.get('provider'),
            'algorithm': signature.get('algorithm'),
        },
        'policy_snapshot_present': by_key[CHECK_POLICY_SNAPSHOT]['status'] == CHECK_PASSED,
        'provenance_complete': by_key[CHECK_PROVENANCE]['status'] == CHECK_PASSED,
        'required_evidence_complete': by_key[CHECK_REQUIRED_EVIDENCE]['status'] == CHECK_PASSED,
        # Truthful signer assurance. Never claims hardware custody.
        'signer': active_signer.identity.as_dict(),
    }


def legacy_verification_view(result: dict[str, Any]) -> dict[str, Any]:
    """Project the structured result onto the pre-existing ``verification`` shape.

    Existing readers (the list projection, ``derive_integrity_status``, the
    detail view's per-file ``verification_status`` and the Screen 9 regression
    suite) consume ``valid`` / ``files_total`` / ``files_verified`` /
    ``files_failed`` / ``missing_files`` / ``manifest_ok`` / ``seal_status``.
    Keeping that projection means the upgrade adds structure without breaking a
    single existing consumer.

    ``valid`` is deliberately TRI-STATE:

        ``True``   the package verified in full,
        ``False``  a check FAILED, or a listed artifact is missing — a real
                   integrity problem,
        ``None``   everything checkable passed but something could NOT be checked
                   (typically no verification key available).

    ``None`` matters: ``derive_integrity_status`` tests ``valid is True`` /
    ``valid is False``, so an UNCHECKABLE package falls through to
    ``hash_generated`` instead of being branded ``integrity_failed``. Reporting
    "we could not check the signature" as "this package was tampered with" would
    be exactly as untruthful as the reverse.
    """
    artifacts = result['artifact_hashes']
    seal_state = str(result['manifest_signature'].get('state') or '')
    seal_status = {
        SIGNATURE_VALID: 'valid',
        SIGNATURE_INVALID: 'invalid',
        SIGNATURE_UNAVAILABLE: 'unverifiable',
        SIGNATURE_ABSENT: 'absent',
    }.get(seal_state, 'absent')
    status = str(result['status'])
    if status == STATUS_VERIFIED:
        valid: bool | None = True
    elif status in {STATUS_VERIFICATION_FAILED, STATUS_INCOMPLETE_PACKAGE}:
        valid = False
    else:
        valid = None
    return {
        'valid': valid,
        'verified_at': result.get('verified_at'),
        'files_total': artifacts['total'],
        'files_verified': artifacts['valid'],
        'files_failed': artifacts['failed_artifact_ids'],
        'missing_files': artifacts['missing_artifact_ids'],
        'manifest_ok': bool(result['manifest_hash']['valid']),
        'seal_status': seal_status,
        'verified_by_user_id': result.get('verified_by_user_id'),
        # New structured fields, additive.
        'verification_status': result['status'],
        'merkle_root_valid': result['merkle_root']['valid'],
        'merkle_root': result['merkle_root'].get('expected'),
    }


# ─────────────────────────────────────────────────────────────────────────────
# THE CANONICAL SCREEN 9 VERIFICATION CONTRACT
# ─────────────────────────────────────────────────────────────────────────────
# One backend result. Four surfaces.
#
# The Evidence Package table's Integrity column, the package detail view, the
# Crypto-Auditing Clerk sidebar and the Verification Checklist ALL render
# ``build_verification_contract(...)``. None of them may compute a verification
# outcome of their own — that is what produced the contradiction this contract
# exists to make impossible (a package showing Integrity=Verified, Files
# Verified=9, Integrity Failures=0 beside a checklist saying "Hashes verified"
# was false, because the checklist was a FROZEN BUILD-TIME snapshot taken before
# any verification could have run).
#
# COMPLETENESS is not INTEGRITY
# ----------------------------
# ``Evidence Completeness`` answers "do we hold all the required evidence?" and
# is computed by ``evidence_completeness``. ``Integrity Verification`` answers
# "was that evidence cryptographically re-validated on the server?" and is
# computed HERE. A package can be 100% complete and NOT_VERIFIED; it can be 100%
# complete and VERIFICATION_FAILED. Completeness never upgrades a package to
# VERIFIED, and this module never reads a completeness score to decide one.

#: A package that is complete and manifested but on which server-side
#: verification has NEVER been executed. It is not a failure and not a pass.
STATUS_NOT_VERIFIED = 'NOT_VERIFIED'
#: A newer package supersedes this one; its historical state is preserved as-is.
STATUS_SUPERSEDED = 'SUPERSEDED'
#: A completed export that never built a tamper-evident manifest at all.
STATUS_LEGACY_EXPORT = 'LEGACY_EXPORT'
#: Built as an evidence bundle, but no manifest is retrievable — unverifiable.
STATUS_MANIFEST_MISSING = 'MANIFEST_MISSING'
#: The package artifact is still being generated; there is nothing to verify yet.
STATUS_BUILDING = 'BUILDING'
#: Package generation itself failed. Distinct from VERIFICATION_FAILED.
STATUS_PACKAGE_FAILED = 'PACKAGE_FAILED'

STATUS_LABELS.update({
    STATUS_NOT_VERIFIED: 'Not Verified',
    STATUS_SUPERSEDED: 'Superseded',
    STATUS_LEGACY_EXPORT: 'Legacy Export',
    STATUS_MANIFEST_MISSING: 'Manifest Missing',
    STATUS_BUILDING: 'Building',
    STATUS_PACKAGE_FAILED: 'Failed',
})

#: A check that has not been executed. Distinct from ``failed`` (it ran and did
#: not match) and from ``unavailable`` (it ran and could not be completed).
CHECK_NOT_VERIFIED = 'not_verified'

# Shield states. The green shield is licensed by exactly ONE of them.
SHIELD_VERIFIED = 'VERIFIED'
SHIELD_READY_FOR_VERIFICATION = 'READY_FOR_VERIFICATION'
SHIELD_INTEGRITY_CHECK_FAILED = 'INTEGRITY_CHECK_FAILED'
SHIELD_NOT_FULLY_VERIFIED = 'NOT_FULLY_VERIFIED'
SHIELD_INCOMPLETE_PACKAGE = 'INCOMPLETE_PACKAGE'
SHIELD_NOT_VERIFIABLE = 'NOT_VERIFIABLE'
SHIELD_SUPERSEDED = 'SUPERSEDED'
SHIELD_BUILDING = 'BUILDING'

_SHIELD_LABELS: dict[str, str] = {
    SHIELD_VERIFIED: 'Verified',
    SHIELD_READY_FOR_VERIFICATION: 'Ready for Verification',
    SHIELD_INTEGRITY_CHECK_FAILED: 'Integrity Check Failed',
    SHIELD_NOT_FULLY_VERIFIED: 'Not Fully Verified',
    SHIELD_INCOMPLETE_PACKAGE: 'Incomplete Package',
    SHIELD_NOT_VERIFIABLE: 'Not Verifiable',
    SHIELD_SUPERSEDED: 'Superseded',
    SHIELD_BUILDING: 'Building',
}

#: Overall statuses that mean "a real server-side verification produced this".
_EXECUTED_STATUSES = frozenset({
    STATUS_VERIFIED,
    STATUS_PARTIALLY_VERIFIED,
    STATUS_VERIFICATION_FAILED,
    STATUS_SIGNATURE_UNAVAILABLE,
    STATUS_INCOMPLETE_PACKAGE,
})

#: Screen 9 checklist rows sourced from the CANONICAL verification checks.
#: Every row names the backend check it renders — no row exists without one, so
#: the checklist can never display a check the backend does not actually run.
_VERIFICATION_CHECKLIST_ROWS: tuple[tuple[str, str, str], ...] = (
    ('hashes_verified', CHECK_ARTIFACT_HASHES, 'File hashes verified'),
    ('merkle_root', CHECK_MERKLE_ROOT, 'Merkle root matches'),
    ('manifest_signature', CHECK_MANIFEST_SIGNATURE, 'Manifest signature valid'),
    ('policy_snapshot', CHECK_POLICY_SNAPSHOT, 'Policy snapshot present'),
    ('provenance', CHECK_PROVENANCE, 'Provenance complete'),
)

#: Screen 9 checklist rows sourced from EVIDENCE COMPLETENESS (what the package
#: contains), not from cryptography. Each maps to completeness category codes;
#: a row is satisfied when every mapped category is 'present' or 'not_applicable'.
_COMPLETENESS_CHECKLIST_ROWS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ('required_fields', 'Required fields present',
     ('incident_identity', 'original_alert', 'detection_provenance', 'manifest_hash', 'file_hashes')),
    ('chain_data', 'Chain data complete', ('chain_metadata',)),
    ('logs_included', 'Logs included', ('audit_events',)),
    ('approvals_included', 'Response approvals included', ('approval_decision',)),
    ('execution_included', 'Execution outcome included', ('execution_result',)),
)


def _completeness_category_status(completeness: dict[str, Any] | None, code: str) -> str | None:
    categories = (completeness or {}).get('categories')
    if not isinstance(categories, list):
        return None
    for category in categories:
        if isinstance(category, dict) and category.get('code') == code:
            return str(category.get('status') or '')
    return None


def _checklist_row(code: str, label: str, state: str, *, source: str, detail: str | None = None) -> dict[str, Any]:
    """One checklist row.

    ``state`` is tri-state-plus: ``passed`` / ``failed`` / ``not_verified`` /
    ``unavailable`` / ``not_applicable`` / ``missing``. ``present`` is kept for
    existing readers and is True ONLY for ``passed`` — an unexecuted check is
    never rendered as a pass, and never as a failure either.
    """
    return {
        'code': code,
        'label': label,
        'state': state,
        'present': state == CHECK_PASSED,
        'source': source,
        'detail': detail,
    }


def resolve_hashes_verified(artifact_check: dict[str, Any] | None, *, executed: bool) -> bool:
    """The ONE definition of "file hashes verified".

    ``files_hashed``   artifacts carrying a stored SHA-256 (a packaging fact).
    ``files_verified`` artifacts whose CURRENT stored bytes were independently
                       recomputed and matched the expected digest.

    Hashes are verified when every artifact that could be recomputed WAS
    recomputed and matched::

        files_verified == verifiable_file_count AND hash_failures == 0

    Artifacts listed in the manifest but absent from storage are not verifiable,
    so they are excluded from the denominator — they make the package
    INCOMPLETE, which is a separate, independently reported fact. The existence
    of SHA-256 fields is never sufficient: without an executed verification this
    returns False.
    """
    if not executed or not isinstance(artifact_check, dict):
        return False
    total = int(artifact_check.get('total') or 0)
    verified = int(artifact_check.get('valid') or 0)
    failures = len(artifact_check.get('failed_artifact_paths') or artifact_check.get('failed_artifact_ids') or [])
    missing = len(artifact_check.get('missing_artifact_paths') or artifact_check.get('missing_artifact_ids') or [])
    verifiable = max(total - missing, 0)
    if verifiable <= 0:
        return False
    return verified == verifiable and failures == 0


def _overall_status_for_package(
    *,
    job_status: str,
    superseded: bool,
    manifest_exists: bool,
    manifest_retrievable: bool,
    is_manifest_missing: bool,
    is_legacy_export: bool,
    executed_status: str | None,
) -> str:
    """Canonical overall status for a package, verification-executed or not.

    Lifecycle states that make verification IMPOSSIBLE are reported as
    themselves (SUPERSEDED / LEGACY_EXPORT / MANIFEST_MISSING / BUILDING /
    PACKAGE_FAILED) rather than being collapsed into a generic "not verified" —
    each one tells the operator something different about what to do next.

    A historical package is never silently upgraded. The lifecycle gates are
    evaluated BEFORE any recorded verification result, so:

      * a superseded package stays SUPERSEDED,
      * a package that never built a manifest stays LEGACY_EXPORT even if a
        stale ``verification`` record claims VERIFIED — a verification covers a
        manifest, so with no manifest there is nothing it could have covered,
      * a package built as an evidence bundle whose manifest is gone stays
        MANIFEST_MISSING.

    ``manifest_exists`` (a manifest was built) is deliberately distinct from
    ``manifest_retrievable`` (its bytes can be read right now). A verified
    package whose storage is momentarily unreadable keeps its VERIFIED result —
    a transient outage is not a retraction — but a package that never had a
    manifest can never reach it.
    """
    if superseded:
        return STATUS_SUPERSEDED
    status = str(job_status or '').lower()
    if status in {'queued', 'pending', 'building', 'running'}:
        return STATUS_BUILDING
    if status == 'failed':
        return STATUS_PACKAGE_FAILED
    if is_legacy_export:
        return STATUS_LEGACY_EXPORT
    if is_manifest_missing:
        return STATUS_MANIFEST_MISSING
    if not manifest_exists:
        # No manifest was ever sealed for this export. Any verification record
        # attached to it describes something else and must not be honoured.
        return STATUS_LEGACY_EXPORT if status == 'completed' else STATUS_BUILDING
    if executed_status in _EXECUTED_STATUSES:
        return executed_status
    if not manifest_retrievable:
        return STATUS_MANIFEST_MISSING if status == 'completed' else STATUS_BUILDING
    return STATUS_NOT_VERIFIED


def _shield_for(overall_status: str, *, evidence_complete: bool) -> dict[str, Any]:
    """The shield state. Derived from the verification status ONLY.

    Evidence completeness may not turn a shield green — it only distinguishes
    "ready for verification" (all required evidence held, nothing verified yet)
    from a package still short of evidence.
    """
    if overall_status == STATUS_VERIFIED:
        state = SHIELD_VERIFIED
    elif overall_status == STATUS_VERIFICATION_FAILED:
        state = SHIELD_INTEGRITY_CHECK_FAILED
    elif overall_status == STATUS_INCOMPLETE_PACKAGE:
        state = SHIELD_INCOMPLETE_PACKAGE
    elif overall_status in {STATUS_PARTIALLY_VERIFIED, STATUS_SIGNATURE_UNAVAILABLE}:
        state = SHIELD_NOT_FULLY_VERIFIED
    elif overall_status == STATUS_SUPERSEDED:
        state = SHIELD_SUPERSEDED
    elif overall_status == STATUS_BUILDING:
        state = SHIELD_BUILDING
    elif overall_status == STATUS_NOT_VERIFIED:
        state = SHIELD_READY_FOR_VERIFICATION if evidence_complete else SHIELD_NOT_VERIFIABLE
    else:
        # LEGACY_EXPORT / MANIFEST_MISSING / PACKAGE_FAILED — nothing to verify.
        state = SHIELD_NOT_VERIFIABLE
    return {
        'state': state,
        'label': _SHIELD_LABELS.get(state, state.replace('_', ' ').title()),
        # The ONE boolean that may render the large green shield anywhere.
        'verified': state == SHIELD_VERIFIED,
    }


def build_verification_contract(
    *,
    package: dict[str, Any],
    display_state: dict[str, Any],
    verification: dict[str, Any] | None = None,
    completeness: dict[str, Any] | None = None,
    include_checks: bool = True,
) -> dict[str, Any]:
    """THE canonical Screen 9 verification result. Every surface renders this.

    ``package``        the package projection (list row or detail item).
    ``display_state``  ``get_evidence_package_display_state`` output — owns the
                       lifecycle facts (superseded / manifest retrievable /
                       legacy / files_hashed) this contract must respect.
    ``verification``   the persisted ``filters.verification`` record, whose
                       ``result`` key holds the structured outcome written by
                       ``verify_evidence_package_document``. ``None`` (or a
                       record with no structured result) means verification has
                       never been executed — reported as NOT_VERIFIED, never as
                       an optimistic pass and never as a failure.
    ``completeness``   the evidence-completeness snapshot. Used ONLY for the
                       evidence-collection checklist rows and the separately
                       reported completeness block. It can never decide
                       ``overall_status``.

    The returned ``checklist`` is computed HERE, at read time, from this
    contract — never read back from the build-time completeness snapshot, which
    is frozen before any verification can have run and whose "Hashes verified"
    row was therefore permanently false.
    """
    result = verification.get('result') if isinstance(verification, dict) else None
    result = result if isinstance(result, dict) else None
    executed_status = str(result.get('status')) if result else (
        str((verification or {}).get('verification_status') or '') or None
    )
    if executed_status not in _EXECUTED_STATUSES:
        executed_status = None

    _manifest_ref = display_state.get('manifest_reference') or {}
    overall_status = _overall_status_for_package(
        job_status=str(package.get('status') or ''),
        superseded=bool(package.get('superseded')),
        manifest_exists=bool(_manifest_ref.get('exists')),
        manifest_retrievable=bool(display_state.get('manifest_retrievable')),
        is_manifest_missing=bool(display_state.get('is_manifest_missing')),
        is_legacy_export=bool(display_state.get('is_legacy_export')),
        executed_status=executed_status,
    )
    # "Executed" describes THIS package's live state: a superseded or legacy
    # package never presents a stale run as a current verification.
    executed = overall_status in _EXECUTED_STATUSES and result is not None

    checks_by_key: dict[str, dict[str, Any]] = {}
    if executed and isinstance(result.get('checks'), list):
        for check in result['checks']:
            if isinstance(check, dict) and check.get('check'):
                checks_by_key[str(check['check'])] = check

    artifact_check = checks_by_key.get(CHECK_ARTIFACT_HASHES)
    files_hashed = int(display_state.get('files_hashed') or 0)
    total = int((artifact_check or {}).get('total') or 0)
    files_verified = int((artifact_check or {}).get('valid') or 0) if executed else 0
    hash_failures = list((artifact_check or {}).get('failed_artifact_paths') or []) if executed else []
    missing_artifacts = list((artifact_check or {}).get('missing_artifact_paths') or []) if executed else []
    verifiable_count = max(total - len(missing_artifacts), 0) if executed else 0
    hashes_verified = resolve_hashes_verified(artifact_check, executed=executed)

    def category(key: str) -> dict[str, Any]:
        """One verification category, in the SAME shape whether or not it ran."""
        check = checks_by_key.get(key)
        if not executed or check is None:
            return {
                'status': CHECK_NOT_VERIFIED,
                'valid': False,
                'label': 'Not verified',
                'detail': 'Server-side verification has not been run for this package.',
            }
        return {
            'status': str(check.get('status')),
            'valid': check.get('status') == CHECK_PASSED,
            'label': str(check.get('label') or ''),
            'detail': check.get('detail'),
        }

    merkle = category(CHECK_MERKLE_ROOT)
    signature = category(CHECK_MANIFEST_SIGNATURE)
    if executed and result:
        merkle.update({
            'expected': (result.get('merkle_root') or {}).get('expected'),
            'computed': (result.get('merkle_root') or {}).get('computed'),
        })
        signature.update({
            'state': (result.get('manifest_signature') or {}).get('state'),
            'key_id': (result.get('manifest_signature') or {}).get('key_id'),
            'provider': (result.get('manifest_signature') or {}).get('provider'),
            'algorithm': (result.get('manifest_signature') or {}).get('algorithm'),
        })

    # ── Checklist: verification rows from the canonical checks, evidence rows
    # from completeness. Every row states which it is, so the UI never presents
    # a completeness fact as a cryptographic one.
    checklist: list[dict[str, Any]] = []
    for code, label, codes in _COMPLETENESS_CHECKLIST_ROWS[:1]:
        statuses = [_completeness_category_status(completeness, item) for item in codes]
        if all(item is None for item in statuses):
            state = CHECK_NOT_VERIFIED if completeness is None else CHECK_NOT_APPLICABLE
        elif all(item in {'present', 'not_applicable', None} for item in statuses):
            state = CHECK_PASSED
        else:
            state = CHECK_FAILED
        checklist.append(_checklist_row(code, label, state, source='completeness'))
    # "File hashes generated" is a PACKAGING fact (stored SHA-256s exist), kept
    # deliberately distinct from "File hashes verified" below.
    checklist.append(_checklist_row(
        'hashes_generated', 'File hashes generated',
        CHECK_PASSED if files_hashed > 0 else CHECK_FAILED, source='packaging',
    ))
    for code, check_key, label in _VERIFICATION_CHECKLIST_ROWS:
        if code == 'hashes_verified':
            if not executed:
                state = CHECK_NOT_VERIFIED
            else:
                state = CHECK_PASSED if hashes_verified else CHECK_FAILED
            checklist.append(_checklist_row(
                code, label, state, source='verification',
                detail=(artifact_check or {}).get('detail') if executed else None,
            ))
            continue
        resolved = category(check_key)
        checklist.append(_checklist_row(
            code, label, str(resolved['status']), source='verification',
            detail=resolved.get('detail'),
        ))
    for code, label, codes in _COMPLETENESS_CHECKLIST_ROWS[1:]:
        statuses = [_completeness_category_status(completeness, item) for item in codes]
        if all(item is None for item in statuses):
            state = CHECK_NOT_VERIFIED if completeness is None else CHECK_NOT_APPLICABLE
        elif all(item in {'present', 'not_applicable', None} for item in statuses):
            state = CHECK_PASSED
        else:
            state = CHECK_FAILED
        checklist.append(_checklist_row(code, label, state, source='completeness'))

    required_count = (completeness or {}).get('required_count')
    present_count = (completeness or {}).get('present_count')
    evidence_complete = (
        isinstance(required_count, int)
        and isinstance(present_count, int)
        and required_count > 0
        and present_count >= required_count
    )
    required_evidence = category(CHECK_REQUIRED_EVIDENCE)

    verified_at = (verification or {}).get('verified_at') if executed else None
    contract = {
        # ── The single authoritative status ────────────────────────────────
        'overall_status': overall_status,
        'overall_label': STATUS_LABELS.get(overall_status, overall_status.replace('_', ' ').title()),
        'verified': overall_status == STATUS_VERIFIED,
        'executed': executed,
        'verified_at': verified_at,
        'verified_by_user_id': (verification or {}).get('verified_by_user_id') if executed else None,
        # The lifecycle projection the Integrity column renders. Emitted from the
        # SAME builder call as overall_status so the table and the detail view
        # cannot drift apart.
        'integrity_status': display_state.get('integrity_status'),
        'shield': _shield_for(overall_status, evidence_complete=evidence_complete),
        # ── Per-category outcomes ──────────────────────────────────────────
        'artifact_hashes': {
            'files_hashed': files_hashed,
            'files_verified': files_verified,
            'verifiable_count': verifiable_count,
            'total': total,
            'hash_failures': len(hash_failures),
            'failed_artifact_ids': hash_failures,
            'missing_artifact_ids': missing_artifacts,
            'hashes_verified': hashes_verified,
            'status': (artifact_check or {}).get('status') if executed else CHECK_NOT_VERIFIED,
            'label': (
                f'{files_verified} / {verifiable_count} valid' if executed
                else f'{files_hashed} hashed · not verified'
            ),
        },
        'merkle_root': merkle,
        'manifest_signature': signature,
        'policy_snapshot': category(CHECK_POLICY_SNAPSHOT),
        'provenance': category(CHECK_PROVENANCE),
        'required_evidence': required_evidence,
        'manifest_hash': category(CHECK_MANIFEST_HASH),
        # ── Completeness, reported as its OWN axis. Never an input to status. ──
        'completeness': {
            'score': (completeness or {}).get('score'),
            'status': (completeness or {}).get('status'),
            'required_count': required_count,
            'present_count': present_count,
            'missing_count': (completeness or {}).get('missing_count'),
            'unverifiable_count': (completeness or {}).get('unverifiable_count'),
            'complete': evidence_complete,
        },
        'checklist': checklist,
        'signer': (result or {}).get('signer') if executed else None,
        'failed_checks': (result or {}).get('failed_checks') or [] if executed else [],
        'unavailable_checks': (result or {}).get('unavailable_checks') or [] if executed else [],
    }
    if include_checks:
        # The full per-check detail the Package Verification panel renders.
        contract['checks'] = (result or {}).get('checks') or [] if executed else []
    return contract
