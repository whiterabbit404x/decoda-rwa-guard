"""Tests for the deterministic Digital Forensics Investigator layer (Screen 7).

Follows the repo's fake-connection / pure-function unit style. The deterministic
analyzer is exercised without a database or an LLM: identical snapshot input must
produce identical, evidence-grounded output. The few DB-facing helpers (reference
numbering) use a small fake connection.
"""
from __future__ import annotations

from services.api.app import forensic_investigation as fi


# --------------------------------------------------------------------------
# Snapshot fixtures (shape mirrors ai_triage.build_evidence_snapshot output)
# --------------------------------------------------------------------------
def _rich_snapshot(**over):
    snap = {
        'schema_version': '1.0',
        'workspace_id': 'ws-1',
        'incident_id': 'inc-1',
        'alert': {'alert_id': 'alert-1', 'severity': 'critical', 'created_at': '2026-07-11T00:00:00+00:00', 'rule_id': 'wallet_transfer'},
        'rule': {'rule_id': 'wallet_transfer', 'name': 'Wallet transfer', 'description': 'Monitored wallet transfer detected.', 'version': '1'},
        'target': {'target_id': 'tgt-1', 'asset_id': 'asset-1', 'chain_id': 8453, 'address': '0xtarget', 'asset_type': 'wallet'},
        'telemetry': [{
            'telemetry_id': 'tel-1', 'event_type': 'wallet_transfer_detected', 'detected_by': 'quicknode_stream',
            'tx_hash': '0xdead', 'from': '0xtarget', 'to': '0xto', 'value': '100', 'block_number': 123,
            'chain_id': 8453, 'observed_at': '2026-07-11T00:00:00+00:00', 'ingested_at': '2026-07-11T00:00:01+00:00',
            'evidence_source': 'live_provider',
        }],
        'provider_observations': [],
        'policies': [{'policy_version': '1.0'}],
        'available_runbooks': [],
        'evidence_complete': True,
        'evidence_incomplete': False,
        'incomplete_reasons': [],
        'missing_evidence': [],
        'telemetry_ambiguous': False,
    }
    snap.update(over)
    return snap


def _empty_snapshot(**over):
    snap = {
        'schema_version': '1.0', 'workspace_id': 'ws-1', 'incident_id': 'inc-2',
        'alert': {'alert_id': None}, 'rule': {}, 'target': {}, 'telemetry': [],
        'provider_observations': [],
        'evidence_complete': False, 'evidence_incomplete': True,
        'incomplete_reasons': ['no telemetry evidence linked to the incident alert'],
        'missing_evidence': [{'code': 'telemetry_unresolved', 'description': 'No canonical telemetry resolved.', 'required_for': 'factual_transfer_findings'}],
        'telemetry_ambiguous': False,
    }
    snap.update(over)
    return snap


# --------------------------------------------------------------------------
# Findings: derived only from the snapshot; every ref is grounded
# --------------------------------------------------------------------------
def test_findings_are_verified_facts_or_gaps():
    findings = fi.derive_findings(_rich_snapshot())
    assert findings, 'rich snapshot should yield findings'
    for f in findings:
        assert f['verification_state'] in fi.VERIFICATION_STATES
    # Deterministic derivations are verified facts (gaps are also verified absences).
    non_gap = [f for f in findings if f['finding_type'] != 'evidence_gap']
    assert all(f['verification_state'] == fi.VERIFIED_FACT for f in non_gap)


def test_findings_cite_only_snapshot_references():
    """Correlation uses only related records: every cited ref must exist in the
    snapshot's valid reference set (no invented / cross-workspace evidence IDs)."""
    snap = _rich_snapshot()
    valid = fi.valid_reference_set(snap)
    findings = fi.derive_findings(snap)
    cited = {ref for f in findings for ref in f['evidence_refs']}
    assert cited, 'expected at least one grounded citation'
    assert cited.issubset(valid), f'ungrounded refs: {cited - valid}'


def test_findings_never_cite_foreign_workspace_ids():
    """A telemetry id that is NOT part of this snapshot can never appear in findings."""
    snap = _rich_snapshot()
    findings = fi.derive_findings(snap)
    cited = {ref for f in findings for ref in f['evidence_refs']}
    assert 'telemetry:foreign-ws-999' not in cited
    assert all(not ref.endswith('foreign-ws-999') for ref in cited)


def test_missing_evidence_becomes_explicit_gap_not_safe():
    findings = fi.derive_findings(_empty_snapshot())
    gaps = [f for f in findings if f['finding_type'] == 'evidence_gap']
    assert gaps, 'a missing-evidence snapshot must surface an explicit gap'
    assert 'telemetry' in gaps[0]['description'].lower()


def test_distinct_transaction_and_address_counts_are_factual():
    snap = _rich_snapshot(telemetry=[
        {'telemetry_id': 't1', 'tx_hash': '0xAAA', 'from': '0x1', 'to': '0x2', 'block_number': 10, 'observed_at': '2026-07-11T00:00:00+00:00', 'event_type': 'wallet_transfer_detected'},
        {'telemetry_id': 't2', 'tx_hash': '0xBBB', 'from': '0x2', 'to': '0x3', 'block_number': 11, 'observed_at': '2026-07-11T00:05:00+00:00', 'event_type': 'wallet_transfer_detected'},
    ])
    findings = {f['finding_type']: f for f in fi.derive_findings(snap)}
    assert findings['distinct_transactions']['value'] == 2
    assert findings['distinct_addresses']['value'] == 3


# --------------------------------------------------------------------------
# Rule matching: versioned, evidence-linked, "potential" only
# --------------------------------------------------------------------------
def test_rule_matches_are_potential_and_linked_to_evidence():
    matches = fi.match_rules(_rich_snapshot())
    assert matches, 'a monitored-target transfer should match at least one rule'
    for m in matches:
        assert m['framework'] == fi.FORENSIC_RULESET_NAME
        assert m['framework_version'] == fi.FORENSIC_RULESET_VERSION
        assert m['match_state'] == 'potential'
        assert m['verification_state'] == fi.RULE_MATCH
    ids = {m['rule_id'] for m in matches}
    # The transfer touches the monitored target address.
    assert 'RWA-FR-001' in ids


def test_ambiguous_transfer_rule_matches_on_multiple_tx():
    snap = _rich_snapshot(
        telemetry=[
            {'telemetry_id': 't1', 'tx_hash': '0xAAA', 'from': '0x1', 'to': '0x2', 'observed_at': '2026-07-11T00:00:00+00:00'},
            {'telemetry_id': 't2', 'tx_hash': '0xBBB', 'from': '0x1', 'to': '0x3', 'observed_at': '2026-07-11T00:00:00+00:00'},
        ],
        telemetry_ambiguous=True,
    )
    ids = {m['rule_id'] for m in fi.match_rules(snap)}
    assert 'RWA-FR-003' in ids


def test_unresolved_telemetry_rule_matches_when_empty():
    ids = {m['rule_id'] for m in fi.match_rules(_empty_snapshot())}
    assert 'RWA-FR-004' in ids


def test_large_transfer_rule_only_on_real_value():
    snap = fi.match_rules(_rich_snapshot(telemetry=[
        {'telemetry_id': 't1', 'tx_hash': '0xAAA', 'from': '0xtarget', 'to': '0x2', 'value': '500000', 'observed_at': '2026-07-11T00:00:00+00:00'},
    ]))
    assert any(m['rule_id'] == 'RWA-FR-002' for m in snap)
    # A tiny value must NOT fabricate a large-transfer match.
    small = fi.match_rules(_rich_snapshot(telemetry=[
        {'telemetry_id': 't1', 'tx_hash': '0xAAA', 'from': '0xtarget', 'to': '0x2', 'value': '1', 'observed_at': '2026-07-11T00:00:00+00:00'},
    ]))
    assert not any(m['rule_id'] == 'RWA-FR-002' for m in small)


# --------------------------------------------------------------------------
# Confidence + coverage: deterministic, clamped, factor-explained
# --------------------------------------------------------------------------
def test_confidence_high_for_grounded_snapshot():
    result = fi.compute_confidence(_rich_snapshot())
    assert 0.0 <= result['confidence'] <= 1.0
    assert result['band'] == 'high'
    assert result['calc_version'] == fi.CALC_VERSION
    assert any(f['factor'] == 'Canonical transaction hash' for f in result['factors'])


def test_confidence_low_and_clamped_for_empty_snapshot():
    result = fi.compute_confidence(_empty_snapshot())
    assert 0.0 <= result['confidence'] <= 1.0
    assert result['band'] == 'low'
    assert any(f['effect'] == 'decrease' for f in result['factors'])


def test_confidence_missing_evidence_lowers_score():
    with_gap = fi.compute_confidence(_empty_snapshot())['confidence']
    grounded = fi.compute_confidence(_rich_snapshot())['confidence']
    assert grounded > with_gap


def test_confidence_never_exceeds_one():
    snap = _rich_snapshot(
        provider_observations=[{'p': 1}, {'p': 2}, {'p': 3}],
        alert={'alert_id': 'alert-1', 'severity': 'critical', 'created_at': 'x', 'rule_id': 'r', 'confidence': 1.0},
    )
    assert fi.compute_confidence(snap)['confidence'] <= 1.0


def test_confidence_conflicting_evidence_penalized():
    snap = _rich_snapshot(
        telemetry=[
            {'telemetry_id': 't1', 'tx_hash': '0xAAA', 'from': '0x1', 'to': '0x2', 'block_number': 1, 'observed_at': 'a'},
            {'telemetry_id': 't2', 'tx_hash': '0xBBB', 'from': '0x1', 'to': '0x3', 'block_number': 2, 'observed_at': 'b'},
        ],
        telemetry_ambiguous=True,
    )
    factors = fi.compute_confidence(snap)['factors']
    assert any(f['factor'] == 'Conflicting evidence' for f in factors)


def test_coverage_is_fraction_of_present_dimensions():
    full = fi.compute_coverage(_rich_snapshot())
    assert 0.0 <= full['coverage'] <= 1.0
    assert full['present_count'] == full['expected_count']  # rich snapshot has every dimension
    empty = fi.compute_coverage(_empty_snapshot())
    assert empty['coverage'] < full['coverage']
    assert 'Correlated telemetry event' in empty['missing']


def test_confidence_band_boundaries():
    assert fi.confidence_band(0.75) == 'high'
    assert fi.confidence_band(0.74) == 'medium'
    assert fi.confidence_band(0.40) == 'medium'
    assert fi.confidence_band(0.39) == 'low'
    assert fi.confidence_band(-5) == 'low'
    assert fi.confidence_band(5) == 'high'


# --------------------------------------------------------------------------
# Workflow stages: state from persisted facts
# --------------------------------------------------------------------------
def test_workflow_stages_reflect_persisted_facts():
    stages = {s['stage']: s['state'] for s in fi.derive_workflow_stages(
        _rich_snapshot(), triage_status='completed', recommendation_count=2, report_generated=True)}
    assert stages['detection'] == 'completed'
    assert stages['evidence_collection'] == 'completed'
    assert stages['correlation'] == 'completed'
    assert stages['analysis'] == 'completed'
    assert stages['recommendation'] == 'completed'
    assert stages['report'] == 'completed'


def test_workflow_analysis_state_maps_from_triage_status():
    def analysis_state(status):
        return next(s['state'] for s in fi.derive_workflow_stages(
            _rich_snapshot(), triage_status=status, recommendation_count=0, report_generated=False)
            if s['stage'] == 'analysis')
    assert analysis_state('running') == 'in_progress'
    assert analysis_state('queued') == 'queued'
    assert analysis_state('failed') == 'failed'
    assert analysis_state('validation_failed') == 'failed'
    assert analysis_state(None) == 'pending'
    assert analysis_state('unavailable') == 'skipped'


def test_workflow_pending_report_and_recommendation_without_facts():
    stages = {s['stage']: s['state'] for s in fi.derive_workflow_stages(
        _rich_snapshot(), triage_status=None, recommendation_count=0, report_generated=False)}
    assert stages['recommendation'] == 'pending'
    assert stages['report'] == 'pending'


def test_evidence_collection_degraded_when_incomplete():
    snap = _rich_snapshot(evidence_incomplete=True, incomplete_reasons=['ambiguous'])
    stages = {s['stage']: s['state'] for s in fi.derive_workflow_stages(
        snap, triage_status='completed', recommendation_count=0, report_generated=False)}
    assert stages['evidence_collection'] == 'degraded'


# --------------------------------------------------------------------------
# Full analysis + report
# --------------------------------------------------------------------------
def test_build_analysis_degraded_when_snapshot_incomplete():
    analysis = fi.build_analysis(_empty_snapshot())
    assert analysis['status'] == 'degraded'
    assert analysis['degraded_reason']
    assert analysis['calc_version'] == fi.CALC_VERSION


def test_build_analysis_is_deterministic_and_independent_of_ai():
    """AI provider failure preserves deterministic findings: build_analysis never
    calls the AI, so its output is identical regardless of triage status."""
    a = fi.build_analysis(_rich_snapshot(), triage_status='failed')
    b = fi.build_analysis(_rich_snapshot(), triage_status='completed')
    # Findings / rule matches / confidence are the SAME (only workflow analysis-state differs).
    assert a['findings'] == b['findings']
    assert a['rule_matches'] == b['rule_matches']
    assert a['confidence'] == b['confidence']
    assert a['counts']['verified_facts'] >= 1


def test_report_statements_cite_persisted_evidence():
    snap = _rich_snapshot()
    analysis = fi.build_analysis(snap)
    header = {'incident_id': 'inc-1', 'reference': 'INC-2026-001', 'title': 'T', 'severity': 'critical', 'status': 'open', 'detected_at': 'x'}
    report = fi.build_report(header=header, snapshot=snap, snapshot_hash='sha256:abc', analysis=analysis)
    sections = report['sections']
    assert sections['incident_identity']['reference'] == 'INC-2026-001'
    assert sections['evidence_inventory'], 'report must inventory evidence references'
    # Every verified finding in the report carries at least one evidence reference.
    for f in sections['verified_findings']:
        assert f['evidence_refs'], f"verified finding without citation: {f['title']}"
    # The recommended next step is never an executed action.
    assert sections['recommended_next_step']['executes'] is False


def test_evidence_rows_are_real_records_with_corroboration_state():
    rows = fi.build_evidence_rows(_rich_snapshot())
    assert rows['total'] >= 1
    telem = [r for r in rows['rows'] if r['kind'] == 'telemetry']
    assert telem and telem[0]['corroboration'] == 'corroborated'  # canonical tx present
    # A telemetry row without a tx hash is only 'partial'.
    partial = fi.build_evidence_rows(_rich_snapshot(telemetry=[
        {'telemetry_id': 't9', 'event_type': 'oracle_update', 'observed_at': 'x'},
    ]))
    assert any(r['corroboration'] == 'partial' for r in partial['rows'])


# --------------------------------------------------------------------------
# Reference numbering (concurrency-safe, idempotent) — fake connection
# --------------------------------------------------------------------------
class _Result:
    def __init__(self, row=None):
        self._row = row

    def fetchone(self):
        return self._row


class _CounterConn:
    """Fake connection whose reference-counter UPSERT increments monotonically, like
    the real atomic ``ON CONFLICT DO UPDATE ... RETURNING counter``."""

    def __init__(self, *, schema_ready=True, reference=None):
        self._counter = 0
        self.schema_ready = schema_ready
        self.reference = reference
        self.executed = []

    def execute(self, sql, params=None):
        s = ' '.join(sql.split())
        self.executed.append(s)
        if 'to_regclass' in s:
            return _Result({'present': self.schema_ready})
        if s.startswith('INSERT INTO incident_reference_counters'):
            self._counter += 1
            return _Result({'counter': self._counter})
        if s.startswith('SELECT reference FROM incidents'):
            return _Result({'reference': self.reference})
        if s.startswith('UPDATE incidents SET reference'):
            self.reference = params[0]
            return _Result(None)
        return _Result(None)

    def commit(self):
        pass


def test_next_incident_reference_is_monotonic_not_count():
    conn = _CounterConn()
    a = fi.next_incident_reference(conn, workspace_id='ws-1', year=2026)
    b = fi.next_incident_reference(conn, workspace_id='ws-1', year=2026)
    assert a == 'INC-2026-001'
    assert b == 'INC-2026-002'
    # Numbering came from the atomic counter UPSERT, never COUNT(*).
    assert any('incident_reference_counters' in s for s in conn.executed)
    assert not any('COUNT(*)' in s for s in conn.executed)


def test_assign_incident_reference_is_idempotent():
    # Already-numbered incident keeps its reference (never renumbered).
    conn = _CounterConn(reference='INC-2026-042')
    ref = fi.assign_incident_reference(conn, workspace_id='ws-1', incident_id='inc-1', now=_Now(2026))
    assert ref == 'INC-2026-042'
    assert not any('incident_reference_counters' in s for s in conn.executed)


def test_assign_incident_reference_noop_without_schema():
    conn = _CounterConn(schema_ready=False)
    assert fi.assign_incident_reference(conn, workspace_id='ws-1', incident_id='inc-1', now=_Now(2026)) is None


class _Now:
    def __init__(self, year):
        self.year = year
