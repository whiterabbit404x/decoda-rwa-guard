"""Screen 9 — ONE verification state, and legacy validation is not it (EV-2026-007).

The production contradiction these lock in. For one package, at the same moment:

    Package list       Integrity = Verified
    Detail header      Verified
    Hash Verification  Verified
    Detail body        "Integrity verified · 9/9 files matched · … · seal valid"

    …while the SAME package's own detail reported:

    Files Verified 0 · Integrity failures 0 · Package detail: Not Verified
    Last Verified: Never verified · Merkle Root: not sealed (manifest 1.0)
    Policy Snapshot: not sealed · Ready for Verification
    Checklist: cryptographic checks — not verified

Two independent claims came from two different places:

  1. a PRE-CANONICAL ``verification`` record — a bare ``valid: True`` written
     before the structured verification service existed — which
     ``derive_integrity_status`` read as ``verified``, colouring the table
     badge, the header badge and the Hash Verification field; and
  2. that same record rendered verbatim as a sentence in the present tense.

Neither proves the current cryptographic verification ran. The rules asserted
here:

  * generated ≠ hashed ≠ signed ≠ verified;
  * evidence completeness never implies verification;
  * a legacy hash validation is preserved as HISTORY and reported as
    LEGACY_HASH_VALIDATED — never as CURRENT_CRYPTOGRAPHICALLY_VERIFIED;
  * a manifest that predates the current schema's sealed facts cannot reach
    VERIFIED, and says why;
  * every surface renders ONE contract field, so none can disagree.

Nothing here hardcodes a package number or an artifact count.
"""
from __future__ import annotations

import datetime as _dt
from contextlib import contextmanager

from services.api.app import evidence_completeness as ec
from services.api.app import evidence_verification as ev
from services.api.app import pilot
from services.api.app.evidence_manifest_signer import resolve_manifest_signer
from services.api.app.evidence_signing import build_evidence_manifest

WS = 'ws-legacy-screen9'
PKG = 'pkg-legacy-screen9'
INCIDENT = 'inc-legacy-screen9'

# Nine artifacts — the production shape — but every assertion is written against
# the package's OWN counts.
_FILES = {
    'summary.json': {'export_id': PKG, 'incident_id': INCIDENT},
    'incidents.json': [{'id': INCIDENT, 'status': 'closed'}],
    'alerts.json': [{'id': 'alert-1', 'severity': 'critical'}],
    'detections.json': [{'id': 'det-1'}],
    'response_actions.json': [{'id': 'act-1', 'status': 'executed'}],
    'audit_log.json': [{'id': 'aud-1', 'action': 'incident.opened'}],
    'evidence.json': [{'tx_hash': '0x4c1a'}],
    'detection_metrics.json': [{'id': 'dm-1'}],
    'investigation_timeline.json': {'present': True},
}


def _legacy_manifest():
    """A schema 1.0 manifest: no sealed Merkle root, policy snapshot or
    required-artifact list — the EV-2026-007 shape."""
    manifest, _ = build_evidence_manifest(
        export_id=PKG, export_type='proof_bundle', workspace_id=WS,
        generated_at='2026-08-01T00:00:00Z', generated_by_user_id='user-1',
        source_resource_type='incident', source_resource_id=INCIDENT,
        storage_backend='local', file_values=_FILES,
    )
    return manifest, resolve_manifest_signer().sign(manifest), dict(_FILES)


def _current_manifest():
    """A manifest sealed in the CURRENT schema, as package generation writes one."""
    manifest, _ = build_evidence_manifest(
        export_id=PKG, export_type='proof_bundle', workspace_id=WS,
        generated_at='2026-08-01T00:00:00Z', generated_by_user_id='user-1',
        source_resource_type='incident', source_resource_id=INCIDENT,
        storage_backend='local', file_values=_FILES,
        seal_merkle=True,
        policy_snapshot={
            'present': True, 'policy_key': 'POL-LEGACY-1', 'policy_version': 4,
            'decision': 'DENY', 'decision_kind': 'enforcement',
            'evaluation_id': 'eval-l1', 'evaluated_at': '2026-08-01T00:00:00Z',
            'source': 'governance_policy_versions',
        },
        required_artifacts=sorted(_FILES),
        file_provenance={
            path: {'media_type': 'application/json', 'domain': 'OPERATIONAL',
                   'source_record_type': 'evidence'}
            for path in _FILES
        },
    )
    return manifest, resolve_manifest_signer().sign(manifest), dict(_FILES)


#: The record production carried: an older hash-and-seal check, no structured
#: result, no canonical status. It is what made four surfaces say "Verified".
_LEGACY_RECORD = {
    'valid': True,
    'verified_at': '2026-08-17T14:02:11Z',
    'files_total': len(_FILES),
    'files_verified': len(_FILES),
    'files_failed': [],
    'missing_files': [],
    'manifest_ok': True,
    'seal_status': 'valid',
}


def _package(**over) -> dict:
    base = {
        'id': PKG,
        'status': 'completed',
        'export_type': 'proof_bundle',
        'incident_id': INCIDENT,
        'size_bytes': 4096,
        'integrity_hash': 'e' * 64,
        'manifest_sha256': 'e' * 64,
        'manifest_file_count': len(_FILES),
        'files_hashed': len(_FILES),
        'completeness_score': 100,
        'manifest_schema_version': '1.0',
    }
    base.update(over)
    return base


def _complete_completeness() -> dict:
    """A genuinely 100%-complete evidence snapshot. No verification involved."""
    return ec.compute_evidence_completeness({
        'incident_status': 'closed', 'evidence_source_type': 'live',
        'has_incident': True, 'has_alert': True, 'has_detection': True,
        'has_telemetry': True, 'has_asset': True, 'has_audit_events': True,
        'has_investigation_timeline': True, 'has_chain_metadata': True,
        'has_manifest': True, 'files_hashed': len(_FILES),
        'response_action_count': 1, 'executed_action_count': 1,
        'rejected_action_count': 0, 'requires_approval': True,
        'approval_present': True, 'has_execution_result': True,
        'manifest_verified': None,
    })


def _contract(package: dict, verification=None, completeness=None, **kw) -> dict:
    state = ec.get_evidence_package_display_state(package)
    return ev.build_verification_contract(
        package=package, display_state=state,
        verification=verification, completeness=completeness, **kw,
    )


def _row(contract: dict, code: str) -> dict:
    for item in contract['checklist']:
        if item['code'] == code:
            return item
    raise AssertionError(f'checklist row {code!r} missing')


# ── 1. Generation does not imply verification ────────────────────────────────

def test_package_generation_does_not_imply_verification():
    """A cleanly GENERATED package with a manifest is Not Verified until a
    verification actually runs. Generated ≠ hashed ≠ signed ≠ verified."""
    package = _package()
    state = ec.get_evidence_package_display_state(package)
    contract = _contract(package, completeness=_complete_completeness())

    assert state['generation_status'] == ec.GENERATION_STATUS_GENERATED
    assert state['manifest_status'] == ec.MANIFEST_STATUS_PRESENT
    assert contract['overall_status'] == ev.STATUS_NOT_VERIFIED
    assert contract['verified'] is False
    assert contract['badge']['verified'] is False
    assert contract['shield']['verified'] is False


# ── 2. Completeness does not imply verification ──────────────────────────────

def test_evidence_completeness_does_not_imply_verification():
    """100% complete is a statement about what the package CONTAINS. It can
    never colour a verification badge."""
    completeness = _complete_completeness()
    assert completeness['score'] == 100

    contract = _contract(_package(), completeness=completeness)
    assert contract['completeness']['score'] == 100
    assert contract['completeness']['complete'] is True
    assert contract['overall_status'] == ev.STATUS_NOT_VERIFIED
    assert contract['badge']['verified'] is False
    # Complete evidence makes it READY for verification — not verified. (The
    # shield reads the manifest fact, not this score: completeness is never an
    # input to a verification state.)
    assert contract['shield']['state'] == ev.SHIELD_READY_FOR_VERIFICATION


# ── 3. Stored hashes do not imply hash verification ──────────────────────────

def test_sha256_hashes_present_does_not_imply_hash_verification():
    """Files Hashed counts stored digests. Files Verified counts digests
    RECOMPUTED from stored bytes and matched. Only the second reads Verified."""
    contract = _contract(_package(), completeness=_complete_completeness())
    hashes = contract['artifact_hashes']

    assert hashes['files_hashed'] == len(_FILES)
    assert hashes['files_verified'] == 0
    assert hashes['hashes_verified'] is False
    assert _row(contract, 'hashes_generated')['state'] == ev.CHECK_PASSED
    assert _row(contract, 'hashes_verified')['state'] == ev.CHECK_NOT_VERIFIED
    assert contract['hash_verification']['verified'] is False
    assert contract['hash_verification']['label'] == 'Hashes Available — Not Verified'


# ── 4. A signature existing is not a signature verified ──────────────────────

def test_manifest_signature_present_does_not_imply_signature_verified():
    """A sealed manifest.sig proves the package was SIGNED. Whether that
    signature verifies is a separate fact, and it is unknown until it is
    checked."""
    manifest, seal, _ = _legacy_manifest()
    assert str(seal.get('signature') or ''), 'fixture must actually be signed'

    contract = _contract(
        _package(signing={'signed': True, 'algorithm': 'HMAC-SHA256'}),
        completeness=_complete_completeness(),
    )
    assert contract['manifest_signature']['status'] == ev.CHECK_NOT_VERIFIED
    assert contract['manifest_signature']['valid'] is False
    assert _row(contract, 'manifest_signature')['state'] == ev.CHECK_NOT_VERIFIED


# ── 5. A legacy hash validation is not a current verification ────────────────

def test_legacy_hash_success_does_not_produce_current_verified():
    """THE EV-2026-007 bug. A pre-canonical record that passed an older hash
    check must not turn any surface green."""
    package = _package(verification=_LEGACY_RECORD)
    state = ec.get_evidence_package_display_state(package)
    contract = _contract(package, verification=_LEGACY_RECORD,
                         completeness=_complete_completeness())

    assert state['integrity_status'] == ec.INTEGRITY_LEGACY_HASH_VALIDATED
    assert state['integrity_status'] != ec.INTEGRITY_VERIFIED
    assert contract['overall_status'] == ev.STATUS_NOT_VERIFIED
    assert contract['verified'] is False
    assert contract['executed'] is False
    assert contract['badge']['verified'] is False
    assert contract['badge']['label'] != 'Verified'
    assert contract['shield']['verified'] is False


def test_legacy_record_is_preserved_as_a_labelled_historical_record():
    """Historical data is preserved, not deleted — and never re-dated as
    current. LEGACY_HASH_VALIDATED stays distinct from
    CURRENT_CRYPTOGRAPHICALLY_VERIFIED."""
    contract = _contract(_package(verification=_LEGACY_RECORD),
                         verification=_LEGACY_RECORD,
                         completeness=_complete_completeness())
    legacy = contract['legacy_validation']

    assert legacy['present'] is True
    assert legacy['outcome'] == ev.LEGACY_VALIDATION_PASSED
    assert legacy['files_verified'] == len(_FILES)
    assert legacy['files_total'] == len(_FILES)
    assert legacy['validated_at'] == _LEGACY_RECORD['verified_at']
    assert legacy['manifest_schema_version'] == '1.0'
    assert legacy['detail']
    # It is history — it never becomes the CURRENT result.
    assert contract['verified_at'] is None
    assert contract['artifact_hashes']['files_verified'] == 0
    # And it never carries the seal claim forward.
    assert 'seal_status' not in legacy


def test_a_current_verification_supersedes_the_legacy_record():
    """Once a real run has happened, the legacy record stops being reported —
    the current result is the answer."""
    manifest, seal, values = _current_manifest()
    result = ev.verify_evidence_package_document(
        manifest=manifest, seal=seal, file_values=values,
        verified_at='2026-09-01T09:00:00Z', verified_by_user_id='user-1')
    record = ev.legacy_verification_view(result)
    record['result'] = result

    contract = _contract(_package(verification=record), verification=record,
                         completeness=_complete_completeness())
    assert result['status'] == ev.STATUS_VERIFIED
    assert contract['overall_status'] == ev.STATUS_VERIFIED
    assert contract['legacy_validation'] is None


def test_a_recorded_legacy_failure_is_never_softened_into_not_verified():
    """Fail closed. An older check that found a real mismatch keeps blocking the
    package until a current verification supersedes it."""
    failed = {**_LEGACY_RECORD, 'valid': False, 'files_verified': len(_FILES) - 1,
              'files_failed': ['alerts.json']}
    package = _package(verification=failed)
    state = ec.get_evidence_package_display_state(package)
    contract = _contract(package, verification=failed, completeness=_complete_completeness())

    assert state['integrity_status'] == ec.INTEGRITY_INTEGRITY_FAILED
    assert contract['overall_status'] == ev.STATUS_VERIFICATION_FAILED
    assert contract['shield']['state'] == ev.SHIELD_INTEGRITY_CHECK_FAILED
    assert contract['badge']['variant'] == 'danger'
    assert contract['legacy_validation']['outcome'] == ev.LEGACY_VALIDATION_FAILED


def test_a_verifiable_package_is_never_shielded_not_verifiable():
    """A retrievable manifest means Verify Integrity is offered. The shield must
    not read "Not Verifiable" ("no retrievable signed manifest") beside it —
    evidence completeness never decides a verification state."""
    incomplete = ec.compute_evidence_completeness({
        'incident_status': 'open', 'evidence_source_type': 'live',
        'has_incident': True, 'has_alert': False, 'has_detection': False,
        'has_telemetry': False, 'has_asset': False, 'has_audit_events': False,
        'has_investigation_timeline': False, 'has_chain_metadata': False,
        'has_manifest': True, 'files_hashed': len(_FILES),
        'response_action_count': 0, 'executed_action_count': 0,
        'rejected_action_count': 0, 'requires_approval': False,
        'approval_present': False, 'has_execution_result': False,
        'manifest_verified': None,
    })
    package = _package()
    contract = _contract(package, completeness=incomplete)

    assert ec.get_package_allowed_actions(package, can_export=True)['verify'] is True
    assert contract['completeness']['complete'] is False
    assert contract['shield']['state'] == ev.SHIELD_READY_FOR_VERIFICATION
    assert contract['shield']['verified'] is False


# ── 6 & 7. One status for every surface ──────────────────────────────────────

def test_table_row_and_detail_report_the_same_status():
    """The package list and the package detail project the SAME contract, so the
    table badge cannot say Verified while the detail says Not Verified."""
    row_contract = _contract(_package(verification=_LEGACY_RECORD),
                             verification=_LEGACY_RECORD, include_checks=False)
    detail_contract = _contract(_package(verification=_LEGACY_RECORD),
                                verification=_LEGACY_RECORD,
                                completeness=_complete_completeness())

    assert row_contract['overall_status'] == detail_contract['overall_status']
    assert row_contract['badge'] == detail_contract['badge']
    assert row_contract['verified'] == detail_contract['verified'] is False


def test_header_badge_and_package_verification_share_one_source():
    """The detail overlay's header badge and the Package Verification panel are
    ONE field. There is no second place for a header state to come from."""
    contract = _contract(_package(verification=_LEGACY_RECORD),
                         verification=_LEGACY_RECORD,
                         completeness=_complete_completeness())
    # The badge the header renders is derived from the same overall_status the
    # panel and the shield render — never from a separate lifecycle field.
    assert contract['badge']['status'] == contract['overall_status']
    assert contract['badge']['verified'] == contract['verified'] == contract['shield']['verified']


# ── 8, 9, 10. The impossible combinations ────────────────────────────────────

def test_files_verified_zero_can_never_show_hash_verification_verified():
    """Invariant over the contract, not one example: Hash Verification reads
    Verified only when the artifact counters say every verifiable artifact was
    recomputed and matched."""
    for verification in (None, _LEGACY_RECORD, {**_LEGACY_RECORD, 'valid': False}):
        contract = _contract(_package(verification=verification),
                             verification=verification,
                             completeness=_complete_completeness())
        hashes = contract['artifact_hashes']
        if hashes['files_verified'] == 0:
            assert contract['hash_verification']['verified'] is False
            assert contract['hash_verification']['label'] != 'Verified'
            assert _row(contract, 'hashes_verified')['state'] != ev.CHECK_PASSED


def test_never_verified_can_never_show_a_current_verified_timestamp():
    """No Last Verified value, no "Integrity verified at …" anywhere."""
    contract = _contract(_package(verification=_LEGACY_RECORD),
                         verification=_LEGACY_RECORD,
                         completeness=_complete_completeness())
    assert contract['verified_at'] is None
    assert contract['verified_by_user_id'] is None
    # The legacy record's own timestamp is reported only inside its historical
    # block, and is labelled as a validation, not a verification.
    assert contract['legacy_validation']['validated_at'] == _LEGACY_RECORD['verified_at']


def test_package_without_merkle_root_never_reports_a_valid_seal():
    """"seal valid" claimed a Merkle commitment this package never sealed."""
    manifest, _, _ = _legacy_manifest()
    assert 'merkle_root' not in manifest

    contract = _contract(_package(verification=_LEGACY_RECORD),
                         verification=_LEGACY_RECORD,
                         completeness=_complete_completeness())
    assert contract['merkle_root']['status'] == ev.CHECK_NOT_VERIFIED
    assert contract['merkle_root']['valid'] is False
    assert contract['legacy_validation'].get('seal_status') is None


# ── 11. A legacy schema cannot silently become VERIFIED ──────────────────────

def test_schema_1_0_package_cannot_reach_current_verified():
    """Even a fully successful run on a schema 1.0 manifest stops at
    PARTIALLY_VERIFIED — the sealed facts the current schema requires do not
    exist to be recomputed."""
    manifest, seal, values = _legacy_manifest()
    result = ev.verify_evidence_package_document(
        manifest=manifest, seal=seal, file_values=values,
        verified_at='2026-09-01T09:00:00Z', verified_by_user_id='user-1')

    assert result['status'] == ev.STATUS_PARTIALLY_VERIFIED
    assert result['verified'] is False
    assert result['failed_checks'] == []
    assert result['legacy_schema']['legacy'] is True
    assert result['legacy_schema']['schema_version'] == '1.0'
    assert set(result['legacy_schema']['missing_sealed_facts']) == {
        'merkle_root', 'policy_snapshot', 'required_artifacts'}
    assert result['legacy_schema']['reason']


def test_legacy_verification_reports_each_category_truthfully():
    """The legacy verdict withholds a claim; it never invents a failure. The
    checks the manifest CAN support really ran and really passed."""
    manifest, seal, values = _legacy_manifest()
    result = ev.verify_evidence_package_document(
        manifest=manifest, seal=seal, file_values=values,
        verified_at='2026-09-01T09:00:00Z', verified_by_user_id='user-1')
    by_key = {check['check']: check for check in result['checks']}

    assert by_key[ev.CHECK_ARTIFACT_HASHES]['status'] == ev.CHECK_PASSED
    assert by_key[ev.CHECK_ARTIFACT_HASHES]['valid'] == len(values)
    assert by_key[ev.CHECK_MANIFEST_HASH]['status'] == ev.CHECK_PASSED
    assert by_key[ev.CHECK_MANIFEST_SIGNATURE]['status'] == ev.CHECK_PASSED
    for key in (ev.CHECK_MERKLE_ROOT, ev.CHECK_POLICY_SNAPSHOT, ev.CHECK_REQUIRED_EVIDENCE):
        assert by_key[key]['status'] == ev.CHECK_NOT_APPLICABLE, key
        assert by_key[key]['mandatory'] is False, key
        assert by_key[key]['detail'], key


def test_legacy_run_lands_on_the_legacy_state_on_every_surface():
    """The status recomputed at READ time matches the one the verify endpoint
    persists, and the badge names the legacy outcome rather than implying more."""
    manifest, seal, values = _legacy_manifest()
    result = ev.verify_evidence_package_document(
        manifest=manifest, seal=seal, file_values=values,
        verified_at='2026-09-01T09:00:00Z', verified_by_user_id='user-1')
    record = ev.legacy_verification_view(result)
    record['result'] = result

    package = _package(verification=record)
    state = ec.get_evidence_package_display_state(package)
    contract = _contract(package, verification=record, completeness=_complete_completeness())

    assert state['integrity_status'] == ec.INTEGRITY_LEGACY_HASH_VALIDATED
    assert state['is_export_ready'] is False
    assert contract['overall_status'] == ev.STATUS_PARTIALLY_VERIFIED
    assert contract['badge']['verified'] is False
    assert 'Legacy' in contract['badge']['label']
    assert contract['legacy_schema']['legacy'] is True
    # The hashes really were recomputed on this run, and that IS reported.
    assert contract['hash_verification']['verified'] is True
    assert contract['artifact_hashes']['files_verified'] == len(values)


def test_current_schema_package_still_reaches_verified():
    """The gate is about missing sealed facts, not about age: a package sealed in
    the current schema verifies to VERIFIED exactly as before."""
    manifest, seal, values = _current_manifest()
    result = ev.verify_evidence_package_document(
        manifest=manifest, seal=seal, file_values=values,
        verified_at='2026-09-01T09:00:00Z', verified_by_user_id='user-1')
    record = ev.legacy_verification_view(result)
    record['result'] = result

    package = _package(manifest_schema_version='2.0', verification=record)
    contract = _contract(package, verification=record, completeness=_complete_completeness())
    assert result['status'] == ev.STATUS_VERIFIED
    assert result['legacy_schema']['legacy'] is False
    assert ec.get_evidence_package_display_state(package)['integrity_status'] == ec.INTEGRITY_VERIFIED
    assert contract['badge'] == {'label': 'Verified', 'variant': 'success',
                                 'status': ev.STATUS_VERIFIED, 'verified': True}
    assert contract['shield']['state'] == ev.SHIELD_VERIFIED


# ── 12. A real verification updates every consumer together ──────────────────

def test_running_verification_records_verified_at_and_moves_every_consumer():
    """One run, one recorded result, and every surface moves with it."""
    manifest, seal, values = _current_manifest()
    verified_at = '2026-09-02T11:30:00Z'
    result = ev.verify_evidence_package_document(
        manifest=manifest, seal=seal, file_values=values,
        verified_at=verified_at, verified_by_user_id='user-1')
    record = ev.legacy_verification_view(result)
    record['result'] = result

    package = _package(manifest_schema_version='2.0', verification=record)
    state = ec.get_evidence_package_display_state(package)
    contract = _contract(package, verification=record, completeness=_complete_completeness())

    # Recorded.
    assert contract['verified_at'] == verified_at
    assert contract['verified_by_user_id'] == 'user-1'
    # Every consumer of the ONE contract agrees.
    assert contract['overall_status'] == ev.STATUS_VERIFIED          # detail status
    assert contract['badge']['verified'] is True                     # table + header badge
    assert contract['shield']['state'] == ev.SHIELD_VERIFIED         # shield
    assert contract['hash_verification']['verified'] is True         # Hash Verification field
    assert contract['artifact_hashes']['files_verified'] == len(values)  # Clerk counters
    assert _row(contract, 'hashes_verified')['state'] == ev.CHECK_PASSED  # checklist
    assert state['verification_status'] == ec.VERIFICATION_STATUS_VERIFIED
    assert state['is_export_ready'] is True
    # And the workspace metrics count it exactly once.
    assert ec.get_workspace_evidence_metrics([package])['verified'] == 1


# ── The list endpoint, end to end ────────────────────────────────────────────

class _Result:
    def __init__(self, rows=None):
        self._rows = rows or []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _ListConn:
    def __init__(self, filters: dict):
        self._filters = filters

    def execute(self, statement, params=None):
        norm = ' '.join(str(statement).split())
        if 'FROM export_jobs' in norm and 'ORDER BY created_at DESC' in norm:
            return _Result([{
                'id': PKG, 'workspace_id': WS, 'export_type': 'proof_bundle',
                'format': 'json', 'status': 'completed', 'output_path': 'x',
                'storage_backend': 'local', 'storage_object_key': f'{WS}/{PKG}.json',
                'error_message': None, 'filters': dict(self._filters), 'size_bytes': 4096,
                'package_number': 'EV-2026-007',
                'created_at': _dt.datetime(2026, 8, 1), 'updated_at': _dt.datetime(2026, 8, 17),
            }])
        return _Result()

    def commit(self):
        pass


class _Req:
    headers = {'x-workspace-id': WS}
    query_params: dict = {}
    client = None


def test_list_endpoint_never_badges_a_legacy_validated_package_verified(monkeypatch):
    """End to end: the row the package table renders. The Integrity badge it
    projects, and the workspace 'Verified' metric, both refuse the legacy
    record."""
    conn = _ListConn({
        'incident_id': INCIDENT, 'completeness_score': 100,
        'files_hashed': len(_FILES), 'integrity_hash': 'e' * 64,
        'verification': _LEGACY_RECORD,
    })

    @contextmanager
    def _fake_pg():
        yield conn

    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_a, **_k: {'id': 'user-1'})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda *_a, **_k: {'workspace_id': WS, 'role': 'analyst'})
    monkeypatch.setattr(pilot, '_workspace_permission_granted', lambda *a, **k: True)

    payload = pilot.list_exports(_Req())
    row = payload['exports'][0]

    assert row['integrity_status'] == ec.INTEGRITY_LEGACY_HASH_VALIDATED
    assert row['verification_contract']['badge']['verified'] is False
    assert row['verification_contract']['badge']['label'] != 'Verified'
    assert row['verification_overall_status'] == ev.STATUS_NOT_VERIFIED
    assert row['export_ready'] is False
    assert row['ready_for_verification'] is True
    assert payload['metrics']['verified'] == 0


# ── The detail endpoint, end to end ──────────────────────────────────────────

class _DetailStorage:
    backend_name = 'local'

    def __init__(self, content: bytes):
        self._content = content

    def read_bytes(self, *, object_key: str) -> bytes:
        return self._content

    def get_object_size(self, *, object_key: str):
        return len(self._content)


class _DetailConn:
    def __init__(self, filters: dict):
        self._filters = filters

    def execute(self, statement, params=None):
        norm = ' '.join(str(statement).split())
        if 'SELECT * FROM export_jobs WHERE id = %s AND workspace_id = %s' in norm:
            return _Result([{
                'id': PKG, 'workspace_id': WS, 'export_type': 'proof_bundle',
                'format': 'json', 'status': 'completed', 'output_path': 'x',
                'storage_backend': 'local', 'storage_object_key': f'{WS}/{PKG}.json',
                'error_message': None, 'filters': dict(self._filters), 'size_bytes': 4096,
                'package_number': 'EV-2026-007',
                'created_at': '2026-08-01T00:00:00Z', 'updated_at': '2026-08-17T00:00:00Z',
            }])
        return _Result()

    def commit(self):
        pass


def _detail_for(monkeypatch, verification) -> dict:
    """GET /exports/{id} for a stored schema-1.0 package with the given record."""
    import json as _json

    manifest, seal, values = _legacy_manifest()
    bundle = {**values, 'manifest.json': manifest, 'seal.json': seal}
    content = _json.dumps({'rows': [bundle]}).encode('utf-8')
    conn = _DetailConn({
        'incident_id': INCIDENT, 'completeness_score': 100,
        'files_hashed': len(values), 'integrity_hash': manifest['manifest_sha256'],
        'manifest_sha256': manifest['manifest_sha256'],
        'manifest_file_count': len(manifest['files']),
        'verification': verification,
    })

    @contextmanager
    def _fake_pg():
        yield conn

    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_a, **_k: {'id': 'user-1'})
    monkeypatch.setattr(pilot, 'resolve_workspace', lambda *_a, **_k: {'workspace_id': WS, 'role': 'analyst'})
    monkeypatch.setattr(pilot, 'load_export_storage', lambda: _DetailStorage(content))
    monkeypatch.setattr(pilot, 'log_audit', lambda *a, **k: None)
    monkeypatch.setattr(pilot, '_workspace_permission_granted', lambda *a, **k: True)
    return pilot.get_export(PKG, _Req())['export']


def test_detail_endpoint_never_marks_files_verified_from_a_legacy_record(monkeypatch):
    """Every Included File row said "verified" because a bare ``valid: True``
    was read as a per-file result. A file reads verified only when the CURRENT
    run recomputed its stored bytes."""
    export = _detail_for(monkeypatch, _LEGACY_RECORD)

    assert export['files'], 'fixture must list the manifest files'
    assert all(f['verification_status'] == 'hash_generated' for f in export['files'])
    assert not any(f['verification_status'] == 'verified' for f in export['files'])


def test_detail_endpoint_is_internally_consistent_for_a_legacy_package(monkeypatch):
    """Every field the four contradictory surfaces read, in one response."""
    export = _detail_for(monkeypatch, _LEGACY_RECORD)
    contract = export['verification_contract']

    # The lifecycle axes.
    assert export['generation_status'] == ec.GENERATION_STATUS_GENERATED
    assert export['manifest_status'] == ec.MANIFEST_STATUS_PRESENT
    assert export['integrity_status'] == ec.INTEGRITY_LEGACY_HASH_VALIDATED
    assert export['verification_status'] == ec.VERIFICATION_STATUS_READY
    # The one canonical verification result, and everything projected from it.
    assert contract['overall_status'] == ev.STATUS_NOT_VERIFIED
    assert contract['badge']['verified'] is False
    assert contract['shield']['state'] == ev.SHIELD_READY_FOR_VERIFICATION
    assert contract['hash_verification']['verified'] is False
    assert contract['artifact_hashes']['files_hashed'] == len(_FILES)
    assert contract['artifact_hashes']['files_verified'] == 0
    assert contract['verified_at'] is None
    # The legacy record survives, in its historical block only.
    assert contract['legacy_validation']['outcome'] == ev.LEGACY_VALIDATION_PASSED
    # Verify Integrity is offered — this package is ready for a real run.
    assert export['allowed_actions']['verify'] is True
    # And no cryptographic checklist row claims to have passed.
    crypto_rows = [r for r in contract['checklist'] if r['source'] == 'verification']
    assert crypto_rows and all(r['state'] != ev.CHECK_PASSED for r in crypto_rows)


def test_detail_completeness_checklist_never_claims_hashes_were_verified(monkeypatch):
    """The frozen build-time snapshot is rebuilt from the contract, so the
    "File hashes verified" row cannot inherit a legacy record's success."""
    export = _detail_for(monkeypatch, _LEGACY_RECORD)
    rows = {row['code']: row for row in (export['completeness'] or {}).get('checklist', [])}

    assert rows['hashes_generated']['state'] == ev.CHECK_PASSED
    assert rows['hashes_verified']['state'] == ev.CHECK_NOT_VERIFIED
    assert rows['hashes_verified']['present'] is False
