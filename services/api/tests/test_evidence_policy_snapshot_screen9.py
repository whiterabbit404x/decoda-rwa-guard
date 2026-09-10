"""Screen 9 — the sealed policy snapshot points at the INCIDENT-TIME version.

Policies change. An evidence package that re-read the live policy configuration
when it was opened would describe a rule that may never have applied to the
incident it claims to prove. So the snapshot is anchored to the immutable
evaluation record the deterministic engine persisted (its ``policy_key`` and
``policy_version``) and the constraint values are read from
``governance_policy_versions`` AT THAT EXACT VERSION.

Covered here:
  L. The snapshot names the incident-time version, not the latest policy.
  +  An enforcement decision outranks a Screen 11 what-if simulation.
  +  "No policy evaluation" is a recorded fact with a reason, never a missing key.
  +  A version row that cannot be read reports ``source: unavailable`` rather
     than silently substituting the current policy.
"""
from __future__ import annotations

from services.api.app import pilot

WS = 'ws-policy-1'
INCIDENT = 'inc-policy-1'
POLICY_ID = 'policy-uuid-1'


class _Result:
    def __init__(self, rows=None):
        self._rows = rows or []

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _PolicyConnection:
    """Answers the evaluation lookup and the versioned-snapshot lookup.

    ``version_rows`` maps a requested policy VERSION to its immutable stored
    snapshot, so a test can prove which version was actually asked for.
    """

    def __init__(self, *, evaluations, version_rows=None, version_readable=True):
        self._evaluations = evaluations
        self._version_rows = version_rows or {}
        self._version_readable = version_readable
        self.requested_versions: list[int] = []

    def execute(self, stmt, params=None):
        normalized = ' '.join(str(stmt).split())
        if 'FROM governance_policy_evaluations' in normalized:
            return _Result(self._evaluations)
        if 'FROM governance_policy_versions' in normalized:
            if not self._version_readable:
                raise RuntimeError('relation governance_policy_versions does not exist')
            requested = int(params[2])
            self.requested_versions.append(requested)
            row = self._version_rows.get(requested)
            return _Result([row] if row else [])
        raise AssertionError(f'unexpected query: {normalized!r}')


def _evaluation(*, version: int, decision: str = 'DENY', simulation: bool = False, eval_id: str = 'eval-1'):
    return {
        'id': eval_id,
        'policy_id': POLICY_ID,
        'policy_key': 'POL-MINT-007',
        'policy_version': version,
        'decision': decision,
        'reason_codes': ['COMPLIANCE_APPROVAL_MISSING'],
        'required_approvals': ['compliance_approver'],
        'checks': [],
        'operation': 'MINT',
        'amount_usd': '5000000.00',
        'simulation': simulation,
        'engine_version': 'v4',
        'canonical_event_id': '0x7a1d9c3f',
        'asset_id': 'rwa-001',
        'incident_id': INCIDENT,
        'input_snapshot': {},
        'evaluated_at': '2026-02-01T10:42:18Z',
    }


# ── L. The snapshot uses the incident-time version, not the latest policy ────

def test_snapshot_points_at_the_incident_time_version_not_the_latest_policy():
    """The policy has since advanced to v9; the package must still say v7."""
    connection = _PolicyConnection(
        evaluations=[_evaluation(version=7)],
        version_rows={
            7: {'snapshot': {'maximum_daily_amount_usd': '10000000.00', 'version': 7}, 'status': 'ACTIVE', 'changed_at': 'x'},
            9: {'snapshot': {'maximum_daily_amount_usd': '99000000.00', 'version': 9}, 'status': 'ACTIVE', 'changed_at': 'y'},
        },
    )

    snapshot = pilot._incident_policy_snapshot(connection, workspace_id=WS, evaluations=[_evaluation(version=7)])

    assert snapshot['present'] is True
    assert snapshot['policy_key'] == 'POL-MINT-007'
    assert snapshot['policy_version'] == 7
    # The version row lookup asked for 7 — never for "the current policy".
    assert connection.requested_versions == [7]
    assert snapshot['policy_version_snapshot']['version'] == 7
    assert snapshot['source'] == 'governance_policy_versions'


def test_snapshot_never_queries_the_governance_policies_current_state():
    """Any read of the live policy table would raise in this fake."""
    connection = _PolicyConnection(
        evaluations=[_evaluation(version=3)],
        version_rows={3: {'snapshot': {'version': 3}, 'status': 'ACTIVE', 'changed_at': 'z'}},
    )
    snapshot = pilot._incident_policy_snapshot(connection, workspace_id=WS, evaluations=[_evaluation(version=3)])
    assert snapshot['policy_version'] == 3


# ── Enforcement outranks a what-if simulation ────────────────────────────────

def test_enforcement_decision_is_preferred_over_a_simulation():
    """A Screen 11 what-if predicts; it never authorized anything."""
    evaluations = [
        _evaluation(version=9, decision='ALLOW', simulation=True, eval_id='sim-1'),
        _evaluation(version=7, decision='DENY', simulation=False, eval_id='enf-1'),
    ]
    connection = _PolicyConnection(
        evaluations=evaluations,
        version_rows={7: {'snapshot': {'version': 7}, 'status': 'ACTIVE', 'changed_at': 'x'}},
    )

    snapshot = pilot._incident_policy_snapshot(connection, workspace_id=WS, evaluations=evaluations)

    assert snapshot['evaluation_id'] == 'enf-1'
    assert snapshot['decision'] == 'DENY'
    assert snapshot['decision_kind'] == 'enforcement'
    assert snapshot['policy_version'] == 7
    assert snapshot['enforcement_evaluation_count'] == 1
    assert snapshot['evaluation_count'] == 2


def test_a_simulation_only_incident_is_labelled_as_a_simulation():
    evaluations = [_evaluation(version=4, decision='ALLOW', simulation=True, eval_id='sim-only')]
    connection = _PolicyConnection(
        evaluations=evaluations,
        version_rows={4: {'snapshot': {'version': 4}, 'status': 'DRAFT', 'changed_at': 'x'}},
    )

    snapshot = pilot._incident_policy_snapshot(connection, workspace_id=WS, evaluations=evaluations)

    assert snapshot['decision_kind'] == 'simulation'
    assert snapshot['enforcement_evaluation_count'] == 0


# ── Absence is a recorded fact, never a missing key ──────────────────────────

def test_no_policy_evaluation_is_recorded_with_a_reason():
    connection = _PolicyConnection(evaluations=[])
    snapshot = pilot._incident_policy_snapshot(connection, workspace_id=WS, evaluations=[])
    assert snapshot['present'] is False
    assert 'No policy evaluation' in snapshot['reason']
    assert snapshot['source'] == 'governance_policy_evaluations'


def test_an_evaluation_without_a_version_is_reported_as_not_present():
    evaluations = [{**_evaluation(version=7), 'policy_version': None}]
    connection = _PolicyConnection(evaluations=evaluations)
    snapshot = pilot._incident_policy_snapshot(connection, workspace_id=WS, evaluations=evaluations)
    assert snapshot['present'] is False
    assert 'policy version' in snapshot['reason']


# ── A version row that cannot be read never falls back to the live policy ────

def test_unreadable_version_row_reports_source_unavailable():
    evaluations = [_evaluation(version=7)]
    connection = _PolicyConnection(evaluations=evaluations, version_readable=False)

    snapshot = pilot._incident_policy_snapshot(connection, workspace_id=WS, evaluations=evaluations)

    assert snapshot['present'] is True
    assert snapshot['policy_version'] == 7
    assert snapshot['source'] == 'unavailable'
    assert snapshot['policy_version_snapshot'] is None


def test_a_missing_version_row_reports_source_unavailable_rather_than_a_substitute():
    evaluations = [_evaluation(version=7)]
    connection = _PolicyConnection(evaluations=evaluations, version_rows={9: {'snapshot': {'version': 9}}})

    snapshot = pilot._incident_policy_snapshot(connection, workspace_id=WS, evaluations=evaluations)

    assert snapshot['policy_version'] == 7
    assert snapshot['policy_version_snapshot'] is None
    assert snapshot['source'] == 'unavailable'


# ── Evaluations are resolved by canonical identifiers only ───────────────────

def test_evaluations_are_resolved_by_incident_and_canonical_event_id():
    captured: dict = {}

    class _Capture(_PolicyConnection):
        def execute(self, stmt, params=None):
            normalized = ' '.join(str(stmt).split())
            if 'FROM governance_policy_evaluations' in normalized:
                captured['sql'] = normalized
                captured['params'] = params
            return super().execute(stmt, params)

    connection = _Capture(evaluations=[_evaluation(version=7)])
    rows = pilot._incident_policy_evaluation_rows(
        connection, workspace_id=WS, incident_id=INCIDENT, canonical_event_id='0x7a1d9c3f',
    )

    assert len(rows) == 1
    assert 'incident_id = %s::uuid' in captured['sql']
    assert 'canonical_event_id = %s::text' in captured['sql']
    # Workspace-scoped, and never a time-window heuristic.
    assert captured['params'][0] == WS
    assert 'INTERVAL' not in captured['sql'] and 'NOW()' not in captured['sql']


def test_a_failed_evaluation_read_returns_no_rows_rather_than_fabricating_one():
    class _Broken:
        def execute(self, stmt, params=None):
            raise RuntimeError('relation governance_policy_evaluations does not exist')

    rows = pilot._incident_policy_evaluation_rows(
        _Broken(), workspace_id=WS, incident_id=INCIDENT, canonical_event_id=None,
    )
    assert rows == []


# ── The snapshot is what the manifest seals ──────────────────────────────────

def test_sealed_manifest_carries_the_incident_time_snapshot_and_the_signature_covers_it():
    from services.api.app.evidence_manifest_signer import resolve_manifest_signer
    from services.api.app.evidence_signing import build_evidence_manifest

    evaluations = [_evaluation(version=7)]
    connection = _PolicyConnection(
        evaluations=evaluations,
        version_rows={7: {'snapshot': {'version': 7}, 'status': 'ACTIVE', 'changed_at': 'x'}},
    )
    snapshot = pilot._incident_policy_snapshot(connection, workspace_id=WS, evaluations=evaluations)

    values = {'summary.json': {'incident_id': INCIDENT}}
    manifest, _ = build_evidence_manifest(
        export_id='pkg-1', export_type='proof_bundle', workspace_id=WS,
        generated_at='2026-02-01T10:42:20Z', generated_by_user_id='user-1',
        source_resource_type='incident', source_resource_id=INCIDENT,
        storage_backend='local', file_values=values, seal_merkle=True,
        policy_snapshot=snapshot, required_artifacts=['summary.json'],
        file_provenance={'summary.json': {'domain': 'PACKAGE', 'source_record_type': 'package_summary'}},
    )
    signer = resolve_manifest_signer()
    seal = signer.sign(manifest)

    assert manifest['policy_snapshot']['policy_version'] == 7
    assert signer.verify(manifest, seal)['valid'] is True

    # Rewriting the sealed policy version breaks the signature — the snapshot is
    # genuinely covered by the seal, not merely attached beside it.
    tampered = {**manifest, 'policy_snapshot': {**manifest['policy_snapshot'], 'policy_version': 9}}
    assert signer.verify(tampered, seal)['valid'] is False
