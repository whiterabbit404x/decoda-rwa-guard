"""Screen 9 — ONE canonical verification result for every surface.

These tests lock in the fix for the verification-state contradiction production
showed for a real package:

    Table            Integrity = Verified
    Clerk sidebar    Files Hashed 9 · Files Verified 9 · Integrity Failures 0
    Checklist        "Hashes verified"  ✗

Three surfaces, three independent derivations of the same fact. The checklist
was served from ``completeness.checklist`` — a snapshot FROZEN into the package
summary at BUILD time (computed with ``manifest_verified=None``, before any
verification could possibly have run) and returned unchanged by
``reconcile_completeness_hash_evidence`` for every healthy package. So a package
that verified successfully kept a checklist saying its hashes were unverified,
forever.

The contract under test: ``build_verification_contract`` is the ONLY place a
verification outcome is decided. The Evidence Package table, the package detail
view, the Crypto-Auditing Clerk and the Verification Checklist all project it.

Nothing here hardcodes a package number, an artifact count or a badge.
"""
from __future__ import annotations

import copy
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from services.api.app import evidence_completeness as ec
from services.api.app import evidence_verification as ev
from services.api.app import pilot
from services.api.app.evidence_manifest_signer import resolve_manifest_signer
from services.api.app.evidence_signing import build_evidence_manifest

WS = 'ws-contract-screen9'
PKG = 'pkg-contract-screen9'
INCIDENT = 'inc-contract-screen9'

# Nine artifacts — the production shape (Files Hashed 9 / Files Verified 9), but
# every assertion below is written against the package's OWN counts, never 9.
REQUIRED = [
    'summary.json', 'incidents.json', 'alerts.json', 'detections.json',
    'response_actions.json', 'audit_log.json', 'evidence.json',
    'investigation_timeline.json', 'policy_evaluations.json',
]

POLICY_SNAPSHOT = {
    'present': True,
    'policy_key': 'POL-RWA-011',
    'policy_version': 11,
    'decision': 'DENY',
    'decision_kind': 'enforcement',
    'evaluation_id': 'eval-c1',
    'evaluated_at': '2026-03-02T09:15:00Z',
    'source': 'governance_policy_versions',
}


def _file_values() -> dict:
    return {
        'summary.json': {'export_id': PKG, 'incident_id': INCIDENT, 'schema_version': '1.1'},
        'incidents.json': [{'id': INCIDENT, 'status': 'closed'}],
        'alerts.json': [{'id': 'alert-1', 'severity': 'critical'}],
        'detections.json': [{'id': 'det-1', 'detection_type': 'unmatched_issuance'}],
        'response_actions.json': [{'id': 'act-1', 'status': 'executed'}],
        'audit_log.json': [{'id': 'aud-1', 'action': 'incident.opened'}],
        'evidence.json': [{'tx_hash': '0x9f2c', 'block_number': 7781}],
        'investigation_timeline.json': {'present': True, 'workflow_stages': []},
        'policy_evaluations.json': [{'id': 'eval-c1', 'decision': 'DENY', 'policy_version': 11}],
    }


def _provenance(paths) -> dict:
    return {
        path: {'media_type': 'application/json', 'domain': 'OPERATIONAL', 'source_record_type': 'evidence'}
        for path in paths
    }


def _sealed(*, policy_snapshot=POLICY_SNAPSHOT, required=None, seal_merkle=True):
    values = _file_values()
    manifest, _ = build_evidence_manifest(
        export_id=PKG, export_type='proof_bundle', workspace_id=WS,
        generated_at='2026-03-02T09:15:04Z', generated_by_user_id='user-1',
        source_resource_type='incident', source_resource_id=INCIDENT,
        storage_backend='local', file_values=values,
        seal_merkle=seal_merkle,
        policy_snapshot=policy_snapshot,
        required_artifacts=REQUIRED if required is None else required,
        file_provenance=_provenance(values),
    )
    seal = resolve_manifest_signer().sign(manifest)
    return manifest, seal, values


def _verify(manifest, seal, values, *, verified_at='2026-03-02T10:00:00Z'):
    """Run the REAL verification service and project it as the persisted record."""
    result = ev.verify_evidence_package_document(
        manifest=manifest, seal=seal, file_values=values,
        verified_at=verified_at, verified_by_user_id='user-1',
    )
    record = ev.legacy_verification_view(result)
    record['result'] = result
    record['manifest_sha256'] = manifest.get('manifest_sha256')
    return result, record


def _package(**over) -> dict:
    base = {
        'id': PKG,
        'status': 'completed',
        'export_type': 'proof_bundle',
        'incident_id': INCIDENT,
        'size_bytes': 4096,
        'integrity_hash': 'c' * 64,
        'manifest_sha256': 'c' * 64,
        'manifest_file_count': len(REQUIRED),
        'files_hashed': len(REQUIRED),
        'completeness_score': 100,
    }
    base.update(over)
    return base


def _full_completeness() -> dict:
    """A genuinely 100%-complete evidence snapshot (no verification involved)."""
    return ec.compute_evidence_completeness({
        'incident_status': 'closed', 'evidence_source_type': 'live',
        'has_incident': True, 'has_alert': True, 'has_detection': True,
        'has_telemetry': True, 'has_asset': True, 'has_audit_events': True,
        'has_investigation_timeline': True, 'has_chain_metadata': True,
        'has_manifest': True, 'files_hashed': len(REQUIRED),
        'response_action_count': 1, 'executed_action_count': 1,
        'rejected_action_count': 0, 'requires_approval': True,
        'approval_present': True, 'has_execution_result': True,
        'manifest_verified': None,
    })


def _contract(package: dict, verification=None, completeness=None, **kwargs):
    state = ec.get_evidence_package_display_state(package)
    return ev.build_verification_contract(
        package=package, display_state=state,
        verification=verification, completeness=completeness, **kwargs,
    )


def _row(contract: dict, code: str) -> dict:
    for item in contract['checklist']:
        if item['code'] == code:
            return item
    raise AssertionError(f'checklist row {code!r} missing')


# ── 1. The exact production contradiction ────────────────────────────────────

def test_all_artifacts_hashed_and_verified_yields_hashes_verified_true():
    """N hashed + N verified + zero failures => hashes_verified is TRUE.

    This is the state production rendered as "Hashes verified ✗".
    """
    manifest, seal, values = _sealed()
    result, record = _verify(manifest, seal, values)
    assert result['status'] == ev.STATUS_VERIFIED

    contract = _contract(_package(verification=record), verification=record,
                         completeness=_full_completeness())

    hashes = contract['artifact_hashes']
    assert hashes['files_hashed'] == len(REQUIRED)
    assert hashes['files_verified'] == len(REQUIRED)
    assert hashes['hash_failures'] == 0
    assert hashes['hashes_verified'] is True
    # The checklist row — the one that used to read ✗ — now agrees.
    assert _row(contract, 'hashes_verified')['state'] == 'passed'
    assert _row(contract, 'hashes_verified')['present'] is True


def test_checklist_can_never_contradict_the_counters_it_sits_beside():
    """The production regression, asserted as an invariant over the contract.

    Files Verified == verifiable count AND zero failures AND Integrity=Verified
    can never coexist with a checklist row saying hashes are unverified.
    """
    manifest, seal, values = _sealed()
    _, record = _verify(manifest, seal, values)
    package = _package(verification=record)
    contract = _contract(package, verification=record, completeness=_full_completeness())

    counters_say_verified = (
        contract['artifact_hashes']['files_verified'] == contract['artifact_hashes']['verifiable_count']
        and contract['artifact_hashes']['hash_failures'] == 0
        and contract['overall_status'] == ev.STATUS_VERIFIED
    )
    checklist_says_verified = _row(contract, 'hashes_verified')['state'] == 'passed'
    assert counters_say_verified == checklist_says_verified


def test_stale_build_time_checklist_never_reaches_the_contract():
    """A frozen snapshot claiming hashes are unverified cannot survive a real pass.

    The build-time completeness snapshot is passed in verbatim (its own checklist
    still says hashes_verified is not verified, because it was computed before
    verification ran). The canonical contract must ignore it.
    """
    build_time = _full_completeness()
    assert _row_in(build_time['checklist'], 'hashes_verified')['state'] == 'not_verified'

    manifest, seal, values = _sealed()
    _, record = _verify(manifest, seal, values)
    contract = _contract(_package(verification=record), verification=record,
                         completeness=build_time)
    assert _row(contract, 'hashes_verified')['state'] == 'passed'


def _row_in(rows, code):
    for item in rows:
        if item['code'] == code:
            return item
    raise AssertionError(f'row {code!r} missing')


# ── 2. Partial hash verification is never VERIFIED ───────────────────────────

def test_one_artifact_short_is_not_verified():
    """N hashed + (N-1) verified must NOT produce VERIFIED."""
    manifest, seal, values = _sealed()
    tampered = copy.deepcopy(values)
    tampered['alerts.json'] = [{'id': 'alert-1', 'severity': 'low'}]  # one byte-changed artifact

    result, record = _verify(manifest, seal, tampered)
    contract = _contract(_package(verification=record), verification=record,
                         completeness=_full_completeness())

    hashes = contract['artifact_hashes']
    assert hashes['files_verified'] == len(REQUIRED) - 1
    assert hashes['hash_failures'] == 1
    assert hashes['hashes_verified'] is False
    assert contract['overall_status'] == ev.STATUS_VERIFICATION_FAILED
    assert contract['verified'] is False
    assert _row(contract, 'hashes_verified')['state'] == 'failed'


def test_hashes_verified_ignores_the_mere_existence_of_sha256_fields():
    """Stored SHA-256s are a packaging fact, never a verification outcome."""
    package = _package()  # hashes recorded, verification never run
    contract = _contract(package, verification=None, completeness=_full_completeness())
    assert contract['artifact_hashes']['files_hashed'] == len(REQUIRED)
    assert contract['artifact_hashes']['files_verified'] == 0
    assert contract['artifact_hashes']['hashes_verified'] is False
    assert _row(contract, 'hashes_generated')['state'] == 'passed'
    assert _row(contract, 'hashes_verified')['state'] == 'not_verified'


def test_missing_artifacts_are_excluded_from_the_verifiable_denominator():
    """Hashes that COULD be checked and matched are reported verified even when
    the package is separately INCOMPLETE — the two facts stay independent."""
    manifest, seal, values = _sealed()
    del values['alerts.json']  # listed in the manifest, absent from storage
    result, record = _verify(manifest, seal, values)

    contract = _contract(_package(verification=record), verification=record)
    hashes = contract['artifact_hashes']
    assert hashes['verifiable_count'] == len(REQUIRED) - 1
    assert hashes['files_verified'] == len(REQUIRED) - 1
    assert hashes['hashes_verified'] is True          # everything checkable matched
    assert contract['overall_status'] == ev.STATUS_INCOMPLETE_PACKAGE  # but incomplete
    assert contract['verified'] is False
    assert contract['shield']['verified'] is False


# ── 3. Completeness is not integrity ─────────────────────────────────────────

def test_hundred_percent_completeness_without_verification_is_not_verified():
    completeness = _full_completeness()
    assert completeness['score'] == 100
    assert completeness['missing_count'] == 0

    contract = _contract(_package(), verification=None, completeness=completeness)
    assert contract['overall_status'] == ev.STATUS_NOT_VERIFIED
    assert contract['verified'] is False
    assert contract['executed'] is False
    # 100% complete + nothing verified => READY FOR VERIFICATION, never green.
    assert contract['shield']['state'] == ev.SHIELD_READY_FOR_VERIFICATION
    assert contract['shield']['verified'] is False
    assert contract['completeness']['score'] == 100
    assert contract['completeness']['complete'] is True


def test_completeness_score_never_promotes_a_package_to_verified():
    """The completeness axis is reported, never consulted for overall_status."""
    for score in (0, 40, 80, 100):
        completeness = dict(_full_completeness(), score=score)
        contract = _contract(_package(), verification=None, completeness=completeness)
        assert contract['overall_status'] == ev.STATUS_NOT_VERIFIED
        assert contract['verified'] is False


# ── 4/5/6. One result, every surface ─────────────────────────────────────────

def test_table_detail_clerk_and_checklist_share_one_backend_result():
    manifest, seal, values = _sealed()
    _, record = _verify(manifest, seal, values)
    package = _package(verification=record)
    state = ec.get_evidence_package_display_state(package)
    contract = ev.build_verification_contract(
        package=package, display_state=state, verification=record,
        completeness=_full_completeness(),
    )

    # Table Integrity column (integrity_status) and the canonical overall status
    # are emitted by the SAME builder call and agree by construction.
    assert contract['integrity_status'] == state['integrity_status'] == ec.INTEGRITY_VERIFIED
    assert contract['overall_status'] == ev.STATUS_VERIFIED
    # Clerk counters come from the contract, not from a parallel calculation.
    assert contract['artifact_hashes']['files_verified'] == record['files_verified']
    assert contract['artifact_hashes']['hash_failures'] == len(record['files_failed'])
    # Checklist rows are projections of the same per-check outcomes.
    assert _row(contract, 'merkle_root')['state'] == contract['merkle_root']['status']
    assert _row(contract, 'manifest_signature')['state'] == contract['manifest_signature']['status']
    assert _row(contract, 'provenance')['state'] == contract['provenance']['status']
    assert _row(contract, 'policy_snapshot')['state'] == contract['policy_snapshot']['status']


def test_integrity_status_and_overall_status_agree_across_every_state():
    """``VERIFIED`` in one projection iff ``VERIFIED`` in the other. Always."""
    manifest, seal, values = _sealed()
    _, passing = _verify(manifest, seal, values)
    tampered = copy.deepcopy(values)
    tampered['audit_log.json'] = [{'id': 'aud-9'}]
    _, failing = _verify(manifest, seal, tampered)

    cases = [
        _package(verification=passing),
        _package(verification=failing),
        _package(),                                   # never verified
        _package(verification=passing, superseded=True),
        _package(integrity_hash=None, manifest_sha256=None, manifest_file_count=None,
                 files_hashed=0, completeness_score=None),  # legacy export
    ]
    for package in cases:
        contract = _contract(package, verification=package.get('verification'))
        green_overall = contract['overall_status'] == ev.STATUS_VERIFIED
        green_integrity = contract['integrity_status'] == ec.INTEGRITY_VERIFIED
        assert green_overall == green_integrity, package
        assert contract['shield']['verified'] == green_overall


def test_checklist_only_lists_checks_the_backend_actually_runs():
    """Every verification row names a real check in the verification service."""
    manifest, seal, values = _sealed()
    _, record = _verify(manifest, seal, values)
    contract = _contract(_package(verification=record), verification=record,
                         completeness=_full_completeness())

    executed = {check['check'] for check in contract['checks']}
    for code, check_key, _label in ev._VERIFICATION_CHECKLIST_ROWS:
        assert check_key in executed, f'{code} renders a check the backend never ran'
    # Nothing is faked: every row declares whether it is crypto or completeness.
    assert all(row['source'] in {'verification', 'completeness', 'packaging'}
               for row in contract['checklist'])


# ── 7/8/9. Real cryptographic failures ───────────────────────────────────────

def test_merkle_mismatch_produces_failed():
    manifest, seal, values = _sealed()
    broken = copy.deepcopy(manifest)
    broken['merkle_root'] = '0x' + ('f' * 64)
    result = ev.verify_evidence_package_document(
        manifest=broken, seal=seal, file_values=values, verified_at='2026-03-02T11:00:00Z')
    record = ev.legacy_verification_view(result)
    record['result'] = result

    contract = _contract(_package(verification=record), verification=record)
    assert contract['merkle_root']['valid'] is False
    assert contract['overall_status'] == ev.STATUS_VERIFICATION_FAILED
    assert contract['shield']['state'] == ev.SHIELD_INTEGRITY_CHECK_FAILED
    assert _row(contract, 'merkle_root')['state'] == 'failed'


def test_manifest_signature_failure_produces_failed():
    manifest, seal, values = _sealed()
    forged = dict(seal, signature='00' * 32)
    result = ev.verify_evidence_package_document(
        manifest=manifest, seal=forged, file_values=values, verified_at='2026-03-02T11:00:00Z')
    record = ev.legacy_verification_view(result)
    record['result'] = result

    contract = _contract(_package(verification=record), verification=record)
    assert contract['manifest_signature']['valid'] is False
    assert contract['overall_status'] == ev.STATUS_VERIFICATION_FAILED
    assert contract['verified'] is False
    assert _row(contract, 'manifest_signature')['state'] == 'failed'


def test_missing_required_evidence_produces_incomplete():
    manifest, seal, values = _sealed()
    del values['policy_evaluations.json']
    result, record = _verify(manifest, seal, values)

    contract = _contract(_package(verification=record), verification=record)
    assert contract['overall_status'] == ev.STATUS_INCOMPLETE_PACKAGE
    assert contract['verified'] is False
    assert contract['shield']['state'] == ev.SHIELD_INCOMPLETE_PACKAGE


def test_unsigned_package_is_neither_a_pass_nor_a_tampering_finding():
    """An UNSIGNED package: "we could not check it" is never ✓ and never ✗.

    This is the product's existing name for the UNSIGNED state — the signature
    could not be checked at all, which is a different fact from a signature that
    failed to verify, and must never render as either a pass or tampering.
    """
    manifest, _seal, values = _sealed()
    result = ev.verify_evidence_package_document(
        manifest=manifest, seal=None, file_values=values,
        verified_at='2026-03-02T11:00:00Z')
    record = ev.legacy_verification_view(result)
    record['result'] = result

    contract = _contract(_package(verification=record), verification=record)
    assert contract['manifest_signature']['status'] == ev.CHECK_UNAVAILABLE
    assert contract['overall_status'] == ev.STATUS_SIGNATURE_UNAVAILABLE
    assert contract['verified'] is False
    assert contract['shield']['verified'] is False
    assert contract['shield']['state'] == ev.SHIELD_NOT_FULLY_VERIFIED
    row = _row(contract, 'manifest_signature')
    assert row['state'] == ev.CHECK_UNAVAILABLE
    assert row['present'] is False       # not a pass
    assert row['state'] != 'failed'      # and not a failure
    # An uncheckable package is never branded integrity_failed either.
    assert contract['integrity_status'] != ec.INTEGRITY_INTEGRITY_FAILED


# ── 11. A failure removes the green shield ───────────────────────────────────

def test_verification_failure_never_renders_a_green_shield():
    manifest, seal, values = _sealed()
    tampered = copy.deepcopy(values)
    tampered['evidence.json'] = [{'tx_hash': '0xdead'}]
    _, record = _verify(manifest, seal, tampered)

    contract = _contract(_package(verification=record), verification=record,
                         completeness=_full_completeness())
    assert contract['shield']['verified'] is False
    assert contract['shield']['state'] == ev.SHIELD_INTEGRITY_CHECK_FAILED
    assert contract['shield']['label'] == 'Integrity Check Failed'
    # Even at 100% evidence completeness.
    assert contract['completeness']['score'] == 100


# ── 12/13/14. Historical states are preserved, never upgraded ────────────────

def test_superseded_packages_stay_superseded():
    manifest, seal, values = _sealed()
    _, record = _verify(manifest, seal, values)
    contract = _contract(_package(verification=record, superseded=True), verification=record)
    assert contract['overall_status'] == ev.STATUS_SUPERSEDED
    assert contract['verified'] is False
    assert contract['executed'] is False   # a stale run is not this package's state
    assert contract['shield']['state'] == ev.SHIELD_SUPERSEDED


def test_manifest_missing_packages_stay_degraded():
    package = _package(integrity_hash=None, manifest_sha256=None, manifest_file_count=None,
                       files_hashed=0, completeness_score=40, export_status='partial')
    state = ec.get_evidence_package_display_state(package)
    assert state['is_manifest_missing'] is True

    contract = ev.build_verification_contract(package=package, display_state=state,
                                              verification=None, completeness=None)
    assert contract['overall_status'] == ev.STATUS_MANIFEST_MISSING
    assert contract['verified'] is False
    assert contract['shield']['state'] == ev.SHIELD_NOT_VERIFIABLE


def test_legacy_exports_are_not_silently_upgraded_to_verified():
    legacy = {'id': 'legacy-1', 'status': 'completed', 'export_type': 'csv_report',
              'size_bytes': 500}
    state = ec.get_evidence_package_display_state(legacy)
    assert state['is_legacy_export'] is True

    contract = ev.build_verification_contract(package=legacy, display_state=state,
                                              verification=None, completeness=None)
    assert contract['overall_status'] == ev.STATUS_LEGACY_EXPORT
    assert contract['verified'] is False
    assert contract['shield']['verified'] is False
    # Even with a stale verification record attached, a legacy export stays legacy.
    legacy_with_stale = dict(legacy, verification={'valid': True, 'verification_status': 'VERIFIED'})
    stale_state = ec.get_evidence_package_display_state(legacy_with_stale)
    stale_contract = ev.build_verification_contract(
        package=legacy_with_stale, display_state=stale_state,
        verification=legacy_with_stale['verification'], completeness=None)
    assert stale_contract['verified'] is False


def test_a_package_still_building_is_not_verifiable():
    contract = _contract(_package(status='queued'), verification=None)
    assert contract['overall_status'] == ev.STATUS_BUILDING
    assert contract['verified'] is False
    assert contract['shield']['state'] == ev.SHIELD_BUILDING


# ── 10. Verify Integrity refreshes last_verified_at ──────────────────────────

def test_successful_verification_records_verified_at_on_the_contract():
    manifest, seal, values = _sealed()
    stamp = '2026-03-02T12:34:56Z'
    result, record = _verify(manifest, seal, values, verified_at=stamp)
    contract = _contract(_package(verification=record), verification=record)
    assert contract['verified_at'] == stamp
    assert contract['executed'] is True

    # A later run moves the timestamp forward; nothing else is carried over.
    later = '2026-03-05T08:00:00Z'
    _, record2 = _verify(manifest, seal, values, verified_at=later)
    contract2 = _contract(_package(verification=record2), verification=record2)
    assert contract2['verified_at'] == later


def test_never_verified_package_reports_no_verified_at():
    contract = _contract(_package(), verification=None, completeness=_full_completeness())
    assert contract['verified_at'] is None
    assert contract['executed'] is False


# ── Endpoint-level: the table row carries the canonical status ───────────────

class _Row:
    def __init__(self, rows=None):
        self._rows = rows or []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _ListConnection:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, stmt, params=None):
        return _Row(self._rows)

    def commit(self):
        pass


def _fake_request(workspace_id=WS):
    return SimpleNamespace(
        headers={'x-workspace-id': workspace_id},
        query_params=SimpleNamespace(get=lambda key, default=None: default),
    )


def _monkeypatch_list(monkeypatch, rows):
    @contextmanager
    def _fake_pg():
        yield _ListConnection(rows)

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_: {'id': 'user-1'})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda *_: {'workspace_id': WS, 'role': 'admin'})


def _export_row(**filters_over):
    filters = {'incident_id': INCIDENT, 'integrity_hash': 'c' * 64,
               'manifest_sha256': 'c' * 64, 'manifest_file_count': len(REQUIRED),
               'files_hashed': len(REQUIRED), 'completeness_score': 100}
    filters.update(filters_over)
    return {
        'id': PKG, 'workspace_id': WS, 'export_type': 'proof_bundle', 'format': 'json',
        'status': 'completed', 'output_path': f'{WS}/{PKG}.json', 'storage_backend': 's3',
        'storage_object_key': f'evidence/{WS}/{PKG}.json', 'error_message': None,
        'filters': filters, 'size_bytes': 4096, 'package_number': 'EV-TEST-001',
        'created_at': '2026-03-02T09:15:04Z', 'updated_at': '2026-03-02T10:00:00Z',
    }


def test_list_row_carries_the_canonical_contract(monkeypatch):
    manifest, seal, values = _sealed()
    _, record = _verify(manifest, seal, values)
    _monkeypatch_list(monkeypatch, [_export_row(verification=record)])

    result = pilot.list_exports(_fake_request())
    row = result['exports'][0]

    contract = row['verification_contract']
    assert contract['overall_status'] == ev.STATUS_VERIFIED
    assert row['verification_overall_status'] == ev.STATUS_VERIFIED
    # The Integrity column badge and the canonical status are one result.
    assert row['integrity_status'] == contract['integrity_status'] == ec.INTEGRITY_VERIFIED
    assert row['integrity_label'] == 'Verified'


def test_list_row_never_verified_reports_not_verified(monkeypatch):
    _monkeypatch_list(monkeypatch, [_export_row()])
    result = pilot.list_exports(_fake_request())
    row = result['exports'][0]
    assert row['verification_contract']['overall_status'] == ev.STATUS_NOT_VERIFIED
    assert row['verification_contract']['verified'] is False
    assert row['integrity_status'] == ec.INTEGRITY_HASH_GENERATED


def test_list_row_failed_verification_reports_failed(monkeypatch):
    manifest, seal, values = _sealed()
    tampered = copy.deepcopy(values)
    tampered['incidents.json'] = [{'id': INCIDENT, 'status': 'open'}]
    _, record = _verify(manifest, seal, tampered)
    _monkeypatch_list(monkeypatch, [_export_row(verification=record)])

    row = pilot.list_exports(_fake_request())['exports'][0]
    assert row['verification_contract']['overall_status'] == ev.STATUS_VERIFICATION_FAILED
    assert row['integrity_status'] == ec.INTEGRITY_INTEGRITY_FAILED
    assert row['verification_contract']['verified'] is False


# ── Endpoint-level: the DETAIL response serves the canonical checklist ───────
#
# The production contradiction lived in this exact response. The detail endpoint
# read `completeness.checklist` straight out of the stored package summary — a
# snapshot frozen at BUILD time — while its verification counters came from the
# live verification record. These drive the REAL handler with the project's
# fake-DB + fake-storage harness.

DETAIL_WS = 'ws-detail-contract'
DETAIL_USER = 'user-detail-contract'
DETAIL_PKG = 'pkg-detail-contract'
DETAIL_KEY = f'evidence/{DETAIL_WS}/{DETAIL_PKG}.json'


def _detail_files() -> dict:
    return _file_values()


def _detail_completeness() -> dict:
    """The snapshot exactly as package generation persists it: manifest_verified
    is None, so its own checklist row for hashes is 'not verified' forever."""
    return ec.compute_evidence_completeness({
        'incident_status': 'closed', 'evidence_source_type': 'live',
        'has_incident': True, 'has_alert': True, 'has_detection': True,
        'has_telemetry': True, 'has_asset': True, 'has_audit_events': True,
        'has_investigation_timeline': True, 'has_chain_metadata': True,
        'has_manifest': True, 'files_hashed': len(REQUIRED),
        'response_action_count': 1, 'executed_action_count': 1,
        'rejected_action_count': 0, 'requires_approval': True,
        'approval_present': True, 'has_execution_result': True,
        'manifest_verified': None,
    })


def _stored_bundle_bytes(manifest, seal, files, completeness):
    import json
    bundle = {k: v for k, v in files.items()}
    bundle['summary.json'] = {**files['summary.json'], 'completeness': completeness}
    bundle['manifest.json'] = manifest
    bundle['seal.json'] = seal
    return json.dumps({'rows': [bundle]}).encode('utf-8')


class _DetailResult:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row

    def fetchall(self):
        return [] if self._row is None else [self._row]


class _DetailStorage:
    backend_name = 'local'

    def __init__(self, content):
        self._content = content

    def read_bytes(self, *, object_key):
        return self._content

    def get_object_size(self, *, object_key):
        return len(self._content)


class _DetailConn:
    def __init__(self, filters):
        self._filters = filters

    def execute(self, statement, params=None):
        normalized = ' '.join(str(statement).split())
        if 'SELECT * FROM export_jobs WHERE id = %s AND workspace_id = %s' in normalized:
            return _DetailResult({
                'id': DETAIL_PKG, 'workspace_id': DETAIL_WS, 'export_type': 'proof_bundle',
                'format': 'json', 'status': 'completed', 'output_path': 'x',
                'storage_backend': 'local', 'storage_object_key': DETAIL_KEY,
                'error_message': None, 'filters': dict(self._filters), 'size_bytes': 4096,
                'package_number': 'EV-TEST-042',
                'created_at': '2026-03-02T09:15:04Z', 'updated_at': '2026-03-02T10:00:00Z',
            })
        return _DetailResult(None)

    def commit(self):
        pass


class _DetailReq:
    headers = {'x-workspace-id': DETAIL_WS}
    query_params: dict = {}
    client = None


def _bootstrap_detail(monkeypatch, *, verified: bool, tamper: bool = False):
    """Build a real sealed package, optionally verify it, and serve it via the
    real GET /exports/{id} handler."""
    values = _detail_files()
    manifest, _ = build_evidence_manifest(
        export_id=DETAIL_PKG, export_type='proof_bundle', workspace_id=DETAIL_WS,
        generated_at='2026-03-02T09:15:04Z', generated_by_user_id=DETAIL_USER,
        source_resource_type='incident', source_resource_id=INCIDENT,
        storage_backend='local', file_values=values, seal_merkle=True,
        policy_snapshot=POLICY_SNAPSHOT, required_artifacts=REQUIRED,
        file_provenance=_provenance(values),
    )
    seal = resolve_manifest_signer().sign(manifest)
    completeness = _detail_completeness()

    stored_values = copy.deepcopy(values)
    if tamper:
        stored_values['alerts.json'] = [{'id': 'alert-1', 'severity': 'low'}]

    filters = {
        'incident_id': INCIDENT,
        'completeness_score': completeness['score'],
        'files_hashed': len(manifest['files']),
        'integrity_hash': manifest['manifest_sha256'],
        'manifest_sha256': manifest['manifest_sha256'],
    }
    if verified:
        _result, record = _verify(manifest, seal, stored_values)
        filters['verification'] = record

    content = _stored_bundle_bytes(manifest, seal, stored_values, completeness)
    conn = _DetailConn(filters)

    @contextmanager
    def _fake_pg():
        yield conn

    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_a, **_k: {'id': DETAIL_USER})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda *_a, **_k: {'workspace_id': DETAIL_WS, 'role': 'admin'})
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: _DetailStorage(content))
    monkeypatch.setattr(pilot, 'log_audit', lambda *a, **k: None)
    monkeypatch.setattr(pilot, '_workspace_permission_granted', lambda *a, **k: True)
    return completeness


def _detail_checklist(export, code):
    for item in (export.get('verification_contract') or {}).get('checklist') or []:
        if item.get('code') == code:
            return item
    raise AssertionError(f'checklist row {code!r} missing from the detail response')


def test_detail_response_serves_the_canonical_checklist_not_the_frozen_snapshot(monkeypatch):
    """THE production regression, end to end through the real detail handler.

    A verified package must not report "hashes verified" as false anywhere in the
    response — not in the contract, and not in `completeness.checklist` either.
    """
    build_time = _bootstrap_detail(monkeypatch, verified=True)
    # The stored snapshot genuinely still says the hashes are unverified...
    assert _row_in(build_time['checklist'], 'hashes_verified')['state'] == 'not_verified'

    export = pilot.get_export(DETAIL_PKG, _DetailReq())['export']

    contract = export['verification_contract']
    assert contract['overall_status'] == ev.STATUS_VERIFIED
    assert contract['artifact_hashes']['files_verified'] == len(REQUIRED)
    assert contract['artifact_hashes']['hash_failures'] == 0
    assert contract['artifact_hashes']['hashes_verified'] is True
    # ...and the response the browser receives no longer contains it.
    assert _detail_checklist(export, 'hashes_verified')['state'] == 'passed'
    assert _row_in(export['completeness']['checklist'], 'hashes_verified')['state'] == 'passed'
    # The table badge agrees with the contract, from the same handler.
    assert export['integrity_status'] == ec.INTEGRITY_VERIFIED
    assert export['integrity_status'] == contract['integrity_status']


def test_detail_response_every_surface_agrees_for_a_verified_package(monkeypatch):
    """No contradiction anywhere in the payload the four surfaces read."""
    _bootstrap_detail(monkeypatch, verified=True)
    export = pilot.get_export(DETAIL_PKG, _DetailReq())['export']
    contract = export['verification_contract']

    # Clerk counters (contract) vs table/detail badge vs shield vs checklist.
    verified_everywhere = {
        'contract': contract['verified'],
        'shield': contract['shield']['verified'],
        'integrity_badge': export['integrity_status'] == ec.INTEGRITY_VERIFIED,
        'checklist_hashes': _detail_checklist(export, 'hashes_verified')['state'] == 'passed',
        'checklist_merkle': _detail_checklist(export, 'merkle_root')['state'] == 'passed',
        'checklist_signature': _detail_checklist(export, 'manifest_signature')['state'] == 'passed',
    }
    assert all(verified_everywhere.values()), verified_everywhere
    # Completeness is reported as its own axis, never conflated with the above.
    assert contract['completeness']['score'] == export['completeness']['score']


def test_detail_response_never_verified_package_shows_not_verified_everywhere(monkeypatch):
    """100% complete, hashed, manifested — and explicitly NOT verified."""
    _bootstrap_detail(monkeypatch, verified=False)
    export = pilot.get_export(DETAIL_PKG, _DetailReq())['export']
    contract = export['verification_contract']

    assert contract['overall_status'] == ev.STATUS_NOT_VERIFIED
    assert contract['verified'] is False
    assert contract['shield']['state'] == ev.SHIELD_READY_FOR_VERIFICATION
    assert contract['verified_at'] is None
    assert export['integrity_status'] == ec.INTEGRITY_HASH_GENERATED
    # Unrun checks are "not verified" — never rendered as failures.
    for code in ('hashes_verified', 'merkle_root', 'manifest_signature', 'provenance'):
        assert _detail_checklist(export, code)['state'] == 'not_verified'
    # Evidence completeness is untouched by the absence of verification.
    assert export['completeness']['score'] == 100


def test_detail_response_tampered_package_fails_on_every_surface(monkeypatch):
    """One changed artifact: no green anywhere, and the failure names itself."""
    _bootstrap_detail(monkeypatch, verified=True, tamper=True)
    export = pilot.get_export(DETAIL_PKG, _DetailReq())['export']
    contract = export['verification_contract']

    assert contract['overall_status'] == ev.STATUS_VERIFICATION_FAILED
    assert contract['verified'] is False
    assert contract['shield']['state'] == ev.SHIELD_INTEGRITY_CHECK_FAILED
    assert contract['artifact_hashes']['hash_failures'] == 1
    assert contract['artifact_hashes']['files_verified'] == len(REQUIRED) - 1
    assert export['integrity_status'] == ec.INTEGRITY_INTEGRITY_FAILED
    assert _detail_checklist(export, 'hashes_verified')['state'] == 'failed'
    # Merkle is rebuilt over the manifest's own (path, sha256) set, which the
    # manifest still describes, so it is the ARTIFACT check that names the fault.
    assert 'artifact_hashes' in contract['failed_checks']
