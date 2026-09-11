"""Pilot safety mode — production execution is locked, everything else is not.

Decoda's stated authority model is: AI recommends, a deterministic policy engine
decides, a human authorizes. A Pilot evaluation adds one more constraint on top:
no run reaches production at all.

What these tests pin down:
  1  A LIVE run under a recommend-only plan is refused by the BACKEND gate, with
     its own reason code — not by hiding a button.
  2  A simulated or recommended action is untouched, so Screen 8 stays a working
     screen rather than a disabled one.
  3  The lock is a CAPABILITY fact: a valid policy ALLOW is still reported as
     AUTHORIZED, so an operator is never told policy denied something it did not.
  4  The lock fails CLOSED — an entitlement that could not be read is not
     permission to execute — but is absent entirely before the tenancy migration.
  5  An explicit, audited entitlement override can unlock it; being Enterprise
     alone cannot.
  6  The EXECUTE command refuses on the code, so a direct API call that never
     rendered the UI hits the same lock.

Run:
    python -m pytest services/api/tests/test_pilot_execution_lock.py -q
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from services.api.app import entitlements as ent
from services.api.app import pilot
from services.api.app.domains.response_gate import config as rgc

#: The lock reads the REAL clock (it is a live-run capability check, not a
#: replay), so the fixture below anchors its evaluation window to wall time. A
#: hard-coded date would quietly age into an EXPIRED evaluation and these tests
#: would stop testing the case they name.
NOW = datetime.now(timezone.utc)
ORG = 'org-pilot-1'
WS = 'ws-pilot-1'


class _Result:
    def __init__(self, row: Any = None) -> None:
        self._row = row

    def fetchone(self) -> Any:
        return self._row

    def fetchall(self) -> list[Any]:
        return [] if self._row is None else [self._row]


class _Conn:
    def __init__(self, organization: dict[str, Any] | None, *, schema_ready: bool = True, fail: bool = False) -> None:
        self.organization = organization
        self.schema_ready = schema_ready
        self.fail = fail

    def execute(self, query: str, params: Any = None) -> _Result:
        sql = ' '.join(str(query).split()).lower()
        if self.fail:
            raise RuntimeError('database unavailable')
        if 'information_schema.tables' in sql:
            return _Result({'table_count': 2 if self.schema_ready else 0,
                            'link_count': 1 if self.schema_ready else 0})
        if 'join organizations o on o.id = w.organization_id' in sql:
            return _Result(dict(self.organization) if self.organization else None)
        return _Result()

    def commit(self) -> None:
        pass


def _org(plan: str = ent.PLAN_PILOT, **overrides: Any) -> dict[str, Any]:
    row = {
        'id': ORG, 'name': 'Pilot Co', 'slug': 'pilot-co',
        'plan': plan, 'status': ent.STATUS_ACTIVE,
        'evaluation_started_at': NOW - timedelta(days=7),
        'evaluation_expires_at': NOW + timedelta(days=23),
        'entitlement_overrides': {},
        'created_at': NOW, 'updated_at': NOW,
    }
    row.update(overrides)
    return row


def _authorized_gate() -> dict[str, Any]:
    """A gate the deterministic engine fully authorized."""
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


# ── 1 — a live run is locked under a recommend-only plan ──────────────────────

def test_live_action_is_locked_for_a_pilot_organization() -> None:
    connection = _Conn(_org())
    gate = pilot._apply_plan_execution_lock(
        connection, _authorized_gate(), action={'id': 'a1', 'mode': 'live'}, workspace_id=WS,
    )
    assert gate['plan_execution_locked'] is True
    assert gate['can_execute'] is False
    assert gate['decision'] == rgc.GATE_LOCKED
    assert rgc.PLAN_EXECUTION_NOT_ENTITLED in gate['reason_codes']
    assert any(item['code'] == rgc.PLAN_EXECUTION_NOT_ENTITLED for item in gate['reasons'])
    assert gate['plan'] == 'pilot'


def test_lock_reports_a_reason_an_operator_can_act_on() -> None:
    connection = _Conn(_org())
    gate = pilot._apply_plan_execution_lock(
        connection, _authorized_gate(), action={'id': 'a1', 'mode': 'live'}, workspace_id=WS,
    )
    label = next(item['label'] for item in gate['reasons'] if item['code'] == rgc.PLAN_EXECUTION_NOT_ENTITLED)
    assert 'recommend-only' in label.lower()


def test_an_ended_evaluation_is_named_as_the_reason_rather_than_the_plan_mode() -> None:
    """An expired evaluator cannot act on the recommendation either, so telling
    them "your plan is recommend-only" would point at the wrong remedy."""
    connection = _Conn(_org(
        evaluation_started_at=NOW - timedelta(days=40), evaluation_expires_at=NOW - timedelta(days=10),
    ))
    gate = pilot._apply_plan_execution_lock(
        connection, _authorized_gate(), action={'id': 'a1', 'mode': 'live'}, workspace_id=WS,
    )
    assert gate['plan_execution_locked'] is True
    assert gate['plan_execution_lock_reason'] == 'evaluation_expired'
    label = next(item['label'] for item in gate['reasons'] if item['code'] == rgc.PLAN_EXECUTION_NOT_ENTITLED)
    assert 'evaluation has ended' in label.lower()
    assert 'upgrade to scale' in label.lower()


# ── 2 — the rest of Screen 8 keeps working ────────────────────────────────────

@pytest.mark.parametrize('mode', ['simulated', 'recommended', 'dry_run', ''])
def test_non_live_modes_are_untouched_by_the_plan_lock(mode: str) -> None:
    connection = _Conn(_org())
    gate = pilot._apply_plan_execution_lock(
        connection, _authorized_gate(), action={'id': 'a1', 'mode': mode}, workspace_id=WS,
    )
    assert gate['plan_execution_locked'] is False
    assert gate['can_execute'] is True
    assert gate['decision'] == rgc.GATE_AUTHORIZED
    assert rgc.PLAN_EXECUTION_NOT_ENTITLED not in gate['reason_codes']


def test_pilot_keeps_recommendation_investigation_and_evidence_entitlements() -> None:
    entitlements = ent.get_entitlements(_org())
    assert ent.has_entitlement(entitlements, ent.FEATURE_RESPONSE_RECOMMENDATIONS) is True
    assert ent.has_entitlement(entitlements, ent.FEATURE_AI_INVESTIGATION) is True
    assert ent.has_entitlement(entitlements, ent.FEATURE_EVIDENCE_EXPORT) is True


# ── 3 — the lock is capability, not an authorization verdict ──────────────────

def test_policy_allow_is_not_rewritten_into_a_denial() -> None:
    connection = _Conn(_org())
    gate = pilot._apply_plan_execution_lock(
        connection, _authorized_gate(), action={'id': 'a1', 'mode': 'live'}, workspace_id=WS,
    )
    assert gate['authorization_decision'] == rgc.GATE_AUTHORIZED
    assert gate['policy_decision'] == rgc.POLICY_ALLOW
    assert gate['decision'] != rgc.GATE_DENIED


def test_existing_lock_reasons_are_preserved_alongside_the_plan_code() -> None:
    base = _authorized_gate()
    base['reason_codes'] = [rgc.HUMAN_QUORUM_INCOMPLETE]
    base['reasons'] = [{'code': rgc.HUMAN_QUORUM_INCOMPLETE, 'label': 'x'}]
    base['can_execute'] = False
    gate = pilot._apply_plan_execution_lock(
        _Conn(_org()), base, action={'id': 'a1', 'mode': 'live'}, workspace_id=WS,
    )
    assert gate['reason_codes'] == [rgc.HUMAN_QUORUM_INCOMPLETE, rgc.PLAN_EXECUTION_NOT_ENTITLED]


# ── 4 — fail closed, but absent before the migration ──────────────────────────

def test_unreadable_entitlement_fails_closed() -> None:
    """A fact we could not look up is not an authorization."""
    lock = pilot.plan_execution_lock(_Conn(None, fail=True), WS)
    assert lock['locked'] is True
    assert lock['reason'] == 'entitlement_unavailable'


def test_workspace_with_no_organization_fails_closed() -> None:
    lock = pilot.plan_execution_lock(_Conn(None), WS)
    assert lock['locked'] is True
    assert lock['reason'] == 'organization_not_linked'


def test_unmigrated_deployment_is_not_locked() -> None:
    """Before migration 0150 there is no plan to consult, and every pre-existing
    gate still applies, so the new rule simply does not participate."""
    lock = pilot.plan_execution_lock(_Conn(None, schema_ready=False), WS)
    assert lock['locked'] is False
    assert lock['reason'] == 'tenancy_schema_not_migrated'


def test_lock_result_is_cached_per_workspace_within_one_request() -> None:
    connection = _Conn(_org())
    cache: dict[str, Any] = {}
    first = pilot.plan_execution_lock(connection, WS, cache=cache)
    connection.organization = None      # a second read would now fail closed
    second = pilot.plan_execution_lock(connection, WS, cache=cache)
    assert first == second


# ── 5 — only an explicit override unlocks execution ───────────────────────────

def test_enterprise_alone_does_not_unlock_production_execution() -> None:
    gate = pilot._apply_plan_execution_lock(
        _Conn(_org(plan=ent.PLAN_ENTERPRISE, evaluation_expires_at=None)),
        _authorized_gate(), action={'id': 'a1', 'mode': 'live'}, workspace_id=WS,
    )
    assert gate['plan_execution_locked'] is True


def test_explicit_entitlement_override_unlocks_production_execution() -> None:
    organization = _org(
        plan=ent.PLAN_ENTERPRISE,
        evaluation_expires_at=None,
        entitlement_overrides={ent.FEATURE_AUTOMATIC_EXECUTION: True},
    )
    gate = pilot._apply_plan_execution_lock(
        _Conn(organization), _authorized_gate(), action={'id': 'a1', 'mode': 'live'}, workspace_id=WS,
    )
    assert gate['plan_execution_locked'] is False
    assert gate['can_execute'] is True
    assert gate['decision'] == rgc.GATE_AUTHORIZED


# ── 6 — the EXECUTE command refuses on the code ───────────────────────────────

def test_execute_command_treats_the_plan_code_as_blocking() -> None:
    assert rgc.PLAN_EXECUTION_NOT_ENTITLED in pilot._GATE_BLOCKING_REASON_CODES


def test_reason_code_is_registered_in_the_canonical_vocabulary() -> None:
    assert rgc.PLAN_EXECUTION_NOT_ENTITLED in rgc.REASON_CODES
    assert rgc.reason_label(rgc.PLAN_EXECUTION_NOT_ENTITLED) != rgc.PLAN_EXECUTION_NOT_ENTITLED
