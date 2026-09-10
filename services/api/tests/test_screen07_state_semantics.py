"""Screen 7 — backend state semantics the UI is not allowed to blur.

Companion to `apps/web/tests/incidents-screen07-state-consistency.spec.ts`. That
suite pins the words an operator reads; this one pins the facts underneath them,
because every contradiction the audit found was two DIFFERENT backend facts being
rendered under one name:

  * ``analysis['status']`` is the ANALYZER's run state, not the investigation's;
  * ``evidence_coverage`` counts SNAPSHOT dimensions, not forensic domains;
  * the snapshot's ``rule`` block is provenance, not a detection entity;
  * ``report_available`` tracks a persisted report, not a pressed button.

Follows the repo's pure-function / source-inspection unit style: no database, no
LLM, no network.
"""
from __future__ import annotations

import pathlib
import re

from services.api.app import forensic_investigation as fi
from services.api.app import incident_forensics as inf


REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]


def _source(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding='utf-8')


def _snapshot(**over):
    snap = {
        'schema_version': '1.0',
        'workspace_id': 'ws-1',
        'incident_id': 'inc-1',
        'alert': {'alert_id': 'alert-77', 'severity': 'high', 'created_at': '2026-07-11T00:00:00+00:00',
                  'rule_id': 'smoke_wallet_transfer'},
        'rule': {'rule_id': 'smoke_wallet_transfer', 'name': 'Smoke Wallet Transfer'},
        'target': {'target_id': 'tgt-1', 'asset_id': 'asset-1', 'chain_id': 8453},
        'telemetry': [{
            'telemetry_id': 'tel-1', 'event_type': 'wallet_transfer_detected',
            'tx_hash': '0xdead', 'block_number': 123, 'chain_id': 8453,
            'observed_at': '2026-07-11T00:00:00+00:00', 'evidence_source': 'live_provider',
        }],
        'provider_observations': [],
        'evidence_complete': True,
        'evidence_incomplete': False,
        'incomplete_reasons': [],
        'missing_evidence': [],
        'telemetry_ambiguous': False,
    }
    snap.update(over)
    return snap


# --------------------------------------------------------------------------
# 1. A finished analyzer run is one stage of seven
# --------------------------------------------------------------------------
def test_completed_analysis_still_leaves_three_stages_pending():
    """The exact state the audit screenshots showed.

    The analyzer completes over a full snapshot while the AI narrative has never
    been requested, no response action exists and no report was generated. Four
    stages are done and three are not — so ``analysis['status'] == 'completed'``
    is a claim about the ANALYZER, and any surface that renders it as the
    investigation's own verdict is asserting something the stage list denies.
    """
    analysis = fi.build_analysis(
        _snapshot(), triage_status=None, recommendation_count=0, report_generated=False,
    )
    assert analysis['status'] == 'completed'

    states = {stage['stage']: stage['state'] for stage in analysis['workflow_stages']}
    assert len(states) == 7
    completed = [key for key, state in states.items() if state == 'completed']
    assert sorted(completed) == ['correlation', 'detection', 'evidence_collection', 'triage']
    assert states['analysis'] == 'pending'
    assert states['recommendation'] == 'pending'
    assert states['report'] == 'pending'


def test_workflow_stage_order_is_the_canonical_seven():
    """One stage model, named once, in one order — the queue, the hero workflow
    card and the Workflow tab all count THIS list."""
    assert [key for key, _ in fi.WORKFLOW_STAGES] == [
        'detection', 'triage', 'evidence_collection', 'correlation',
        'analysis', 'recommendation', 'report',
    ]
    labels = [label for _, label in fi.WORKFLOW_STAGES]
    assert labels == ['Detection', 'Triage', 'Evidence Collection', 'Correlation',
                      'Analysis', 'Recommendation', 'Report Generated']


def test_agent_status_is_the_triage_job_not_the_analyzer():
    """The Analysis stage tracks the AI triage job. A completed analyzer run with
    no requested job leaves it pending — which is why the agent pill may not be
    derived from ``analysis['status']``."""
    for triage_status, expected in [
        (None, 'pending'), ('not_requested', 'pending'), ('queued', 'queued'),
        ('running', 'in_progress'), ('completed', 'completed'), ('failed', 'failed'),
        ('disabled', 'skipped'), ('budget_blocked', 'degraded'),
    ]:
        stages = fi.derive_workflow_stages(
            _snapshot(), triage_status=triage_status, recommendation_count=0, report_generated=False,
        )
        state = {s['stage']: s['state'] for s in stages}['analysis']
        assert state == expected, (triage_status, state)


# --------------------------------------------------------------------------
# 2. Coverage counts snapshot dimensions, not forensic domains
# --------------------------------------------------------------------------
def test_full_coverage_says_nothing_about_operational_state():
    """100% coverage means all eight SNAPSHOT dimensions are present.

    Operational state, policy and human actions are not among them, so a full
    score is entirely compatible with "Operational: not collected". Labelling this
    "Evidence coverage" is what made the two look contradictory.
    """
    coverage = fi.compute_coverage(_snapshot())
    assert coverage['coverage'] == 1.0
    assert coverage['present_count'] == coverage['expected_count'] == 8
    assert coverage['missing'] == []

    dimensions = {key for key, _ in fi.COVERAGE_DIMENSIONS}
    assert dimensions == {
        'source_alert', 'detection_rule', 'target_context', 'telemetry_event',
        'transaction_hash', 'block_number', 'timestamp', 'chain_id',
    }
    # The forensic DOMAINS are a different vocabulary entirely.
    assert not dimensions & set(inf.EVIDENCE_DOMAINS)
    assert 'operational' not in {d.lower() for d in dimensions}


def test_coverage_is_not_an_integrity_check():
    """Nothing in the coverage path hashes or verifies anything — so it must not
    be labelled "verified"."""
    coverage = fi.compute_coverage(_snapshot())
    assert set(coverage) == {'coverage', 'present_count', 'expected_count', 'dimensions', 'missing'}
    for dimension in coverage['dimensions']:
        assert set(dimension) == {'key', 'label', 'present'}


def test_confidence_band_boundaries_are_the_documented_ones():
    """The UI documents High >= 75% and Medium >= 40%; those numbers come from
    here and are never recomputed in the browser."""
    assert fi.confidence_band(0.75) == 'high'
    assert fi.confidence_band(0.7499) == 'medium'
    assert fi.confidence_band(0.40) == 'medium'
    assert fi.confidence_band(0.3999) == 'low'


# --------------------------------------------------------------------------
# 3. Domain counts reconcile, or the residual is visible
# --------------------------------------------------------------------------
def test_domain_counts_never_absorb_an_unclassified_artifact():
    """An artifact with no domain counts toward the total ONLY.

    The four numbers are therefore allowed to sum to less than the total, which is
    exactly why the UI must surface the residual rather than print four counts
    that silently fail to add up.
    """
    counts = inf.count_domains([
        {'domain': inf.ON_CHAIN}, {'domain': inf.ON_CHAIN},
        {'domain': inf.POLICY},
        {'domain': inf.HUMAN_ACTION},
        {'domain': None},
        {'domain': 'SOMETHING_NEW'},
    ])
    assert counts['total'] == 6
    assert counts['on_chain'] == 2
    assert counts['operational'] == 0
    assert counts['policy'] == 1
    assert counts['human_actions'] == 1
    classified = sum(counts[key] for key in inf.DOMAIN_COUNT_KEYS.values())
    assert classified == 4
    assert counts['total'] - classified == 2


# --------------------------------------------------------------------------
# 4. Originating rule is provenance, never a detection entity
# --------------------------------------------------------------------------
def test_originating_rule_reads_the_snapshot_and_never_invents_one():
    assert inf.originating_rule(None) is None
    assert inf.originating_rule({}) is None
    assert inf.originating_rule({'rule': {}}) is None
    assert inf.originating_rule({'rule': {'rule_id': None, 'name': 'Ghost'}}) is None
    assert inf.originating_rule({'rule': {'rule_id': 'smoke_wallet_transfer', 'name': 'Smoke Wallet Transfer'}}) == {
        'rule_id': 'smoke_wallet_transfer', 'rule_name': 'Smoke Wallet Transfer',
    }
    # A rule with no display name falls back to its own id — never to a placeholder.
    assert inf.originating_rule({'rule': {'rule_id': 'r-1'}}) == {'rule_id': 'r-1', 'rule_name': 'r-1'}


def test_case_summary_names_the_rule_without_creating_a_detection():
    """An alert-escalated incident: a real rule name, no detection record.

    The detection section must stay ``not_recorded`` and carry no detection id.
    Promoting the rule into that slot would fabricate a linked Screen 5 entity.
    """
    summary = inf.build_case_summary(
        correlation={'event_id': 'evt-1', 'incident_id': 'inc-1', 'alert_id': 'alert-77',
                     'detection_id': None, 'asset_id': None, 'detection': {}},
        artifacts=[], evaluations=[],
        counts={'on_chain': 0, 'operational': 0, 'policy': 0, 'human_actions': 0, 'total': 0},
        snapshot={'status': 'ready', 'hash_verified': True}, package={},
        incident={'id': 'inc-1', 'source_alert_id': 'alert-77'},
        snapshot_payload=_snapshot(),
    )
    detection = summary['detection']
    assert detection['detection_id'] is None
    assert detection['state'] == inf.STATE_NOT_RECORDED
    assert detection['collection_state'] == inf.collection_state(inf.STATE_NOT_RECORDED)
    assert detection['originating_rule'] == {
        'rule_id': 'smoke_wallet_transfer', 'rule_name': 'Smoke Wallet Transfer',
    }
    # The originating ALERT is a first-class linked record; the detection is not.
    assert summary['correlation']['alert_id'] == 'alert-77'
    assert summary['correlation']['detection_id'] is None


def test_case_summary_without_a_snapshot_reports_no_rule():
    """No snapshot means no rule — nothing is resolved a second time to fill it."""
    summary = inf.build_case_summary(
        correlation={'event_id': 'evt-1', 'incident_id': 'inc-1', 'alert_id': None,
                     'detection_id': None, 'asset_id': None, 'detection': {}},
        artifacts=[], evaluations=[],
        counts={'on_chain': 0, 'operational': 0, 'policy': 0, 'human_actions': 0, 'total': 0},
        snapshot={}, package={}, incident={'id': 'inc-1'},
    )
    assert summary['detection']['originating_rule'] is None


# --------------------------------------------------------------------------
# 5. Fail-closed policy forensics survive a missing policy record
# --------------------------------------------------------------------------
def test_fail_closed_denial_keeps_its_evaluation_identity():
    """A DENY reached because no policy governed the operation still carries the
    evaluation's own recorded identity — the historical verdict outlives the
    (absent) policy record, and no policy row is read to reconstruct it."""
    evaluation = {
        'decision': 'DENY', 'policy_id': None, 'policy_key': None, 'policy_version': None,
        'engine_version': 'policy-engine-2.4', 'evaluation_id': 'eval-1',
        'evaluated_at': '2026-07-11T00:05:00+00:00',
        'reason_codes': ['POLICY_NOT_FOUND', 'OPERATION_NOT_ESTABLISHED'],
        'required_approvals': [], 'simulation': False,
    }
    summary = inf.build_case_summary(
        correlation={'event_id': 'evt-1', 'incident_id': 'inc-1', 'alert_id': 'alert-77',
                     'detection_id': None, 'asset_id': None, 'detection': {}},
        artifacts=[], evaluations=[evaluation],
        counts={'on_chain': 0, 'operational': 0, 'policy': 1, 'human_actions': 0, 'total': 1},
        snapshot={'status': 'ready', 'hash_verified': True}, package={},
        incident={'id': 'inc-1'}, snapshot_payload=_snapshot(),
    )
    policy = summary['policy']
    assert policy['decision'] == 'DENY'
    assert policy['state'] == inf.STATE_DECIDED
    # No matched policy — and the absence does not erase the evaluation.
    assert policy['policy_id'] is None and policy['policy_key'] is None
    assert policy['evaluation_id'] == 'eval-1'
    assert policy['engine_version'] == 'policy-engine-2.4'
    assert policy['reason_codes'] == ['POLICY_NOT_FOUND', 'OPERATION_NOT_ESTABLISHED']
    assert policy['authority'] == 'deterministic_policy_engine'


def test_uncollected_operational_state_is_not_a_mismatch():
    """NOT COLLECTED and NOT MATCHED are opposite claims about a customer's books.
    With no reconciliation artifact, nothing was compared and no verdict is made."""
    summary = inf.build_case_summary(
        correlation={'event_id': 'evt-1', 'incident_id': 'inc-1', 'alert_id': 'alert-77',
                     'detection_id': None, 'asset_id': None, 'detection': {}},
        artifacts=[], evaluations=[],
        counts={'on_chain': 0, 'operational': 0, 'policy': 0, 'human_actions': 0, 'total': 0},
        snapshot={'status': 'ready', 'hash_verified': True}, package={},
        incident={'id': 'inc-1'}, snapshot_payload=_snapshot(),
    )
    operational = summary['operational']
    assert operational['state'] == inf.STATE_NOT_RECORDED
    assert operational['collection_state'] == 'not_collected'
    assert operational['reconciliation_status'] is None
    assert operational['reconciliation_scope'] is None


# --------------------------------------------------------------------------
# 6. Report / re-run authority boundary
# --------------------------------------------------------------------------
def _function_body(source: str, name: str, next_name: str) -> str:
    start = source.index(f'def {name}(')
    end = source.index(f'def {next_name}(', start)
    return source[start:end]


FORBIDDEN_MUTATIONS = (
    'DELETE FROM',
    'UPDATE incident_evidence_snapshots',
    'UPDATE incident_timeline',
    'UPDATE threat_detections',
    'DROP TABLE',
    'TRUNCATE',
)


def test_generate_report_cannot_mutate_deterministic_forensic_state():
    """Generating a report reads the snapshot, persists an analysis keyed by that
    snapshot's hash, and appends an audit + timeline record. It rewrites no
    evidence, no policy evaluation and no historical event, and it executes no
    response action."""
    body = _function_body(
        _source('services/api/app/forensic_investigation.py'), 'generate_report', 'build_report',
    )
    for statement in FORBIDDEN_MUTATIONS:
        assert statement not in body, statement
    assert 'append_incident_timeline_event' in body
    assert 'log_audit' in body
    # The only SQL it runs goes through the connection; it calls no execution,
    # approval or enforcement helper.
    calls = set(re.findall(r'\b([a-z_][a-z0-9_.]*)\(', body))
    for call in calls:
        assert not call.startswith('execute'), call
    for forbidden in ('approve', 'enforce', 'dispatch_action', 'run_action'):
        assert not any(forbidden in call for call in calls), forbidden


def test_generate_report_does_not_mark_the_report_stage_complete():
    """``_report_generated`` is keyed on a COMPLETED AI triage job, not on the
    button. A deterministic report body is a real result that leaves the Report
    Generated stage pending — and the UI says exactly that."""
    source = _source('services/api/app/forensic_investigation.py')
    body = _function_body(source, '_report_generated', '_incident_header')
    assert "status IN ('completed', 'completed_with_warnings')" in body
    # The docstring is wrapped in the source, so compare on normalized whitespace.
    assert 'Deterministic completion alone does not claim a report' in ' '.join(body.split())
    # With no triage job, the stage stays pending regardless of the analyzer.
    stages = fi.derive_workflow_stages(
        _snapshot(), triage_status=None, recommendation_count=0, report_generated=False,
    )
    assert {s['stage']: s['state'] for s in stages}['report'] == 'pending'


def test_rerun_investigation_appends_rather_than_rewrites():
    """A re-run builds a NEW snapshot and appends a new timeline event. Prior
    snapshots, artifacts and events are untouched, and the AI hand-off only queues
    an analysis job."""
    body = _function_body(
        _source('services/api/app/forensic_investigation.py'), 'rerun_investigation', 'generate_report',
    )
    for statement in FORBIDDEN_MUTATIONS:
        assert statement not in body, statement
    assert '_build_and_store_snapshot' in body
    assert 'append_incident_timeline_event' in body
    assert 'it only queues an analysis job' in body


def test_deterministic_analysis_is_reproducible_across_reruns():
    """Identical snapshot in, identical analysis out — so a re-run over unchanged
    evidence cannot silently move a confidence score or a stage state."""
    first = fi.build_analysis(_snapshot(), triage_status=None, recommendation_count=0, report_generated=False)
    second = fi.build_analysis(_snapshot(), triage_status=None, recommendation_count=0, report_generated=False)
    assert first == second


# --------------------------------------------------------------------------
# 7. Workspace scoping is unchanged by this pass
# --------------------------------------------------------------------------
def test_evidence_reads_stay_workspace_scoped():
    """The audit added no query. Every read in the evidence path still carries a
    workspace predicate."""
    source = _source('services/api/app/incident_forensics.py')
    for statement in re.findall(r"'''SELECT.*?'''", source, flags=re.DOTALL):
        assert 'workspace_id' in statement or 'FROM targets' in statement or 'FROM assets' in statement, statement


def test_originating_rule_opens_no_cursor():
    """It folds a payload this request already read — no second query, so the two
    surfaces can never resolve a different rule."""
    source = _source('services/api/app/incident_forensics.py')
    body = _function_body(source, 'originating_rule', 'build_case_summary')
    assert 'execute' not in body
    assert 'SELECT' not in body
