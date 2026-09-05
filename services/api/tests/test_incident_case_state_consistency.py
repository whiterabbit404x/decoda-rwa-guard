"""Screen 7 — case-state consistency audit.

Pins the state COMBINATIONS an operator can actually be shown, and the semantic
separations that make them readable. Every test here exists because the
combination it covers was observed on a real case file and could not be explained
from the UI alone:

  * "No linked detection" beside an observed on-chain event;
  * "Policy Not Found" beside an authoritative DENY;
  * "Operational: Not collected" beside a double-digit evidence count;
  * an approval count whose unit was not stated;
  * a completion denominator with no visible stage model.

The invariants (see CLAUDE.md):
  * NOT COLLECTED is never NOT MATCHED. No data must not be shown as a failed
    comparison, and a failed comparison must not be shown as missing data.
  * NO POLICY EVALUATION is never DENY. Only a deterministic mechanism that
    actually ran may produce a decision.
  * A fail-closed DENY names its source and its reason; an authoritative decision
    is never left unexplained.
  * Historical forensic truth survives the current configuration: the policy
    identity comes off the persisted evaluation, never off the policy table.
  * Nothing is fabricated to fill a gap — not a detection, not a timestamp, not a
    lifecycle stage.
"""
from __future__ import annotations

import uuid

from services.api.app import forensic_investigation as fi
from services.api.app import incident_forensics as f
from services.api.app.domains.governance_policy import config as gpc
from services.api.app.domains.governance_policy import engine as policy_engine
from services.api.app.domains.governance_policy import schemas as policy_schemas


INCIDENT_ID = str(uuid.uuid4())
DETECTION_ID = str(uuid.uuid4())
ALERT_ID = str(uuid.uuid4())
ASSET_ID = str(uuid.uuid4())
EVALUATION_ID = str(uuid.uuid4())
TX_HASH = '0xfeed01'


def _correlation(**overrides):
    base = {
        'event_id': TX_HASH,
        'incident_id': INCIDENT_ID,
        'alert_id': None,
        'detection_id': None,
        'asset_id': ASSET_ID,
        'detection': {},
    }
    base.update(overrides)
    return base


def _counts(**overrides):
    base = {'on_chain': 0, 'operational': 0, 'policy': 0, 'human_actions': 0, 'total': 0}
    base.update(overrides)
    base['total'] = sum(v for k, v in base.items() if k != 'total')
    return base


def _summary(*, correlation=None, artifacts=None, evaluations=None, counts=None,
             snapshot=None, package=None, incident=None):
    return f.build_case_summary(
        correlation=correlation or _correlation(),
        artifacts=artifacts or [],
        evaluations=evaluations or [],
        counts=counts or _counts(),
        snapshot=snapshot or {},
        package=package or {},
        incident=incident or {},
    )


def _telemetry_artifact(*, tx_hash=TX_HASH, block='918273', collected_at='2026-02-02T10:00:00+00:00'):
    """A chain-side directory row as `_onchain_artifacts` builds one."""
    return {
        'id': f'telemetry:{uuid.uuid4()}',
        'domain': f.ON_CHAIN,
        'artifact_type': 'telemetry_event',
        'source': 'quicknode',
        'collected_at': collected_at,
        'metadata': {'tx_hash': tx_hash, 'block_number': block,
                     'event_type': 'mint', 'link_scope': f.LINK_SCOPE_INCIDENT},
    }


# ==========================================================================
# 1. No linked detection + an observed on-chain event
# ==========================================================================
def test_an_incident_without_a_detection_can_still_have_a_chain_observation():
    # This combination is LEGITIMATE, not a broken join: the evidence snapshot's
    # telemetry is collected against the incident, so an alert-escalated case has
    # chain evidence without ever having had a Screen 5 detection.
    summary = _summary(
        correlation=_correlation(alert_id=ALERT_ID),
        artifacts=[_telemetry_artifact()],
        counts=_counts(on_chain=1),
        incident={'event_type': 'alert_escalation'},
    )
    assert summary['detection']['state'] == f.STATE_NOT_RECORDED
    assert summary['detection']['detection_id'] is None
    assert summary['on_chain']['state'] == f.STATE_OBSERVED


def test_an_observed_chain_event_without_a_detection_still_carries_its_identity():
    # "Observed" with no transaction, block or time is a claim an operator cannot
    # check. The persisted telemetry artifact supplies the identity, and the
    # summary says WHICH record it came from.
    summary = _summary(
        correlation=_correlation(alert_id=ALERT_ID),
        artifacts=[_telemetry_artifact()],
        counts=_counts(on_chain=1),
        incident={'event_type': 'alert_escalation'},
    )
    on_chain = summary['on_chain']
    assert on_chain['tx_hash'] == TX_HASH
    assert on_chain['block_number'] == '918273'
    assert on_chain['observed_at'] == '2026-02-02T10:00:00+00:00'
    assert on_chain['fact_source'] == 'evidence_snapshot'


def test_no_detection_is_ever_fabricated_to_fill_the_section():
    # With neither a detection nor chain artifacts, both sections report absence.
    # Nothing is borrowed from the incident row to manufacture a detection.
    summary = _summary(incident={'event_type': 'incident'})
    assert summary['detection']['detection_id'] is None
    assert summary['detection']['category'] is None
    assert summary['detection']['title'] is None
    assert summary['on_chain']['state'] == f.STATE_NOT_RECORDED
    assert summary['on_chain']['tx_hash'] is None
    assert summary['on_chain']['fact_source'] is None


def test_incident_origin_explains_a_missing_detection_from_persisted_linkage():
    detection_origin = f.incident_origin(
        incident={'event_type': 'unmatched_issuance'},
        correlation=_correlation(detection_id=DETECTION_ID, alert_id=ALERT_ID),
    )
    assert detection_origin['origin'] == f.ORIGIN_DETECTION
    assert detection_origin['detection_linked'] is True

    alert_origin = f.incident_origin(
        incident={'event_type': 'alert_escalation'},
        correlation=_correlation(alert_id=ALERT_ID),
    )
    assert alert_origin['origin'] == f.ORIGIN_ALERT
    assert alert_origin['detection_linked'] is False

    manual_origin = f.incident_origin(
        incident={'event_type': 'incident'}, correlation=_correlation(),
    )
    assert manual_origin['origin'] == f.ORIGIN_MANUAL

    system_origin = f.incident_origin(
        incident={'event_type': 'incident.monitoring_proof_chain'}, correlation=_correlation(),
    )
    assert system_origin['origin'] == f.ORIGIN_SYSTEM


def test_an_unrecorded_origin_is_unknown_not_assumed_manual():
    # Defaulting to "manual" would assert that a person opened the case. Nothing
    # in the record says that, so nothing claims it.
    origin = f.incident_origin(incident={}, correlation=_correlation())
    assert origin['origin'] == f.ORIGIN_UNKNOWN


# ==========================================================================
# 2. "Policy Not Found" + DENY
# ==========================================================================
def test_policy_not_found_deny_is_the_engines_own_fail_closed_terminal():
    # Where the DENY actually comes from: `evaluate_policy` with no policy takes
    # its step-1 terminal branch. It is a deterministic refusal, and the row it
    # writes carries NO policy identity — which is why the UI must not word it as
    # a failed lookup of a policy record.
    context = policy_engine.EvaluationContext(
        operation='mint', asset_id=ASSET_ID, incident_id=INCIDENT_ID,
        canonical_event_id=TX_HASH,
    )
    decision = policy_engine.evaluate_policy(None, context, evaluation_id=EVALUATION_ID)
    assert decision.decision == gpc.DECISION_DENY
    assert decision.reason_codes == (gpc.POLICY_NOT_FOUND,)
    assert decision.policy_id is None
    assert decision.policy_key is None
    assert decision.policy_version is None
    assert decision.checks[0].status == policy_schemas.FAIL


def test_a_fail_closed_deny_is_labelled_by_its_source_not_by_a_missing_policy():
    evaluation = {
        'evaluation_id': EVALUATION_ID, 'decision': 'DENY',
        'policy_id': None, 'policy_key': None, 'policy_version': None,
        'reason_codes': [gpc.POLICY_NOT_FOUND, gpc.OPERATION_NOT_ESTABLISHED],
        'evaluated_at': '2026-02-02T10:00:01+00:00',
    }
    summary = _summary(evaluations=[evaluation], counts=_counts(policy=1))
    policy = summary['policy']
    assert policy['decision'] == 'DENY'
    assert policy['decision_source'] == f.DECISION_SOURCE_FAIL_CLOSED
    # The reason codes travel with it, so the refusal is explainable end to end.
    assert policy['reason_codes'] == [gpc.POLICY_NOT_FOUND, gpc.OPERATION_NOT_ESTABLISHED]
    # No policy identity is invented to fill the gap.
    assert policy['policy_key'] is None
    assert policy['policy_id'] is None


def test_a_matched_policy_deny_is_attributed_to_that_policy():
    evaluation = {
        'evaluation_id': EVALUATION_ID, 'decision': 'DENY',
        'policy_id': str(uuid.uuid4()), 'policy_key': 'issuance-authorization',
        'policy_version': 3, 'reason_codes': ['COMPLIANCE_APPROVAL_MISSING'],
        'engine_version': 'policy-v1', 'evaluated_at': '2026-02-02T10:00:01+00:00',
    }
    summary = _summary(evaluations=[evaluation], counts=_counts(policy=1))
    policy = summary['policy']
    assert policy['decision_source'] == f.DECISION_SOURCE_POLICY
    assert policy['policy_key'] == 'issuance-authorization'
    assert policy['policy_version'] == 3


def test_no_policy_evaluation_never_becomes_a_deny():
    # NO POLICY EVALUATION and DENY are different claims. With no evaluation the
    # section reports absence and carries no decision at all.
    summary = _summary()
    policy = summary['policy']
    assert policy['state'] == f.STATE_NOT_RECORDED
    assert policy['decision'] is None
    assert policy['decision_source'] == f.DECISION_SOURCE_NONE
    assert policy['evaluation_count'] == 0


def test_a_simulation_never_supplies_the_enforcement_decision():
    # A Screen 11 what-if predicts; it never authorized anything. A case whose only
    # evaluation is a simulation reports NO enforcement decision.
    simulated = {
        'evaluation_id': EVALUATION_ID, 'decision': 'DENY', 'simulation': True,
        'policy_key': 'issuance-authorization', 'policy_version': 3,
        'reason_codes': ['COMPLIANCE_APPROVAL_MISSING'],
    }
    summary = _summary(evaluations=[simulated], counts=_counts(policy=1))
    assert summary['policy']['state'] == f.STATE_NOT_RECORDED
    assert summary['policy']['decision'] is None
    assert summary['policy']['evaluation_count'] == 0


def test_an_unattributed_decision_is_not_called_fail_closed():
    # The fail-closed branch only ever produces DENY. An ALLOW with no policy
    # identity is a record whose source cannot be established, and it says so
    # rather than borrowing a mechanism that did not run.
    evaluation = {'evaluation_id': EVALUATION_ID, 'decision': 'ALLOW',
                  'policy_id': None, 'policy_key': None, 'reason_codes': []}
    summary = _summary(evaluations=[evaluation], counts=_counts(policy=1))
    assert summary['policy']['decision_source'] == f.DECISION_SOURCE_UNATTRIBUTED


# ==========================================================================
# 3. Historical forensic truth survives the current policy record
# ==========================================================================
def test_the_policy_identity_comes_off_the_evaluation_not_the_policy_table():
    # The evaluation row carries key, version and engine version. The summary is a
    # pure fold over it: no policy table is consulted, so an edited, archived or
    # DELETED policy cannot change what this incident proves.
    evaluation = {
        'evaluation_id': EVALUATION_ID, 'decision': 'DENY',
        'policy_id': str(uuid.uuid4()), 'policy_key': 'issuance-authorization',
        'policy_version': 7, 'engine_version': 'policy-v1',
        'reason_codes': ['DAILY_LIMIT_EXCEEDED'],
        'evaluated_at': '2026-02-02T10:00:01+00:00',
    }
    summary = _summary(evaluations=[evaluation], counts=_counts(policy=1))
    policy = summary['policy']
    for field, expected in [
        ('policy_key', 'issuance-authorization'), ('policy_version', 7),
        ('engine_version', 'policy-v1'), ('evaluation_id', EVALUATION_ID),
        ('evaluated_at', '2026-02-02T10:00:01+00:00'),
        ('reason_codes', ['DAILY_LIMIT_EXCEEDED']),
    ]:
        assert policy[field] == expected


def test_the_case_summary_reads_no_policy_table_at_all():
    # Structural guard on the rule above: the fold takes no connection, so it
    # CANNOT resolve a policy by foreign key at render time.
    import inspect
    parameters = inspect.signature(f.build_case_summary).parameters
    assert 'connection' not in parameters
    source = inspect.getsource(f.build_case_summary)
    assert 'governance_policies' not in source


def test_the_persisted_evaluation_columns_cover_the_forensic_record():
    # The evaluation read names every field a historical decision must prove on its
    # own. If one is dropped, this fails rather than the UI silently degrading to a
    # lookup against the mutable policy row.
    import inspect
    source = inspect.getsource(f._policy_evaluations)
    for column in ('policy_id', 'policy_key', 'policy_version', 'decision',
                   'reason_codes', 'checks', 'engine_version', 'evaluated_at'):
        assert column in source


# ==========================================================================
# 4. Operational: NOT COLLECTED is not NOT MATCHED
# ==========================================================================
def test_not_collected_and_not_matched_are_different_states():
    absent = _summary()['operational']
    assert absent['state'] == f.STATE_NOT_RECORDED
    assert absent['collection_state'] == f.COLLECTION_NOT_COLLECTED

    reconciliation = {
        'id': 'reconciliation:1', 'domain': f.OPERATIONAL,
        'artifact_type': 'reconciliation_output', 'source': 'Transfer Agent',
        'collected_at': '2026-02-02T10:00:00+00:00',
        'metadata': {'status': 'UNEXPLAINED_VARIANCE', 'link_scope': f.LINK_SCOPE_EVENT},
    }
    mismatched = _summary(artifacts=[reconciliation], counts=_counts(operational=1))['operational']
    assert mismatched['state'] == f.STATE_ANOMALY
    assert mismatched['collection_state'] == f.COLLECTION_COLLECTED


def test_could_not_establish_truth_counts_as_collected_but_not_as_reconciled():
    # The engine ran and could not decide. That is a different fact from never
    # having looked, and it is never reported as agreement.
    indeterminate_status = sorted(f._RECON_INDETERMINATE_STATUSES)[0]
    row = {
        'id': 'reconciliation:2', 'domain': f.OPERATIONAL,
        'artifact_type': 'reconciliation_output', 'source': 'Transfer Agent',
        'collected_at': '2026-02-02T10:00:00+00:00',
        'metadata': {'status': indeterminate_status, 'link_scope': f.LINK_SCOPE_EVENT},
    }
    operational = _summary(artifacts=[row], counts=_counts(operational=1))['operational']
    assert operational['state'] == f.STATE_INDETERMINATE
    assert operational['collection_state'] == f.COLLECTION_COLLECTED
    assert operational['state'] != f.STATE_RECONCILED


def test_collection_state_is_computed_from_the_verdict_states_not_guessed():
    assert f.collection_state(f.STATE_NOT_RECORDED) == f.COLLECTION_NOT_COLLECTED
    for state in (f.STATE_OBSERVED, f.STATE_ANOMALY, f.STATE_RECONCILED,
                  f.STATE_DECIDED, f.STATE_INDETERMINATE):
        assert f.collection_state(state) == f.COLLECTION_COLLECTED
    assert f.collection_state(None) == f.COLLECTION_NOT_COLLECTED


# ==========================================================================
# 5. Evidence counts are grouped by domain
# ==========================================================================
def test_an_evidence_total_never_implies_operational_evidence_was_collected():
    # The observed combination: a double-digit artifact total beside "Operational:
    # Not collected". Both are true — the artifacts belong to the other three
    # domains — and the payload carries the split so the UI can say so.
    counts = _counts(on_chain=4, operational=0, policy=3, human_actions=6)
    summary = _summary(artifacts=[_telemetry_artifact()], counts=counts)
    assert summary['evidence']['artifact_count'] == 13
    assert summary['evidence']['counts']['operational'] == 0
    assert summary['operational']['collection_state'] == f.COLLECTION_NOT_COLLECTED
    assert summary['operational']['artifact_count'] == 0


def test_asset_scoped_operational_artifacts_do_not_become_this_events_verdict():
    # Operational artifacts CAN exist while nothing was reconciled against THIS
    # event. The count is reported; the verdict is not.
    asset_scoped = {
        'id': 'reconciliation:3', 'domain': f.OPERATIONAL,
        'artifact_type': 'reconciliation_output', 'source': 'Transfer Agent',
        'collected_at': '2026-02-02T10:00:00+00:00',
        'metadata': {'status': 'UNEXPLAINED_VARIANCE', 'link_scope': f.LINK_SCOPE_ASSET},
    }
    summary = _summary(artifacts=[asset_scoped], counts=_counts(operational=2))
    operational = summary['operational']
    assert operational['state'] == f.STATE_NOT_RECORDED
    assert operational['collection_state'] == f.COLLECTION_NOT_COLLECTED
    # The count is still surfaced, with the scope that explains it.
    assert operational['artifact_count'] == 2
    assert operational['reconciliation_scope'] == 'ASSET'


def test_domain_counts_are_derived_from_the_artifacts_themselves():
    artifacts = [
        _telemetry_artifact(),
        {'domain': f.POLICY, 'artifact_type': 'policy_decision'},
        {'domain': f.HUMAN_ACTION, 'artifact_type': 'approval_record'},
    ]
    counts = f.count_domains(artifacts)
    assert counts == {'on_chain': 1, 'operational': 0, 'policy': 1,
                      'human_actions': 1, 'total': 3}


# ==========================================================================
# 6. The investigation stage model behind the completion denominator
# ==========================================================================
def test_the_seven_investigation_stages_are_an_explicit_ordered_model():
    assert [key for key, _ in fi.WORKFLOW_STAGES] == [
        'detection', 'triage', 'evidence_collection', 'correlation',
        'analysis', 'recommendation', 'report',
    ]
    assert len(fi.WORKFLOW_STAGES) == 7


def test_every_stage_state_is_derived_from_a_persisted_fact():
    # No stage may be "completed" without a record behind it. With an empty
    # snapshot, no triage job, no recommendation and no report, every stage is
    # pending — the denominator is 7 and the numerator is 0.
    stages = fi.derive_workflow_stages(
        {}, triage_status=None, recommendation_count=0, report_generated=False,
    )
    assert len(stages) == 7
    assert {s['state'] for s in stages} == {'pending'}


def test_stage_completion_tracks_the_records_that_prove_it():
    snapshot = {'alert': {'alert_id': ALERT_ID}, 'telemetry': [{'tx_hash': TX_HASH}]}
    stages = {s['stage']: s['state'] for s in fi.derive_workflow_stages(
        snapshot, triage_status='completed', recommendation_count=2, report_generated=True,
    )}
    assert stages == {
        'detection': 'completed', 'triage': 'completed',
        'evidence_collection': 'completed', 'correlation': 'completed',
        'analysis': 'completed', 'recommendation': 'completed', 'report': 'completed',
    }


def test_incomplete_evidence_is_degraded_not_completed():
    snapshot = {'alert': {'alert_id': ALERT_ID}, 'telemetry': [{'tx_hash': TX_HASH}],
                'evidence_incomplete': True}
    stages = {s['stage']: s['state'] for s in fi.derive_workflow_stages(
        snapshot, triage_status=None, recommendation_count=0, report_generated=False,
    )}
    assert stages['evidence_collection'] == 'degraded'
    assert stages['analysis'] == 'pending'
    assert stages['report'] == 'pending'


# ==========================================================================
# 7. The timeline contains only persisted events
# ==========================================================================
def test_a_case_with_no_records_produces_no_timeline_events():
    events = f.build_forensic_timeline(
        incident_id=INCIDENT_ID, correlation=_correlation(), timeline_rows=[],
        reconciliations=[], evaluations=[], snapshot_row={}, package={},
    )
    assert events == []


def _detection_timeline(**detection_overrides):
    detection = {
        'id': DETECTION_ID, 'telemetry_observed_at': '2026-02-02T10:00:00.001+00:00',
        'telemetry_source': 'quicknode', 'tx_hash': TX_HASH,
        'detected_at': '2026-02-02T10:00:00.059+00:00',
    }
    detection.update(detection_overrides)
    return f.build_forensic_timeline(
        incident_id=INCIDENT_ID,
        correlation=_correlation(detection_id=DETECTION_ID, detection=detection),
        timeline_rows=[], reconciliations=[], evaluations=[], snapshot_row={}, package={},
    )


def test_every_placed_timeline_event_carries_a_canonical_timestamp():
    # A missing lifecycle stage stays missing. Nothing is back-filled, and no
    # timestamp is estimated to make the chronology look complete.
    events = _detection_timeline()
    assert events, 'a detection with an observation must produce at least one event'
    dated, undated = f.datable_timeline_events(events)
    assert undated == 0
    for event in dated:
        assert event['occurred_at'], event['event_type']
    # Only the stages with a record appear — no policy or response event is invented.
    assert all(event['stage'] != 'policy_evaluated' for event in events)


def test_an_undated_record_is_withheld_from_the_chronology_and_counted():
    # A persisted row whose timestamp column is NULL cannot be placed in time. It is
    # withheld rather than parked at the end of the order, and it is COUNTED — a
    # filtered timeline is never presented as the complete record.
    events = _detection_timeline(detected_at=None)
    dated, undated = f.datable_timeline_events(events)
    assert undated == 1
    assert all(event['occurred_at'] for event in dated)
    assert all(event['event_type'] != 'detection.recorded' for event in dated)


def test_timeline_events_are_ordered_by_canonical_timestamp_not_array_position():
    events = _detection_timeline()
    times = [event['occurred_at'] for event in events]
    assert times == sorted(times)
