"""Screen 8 — the deterministic execution gate.

The architecture principle under test:

    AI may recommend. Deterministic policy controls execution.

These tests prove the boundary is real rather than rendered: an AI
recommendation alone never reaches ``can_execute``, a policy DENY keeps the gate
shut, an incomplete human quorum keeps it shut, a role nobody signed keeps it
shut, and a direct API execute request — the one that skips the UI entirely — is
refused with machine-readable reason codes and an audit record.

Engine tests are pure (no DB, no network). Command tests follow the repository's
fake-connection unit style.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from services.api.app import pilot
from services.api.app.domains.response_gate import config as rgc
from services.api.app.domains.response_gate.engine import (
    ApprovalRecord,
    GateInputs,
    evaluate_gate,
)

WORKSPACE = 'ws-1'
OTHER_WORKSPACE = 'ws-2'
ACTION_ID = 'b2222222-2222-4222-8222-222222222222'
INCIDENT_ID = 'c537b73f-1976-4a44-b589-946194794399'

TREASURY = rgc.APPROVER_ROLES[0]   # TREASURY_OPERATOR
COMPLIANCE = rgc.APPROVER_ROLES[1]  # COMPLIANCE_APPROVER

NOW = datetime(2026, 9, 1, 10, 43, 0, tzinfo=timezone.utc)


def _approved(user_id: str, role: str | None, at: str = '2026-09-01T10:43:02+00:00') -> ApprovalRecord:
    return ApprovalRecord(approver_user_id=user_id, decision='approved', role=role, decided_at=at)


def _allow(**overrides) -> GateInputs:
    """A gate context that is satisfied in every respect, so each test can break
    exactly ONE condition and prove that condition is what closes the lock."""
    base = dict(
        action_id=ACTION_ID,
        policy_decision=rgc.POLICY_ALLOW,
        policy_id='pol-1',
        policy_key='POL-MINT-007',
        policy_version=7,
        policy_current_version=7,
        evaluation_id='eval-1',
        required_roles=(),
        approvals=(),
        required_quorum=0,
        lifecycle_approval_status='not_required',
        action_status='pending',
        execution_status='not_started',
        now=NOW,
        incident_id=INCIDENT_ID,
        incident_status='investigating',
        requester_authorized=True,
        execution_authority_available=True,
        execution_adapter_configured=True,
    )
    base.update(overrides)
    return GateInputs(**base)


# ─────────────────────────────────────────────────────────────────────────────
# 1. AI recommendation alone cannot execute an action.
# ─────────────────────────────────────────────────────────────────────────────
def test_ai_recommendation_alone_cannot_execute():
    """The engine has NO input an AI layer could write.

    A recommendation is not a policy evaluation and not an approval, so an action
    that has only been recommended sits at NOT_EVALUATED with an empty approval
    set — locked, with the reason named.
    """
    gate = evaluate_gate(_allow(
        policy_decision=rgc.POLICY_NOT_EVALUATED,
        policy_id=None, policy_key=None, policy_version=None, policy_current_version=None,
        lifecycle_approval_status='pending', required_quorum=1,
    ))
    assert gate.can_execute is False
    assert gate.decision == rgc.GATE_LOCKED
    assert rgc.POLICY_EVALUATION_MISSING in gate.reason_codes

    # The structural guarantee: no field on GateInputs carries model output.
    ai_ish = {'ai_recommendation', 'ai_confidence', 'ai_explanation', 'recommendation',
              'model', 'confidence', 'llm', 'ai_authority'}
    assert ai_ish.isdisjoint(set(GateInputs.__dataclass_fields__))


def test_gate_states_both_authorities_on_every_result():
    """The trust boundary is stated by the BACKEND, not by UI copy."""
    payload = evaluate_gate(_allow()).as_dict()
    assert payload['ai_authority'] == 'Recommend only'
    assert payload['execution_authority'] == 'Deterministic Policy Engine'
    assert payload['ai_authority_mode'] == 'RECOMMEND_ONLY'
    assert payload['execution_authority_mode'] == 'DETERMINISTIC_POLICY_ENGINE'
    assert 'execute_action' in payload['ai_prohibited']
    assert 'unlock_execution' in payload['ai_prohibited']


# ─────────────────────────────────────────────────────────────────────────────
# 2. Policy DENY always keeps the action locked.
# ─────────────────────────────────────────────────────────────────────────────
def test_policy_deny_keeps_action_locked_even_with_full_quorum():
    gate = evaluate_gate(_allow(
        policy_decision=rgc.POLICY_DENY,
        policy_reason_codes=('COMPLIANCE_APPROVAL_MISSING',),
        required_roles=(TREASURY, COMPLIANCE),
        approvals=(_approved('u1', TREASURY), _approved('u2', COMPLIANCE)),
        required_quorum=2,
    ))
    assert gate.can_execute is False
    assert gate.decision == rgc.GATE_DENIED
    assert gate.missing_roles == ()
    assert rgc.POLICY_DENIED in gate.reason_codes


def test_policy_reason_codes_are_preserved_verbatim():
    """Screen 8 reflects Screen 11's codes; it never re-interprets or drops one."""
    gate = evaluate_gate(_allow(
        policy_decision=rgc.POLICY_DENY,
        policy_reason_codes=('COMPLIANCE_APPROVAL_MISSING', 'SETTLEMENT_NOT_CLEARED'),
    ))
    assert 'COMPLIANCE_APPROVAL_MISSING' in gate.reason_codes
    assert 'SETTLEMENT_NOT_CLEARED' in gate.reason_codes


def test_policy_version_mismatch_locks_the_gate():
    """A verdict produced under superseded rules is not a current authorization."""
    gate = evaluate_gate(_allow(policy_version=7, policy_current_version=8))
    assert gate.can_execute is False
    assert rgc.POLICY_VERSION_MISMATCH in gate.reason_codes


def test_unreadable_policy_decision_is_never_an_allow():
    gate = evaluate_gate(_allow(policy_decision='SOMETHING_ELSE'))
    assert gate.policy_decision == rgc.POLICY_NOT_EVALUATED
    assert gate.can_execute is False


# ─────────────────────────────────────────────────────────────────────────────
# 3-4. Quorum.
# ─────────────────────────────────────────────────────────────────────────────
def test_allow_with_insufficient_quorum_remains_locked():
    """The acceptance scenario: 2 of 3 roles signed, execution stays locked."""
    gate = evaluate_gate(_allow(
        required_roles=(TREASURY, COMPLIANCE),
        approvals=(_approved('u1', TREASURY),),
        required_quorum=2,
    ))
    assert gate.can_execute is False
    assert gate.decision == rgc.GATE_LOCKED
    assert gate.approvals_collected == 1
    assert gate.required_quorum == 2
    assert gate.missing_roles == (COMPLIANCE,)
    assert rgc.REQUIRED_ROLE_MISSING in gate.reason_codes
    assert gate.as_dict()['missing_role_labels'] == ['Compliance Approver']


def test_quorum_satisfied_makes_the_action_executable():
    gate = evaluate_gate(_allow(
        required_roles=(TREASURY, COMPLIANCE),
        approvals=(_approved('u1', TREASURY), _approved('u2', COMPLIANCE)),
        required_quorum=2,
    ))
    assert gate.can_execute is True
    assert gate.decision == rgc.GATE_AUTHORIZED
    assert gate.missing_roles == ()
    assert gate.reason_codes[0] == rgc.EXECUTION_AUTHORIZED


def test_one_person_cannot_satisfy_two_required_roles():
    """A single approver's single decision covers exactly the role it named."""
    gate = evaluate_gate(_allow(
        required_roles=(TREASURY, COMPLIANCE),
        approvals=(_approved('same-user', TREASURY),),
        required_quorum=2,
    ))
    assert gate.can_execute is False
    assert gate.missing_roles == (COMPLIANCE,)
    assert gate.approvals_collected == 1


def test_duplicate_approval_does_not_increase_quorum():
    """Two rows from the SAME approver count once (the store's unique index makes
    this unreachable in production; the engine is defensive anyway)."""
    gate = evaluate_gate(_allow(
        approvals=(_approved('u1', None), _approved('u1', None)),
        required_quorum=2,
    ))
    assert gate.approvals_collected == 1
    assert gate.can_execute is False
    assert rgc.HUMAN_QUORUM_INCOMPLETE in gate.reason_codes


def test_role_less_approvals_count_only_toward_the_numeric_quorum():
    gate = evaluate_gate(_allow(
        approvals=(_approved('u1', None), _approved('u2', None)),
        required_quorum=2,
    ))
    assert gate.can_execute is True
    assert gate.approvals_collected == 2


def test_required_quorum_is_never_smaller_than_the_required_role_count():
    gate = evaluate_gate(_allow(required_roles=(TREASURY, COMPLIANCE), required_quorum=1))
    assert gate.required_quorum == 2


# ─────────────────────────────────────────────────────────────────────────────
# 5-10. Rejection, expiry, terminal states, incident state.
# ─────────────────────────────────────────────────────────────────────────────
def test_rejection_locks_the_action():
    gate = evaluate_gate(_allow(rejected=True, lifecycle_approval_status='rejected'))
    assert gate.can_execute is False
    assert rgc.APPROVAL_REJECTED in gate.reason_codes


def test_expired_action_cannot_execute():
    gate = evaluate_gate(_allow(expires_at=(NOW - timedelta(minutes=1)).isoformat()))
    assert gate.can_execute is False
    assert rgc.ACTION_EXPIRED in gate.reason_codes


def test_action_with_a_future_expiry_is_not_expired():
    gate = evaluate_gate(_allow(expires_at=(NOW + timedelta(hours=1)).isoformat()))
    assert gate.can_execute is True
    assert rgc.ACTION_EXPIRED not in gate.reason_codes


def test_absent_expiry_is_an_authored_no_expiry_not_an_unknown():
    gate = evaluate_gate(_allow(expires_at=None))
    assert gate.can_execute is True


def test_cancelled_action_cannot_execute():
    gate = evaluate_gate(_allow(action_status='canceled'))
    assert gate.can_execute is False
    assert rgc.ACTION_CANCELLED in gate.reason_codes


def test_already_executed_action_cannot_execute_again():
    gate = evaluate_gate(_allow(execution_status='executed'))
    assert gate.can_execute is False
    assert rgc.ACTION_ALREADY_EXECUTED in gate.reason_codes


@pytest.mark.parametrize('closed_state', ['resolved', 'suppressed', 'closed'])
def test_closed_incident_blocks_response_execution(closed_state):
    gate = evaluate_gate(_allow(incident_status=closed_state))
    assert gate.can_execute is False
    assert rgc.INCIDENT_CLOSED in gate.reason_codes


def test_rbac_forbidden_requester_cannot_execute():
    gate = evaluate_gate(_allow(requester_authorized=False))
    assert gate.can_execute is False
    assert rgc.RBAC_FORBIDDEN in gate.reason_codes


# ─────────────────────────────────────────────────────────────────────────────
# 18. Execution adapter honesty — an authorized gate never claims a submitted
#     transaction the deployment cannot make.
# ─────────────────────────────────────────────────────────────────────────────
def test_missing_execution_adapter_is_reported_but_is_not_an_authorization_failure():
    gate = evaluate_gate(_allow(execution_adapter_configured=False))
    assert gate.can_execute is True
    assert gate.execution_adapter_configured is False
    assert rgc.EXECUTION_ADAPTER_NOT_CONFIGURED in gate.reason_codes


def test_authorized_gate_is_never_an_empty_payload():
    gate = evaluate_gate(_allow())
    assert gate.reason_codes == (rgc.EXECUTION_AUTHORIZED,)
    assert gate.as_dict()['decision_label'] == 'Execution Authorized'


# ─────────────────────────────────────────────────────────────────────────────
# Command-level enforcement (fake connection, repository style).
# ─────────────────────────────────────────────────────────────────────────────
class _Result:
    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def fetchone(self):
        return self._row

    def fetchall(self):
        return self._rows


class _GateConn:
    """A connection that answers the gate's reads: the action row, the policy
    evaluation Screen 11 recorded, the approval decisions, and the incident."""

    def __init__(self, *, action_row, evaluation=None, approvals=None,
                 incident_status='investigating', policy_version=None, tables=None):
        self.executed: list[tuple[str, object]] = []
        self.committed = 0
        self._action_row = action_row
        self._evaluation = evaluation
        self._approvals = approvals or []
        self._incident_status = incident_status
        self._policy_version = policy_version
        self._tables = tables if tables is not None else {
            'public.governance_policy_evaluations', 'public.governance_policies',
            'public.response_action_approvals',
        }

    def execute(self, statement, params=None):
        n = ' '.join(str(statement).split())
        self.executed.append((n, params))
        if 'to_regclass' in n:
            return _Result(row={'present': str(params[0]) in self._tables})
        if 'information_schema.columns' in n:
            return _Result(row={'present': True})
        if 'FROM response_actions WHERE id = %s AND workspace_id = %s' in n or \
           'FROM response_actions WHERE id = %s::uuid AND workspace_id = %s' in n:
            ws = str(params[1])
            return _Result(row=self._action_row if ws == WORKSPACE else None)
        if 'FROM governance_policy_evaluations' in n:
            return _Result(row=self._evaluation)
        if 'FROM governance_policies WHERE id' in n:
            return _Result(row={'version': self._policy_version} if self._policy_version else None)
        if 'FROM governance_policies' in n:
            return _Result(row={'present': True} if self._evaluation else None)
        if 'FROM response_action_approvals' in n:
            return _Result(rows=list(self._approvals))
        if 'FROM incidents WHERE id' in n:
            return _Result(row={'status': self._incident_status})
        return _Result()

    def commit(self):
        self.committed += 1


def _live_action_row(**overrides):
    row = {
        'id': ACTION_ID,
        'status': 'pending',
        'mode': 'simulated',
        'action_type': 'pause_mint_redeem',
        'execution_metadata': {},
        'execution_state': 'proposed',
        'incident_id': INCIDENT_ID,
        'alert_id': None,
        'approved_by_user_id': 'approver-1',
        'created_by_user_id': 'proposer-1',
    }
    row.update(overrides)
    return row


def _patch_common(monkeypatch, connection, role='admin'):
    @contextmanager
    def _fake_pg():
        yield connection

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, 'log_audit', lambda *_a, **_k: None)
    monkeypatch.setattr(
        pilot, '_require_workspace_permission',
        lambda *_a, **_k: ({'id': 'operator-1', 'mfa_enabled': False},
                           {'workspace_id': WORKSPACE, 'role': role}),
    )


DENY_EVALUATION = {
    'id': 'eval-deny-1',
    'policy_id': 'pol-1',
    'policy_key': 'POL-MINT-007',
    'policy_version': 7,
    'decision': 'DENY',
    'reason_codes': ['COMPLIANCE_APPROVAL_MISSING'],
    'required_approvals': [COMPLIANCE],
    'asset_id': None,
    'incident_id': INCIDENT_ID,
    'canonical_event_id': 'EVT-928181',
    'operation': 'MINT',
    'evaluated_at': '2026-09-01T10:42:18+00:00',
}


def test_direct_execute_request_is_rejected_when_the_gate_is_locked(monkeypatch):
    """§21 — the API attempt that skips the UI entirely.

    Frontend disabling is not authorization: the command re-evaluates the gate
    and refuses, with machine-readable reason codes in the response body.
    """
    connection = _GateConn(action_row=_live_action_row(), evaluation=DENY_EVALUATION, policy_version=7)
    _patch_common(monkeypatch, connection)
    request = SimpleNamespace(headers={'x-workspace-id': WORKSPACE})

    with pytest.raises(HTTPException) as exc:
        pilot.execute_enforcement_action(ACTION_ID, request)

    assert exc.value.status_code == 409
    assert exc.value.detail['code'] == 'EXECUTION_GATE_LOCKED'
    assert rgc.POLICY_DENIED in exc.value.detail['reason_codes']
    assert exc.value.detail['execution_gate']['policy_decision'] == 'DENY'
    assert exc.value.detail['execution_gate']['can_execute'] is False
    # No execution state was written for a refused attempt.
    assert not any('SET status = \'executed\'' in stmt for stmt, _ in connection.executed)


def test_locked_execution_attempt_writes_an_audit_and_timeline_event(monkeypatch):
    """§13 — a refused execution is recorded, never silent."""
    connection = _GateConn(action_row=_live_action_row(), evaluation=DENY_EVALUATION, policy_version=7)
    _patch_common(monkeypatch, connection)
    request = SimpleNamespace(headers={'x-workspace-id': WORKSPACE})

    with pytest.raises(HTTPException):
        pilot.execute_enforcement_action(ACTION_ID, request)

    history = [p for stmt, p in connection.executed if 'INSERT INTO action_history' in stmt]
    assert any(p[6] == 'response_action.execution_gate_locked' for p in history)
    timeline = [p for stmt, p in connection.executed if 'INSERT INTO incident_timeline' in stmt]
    assert any(p[3] == 'response_action.execution_gate_locked' for p in timeline)


def test_execute_is_blocked_while_a_required_role_has_not_signed(monkeypatch):
    """ALLOW + an outstanding required role is still locked."""
    allow_evaluation = {**DENY_EVALUATION, 'decision': 'ALLOW',
                        'reason_codes': ['POLICY_SATISFIED'],
                        'required_approvals': [TREASURY, COMPLIANCE]}
    connection = _GateConn(
        action_row=_live_action_row(), evaluation=allow_evaluation, policy_version=7,
        approvals=[{'approver_user_id': 'u1', 'approver_role': 'admin',
                    'decision': 'approved', 'created_at': '2026-09-01T10:43:02+00:00',
                    'approval_role': TREASURY}],
    )
    _patch_common(monkeypatch, connection)
    request = SimpleNamespace(headers={'x-workspace-id': WORKSPACE})

    with pytest.raises(HTTPException) as exc:
        pilot.execute_enforcement_action(ACTION_ID, request)

    gate = exc.value.detail['execution_gate']
    assert gate['policy_decision'] == 'ALLOW'
    assert gate['missing_roles'] == [COMPLIANCE]
    assert gate['approvals_collected'] == 1
    assert gate['required_quorum'] == 2
    assert rgc.REQUIRED_ROLE_MISSING in exc.value.detail['reason_codes']


def test_execute_proceeds_once_every_required_role_has_signed(monkeypatch):
    """§21 — after the outstanding approver signs, the gate re-evaluates to
    can_execute and the command is no longer refused by the gate."""
    allow_evaluation = {**DENY_EVALUATION, 'decision': 'ALLOW',
                        'reason_codes': ['POLICY_SATISFIED'],
                        'required_approvals': [TREASURY, COMPLIANCE]}
    connection = _GateConn(
        action_row=_live_action_row(), evaluation=allow_evaluation, policy_version=7,
        approvals=[
            {'approver_user_id': 'u1', 'approver_role': 'admin', 'decision': 'approved',
             'created_at': '2026-09-01T10:43:02+00:00', 'approval_role': TREASURY},
            {'approver_user_id': 'u2', 'approver_role': 'owner', 'decision': 'approved',
             'created_at': '2026-09-01T10:43:17+00:00', 'approval_role': COMPLIANCE},
        ],
    )
    _patch_common(monkeypatch, connection)
    request = SimpleNamespace(headers={'x-workspace-id': WORKSPACE})

    payload = pilot.execute_enforcement_action(ACTION_ID, request)
    assert payload['status'] == 'executed'

    # §13 — crossing the trust boundary is itself an audited transition, carrying
    # the policy that decided, its version, and the quorum that was satisfied.
    history = [p for stmt, p in connection.executed if 'INSERT INTO action_history' in stmt]
    authorized = [p for p in history if p[6] == 'response_action.execution_gate_authorized']
    assert authorized, 'the authorizing gate must be recorded'
    detail = str(authorized[0][7])
    assert 'POL-MINT-007' in detail
    assert 'Deterministic Policy Engine' in detail


def test_execution_gate_view_is_workspace_scoped(monkeypatch):
    """§17 — an action in another workspace is a 404, never a gate."""
    connection = _GateConn(action_row=_live_action_row(), evaluation=DENY_EVALUATION)
    _patch_common(monkeypatch, connection)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_a, **_k: {'id': 'operator-1'})
    monkeypatch.setattr(
        pilot, 'resolve_workspace',
        lambda *_a, **_k: {'workspace_id': OTHER_WORKSPACE, 'role': 'admin'},
    )
    request = SimpleNamespace(headers={'x-workspace-id': OTHER_WORKSPACE})

    with pytest.raises(HTTPException) as exc:
        pilot.response_action_execution_gate_view(ACTION_ID, request)
    assert exc.value.status_code == 404


def test_execution_gate_view_returns_the_normalized_gate(monkeypatch):
    connection = _GateConn(action_row=_live_action_row(), evaluation=DENY_EVALUATION, policy_version=7)
    _patch_common(monkeypatch, connection)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_a, **_k: {'id': 'operator-1'})
    monkeypatch.setattr(
        pilot, 'resolve_workspace',
        lambda *_a, **_k: {'workspace_id': WORKSPACE, 'role': 'admin'},
    )
    payload = pilot.response_action_execution_gate_view(
        ACTION_ID, SimpleNamespace(headers={'x-workspace-id': WORKSPACE}),
    )
    gate = payload['execution_gate']
    assert gate['decision'] == 'DENIED'
    assert gate['can_execute'] is False
    assert gate['policy_key'] == 'POL-MINT-007'
    assert gate['policy_version'] == 7
    assert gate['ai_authority'] == 'Recommend only'
    assert gate['execution_authority'] == 'Deterministic Policy Engine'
    # Traceable to the canonical event lifecycle, not a parallel demo object.
    assert gate['chain_linked_ids']['incident_id'] == INCIDENT_ID
    assert gate['chain_linked_ids']['action_id'] == ACTION_ID


def test_gate_fails_closed_when_a_read_raises(monkeypatch):
    """Any unreadable fact leaves the lock shut — never an accidental ALLOW."""
    class _Boom:
        def execute(self, *_a, **_k):
            raise RuntimeError('database unavailable')

    gate = pilot.response_action_execution_gate(
        _Boom(), _live_action_row(), workspace_id=WORKSPACE,
        workspace_context={'role': 'admin'},
    )
    assert gate['can_execute'] is False
    assert gate['decision'] == rgc.GATE_LOCKED
    assert gate['policy_decision'] == rgc.POLICY_NOT_EVALUATED


def test_an_unreadable_fact_is_not_the_same_answer_as_an_absent_one():
    """A read that FAILED closes the gate, even when every other condition looks
    satisfied — otherwise a database outage would silently widen authorization."""
    satisfied_but_unread = evaluate_gate(_allow(unreadable_facts=('approvals',)))
    assert satisfied_but_unread.can_execute is False
    assert rgc.GATE_FACTS_UNAVAILABLE in satisfied_but_unread.reason_codes
    # The same context with every fact READ is authorized, so the lock above is
    # attributable to the unreadable fact and nothing else.
    assert evaluate_gate(_allow()).can_execute is True


# ─────────────────────────────────────────────────────────────────────────────
# Role-scoped approval: the client may NAME a role, never assert it.
# ─────────────────────────────────────────────────────────────────────────────
class _RoleConn:
    def __init__(self, *, granted: bool):
        self._granted = granted
        self.executed: list[str] = []

    def execute(self, statement, params=None):
        n = ' '.join(str(statement).split())
        self.executed.append(n)
        if 'FROM workspace_role_permissions' in n:
            return _Result(row={'granted': self._granted})
        return _Result()


def test_approval_role_must_be_evidenced_by_the_callers_own_permission(monkeypatch):
    """A crafted payload cannot close a quorum the operator was not entitled to
    close: the named role is checked against the caller's membership."""
    connection = _RoleConn(granted=False)
    with pytest.raises(HTTPException) as exc:
        pilot._resolve_approval_role(
            connection, workspace_id=WORKSPACE,
            workspace_context={'role': 'viewer'},
            payload={'approval_role': COMPLIANCE},
        )
    assert exc.value.status_code == 403
    assert exc.value.detail['code'] == 'APPROVAL_ROLE_NOT_HELD'


def test_approval_role_is_accepted_when_the_caller_holds_the_permission():
    connection = _RoleConn(granted=True)
    assert pilot._resolve_approval_role(
        connection, workspace_id=WORKSPACE,
        workspace_context={'role': 'admin'},
        payload={'approval_role': COMPLIANCE},
    ) == COMPLIANCE


def test_unknown_approval_role_is_rejected():
    connection = _RoleConn(granted=True)
    with pytest.raises(HTTPException) as exc:
        pilot._resolve_approval_role(
            connection, workspace_id=WORKSPACE,
            workspace_context={'role': 'admin'},
            payload={'approval_role': 'SUPREME_LEADER'},
        )
    assert exc.value.status_code == 400
    assert exc.value.detail['code'] == 'UNKNOWN_APPROVAL_ROLE'


def test_absent_approval_role_stays_role_agnostic():
    """The pre-existing role-less approval keeps working (it counts toward the
    numeric quorum only) — nothing is inferred for it."""
    connection = _RoleConn(granted=True)
    assert pilot._resolve_approval_role(
        connection, workspace_id=WORKSPACE,
        workspace_context={'role': 'admin'}, payload=None,
    ) is None
    assert pilot._resolve_approval_role(
        connection, workspace_id=WORKSPACE,
        workspace_context={'role': 'admin'}, payload={},
    ) is None
