"""Screen 9 — backend cryptographic verification of an evidence package.

The property under test throughout: VERIFIED is a CONCLUSION, never an
assumption. Concretely —

  C. Modifying one artifact makes verification FAIL.
  D. Modifying the manifest makes the signature verification FAIL.
  E. A missing artifact makes the package INCOMPLETE.
  F. A wrong signing key fails verification.
  G. A valid package returns VERIFIED.
  N. No failure path ever produces a green verified state.

plus the distinctions the truthfulness rules require:
  * "generated successfully" != "signed" != "verified" != "HSM-backed",
  * "we could not check the signature" != "this was tampered with",
  * a legacy (schema 1.0) manifest is not FAILED for lacking facts it never sealed.
"""
from __future__ import annotations

import copy

import pytest

from services.api.app import evidence_verification as verification
from services.api.app.evidence_manifest_signer import (
    EvidenceManifestSigner,
    SignerIdentity,
    resolve_manifest_signer,
)
from services.api.app.evidence_signing import build_evidence_manifest

WS = 'ws-verify-screen9'
PKG = 'pkg-verify-screen9'
INCIDENT = 'inc-verify-screen9'

REQUIRED = [
    'summary.json', 'incidents.json', 'alerts.json', 'detections.json',
    'response_actions.json', 'audit_log.json', 'evidence.json',
    'detection_metrics.json', 'investigation_timeline.json', 'policy_evaluations.json',
]

POLICY_SNAPSHOT = {
    'present': True,
    'policy_key': 'POL-MINT-007',
    'policy_version': 7,
    'decision': 'DENY',
    'decision_kind': 'enforcement',
    'evaluation_id': 'eval-1',
    'evaluated_at': '2026-02-01T10:42:18Z',
    'source': 'governance_policy_versions',
}


def _file_values() -> dict:
    return {
        'summary.json': {'export_id': PKG, 'incident_id': INCIDENT, 'schema_version': '1.1'},
        'incidents.json': [{'id': INCIDENT, 'status': 'investigating'}],
        'alerts.json': [{'id': 'alert-1', 'severity': 'critical'}],
        'detections.json': [{'id': 'det-1', 'detection_type': 'unmatched_issuance'}],
        'response_actions.json': [{'id': 'act-1', 'status': 'pending'}],
        'audit_log.json': [{'id': 'aud-1', 'action': 'incident.opened'}],
        'evidence.json': [{'tx_hash': '0x7a1d', 'block_number': 4211}],
        'detection_metrics.json': [{'id': 'dm-1', 'mttd_seconds': 12}],
        'investigation_timeline.json': {'present': True, 'workflow_stages': []},
        'policy_evaluations.json': [{'id': 'eval-1', 'decision': 'DENY', 'policy_version': 7}],
    }


def _provenance(paths) -> dict:
    return {
        path: {'media_type': 'application/json', 'domain': 'OPERATIONAL', 'source_record_type': 'evidence'}
        for path in paths
    }


def _sealed_package(*, policy_snapshot=POLICY_SNAPSHOT, required=None):
    """Build a real schema-2.0 manifest + seal over real canonical bytes."""
    values = _file_values()
    manifest, _ = build_evidence_manifest(
        export_id=PKG, export_type='proof_bundle', workspace_id=WS,
        generated_at='2026-02-01T10:42:20Z', generated_by_user_id='user-1',
        source_resource_type='incident', source_resource_id=INCIDENT,
        storage_backend='local', file_values=values,
        seal_merkle=True,
        policy_snapshot=policy_snapshot,
        required_artifacts=REQUIRED if required is None else required,
        file_provenance=_provenance(values),
    )
    seal = resolve_manifest_signer().sign(manifest)
    return manifest, seal, values


def _verify(manifest, seal, values, signer=None):
    return verification.verify_evidence_package_document(
        manifest=manifest, seal=seal, file_values=values, signer=signer,
        verified_at='2026-02-01T11:00:00Z', verified_by_user_id='user-1',
    )


def _check(result, key):
    return next(c for c in result['checks'] if c['check'] == key)


# ── G. A valid package returns VERIFIED ──────────────────────────────────────

def test_valid_package_returns_verified():
    result = _verify(*_sealed_package())
    assert result['status'] == verification.STATUS_VERIFIED
    assert result['verified'] is True
    assert result['failed_checks'] == []
    assert result['unavailable_checks'] == []


def test_verified_package_passes_every_documented_category():
    result = _verify(*_sealed_package())
    for key in (
        verification.CHECK_ARTIFACT_HASHES,
        verification.CHECK_MERKLE_ROOT,
        verification.CHECK_MANIFEST_HASH,
        verification.CHECK_MANIFEST_SIGNATURE,
        verification.CHECK_POLICY_SNAPSHOT,
        verification.CHECK_PROVENANCE,
        verification.CHECK_REQUIRED_EVIDENCE,
    ):
        assert _check(result, key)['status'] == verification.CHECK_PASSED, key


def test_artifact_hash_counts_are_real_not_hardcoded():
    manifest, seal, values = _sealed_package()
    result = _verify(manifest, seal, values)
    assert result['artifact_hashes']['total'] == len(values)
    assert result['artifact_hashes']['valid'] == len(values)
    assert _check(result, verification.CHECK_ARTIFACT_HASHES)['label'] == (
        f'{len(values)} / {len(values)} artifact hashes valid'
    )


def test_merkle_root_is_recomputed_and_compared_not_trusted():
    manifest, seal, values = _sealed_package()
    result = _verify(manifest, seal, values)
    merkle = result['merkle_root']
    assert merkle['valid'] is True
    assert merkle['computed'] == merkle['expected'] == manifest['merkle_root']
    assert merkle['expected']


# ── C. Modifying one artifact makes verification fail ────────────────────────

def test_modifying_one_artifact_fails_verification():
    manifest, seal, values = _sealed_package()
    values['alerts.json'] = [{'id': 'alert-1', 'severity': 'low', 'injected': True}]

    result = _verify(manifest, seal, values)

    assert result['status'] == verification.STATUS_VERIFICATION_FAILED
    assert result['verified'] is False
    assert result['artifact_hashes']['failed_artifact_ids'] == ['alerts.json']
    assert result['artifact_hashes']['valid'] == len(values) - 1


def test_a_single_artifact_mismatch_names_its_own_category():
    """A failure must not collapse into one generic boolean."""
    manifest, seal, values = _sealed_package()
    values['audit_log.json'] = [{'id': 'aud-1', 'action': 'TAMPERED'}]

    result = _verify(manifest, seal, values)

    assert result['failed_checks'] == [verification.CHECK_ARTIFACT_HASHES]
    # The signature still verifies (the MANIFEST is untouched) and the manifest
    # hash still recomputes — those categories report their own truth.
    assert _check(result, verification.CHECK_MANIFEST_SIGNATURE)['status'] == verification.CHECK_PASSED
    assert _check(result, verification.CHECK_MANIFEST_HASH)['status'] == verification.CHECK_PASSED


def test_artifact_tampering_does_not_silently_pass_the_merkle_check():
    """The Merkle root commits to the manifest's digests, and the artifact hash
    check ties those digests back to the real bytes — so tampering is caught."""
    manifest, seal, values = _sealed_package()
    values['evidence.json'] = [{'tx_hash': '0xDEADBEEF'}]
    result = _verify(manifest, seal, values)
    assert result['verified'] is False
    assert 'evidence.json' in result['artifact_hashes']['failed_artifact_ids']


# ── D. Modifying the manifest fails signature verification ───────────────────

def test_modifying_the_manifest_fails_signature_verification():
    manifest, seal, values = _sealed_package()
    tampered = copy.deepcopy(manifest)
    tampered['files'][0]['sha256'] = '0' * 64

    result = _verify(tampered, seal, values)

    assert result['status'] == verification.STATUS_VERIFICATION_FAILED
    assert result['manifest_signature']['valid'] is False
    assert _check(result, verification.CHECK_MANIFEST_SIGNATURE)['signature_state'] == 'invalid'


def test_swapping_the_sealed_merkle_root_is_caught_by_the_merkle_check():
    manifest, seal, values = _sealed_package()
    tampered = copy.deepcopy(manifest)
    tampered['merkle_root'] = 'f' * 64

    result = _verify(tampered, seal, values)

    assert result['status'] == verification.STATUS_VERIFICATION_FAILED
    assert result['merkle_root']['valid'] is False
    assert result['merkle_root']['computed'] == manifest['merkle_root']


def test_rewriting_the_manifest_hash_is_caught_by_the_manifest_hash_check():
    manifest, seal, values = _sealed_package()
    tampered = copy.deepcopy(manifest)
    tampered['manifest_sha256'] = 'a' * 64

    result = _verify(tampered, seal, values)

    assert result['manifest_hash']['valid'] is False
    assert result['status'] == verification.STATUS_VERIFICATION_FAILED


def test_a_signature_that_validates_but_a_failing_merkle_tree_is_not_verified():
    """Explicit truthfulness rule: a valid signature never rescues a bad tree.

    Re-sign the tampered manifest so the SIGNATURE is genuinely valid; the
    Merkle check must still fail the package.
    """
    manifest, _seal, values = _sealed_package()
    tampered = copy.deepcopy(manifest)
    tampered['merkle_root'] = 'b' * 64
    resigned = resolve_manifest_signer().sign(tampered)

    result = _verify(tampered, resigned, values)

    assert result['manifest_signature']['valid'] is True
    assert result['merkle_root']['valid'] is False
    assert result['status'] == verification.STATUS_VERIFICATION_FAILED
    assert result['verified'] is False


# ── E. A missing artifact makes the package incomplete ───────────────────────

def test_missing_artifact_makes_the_package_incomplete():
    manifest, seal, values = _sealed_package()
    values.pop('detections.json')

    result = _verify(manifest, seal, values)

    assert result['status'] == verification.STATUS_INCOMPLETE_PACKAGE
    assert result['verified'] is False
    assert result['artifact_hashes']['missing_artifact_ids'] == ['detections.json']
    assert result['required_evidence_complete'] is False


def test_missing_artifact_plus_tampering_reports_the_more_serious_failure():
    manifest, seal, values = _sealed_package()
    values.pop('detections.json')
    values['alerts.json'] = [{'id': 'alert-1', 'severity': 'low'}]

    result = _verify(manifest, seal, values)

    assert result['status'] == verification.STATUS_VERIFICATION_FAILED


def test_a_package_whose_manifest_lists_no_artifacts_is_never_verified():
    manifest, seal, values = _sealed_package()
    empty = copy.deepcopy(manifest)
    empty['files'] = []
    result = _verify(empty, seal, {})
    assert result['verified'] is False
    assert result['status'] == verification.STATUS_VERIFICATION_FAILED


# ── F. A wrong signing key fails verification ────────────────────────────────

def _signer_with_key(secret: bytes) -> EvidenceManifestSigner:
    class _WrongKeySigner(EvidenceManifestSigner):
        def verify(self, manifest, seal):
            import hmac

            from services.api.app.evidence_signing import canonical_json

            expected = hmac.new(secret, canonical_json(manifest), 'sha256').hexdigest()
            actual = str((seal or {}).get('signature') or '')
            valid = hmac.compare_digest(expected.encode(), actual.encode())
            return {
                'status': 'valid' if valid else 'invalid',
                'valid': valid,
                'reason': None if valid else 'signature_mismatch',
                'key_id': 'other-key', 'key_version': 'v1',
                'provider': 'env', 'algorithm': 'HMAC-SHA256',
            }

    return _WrongKeySigner(
        SignerIdentity(
            provider='env', algorithm='HMAC-SHA256', key_id='other-key', key_version='v1',
            hardware_backed=False, assurance='shared_secret_hmac',
            key_custody='application_memory', production_grade=True,
        )
    )


def test_wrong_signing_key_fails_verification():
    manifest, seal, values = _sealed_package()

    result = _verify(manifest, seal, values, signer=_signer_with_key(b'a-different-signing-key'))

    assert result['status'] == verification.STATUS_VERIFICATION_FAILED
    assert result['manifest_signature']['valid'] is False
    assert result['verified'] is False


def test_unsigned_package_is_not_verified():
    """Generated successfully is NOT signed, and not signed is NOT verified."""
    manifest, _seal, values = _sealed_package()

    result = _verify(manifest, None, values)

    assert result['status'] == verification.STATUS_SIGNATURE_UNAVAILABLE
    assert result['verified'] is False
    assert _check(result, verification.CHECK_MANIFEST_SIGNATURE)['signature_state'] == 'absent'


def test_unavailable_verification_key_is_not_reported_as_tampering():
    """"We could not check it" must never render as "it was tampered with"."""
    manifest, seal, values = _sealed_package()

    class _NoKeySigner(EvidenceManifestSigner):
        def verify(self, manifest, seal):
            return {
                'status': 'unavailable', 'valid': False, 'reason': 'verification_key_unavailable',
                'key_id': None, 'key_version': None, 'provider': None, 'algorithm': None,
            }

    signer = _NoKeySigner(
        SignerIdentity(
            provider='env', algorithm='HMAC-SHA256', key_id='k', key_version='v',
            hardware_backed=False, assurance='shared_secret_hmac',
            key_custody='application_memory', production_grade=False,
        )
    )
    result = _verify(manifest, seal, values, signer=signer)

    assert result['status'] == verification.STATUS_SIGNATURE_UNAVAILABLE
    assert result['verified'] is False
    signature_check = _check(result, verification.CHECK_MANIFEST_SIGNATURE)
    assert signature_check['status'] == verification.CHECK_UNAVAILABLE
    assert signature_check['status'] != verification.CHECK_FAILED
    # And the legacy projection reports tri-state None, so the integrity status
    # never becomes integrity_failed for an uncheckable package.
    assert verification.legacy_verification_view(result)['valid'] is None


# ── N. No failure path ever produces a green verified state ──────────────────

@pytest.mark.parametrize(
    'mutate',
    [
        pytest.param(lambda m, s, v: v.__setitem__('alerts.json', [{'x': 1}]), id='artifact_tampered'),
        pytest.param(lambda m, s, v: v.pop('audit_log.json'), id='artifact_missing'),
        pytest.param(lambda m, s, v: m.__setitem__('merkle_root', 'c' * 64), id='merkle_mismatch'),
        pytest.param(lambda m, s, v: m.__setitem__('manifest_sha256', 'd' * 64), id='manifest_hash_mismatch'),
        pytest.param(lambda m, s, v: s.__setitem__('signature', 'e' * 64), id='signature_invalid'),
        pytest.param(lambda m, s, v: m.__setitem__('policy_snapshot', {'present': True}), id='policy_snapshot_incomplete'),
        pytest.param(lambda m, s, v: m['files'][0].pop('sha256'), id='provenance_incomplete'),
    ],
)
def test_no_failure_mode_ever_reports_verified(mutate):
    manifest, seal, values = _sealed_package()
    mutate(manifest, seal, values)

    result = _verify(manifest, seal, values)

    assert result['verified'] is False
    assert result['status'] != verification.STATUS_VERIFIED
    assert result['status_label'] != 'Verified'
    assert verification.legacy_verification_view(result)['valid'] is not True


def test_generated_but_unverified_package_has_no_recorded_verification():
    """A package that was merely generated carries no verification result at all."""
    manifest, _seal, _values = _sealed_package()
    assert 'verification' not in manifest
    assert 'status' not in manifest


# ── Signer assurance truthfulness ────────────────────────────────────────────

def test_result_never_claims_hardware_backed_signing_in_this_build():
    result = _verify(*_sealed_package())
    assert result['signer']['hardware_backed'] is False
    assert result['signer']['assurance'] == 'shared_secret_hmac'
    assert result['signer']['key_custody'] == 'application_memory'


def test_signer_status_reports_no_hsm_or_kms_backing():
    from services.api.app.evidence_manifest_signer import signer_status

    status = signer_status()
    assert status['hsm_backed'] is False
    assert status['kms_backed'] is False
    assert status['hardware_backed'] is False


# ── Legacy (schema 1.0) manifests stay verifiable and truthful ───────────────

def _legacy_package():
    values = _file_values()
    manifest, _ = build_evidence_manifest(
        export_id=PKG, export_type='proof_bundle', workspace_id=WS,
        generated_at='2026-01-01T00:00:00Z', generated_by_user_id='user-1',
        source_resource_type='incident', source_resource_id=INCIDENT,
        storage_backend='local', file_values=values,
    )
    return manifest, resolve_manifest_signer().sign(manifest), values


def test_legacy_manifest_still_verifies_and_is_not_failed_for_missing_v2_facts():
    """Backward compatibility: a 1.0 package never sealed a Merkle root, a policy
    snapshot or a required-artifact list. Reporting those as FAILED would be
    untrue; reporting them as PASSED would claim commitments that do not exist."""
    result = _verify(*_legacy_package())

    assert result['status'] == verification.STATUS_VERIFIED
    assert _check(result, verification.CHECK_MERKLE_ROOT)['status'] == verification.CHECK_NOT_APPLICABLE
    assert _check(result, verification.CHECK_POLICY_SNAPSHOT)['status'] == verification.CHECK_NOT_APPLICABLE
    assert _check(result, verification.CHECK_REQUIRED_EVIDENCE)['status'] == verification.CHECK_NOT_APPLICABLE
    # And those rows are explicitly non-mandatory with a stated reason.
    assert _check(result, verification.CHECK_MERKLE_ROOT)['mandatory'] is False
    assert _check(result, verification.CHECK_MERKLE_ROOT)['detail']


def test_legacy_manifest_tampering_is_still_caught():
    manifest, seal, values = _legacy_package()
    values['summary.json'] = {'export_id': PKG, 'incident_id': 'OTHER'}
    result = _verify(manifest, seal, values)
    assert result['status'] == verification.STATUS_VERIFICATION_FAILED


# ── Policy snapshot semantics ────────────────────────────────────────────────

def test_absent_policy_evaluation_is_not_applicable_rather_than_failed():
    """An incident with no policy evaluation is a truthful fact, not a defect."""
    manifest, seal, values = _sealed_package(
        policy_snapshot={'present': False, 'reason': 'No policy evaluation was recorded for this incident.'}
    )
    result = _verify(manifest, seal, values)
    assert result['status'] == verification.STATUS_VERIFIED
    check = _check(result, verification.CHECK_POLICY_SNAPSHOT)
    assert check['status'] == verification.CHECK_NOT_APPLICABLE
    assert 'No policy evaluation' in check['detail']


def test_policy_snapshot_check_reports_the_sealed_version():
    result = _verify(*_sealed_package())
    check = _check(result, verification.CHECK_POLICY_SNAPSHOT)
    assert check['policy_key'] == 'POL-MINT-007'
    assert check['policy_version'] == 7


# ── Legacy projection compatibility ──────────────────────────────────────────

def test_legacy_projection_preserves_the_pre_existing_verification_shape():
    result = _verify(*_sealed_package())
    view = verification.legacy_verification_view(result)
    for key in ('valid', 'files_total', 'files_verified', 'files_failed', 'missing_files',
                'manifest_ok', 'seal_status'):
        assert key in view
    assert view['valid'] is True
    assert view['seal_status'] == 'valid'
    assert view['manifest_ok'] is True
    assert view['verification_status'] == verification.STATUS_VERIFIED
