"""Screen 9 — the structured evidence ZIP (Download Evidence Package).

  J. The archive contains the expected files: manifest.json, manifest.sig,
     verification.json, the ORIGINAL artifacts grouped by provenance domain, the
     investigation report and offline re-verification material.
  K. The archive contains NO private signing material, API secrets, database
     credentials, storage credentials or internal tokens.
  +  Artifact bytes in the archive hash to exactly the digests manifest.json
     records, so an auditor can re-verify offline with sha256sum alone.
  +  Entry names are ZIP-slip safe and the build fails CLOSED otherwise.
  +  The same package always produces byte-identical archive bytes.
"""
from __future__ import annotations

import hashlib
import io
import json
import zipfile

import pytest

from services.api.app import evidence_archive as archive
from services.api.app import evidence_verification as verification
from services.api.app.evidence_manifest_signer import resolve_manifest_signer
from services.api.app.evidence_signing import build_evidence_manifest, canonical_json

PKG = 'pkg-archive-1'
PKG_NUMBER = 'EV-2026-017'
WS = 'ws-archive-1'
INCIDENT = 'inc-archive-1'

POLICY_SNAPSHOT = {
    'present': True, 'policy_key': 'POL-MINT-007', 'policy_version': 7,
    'decision': 'DENY', 'decision_kind': 'enforcement', 'evaluation_id': 'eval-1',
    'evaluated_at': '2026-02-01T10:42:18Z', 'source': 'governance_policy_versions',
}


def _values() -> dict:
    return {
        'summary.json': {'export_id': PKG, 'incident_id': INCIDENT, 'asset_id': 'rwa-001'},
        'alerts.json': [{'id': 'alert-1', 'severity': 'critical'}],
        'incidents.json': [{'id': INCIDENT, 'status': 'investigating'}],
        'evidence.json': [{'tx_hash': '0x7a1d', 'block_number': 4211}],
        'policy_evaluations.json': [{'id': 'eval-1', 'decision': 'DENY'}],
        'response_actions.json': [{'id': 'act-1', 'status': 'pending'}],
        'audit_log.json': [{'id': 'aud-1', 'action': 'incident.opened'}],
    }


def _sealed():
    values = _values()
    manifest, _ = build_evidence_manifest(
        export_id=PKG, export_type='proof_bundle', workspace_id=WS,
        generated_at='2026-02-01T10:42:20Z', generated_by_user_id='user-1',
        source_resource_type='incident', source_resource_id=INCIDENT,
        storage_backend='local', file_values=values, seal_merkle=True,
        policy_snapshot=POLICY_SNAPSHOT,
        required_artifacts=sorted(values.keys()),
        file_provenance={
            path: {'domain': 'OPERATIONAL', 'source_record_type': 'evidence'} for path in values
        },
    )
    signer = resolve_manifest_signer()
    seal = signer.sign(manifest)
    result = verification.verify_evidence_package_document(
        manifest=manifest, seal=seal, file_values=values, signer=signer,
        verified_at='2026-02-01T11:00:00Z', verified_by_user_id='user-1',
    )
    return manifest, seal, values, result, signer


def _build(**overrides) -> bytes:
    manifest, seal, values, result, signer = _sealed()
    kwargs = dict(
        package_id=PKG, package_number=PKG_NUMBER, manifest=manifest, seal=seal,
        file_values=values, verification=result, signer=signer.identity.as_dict(),
        summary=values['summary.json'], generated_at='2026-02-01T11:00:00Z',
    )
    kwargs.update(overrides)
    return archive.build_evidence_archive(**kwargs)


def _names(data: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return sorted(zf.namelist())


def _read(data: bytes, name: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return zf.read(name)


# ── J. Expected files ────────────────────────────────────────────────────────

def test_archive_contains_the_expected_top_level_files():
    names = _names(_build())
    assert f'{PKG_NUMBER}/manifest.json' in names
    assert f'{PKG_NUMBER}/manifest.sig' in names
    assert f'{PKG_NUMBER}/verification.json' in names
    assert f'{PKG_NUMBER}/reports/investigation.md' in names
    assert f'{PKG_NUMBER}/verification/README.txt' in names
    assert f'{PKG_NUMBER}/verification/signing-key.json' in names


def test_archive_contains_the_original_artifacts_grouped_by_provenance_domain():
    names = _names(_build())
    assert f'{PKG_NUMBER}/artifacts/on-chain/evidence.json' in names
    assert f'{PKG_NUMBER}/artifacts/operational/alerts.json' in names
    assert f'{PKG_NUMBER}/artifacts/operational/incidents.json' in names
    assert f'{PKG_NUMBER}/artifacts/policy/policy_evaluations.json' in names
    assert f'{PKG_NUMBER}/artifacts/human-actions/response_actions.json' in names
    assert f'{PKG_NUMBER}/artifacts/human-actions/audit_log.json' in names
    assert f'{PKG_NUMBER}/artifacts/package/summary.json' in names


def test_archive_contains_every_artifact_the_manifest_lists():
    data = _build()
    manifest = json.loads(_read(data, f'{PKG_NUMBER}/manifest.json'))
    names = set(_names(data))
    for entry in manifest['files']:
        path = entry['path']
        expected = f'{PKG_NUMBER}/artifacts/{archive.artifact_domain(path)}/{path}'
        assert expected in names, path


def test_archived_artifact_bytes_hash_to_the_manifest_digests():
    """An auditor can re-verify the archive offline with sha256sum alone."""
    data = _build()
    manifest = json.loads(_read(data, f'{PKG_NUMBER}/manifest.json'))
    for entry in manifest['files']:
        path = entry['path']
        payload = _read(data, f'{PKG_NUMBER}/artifacts/{archive.artifact_domain(path)}/{path}')
        assert hashlib.sha256(payload).hexdigest() == entry['sha256'], path
        assert len(payload) == entry['size_bytes'], path


def test_archived_artifacts_are_the_originals_not_ui_summaries():
    data = _build()
    payload = _read(data, f'{PKG_NUMBER}/artifacts/on-chain/evidence.json')
    assert json.loads(payload) == _values()['evidence.json']
    assert payload == canonical_json(_values()['evidence.json'], path='evidence.json')


def test_archive_carries_the_recomputed_verification_result():
    data = _build()
    result = json.loads(_read(data, f'{PKG_NUMBER}/verification.json'))
    assert result['status'] == verification.STATUS_VERIFIED
    assert result['merkle_root']['valid'] is True
    assert result['artifact_hashes']['total'] == len(_values())


def test_investigation_report_cites_the_package_facts():
    report = _read(_build(), f'{PKG_NUMBER}/reports/investigation.md').decode('utf-8')
    manifest, *_ = _sealed()
    assert PKG_NUMBER in report
    assert INCIDENT in report
    assert manifest['merkle_root'] in report
    assert 'POL-MINT-007' in report
    assert 'Version' in report or 'Policy version: 7' in report
    # It must state plainly that it does not replace the artifacts.
    assert 'does NOT replace the original evidence artifacts' in report


def test_unsealed_package_omits_manifest_sig_rather_than_faking_one():
    names = _names(_build(seal=None))
    assert f'{PKG_NUMBER}/manifest.sig' not in names
    assert f'{PKG_NUMBER}/manifest.json' in names


# ── K. No credential material ────────────────────────────────────────────────

def test_archive_contains_no_private_signing_material_or_secrets():
    from services.api.app import evidence_signing

    data = _build(secret_denylist=(evidence_signing._DEV_FALLBACK_SECRET,))
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        blob = b''.join(zf.read(name) for name in zf.namelist())

    assert evidence_signing._DEV_FALLBACK_SECRET not in blob
    for marker in (
        b'EXPORT_SIGNING_SECRET', b'EVIDENCE_SIGNING_SECRET', b'AUTH_TOKEN_SECRET',
        b'SECRET_ENCRYPTION_KEY', b'AWS_SECRET_ACCESS_KEY', b'AWS_ACCESS_KEY_ID',
        b'DATABASE_URL', b'POSTGRES_PASSWORD', b'PRIVATE KEY', b'BEGIN RSA',
    ):
        assert marker not in blob, marker


def test_signing_key_file_carries_public_identifiers_only():
    payload = json.loads(_read(_build(), f'{PKG_NUMBER}/verification/signing-key.json'))
    assert payload['contains_key_material'] is False
    assert set(payload) >= {'provider', 'algorithm', 'key_id', 'hardware_backed'}
    assert 'material' not in payload and 'secret' not in payload and 'signature' not in payload
    # Truthful assurance: this build is not hardware-backed.
    assert payload['hardware_backed'] is False


def test_build_fails_closed_when_secret_material_would_be_included():
    """A leak is a defect, not a warning — the archive is never emitted."""
    manifest, seal, values, result, signer = _sealed()
    values['leak.json'] = {'note': 'AWS_SECRET_ACCESS_KEY=AKIAsomething'}
    manifest = dict(manifest)
    manifest['files'] = [*manifest['files'], {'path': 'leak.json', 'sha256': 'a' * 64, 'size_bytes': 1}]

    with pytest.raises(archive.ArchiveSafetyError) as exc:
        archive.build_evidence_archive(
            package_id=PKG, package_number=PKG_NUMBER, manifest=manifest, seal=seal,
            file_values=values, verification=result, signer=signer.identity.as_dict(),
            summary=None, generated_at='2026-02-01T11:00:00Z',
        )
    assert exc.value.reason == 'secret_material_in_archive'


# ── ZIP-slip / path traversal ────────────────────────────────────────────────

@pytest.mark.parametrize(
    'hostile',
    [
        '../../etc/passwd',
        '/etc/passwd',
        '..',
        '.',
        'a/../../b',
        'C:\\Windows\\system32',
        'evil\\path.json',
        'nul\x00.json',
        'ctrl\x1fchar.json',
        '~/.ssh/id_rsa',
    ],
)
def test_hostile_archive_names_are_rejected(hostile):
    with pytest.raises(archive.ArchiveSafetyError):
        archive.safe_archive_segment(hostile)


def test_archive_entry_name_cannot_escape_the_package_root():
    with pytest.raises(archive.ArchiveSafetyError):
        archive.archive_entry_name(PKG_NUMBER, 'artifacts', '../../escape.json')


def test_a_manifest_listing_a_traversal_path_fails_the_build_closed():
    manifest, seal, values, result, signer = _sealed()
    manifest = dict(manifest)
    manifest['files'] = [*manifest['files'], {'path': '../../escape.json', 'sha256': 'b' * 64, 'size_bytes': 1}]
    values['../../escape.json'] = {'x': 1}

    with pytest.raises(archive.ArchiveSafetyError):
        archive.build_evidence_archive(
            package_id=PKG, package_number=PKG_NUMBER, manifest=manifest, seal=seal,
            file_values=values, verification=result, signer=signer.identity.as_dict(),
            summary=None, generated_at='2026-02-01T11:00:00Z',
        )


def test_every_emitted_entry_stays_under_the_package_root():
    for name in _names(_build()):
        assert name.startswith(f'{PKG_NUMBER}/')
        assert '..' not in name.split('/')
        assert not name.startswith('/')


# ── Determinism ──────────────────────────────────────────────────────────────

def test_the_same_package_always_produces_byte_identical_archive_bytes():
    assert _build() == _build()


def test_archive_entries_carry_a_fixed_timestamp():
    with zipfile.ZipFile(io.BytesIO(_build())) as zf:
        for info in zf.infolist():
            assert info.date_time == (1980, 1, 1, 0, 0, 0)


# ── Package Contents listing ─────────────────────────────────────────────────

def test_contents_listing_reports_unavailable_files_truthfully():
    manifest, seal, values, *_ = _sealed()
    listing = archive.archive_contents_listing(
        package_number=PKG_NUMBER, manifest=manifest, seal=None,
        file_values=values, verification_available=False, pdf_available=False,
    )
    by_key = {entry['key']: entry for entry in listing}

    assert by_key['artifacts']['available'] is True
    assert by_key['artifacts']['count'] == len(values)
    # An unsigned package must not list manifest.sig as present.
    assert by_key['signature']['available'] is False
    assert by_key['signature']['unavailable_reason']
    # PDF rendering is not configured — it is reported unavailable, never faked.
    assert by_key['report_pdf']['available'] is False
    assert 'PDF rendering is not configured' in by_key['report_pdf']['unavailable_reason']
    # An unverified package has no verification.json to offer yet.
    assert by_key['verification']['available'] is False


def test_contents_artifact_count_is_dynamic_not_hardcoded():
    manifest, seal, values, *_ = _sealed()
    trimmed = dict(list(values.items())[:3])
    listing = archive.archive_contents_listing(
        package_number=PKG_NUMBER, manifest=manifest, seal=seal,
        file_values=trimmed, verification_available=True,
    )
    artifacts = next(entry for entry in listing if entry['key'] == 'artifacts')
    assert artifacts['count'] == 3
    assert artifacts['declared_count'] == len(values)
