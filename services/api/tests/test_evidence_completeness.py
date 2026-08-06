"""Unit tests for the deterministic evidence-completeness engine and the
SHA-256 verification helper that power Screen 9 (Evidence & Audit).

These are pure-function tests: no database, no network, no LLM. They lock in the
truthfulness guarantees — identical facts produce identical scores, hashing is
deterministic, tampering is detected, and "verified" is never asserted unless a
verification actually passed.
"""
from __future__ import annotations

import copy

from services.api.app.evidence_completeness import (
    INTEGRITY_BUILDING,
    INTEGRITY_FAILED,
    INTEGRITY_HASH_GENERATED,
    INTEGRITY_INTEGRITY_FAILED,
    INTEGRITY_NEEDS_EVIDENCE,
    INTEGRITY_SUPERSEDED,
    INTEGRITY_VERIFIED,
    compute_evidence_completeness,
    derive_integrity_status,
    verify_files_and_manifest,
)
from services.api.app.evidence_signing import build_evidence_manifest


def _full_facts(**overrides):
    facts = {
        'incident_status': 'contained',
        'evidence_source_type': 'live',
        'has_incident': True,
        'has_alert': True,
        'has_detection': True,
        'has_telemetry': True,
        'has_asset': True,
        'has_audit_events': True,
        'has_investigation_timeline': True,
        'has_chain_metadata': True,
        'has_manifest': True,
        'files_hashed': 8,
        'response_action_count': 1,
        'executed_action_count': 1,
        'rejected_action_count': 0,
        'requires_approval': True,
        'approval_present': True,
        'has_execution_result': True,
        'manifest_verified': True,
    }
    facts.update(overrides)
    return facts


# ── Completeness scoring ──────────────────────────────────────────────────────

def test_full_package_scores_100_excellent():
    result = compute_evidence_completeness(_full_facts())
    assert result['score'] == 100
    assert result['status'] == 'excellent'
    assert result['status_label'] == 'Excellent'
    assert result['missing_count'] == 0
    assert result['missing_codes'] == []


def test_scoring_is_deterministic_for_identical_facts():
    facts = _full_facts(evidence_source_type='live', incident_status='open',
                        executed_action_count=0, response_action_count=0, requires_approval=False)
    a = compute_evidence_completeness(facts)
    b = compute_evidence_completeness(copy.deepcopy(facts))
    assert a == b


def test_missing_core_evidence_lowers_score_and_lists_codes():
    result = compute_evidence_completeness(_full_facts(
        has_alert=False, has_detection=False, has_telemetry=False,
    ))
    assert result['score'] < 100
    assert 'original_alert' in result['missing_codes']
    assert 'detection_provenance' in result['missing_codes']
    assert 'telemetry_reference' in result['missing_codes']


def test_status_bands_match_spec_thresholds():
    # 95-100 excellent, 80-94 good, 60-79 incomplete, <60 critical
    assert compute_evidence_completeness(_full_facts())['status'] == 'excellent'
    # Drop asset + timeline (2 of 14 required) → ~86% good
    good = compute_evidence_completeness(_full_facts(has_asset=False, has_investigation_timeline=False))
    assert good['status'] == 'good'
    # Drop more → incomplete/critical band
    sparse = compute_evidence_completeness(_full_facts(
        has_alert=False, has_detection=False, has_telemetry=False, has_asset=False,
        has_audit_events=False, has_investigation_timeline=False, has_chain_metadata=False,
        evidence_source_type='missing',
    ))
    assert sparse['status'] in {'incomplete', 'critical'}


def test_requirements_adapt_to_incident_state():
    # An OPEN incident with no response action must NOT require execution,
    # approval, rejection, or closure evidence.
    open_facts = _full_facts(
        incident_status='open', response_action_count=0, executed_action_count=0,
        rejected_action_count=0, requires_approval=False, approval_present=False,
        has_execution_result=False,
    )
    result = compute_evidence_completeness(open_facts)
    codes = {c['code']: c for c in result['categories']}
    assert codes['execution_result']['status'] == 'not_applicable'
    assert codes['closure_state']['status'] == 'not_applicable'
    assert codes['rejection_evidence']['status'] == 'not_applicable'
    # It can still be complete without them.
    assert result['status'] == 'excellent'


def test_rejected_response_requires_rejection_not_execution():
    facts = _full_facts(
        incident_status='open', response_action_count=1, executed_action_count=0,
        rejected_action_count=1, requires_approval=True, approval_present=False,
        has_execution_result=False,
    )
    result = compute_evidence_completeness(facts)
    codes = {c['code']: c for c in result['categories']}
    assert codes['rejection_evidence']['status'] == 'present'
    assert codes['execution_result']['status'] == 'not_applicable'
    # A rejection counts as an approval decision.
    assert codes['approval_decision']['status'] == 'present'


def test_checklist_hashes_verified_only_true_after_verification():
    generated = compute_evidence_completeness(_full_facts(manifest_verified=None))
    verified = compute_evidence_completeness(_full_facts(manifest_verified=True))
    gen_map = {c['code']: c['present'] for c in generated['checklist']}
    ver_map = {c['code']: c['present'] for c in verified['checklist']}
    # Hashes are generated in both, but only "verified" once verification passed.
    assert gen_map['hashes_generated'] is True
    assert gen_map['hashes_verified'] is False
    assert ver_map['hashes_verified'] is True


# ── Integrity status derivation ───────────────────────────────────────────────

def test_integrity_status_lifecycle_states():
    full = compute_evidence_completeness(_full_facts())
    sparse = compute_evidence_completeness(_full_facts(
        has_alert=False, has_detection=False, has_telemetry=False, has_asset=False,
        has_audit_events=False, has_investigation_timeline=False, evidence_source_type='missing',
    ))
    assert derive_integrity_status(job_status='queued', verification=None, completeness=None) == INTEGRITY_BUILDING
    assert derive_integrity_status(job_status='failed', verification=None, completeness=None) == INTEGRITY_FAILED
    assert derive_integrity_status(job_status='completed', verification={'valid': True}, completeness=None) == INTEGRITY_VERIFIED
    assert derive_integrity_status(job_status='completed', verification={'valid': False}, completeness=None) == INTEGRITY_INTEGRITY_FAILED
    assert derive_integrity_status(job_status='completed', verification=None, completeness=full) == INTEGRITY_HASH_GENERATED
    assert derive_integrity_status(job_status='completed', verification=None, completeness=sparse) == INTEGRITY_NEEDS_EVIDENCE
    assert derive_integrity_status(job_status='completed', verification={'valid': True}, completeness=None, superseded=True) == INTEGRITY_SUPERSEDED


def test_verified_requires_actual_passing_verification():
    # Without a recorded verification, a completed package is never "verified".
    full = compute_evidence_completeness(_full_facts())
    assert derive_integrity_status(job_status='completed', verification=None, completeness=full) != INTEGRITY_VERIFIED


# ── SHA-256 verification helper ───────────────────────────────────────────────

def _signed(files):
    manifest, _ = build_evidence_manifest(
        export_id='e1', export_type='proof_bundle', workspace_id='ws',
        generated_at='2026-01-01T00:00:00Z', generated_by_user_id='u1',
        source_resource_type='incident', source_resource_id='inc1',
        storage_backend='local', file_values=files,
    )
    return manifest


def test_identical_bytes_verify_successfully():
    files = {'summary.json': {'b': 2, 'a': 1}, 'alerts.json': [{'id': 'a1'}]}
    manifest = _signed(files)
    result = verify_files_and_manifest(files, manifest)
    assert result['valid'] is True
    assert result['files_verified'] == 2
    assert result['files_failed'] == []
    assert result['manifest_ok'] is True


def test_changed_bytes_produce_a_different_hash_and_fail():
    files = {'summary.json': {'b': 2, 'a': 1}, 'alerts.json': [{'id': 'a1'}]}
    manifest = _signed(files)
    tampered = {'summary.json': {'b': 2, 'a': 999}, 'alerts.json': [{'id': 'a1'}]}
    result = verify_files_and_manifest(tampered, manifest)
    assert result['valid'] is False
    assert 'summary.json' in result['files_failed']


def test_missing_file_is_detected():
    files = {'summary.json': {'a': 1}, 'alerts.json': [{'id': 'a1'}]}
    manifest = _signed(files)
    result = verify_files_and_manifest({'alerts.json': [{'id': 'a1'}]}, manifest)
    assert result['valid'] is False
    assert 'summary.json' in result['missing_files']


def test_manifest_hash_excludes_its_own_field():
    files = {'summary.json': {'a': 1}}
    manifest = _signed(files)
    # A manifest whose own hash is corrupted must fail — proving the hash was not
    # computed over bytes that already contain manifest_sha256.
    corrupted = dict(manifest)
    corrupted['manifest_sha256'] = 'deadbeef'
    result = verify_files_and_manifest(files, corrupted)
    assert result['manifest_ok'] is False
    assert result['valid'] is False


def test_key_order_does_not_change_the_hash():
    # Canonical serialization sorts keys, so reordering must not break verification.
    files = {'summary.json': {'a': 1, 'b': 2, 'c': 3}}
    manifest = _signed(files)
    reordered = {'summary.json': {'c': 3, 'a': 1, 'b': 2}}
    result = verify_files_and_manifest(reordered, manifest)
    assert result['valid'] is True
