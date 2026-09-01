"""Screen 11 — the deterministic policy engine.

The invariant every test here defends:

    ALLOW is produced only when every requirement a policy imposes was met, by
    deterministic code, from inputs the server resolved. There is no path
    through this engine on which missing, unreadable, or malformed input yields
    ALLOW.

Pure-function tests: no DB, no network, no AI.
"""

from __future__ import annotations

import ast
import inspect
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from services.api.app.domains.governance_policy import config as gpc
from services.api.app.domains.governance_policy import engine, explanation, schemas
from services.api.app.domains.governance_policy.schemas import EvaluationContext, PolicyDefinition

# 10:42 UTC — inside the reference policy's 08:00-18:00 window.
NOW = datetime(2026, 9, 1, 10, 42, 0, tzinfo=timezone.utc)


def _policy(**overrides) -> PolicyDefinition:
    """The reference policy from the Screen 11 design: POL-MINT-007 v7."""
    fields = dict(
        policy_id='11111111-1111-1111-1111-111111111111',
        policy_key='POL-MINT-007',
        name='RWA Mint Policy',
        operation=gpc.OPERATION_MINT,
        status=gpc.STATUS_ACTIVE,
        version=7,
        workspace_id='ws-1',
        required_business_event=gpc.BUSINESS_EVENT_SUBSCRIPTION,
        settlement_requirement=gpc.REQUIREMENT_CLEARED,
        allowed_window_start_utc='08:00',
        allowed_window_end_utc='18:00',
        maximum_daily_amount_usd=Decimal('10000000'),
        required_roles=(gpc.ROLE_TREASURY_OPERATOR, gpc.ROLE_COMPLIANCE_APPROVER),
    )
    fields.update(overrides)
    return PolicyDefinition(**fields)


def _context(**overrides) -> EvaluationContext:
    """A fully-satisfying $5,000,000 mint."""
    fields = dict(
        operation=gpc.OPERATION_MINT,
        amount_usd=Decimal('5000000'),
        operator_id='user-183',
        operator_has_treasury_role=True,
        business_event=gpc.BUSINESS_EVENT_SUBSCRIPTION,
        settlement_status=gpc.SETTLEMENT_CLEARED,
        compliance_approval=True,
        evaluated_at=NOW,
        daily_total_usd=Decimal('0'),
    )
    fields.update(overrides)
    return EvaluationContext(**fields)


def _statuses(decision) -> dict[str, str]:
    return {c.key: c.status for c in decision.checks}


# -- 1. Valid mint -> ALLOW ---------------------------------------------------
def test_a_fully_satisfying_mint_is_allowed():
    decision = engine.evaluate_policy(_policy(), _context(), now=NOW)
    assert decision.decision == gpc.DECISION_ALLOW
    assert decision.reason_codes == (gpc.POLICY_SATISFIED,)
    assert decision.required_approvals == ()
    assert decision.policy_version == 7
    statuses = _statuses(decision)
    assert statuses[gpc.CHECK_SETTLEMENT] == schemas.PASS
    assert statuses[gpc.CHECK_COMPLIANCE_APPROVAL] == schemas.PASS


# -- 2. Missing compliance approver -> DENY (the design's reference case) -----
def test_missing_compliance_approval_denies_with_the_canonical_reason_code():
    decision = engine.evaluate_policy(_policy(), _context(compliance_approval=False), now=NOW)
    assert decision.decision == gpc.DECISION_DENY
    assert decision.reason_codes == (gpc.COMPLIANCE_APPROVAL_MISSING,)
    # Exactly the sign-off Screen 8 must still collect.
    assert decision.required_approvals == (gpc.ROLE_COMPLIANCE_APPROVER,)
    assert _statuses(decision)[gpc.CHECK_COMPLIANCE_APPROVAL] == schemas.FAIL


# -- 3 + 4. Settlement PENDING / FAILED -> DENY ------------------------------
@pytest.mark.parametrize('settlement', [gpc.SETTLEMENT_PENDING, gpc.SETTLEMENT_FAILED])
def test_settlement_that_has_not_cleared_denies(settlement):
    decision = engine.evaluate_policy(_policy(), _context(settlement_status=settlement), now=NOW)
    assert decision.decision == gpc.DECISION_DENY
    assert gpc.SETTLEMENT_NOT_CLEARED in decision.reason_codes


def test_settlement_missing_is_reported_as_not_cleared_not_as_satisfied():
    decision = engine.evaluate_policy(
        _policy(), _context(settlement_status=gpc.SETTLEMENT_MISSING), now=NOW)
    assert decision.decision == gpc.DECISION_DENY
    assert gpc.SETTLEMENT_NOT_CLEARED in decision.reason_codes


def test_an_unreadable_settlement_state_denies_with_its_own_code():
    # "the platform could not read it" is a DIFFERENT fact from "the source said
    # PENDING", and it must never be silently treated as satisfied.
    decision = engine.evaluate_policy(_policy(), _context(settlement_status=None), now=NOW)
    assert decision.decision == gpc.DECISION_DENY
    assert gpc.SETTLEMENT_STATE_UNKNOWN in decision.reason_codes


def test_a_cleared_or_pending_requirement_accepts_pending():
    policy = _policy(settlement_requirement=gpc.REQUIREMENT_CLEARED_OR_PENDING)
    decision = engine.evaluate_policy(policy, _context(settlement_status=gpc.SETTLEMENT_PENDING), now=NOW)
    assert decision.decision == gpc.DECISION_ALLOW


def test_a_settlement_requirement_the_engine_does_not_recognize_fails_closed():
    policy = _policy(settlement_requirement='SETTLED_SOMEHOW')
    decision = engine.evaluate_policy(policy, _context(), now=NOW)
    assert decision.decision == gpc.DECISION_DENY
    assert gpc.SETTLEMENT_STATE_UNKNOWN in decision.reason_codes


# -- 5 + 6. Business event missing / wrong type -> DENY ----------------------
def test_a_missing_business_event_denies():
    decision = engine.evaluate_policy(_policy(), _context(business_event=None), now=NOW)
    assert decision.decision == gpc.DECISION_DENY
    assert gpc.BUSINESS_EVENT_MISSING in decision.reason_codes


def test_the_wrong_business_event_type_denies():
    decision = engine.evaluate_policy(
        _policy(), _context(business_event=gpc.BUSINESS_EVENT_REDEMPTION), now=NOW)
    assert decision.decision == gpc.DECISION_DENY
    assert gpc.BUSINESS_EVENT_MISMATCH in decision.reason_codes


def test_an_unrecognized_business_event_value_is_a_mismatch_not_a_pass():
    decision = engine.evaluate_policy(_policy(), _context(business_event='WIRE_TRANSFER'), now=NOW)
    assert decision.decision == gpc.DECISION_DENY
    assert gpc.BUSINESS_EVENT_MISMATCH in decision.reason_codes


# -- 7. Amount above the limit -> DENY ---------------------------------------
def test_an_amount_over_the_daily_limit_denies():
    decision = engine.evaluate_policy(
        _policy(), _context(amount_usd=Decimal('10000001')), now=NOW)
    assert decision.decision == gpc.DECISION_DENY
    assert gpc.AMOUNT_LIMIT_EXCEEDED in decision.reason_codes


def test_the_limit_counts_what_was_already_permitted_today():
    # 6M already permitted + 5M requested exceeds the 10M cap, even though
    # either amount alone would not.
    decision = engine.evaluate_policy(
        _policy(), _context(daily_total_usd=Decimal('6000000')), now=NOW)
    assert decision.decision == gpc.DECISION_DENY
    assert gpc.AMOUNT_LIMIT_EXCEEDED in decision.reason_codes


def test_exactly_at_the_limit_is_allowed():
    decision = engine.evaluate_policy(
        _policy(), _context(amount_usd=Decimal('10000000')), now=NOW)
    assert decision.decision == gpc.DECISION_ALLOW


def test_an_unavailable_daily_total_fails_closed_under_a_capped_policy():
    decision = engine.evaluate_policy(_policy(), _context(daily_total_usd=None), now=NOW)
    assert decision.decision == gpc.DECISION_DENY
    assert gpc.DAILY_TOTAL_UNAVAILABLE in decision.reason_codes


def test_an_unavailable_daily_total_is_irrelevant_when_the_policy_has_no_cap():
    policy = _policy(maximum_daily_amount_usd=None)
    decision = engine.evaluate_policy(policy, _context(daily_total_usd=None), now=NOW)
    assert decision.decision == gpc.DECISION_ALLOW
    statuses = _statuses(decision)
    assert statuses[gpc.CHECK_DAILY_LIMIT] == schemas.NOT_APPLICABLE


def test_a_missing_or_negative_amount_denies_under_a_capped_policy():
    for amount in (None, Decimal('-1')):
        decision = engine.evaluate_policy(_policy(), _context(amount_usd=amount), now=NOW)
        assert decision.decision == gpc.DECISION_DENY
        assert gpc.AMOUNT_INVALID in decision.reason_codes


def test_money_is_decimal_and_never_passes_through_a_float():
    # 0.1 + 0.2 in binary floating point is not 0.3. A Decimal cap of 0.3 must
    # therefore accept a Decimal request of 0.1 with 0.2 already permitted.
    policy = _policy(maximum_daily_amount_usd=Decimal('0.3'))
    decision = engine.evaluate_policy(
        policy, _context(amount_usd=Decimal('0.1'), daily_total_usd=Decimal('0.2')), now=NOW)
    assert decision.decision == gpc.DECISION_ALLOW


# -- 8. Outside the allowed UTC window -> DENY -------------------------------
def test_outside_the_allowed_utc_window_denies():
    late = NOW.replace(hour=19, minute=30)
    decision = engine.evaluate_policy(_policy(), _context(evaluated_at=late), now=late)
    assert decision.decision == gpc.DECISION_DENY
    assert gpc.OUTSIDE_ALLOWED_WINDOW in decision.reason_codes


def test_the_window_is_evaluated_in_utc_not_in_a_local_timezone():
    # 23:30 in UTC-08:00 is 07:30 the next day in UTC — outside 08:00-18:00.
    local = datetime(2026, 9, 1, 23, 30, tzinfo=timezone(timedelta(hours=-8)))
    decision = engine.evaluate_policy(_policy(), _context(evaluated_at=local), now=local)
    assert decision.decision == gpc.DECISION_DENY
    assert gpc.OUTSIDE_ALLOWED_WINDOW in decision.reason_codes


def test_a_window_that_wraps_past_midnight_is_honoured():
    policy = _policy(allowed_window_start_utc='22:00', allowed_window_end_utc='04:00')
    inside = NOW.replace(hour=23)
    outside = NOW.replace(hour=12)
    assert engine.evaluate_policy(
        policy, _context(evaluated_at=inside), now=inside).decision == gpc.DECISION_ALLOW
    assert engine.evaluate_policy(
        policy, _context(evaluated_at=outside), now=outside).decision == gpc.DECISION_DENY


def test_a_malformed_stored_window_fails_closed():
    policy = _policy(allowed_window_start_utc='8am', allowed_window_end_utc='18:00')
    decision = engine.evaluate_policy(_policy_wrap(policy), _context(), now=NOW)
    assert decision.decision == gpc.DECISION_DENY
    assert gpc.OUTSIDE_ALLOWED_WINDOW in decision.reason_codes


def _policy_wrap(policy):  # readability helper for the test above
    return policy


def test_a_missing_evaluation_timestamp_fails_closed_under_a_windowed_policy():
    decision = engine.evaluate_policy(_policy(), _context(evaluated_at=None), now=None)
    assert decision.decision == gpc.DECISION_DENY
    assert gpc.EVALUATION_TIMESTAMP_MISSING in decision.reason_codes


def test_a_policy_without_a_window_does_not_constrain_the_time_of_day():
    policy = _policy(allowed_window_start_utc=None, allowed_window_end_utc=None)
    late = NOW.replace(hour=3)
    decision = engine.evaluate_policy(policy, _context(evaluated_at=late), now=late)
    assert decision.decision == gpc.DECISION_ALLOW
    assert _statuses(decision)[gpc.CHECK_ALLOWED_WINDOW] == schemas.NOT_APPLICABLE


# -- 9. Missing Treasury Operator role -> DENY -------------------------------
def test_an_operator_without_the_treasury_role_denies():
    decision = engine.evaluate_policy(
        _policy(), _context(operator_has_treasury_role=False), now=NOW)
    assert decision.decision == gpc.DECISION_DENY
    assert gpc.TREASURY_OPERATOR_MISSING in decision.reason_codes
    assert gpc.ROLE_TREASURY_OPERATOR in decision.required_approvals


def test_an_unresolvable_operator_authority_is_never_treated_as_held():
    # None means the platform could not establish the operator's authority.
    decision = engine.evaluate_policy(
        _policy(), _context(operator_has_treasury_role=None), now=NOW)
    assert decision.decision == gpc.DECISION_DENY
    assert gpc.TREASURY_OPERATOR_MISSING in decision.reason_codes


def test_a_role_the_engine_cannot_evidence_fails_closed_rather_than_passing():
    policy = _policy(required_roles=(gpc.ROLE_TREASURY_OPERATOR, 'BOARD_SIGNATORY'))
    decision = engine.evaluate_policy(_policy_wrap(policy), _context(), now=NOW)
    assert decision.decision == gpc.DECISION_DENY
    assert gpc.REQUIRED_ROLE_MISSING in decision.reason_codes
    assert 'BOARD_SIGNATORY' in decision.required_approvals


# -- 10. Disabled policy -> DENY ---------------------------------------------
def test_a_disabled_policy_denies_and_stops_evaluating():
    decision = engine.evaluate_policy(_policy(status=gpc.STATUS_DISABLED), _context(), now=NOW)
    assert decision.decision == gpc.DECISION_DENY
    assert decision.reason_codes == (gpc.POLICY_DISABLED,)
    # Terminal: the constraints of a policy that cannot authorize anything are
    # not reported as if they had been evaluated.
    assert gpc.CHECK_SETTLEMENT not in _statuses(decision)


@pytest.mark.parametrize('status_value', [gpc.STATUS_DRAFT, gpc.STATUS_ARCHIVED])
def test_a_draft_or_archived_policy_denies_distinctly_from_disabled(status_value):
    decision = engine.evaluate_policy(_policy(status=status_value), _context(), now=NOW)
    assert decision.decision == gpc.DECISION_DENY
    assert decision.reason_codes == (gpc.POLICY_NOT_ACTIVE,)


# -- 11. Policy not found -> fail closed -------------------------------------
def test_no_policy_at_all_fails_closed():
    decision = engine.evaluate_policy(None, _context(), now=NOW)
    assert decision.decision == gpc.DECISION_DENY
    assert decision.reason_codes == (gpc.POLICY_NOT_FOUND,)
    assert decision.policy_id is None
    assert decision.policy_version is None


def test_an_operation_the_policy_does_not_govern_denies():
    decision = engine.evaluate_policy(_policy(), _context(operation=gpc.OPERATION_BURN), now=NOW)
    assert decision.decision == gpc.DECISION_DENY
    assert decision.reason_codes == (gpc.OPERATION_MISMATCH,)


def test_an_unrecognized_operation_denies():
    decision = engine.evaluate_policy(_policy(), _context(operation='AIRDROP'), now=NOW)
    assert decision.decision == gpc.DECISION_DENY
    assert decision.reason_codes == (gpc.OPERATION_MISMATCH,)


# -- 13. The policy version travels with the decision ------------------------
def test_the_decision_carries_the_policy_version_that_produced_it():
    decision = engine.evaluate_policy(_policy(version=12), _context(), now=NOW)
    assert decision.policy_version == 12
    assert decision.as_dict()['policy_version'] == 12
    assert decision.as_dict()['policy_id'] == '11111111-1111-1111-1111-111111111111'
    assert decision.as_dict()['engine_version'] == gpc.ENGINE_VERSION


# -- Every failure at once ---------------------------------------------------
def test_every_violated_requirement_is_reported_not_just_the_first():
    late = NOW.replace(hour=21)
    decision = engine.evaluate_policy(
        _policy(),
        _context(
            business_event=gpc.BUSINESS_EVENT_REDEMPTION,
            settlement_status=gpc.SETTLEMENT_FAILED,
            evaluated_at=late,
            amount_usd=Decimal('20000000'),
            operator_has_treasury_role=False,
            compliance_approval=False,
        ),
        now=late,
    )
    assert decision.decision == gpc.DECISION_DENY
    assert set(decision.reason_codes) == {
        gpc.BUSINESS_EVENT_MISMATCH,
        gpc.SETTLEMENT_NOT_CLEARED,
        gpc.OUTSIDE_ALLOWED_WINDOW,
        gpc.AMOUNT_LIMIT_EXCEEDED,
        gpc.TREASURY_OPERATOR_MISSING,
        gpc.COMPLIANCE_APPROVAL_MISSING,
    }
    # Reported in evaluation order, so the UI never reorders the story.
    assert decision.reason_codes.index(gpc.BUSINESS_EVENT_MISMATCH) < decision.reason_codes.index(
        gpc.COMPLIANCE_APPROVAL_MISSING)


def test_the_same_inputs_always_produce_the_same_decision():
    first = engine.evaluate_policy(_policy(), _context(compliance_approval=False), evaluation_id='fixed', now=NOW)
    second = engine.evaluate_policy(_policy(), _context(compliance_approval=False), evaluation_id='fixed', now=NOW)
    assert first.as_dict() == second.as_dict()


def test_every_reason_code_the_engine_emits_is_in_the_canonical_vocabulary():
    contexts = [
        _context(), _context(compliance_approval=False), _context(business_event=None),
        _context(settlement_status=gpc.SETTLEMENT_FAILED), _context(settlement_status=None),
        _context(amount_usd=Decimal('99999999')), _context(amount_usd=None),
        _context(daily_total_usd=None), _context(operator_has_treasury_role=False),
        _context(evaluated_at=None), _context(operation='AIRDROP'),
    ]
    emitted: set[str] = set()
    for ctx in contexts:
        for policy in (_policy(), _policy(status=gpc.STATUS_DISABLED), _policy(status=gpc.STATUS_DRAFT), None):
            emitted.update(engine.evaluate_policy(policy, ctx, now=NOW).reason_codes)
    assert emitted, 'the sweep must actually exercise the engine'
    assert emitted <= set(gpc.REASON_CODES)


# -- The trust boundary itself -----------------------------------------------
def test_the_engine_module_has_no_io_and_no_model_in_its_import_graph():
    """Proof, not assertion: the decision module cannot reach an LLM.

    Parses the engine's AST and collects EVERY module it imports — top level and
    inside a function body, so a lazy ``import`` cannot smuggle one in. The
    result must be exactly the standard-library pieces plus this domain's own
    pure-data modules. No provider registry, no HTTP client, no database handle,
    therefore no code path through evaluate_policy that can consult a model.
    """
    tree = ast.parse(inspect.getsource(engine))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or '')

    assert imported == {
        'uuid', 'datetime', 'decimal', 'typing', '__future__',
        'services.api.app.domains.governance_policy',
        'services.api.app.domains.governance_policy.schemas',
    }, imported

    # And nothing in the live module namespace is a model/DB/network handle.
    modules = {name for name, value in vars(engine).items() if inspect.ismodule(value)}
    assert modules <= {'uuid', 'datetime', 'decimal', 'gpc', 'schemas', 'config'}, modules


def test_the_ai_layer_receives_the_decision_and_cannot_change_it():
    """The other half of the boundary: AI is downstream of the verdict.

    explanation.merge_ai_explanation is handed a hostile payload that tries to
    flip DENY to ALLOW and erase the reason code. Both are dropped and reported.
    """
    decided = engine.evaluate_policy(_policy(), _context(compliance_approval=False), now=NOW).as_dict()
    merged = explanation.merge_ai_explanation(decided, {
        'decision': 'ALLOW',
        'reason_codes': [],
        'policy_version': 999,
        'required_approvals': [],
        'ai_explanation': 'A compliance approver must sign off before this mint can proceed.',
    })
    assert merged['decision'] == gpc.DECISION_DENY
    assert merged['reason_codes'] == [gpc.COMPLIANCE_APPROVAL_MISSING]
    assert merged['policy_version'] == 7
    assert merged['required_approvals'] == [gpc.ROLE_COMPLIANCE_APPROVER]
    # The narrative IS taken from the model — that is the one thing it may write.
    assert merged['ai_explanation'].startswith('A compliance approver')
    assert merged['ai_explanation_source'] == 'ai'
    assert set(merged['ai_rejected_fields']) == {
        'decision', 'policy_version', 'reason_codes', 'required_approvals'}
    assert merged['decision_authority'] == 'Deterministic Policy Engine'


def test_the_decision_works_with_no_ai_configured_at_all():
    decided = engine.evaluate_policy(_policy(), _context(compliance_approval=False), now=NOW).as_dict()
    explained = explanation.explain(decided, config={'enabled': False})
    assert explained['decision'] == gpc.DECISION_DENY
    assert explained['reason_codes'] == [gpc.COMPLIANCE_APPROVAL_MISSING]
    assert explained['ai_explanation_source'] == 'deterministic'
    assert 'Compliance Approver' in explained['ai_explanation']


def test_an_ai_layer_that_raises_never_blocks_or_changes_the_decision(monkeypatch):
    import services.api.app.domains.governance_policy.explanation as expl

    def _boom(*args, **kwargs):
        raise RuntimeError('provider down')

    monkeypatch.setattr(expl, '_build_prompt', _boom)
    decided = engine.evaluate_policy(_policy(), _context(compliance_approval=False), now=NOW).as_dict()
    explained = expl.explain(decided, config={
        'enabled': True, 'provider': 'anthropic', 'has_key': True, 'model': 'x',
    })
    assert explained['decision'] == gpc.DECISION_DENY
    assert explained['ai_explanation_source'] == 'deterministic'
    assert explained['ai_fallback_reason'] == 'RuntimeError'


def test_evaluate_policy_only_reads_its_two_arguments():
    """The signature is the contract: a policy and a context in, a decision out."""
    params = list(inspect.signature(engine.evaluate_policy).parameters)
    assert params == ['policy', 'context', 'evaluation_id', 'now']
