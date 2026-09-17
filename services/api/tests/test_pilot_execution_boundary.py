"""Pilot is Recommend-only as a BACKEND security boundary, not a disabled button.

The invariant under test: a workspace on the Pilot plan may observe, investigate,
simulate, recommend and produce evidence — and cannot cause a production
blockchain state change by any route. It holds when the frontend is bypassed,
when the API is called directly, when a worker or queue consumer picks up a job,
when an AI agent recommends a run, when policy approval has fully succeeded, and
when the caller is a Founder or Admin.

What these tests pin down, in the order the request travels:

    1   API             a live execute call is refused 403 PILOT_EXECUTION_DISABLED
    2   identity        a Founder/Admin gets the SAME refusal — the role is not an input
    3   service layer   a direct service call is refused without going near HTTP
    4   worker / queue  a job that reaches a worker is refused by the same policy
    5   AI              a recommendation is produced and still cannot execute
    6   policy          a fully AUTHORIZED gate is still refused for a Pilot tenant
    7-10 capability     simulate / incidents / recommendations / evidence stay ON
    11  non-Pilot       an entitled tenant keeps its existing execution behaviour
    12  fail-closed     an unknown, unlinked or unreadable tenant does not execute
    13  provider        the two write-capable provider calls verify a grant issued
                        for THAT workspace and THAT action, so no upstream caller
                        can reach them by forgetting a check
    14  credentials     a refused run never causes signer material to be read
    15  audit           a refusal records ``pilot_execution_blocked`` with machine
                        facts only — no key, no seed phrase, no token

Run:
    python -m pytest services/api/tests/test_pilot_execution_boundary.py -q
"""

from __future__ import annotations

import inspect
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from services.api.app import ai_triage
from services.api.app import entitlements as ent
from services.api.app import execution_authorization as execution_authz
from services.api.app import pilot
from services.api.app.domains.response_gate import config as rgc

#: The lock reads the REAL clock (it is a live-run capability check, not a
#: replay), so an evaluation window is anchored to wall time. A hard-coded date
#: would quietly age into an EXPIRED evaluation and these tests would stop
#: testing the case they name.
NOW = datetime.now(timezone.utc)
WS = 'ws-pilot-1'
OTHER_WS = 'ws-pilot-2'


# ── fakes ────────────────────────────────────────────────────────────────────
class _Result:
    def __init__(self, row: Any = None) -> None:
        self._row = row

    def fetchone(self) -> Any:
        return self._row

    def fetchall(self) -> list[Any]:
        return [] if self._row is None else [self._row]


class _Conn:
    """A workspace-scoped fake: one organization, and a log of every statement.

    Records executed SQL so a test can assert what was WRITTEN (the audit event)
    rather than only what was returned.
    """

    def __init__(
        self,
        organization: dict[str, Any] | None,
        *,
        schema_ready: bool = True,
        fail: bool = False,
        action: dict[str, Any] | None = None,
    ) -> None:
        self.organization = organization
        self.schema_ready = schema_ready
        self.fail = fail
        self.action = action
        self.statements: list[tuple[str, Any]] = []
        self.commits = 0

    def execute(self, query: str, params: Any = None) -> _Result:
        sql = ' '.join(str(query).split())
        self.statements.append((sql, params))
        lowered = sql.lower()
        if 'information_schema.tables' in lowered:
            if self.fail:
                raise RuntimeError('database unavailable')
            return _Result({'table_count': 2 if self.schema_ready else 0,
                            'link_count': 1 if self.schema_ready else 0})
        if 'join organizations o on o.id = w.organization_id' in lowered:
            if self.fail:
                raise RuntimeError('database unavailable')
            return _Result(dict(self.organization) if self.organization else None)
        if 'from response_actions where id = %s and workspace_id = %s' in lowered:
            return _Result(dict(self.action) if self.action else None)
        if 'select role from workspace_members' in lowered:
            return _Result({'role': 'owner'})
        return _Result()

    def commit(self) -> None:
        self.commits += 1

    # -- helpers ------------------------------------------------------------
    def written(self, needle: str) -> list[tuple[str, Any]]:
        return [item for item in self.statements if needle in item[0]]


def _org(plan: str = ent.PLAN_PILOT, **overrides: Any) -> dict[str, Any]:
    row = {
        'id': 'org-1', 'name': 'Pilot Co', 'slug': 'pilot-co',
        'plan': plan, 'status': ent.STATUS_ACTIVE,
        'evaluation_started_at': NOW - timedelta(days=7),
        'evaluation_expires_at': NOW + timedelta(days=23),
        'entitlement_overrides': {},
        'created_at': NOW, 'updated_at': NOW,
    }
    row.update(overrides)
    return row


def _entitled_org() -> dict[str, Any]:
    """The ONLY shape that may execute: an explicit, audited entitlement override.

    Being Enterprise is deliberately not enough (see the plan table), so a test
    for "a non-Pilot tenant keeps executing" has to state the override rather
    than assume a plan name unlocks it.
    """
    return _org(
        plan=ent.PLAN_ENTERPRISE,
        evaluation_expires_at=None,
        entitlement_overrides={ent.FEATURE_AUTOMATIC_EXECUTION: True},
    )


def _live_action(**overrides: Any) -> dict[str, Any]:
    action = {
        'id': 'act-live-1',
        'workspace_id': WS,
        'status': 'approved',
        'mode': 'live',
        'action_type': 'revoke_approval',
        'incident_id': 'inc-1',
        'alert_id': 'alert-1',
        'execution_metadata': {},
        'token_contract': '0x1111111111111111111111111111111111111111',
        'calldata': '0x095ea7b3',
        'chain_network': 'ethereum',
        'approved_by_user_id': 'approver-1',
        'created_by_user_id': 'analyst-1',
    }
    action.update(overrides)
    return action


def _refusal(error: Any) -> dict[str, Any]:
    """The refusal body, whether it arrived as an HTTPException or the plain one."""
    detail = getattr(error, 'detail', None)
    return detail if isinstance(detail, dict) else error.as_dict()


@contextmanager
def _no_connection():  # pragma: no cover - only reached if a guard fails open
    raise AssertionError('execution reached the database after it should have been refused')
    yield


def _execute_api(
    monkeypatch: pytest.MonkeyPatch,
    connection: _Conn,
    *,
    role: str = 'owner',
    user_id: str = 'user-1',
) -> Any:
    """Call the real execute command end to end, minus auth and schema setup."""
    @contextmanager
    def _fake_pg():
        yield connection

    monkeypatch.setattr(pilot, 'require_live_mode', lambda: None)
    monkeypatch.setattr(pilot, 'ensure_pilot_schema', lambda *_a, **_k: None)
    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(
        pilot, '_require_workspace_permission',
        lambda *_a, **_k: ({'id': user_id, 'mfa_enabled': False}, {'workspace_id': WS, 'role': role}),
    )
    monkeypatch.setenv('LIVE_ACTION_EXECUTION_ENABLED', 'true')
    request = SimpleNamespace(headers={'x-workspace-id': WS, 'x-request-id': 'req-1'}, client=None)
    with pytest.raises(Exception) as exc_info:
        pilot.execute_enforcement_action('act-live-1', request)
    return exc_info.value


# ── 1 — a Pilot user calling the execution API is denied ─────────────────────

def test_1_pilot_user_calling_the_execute_api_is_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    connection = _Conn(_org(), action=_live_action())
    error = _execute_api(monkeypatch, connection)
    body = _refusal(error)
    assert getattr(error, 'status_code', None) == 403
    assert body['code'] == execution_authz.CODE_PILOT_EXECUTION_DISABLED
    assert body['reason'] == execution_authz.REASON_PLAN_RECOMMEND_ONLY
    assert body['plan'] == ent.PLAN_PILOT
    assert 'Pilot' in body['message']


def test_1b_the_user_safe_message_names_what_pilot_can_still_do() -> None:
    message = execution_authz.reason_message(execution_authz.REASON_PLAN_RECOMMEND_ONLY)
    for capability in ('monitoring', 'investigation', 'simulation', 'recommendations', 'evidence'):
        assert capability in message


# ── 2 — Founder / Admin privileges do not override the restriction ───────────

@pytest.mark.parametrize('role', ['owner', 'admin', 'founder', 'internal_admin'])
def test_2_a_privileged_caller_gets_the_same_refusal(
    role: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _Conn(_org(), action=_live_action())
    body = _refusal(_execute_api(monkeypatch, connection, role=role))
    assert body['code'] == execution_authz.CODE_PILOT_EXECUTION_DISABLED


def test_2b_the_decision_never_learns_who_the_caller_is() -> None:
    """Structural: no caller identity is a parameter of the policy."""
    for function in (execution_authz.decide, execution_authz.resolve_execution_lock,
                     execution_authz.assert_execution_allowed):
        parameters = set(inspect.signature(function).parameters)
        assert not parameters & {'user', 'user_id', 'role', 'is_internal_admin', 'request'}


# ── 3 — the service layer refuses without any HTTP involved ──────────────────

def test_3_direct_service_layer_execution_is_denied() -> None:
    connection = _Conn(_org())
    with pytest.raises(execution_authz.ExecutionForbidden) as exc_info:
        execution_authz.assert_execution_allowed(
            connection, workspace_id=WS, action=_live_action(),
            source=execution_authz.SOURCE_SERVICE,
        )
    error = exc_info.value
    assert error.code == execution_authz.CODE_PILOT_EXECUTION_DISABLED
    assert error.status_code == 403
    assert error.action_id == 'act-live-1'
    assert error.action_type == 'revoke_approval'


def test_3b_an_entitled_tenant_receives_a_grant_for_that_action() -> None:
    grant = execution_authz.assert_execution_allowed(
        _Conn(_entitled_org()), workspace_id=WS, action=_live_action(),
        source=execution_authz.SOURCE_SERVICE,
    )
    assert grant.workspace_id == WS
    assert grant.action_id == 'act-live-1'
    assert grant.matches(workspace_id=WS, action_id='act-live-1')


# ── 4 — a worker / queue job hits the same policy ────────────────────────────

@pytest.mark.parametrize('source', [
    execution_authz.SOURCE_WORKER,
    execution_authz.SOURCE_QUEUE,
    execution_authz.SOURCE_AGENT,
    execution_authz.SOURCE_SCRIPT,
])
def test_4_a_job_reaching_a_worker_is_denied_and_names_its_source(source: str) -> None:
    with pytest.raises(execution_authz.ExecutionForbidden) as exc_info:
        execution_authz.assert_execution_allowed(
            _Conn(_org()), workspace_id=WS, action=_live_action(), source=source,
        )
    assert exc_info.value.source == source


def test_4b_the_guard_imports_without_the_request_stack() -> None:
    """A worker must be able to call the policy without importing ``pilot``.

    ``pilot`` pulls in FastAPI, the database layer and the whole request path;
    a policy a worker cannot cheaply import is a policy a worker will skip.
    """
    import services.api.app.execution_authorization as module
    source = inspect.getsource(module)
    assert 'import pilot' not in source
    assert '\nfrom fastapi import' not in source  # only a guarded, local import


# ── 5 — AI may recommend; it may never execute ───────────────────────────────

def test_5_ai_recommendations_are_allowed_while_execution_is_denied() -> None:
    entitlements = ent.effective_entitlements(_org())
    assert ent.has_entitlement(entitlements, ent.FEATURE_RESPONSE_RECOMMENDATIONS) is True
    assert ent.has_entitlement(entitlements, ent.FEATURE_AI_INVESTIGATION) is True
    assert ent.has_entitlement(entitlements, ent.FEATURE_AUTOMATIC_EXECUTION) is False
    with pytest.raises(execution_authz.ExecutionForbidden):
        execution_authz.assert_execution_allowed(
            _Conn(_org()), workspace_id=WS, action=_live_action(),
            source=execution_authz.SOURCE_AGENT,
        )


def test_5b_no_ai_recommendable_action_is_a_production_state_change() -> None:
    """The AI action catalog cannot even NAME a state-changing operation."""
    forbidden = {'sign_transaction', 'transfer_funds', 'pause_contract',
                 'upgrade_contract', 'change_admin', 'freeze_wallet'}
    assert forbidden <= set(ai_triage.PROHIBITED_ACTION_TYPES)
    assert not (set(ai_triage.ALLOWED_ACTION_TYPES) & forbidden)


def test_5c_the_gate_forbids_an_ai_layer_from_unlocking_execution() -> None:
    assert 'execute_action' in rgc.AI_PROHIBITED
    assert 'unlock_execution' in rgc.AI_PROHIBITED
    assert rgc.AI_AUTHORITY_MODE == 'RECOMMEND_ONLY'


# ── 6 — a fully authorized gate is still refused for a Pilot tenant ──────────

def _authorized_gate() -> dict[str, Any]:
    return {
        'decision': rgc.GATE_AUTHORIZED,
        'decision_label': rgc.GATE_DECISION_LABELS[rgc.GATE_AUTHORIZED],
        'can_execute': True,
        'authorization_decision': rgc.GATE_AUTHORIZED,
        'execution_ready': True,
        'policy_decision': rgc.POLICY_ALLOW,
        'required_quorum': 1,
        'approvals_collected': 1,
        'reason_codes': [rgc.EXECUTION_AUTHORIZED],
        'reasons': [{'code': rgc.EXECUTION_AUTHORIZED, 'label': rgc.reason_label(rgc.EXECUTION_AUTHORIZED)}],
    }


def test_6_policy_allow_and_a_complete_quorum_still_do_not_execute() -> None:
    gate = pilot._apply_plan_execution_lock(
        _Conn(_org()), _authorized_gate(), action=_live_action(), workspace_id=WS,
    )
    # The AUTHORIZATION verdict is left standing — the tenant is simply not
    # capable of running it. An operator is never told policy denied something
    # policy allowed.
    assert gate['policy_decision'] == rgc.POLICY_ALLOW
    assert rgc.EXECUTION_AUTHORIZED in gate['reason_codes']
    assert gate['plan_execution_locked'] is True
    assert gate['can_execute'] is False
    assert rgc.PLAN_EXECUTION_NOT_ENTITLED in gate['reason_codes']


def test_6b_an_approved_pilot_action_is_refused_by_the_execute_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Approval succeeded, the approver is an owner, the adapter is configured —
    and the run is still refused, on the tenant code rather than a gate code."""
    connection = _Conn(_org(), action=_live_action(status='approved'))
    body = _refusal(_execute_api(monkeypatch, connection))
    assert body['code'] == execution_authz.CODE_PILOT_EXECUTION_DISABLED


# ── 7-10 — everything Pilot IS for keeps working ─────────────────────────────

def test_7_pilot_can_still_simulate() -> None:
    action = _live_action(mode='simulated')
    assert execution_authz.is_production_execution(action) is False
    gate = pilot._apply_plan_execution_lock(
        _Conn(_org()), _authorized_gate(), action=action, workspace_id=WS,
    )
    assert gate['plan_execution_locked'] is False
    assert gate['can_execute'] is True


def test_7b_a_recommended_action_is_not_a_production_execution() -> None:
    gate = pilot._apply_plan_execution_lock(
        _Conn(_org()), _authorized_gate(), action=_live_action(mode='recommended'), workspace_id=WS,
    )
    assert gate['plan_execution_locked'] is False
    assert gate['can_execute'] is True


def test_8_pilot_can_still_create_incidents() -> None:
    """Incidents are not plan-gated at all — they are SOURCE_ALWAYS in the matrix."""
    rows = {row['capability']: row for row in ent.capability_matrix()}
    assert rows['Incidents'][ent.MATRIX_ACTIVE_PILOT] == ent.MATRIX_YES
    assert rows['Alerts'][ent.MATRIX_ACTIVE_PILOT] == ent.MATRIX_YES
    assert rows['Threat detection'][ent.MATRIX_ACTIVE_PILOT] == ent.MATRIX_YES


def test_9_pilot_can_still_generate_recommendations() -> None:
    rows = {row['capability']: row for row in ent.capability_matrix()}
    assert rows['Response recommendations'][ent.MATRIX_ACTIVE_PILOT] == ent.MATRIX_YES
    assert rows['AI investigation'][ent.MATRIX_ACTIVE_PILOT] == ent.MATRIX_YES
    assert rows['Incident playbooks'][ent.MATRIX_ACTIVE_PILOT] == ent.MATRIX_YES


def test_10_pilot_can_still_export_evidence() -> None:
    rows = {row['capability']: row for row in ent.capability_matrix()}
    assert rows['Audit-ready exports'][ent.MATRIX_ACTIVE_PILOT] == ent.MATRIX_YES
    assert rows['Evidence workflows'][ent.MATRIX_ACTIVE_PILOT] == ent.MATRIX_YES
    assert ent.has_entitlement(ent.effective_entitlements(_org()), ent.FEATURE_EVIDENCE_EXPORT) is True


def test_10b_production_execution_is_the_only_capability_the_matrix_withholds() -> None:
    """The boundary is narrow on purpose: exactly one row differs for an ACTIVE
    Pilot from what an entitled tenant gets, and it is the execution row."""
    withheld = [
        row['capability'] for row in ent.capability_matrix()
        if row['source'] == ent.SOURCE_FEATURE
        and row[ent.MATRIX_ACTIVE_PILOT] == ent.MATRIX_NO
        and row['enforced']
    ]
    assert withheld == ['Automatic production execution']


# ── 11 — a non-Pilot, entitled tenant keeps executing ────────────────────────

def test_11_an_entitled_tenant_is_not_locked() -> None:
    gate = pilot._apply_plan_execution_lock(
        _Conn(_entitled_org()), _authorized_gate(), action=_live_action(), workspace_id=WS,
    )
    assert gate['plan_execution_locked'] is False
    assert gate['can_execute'] is True
    assert gate['decision'] == rgc.GATE_AUTHORIZED


def test_11b_scale_and_enterprise_alone_do_not_unlock_execution() -> None:
    """Unchanged product rule: only an explicit, audited override unlocks it."""
    for plan in (ent.PLAN_SCALE, ent.PLAN_ENTERPRISE):
        lock = execution_authz.resolve_execution_lock(
            _Conn(_org(plan=plan, evaluation_expires_at=None)), WS,
        )
        assert lock['locked'] is True
        assert lock['reason'] == execution_authz.REASON_PLAN_RECOMMEND_ONLY


def test_11d_an_open_ended_pilot_still_cannot_execute_production_actions() -> None:
    """The line an open-ended Pilot does NOT cross.

    Removing the 30-day deadline changed WHEN a Pilot ends, not what it may do:
    execution is refused by the plan table, which never consulted the evaluation
    window in the first place. A Pilot with no deadline is refused exactly as a
    dated one is, and for the same reason.
    """
    open_ended = execution_authz.resolve_execution_lock(
        _Conn(_org(evaluation_expires_at=None)), WS,
    )
    dated = execution_authz.resolve_execution_lock(_Conn(_org()), WS)
    assert open_ended == dated
    assert open_ended['locked'] is True
    assert open_ended['reason'] == execution_authz.REASON_PLAN_RECOMMEND_ONLY
    assert open_ended['plan'] == ent.PLAN_PILOT


def test_11e_an_open_ended_pilot_keeps_its_plan_limits() -> None:
    """...and it is metered exactly as a dated Pilot is."""
    open_ended = ent.effective_entitlements(_org(evaluation_expires_at=None))
    dated = ent.effective_entitlements(_org())
    for key in ent.LIMIT_KEYS:
        assert ent.limit_for(open_ended, key) == ent.limit_for(dated, key)
    assert ent.limit_for(open_ended, ent.LIMIT_MONITORED_CONTRACTS) == 5


def test_11c_an_unmigrated_deployment_keeps_its_existing_behaviour() -> None:
    lock = execution_authz.resolve_execution_lock(_Conn(None, schema_ready=False), WS)
    assert lock['locked'] is False
    assert lock['reason'] == execution_authz.REASON_SCHEMA_NOT_MIGRATED


# ── 12 — an unknown / unreadable tenant fails safe ───────────────────────────

def test_12_an_unknown_plan_falls_back_to_the_strictest_plan() -> None:
    lock = execution_authz.resolve_execution_lock(_Conn(_org(plan='platinum-unlimited')), WS)
    assert lock['locked'] is True
    assert lock['plan'] == ent.PLAN_PILOT


def test_12b_a_workspace_with_no_organization_is_not_executable() -> None:
    lock = execution_authz.resolve_execution_lock(_Conn(None), WS)
    assert lock['locked'] is True
    assert lock['reason'] == execution_authz.REASON_ORGANIZATION_NOT_LINKED


def test_12c_an_entitlement_that_could_not_be_read_is_not_permission() -> None:
    lock = execution_authz.resolve_execution_lock(_Conn(None, fail=True), WS)
    assert lock['locked'] is True
    assert lock['reason'] == execution_authz.REASON_ENTITLEMENT_UNAVAILABLE


def test_12d_an_expired_or_suspended_tenant_is_refused_with_its_own_reason() -> None:
    expired = execution_authz.resolve_execution_lock(
        _Conn(_org(evaluation_expires_at=NOW - timedelta(days=1))), WS,
    )
    assert expired == {'locked': True, 'reason': execution_authz.REASON_EVALUATION_EXPIRED,
                       'plan': ent.PLAN_PILOT}
    suspended = execution_authz.resolve_execution_lock(
        _Conn(_org(status=ent.STATUS_SUSPENDED)), WS,
    )
    assert suspended['reason'] == execution_authz.REASON_ORGANIZATION_SUSPENDED


def test_12f_a_pilot_the_founder_ENDED_is_refused_as_an_expired_evaluation() -> None:
    """A Pilot with no deadline is stopped by ``status``, and reported that way.

    This is how an open-ended Pilot ends, so the reason code has to be the
    evaluation one — not "suspended", and certainly not an unlocked gate.
    """
    ended = execution_authz.resolve_execution_lock(
        _Conn(_org(evaluation_expires_at=None, status=ent.STATUS_EXPIRED)), WS,
    )
    assert ended == {'locked': True, 'reason': execution_authz.REASON_EVALUATION_EXPIRED,
                     'plan': ent.PLAN_PILOT}


def test_12e_every_lock_reason_carries_a_user_safe_sentence() -> None:
    for reason in execution_authz.LOCK_REASONS:
        assert execution_authz.reason_message(reason) != reason
        assert len(execution_authz.reason_message(reason)) > 40


# ── 13 — the provider boundary verifies, it does not trust ───────────────────

def test_13_the_safe_provider_refuses_a_call_with_no_grant() -> None:
    with pytest.raises(execution_authz.ExecutionForbidden):
        pilot._propose_safe_transaction(
            'act-live-1', to='0x1', data='0x2', chain_network='ethereum', workspace_id=WS,
        )


def test_13b_the_governance_provider_refuses_a_call_with_no_grant() -> None:
    with pytest.raises(execution_authz.ExecutionForbidden):
        pilot._submit_freeze_wallet_governance_action(
            _live_action(action_type='freeze_wallet'), {'workspace_id': WS}, {'id': 'user-1'},
        )


def test_13c_a_grant_from_another_tenant_cannot_be_replayed() -> None:
    borrowed = execution_authz.ExecutionAuthorization(
        workspace_id=OTHER_WS, plan=ent.PLAN_ENTERPRISE,
        source=execution_authz.SOURCE_SERVICE, action_id='act-live-1',
    )
    with pytest.raises(execution_authz.ExecutionForbidden):
        execution_authz.assert_provider_call_allowed(
            borrowed, workspace_id=WS, action_id='act-live-1', provider='safe',
        )


def test_13d_a_grant_for_another_action_cannot_be_reused() -> None:
    grant = execution_authz.ExecutionAuthorization(
        workspace_id=WS, plan=ent.PLAN_ENTERPRISE,
        source=execution_authz.SOURCE_SERVICE, action_id='act-other',
    )
    with pytest.raises(execution_authz.ExecutionForbidden):
        execution_authz.assert_provider_call_allowed(
            grant, workspace_id=WS, action_id='act-live-1', provider='governance',
        )


def test_13e_every_write_capable_provider_call_requires_a_grant() -> None:
    """Structural: a new caller cannot reach a provider by forgetting a check."""
    for function in (pilot._propose_safe_transaction, pilot._submit_freeze_wallet_governance_action):
        assert 'authorization' in inspect.signature(function).parameters
        assert 'assert_provider_call_allowed' in inspect.getsource(function)


# ── 14 — a refused run never reads signing material ──────────────────────────

def test_14_a_refused_safe_run_never_loads_the_signer(monkeypatch: pytest.MonkeyPatch) -> None:
    loaded: list[str] = []
    monkeypatch.setattr(pilot, '_safe_signer_key', lambda: loaded.append('read') or '0xkey')
    monkeypatch.setenv('SAFE_TX_SERVICE_URL', 'https://safe.example')
    monkeypatch.setenv('SAFE_WALLET_ADDRESS', '0x' + '1' * 40)
    with pytest.raises(execution_authz.ExecutionForbidden):
        pilot._propose_safe_transaction(
            'act-live-1', to='0x1', data='0x2', chain_network='ethereum', workspace_id=WS,
        )
    assert loaded == []


def test_14b_the_guard_runs_before_the_payload_is_assembled() -> None:
    """Structural: the authorization call precedes every env read that follows.

    Reads the EXECUTABLE body — the docstring names both, so comparing raw source
    positions would pass on prose rather than on order of execution.
    """
    source = inspect.getsource(pilot._propose_safe_transaction)
    body = source[source.index('"""', source.index('"""') + 3) + 3:]
    assert body.index('assert_provider_call_allowed') < body.index('SAFE_TX_SERVICE_URL')
    assert body.index('assert_provider_call_allowed') < body.index('_safe_signer_key')


def test_14c_pilot_cannot_configure_a_signer_because_none_is_workspace_scoped() -> None:
    """The signer is a DEPLOYMENT env var, never tenant state: there is no
    workspace-scoped write path that could store one."""
    source = inspect.getsource(pilot._safe_signer_key)
    assert 'workspace' not in source
    assert 'read_encrypted_env' in source


# ── 15 — the refusal is audited, with machine facts only ─────────────────────

def test_15_a_blocked_attempt_records_a_pilot_execution_blocked_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _Conn(_org(), action=_live_action())
    _execute_api(monkeypatch, connection)
    written = ' '.join(sql for sql, _ in connection.statements)
    assert 'action_history' in written
    assert 'audit_logs' in written or 'INSERT INTO audit_logs' in written
    payloads = [str(params) for _sql, params in connection.statements if params]
    assert any(execution_authz.AUDIT_EVENT_BLOCKED in item for item in payloads)


def test_15b_the_audit_metadata_is_machine_facts_and_no_secrets() -> None:
    error = execution_authz.ExecutionForbidden(
        reason=execution_authz.REASON_PLAN_RECOMMEND_ONLY, plan=ent.PLAN_PILOT,
        workspace_id=WS, action_id='act-live-1', action_type='freeze_wallet',
        source=execution_authz.SOURCE_WORKER,
    )
    metadata = execution_authz.blocked_audit_metadata(
        error, actor_id='user-1', request_id='req-1',
    )
    assert metadata['event_type'] == 'pilot_execution_blocked'
    for key in ('workspace_id', 'actor_id', 'action_type', 'source', 'occurred_at',
                'request_id', 'action_id', 'plan'):
        assert key in metadata
    banned = ('private_key', 'seed', 'mnemonic', 'signature', 'bearer', 'token_secret',
              'password', 'secret')
    rendered = str(metadata).lower()
    assert not any(word in rendered for word in banned)


def test_15c_audit_failure_never_turns_a_refusal_into_an_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _AuditBomb(_Conn):
        def execute(self, query: str, params: Any = None) -> _Result:
            if 'action_history' in str(query):
                raise RuntimeError('audit table unavailable')
            return super().execute(query, params)

    connection = _AuditBomb(_org(), action=_live_action())
    body = _refusal(_execute_api(monkeypatch, connection))
    assert body['code'] == execution_authz.CODE_PILOT_EXECUTION_DISABLED


# ── the vocabulary stays canonical ───────────────────────────────────────────

def test_the_refusal_code_is_stable_and_singular() -> None:
    assert execution_authz.CODE_PILOT_EXECUTION_DISABLED == 'PILOT_EXECUTION_DISABLED'
    assert execution_authz.ExecutionForbidden.status_code == 403


def test_screen_8_and_the_guard_read_one_definition_of_the_lock() -> None:
    """``pilot.plan_execution_lock`` must not re-derive the decision."""
    assert 'resolve_execution_lock' in inspect.getsource(pilot.plan_execution_lock)
    assert pilot.plan_execution_lock(_Conn(_org()), WS) == \
        execution_authz.resolve_execution_lock(_Conn(_org()), WS)


def test_the_plan_code_still_blocks_the_gate_as_a_backstop() -> None:
    assert rgc.PLAN_EXECUTION_NOT_ENTITLED in pilot._GATE_BLOCKING_REASON_CODES


# ── 16 — the compliance gateway is not the way around Recommend-only ─────────
# POST /compliance/governance/actions and POST /pilot/compliance/governance/actions
# submit a freeze / pause / block WITHOUT creating a response action, so the
# deterministic execution gate never sees them. They are the product's second
# production-write entry point and carry the same tenant boundary.

def _governance_request() -> SimpleNamespace:
    return SimpleNamespace(
        headers={'x-workspace-id': WS, 'x-request-id': 'req-gov-1'}, client=None,
    )


def _patch_governance_identity(
    monkeypatch: pytest.MonkeyPatch, connection: _Conn, *, workspace_id: str | None = WS,
) -> None:
    @contextmanager
    def _fake_pg():
        yield connection

    monkeypatch.setattr(pilot, 'pg_connection', _fake_pg)
    monkeypatch.setattr(pilot, 'authenticate_with_connection', lambda *_a, **_k: {'id': 'user-1'})
    if workspace_id is None:
        def _unresolvable(*_a: Any, **_k: Any) -> dict[str, Any]:
            raise RuntimeError('workspace could not be resolved')
        monkeypatch.setattr(pilot, 'resolve_workspace', _unresolvable)
    else:
        monkeypatch.setattr(
            pilot, 'resolve_workspace',
            lambda *_a, **_k: {'workspace_id': workspace_id, 'role': 'owner'},
        )


@pytest.mark.parametrize('action_type', sorted(pilot.STATE_CHANGING_GOVERNANCE_ACTION_TYPES))
def test_16_a_pilot_tenant_cannot_submit_a_governance_action(
    action_type: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    connection = _Conn(_org())
    _patch_governance_identity(monkeypatch, connection)
    with pytest.raises(Exception) as exc_info:
        pilot.require_governance_action_execution_allowed(
            {'action_type': action_type, 'target_type': 'wallet', 'target_id': '0xabc'},
            _governance_request(),
        )
    body = _refusal(exc_info.value)
    assert body['code'] == execution_authz.CODE_PILOT_EXECUTION_DISABLED
    assert body['action_type'] == action_type


def test_16b_an_entitled_tenant_may_still_submit_a_governance_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_governance_identity(monkeypatch, _Conn(_entitled_org()))
    pilot.require_governance_action_execution_allowed(
        {'action_type': 'freeze_wallet', 'target_type': 'wallet', 'target_id': '0xabc'},
        _governance_request(),
    )


def test_16c_an_unresolvable_workspace_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_governance_identity(monkeypatch, _Conn(_org()), workspace_id=None)
    with pytest.raises(Exception) as exc_info:
        pilot.require_governance_action_execution_allowed(
            {'action_type': 'freeze_wallet'}, _governance_request(),
        )
    body = _refusal(exc_info.value)
    assert body['code'] == execution_authz.CODE_PILOT_EXECUTION_DISABLED
    assert body['reason'] == execution_authz.REASON_ORGANIZATION_NOT_LINKED


def test_16d_an_unmigrated_deployment_keeps_its_gateway_behaviour(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_governance_identity(monkeypatch, _Conn(None, schema_ready=False))
    pilot.require_governance_action_execution_allowed(
        {'action_type': 'freeze_wallet'}, _governance_request(),
    )


def test_16e_an_unknown_governance_action_type_is_treated_as_a_write() -> None:
    """Fail closed on a verb this build does not know — the schema has no read verb."""
    assert pilot.governance_action_changes_state('some_new_upstream_verb') is True
    assert pilot.governance_action_changes_state('') is True
    for action_type in pilot.STATE_CHANGING_GOVERNANCE_ACTION_TYPES:
        assert pilot.governance_action_changes_state(action_type) is True


def test_16f_both_gateway_routes_apply_the_boundary() -> None:
    """Structural: neither route can contact the compliance service first."""
    import services.api.app.main as api_main

    for handler in (api_main.compliance_create_governance_action,
                    api_main.pilot_compliance_governance_action):
        source = inspect.getsource(handler)
        assert 'require_governance_action_execution_allowed' in source
        assert source.index('require_governance_action_execution_allowed') < \
            source.index("proxy_compliance('governance/actions'")


# ── 17 — regression sweep: no unguarded production-write path exists ─────────
# Phase 8 of the hardening, written as a test rather than a one-off search, so a
# NEW way to reach production has to come with its authorization or fail here.

import re  # noqa: E402  (kept beside the sweep it serves)
from pathlib import Path  # noqa: E402

APP_DIR = Path(__file__).resolve().parents[1] / 'app'

#: Modules allowed to name a signing / broadcasting primitive at all, with why.
_SIGNING_VOCABULARY_ALLOWLIST = {
    # The AI agent policy ENUMERATES these to forbid them; naming is the point.
    'ai_triage.py',
    # The refusal vocabulary and the redaction denylists name them so they can
    # never be logged or produced.
    'pilot.py',
    'structured_logging.py',
}

#: Primitives that would mean this product signs or broadcasts on chain itself.
_BROADCAST_PRIMITIVES = (
    'eth_sendRawTransaction',
    'eth_sendTransaction',
    'send_raw_transaction',
    'sendRawTransaction',
    'personal_sign',
    'eth_signTransaction',
)


def _app_sources() -> list[Path]:
    return sorted(path for path in APP_DIR.rglob('*.py') if '__pycache__' not in str(path))


def test_17_the_product_never_broadcasts_a_transaction_itself() -> None:
    """Decoda proposes to a multisig or a governance provider; it does not sign.

    A hit here means a new code path can put a transaction on chain directly,
    which is a different security model from the one this boundary assumes.
    """
    offenders: list[str] = []
    for path in _app_sources():
        text = path.read_text(encoding='utf-8')
        for primitive in _BROADCAST_PRIMITIVES:
            if primitive in text and path.name not in _SIGNING_VOCABULARY_ALLOWLIST:
                offenders.append(f'{path.name}:{primitive}')
    assert offenders == []


def test_17b_every_rpc_method_the_product_calls_is_read_only() -> None:
    """The JSON-RPC surface is a READ surface. A write method here would be a
    production action no plan gate covers."""
    write_methods = {'eth_sendtransaction', 'eth_sendrawtransaction', 'eth_sign',
                     'eth_signtransaction', 'personal_sendtransaction'}
    called: set[str] = set()
    for path in _app_sources():
        text = path.read_text(encoding='utf-8')
        called.update(match.lower() for match in re.findall(r"'method': '(eth_[a-zA-Z]+)'", text))
        called.update(match.lower() for match in re.findall(r"self\._rpc_call\('(eth_[a-zA-Z]+)'", text))
    assert called, 'the sweep found no RPC calls at all — the pattern has drifted'
    assert not (called & write_methods)


def test_17c_only_two_call_sites_contact_a_write_capable_provider() -> None:
    """Both are the guarded ones. A third would reach production ungated."""
    text = (APP_DIR / 'pilot.py').read_text(encoding='utf-8')
    assert text.count('/governance/actions') == 1
    assert text.count('multisig-transactions') == 1


def test_17d_no_worker_or_queue_module_can_reach_a_provider_call() -> None:
    """Workers execute monitoring, detection, risk, triage and retention work —
    none of them an on-chain action. If one ever does, it must go through
    ``execution_authorization`` rather than calling a provider helper directly."""
    provider_helpers = ('_propose_safe_transaction', '_submit_freeze_wallet_governance_action',
                        'proxy_compliance')
    offenders: list[str] = []
    for path in _app_sources():
        if not (path.name.startswith('run_') or path.name.endswith('_worker.py')):
            continue
        text = path.read_text(encoding='utf-8')
        for helper in provider_helpers:
            if helper in text:
                offenders.append(f'{path.name}:{helper}')
    assert offenders == []


def test_17e_the_destructive_action_vocabulary_is_covered_by_the_boundary() -> None:
    """Every action the product itself calls destructive runs in 'live' mode
    through the execute command, so the tenant boundary sees all of them."""
    from services.api.app import response_action_executor as executor

    assert executor.DESTRUCTIVE_ACTION_TYPES  # the set is not empty
    for action_type in executor.DESTRUCTIVE_ACTION_TYPES:
        assert execution_authz.is_production_execution(
            {'mode': 'live', 'action_type': action_type},
        ) is True


def test_17f_live_execution_still_requires_a_configured_adapter_as_well() -> None:
    """The plan boundary ADDS to the existing controls; it replaces none of them."""
    assert rgc.EXECUTION_ADAPTER_NOT_CONFIGURED in rgc.REASON_CODES
    assert 'HUMAN_QUORUM_INCOMPLETE' in pilot._GATE_BLOCKING_REASON_CODES
    assert 'POLICY_DENIED' in pilot._GATE_BLOCKING_REASON_CODES
    assert 'GATE_FACTS_UNAVAILABLE' in pilot._GATE_BLOCKING_REASON_CODES
