"""Screen 8 — the trust boundary repairs.

Three properties this file exists to keep true, each of which was demonstrably
false before:

  1. ENFORCEMENT IS PRODUCED, NOT ASSUMED. ``governance_policy_evaluations`` had
     exactly one producer, and it stamped ``simulation = TRUE``. Screen 8 filters
     those out, so no action could ever reach an authorized policy verdict. There
     is now a real enforcement producer that runs the SAME deterministic engine on
     canonical backend facts and stores ``simulation = FALSE``.

  2. A QUORUM IS SATISFIED BY PEOPLE. ``quorum_authority='delegated_governance'``
     short-circuited the quorum check, so 0 of 2 approvals reported AUTHORIZED.
     Naming an authority is not evidence from one.

  3. AUTHORIZATION IS NOT CAPABILITY. ``LIVE_ACTION_EXECUTION_ENABLED`` was not
     enforced by the execute command, so an unconfigured deployment could still
     reach a provider — and a failed or unconfigured governance call was recorded
     as a 200 with a manufactured attestation hash.

Engine tests are pure (no DB, no network). Command tests follow the repository's
fake-connection unit style.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from services.api.app import pilot
from services.api.app.domains.governance_policy import config as gpc
from services.api.app.domains.governance_policy import enforcement
from services.api.app.domains.response_gate import config as rgc
from services.api.app.domains.response_gate.engine import (
    ApprovalRecord,
    GateInputs,
    evaluate_gate,
)

WORKSPACE = 'ws-1'
ACTION_ID = 'b2222222-2222-4222-8222-222222222222'
INCIDENT_ID = 'c537b73f-1976-4a44-b589-946194794399'
ALERT_ID = 'a1111111-1111-4111-8111-111111111111'
DETECTION_ID = 'd3333333-3333-4333-8333-333333333333'
ASSET_ID = 'e4444444-4444-4444-8444-444444444444'
POLICY_ID = 'f5555555-5555-4555-8555-555555555555'
PROPOSER_ID = '99999999-9999-4999-8999-999999999999'

SECURITY_LEAD = gpc.ROLE_SECURITY_LEAD
TREASURY = gpc.ROLE_TREASURY_OPERATOR
COMPLIANCE = gpc.ROLE_COMPLIANCE_APPROVER

NOW = datetime(2026, 9, 1, 10, 43, 0, tzinfo=timezone.utc)


# ─────────────────────────────────────────────────────────────────────────────
# Fakes
# ─────────────────────────────────────────────────────────────────────────────
class _Result:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


def _policy_row(**overrides):
    """An ACTIVE MINT policy requiring a cleared subscription inside a window."""
    row = {
        'id': POLICY_ID,
        'workspace_id': WORKSPACE,
        'policy_key': 'POL-MINT-007',
        'name': 'RWA Mint Policy',
        'operation': gpc.OPERATION_MINT,
        'status': gpc.STATUS_ACTIVE,
        'version': 7,
        'asset_id': ASSET_ID,
        'required_business_event': gpc.BUSINESS_EVENT_SUBSCRIPTION,
        'settlement_requirement': gpc.REQUIREMENT_CLEARED,
        'allowed_window_start_utc': None,
        'allowed_window_end_utc': None,
        'maximum_daily_amount_usd': None,
        'required_roles': [],
        'violation_action': 'DENY',
        'origin': 'customer',
        'created_by_user_id': PROPOSER_ID,
        'updated_by_user_id': PROPOSER_ID,
        'created_at': NOW,
        'updated_at': NOW,
    }
    row.update(overrides)
    return row


def _action_row(**overrides):
    row = {
        'id': ACTION_ID,
        'workspace_id': WORKSPACE,
        'status': 'pending',
        'mode': 'simulated',
        'action_type': 'pause_mint_redeem',
        'execution_state': 'proposed',
        # Exactly what create_enforcement_action and
        # recommend_response_action_for_incident write: the incident and the
        # alert, and NOTHING else. No production writer puts a detection_id in
        # chain_linked_ids, so a fixture that supplies one tests a shape the
        # product never produces — which is how the identifier mismatch below
        # stayed invisible.
        'execution_metadata': {'chain_linked_ids': {
            'incident_id': INCIDENT_ID, 'alert_id': ALERT_ID,
        }},
        'incident_id': INCIDENT_ID,
        'alert_id': ALERT_ID,
        'approved_by_user_id': 'approver-1',
        'created_by_user_id': PROPOSER_ID,
    }
    row.update(overrides)
    return row


class _EnforcementConn:
    """Answers every canonical read the enforcement resolver and the gate make.

    Deliberately literal: each branch is one real statement the code issues, so a
    query that stops being made (or starts being made) shows up as a test change
    rather than passing silently.
    """

    def __init__(
        self,
        *,
        action_row=None,
        policy_row=None,
        detection=None,
        issuance=None,
        alert=None,
        approvals=None,
        evaluation=None,
        incident_status='investigating',
        tables=None,
        fail_on=None,
        honour_simulation_filter=True,
        linked_alert_id=ALERT_ID,
        linked_incident_id=INCIDENT_ID,
    ):
        self.executed: list[tuple[str, object]] = []
        self.inserted_evaluations: list[dict] = []
        self.committed = 0
        self._action_row = action_row if action_row is not None else _action_row()
        self._policy_row = policy_row
        self._detection = detection
        #: Which alert / incident the detection row is linked to, i.e. the values
        #: threat_detection.service stamped on it.
        self._linked_alert_id = str(linked_alert_id or '') or None
        self._linked_incident_id = str(linked_incident_id or '') or None
        self._issuance = issuance
        # `ensure_alert_for_detection` never sets alerts.detection_id (it is a
        # `detections(id)`, migration 0042); it stamps
        # threat_detections.linked_alert_id instead. The fake mirrors that.
        self._alert = alert if alert is not None else {
            'id': ALERT_ID, 'incident_id': INCIDENT_ID, 'target_id': None,
        }
        self._approvals = approvals or []
        #: The row `latest_policy_evaluation` finds, if the query is allowed to.
        self._evaluation = evaluation
        self._incident_status = incident_status
        self._fail_on = tuple(fail_on or ())
        #: When True the fake honours `simulation = FALSE` the way Postgres would,
        #: so a stored SIMULATION is genuinely invisible to Screen 8.
        self._honour_simulation_filter = honour_simulation_filter
        self._tables = tables if tables is not None else {
            'public.governance_policies', 'public.governance_policy_versions',
            'public.governance_policy_evaluations', 'public.response_action_approvals',
            'public.alerts', 'public.targets', 'public.threat_detections',
            'public.asset_authorized_issuances',
        }

    def execute(self, statement, params=None):
        n = ' '.join(str(statement).split())
        self.executed.append((n, params))
        for marker in self._fail_on:
            if marker in n:
                raise RuntimeError(f'read failed: {marker}')

        if 'to_regclass' in n:
            return _Result(row={'present': str(params[0]) in self._tables, 'ok': str(params[0]) in self._tables})
        if 'information_schema.columns' in n:
            return _Result(row={'present': True, 'ok': True})
        if 'INSERT INTO governance_policy_evaluations' in n:
            self.inserted_evaluations.append({'statement': n, 'params': params})
            return _Result()
        if 'FROM response_actions WHERE id = %s::uuid AND workspace_id = %s' in n or \
           'FROM response_actions WHERE id = %s AND workspace_id = %s' in n:
            return _Result(row=self._action_row if str(params[1]) == WORKSPACE else None)
        if 'FROM alerts WHERE id' in n:
            return _Result(row=self._alert)
        if 'FROM targets WHERE id' in n:
            return _Result(row={'asset_id': ASSET_ID})
        if 'FROM threat_detections' in n:
            # Postgres-faithful. `threat_detections` is reachable ONLY by its own
            # id or its own linkage columns. A lookup keyed on alerts.detection_id
            # (a `detections(id)`) matches nothing here, exactly as in production.
            detection = self._detection or {}
            if 'WHERE id = %s::uuid' in n:
                return _Result(row=self._detection if str(params[0]) == str(detection.get('id')) else None)
            if 'linked_alert_id = %s::uuid' in n:
                return _Result(row=self._detection if str(params[0]) == self._linked_alert_id else None)
            if 'linked_incident_id = %s::uuid' in n:
                return _Result(row=self._detection if str(params[0]) == self._linked_incident_id else None)
            return _Result()
        if 'FROM asset_authorized_issuances' in n:
            return _Result(row=self._issuance)
        if "input_snapshot->>'fact_digest'" in n:
            # The idempotency lookup, answered from what this fake has stored.
            digest = str(params[2])
            for record in self.inserted_evaluations:
                snapshot = json.loads(record['params'][14])
                if snapshot.get('fact_digest') == digest:
                    return _Result(row={'id': record['params'][0],
                                        'decision': record['params'][9],
                                        'policy_version': record['params'][4]})
            return _Result()
        if 'COALESCE(SUM(amount_usd), 0)' in n:
            return _Result(row={'total': '0'})
        if 'FROM governance_policy_evaluations' in n:
            row = self._evaluation
            if (self._honour_simulation_filter and row
                    and 'simulation = FALSE' in n and row.get('simulation') is True):
                # What Postgres would do: the predicate excludes the row.
                return _Result()
            return _Result(row=row)
        if 'FROM governance_policies WHERE workspace_id = %s AND status = %s AND operation' in n:
            return _Result(row=self._policy_row)
        if 'FROM governance_policies WHERE id' in n:
            return _Result(row={'version': (self._policy_row or {}).get('version')} if self._policy_row else None)
        if 'FROM governance_policies' in n:
            return _Result(row={'present': True} if self._policy_row else None)
        if 'FROM response_action_approvals' in n:
            return _Result(rows=list(self._approvals))
        if 'FROM incidents WHERE id' in n:
            return _Result(row={
                'id': INCIDENT_ID, 'source_alert_id': ALERT_ID,
                'status': self._incident_status,
            })
        if 'FROM workspace_role_permissions' in n:
            return _Result(row={'granted': True})
        if 'FROM workspace_members' in n:
            return _Result(row={'role': 'admin'})
        return _Result()

    def commit(self):
        self.committed += 1

    def rollback(self):
        return None

    def statements(self, marker):
        return [(s, p) for s, p in self.executed if marker in s]


CLEARED_ISSUANCE = {
    'id': 'aa000000-0000-4000-8000-000000000001',
    'operation': 'mint',
    'amount': '1000000',
    'settlement_state': 'settled',
    'external_reference': 'SUB-1001',
}

MINT_DETECTION = {
    'id': DETECTION_ID,
    'operation': gpc.OPERATION_MINT,
    'observed_amount': '1000000',
    'primary_asset_id': ASSET_ID,
    'tx_hash': '0xevent-928181',
    'provenance': {'external_reference': 'SUB-1001'},
}


def _evaluation_row_from_insert(conn) -> dict:
    """Reconstruct the persisted evaluation as `latest_policy_evaluation` reads it.

    Built from the INSERT the producer actually issued, so the round trip proves
    the stored row — not a fixture — is what Screen 8 would consume.
    """
    assert conn.inserted_evaluations, 'no enforcement evaluation was persisted'
    params = conn.inserted_evaluations[-1]['params']
    (evaluation_id, _ws, policy_id, policy_key, policy_version, asset_id, incident_id,
     canonical_event_id, operation, decision, reason_codes, required_approvals, _checks,
     _amount, _snapshot, simulation, _engine, _user, evaluated_at, *rest) = params
    return {
        'id': evaluation_id,
        'policy_id': policy_id,
        'policy_key': policy_key,
        'policy_version': policy_version,
        'decision': decision,
        'reason_codes': json.loads(reason_codes),
        'required_approvals': json.loads(required_approvals),
        'required_roles': json.loads(rest[0]) if rest else [],
        'asset_id': asset_id,
        'incident_id': incident_id,
        'canonical_event_id': canonical_event_id,
        'operation': operation,
        'evaluated_at': evaluated_at,
        'simulation': simulation,
    }


def _patch_command(monkeypatch, connection, *, role='admin', mfa=False):
    @contextmanager
    def _fake_pg():
        yield connection

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)
    monkeypatch.setattr(
        pilot, '_require_workspace_permission',
        lambda *_a, **_k: ({'id': 'operator-1', 'mfa_enabled': mfa},
                           {'workspace_id': WORKSPACE, 'role': role}),
    )


# ═════════════════════════════════════════════════════════════════════════════
# 1. POLICY PATH — enforcement is produced, simulations never authorize
# ═════════════════════════════════════════════════════════════════════════════
def test_1_a_simulation_evaluation_can_never_authorize_screen_08():
    """A stored Screen 11 what-if is invisible to the gate.

    The connection here honours `simulation = FALSE` the way Postgres does, so the
    ONLY evaluation in the workspace is a simulation that says ALLOW — and the
    gate still reports NOT_EVALUATED and stays locked.
    """
    simulated_allow = {
        'id': 'sim-1', 'policy_id': POLICY_ID, 'policy_key': 'POL-MINT-007',
        'policy_version': 7, 'decision': 'ALLOW', 'reason_codes': ['POLICY_SATISFIED'],
        'required_approvals': [], 'required_roles': [], 'asset_id': None,
        'incident_id': INCIDENT_ID, 'canonical_event_id': None, 'operation': 'MINT',
        'evaluated_at': '2026-09-01T10:42:18+00:00', 'simulation': True,
    }
    connection = _EnforcementConn(evaluation=simulated_allow, policy_row=_policy_row())
    gate = pilot.response_action_execution_gate(
        connection, _action_row(), workspace_id=WORKSPACE,
        workspace_context={'role': 'admin'},
    )
    assert gate['policy_decision'] == rgc.POLICY_NOT_EVALUATED
    assert gate['can_execute'] is False
    assert rgc.POLICY_EVALUATION_MISSING in gate['reason_codes']

    # The exclusion is in the STATEMENT, not in a post-filter a refactor could drop.
    reads = connection.statements('FROM governance_policy_evaluations')
    assert reads and all('simulation = FALSE' in stmt for stmt, _ in reads)


def test_1_b_the_simulator_and_the_enforcer_are_different_producers():
    """They write different rows because they read different inputs.

    ``build_context(simulation=...)`` is the only place the flag is set, and
    ``record_evaluation`` writes it from the DECISION rather than from a caller's
    argument — so no call site can store a simulation as an enforcement record.
    """
    import inspect
    from services.api.app.domains.governance_policy import endpoints, service

    assert 'simulation=True' in inspect.getsource(endpoints.simulate_endpoint)
    assert 'simulation=False' in inspect.getsource(enforcement.evaluate_response_action)
    assert 'bool(decision.simulation)' in inspect.getsource(service.record_evaluation)
    # The enforcement producer never reads an existing evaluation to copy it.
    producer = inspect.getsource(enforcement)
    assert 'evaluate_policy' in producer


def test_2_real_enforcement_allow_reaches_screen_08():
    """Authoritative facts + an ACTIVE policy => a persisted ALLOW the gate consumes."""
    connection = _EnforcementConn(
        policy_row=_policy_row(), detection=MINT_DETECTION, issuance=CLEARED_ISSUANCE,
    )
    outcome = enforcement.evaluate_response_action(
        connection, workspace_id=WORKSPACE, action=_action_row(), now=NOW, user_id='operator-1',
    )
    assert outcome.status == enforcement.STATUS_RECORDED
    assert outcome.decision.decision == gpc.DECISION_ALLOW
    assert outcome.decision.simulation is False

    # The SAME persisted row, read back through the gate's own lookup semantics.
    stored = _evaluation_row_from_insert(connection)
    assert stored['simulation'] is False
    gate_conn = _EnforcementConn(evaluation=stored, policy_row=_policy_row())
    gate = pilot.response_action_execution_gate(
        gate_conn, _action_row(), workspace_id=WORKSPACE, workspace_context={'role': 'admin'},
    )
    assert gate['policy_decision'] == gpc.DECISION_ALLOW
    assert gate['can_execute'] is True
    assert gate['decision'] == rgc.GATE_AUTHORIZED


def test_2_b_the_enforcement_command_persists_the_record_and_returns_the_gate(monkeypatch):
    """The server-gated producer, end to end.

    POST /response/actions/{id}/policy-evaluation runs the engine, writes the
    ``simulation = FALSE`` row, and returns the SAME gate Screen 8 renders —
    re-read after the write rather than predicted from the decision.
    """
    connection = _EnforcementConn(
        policy_row=_policy_row(), detection=MINT_DETECTION, issuance=CLEARED_ISSUANCE,
    )
    _patch_command(monkeypatch, connection)

    payload = pilot.evaluate_response_action_policy(
        ACTION_ID, SimpleNamespace(headers={'x-workspace-id': WORKSPACE}),
    )
    assert payload['status'] == enforcement.STATUS_RECORDED
    assert payload['recorded'] is True
    assert payload['evaluation']['decision'] == gpc.DECISION_ALLOW
    assert payload['evaluation']['simulation'] is False
    assert payload['decision_authority'] == 'Deterministic Policy Engine'
    assert payload['ai_authority'] == 'Recommend only'
    assert payload['execution_gate']['policy_decision'] in {gpc.DECISION_ALLOW, rgc.POLICY_NOT_EVALUATED}

    # The row was written, and the authorization record is audited (§13).
    assert connection.inserted_evaluations
    history = [prm for stmt, prm in connection.executed if 'INSERT INTO action_history' in stmt]
    assert any('policy_enforcement_evaluated' in str(prm) for prm in history)


def test_2_c_the_enforcement_command_is_workspace_scoped(monkeypatch):
    """An action in another workspace is a 404, never an evaluation."""
    connection = _EnforcementConn(policy_row=_policy_row())
    _patch_command(monkeypatch, connection)
    monkeypatch.setattr(
        pilot, '_require_workspace_permission',
        lambda *_a, **_k: ({'id': 'operator-1', 'mfa_enabled': False},
                           {'workspace_id': 'ws-other', 'role': 'admin'}),
    )
    with pytest.raises(HTTPException) as exc:
        pilot.evaluate_response_action_policy(
            ACTION_ID, SimpleNamespace(headers={'x-workspace-id': 'ws-other'}),
        )
    assert exc.value.status_code == 404
    assert connection.inserted_evaluations == []


def test_2_d_a_recommended_action_gets_its_enforcement_evaluation_on_creation():
    """The producer also runs where actions ENTER Screen 8.

    Without this an operator would have to remember to trigger an evaluation
    before any recommended action could ever be authorized.
    """
    connection = _EnforcementConn(
        policy_row=_policy_row(), detection=MINT_DETECTION, issuance=CLEARED_ISSUANCE,
    )
    status_value = pilot._record_response_action_enforcement_evaluation(
        connection, workspace_id=WORKSPACE, action_id=ACTION_ID, user_id='operator-1',
    )
    assert status_value == enforcement.STATUS_RECORDED
    assert connection.inserted_evaluations
    assert connection.committed >= 1


def test_2_e_a_failed_enforcement_evaluation_never_fails_the_caller():
    """Recommending a plan must not break because a policy could not be read —
    and the unevaluated action must not be treated as authorized either."""
    connection = _EnforcementConn(
        policy_row=_policy_row(), detection=MINT_DETECTION,
        fail_on=('FROM threat_detections',),
    )
    status_value = pilot._record_response_action_enforcement_evaluation(
        connection, workspace_id=WORKSPACE, action_id=ACTION_ID, user_id='operator-1',
    )
    assert status_value == enforcement.STATUS_FACTS_UNAVAILABLE
    assert connection.inserted_evaluations == []


def test_2_f_nothing_is_recorded_when_no_active_policy_governs_the_operation():
    """A DENY/POLICY_NOT_FOUND row would be a recorded refusal for an action the
    workspace never chose to govern. Screen 8 reports NOT_APPLICABLE instead."""
    connection = _EnforcementConn(
        policy_row=None, detection=MINT_DETECTION, issuance=CLEARED_ISSUANCE,
    )
    outcome = enforcement.evaluate_response_action(
        connection, workspace_id=WORKSPACE, action=_action_row(), now=NOW, user_id='operator-1',
    )
    assert outcome.status == enforcement.STATUS_NO_POLICY
    assert connection.inserted_evaluations == []


def test_2_g_only_an_active_policy_can_govern_an_enforcement_evaluation():
    """The lookup filters on status server-side; a DRAFT policy governs nothing."""
    connection = _EnforcementConn(policy_row=_policy_row())
    enforcement.governing_policy(
        connection, workspace_id=WORKSPACE, operation=gpc.OPERATION_MINT, asset_id=ASSET_ID,
    )
    lookup = connection.statements('FROM governance_policies WHERE workspace_id = %s AND status = %s')
    assert lookup
    stmt, params = lookup[-1]
    assert params[1] == gpc.STATUS_ACTIVE
    assert params[0] == WORKSPACE


def test_2_h_re_evaluating_unchanged_facts_writes_no_second_decision():
    """Idempotency, and why it matters.

    A duplicate ALLOW is not merely noise: ``daily_total_usd`` counts ENFORCEMENT
    allows, so a second row for one operation consumes a capped policy's daily
    issuance limit twice and would eventually deny a legitimate mint.
    """
    connection = _EnforcementConn(
        policy_row=_policy_row(), detection=MINT_DETECTION, issuance=CLEARED_ISSUANCE,
    )
    first = enforcement.evaluate_response_action(
        connection, workspace_id=WORKSPACE, action=_action_row(), now=NOW, user_id='operator-1',
    )
    assert first.status == enforcement.STATUS_RECORDED
    assert len(connection.inserted_evaluations) == 1

    second = enforcement.evaluate_response_action(
        connection, workspace_id=WORKSPACE, action=_action_row(), now=NOW, user_id='operator-1',
    )
    assert second.status == enforcement.STATUS_ALREADY_EVALUATED
    assert second.recorded is False
    assert second.existing_decision == gpc.DECISION_ALLOW
    assert len(connection.inserted_evaluations) == 1, 'a duplicate decision was written'


def test_2_i_facts_that_actually_changed_produce_a_new_evaluation():
    """Idempotency must not freeze a stale verdict.

    The settlement that was pending has cleared: that is a different fact, so the
    action is evaluated again and reaches the decision the new state deserves.
    """
    connection = _EnforcementConn(
        policy_row=_policy_row(), detection=MINT_DETECTION,
        issuance={**CLEARED_ISSUANCE, 'settlement_state': 'pending'},
    )
    denied = enforcement.evaluate_response_action(
        connection, workspace_id=WORKSPACE, action=_action_row(), now=NOW, user_id='operator-1',
    )
    assert denied.decision.decision == gpc.DECISION_DENY

    connection._issuance = CLEARED_ISSUANCE
    allowed = enforcement.evaluate_response_action(
        connection, workspace_id=WORKSPACE, action=_action_row(), now=NOW, user_id='operator-1',
    )
    assert allowed.status == enforcement.STATUS_RECORDED
    assert allowed.decision.decision == gpc.DECISION_ALLOW
    assert len(connection.inserted_evaluations) == 2


def test_2_j_a_new_policy_version_is_always_evaluated_afresh():
    """A superseded verdict is not an authorization; the digest binds the version."""
    facts = enforcement.EnforcementFacts(operation=gpc.OPERATION_MINT, asset_id=ASSET_ID)
    v7 = facts.digest(policy_id=POLICY_ID, policy_version=7, response_action_id=ACTION_ID)
    v8 = facts.digest(policy_id=POLICY_ID, policy_version=8, response_action_id=ACTION_ID)
    assert v7 != v8
    # And the clock is NOT part of it, so repeated evaluation is stable.
    assert v7 == facts.digest(policy_id=POLICY_ID, policy_version=7, response_action_id=ACTION_ID)


def test_3_real_enforcement_deny_blocks_execution(monkeypatch):
    """An unsettled issuance is a DENY, and the execute command refuses it."""
    connection = _EnforcementConn(
        policy_row=_policy_row(), detection=MINT_DETECTION,
        issuance={**CLEARED_ISSUANCE, 'settlement_state': 'pending'},
    )
    outcome = enforcement.evaluate_response_action(
        connection, workspace_id=WORKSPACE, action=_action_row(), now=NOW, user_id='operator-1',
    )
    assert outcome.decision.decision == gpc.DECISION_DENY
    assert gpc.SETTLEMENT_NOT_CLEARED in outcome.decision.reason_codes

    stored = _evaluation_row_from_insert(connection)
    execute_conn = _EnforcementConn(evaluation=stored, policy_row=_policy_row())
    _patch_command(monkeypatch, execute_conn)
    with pytest.raises(HTTPException) as exc:
        pilot.execute_enforcement_action(ACTION_ID, SimpleNamespace(headers={'x-workspace-id': WORKSPACE}))

    assert exc.value.status_code == 409
    assert exc.value.detail['execution_gate']['decision'] == rgc.GATE_DENIED
    assert rgc.POLICY_DENIED in exc.value.detail['reason_codes']


def test_4_a_missing_enforcement_evaluation_fails_closed(monkeypatch):
    """A governed action with no enforcement record is LOCKED, never ALLOW."""
    connection = _EnforcementConn(evaluation=None, policy_row=_policy_row())
    _patch_command(monkeypatch, connection)
    with pytest.raises(HTTPException) as exc:
        pilot.execute_enforcement_action(ACTION_ID, SimpleNamespace(headers={'x-workspace-id': WORKSPACE}))
    assert rgc.POLICY_EVALUATION_MISSING in exc.value.detail['reason_codes']


def test_4_b_unreadable_facts_record_no_evaluation_at_all():
    """A failed canonical read abandons the pass rather than guessing.

    Recording a verdict here would be worse than recording nothing: it would look
    exactly like a real enforcement decision to every downstream consumer.
    """
    connection = _EnforcementConn(
        policy_row=_policy_row(), detection=MINT_DETECTION, issuance=CLEARED_ISSUANCE,
        fail_on=('FROM asset_authorized_issuances',),
    )
    outcome = enforcement.evaluate_response_action(
        connection, workspace_id=WORKSPACE, action=_action_row(), now=NOW, user_id='operator-1',
    )
    assert outcome.status == enforcement.STATUS_FACTS_UNAVAILABLE
    assert outcome.recorded is False
    assert connection.inserted_evaluations == []


def test_4_b2_an_unreadable_policy_lookup_also_records_nothing():
    """The policy read is a canonical fact like any other, and fails closed."""
    connection = _EnforcementConn(
        policy_row=_policy_row(), detection=MINT_DETECTION, issuance=CLEARED_ISSUANCE,
        fail_on=('FROM governance_policies WHERE workspace_id = %s AND status = %s',),
    )
    outcome = enforcement.evaluate_response_action(
        connection, workspace_id=WORKSPACE, action=_action_row(), now=NOW, user_id='operator-1',
    )
    assert outcome.status == enforcement.STATUS_FACTS_UNAVAILABLE
    assert 'governing_policy' in outcome.facts.unreadable
    assert connection.inserted_evaluations == []


def test_4_c_an_unrecognized_settlement_state_is_never_treated_as_cleared():
    assert enforcement.normalize_settlement_state('something-new') is None
    assert enforcement.normalize_settlement_state('') is None
    assert enforcement.normalize_settlement_state('settled') == gpc.SETTLEMENT_CLEARED
    assert enforcement.normalize_settlement_state('pending') == gpc.SETTLEMENT_PENDING


def test_5_policy_version_and_reference_are_preserved_end_to_end():
    """The verdict carries the exact policy and version that produced it."""
    connection = _EnforcementConn(
        policy_row=_policy_row(version=9), detection=MINT_DETECTION, issuance=CLEARED_ISSUANCE,
    )
    outcome = enforcement.evaluate_response_action(
        connection, workspace_id=WORKSPACE, action=_action_row(), now=NOW, user_id='operator-1',
    )
    assert outcome.decision.policy_id == POLICY_ID
    assert outcome.decision.policy_key == 'POL-MINT-007'
    assert outcome.decision.policy_version == 9

    stored = _evaluation_row_from_insert(connection)
    assert (stored['policy_id'], stored['policy_key'], stored['policy_version']) == (POLICY_ID, 'POL-MINT-007', 9)
    assert stored['incident_id'] == INCIDENT_ID
    assert stored['canonical_event_id'] == '0xevent-928181'

    # A verdict produced under a SUPERSEDED version is not a current authorization.
    gate_conn = _EnforcementConn(evaluation=stored, policy_row=_policy_row(version=10))
    gate = pilot.response_action_execution_gate(
        gate_conn, _action_row(), workspace_id=WORKSPACE, workspace_context={'role': 'admin'},
    )
    assert rgc.POLICY_VERSION_MISMATCH in gate['reason_codes']
    assert gate['can_execute'] is False


def test_5_a2_an_enforcement_allow_that_names_roles_still_needs_the_signatures():
    """The 0149 role list survives the round trip and binds Screen 8's quorum.

    This is the case the role-scoped quorum exists for: the policy PERMITS the
    operation and still demands named sign-offs before the response runs. On an
    ALLOW the outstanding list (`required_approvals`) is empty by construction, so
    only the authoritative `required_roles` can carry the requirement.
    """
    connection = _EnforcementConn(
        policy_row=_policy_row(required_roles=[SECURITY_LEAD]),
        detection=MINT_DETECTION, issuance=CLEARED_ISSUANCE,
    )
    outcome = enforcement.evaluate_response_action(
        connection, workspace_id=WORKSPACE, action=_action_row(), now=NOW, user_id='operator-1',
    )
    # SECURITY_LEAD is satisfied by an APPROVAL artifact, which the policy engine
    # has no evidence source for — so it is outstanding and the verdict is DENY.
    assert outcome.decision.required_roles == (SECURITY_LEAD,)
    stored = _evaluation_row_from_insert(connection)
    assert stored['required_roles'] == [SECURITY_LEAD]

    # Screen 8 reads the authoritative list and demands a real recorded sign-off.
    allow_with_roles = {**stored, 'decision': 'ALLOW', 'reason_codes': ['POLICY_SATISFIED'],
                        'required_approvals': []}
    gate_conn = _EnforcementConn(
        evaluation=allow_with_roles, policy_row=_policy_row(required_roles=[SECURITY_LEAD]),
    )
    locked = pilot.response_action_execution_gate(
        gate_conn, _action_row(), workspace_id=WORKSPACE, workspace_context={'role': 'admin'},
    )
    assert locked['required_roles'] == [SECURITY_LEAD]
    assert locked['missing_roles'] == [SECURITY_LEAD]
    assert locked['can_execute'] is False

    signed_conn = _EnforcementConn(
        evaluation=allow_with_roles, policy_row=_policy_row(required_roles=[SECURITY_LEAD]),
        approvals=[{'approver_user_id': 'sec-1', 'approver_role': 'owner',
                    'decision': 'approved', 'created_at': '2026-09-01T10:43:02+00:00',
                    'approval_role': SECURITY_LEAD}],
    )
    unlocked = pilot.response_action_execution_gate(
        signed_conn, _action_row(), workspace_id=WORKSPACE, workspace_context={'role': 'admin'},
    )
    assert unlocked['missing_roles'] == []
    assert unlocked['can_execute'] is True


def test_5_a3_the_proposers_own_permissions_cannot_satisfy_a_policy_role():
    """Nobody's own permission satisfies the policy's operator-authority check.

    ``TREASURY_OPERATOR`` is evidenced by ``response.propose`` — exactly the
    permission every proposer of a response action holds. Offering the proposer
    as the operation's operator would therefore have satisfied the requirement
    automatically, for an operation nobody actually authorized. The operator is
    left unestablished instead, and the role stays outstanding.
    """
    connection = _EnforcementConn(
        policy_row=_policy_row(required_roles=[TREASURY]),
        detection=MINT_DETECTION, issuance=CLEARED_ISSUANCE,
    )
    facts = enforcement.resolve_action_facts(
        connection, workspace_id=WORKSPACE, action=_action_row(),
    )
    assert facts.operator_id is None
    # The proposer is still recorded, for traceability, in a field the engine
    # never reads.
    assert facts.proposer_user_id == PROPOSER_ID
    assert 'operator_id' not in facts.as_payload() or facts.as_payload()['operator_id'] is None

    outcome = enforcement.evaluate_response_action(
        connection, workspace_id=WORKSPACE, action=_action_row(), now=NOW, user_id=PROPOSER_ID,
    )
    assert outcome.decision.decision == gpc.DECISION_DENY
    assert gpc.TREASURY_OPERATOR_MISSING in outcome.decision.reason_codes
    assert TREASURY in outcome.decision.required_approvals


def test_5_b_the_input_snapshot_records_where_each_fact_came_from():
    """The stored snapshot is reproducible: the rows read, plus a digest of them."""
    connection = _EnforcementConn(
        policy_row=_policy_row(), detection=MINT_DETECTION, issuance=CLEARED_ISSUANCE,
    )
    enforcement.evaluate_response_action(
        connection, workspace_id=WORKSPACE, action=_action_row(), now=NOW, user_id='operator-1',
    )
    snapshot = json.loads(connection.inserted_evaluations[-1]['params'][14])
    assert snapshot['simulation'] is False
    assert snapshot['response_action_id'] == ACTION_ID
    assert snapshot['fact_sources']['detection_id'] == DETECTION_ID
    assert snapshot['fact_sources']['authorized_issuance_id'] == CLEARED_ISSUANCE['id']
    assert len(snapshot['input_hash']) == 64


def test_5_c_the_browser_cannot_supply_a_decision_to_the_enforcement_path():
    """The producer takes no payload, so there is no field an ALLOW could arrive in."""
    import inspect
    signature = inspect.signature(enforcement.evaluate_response_action)
    assert set(signature.parameters) == {'connection', 'workspace_id', 'action', 'now', 'user_id'}

    facts_fields = set(enforcement.EnforcementFacts.__dataclass_fields__)
    client_assertable = {'decision', 'policy_version', 'policy_id', 'daily_total_usd',
                         'operator_has_treasury_role', 'ai_recommendation', 'confidence'}
    assert client_assertable.isdisjoint(facts_fields)

    # The command it is reached through takes no payload either.
    command_signature = inspect.signature(pilot.evaluate_response_action_policy)
    assert set(command_signature.parameters) == {'action_id', 'request'}

    # ...and so does the route, which is where a request body would arrive.
    # Read as source: importing app.main needs the full web stack.
    import pathlib
    main_source = pathlib.Path(pilot.__file__).with_name('main.py').read_text()
    marker = 'def response_action_policy_evaluation_route('
    assert marker in main_source
    route_params = main_source.split(marker, 1)[1].split(')', 1)[0]
    assert 'Body' not in route_params and 'payload' not in route_params


def test_5_d_a_workspace_cannot_read_another_tenants_enforcement_facts():
    """Every read the producer makes carries the workspace id."""
    connection = _EnforcementConn(
        policy_row=_policy_row(), detection=MINT_DETECTION, issuance=CLEARED_ISSUANCE,
    )
    enforcement.evaluate_response_action(
        connection, workspace_id=WORKSPACE, action=_action_row(), now=NOW, user_id='operator-1',
    )
    scoped_reads = [
        (stmt, params) for stmt, params in connection.executed
        if stmt.startswith('SELECT') and ' FROM ' in stmt
        and 'to_regclass' not in stmt and 'information_schema' not in stmt
    ]
    assert scoped_reads
    for stmt, params in scoped_reads:
        assert 'workspace_id = %s' in stmt, stmt
        assert WORKSPACE in [str(p) for p in (params or ())], stmt


# ═════════════════════════════════════════════════════════════════════════════
# 2. QUORUM — an authority's NAME is not an approval
# ═════════════════════════════════════════════════════════════════════════════
def _gate(**overrides) -> GateInputs:
    base = dict(
        action_id=ACTION_ID,
        policy_decision=rgc.POLICY_ALLOW,
        policy_id=POLICY_ID, policy_key='POL-MINT-007',
        policy_version=7, policy_current_version=7, evaluation_id='eval-1',
        approvals=(), required_quorum=0, lifecycle_approval_status='pending',
        approval_required=True, action_status='pending', execution_status='not_started',
        now=NOW, incident_id=INCIDENT_ID, incident_status='investigating',
        requester_authorized=True, execution_authority_available=True,
        execution_adapter_configured=True,
    )
    base.update(overrides)
    return GateInputs(**base)


def _approved(user_id: str, role=None) -> ApprovalRecord:
    return ApprovalRecord(approver_user_id=user_id, decision='approved', role=role,
                          decided_at='2026-09-01T10:43:02+00:00')


def test_6_delegated_governance_with_zero_of_two_approvals_cannot_authorize():
    """The exact audit finding: delegated_governance, 0 / 2, reported AUTHORIZED."""
    gate = evaluate_gate(_gate(
        quorum_authority='delegated_governance', required_quorum=2, approvals=(),
    ))
    assert gate.required_quorum == 2
    assert gate.approvals_collected == 0
    assert gate.can_execute is False
    assert gate.decision == rgc.GATE_LOCKED
    assert rgc.HUMAN_QUORUM_INCOMPLETE in gate.reason_codes
    assert rgc.DELEGATED_AUTHORITY_NOT_VERIFIED in gate.reason_codes
    # The claimed authority is still REPORTED — the operator can see what the
    # action names — it just does not satisfy anything.
    assert gate.as_dict()['quorum_authority'] == 'delegated_governance'


def test_6_b_delegated_governance_with_one_of_two_approvals_cannot_authorize():
    gate = evaluate_gate(_gate(
        quorum_authority='delegated_governance', required_quorum=2,
        approvals=(_approved('u1', TREASURY),),
    ))
    assert gate.approvals_collected == 1
    assert gate.can_execute is False
    assert rgc.HUMAN_QUORUM_INCOMPLETE in gate.reason_codes


def test_6_c_delegated_governance_with_two_valid_approvals_may_proceed():
    """A real quorum authorizes it — the fix withholds nothing that was earned."""
    gate = evaluate_gate(_gate(
        quorum_authority='delegated_governance', required_quorum=2,
        approvals=(_approved('u1', TREASURY), _approved('u2', COMPLIANCE)),
    ))
    assert gate.approvals_collected == 2
    assert gate.can_execute is True
    assert gate.decision == rgc.GATE_AUTHORIZED
    assert rgc.DELEGATED_AUTHORITY_NOT_VERIFIED not in gate.reason_codes


def test_6_d_a_delegated_claim_cannot_stand_in_for_a_missing_lifecycle_approval():
    """No approval rows AND a pending lifecycle status is still not satisfied."""
    gate = evaluate_gate(_gate(
        quorum_authority='delegated_governance', required_quorum=1,
        approvals=(), lifecycle_approval_status='pending',
    ))
    assert gate.can_execute is False


def test_7_incomplete_quorum_cannot_authorize():
    gate = evaluate_gate(_gate(
        required_roles=(TREASURY, COMPLIANCE),
        approvals=(_approved('u1', TREASURY),), required_quorum=2,
    ))
    assert gate.can_execute is False
    assert gate.missing_roles == (COMPLIANCE,)
    assert rgc.HUMAN_QUORUM_INCOMPLETE in gate.reason_codes


def test_8_a_complete_valid_quorum_can_authorize():
    gate = evaluate_gate(_gate(
        required_roles=(TREASURY, COMPLIANCE),
        approvals=(_approved('u1', TREASURY), _approved('u2', COMPLIANCE)),
        required_quorum=2,
    ))
    assert gate.can_execute is True
    assert gate.decision == rgc.GATE_AUTHORIZED


def test_8_b_one_actor_cannot_satisfy_two_required_roles():
    """Two roles need two people. One approver's one decision covers one role."""
    gate = evaluate_gate(_gate(
        required_roles=(SECURITY_LEAD, COMPLIANCE),
        approvals=(_approved('same-user', SECURITY_LEAD),), required_quorum=2,
    ))
    assert gate.can_execute is False
    assert gate.satisfied_roles == (SECURITY_LEAD,)
    assert gate.missing_roles == (COMPLIANCE,)
    assert gate.approvals_collected == 1

    # Even two rows from the same identity count once, so a duplicated decision
    # can never close a two-person quorum.
    doubled = evaluate_gate(_gate(
        required_quorum=2, approvals=(_approved('same-user'), _approved('same-user')),
    ))
    assert doubled.approvals_collected == 1
    assert doubled.can_execute is False


def test_9_a_a_governance_string_is_not_an_approver_identity(monkeypatch):
    """A live governance action with no human approver is refused.

    The execution path's own name used to be substituted for the approver, which
    satisfied the approver check with a value that has no RBAC to verify, no
    step-up to complete, and no identity for the audit record to name.
    """
    connection = _EnforcementConn(
        action_row=_action_row(mode='live', action_type='freeze_wallet',
                               approved_by_user_id=None),
        policy_row=None,
    )
    _patch_command(monkeypatch, connection)
    monkeypatch.setenv('LIVE_ACTION_EXECUTION_ENABLED', 'true')

    with pytest.raises(HTTPException) as exc:
        pilot.execute_enforcement_action(ACTION_ID, SimpleNamespace(headers={'x-workspace-id': WORKSPACE}))

    assert exc.value.status_code == 409
    assert 'approval' in str(exc.value.detail).lower()
    # Nothing was submitted for the refused attempt.
    assert not connection.statements('INSERT INTO governance_policy_evaluations')


def test_9_b_step_up_authentication_is_not_skipped_for_a_delegated_path(monkeypatch):
    """An MFA-enrolled executor must still step up on a governance live action.

    The delegated branch previously wrote ``step_up = {'required': False}`` and
    skipped the challenge entirely.
    """
    connection = _EnforcementConn(
        action_row=_action_row(mode='live', action_type='freeze_wallet',
                               approved_by_user_id='approver-1'),
        policy_row=None,
    )
    _patch_command(monkeypatch, connection, mfa=True)
    monkeypatch.setenv('LIVE_ACTION_EXECUTION_ENABLED', 'true')
    monkeypatch.setattr(pilot, '_verify_live_action_approver_role', lambda *_a, **_k: None)

    with pytest.raises(HTTPException) as exc:
        pilot.execute_enforcement_action(
            ACTION_ID, SimpleNamespace(headers={'x-workspace-id': WORKSPACE}),
        )
    assert exc.value.status_code == 403
    assert 'step-up' in str(exc.value.detail).lower()


def test_9_c_the_gate_reports_a_delegated_authority_rather_than_hiding_it():
    payload = evaluate_gate(_gate(
        quorum_authority='delegated_governance', required_quorum=2,
    )).as_dict()
    assert payload['quorum_authority_label'] == 'Delegated governance authority'
    assert payload['approvers'] == []
    assert payload['approvals_collected'] == 0


# ═════════════════════════════════════════════════════════════════════════════
# 3. ADAPTER — authorization is not capability
# ═════════════════════════════════════════════════════════════════════════════
def test_10_adapter_unavailable_means_can_execute_false_without_denying_the_policy():
    """The distinction the fix preserves, in one assertion set.

    A live provider-backed action with no adapter: the POLICY still says ALLOW and
    the AUTHORIZATION still says AUTHORIZED — neither is rewritten — but the run
    cannot happen, so can_execute is False and the reason names the adapter.
    """
    gate = evaluate_gate(_gate(
        execution_adapter_required=True, execution_adapter_configured=False,
        required_quorum=0, lifecycle_approval_status='approved',
    ))
    assert gate.policy_decision == rgc.POLICY_ALLOW
    assert gate.authorization_decision == rgc.GATE_AUTHORIZED
    assert gate.execution_ready is False
    assert gate.can_execute is False
    assert gate.decision == rgc.GATE_LOCKED
    assert rgc.EXECUTION_ADAPTER_NOT_CONFIGURED in gate.reason_codes

    payload = gate.as_dict()
    assert payload['authorization_decision'] == rgc.GATE_AUTHORIZED
    assert payload['execution_ready'] is False
    assert payload['can_execute'] is False


def test_10_b_an_action_that_contacts_no_provider_is_runnable_without_an_adapter():
    """A dry run is a legitimate run. The adapter state is advisory for it."""
    gate = evaluate_gate(_gate(
        execution_adapter_required=False, execution_adapter_configured=False,
        required_quorum=0, lifecycle_approval_status='approved',
    ))
    assert gate.can_execute is True
    assert gate.execution_ready is True
    assert rgc.EXECUTION_ADAPTER_NOT_CONFIGURED in gate.reason_codes


def test_10_c_the_adapter_requirement_is_derived_from_the_action_not_the_client():
    assert rgc.execution_adapter_required(mode='live', live_execution_path='safe') is True
    assert rgc.execution_adapter_required(mode='live', live_execution_path='governance') is True
    assert rgc.execution_adapter_required(mode='live', live_execution_path='manual_only') is False
    assert rgc.execution_adapter_required(mode='simulated', live_execution_path='safe') is False
    assert rgc.execution_adapter_required(mode='recommended', live_execution_path='governance') is False


def test_11_direct_execute_with_no_adapter_is_rejected_server_side(monkeypatch):
    """§21 — the API call that skips the UI. Frontend disabling is not the gate."""
    monkeypatch.delenv('LIVE_ACTION_EXECUTION_ENABLED', raising=False)
    contacted: list[str] = []
    connection = _EnforcementConn(
        action_row=_action_row(mode='live', action_type='freeze_wallet',
                               approved_by_user_id='approver-1'),
        policy_row=None,
    )
    _patch_command(monkeypatch, connection)
    monkeypatch.setattr(pilot, '_verify_live_action_approver_role', lambda *_a, **_k: None)
    monkeypatch.setattr(
        pilot, '_submit_freeze_wallet_governance_action',
        lambda *_a, **_k: contacted.append('governance') or {'action_id': 'gov-nope'},
    )

    with pytest.raises(HTTPException) as exc:
        pilot.execute_enforcement_action(ACTION_ID, SimpleNamespace(headers={'x-workspace-id': WORKSPACE}))

    assert exc.value.status_code == 409
    assert rgc.EXECUTION_ADAPTER_NOT_CONFIGURED in exc.value.detail['reason_codes']
    assert contacted == [], 'no provider may be contacted without an adapter'
    assert not any("SET status = 'executed'" in stmt for stmt, _ in connection.executed)


def test_12_a_dry_run_deployment_cannot_reach_a_live_provider(monkeypatch):
    """The guard immediately before the provider call, independent of the gate."""
    monkeypatch.delenv('LIVE_ACTION_EXECUTION_ENABLED', raising=False)
    assert rgc.live_execution_configured() is False
    for path in ('safe', 'governance'):
        with pytest.raises(HTTPException) as exc:
            pilot._require_execution_adapter(action_id=ACTION_ID, live_execution_path=path)
        assert exc.value.status_code == 409
        assert exc.value.detail['code'] == rgc.EXECUTION_ADAPTER_NOT_CONFIGURED

    monkeypatch.setenv('LIVE_ACTION_EXECUTION_ENABLED', 'true')
    assert rgc.live_execution_configured() is True
    assert pilot._require_execution_adapter(action_id=ACTION_ID, live_execution_path='safe') is None


def test_12_b_readiness_has_exactly_one_definition(monkeypatch):
    """There are not two executor systems. The gate reads the executor's own flag."""
    from services.api.app import response_action_executor as executor

    monkeypatch.setattr(executor, 'is_live_execution_enabled', lambda: True)
    assert rgc.live_execution_configured() is True
    monkeypatch.setattr(executor, 'is_live_execution_enabled', lambda: False)
    assert rgc.live_execution_configured() is False


def test_12_c_the_executor_factory_never_returns_a_live_executor_when_disabled(monkeypatch):
    from services.api.app import response_action_executor as executor

    monkeypatch.delenv('LIVE_ACTION_EXECUTION_ENABLED', raising=False)
    monkeypatch.setenv('RESPONSE_ACTION_EXECUTOR', 'safe')
    assert executor.get_executor().is_live_capable() is False


def test_13_a_an_unconfigured_governance_adapter_is_not_a_200_receipt(monkeypatch):
    """No COMPLIANCE_SERVICE_URL: nothing was contacted, so nothing is a receipt."""
    monkeypatch.delenv('COMPLIANCE_SERVICE_URL', raising=False)
    result = pilot._submit_freeze_wallet_governance_action(
        {'id': ACTION_ID, 'target_wallet': '0xabc'}, {'workspace_id': WORKSPACE}, {'id': 'operator-1'},
    )
    assert result['provider_contacted'] is False
    assert result['receipt_type'] == 'simulation'
    assert result['simulation'] is True
    assert result['status'] == 'not_submitted'
    assert result['response_code'] is None
    assert result['attestation_hash'] is None
    assert result['error_code'] == pilot.GOVERNANCE_ADAPTER_NOT_CONFIGURED


def test_13_b_a_failed_provider_call_is_not_a_200_receipt(monkeypatch):
    """An adapter IS configured and the call fails: PROVIDER_UNAVAILABLE, no hash."""
    from urllib.error import URLError

    monkeypatch.setenv('COMPLIANCE_SERVICE_URL', 'https://compliance.invalid')

    def _boom(*_a, **_k):
        raise URLError('connection refused')

    monkeypatch.setattr(pilot, 'urlopen', _boom)
    result = pilot._submit_freeze_wallet_governance_action(
        {'id': ACTION_ID, 'target_wallet': '0xabc'}, {'workspace_id': WORKSPACE}, {'id': 'operator-1'},
    )
    assert result['error_code'] == pilot.GOVERNANCE_PROVIDER_UNAVAILABLE
    assert result['provider_contacted'] is False
    assert result['attestation_hash'] is None
    assert result['response_code'] is None


def test_13_c_a_non_receipt_is_never_persisted_as_a_successful_submission(monkeypatch):
    """End to end: the execution records a FAILURE, not a proposed governance action."""
    connection = _EnforcementConn(
        action_row=_action_row(mode='live', action_type='freeze_wallet',
                               approved_by_user_id='approver-1'),
        policy_row=None,
    )
    _patch_command(monkeypatch, connection)
    monkeypatch.setenv('LIVE_ACTION_EXECUTION_ENABLED', 'true')
    monkeypatch.delenv('COMPLIANCE_SERVICE_URL', raising=False)
    monkeypatch.setattr(pilot, '_verify_live_action_approver_role', lambda *_a, **_k: None)

    payload = pilot.execute_enforcement_action(
        ACTION_ID, SimpleNamespace(headers={'x-workspace-id': WORKSPACE}),
    )
    assert payload['status'] == 'failed'
    assert payload['result_code'] is None
    assert payload['error_code'] == pilot.GOVERNANCE_ADAPTER_NOT_CONFIGURED
    assert payload['provider_receipts'] == []
    assert payload['execution_artifacts']['provider']['provider_contacted'] is False
    assert payload['execution_artifacts']['provider']['receipt_type'] == 'simulation'
    # Nothing that reads like an external attestation was written.
    written = ' '.join(str(params) for _, params in connection.executed)
    assert 'fallback-freeze_wallet' not in written


def test_13_d_a_real_provider_receipt_is_still_recorded_as_one(monkeypatch):
    """The fix removes fabricated receipts, not real ones."""
    connection = _EnforcementConn(
        action_row=_action_row(mode='live', action_type='freeze_wallet',
                               approved_by_user_id='approver-1'),
        policy_row=None,
    )
    _patch_command(monkeypatch, connection)
    monkeypatch.setenv('LIVE_ACTION_EXECUTION_ENABLED', 'true')
    monkeypatch.setattr(pilot, '_verify_live_action_approver_role', lambda *_a, **_k: None)
    monkeypatch.setattr(
        pilot, '_submit_freeze_wallet_governance_action',
        lambda *_a, **_k: {'action_id': 'gov-real-1', 'attestation_hash': 'att-real-1',
                           'policy_effects': ['Wallet frozen'], 'provider_contacted': True,
                           'receipt_type': 'provider', 'response_code': 200},
    )
    payload = pilot.execute_enforcement_action(
        ACTION_ID, SimpleNamespace(headers={'x-workspace-id': WORKSPACE}),
    )
    assert payload['execution_state'] == 'proposed'
    assert payload['result_code'] == 200
    assert payload['provider_receipts'][0]['external_request_id'] == 'gov-real-1'
    assert payload['provider_receipts'][0]['provider_contacted'] is True


def test_14_a_configured_adapter_still_requires_the_policy_verdict(monkeypatch):
    """An adapter is capability, never authorization. A DENY is still a DENY."""
    monkeypatch.setenv('LIVE_ACTION_EXECUTION_ENABLED', 'true')
    deny_evaluation = {
        'id': 'eval-deny-1', 'policy_id': POLICY_ID, 'policy_key': 'POL-MINT-007',
        'policy_version': 7, 'decision': 'DENY', 'reason_codes': ['SETTLEMENT_NOT_CLEARED'],
        'required_approvals': [COMPLIANCE], 'required_roles': [COMPLIANCE], 'asset_id': None,
        'incident_id': INCIDENT_ID, 'canonical_event_id': None, 'operation': 'MINT',
        'evaluated_at': '2026-09-01T10:42:18+00:00',
    }
    connection = _EnforcementConn(evaluation=deny_evaluation, policy_row=_policy_row())
    _patch_command(monkeypatch, connection)
    with pytest.raises(HTTPException) as exc:
        pilot.execute_enforcement_action(ACTION_ID, SimpleNamespace(headers={'x-workspace-id': WORKSPACE}))
    assert exc.value.detail['execution_gate']['decision'] == rgc.GATE_DENIED
    assert rgc.POLICY_DENIED in exc.value.detail['reason_codes']


def test_14_b_configured_adapter_still_requires_quorum_and_rbac():
    """Each condition broken alone still closes the gate with the adapter present."""
    satisfied = _gate(execution_adapter_required=True, execution_adapter_configured=True,
                      required_quorum=0, lifecycle_approval_status='approved')
    assert evaluate_gate(satisfied).can_execute is True

    quorum_short = evaluate_gate(_gate(
        execution_adapter_required=True, execution_adapter_configured=True,
        required_quorum=2, approvals=(_approved('u1', TREASURY),),
        required_roles=(TREASURY, COMPLIANCE),
    ))
    assert quorum_short.can_execute is False
    assert rgc.HUMAN_QUORUM_INCOMPLETE in quorum_short.reason_codes

    forbidden = evaluate_gate(_gate(
        execution_adapter_required=True, execution_adapter_configured=True,
        required_quorum=0, lifecycle_approval_status='approved', requester_authorized=False,
    ))
    assert forbidden.can_execute is False
    assert rgc.RBAC_FORBIDDEN in forbidden.reason_codes


# ═════════════════════════════════════════════════════════════════════════════
# 4. AI — recommend only, on every one of the paths above
# ═════════════════════════════════════════════════════════════════════════════
def test_15_a_no_input_on_either_deterministic_path_can_carry_ai_output():
    ai_ish = {'ai_recommendation', 'ai_confidence', 'ai_explanation', 'ai_summary',
              'recommendation', 'model', 'confidence', 'llm'}
    assert ai_ish.isdisjoint(set(GateInputs.__dataclass_fields__))
    assert ai_ish.isdisjoint(set(enforcement.EnforcementFacts.__dataclass_fields__))
    assert ai_ish.isdisjoint(set(enforcement.EnforcementOutcome.__dataclass_fields__))


def _imported_modules(module) -> set[str]:
    """Every module name this module imports, at any nesting level."""
    import ast
    import inspect

    names: set[str] = set()
    for node in ast.walk(ast.parse(inspect.getsource(module))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_15_b_neither_deterministic_module_can_reach_an_ai_provider():
    """The structural guarantee: there is no seam a model could be called through.

    Asserted on the IMPORT GRAPH rather than on the prose — both modules document
    the boundary in their docstrings, and a text search would match that instead
    of an actual dependency.
    """
    from services.api.app.domains.response_gate import engine as gate_engine

    forbidden = ('ai_provider', 'ai_triage', 'openai', 'anthropic', 'explanation')
    for module in (gate_engine, enforcement):
        imports = _imported_modules(module)
        for name in imports:
            assert not any(bad in name.lower() for bad in forbidden), f'{module.__name__} imports {name}'
        # And no HTTP client either: neither module calls anything out of process.
        assert not {'requests', 'httpx', 'urllib.request'} & imports


def test_15_c_an_ai_recommendation_alone_never_reaches_an_authorization(monkeypatch):
    """A recommended action with no enforcement evaluation stays locked.

    A recommendation is not a policy evaluation and not an approval, whatever the
    model said about it.
    """
    connection = _EnforcementConn(evaluation=None, policy_row=_policy_row())
    _patch_command(monkeypatch, connection)
    with pytest.raises(HTTPException) as exc:
        pilot.execute_enforcement_action(ACTION_ID, SimpleNamespace(headers={'x-workspace-id': WORKSPACE}))
    gate = exc.value.detail['execution_gate']
    assert gate['can_execute'] is False
    assert gate['ai_authority'] == 'Recommend only'
    assert gate['execution_authority'] == 'Deterministic Policy Engine'
    assert 'unlock_execution' in gate['ai_prohibited']
    assert 'satisfy_quorum' in gate['ai_prohibited']


def test_15_d_the_enforcement_producer_reads_no_recommendation_row():
    """Its fact sources are canonical rows; ai_recommendations is not among them."""
    import inspect
    source = inspect.getsource(enforcement)
    assert 'ai_recommendations' not in source
    for table in ('threat_detections', 'asset_authorized_issuances', 'alerts'):
        assert table in source


# ═════════════════════════════════════════════════════════════════════════════
# 5. MIGRATION 0149 — a silent integrity regression is surfaced, not inferred
# ═════════════════════════════════════════════════════════════════════════════
class _MigrationConn:
    def __init__(self, *, present=True, raises=False):
        self._present = present
        self._raises = raises

    def execute(self, statement, params=None):
        if self._raises:
            raise RuntimeError('database unavailable')
        return _Result(row={'present': self._present})


def test_16_a_migration_0149_is_in_the_deploy_migration_chain():
    """The runner globs the migrations directory, so presence in it IS enrolment."""
    files = sorted(p.name for p in pilot.migration_dir().glob('*.sql'))
    assert '0149_screen8_consistency_pass.sql' in files
    # It is the required integrity migration the health check asserts.
    assert any(m['migration'] == '0149_screen8_consistency_pass.sql'
               for m in pilot.REQUIRED_INTEGRITY_MIGRATIONS)


def test_16_b_a_missing_0149_is_reported_as_degraded_not_healthy():
    status = pilot.integrity_migration_status(_MigrationConn(present=False))
    assert status['ready'] is False
    assert status['status'] == 'degraded'
    assert '0149_screen8_consistency_pass.sql' in status['missing_migrations']
    assert 'duplicate' in status['checks'][0]['detail'].lower()


def test_16_c_an_applied_0149_is_reported_as_ready():
    status = pilot.integrity_migration_status(_MigrationConn(present=True))
    assert status['ready'] is True
    assert status['status'] == 'ready'
    assert status['missing_migrations'] == []


def test_16_d_an_unverifiable_migration_is_never_reported_as_applied():
    """Fail-closed: a probe that could not run is 'unknown', never 'ready'."""
    status = pilot.integrity_migration_status(_MigrationConn(raises=True))
    assert status['ready'] is False
    assert status['status'] == 'unknown'
    assert '0149_screen8_consistency_pass.sql' in status['unverified_migrations']


def test_16_e_system_health_surfaces_the_missing_integrity_migration():
    from services.api.app import system_health

    degraded = system_health._check_integrity_migrations(_MigrationConn(present=False))
    assert degraded['status'] == 'degraded'
    assert '0149' in degraded['message']
    assert degraded['action']

    healthy = system_health._check_integrity_migrations(_MigrationConn(present=True))
    assert healthy['status'] == 'healthy'

    unavailable = system_health._check_integrity_migrations(_MigrationConn(raises=True))
    assert unavailable['status'] == 'unavailable'


def test_16_f_the_0149_migration_retires_duplicates_rather_than_deleting_them():
    """No audit record is destroyed by the integrity fix."""
    sql = (pilot.migration_dir() / '0149_screen8_consistency_pass.sql').read_text()
    assert 'superseded_at' in sql
    assert 'DELETE FROM' not in sql.upper()
    assert 'DROP TABLE' not in sql.upper()


# ═════════════════════════════════════════════════════════════════════════════
# 17. THE RUNTIME WIRING — the producer runs, on identifiers that actually match
#
# Everything in section 2 was true of the engine and false of the deployment: the
# enforcement producer existed, but for an action created the way the product
# creates one it either never ran or ran on an identifier that could not resolve.
# Three separate breaks, each on its own sufficient to park every action at
# POLICY_EVALUATION_MISSING / LOCKED:
#
#   a. `POST /response/actions` — how an action ENTERS Screen 8 from the Incident,
#      Alert and Threat-operations consoles — never invoked the producer at all.
#   b. Its INSERT carried 23 placeholders for 22 columns, so psycopg rejected the
#      statement before it reached Postgres.
#   c. The resolver keyed `threat_detections` with `alerts.detection_id`. That
#      column is a `detections(id)` (migration 0042) — a different table, a
#      different id space, and one with no operation/amount/tx_hash column at
#      all — and the Screen 5 writer never sets it, because the link it owns runs
#      the other way (`threat_detections.linked_alert_id`). The lookup could only
#      miss, leaving `operation` unresolved, `governing_policy` empty, and no row
#      written.
# ═════════════════════════════════════════════════════════════════════════════
def test_17_a_the_create_endpoint_insert_is_well_formed():
    """A statement psycopg rejects creates nothing — and evaluates nothing.

    Guards the exact defect: the VALUES list carried one more placeholder than the
    column list, so every `POST /response/actions` raised before the INSERT ran.
    """
    import inspect
    import re

    source = inspect.getsource(pilot.create_enforcement_action)
    match = re.search(
        r'INSERT INTO response_actions \((.*?)\)\s*VALUES \((.*?)\)', source, re.S,
    )
    assert match, 'the create INSERT was not found'
    columns = [c.strip() for c in match.group(1).replace('\n', ' ').split(',') if c.strip()]
    placeholders = match.group(2).count('%s')
    assert placeholders == len(columns), (
        f'{placeholders} placeholders for {len(columns)} columns'
    )


def test_17_b_the_detection_is_resolved_by_threat_detections_own_linkage():
    """The production shape: nothing hands the resolver a detection id.

    `create_enforcement_action` and `recommend_response_action_for_incident` write
    an incident id and an alert id and nothing else, and
    `ensure_alert_for_detection` leaves `alerts.detection_id` NULL. The canonical
    operation must still resolve — through `threat_detections.linked_alert_id`,
    which is the link the Screen 5 writer actually stamps.
    """
    connection = _EnforcementConn(
        policy_row=_policy_row(), detection=MINT_DETECTION, issuance=CLEARED_ISSUANCE,
        alert={'target_id': None},
    )
    action = _action_row(execution_metadata={'chain_linked_ids': {
        'incident_id': INCIDENT_ID, 'alert_id': ALERT_ID,
    }})
    facts = enforcement.resolve_action_facts(
        connection, workspace_id=WORKSPACE, action=action,
    )
    assert facts.readable
    assert facts.operation == gpc.OPERATION_MINT
    assert facts.asset_id == ASSET_ID
    assert facts.canonical_event_id == MINT_DETECTION['tx_hash']
    assert facts.sources['detection_id'] == DETECTION_ID

    outcome = enforcement.evaluate_response_action(
        connection, workspace_id=WORKSPACE, action=action, now=NOW, user_id='operator-1',
    )
    assert outcome.status == enforcement.STATUS_RECORDED
    assert outcome.decision.simulation is False


def test_17_b2_the_incident_link_resolves_an_action_with_no_alert():
    """`threat_detections.linked_incident_id` is the same link at incident level."""
    connection = _EnforcementConn(
        policy_row=_policy_row(), detection=MINT_DETECTION, issuance=CLEARED_ISSUANCE,
    )
    action = _action_row(
        alert_id=None,
        execution_metadata={'chain_linked_ids': {'incident_id': INCIDENT_ID}},
    )
    facts = enforcement.resolve_action_facts(
        connection, workspace_id=WORKSPACE, action=action,
    )
    assert facts.operation == gpc.OPERATION_MINT
    assert facts.sources['detection_id'] == DETECTION_ID


def test_17_c_alerts_detection_id_is_never_used_as_a_threat_detection_id():
    """The identifier mismatch, pinned at the statement level.

    `alerts.detection_id` references `detections(id)`. Selecting it in order to
    key `threat_detections` is the bug this section exists to prevent, so the
    resolver must not read that column, and every `threat_detections` lookup must
    be keyed on that table's own id or its own linkage columns.
    """
    connection = _EnforcementConn(
        policy_row=_policy_row(), detection=MINT_DETECTION, issuance=CLEARED_ISSUANCE,
    )
    enforcement.resolve_action_facts(
        connection, workspace_id=WORKSPACE,
        action=_action_row(execution_metadata={'chain_linked_ids': {
            'incident_id': INCIDENT_ID, 'alert_id': ALERT_ID,
        }}),
    )
    alert_reads = connection.statements('FROM alerts WHERE id')
    assert alert_reads, 'the alert was never read'
    assert all('detection_id' not in stmt for stmt, _ in alert_reads)

    detection_reads = connection.statements('FROM threat_detections')
    assert detection_reads, 'the detection was never looked up'
    for stmt, _ in detection_reads:
        assert ('WHERE id = %s::uuid' in stmt
                or 'linked_alert_id = %s::uuid' in stmt
                or 'linked_incident_id = %s::uuid' in stmt), stmt


def test_17_c2_an_unreadable_detection_still_records_nothing():
    """Fail-closed is preserved by the new lookup: a failed read abandons the pass."""
    connection = _EnforcementConn(
        policy_row=_policy_row(), detection=MINT_DETECTION, issuance=CLEARED_ISSUANCE,
        fail_on=('FROM threat_detections',),
    )
    outcome = enforcement.evaluate_response_action(
        connection, workspace_id=WORKSPACE,
        action=_action_row(execution_metadata={'chain_linked_ids': {
            'incident_id': INCIDENT_ID, 'alert_id': ALERT_ID,
        }}),
        now=NOW, user_id='operator-1',
    )
    assert outcome.status == enforcement.STATUS_FACTS_UNAVAILABLE
    assert connection.inserted_evaluations == []


class _CreatePathConn(_EnforcementConn):
    """Answers the reads `create_enforcement_action` itself makes, and remembers
    the row it INSERTED so the producer's read-back runs on the NEW action id
    rather than on a fixture standing in for it."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.created_actions: list[dict] = []

    def execute(self, statement, params=None):
        n = ' '.join(str(statement).split())
        if 'INSERT INTO response_actions' in n:
            self.executed.append((n, params))
            assert n.count('%s') == len(params), (
                f'{n.count("%s")} placeholders for {len(params)} parameters'
            )
            self.created_actions.append({
                'id': params[0], 'workspace_id': params[1],
                'incident_id': params[2], 'alert_id': params[3],
                'action_type': params[4], 'mode': params[5], 'status': params[6],
                'execution_state': params[14],
                'execution_metadata': json.loads(params[15]),
                'created_by_user_id': params[21],
            })
            return _Result()
        if 'FROM response_actions WHERE id = %s::uuid AND workspace_id = %s' in n and self.created_actions:
            row = self.created_actions[-1]
            match = str(params[0]) == str(row['id']) and str(params[1]) == WORKSPACE
            self.executed.append((n, params))
            return _Result(row=row if match else None)
        return super().execute(statement, params)


def test_17_d_creating_a_response_action_produces_its_enforcement_evaluation(monkeypatch):
    """THE PROOF, on one newly created action.

        canonical facts (detection -> alert -> incident -> action, + issuance)
          -> deterministic enforcement evaluation
          -> persisted governance_policy_evaluations row, simulation = FALSE
          -> Screen 8's latest_policy_evaluation retrieves it
          -> the gate reports ALLOW and applies quorum / adapter rules

    Every link is exercised against what the code actually wrote: the action row
    read by the producer is the one the endpoint INSERTed, and the evaluation the
    gate reads is rebuilt from the INSERT the producer issued.
    """
    connection = _CreatePathConn(
        policy_row=_policy_row(), detection=MINT_DETECTION, issuance=CLEARED_ISSUANCE,
    )
    _patch_command(monkeypatch, connection)

    created = pilot.create_enforcement_action(
        {
            'action_type': 'pause_mint_redeem',
            'mode': 'simulated',
            'incident_id': INCIDENT_ID,
            'alert_id': ALERT_ID,
        },
        SimpleNamespace(headers={'x-workspace-id': WORKSPACE}),
    )
    # 1. The action exists.
    assert connection.created_actions, 'no response action was created'
    new_action = connection.created_actions[-1]
    assert created['id'] == new_action['id']

    # 2. An enforcement evaluation was produced FOR IT, from canonical facts.
    assert connection.inserted_evaluations, 'no enforcement evaluation was persisted'
    snapshot = json.loads(connection.inserted_evaluations[-1]['params'][14])
    assert snapshot['response_action_id'] == new_action['id']
    assert snapshot['fact_sources']['detection_id'] == DETECTION_ID
    assert snapshot['fact_sources']['alert_id'] == ALERT_ID
    assert snapshot['fact_sources']['incident_id'] == INCIDENT_ID

    # 3. It is an ENFORCEMENT row, not a simulation.
    stored = _evaluation_row_from_insert(connection)
    assert stored['simulation'] is False
    assert stored['decision'] in {gpc.DECISION_ALLOW, gpc.DECISION_DENY}

    # 4. Screen 8 retrieves it, and reports a real verdict instead of MISSING.
    gate_conn = _EnforcementConn(evaluation=stored, policy_row=_policy_row())
    gate = pilot.response_action_execution_gate(
        gate_conn, new_action, workspace_id=WORKSPACE, workspace_context={'role': 'admin'},
    )
    assert gate['policy_decision'] == gpc.DECISION_ALLOW
    assert rgc.POLICY_EVALUATION_MISSING not in gate['reason_codes']

    # 5. The execution gate still applies its own rules on top of the verdict.
    assert gate['authorization_decision'] in {rgc.GATE_AUTHORIZED, rgc.GATE_LOCKED}
    assert 'required_quorum' in gate and 'execution_adapter_required' in gate
    assert gate['can_execute'] is (gate['authorization_decision'] == rgc.GATE_AUTHORIZED
                                   and gate['execution_ready'])


def test_17_e_a_denied_policy_reaches_screen_08_as_a_deny(monkeypatch):
    """The same wiring carries a refusal. No ALLOW is fabricated when the facts
    do not support one: an unsettled issuance denies, and the gate stays locked."""
    connection = _CreatePathConn(
        policy_row=_policy_row(), detection=MINT_DETECTION,
        issuance={**CLEARED_ISSUANCE, 'settlement_state': 'pending'},
    )
    _patch_command(monkeypatch, connection)
    pilot.create_enforcement_action(
        {'action_type': 'pause_mint_redeem', 'mode': 'simulated',
         'incident_id': INCIDENT_ID, 'alert_id': ALERT_ID},
        SimpleNamespace(headers={'x-workspace-id': WORKSPACE}),
    )
    stored = _evaluation_row_from_insert(connection)
    assert stored['simulation'] is False
    assert stored['decision'] == gpc.DECISION_DENY

    gate_conn = _EnforcementConn(evaluation=stored, policy_row=_policy_row())
    gate = pilot.response_action_execution_gate(
        gate_conn, connection.created_actions[-1], workspace_id=WORKSPACE,
        workspace_context={'role': 'admin'},
    )
    assert gate['policy_decision'] == gpc.DECISION_DENY
    assert gate['can_execute'] is False
    # A policy refusal is reported as DENIED, not as the weaker LOCKED: the gate
    # names the authority that refused rather than a missing fact.
    assert gate['decision'] == rgc.GATE_DENIED


def test_17_f_a_failed_evaluation_never_fails_the_creation_and_never_authorizes(monkeypatch):
    """Best-effort in the safe direction only.

    A producer failure must not abort creating the action, and must not leave a
    verdict behind. The gate then reports POLICY_EVALUATION_MISSING and stays
    locked, which is the honest state.
    """
    connection = _CreatePathConn(
        policy_row=_policy_row(), detection=MINT_DETECTION, issuance=CLEARED_ISSUANCE,
        fail_on=('FROM threat_detections',),
    )
    _patch_command(monkeypatch, connection)
    created = pilot.create_enforcement_action(
        {'action_type': 'pause_mint_redeem', 'mode': 'simulated',
         'incident_id': INCIDENT_ID, 'alert_id': ALERT_ID},
        SimpleNamespace(headers={'x-workspace-id': WORKSPACE}),
    )
    assert created['id'] and connection.created_actions
    assert connection.inserted_evaluations == []

    gate_conn = _EnforcementConn(policy_row=_policy_row())
    gate = pilot.response_action_execution_gate(
        gate_conn, connection.created_actions[-1], workspace_id=WORKSPACE,
        workspace_context={'role': 'admin'},
    )
    assert gate['policy_decision'] == rgc.POLICY_NOT_EVALUATED
    assert rgc.POLICY_EVALUATION_MISSING in gate['reason_codes']
    assert gate['can_execute'] is False


def test_17_g_every_path_that_creates_a_screen_8_action_evaluates_it():
    """The producer is invoked wherever an action ENTERS the Screen 8 workflow."""
    import inspect

    for command in (
        pilot.create_enforcement_action,
        pilot.recommend_response_action_for_incident,
        pilot.rollback_enforcement_action,
    ):
        assert '_record_response_action_enforcement_evaluation' in inspect.getsource(command), (
            f'{command.__name__} creates a response action without evaluating it'
        )


def test_17_h_an_asset_scoped_policy_is_visible_to_the_gates_scope_probe():
    """An unresolved asset is not evidence that no policy applies.

    Screen 8 read the asset only from `chain_linked_ids`, which no writer fills,
    so `_policy_governs` saw asset=None and matched workspace-wide policies only.
    An action governed by an ASSET-SCOPED policy therefore reported
    NOT_APPLICABLE — which the engine treats as passing — and an action with no
    enforcement decision at all came back AUTHORIZED. The asset is now resolved
    from canonical rows, so the probe sees the policy and the gate locks.
    """
    from services.api.app.domains.response_gate import service as rg_service

    action = _action_row(execution_metadata={'chain_linked_ids': {
        'incident_id': INCIDENT_ID, 'alert_id': ALERT_ID,
    }})
    connection = _EnforcementConn(
        # An ACTIVE policy scoped to ASSET_ID, and no enforcement evaluation.
        policy_row=_policy_row(), evaluation=None,
        detection=MINT_DETECTION, alert={'target_id': 'target-1'},
    )
    resolved = rg_service.resolve_action_asset_id(
        connection, workspace_id=WORKSPACE, alert_id=ALERT_ID, incident_id=INCIDENT_ID,
    )
    assert resolved == ASSET_ID

    gate = pilot.response_action_execution_gate(
        connection, action, workspace_id=WORKSPACE, workspace_context={'role': 'admin'},
    )
    assert gate['policy_decision'] == rgc.POLICY_NOT_EVALUATED
    assert rgc.POLICY_EVALUATION_MISSING in gate['reason_codes']
    assert gate['can_execute'] is False
    assert gate['decision'] == rgc.GATE_LOCKED


def test_17_i_an_unreadable_asset_is_never_reported_as_no_policy_applies():
    """Fail-closed: a walk that could not run reports NOT_EVALUATED, not NOT_APPLICABLE."""
    connection = _EnforcementConn(
        policy_row=None, evaluation=None, detection=MINT_DETECTION,
        fail_on=('FROM alerts WHERE id',),
    )
    gate = pilot.response_action_execution_gate(
        connection,
        _action_row(execution_metadata={'chain_linked_ids': {
            'incident_id': INCIDENT_ID, 'alert_id': ALERT_ID,
        }}),
        workspace_id=WORKSPACE, workspace_context={'role': 'admin'},
    )
    assert gate['policy_decision'] == rgc.POLICY_NOT_EVALUATED
    assert gate['can_execute'] is False


def test_17_j_the_gate_asset_walk_uses_the_same_canonical_links():
    """It must not reintroduce the identifier mismatch it exists to survive."""
    import inspect
    from services.api.app.domains.response_gate import service as rg_service

    source = inspect.getsource(rg_service.resolve_action_asset_id)
    assert 'linked_alert_id' in source and 'linked_incident_id' in source
    assert 'a.detection_id' not in source
    assert 'SELECT detection_id' not in source


def test_17_k_an_action_is_shown_its_own_evaluation_not_a_siblings():
    """Two actions on one incident must not share one verdict by accident.

    The lifecycle identifiers the lookup matches on (incident, asset, event) are
    shared between sibling actions, so the producer's per-action stamp is what
    keeps them apart. The specific match is preferred; the shared ones remain the
    fallback for rows written before the stamp existed.
    """
    import inspect
    from services.api.app.domains.response_gate import service as rg_service

    source = inspect.getsource(rg_service.latest_policy_evaluation)
    assert "input_snapshot->>'response_action_id'" in source
    assert 'ORDER BY' in source and 'simulation = FALSE' in source
    # The shared identifiers are still matched — this narrows nothing away.
    for shared in ('canonical_event_id =', 'incident_id =', 'asset_id ='):
        assert shared in source

    # And the gate passes the action it is building a verdict for.
    gate_source = inspect.getsource(rg_service.build_gate_inputs)
    assert 'response_action_id=action_id' in gate_source
