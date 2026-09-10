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
