"""Screen 7 — incident forensic case record (evidence domains + lifecycle timeline).

Two layers, matching the repo's established backend test style:

  1. Pure unit tests for the deterministic classification, integrity, counting and
     ordering logic — no database, no LLM. Identical input must produce identical,
     evidence-grounded output.
  2. A stateful fake connection exercising the workspace-scoped read paths:
     tenant isolation, per-incident grouping, truthful empty states, and the rule
     that an unverified artifact never becomes a verified one.

The truthfulness invariants these tests pin (see CLAUDE.md):
  * an artifact is "sealed"/"immutable" only when it came from a snapshot whose
    hash RE-COMPUTES — a plain database row never earns the mark;
  * the incident snapshot reaches "sealed" only when Screen 9 confirms a package;
  * a domain with no records reports zero, never a borrowed count;
  * a read that FAILED is reported as partial, never as an empty domain;
  * no artifact from another workspace is ever returned.
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from services.api.app import incident_forensics as f


WS_ID = str(uuid.uuid4())
OTHER_WS_ID = str(uuid.uuid4())
USER_ID = str(uuid.uuid4())
INCIDENT_ID = str(uuid.uuid4())
OTHER_INCIDENT_ID = str(uuid.uuid4())
FOREIGN_INCIDENT_ID = str(uuid.uuid4())
ALERT_ID = str(uuid.uuid4())
DETECTION_ID = str(uuid.uuid4())
ASSET_ID = str(uuid.uuid4())
TARGET_ID = str(uuid.uuid4())
EVALUATION_ID = str(uuid.uuid4())
OTHER_EVALUATION_ID = str(uuid.uuid4())
SNAPSHOT_ID = str(uuid.uuid4())
PACKAGE_ID = str(uuid.uuid4())
APPROVAL_ID = str(uuid.uuid4())
TIMELINE_ID = str(uuid.uuid4())
TX_HASH = '0xabc123'


# ==========================================================================
# 1. Deterministic domain classification (no LLM, explicit map)
# ==========================================================================
def test_every_domain_has_at_least_one_mapped_artifact_type():
    mapped = set(f.ARTIFACT_TYPE_DOMAINS.values())
    assert mapped == set(f.EVIDENCE_DOMAINS)


@pytest.mark.parametrize('artifact_type,expected', [
    ('transaction_receipt', f.ON_CHAIN),
    ('mint_event', f.ON_CHAIN),
    ('signer_information', f.ON_CHAIN),
    ('telemetry_event', f.ON_CHAIN),
    ('subscription_record', f.OPERATIONAL),
    ('settlement_record', f.OPERATIONAL),
    ('nav_snapshot', f.OPERATIONAL),
    ('reconciliation_output', f.OPERATIONAL),
    ('policy_decision', f.POLICY),
    ('policy_reason_codes', f.POLICY),
    ('policy_simulation_result', f.POLICY),
    ('approval_decision', f.HUMAN_ACTION),
    ('rejection_decision', f.HUMAN_ACTION),
    ('analyst_note', f.HUMAN_ACTION),
    ('manual_status_transition', f.HUMAN_ACTION),
])
def test_classification_is_a_deterministic_table_lookup(artifact_type, expected):
    assert f.classify_domain(artifact_type) == expected
    # Case and surrounding whitespace never change the verdict.
    assert f.classify_domain(f'  {artifact_type.upper()}  ') == expected


def test_unknown_artifact_type_is_never_guessed_into_a_domain():
    assert f.classify_domain('something_new') is None
    assert f.classify_domain('') is None
    assert f.classify_domain(None) is None


def test_classification_is_stable_across_calls():
    first = {t: f.classify_domain(t) for t in f.ARTIFACT_TYPE_DOMAINS}
    second = {t: f.classify_domain(t) for t in f.ARTIFACT_TYPE_DOMAINS}
    assert first == second


# ==========================================================================
# 2. Integrity: a database row is never cryptographically immutable
# ==========================================================================
def test_snapshot_sealed_artifact_is_the_only_immutable_one():
    status, immutable = f.artifact_integrity(sealed_in_snapshot=True, digest='sha256:aa')
    assert (status, immutable) == (f.INTEGRITY_SNAPSHOT_SEALED, True)


def test_live_row_is_content_hashed_but_not_immutable():
    status, immutable = f.artifact_integrity(sealed_in_snapshot=False, digest='sha256:aa')
    assert status == f.INTEGRITY_CONTENT_HASHED
    assert immutable is False


def test_unverified_artifact_never_becomes_verified_even_inside_a_snapshot():
    # No digest means nothing was verified, and that outranks snapshot membership.
    status, immutable = f.artifact_integrity(sealed_in_snapshot=True, digest=None)
    assert status == f.INTEGRITY_UNVERIFIED
    assert immutable is False


def test_content_digest_is_a_real_reproducible_sha256():
    payload = {'b': 2, 'a': 1}
    digest = f.content_digest(payload)
    assert digest is not None and digest.startswith('sha256:')
    # Canonical: key order does not change the digest, and it is stable.
    assert digest == f.content_digest({'a': 1, 'b': 2})
    assert digest != f.content_digest({'a': 1, 'b': 3})


def test_digest_failure_yields_none_rather_than_a_decorative_hash(monkeypatch):
    import services.api.app.evidence_signing as signing

    def _boom(_obj, **_kwargs):
        raise RuntimeError('cannot canonicalize')

    monkeypatch.setattr(signing, 'canonical_json', _boom)
    assert f.content_digest({'a': object()}) is None


# ==========================================================================
# 3. Counts and ordering
# ==========================================================================
def _artifact(domain, collected_at=None, artifact_id='a'):
    return {'id': artifact_id, 'domain': domain, 'collected_at': collected_at}


def test_counts_are_per_domain_and_total():
    counts = f.count_domains([
        _artifact(f.ON_CHAIN, artifact_id='1'),
        _artifact(f.ON_CHAIN, artifact_id='2'),
        _artifact(f.OPERATIONAL, artifact_id='3'),
        _artifact(f.POLICY, artifact_id='4'),
        _artifact(f.HUMAN_ACTION, artifact_id='5'),
    ])
    assert counts == {'on_chain': 2, 'operational': 1, 'policy': 1, 'human_actions': 1, 'total': 5}


def test_a_domain_with_no_records_counts_zero_and_borrows_nothing():
    counts = f.count_domains([_artifact(f.ON_CHAIN)])
    assert counts['operational'] == 0
    assert counts['policy'] == 0
    assert counts['human_actions'] == 0


def test_unclassified_artifacts_count_toward_total_only():
    counts = f.count_domains([_artifact(None, artifact_id='1'), _artifact(f.POLICY, artifact_id='2')])
    assert counts['total'] == 2
    assert counts['policy'] == 1
    assert sum(counts[k] for k in ('on_chain', 'operational', 'policy', 'human_actions')) == 1


def test_artifacts_sort_by_canonical_timestamp_not_input_order():
    ordered = f.sort_artifacts([
        _artifact(f.POLICY, '2026-01-01T10:42:18.400Z', 'c'),
        _artifact(f.ON_CHAIN, '2026-01-01T10:42:17.920Z', 'a'),
        _artifact(f.OPERATIONAL, '2026-01-01T10:42:18.001Z', 'b'),
    ])
    assert [a['id'] for a in ordered] == ['a', 'b', 'c']


def test_artifact_without_a_timestamp_sorts_last_and_is_never_dropped():
    ordered = f.sort_artifacts([
        _artifact(f.POLICY, None, 'no-time'),
        _artifact(f.ON_CHAIN, '2026-01-01T10:00:00Z', 'timed'),
    ])
    assert [a['id'] for a in ordered] == ['timed', 'no-time']
    assert len(ordered) == 2


def test_timeline_events_sort_ascending_by_occurred_at_with_millisecond_precision():
    events = f.sort_timeline_events([
        {'id': 'z', 'occurred_at': '2026-01-01T10:42:18.401000+00:00'},
        {'id': 'a', 'occurred_at': '2026-01-01T10:42:17.920000+00:00'},
        {'id': 'm', 'occurred_at': '2026-01-01T10:42:18.001000+00:00'},
    ])
    assert [e['id'] for e in events] == ['a', 'm', 'z']


# ==========================================================================
# 4. Snapshot lifecycle: "sealed" belongs to Screen 9
# ==========================================================================
def test_no_snapshot_is_collecting():
    assert f.snapshot_state(snapshot_row={}, hash_verified=None, package={}) == f.SNAPSHOT_COLLECTING


def test_snapshot_with_a_verifying_hash_is_ready_not_sealed():
    state = f.snapshot_state(
        snapshot_row={'id': SNAPSHOT_ID}, hash_verified=True, package={'available': False},
    )
    assert state == f.SNAPSHOT_READY


def test_snapshot_becomes_sealed_only_when_screen9_confirms_a_package():
    state = f.snapshot_state(
        snapshot_row={'id': SNAPSHOT_ID}, hash_verified=True,
        package={'available': True, 'integrity_status': 'verified'},
    )
    assert state == f.SNAPSHOT_SEALED


def test_a_package_that_is_not_verified_does_not_seal_the_snapshot():
    state = f.snapshot_state(
        snapshot_row={'id': SNAPSHOT_ID}, hash_verified=True,
        package={'available': True, 'integrity_status': 'needs_evidence'},
    )
    assert state == f.SNAPSHOT_READY


def test_hash_mismatch_fails_closed_rather_than_reading_as_ready():
    state = f.snapshot_state(
        snapshot_row={'id': SNAPSHOT_ID}, hash_verified=False,
        package={'available': True, 'integrity_status': 'verified'},
    )
    assert state == f.SNAPSHOT_FAILED


def test_snapshot_hash_verification_recomputes_the_real_digest():
    from services.api.app import ai_triage
    payload = {'schema_version': '1.0', 'telemetry': []}
    good = ai_triage.compute_snapshot_hash(payload)
    assert f.verify_snapshot_hash({'snapshot_json': payload, 'snapshot_hash': good}) is True
    assert f.verify_snapshot_hash({'snapshot_json': payload, 'snapshot_hash': 'sha256:tampered'}) is False
    # Nothing to verify is None — never an optimistic True.
    assert f.verify_snapshot_hash({}) is None
    assert f.verify_snapshot_hash({'snapshot_hash': good}) is None


# ==========================================================================
# 5. Forensic timeline assembly: only stages with real records
# ==========================================================================
def test_timeline_contains_only_stages_that_have_records():
    events = f.build_forensic_timeline(
        incident_id=INCIDENT_ID,
        correlation={'event_id': TX_HASH, 'detection': {}},
        timeline_rows=[], reconciliations=[], evaluations=[],
        snapshot_row={}, package={'available': False},
    )
    assert events == []


def test_timeline_never_claims_a_preconfirmation_the_record_does_not_carry():
    events = f.build_forensic_timeline(
        incident_id=INCIDENT_ID,
        correlation={'event_id': TX_HASH, 'detection': {
            'id': DETECTION_ID, 'detected_at': '2026-01-01T10:42:18.001000+00:00',
            'category': 'OPERATIONAL_INTEGRITY', 'title': 'Unmatched issuance',
            'preconfirmation_received_at': None, 'telemetry_observed_at': None,
        }},
        timeline_rows=[], reconciliations=[], evaluations=[],
        snapshot_row={}, package={'available': False},
    )
    assert [e['stage'] for e in events] == ['operational_anomaly']
    assert not any('preconfirmation' in str(e['event_type']) for e in events)


def test_timeline_records_the_full_lifecycle_when_every_stage_exists():
    events = f.build_forensic_timeline(
        incident_id=INCIDENT_ID,
        correlation={'event_id': TX_HASH, 'detection': {
            'id': DETECTION_ID,
            'preconfirmation_received_at': '2026-01-01T10:42:17.920000+00:00',
            'telemetry_observed_at': '2026-01-01T10:42:18.001000+00:00',
            'detected_at': '2026-01-01T10:42:18.059000+00:00',
            'category': 'OPERATIONAL_INTEGRITY', 'title': 'Unmatched issuance',
            'deterministic_reason_code': 'NO_MATCHING_AUTHORIZED_ISSUANCE',
        }},
        timeline_rows=[{
            'id': TIMELINE_ID, 'event_type': 'incident.created',
            'message': 'Incident created from alert.', 'actor_user_id': USER_ID,
            'metadata': {}, 'created_at': '2026-01-01T10:42:18.382000+00:00',
        }],
        reconciliations=[{
            'id': str(uuid.uuid4()), 'status': 'UNEXPLAINED_VARIANCE',
            'reason_code': 'NO_MATCHING_AUTHORIZED_ISSUANCE', 'rule_id': 'RP-17', 'rule_version': 4,
            'authoritative_source': 'Transfer Agent',
            'evaluated_at': '2026-01-01T10:42:18.188000+00:00',
        }],
        evaluations=[{
            'evaluation_id': EVALUATION_ID, 'policy_key': 'POL-MINT-007', 'policy_version': 7,
            'decision': 'DENY', 'reason_codes': ['COMPLIANCE_APPROVAL_MISSING'],
            'simulation': False, 'evaluated_at': '2026-01-01T10:42:18.214000+00:00',
        }],
        snapshot_row={'id': SNAPSHOT_ID, 'snapshot_hash': 'sha256:aa', 'evidence_count': 3,
                      'created_at': '2026-01-01T10:42:18.401000+00:00'},
        package={'available': True, 'package_id': PACKAGE_ID, 'package_number': 'EV-2026-017',
                 'integrity_status': 'verified', 'sealed_at': '2026-01-01T10:42:18.900000+00:00'},
    )
    stages = [e['stage'] for e in events]
    assert stages == [
        'state_drift_detected',      # preconfirmation
        'state_drift_detected',      # on-chain event observed
        'operational_anomaly',       # detection recorded
        'operational_anomaly',       # reconciliation evaluated
        'policy_decision',           # enforcement decision
        'incident_created',
        'evidence_snapshot_created',
        'evidence_package_sealed',
    ]
    # Ordering is by canonical timestamp, ascending, at millisecond precision.
    times = [e['occurred_at'] for e in events]
    assert times == sorted(times)


def test_a_policy_simulation_is_staged_as_an_evaluation_not_as_the_decision():
    events = f.build_forensic_timeline(
        incident_id=INCIDENT_ID, correlation={'event_id': TX_HASH, 'detection': {}},
        timeline_rows=[], reconciliations=[],
        evaluations=[{
            'evaluation_id': EVALUATION_ID, 'policy_key': 'POL-MINT-007', 'decision': 'DENY',
            'simulation': True, 'evaluated_at': '2026-01-01T10:00:00.000000+00:00',
        }],
        snapshot_row={}, package={'available': False},
    )
    assert [e['stage'] for e in events] == ['policy_evaluated']
    assert events[0]['metadata']['simulation'] is True


def test_sealing_is_recorded_only_when_screen9_reports_a_sealed_at():
    events = f.build_forensic_timeline(
        incident_id=INCIDENT_ID, correlation={'event_id': TX_HASH, 'detection': {}},
        timeline_rows=[], reconciliations=[], evaluations=[], snapshot_row={},
        package={'available': True, 'package_id': PACKAGE_ID, 'sealed_at': None},
    )
    assert events == []


def test_a_timeline_row_without_an_actor_is_never_attributed_to_a_person():
    events = f.build_forensic_timeline(
        incident_id=INCIDENT_ID, correlation={'event_id': TX_HASH, 'detection': {}},
        timeline_rows=[{
            'id': TIMELINE_ID, 'event_type': 'incident.status_changed',
            'message': 'Incident status changed to investigating.',
            'actor_user_id': None, 'metadata': {}, 'created_at': '2026-01-01T10:00:00Z',
        }],
        reconciliations=[], evaluations=[], snapshot_row={}, package={'available': False},
    )
    assert events[0]['actor_type'] == 'system'
    assert events[0]['actor_id'] is None


# ==========================================================================
# 6. DB-facing reads: workspace isolation, grouping, empty states
# ==========================================================================
class _Result:
    def __init__(self, rows=None):
        self._rows = list(rows or [])

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _ForensicConn:
    """Workspace-scoped fake carrying two incidents in two different workspaces.

    Every SELECT below filters on the workspace_id it was handed, exactly as the
    production SQL does, so a cross-tenant read returns nothing rather than another
    workspace's rows.
    """

    def __init__(self, *, missing_tables=(), failing_facts=()):
        self.missing_tables = set(missing_tables)
        self.failing_facts = set(failing_facts)
        self.statements: list[str] = []
        self.incidents = [
            {'id': INCIDENT_ID, 'workspace_id': WS_ID, 'reference': 'INC-2026-017',
             'title': 'Unmatched issuance on US Treasury Bond', 'severity': 'critical',
             'status': 'open', 'workflow_status': 'investigating', 'summary': 'Supply variance.',
             'target_id': TARGET_ID, 'source_alert_id': ALERT_ID,
             'created_at': '2026-01-01T10:42:18.382000+00:00',
             'updated_at': '2026-01-01T10:45:00.000000+00:00'},
            {'id': OTHER_INCIDENT_ID, 'workspace_id': WS_ID, 'reference': 'INC-2026-018',
             'title': 'Second incident', 'severity': 'low', 'status': 'open',
             'workflow_status': 'open', 'summary': None, 'target_id': None,
             'source_alert_id': None, 'created_at': '2026-01-02T00:00:00+00:00',
             'updated_at': '2026-01-02T00:00:00+00:00'},
            {'id': FOREIGN_INCIDENT_ID, 'workspace_id': OTHER_WS_ID, 'reference': 'INC-2026-900',
             'title': 'Another tenant', 'severity': 'critical', 'status': 'open',
             'workflow_status': 'open', 'summary': None, 'target_id': None,
             'source_alert_id': None, 'created_at': '2026-01-03T00:00:00+00:00',
             'updated_at': '2026-01-03T00:00:00+00:00'},
        ]

    # -- psycopg-compatible savepoint used by read_scope -----------------------
    @contextmanager
    def transaction(self):
        yield

    def execute(self, statement: str, params=None):
        norm = ' '.join(str(statement).split())
        self.statements.append(norm)
        params = tuple(params or ())

        if 'to_regclass' in norm:
            table = str(params[0]).removeprefix('public.')
            return _Result([{'present': table not in self.missing_tables}])

        if 'FROM incidents' in norm:
            wanted, workspace = str(params[0]), str(params[1])
            row = next(
                (dict(i) for i in self.incidents
                 if i['id'] == wanted and i['workspace_id'] == workspace),
                None,
            )
            return _Result([row] if row else [])

        if 'FROM threat_detections' in norm:
            self._maybe_fail('threat_detection')
            # Mirrors the production predicate: workspace + (linked_incident_id OR
            # linked_alert_id). Only the first incident carries a linked detection.
            workspace, incident, alert = str(params[0]), str(params[1]), params[2]
            if workspace != WS_ID or (incident != INCIDENT_ID and alert != ALERT_ID):
                return _Result([])
            return _Result([{
                'id': DETECTION_ID, 'detection_type': 'unmatched_issuance',
                'category': 'OPERATIONAL_INTEGRITY', 'title': 'Unmatched issuance',
                'severity': 'critical', 'tx_hash': TX_HASH, 'block_number': 4242,
                'primary_asset_id': ASSET_ID, 'operation': 'mint',
                'deterministic_reason_code': 'NO_MATCHING_AUTHORIZED_ISSUANCE',
                'detected_at': '2026-01-01T10:42:18.059000+00:00',
                'telemetry_stage': 'PRECONFIRMATION', 'telemetry_source': 'base_preconf',
                'evidence_source': 'live', 'operational_checks': {'settlement': 'missing'},
                'observed_amount': '500000', 'expected_amount': '0', 'variance_amount': '500000',
                'amount_decimals': 0, 'amount_unit': 'units', 'provenance': {},
                'preconfirmation_received_at': '2026-01-01T10:42:17.920000+00:00',
                'telemetry_observed_at': '2026-01-01T10:42:18.001000+00:00',
            }])

        if 'FROM targets' in norm:
            return _Result([{'asset_id': ASSET_ID}] if str(params[1]) == WS_ID else [])

        if 'FROM assets' in norm:
            return _Result([{'name': 'US Treasury Bond #013', 'asset_type': 'bond',
                             'identifier': '0x91d2'}] if str(params[1]) == WS_ID else [])

        if 'FROM incident_evidence_snapshots' in norm:
            self._maybe_fail('evidence_snapshot')
            if str(params[0]) != WS_ID or str(params[1]) != INCIDENT_ID:
                return _Result([])
            payload = {'schema_version': '1.0', 'telemetry': [
                {'telemetry_id': 'tel-1', 'event_type': 'wallet_transfer_detected',
                 'tx_hash': TX_HASH, 'block_number': 4242, 'chain_id': 8453,
                 'detected_by': 'quicknode_stream', 'evidence_source': 'live_provider',
                 'observed_at': '2026-01-01T10:42:18.001000+00:00'},
            ], 'provider_observations': []}
            from services.api.app import ai_triage
            return _Result([{
                'id': SNAPSHOT_ID, 'schema_version': '1.0',
                'snapshot_hash': ai_triage.compute_snapshot_hash(payload),
                'snapshot_json': payload, 'evidence_count': 1, 'is_complete': True,
                'incomplete_reasons': [], 'created_at': '2026-01-01T10:42:18.401000+00:00',
            }])

        if 'FROM export_jobs' in norm:
            self._maybe_fail('evidence_package')
            if str(params[0]) != WS_ID or str(params[1]) != INCIDENT_ID:
                return _Result([])
            return _Result([{
                'id': PACKAGE_ID, 'export_type': 'proof_bundle', 'status': 'completed',
                'filters': {'incident_id': INCIDENT_ID, 'manifest_sha256': 'sha256:mm',
                            'verification': {'valid': True}, 'completeness_score': 100},
                'package_number': 'EV-2026-017', 'size_bytes': 1024,
                'created_at': '2026-01-01T10:50:00+00:00',
                'updated_at': '2026-01-01T10:51:00+00:00',
            }])

        if 'FROM governance_policy_evaluations' in norm:
            self._maybe_fail('policy_evaluation')
            if str(params[0]) != WS_ID:
                return _Result([])
            rows = [{
                'id': EVALUATION_ID, 'policy_id': str(uuid.uuid4()), 'policy_key': 'POL-MINT-007',
                'policy_version': 7, 'decision': 'DENY',
                'reason_codes': ['COMPLIANCE_APPROVAL_MISSING'],
                'required_approvals': ['COMPLIANCE_APPROVER'],
                'checks': [{'check': 'compliance_approval', 'passed': False}],
                'operation': 'mint', 'amount_usd': '5000000.00', 'simulation': False,
                'engine_version': 'policy-v1', 'canonical_event_id': TX_HASH,
                'asset_id': ASSET_ID, 'incident_id': INCIDENT_ID,
                'evaluated_at': '2026-01-01T10:42:18.214000+00:00',
            }]
            # The evaluation belonging to the OTHER incident must not match this one.
            if str(params[1]) == OTHER_INCIDENT_ID:
                rows = [{**rows[0], 'id': OTHER_EVALUATION_ID, 'incident_id': OTHER_INCIDENT_ID,
                         'canonical_event_id': None, 'asset_id': None, 'decision': 'ALLOW',
                         'reason_codes': [], 'required_approvals': []}]
            return _Result(rows)

        if 'FROM asset_reconciliation_snapshots' in norm:
            self._maybe_fail('reconciliation_snapshot')
            if str(params[0]) != WS_ID:
                return _Result([])
            return _Result([{
                'id': str(uuid.uuid4()), 'status': 'UNEXPLAINED_VARIANCE',
                'reason_code': 'NO_MATCHING_AUTHORIZED_ISSUANCE', 'severity': 'critical',
                'observed_supply': '5000000', 'expected_supply': '4500000',
                'variance_units': '500000', 'token_decimals': 0, 'rule_id': 'RP-17',
                'rule_version': 4, 'onchain_source': 'base', 'authoritative_source': 'Transfer Agent',
                'evidence_source': 'live', 'tx_hash': TX_HASH, 'block_number': 4242,
                'external_reference': 'SUB-81922', 'matched_issuance_id': None,
                'evaluated_at': '2026-01-01T10:42:18.188000+00:00',
                'onchain_observed_at': '2026-01-01T10:42:18.001000+00:00',
                'authoritative_observed_at': '2026-01-01T10:42:10.000000+00:00',
                'canonical_event_id': DETECTION_ID,
            }])

        if 'FROM asset_authoritative_state' in norm:
            if str(params[0]) != WS_ID:
                return _Result([])
            return _Result([{
                'id': str(uuid.uuid4()), 'expected_total_supply': '4500000', 'token_decimals': 0,
                'settlement_state': 'settled', 'source_name': 'Demo Transfer Agent',
                'source_kind': 'transfer_agent', 'source_status': 'reported', 'source_error': None,
                'external_reference': 'SUB-81922', 'evidence_source': 'live',
                'observed_at': '2026-01-01T10:42:10.000000+00:00',
            }])

        if 'FROM asset_authorized_issuances' in norm:
            return _Result([])

        if 'FROM response_action_approvals' in norm:
            self._maybe_fail('response_action_approval')
            if str(params[0]) != WS_ID or str(params[1]) != INCIDENT_ID:
                return _Result([])
            return _Result([{
                'id': APPROVAL_ID, 'subject_domain': 'response_action',
                'subject_id': str(uuid.uuid4()), 'action_version': 1,
                'approver_user_id': USER_ID, 'approver_role': 'SECURITY_LEAD',
                'decision': 'approved', 'note': None, 'required_quorum': 2,
                'policy': 'quorum', 'created_at': '2026-01-01T10:43:02.000000+00:00',
            }])

        if 'FROM incident_timeline' in norm:
            self._maybe_fail('incident_timeline')
            workspace, incident = str(params[0]), str(params[1])
            if workspace != WS_ID or incident != INCIDENT_ID:
                return _Result([])
            rows = [{
                'id': TIMELINE_ID, 'incident_id': INCIDENT_ID, 'event_type': 'incident.created',
                'message': 'Incident created from alert.', 'actor_user_id': USER_ID,
                'metadata': {'alert_id': ALERT_ID},
                'created_at': '2026-01-01T10:42:18.382000+00:00',
            }, {
                'id': str(uuid.uuid4()), 'incident_id': INCIDENT_ID,
                'event_type': 'evidence.linked', 'message': 'Evidence linked.',
                'actor_user_id': None, 'metadata': {},
                'created_at': '2026-01-01T10:42:18.401000+00:00',
            }]
            if 'actor_user_id IS NOT NULL' in norm:
                rows = [r for r in rows if r['actor_user_id'] is not None]
            return _Result(rows)

        return _Result([])

    def _maybe_fail(self, fact: str):
        if fact in self.failing_facts:
            raise RuntimeError(f'simulated read failure: {fact}')

    def commit(self):
        pass


@contextmanager
def _pg(conn):
    yield conn


def _install(monkeypatch, conn, *, workspace_id=WS_ID):
    from services.api.app import pilot
    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'pg_connection', lambda: _pg(conn))
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda _c: None)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda _c, _r: {'id': USER_ID})
    monkeypatch.setattr(
        pilot, 'resolve_workspace',
        lambda _c, _u, _h: {'workspace_id': workspace_id, 'role': 'admin'},
    )


def _request(workspace_id=WS_ID):
    return SimpleNamespace(headers={'x-workspace-id': workspace_id})


def test_evidence_groups_every_domain_for_the_incident(monkeypatch):
    conn = _ForensicConn()
    _install(monkeypatch, conn)
    payload = f.get_incident_evidence(INCIDENT_ID, _request())

    counts = payload['counts']
    assert counts['on_chain'] >= 1          # snapshot telemetry
    assert counts['operational'] >= 1       # detection + reconciliation + authoritative state
    assert counts['policy'] >= 1            # the deterministic evaluation
    assert counts['human_actions'] >= 1     # the recorded approval
    # The counts describe the artifacts that were actually returned.
    assert counts['total'] == len(payload['artifacts'])
    assert counts == f.count_domains(payload['artifacts'])


def test_every_artifact_belongs_to_the_requested_incident(monkeypatch):
    conn = _ForensicConn()
    _install(monkeypatch, conn)
    payload = f.get_incident_evidence(INCIDENT_ID, _request())
    assert payload['artifacts']
    assert all(a['incident_id'] == INCIDENT_ID for a in payload['artifacts'])


def test_artifacts_are_ordered_by_canonical_collection_time(monkeypatch):
    conn = _ForensicConn()
    _install(monkeypatch, conn)
    payload = f.get_incident_evidence(INCIDENT_ID, _request())
    times = [a['collected_at'] for a in payload['artifacts'] if a['collected_at']]
    assert times == sorted(times)


def test_a_foreign_workspace_incident_is_404_and_leaks_no_artifact(monkeypatch):
    conn = _ForensicConn()
    _install(monkeypatch, conn)  # caller is scoped to WS_ID
    with pytest.raises(HTTPException) as excinfo:
        f.get_incident_evidence(FOREIGN_INCIDENT_ID, _request())
    assert excinfo.value.status_code == 404
    # The 404 is raised BEFORE any artifact table is read.
    assert not any('FROM governance_policy_evaluations' in s for s in conn.statements)
    assert not any('FROM incident_evidence_snapshots' in s for s in conn.statements)


def test_a_workspace_cannot_read_another_workspaces_incident_evidence(monkeypatch):
    conn = _ForensicConn()
    _install(monkeypatch, conn, workspace_id=OTHER_WS_ID)
    with pytest.raises(HTTPException) as excinfo:
        f.get_incident_evidence(INCIDENT_ID, _request(OTHER_WS_ID))
    assert excinfo.value.status_code == 404


def test_policy_evidence_is_linked_to_the_correct_incident(monkeypatch):
    conn = _ForensicConn()
    _install(monkeypatch, conn)
    payload = f.get_incident_evidence(INCIDENT_ID, _request())
    evaluations = payload['policy_evaluations']
    assert [e['evaluation_id'] for e in evaluations] == [EVALUATION_ID]
    assert evaluations[0]['decision'] == 'DENY'
    assert evaluations[0]['reason_codes'] == ['COMPLIANCE_APPROVAL_MISSING']
    assert evaluations[0]['required_approvals'] == ['COMPLIANCE_APPROVER']
    assert evaluations[0]['policy_version'] == 7
    # The authoritative result names the deterministic engine, never an AI layer.
    assert evaluations[0]['authority'] == 'deterministic_policy_engine'

    other = f.get_incident_evidence(OTHER_INCIDENT_ID, _request())
    assert [e['evaluation_id'] for e in other['policy_evaluations']] == [OTHER_EVALUATION_ID]


def test_human_action_evidence_is_linked_to_the_correct_incident(monkeypatch):
    conn = _ForensicConn()
    _install(monkeypatch, conn)
    human = [a for a in f.get_incident_evidence(INCIDENT_ID, _request())['artifacts']
             if a['domain'] == f.HUMAN_ACTION]
    assert any(a['metadata'].get('decision') == 'approved' for a in human)
    assert all(a['incident_id'] == INCIDENT_ID for a in human)

    # The second incident has no approvals and no human timeline rows of its own.
    other_human = [a for a in f.get_incident_evidence(OTHER_INCIDENT_ID, _request())['artifacts']
                   if a['domain'] == f.HUMAN_ACTION]
    assert other_human == []


def test_snapshot_telemetry_is_sealed_and_live_rows_are_only_content_hashed(monkeypatch):
    conn = _ForensicConn()
    _install(monkeypatch, conn)
    payload = f.get_incident_evidence(INCIDENT_ID, _request())
    sealed = [a for a in payload['artifacts'] if a['integrity_status'] == f.INTEGRITY_SNAPSHOT_SEALED]
    hashed = [a for a in payload['artifacts'] if a['integrity_status'] == f.INTEGRITY_CONTENT_HASHED]

    assert sealed and all(a['immutable'] is True for a in sealed)
    assert all(a['domain'] == f.ON_CHAIN for a in sealed)
    # Live database rows are hashed but never marked immutable.
    assert hashed and all(a['immutable'] is False for a in hashed)


def test_every_artifact_hash_is_a_real_digest_of_its_own_payload(monkeypatch):
    conn = _ForensicConn()
    _install(monkeypatch, conn)
    payload = f.get_incident_evidence(INCIDENT_ID, _request())
    digests = [a['content_sha256'] for a in payload['artifacts']]
    assert all(d is None or d.startswith('sha256:') for d in digests)
    # Distinct records produce distinct digests — no shared placeholder value.
    real = [d for d in digests if d]
    assert len(set(real)) == len(real)


def test_missing_evidence_produces_a_truthful_empty_state(monkeypatch):
    # The second incident has no detection, no snapshot, no package and no approvals.
    conn = _ForensicConn()
    _install(monkeypatch, conn)
    payload = f.get_incident_evidence(OTHER_INCIDENT_ID, _request())
    assert payload['counts']['on_chain'] == 0
    assert payload['counts']['human_actions'] == 0
    assert payload['snapshot']['status'] == f.SNAPSHOT_COLLECTING
    assert payload['snapshot']['snapshot_hash'] is None
    assert payload['evidence_package'] == {'available': False, 'reason': 'not_generated'}


def test_a_failed_read_is_reported_as_partial_not_as_an_empty_domain(monkeypatch):
    conn = _ForensicConn(failing_facts={'policy_evaluation'})
    _install(monkeypatch, conn)
    payload = f.get_incident_evidence(INCIDENT_ID, _request())
    assert payload['partial'] is True
    assert 'policy_evaluation' in payload['unreadable']
    assert payload['counts']['policy'] == 0  # zero, but explicitly flagged as unread


def test_a_missing_table_never_fabricates_a_package(monkeypatch):
    conn = _ForensicConn(missing_tables={'export_jobs'})
    _install(monkeypatch, conn)
    payload = f.get_incident_evidence(INCIDENT_ID, _request())
    assert payload['evidence_package']['available'] is False
    assert payload['evidence_package']['reason'] == 'unavailable'
    assert payload['snapshot']['status'] == f.SNAPSHOT_READY  # ready, never sealed


def test_package_linkage_reuses_screen9_state_and_exposes_its_route(monkeypatch):
    conn = _ForensicConn()
    _install(monkeypatch, conn)
    package = f.get_incident_evidence(INCIDENT_ID, _request())['evidence_package']
    assert package['available'] is True
    assert package['package_number'] == 'EV-2026-017'
    assert package['integrity_status'] == 'verified'
    assert package['sealed_at'] is not None
    assert package['route'] == f'/evidence?package_id={PACKAGE_ID}'


def test_case_header_resolves_the_real_asset_and_detection_category(monkeypatch):
    conn = _ForensicConn()
    _install(monkeypatch, conn)
    header = f.get_incident_evidence(INCIDENT_ID, _request())['incident']
    assert header['asset_label'] == 'US Treasury Bond #013'
    assert header['detection_category'] == 'OPERATIONAL_INTEGRITY'
    assert header['detection_type'] == 'unmatched_issuance'
    assert header['reference'] == 'INC-2026-017'
    assert header['opened_at'] == '2026-01-01T10:42:18.382000+00:00'


def test_an_unresolvable_asset_is_reported_absent_not_substituted(monkeypatch):
    conn = _ForensicConn()
    _install(monkeypatch, conn)
    header = f.get_incident_evidence(OTHER_INCIDENT_ID, _request())['incident']
    assert header['asset_label'] is None
    assert header['detection_category'] is None


def test_every_artifact_states_how_it_was_linked_to_the_incident(monkeypatch):
    conn = _ForensicConn()
    _install(monkeypatch, conn)
    artifacts = f.get_incident_evidence(INCIDENT_ID, _request())['artifacts']
    assert artifacts
    # Every artifact declares HOW it reached this incident: policy rows carry
    # Screen 8's match provenance; the rest carry an explicit link scope.
    unstated = [
        a['artifact_type'] for a in artifacts
        if a['metadata'].get('link_scope') not in
        {f.LINK_SCOPE_EVENT, f.LINK_SCOPE_ASSET, f.LINK_SCOPE_INCIDENT}
        and not a['metadata'].get('match_provenance')
    ]
    assert unstated == []


def test_an_asset_scoped_record_is_never_stamped_as_event_evidence(monkeypatch):
    conn = _ForensicConn()
    _install(monkeypatch, conn)
    artifacts = f.get_incident_evidence(INCIDENT_ID, _request())['artifacts']
    # The transfer-agent authoritative state carries no event column, so its link
    # is the ASSET and it says so — it is never presented as this event's record.
    state = [a for a in artifacts if a['artifact_type'] == 'authoritative_state']
    assert state and all(a['metadata']['link_scope'] == f.LINK_SCOPE_ASSET for a in state)
    # The detection itself IS this incident's canonical event.
    detection = [a for a in artifacts if a['artifact_type'] == 'detection_record']
    assert detection and detection[0]['metadata']['link_scope'] == f.LINK_SCOPE_EVENT


def test_reconciliation_prefers_the_snapshot_that_names_this_incidents_event(monkeypatch):
    conn = _ForensicConn()
    _install(monkeypatch, conn)
    f.get_incident_evidence(INCIDENT_ID, _request())
    reads = [s for s in conn.statements if 'FROM asset_reconciliation_snapshots' in s]
    # The event-linked probe runs first; it succeeds, so no asset-wide fallback runs.
    assert len(reads) == 1
    assert 'canonical_event_id = %s::uuid' in reads[0]


def test_policy_evidence_carries_screen8s_own_match_provenance(monkeypatch):
    conn = _ForensicConn()
    _install(monkeypatch, conn)
    from services.api.app.domains.response_gate import config as rgc
    evaluation = f.get_incident_evidence(INCIDENT_ID, _request())['policy_evaluations'][0]
    # The evaluation names this incident's canonical event, so it is event-shared —
    # not the weaker asset-shared link that would only mean "same asset".
    assert evaluation['match_provenance'] == rgc.MATCH_EVENT_SHARED


def test_artifact_provenance_is_carried_through_verbatim(monkeypatch):
    # The collector never rewrites an artifact's evidence provenance, so simulator
    # or replay data reaches the UI labelled as such and can never be rendered as
    # live customer evidence.
    conn = _ForensicConn()
    _install(monkeypatch, conn)
    artifacts = f.get_incident_evidence(INCIDENT_ID, _request())['artifacts']
    telemetry = [a for a in artifacts if a['artifact_type'] == 'telemetry_event']
    assert telemetry and telemetry[0]['metadata']['evidence_source'] == 'live_provider'
    detection = [a for a in artifacts if a['artifact_type'] == 'detection_record']
    assert detection and detection[0]['metadata']['evidence_source'] == 'live'


def test_each_canonical_source_is_queried_once_per_request(monkeypatch):
    # No N+1: per-domain counts derive from the artifact list in memory, and each
    # canonical table is read exactly once for the whole directory.
    conn = _ForensicConn()
    _install(monkeypatch, conn)
    f.get_incident_evidence(INCIDENT_ID, _request())
    for table in ('governance_policy_evaluations', 'incident_evidence_snapshots',
                  'export_jobs', 'asset_reconciliation_snapshots',
                  'response_action_approvals', 'threat_detections'):
        reads = [s for s in conn.statements if f'FROM {table}' in s]
        assert len(reads) == 1, f'{table} was read {len(reads)} times'


def test_the_canonical_event_id_is_reused_never_minted(monkeypatch):
    conn = _ForensicConn()
    _install(monkeypatch, conn)
    payload = f.get_incident_evidence(INCIDENT_ID, _request())
    # The detection's own transaction hash is the correlation key the rest of the
    # workflow already stamps — Screen 7 adopts it rather than creating its own.
    assert payload['event_id'] == TX_HASH
    assert all(a['event_id'] == TX_HASH for a in payload['artifacts'])

    # With no detection there is nothing to borrow, so the incident id is used —
    # still an existing identifier, never a freshly generated one.
    fallback = f.get_incident_evidence(OTHER_INCIDENT_ID, _request())
    assert fallback['event_id'] == OTHER_INCIDENT_ID


# ==========================================================================
# 7. Combined timeline endpoint: legacy shape preserved, forensic added
# ==========================================================================
def test_timeline_endpoint_preserves_the_legacy_newest_first_projection(monkeypatch):
    conn = _ForensicConn()
    _install(monkeypatch, conn)
    payload = f.get_incident_timeline(INCIDENT_ID, _request())
    legacy = payload['timeline']
    assert len(legacy) == 2
    # Unchanged contract: newest first, with the original columns.
    assert legacy[0]['created_at'] > legacy[1]['created_at']
    assert set(legacy[0]) >= {'id', 'incident_id', 'event_type', 'message', 'actor_user_id',
                              'metadata', 'created_at'}


def test_timeline_endpoint_adds_the_forensic_lifecycle_oldest_first(monkeypatch):
    conn = _ForensicConn()
    _install(monkeypatch, conn)
    payload = f.get_incident_timeline(INCIDENT_ID, _request())
    events = payload['events']
    assert events
    times = [e['occurred_at'] for e in events]
    assert times == sorted(times)
    assert payload['event_id'] == TX_HASH
    stages = {e['stage'] for e in events}
    assert {'state_drift_detected', 'operational_anomaly', 'policy_decision',
            'incident_created', 'evidence_snapshot_created'} <= stages


def test_timeline_endpoint_reads_incident_timeline_only_once(monkeypatch):
    conn = _ForensicConn()
    _install(monkeypatch, conn)
    f.get_incident_timeline(INCIDENT_ID, _request())
    reads = [s for s in conn.statements if 'FROM incident_timeline' in s]
    assert len(reads) == 1


def test_timeline_endpoint_is_workspace_scoped(monkeypatch):
    conn = _ForensicConn()
    _install(monkeypatch, conn, workspace_id=OTHER_WS_ID)
    with pytest.raises(HTTPException) as excinfo:
        f.get_incident_timeline(INCIDENT_ID, _request(OTHER_WS_ID))
    assert excinfo.value.status_code == 404


def test_timeline_endpoint_reports_an_unreadable_source_as_partial(monkeypatch):
    conn = _ForensicConn(failing_facts={'reconciliation_snapshot'})
    _install(monkeypatch, conn)
    payload = f.get_incident_timeline(INCIDENT_ID, _request())
    assert payload['partial'] is True
    assert 'reconciliation_snapshot' in payload['unreadable']
    # The legacy projection still returns — a degraded forensic view never takes
    # the timeline endpoint down with it.
    assert payload['timeline']


def test_an_incident_with_no_history_gets_no_fabricated_stages(monkeypatch):
    # The second incident has no timeline rows, no detection, no snapshot and no
    # package — only the one policy evaluation the fake records for it. Every stage
    # the reference design shows but this incident has no record for stays absent.
    conn = _ForensicConn()
    _install(monkeypatch, conn)
    payload = f.get_incident_timeline(OTHER_INCIDENT_ID, _request())
    assert payload['timeline'] == []
    stages = {e['stage'] for e in payload['events']}
    assert stages == {'policy_decision'}
    assert 'incident_created' not in stages
    assert 'evidence_snapshot_created' not in stages
    assert 'evidence_package_sealed' not in stages
    assert 'state_drift_detected' not in stages


# ==========================================================================
# 6. Case summary — the deterministic answer to "what happened, and what proves it"
# ==========================================================================
def test_case_summary_states_the_chain_the_business_the_policy_and_the_evidence(monkeypatch):
    conn = _ForensicConn()
    _install(monkeypatch, conn)
    summary = f.get_incident_evidence(INCIDENT_ID, _request())['case_summary']

    # What the chain recorded.
    assert summary['on_chain']['state'] == f.STATE_OBSERVED
    assert summary['on_chain']['tx_hash'] == TX_HASH
    assert summary['on_chain']['operation'] == 'mint'
    assert summary['on_chain']['observed_amount'] == {'value': '500000', 'decimals': 0, 'unit': 'units'}

    # What the systems of record said — the reconciliation engine's own verdict.
    assert summary['operational']['state'] == f.STATE_ANOMALY
    assert summary['operational']['reconciliation_status'] == 'UNEXPLAINED_VARIANCE'
    assert summary['operational']['reason_code'] == 'NO_MATCHING_AUTHORIZED_ISSUANCE'
    assert summary['operational']['authoritative_source'] == 'Transfer Agent'

    # What the deterministic engine decided.
    assert summary['policy']['state'] == f.STATE_DECIDED
    assert summary['policy']['decision'] == 'DENY'
    assert summary['policy']['authority'] == 'deterministic_policy_engine'

    # What proves it.
    assert summary['evidence']['artifact_count'] == len(
        f.get_incident_evidence(INCIDENT_ID, _request())['artifacts'])
    assert summary['evidence']['snapshot_status']


def test_case_summary_carries_the_canonical_correlation_ids(monkeypatch):
    # Screen 7 correlates; it never mints an identifier. The event id is the one
    # Screens 3/5/8/9/11 are stamped with.
    conn = _ForensicConn()
    _install(monkeypatch, conn)
    payload = f.get_incident_evidence(INCIDENT_ID, _request())
    summary = payload['case_summary']
    assert summary['event_id'] == payload['event_id'] == TX_HASH
    assert summary['correlation'] == {
        'event_id': TX_HASH,
        'incident_id': INCIDENT_ID,
        'alert_id': ALERT_ID,
        'detection_id': DETECTION_ID,
        'asset_id': ASSET_ID,
    }


def test_case_summary_is_a_pure_fold_and_opens_no_cursor():
    # It folds records the request already read. Taking no connection is what makes
    # that structural: the summary cannot silently double the endpoint's cost.
    import inspect

    parameters = inspect.signature(f.build_case_summary).parameters
    assert 'connection' not in parameters
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY for p in parameters.values())
    assert set(parameters) == {'correlation', 'artifacts', 'evaluations', 'counts',
                               'snapshot', 'package'}


def test_the_evidence_endpoint_reads_each_source_once(monkeypatch):
    # Guards the same cost invariant end to end: adding the summary must not have
    # introduced a second read of any table the directory already queried.
    conn = _ForensicConn()
    _install(monkeypatch, conn)
    f.get_incident_evidence(INCIDENT_ID, _request())
    for table in ('FROM threat_detections', 'FROM governance_policy_evaluations',
                  'FROM incident_evidence_snapshots', 'FROM asset_authoritative_state'):
        assert len([s for s in conn.statements if table in ' '.join(s.split())]) <= 1, table


def test_an_incident_with_no_records_reports_not_recorded_never_a_clean_bill(monkeypatch):
    # The second incident has no detection, no reconciliation and no snapshot.
    # Every section must say so rather than defaulting to a reassuring verdict.
    conn = _ForensicConn()
    _install(monkeypatch, conn)
    summary = f.get_incident_evidence(OTHER_INCIDENT_ID, _request())['case_summary']
    assert summary['on_chain']['state'] == f.STATE_NOT_RECORDED
    assert summary['operational']['state'] == f.STATE_NOT_RECORDED
    # Absence is never 'reconciled'.
    assert summary['operational']['state'] != f.STATE_RECONCILED
    assert summary['detection']['detection_type'] is None


def test_a_simulated_policy_evaluation_never_becomes_the_case_verdict():
    # A Screen 11 what-if predicts; it never authorized anything. Promoting one
    # into the policy slot would misstate why the response was gated.
    summary = f.build_case_summary(
        correlation={'event_id': TX_HASH, 'incident_id': INCIDENT_ID, 'detection': {}},
        artifacts=[],
        evaluations=[{'evaluation_id': EVALUATION_ID, 'decision': 'ALLOW', 'simulation': True}],
        counts={}, snapshot={}, package={},
    )
    assert summary['policy']['state'] == f.STATE_NOT_RECORDED
    assert summary['policy']['decision'] is None
    assert summary['policy']['evaluation_count'] == 0


def test_an_asset_scoped_reconciliation_is_not_this_events_verdict():
    # A record that concerns the same ASSET but was never linked to this event is
    # a coincidence, not the case's operational verdict.
    asset_scoped = {
        'artifact_type': 'reconciliation_output', 'source': 'Transfer Agent',
        'collected_at': '2026-01-01T09:00:00+00:00',
        'metadata': {'status': 'RECONCILED', 'link_scope': f.LINK_SCOPE_ASSET},
    }
    summary = f.build_case_summary(
        correlation={'event_id': TX_HASH, 'incident_id': INCIDENT_ID, 'detection': {}},
        artifacts=[asset_scoped], evaluations=[], counts={}, snapshot={}, package={},
    )
    assert summary['operational']['state'] == f.STATE_NOT_RECORDED
    assert summary['operational']['reconciliation_status'] is None


def test_an_indeterminate_reconciliation_is_neither_reconciled_nor_an_anomaly():
    for status in sorted(f._RECON_INDETERMINATE_STATUSES):
        summary = f.build_case_summary(
            correlation={'event_id': TX_HASH, 'incident_id': INCIDENT_ID, 'detection': {}},
            artifacts=[{
                'artifact_type': 'reconciliation_output', 'source': 'Transfer Agent',
                'collected_at': '2026-01-01T09:00:00+00:00',
                'metadata': {'status': status, 'link_scope': f.LINK_SCOPE_EVENT},
            }],
            evaluations=[], counts={}, snapshot={}, package={},
        )
        assert summary['operational']['state'] == f.STATE_INDETERMINATE


def test_a_detection_alone_never_claims_a_chain_observation():
    # A detection row is an operational record. Without a transaction identity or a
    # chain-side artifact, "observed on chain" is not a fact this case has.
    summary = f.build_case_summary(
        correlation={'event_id': INCIDENT_ID, 'incident_id': INCIDENT_ID,
                     'detection': {'id': DETECTION_ID, 'deterministic_reason_code': 'X'}},
        artifacts=[], evaluations=[], counts={'on_chain': 0}, snapshot={}, package={},
    )
    assert summary['on_chain']['state'] == f.STATE_NOT_RECORDED
    # …while the operational half still reports the mismatch the detection recorded.
    assert summary['operational']['state'] == f.STATE_ANOMALY


def test_case_summary_reports_no_response_state(monkeypatch):
    # Response authority is Screen 8's. Screen 7 reads its live state from the
    # response-actions endpoint rather than copying it into this snapshot.
    conn = _ForensicConn()
    _install(monkeypatch, conn)
    summary = f.get_incident_evidence(INCIDENT_ID, _request())['case_summary']
    assert 'response' not in summary


def test_case_summary_is_deterministic(monkeypatch):
    conn = _ForensicConn()
    _install(monkeypatch, conn)
    first = f.get_incident_evidence(INCIDENT_ID, _request())['case_summary']
    second = f.get_incident_evidence(INCIDENT_ID, _request())['case_summary']
    assert first == second


def test_the_current_reconciliation_verdict_wins_over_an_earlier_re_run():
    # The directory is ordered oldest-first, so the LAST event-linked row is the
    # verdict that stands. An earlier run must never outrank the one that followed
    # it — that would report a stale "reconciled" over a live mismatch.
    earlier = {
        'artifact_type': 'reconciliation_output', 'source': 'Transfer Agent',
        'collected_at': '2026-01-01T09:00:00+00:00',
        'metadata': {'status': 'RECONCILED', 'link_scope': f.LINK_SCOPE_EVENT},
    }
    later = {
        'artifact_type': 'reconciliation_output', 'source': 'Transfer Agent',
        'collected_at': '2026-01-01T10:42:18.188000+00:00',
        'metadata': {'status': 'UNEXPLAINED_VARIANCE', 'reason_code': 'AMOUNT_MISMATCH',
                     'link_scope': f.LINK_SCOPE_EVENT},
    }
    summary = f.build_case_summary(
        correlation={'event_id': TX_HASH, 'incident_id': INCIDENT_ID, 'detection': {}},
        artifacts=[earlier, later], evaluations=[], counts={}, snapshot={}, package={},
    )
    assert summary['operational']['state'] == f.STATE_ANOMALY
    assert summary['operational']['reconciliation_status'] == 'UNEXPLAINED_VARIANCE'


def test_the_reconciliation_classes_come_from_the_engine_that_writes_them():
    # Screen 7 must not keep a second copy of the verdict taxonomy.
    from services.api.app.domains.asset_integrity import reconciliation as recon

    assert f._RECON_ANOMALY_STATUSES is recon.ANOMALY_STATUSES
    assert f._RECON_INDETERMINATE_STATUSES is recon.INDETERMINATE_STATUSES
