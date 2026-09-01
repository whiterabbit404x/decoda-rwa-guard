"""The deterministic policy engine.

    evaluate_policy(policy, context) -> PolicyDecision

This function is the ONLY code in the product that decides ALLOW or DENY for an
operation policy.

Trust boundary
--------------
This module imports ``config`` and ``schemas`` and the standard library. It has
no database handle, no HTTP client, no provider registry, no clock it did not
receive, and no import path that reaches ``ai_providers``. An LLM therefore
cannot participate in a decision even by accident: there is no seam through
which one could be called. The AI layer receives the finished PolicyDecision as
an input (see explanation.py) and has nothing left to decide.

Determinism
-----------
Same policy + same context -> same decision, same reason codes, same order.
Money is Decimal; there is no float arithmetic anywhere in this file. The only
value that varies between two otherwise-identical calls is ``evaluation_id``,
which the caller may supply for full reproducibility.

Fail-closed
-----------
Every path that cannot establish a fact returns DENY with an explicit reason
code. There is no branch in this file that reaches ALLOW from missing,
unreadable, or malformed input.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from services.api.app.domains.governance_policy import config as gpc
from services.api.app.domains.governance_policy import schemas
from services.api.app.domains.governance_policy.schemas import (
    EvaluationContext,
    PolicyCheck,
    PolicyDecision,
    PolicyDefinition,
)


def _check(key: str, status: str, detail: str, reason_code: Optional[str] = None) -> PolicyCheck:
    return PolicyCheck(key=key, status=status, detail=detail, reason_code=reason_code)


def _parse_hhmm(value: Any) -> Optional[int]:
    """'08:00' -> minutes since UTC midnight. None when unparseable.

    Deliberately strict: a malformed window bound is not silently treated as
    midnight, it makes the window unusable and the check fails closed.
    """
    text = str(value or '').strip()
    if len(text) != 5 or text[2] != ':':
        return None
    try:
        hours = int(text[0:2])
        minutes = int(text[3:5])
    except ValueError:
        return None
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        return None
    return hours * 60 + minutes


def _minutes_utc(moment: datetime) -> int:
    """Minutes since midnight UTC for an aware or naive datetime.

    A naive datetime is treated as UTC — every timestamp reaching the engine is
    produced by the service from ``pilot.utc_now()`` or an explicit UTC parse.
    """
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc)
    return moment.hour * 60 + moment.minute


def _in_window(minute_of_day: int, start: int, end: int) -> bool:
    """Inclusive-start, inclusive-end containment.

    A window whose end is before its start wraps past midnight (22:00-04:00),
    which is a legitimate operating window for a global desk.
    """
    if start <= end:
        return start <= minute_of_day <= end
    return minute_of_day >= start or minute_of_day <= end


def _as_decimal(value: Any) -> Optional[Decimal]:
    """Money as Decimal, never float. None when the value is not usable."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        return None
    if isinstance(value, float):
        # A float amount has already lost precision upstream; convert through
        # its string form rather than compounding the error silently.
        try:
            return Decimal(repr(value))
        except (InvalidOperation, ValueError):
            return None
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None


def _terminal(
    *,
    reason_code: str,
    checks: dict[str, PolicyCheck],
    policy: Optional[PolicyDefinition],
    context: EvaluationContext,
    evaluation_id: str,
    evaluated_at: datetime,
) -> PolicyDecision:
    """A DENY that stops evaluation: the policy does not apply, so its
    constraints are not meaningful to report on."""
    required_roles = tuple(policy.required_roles) if policy else ()
    return PolicyDecision(
        decision=gpc.DECISION_DENY,
        reason_codes=(reason_code,),
        checks=schemas.checks_in_order(checks),
        policy_id=policy.policy_id if policy else None,
        policy_key=policy.policy_key if policy else None,
        policy_version=policy.version if policy else None,
        evaluation_id=evaluation_id,
        evaluated_at=evaluated_at,
        engine_version=gpc.ENGINE_VERSION,
        # Nothing was evidenced, so every required role is still outstanding.
        required_approvals=required_roles,
        required_roles=required_roles,
        operation=context.operation,
        asset_id=context.asset_id,
        incident_id=context.incident_id,
        canonical_event_id=context.canonical_event_id,
        amount_usd=_as_decimal(context.amount_usd),
        simulation=bool(context.simulation),
        violation_action=policy.violation_action if policy else gpc.VIOLATION_ACTION_DENY,
    )


def evaluate_policy(
    policy: Optional[PolicyDefinition],
    context: EvaluationContext,
    *,
    evaluation_id: Optional[str] = None,
    now: Optional[datetime] = None,
) -> PolicyDecision:
    """Judge one operation against one policy.

    Evaluation order (each numbered step is a named check the UI renders):

      1. policy exists                      -> POLICY_NOT_FOUND        (terminal)
      2. policy is ACTIVE                   -> POLICY_DISABLED /
                                               POLICY_NOT_ACTIVE       (terminal)
      3. operation matches the policy       -> OPERATION_MISMATCH      (terminal)
      4-5. required business event present and of the right type
      6. settlement state satisfies the requirement
      7. the request falls inside the allowed UTC window
      8. the daily issuance limit would not be exceeded
      9. the required operator role is evidenced
      10. the required compliance approval is present
      11. ALLOW

    Steps 1-3 are terminal because a policy that does not apply has no
    constraints worth reporting. Steps 4-10 are collected together, so an
    operator sees EVERY violation at once instead of fixing them one refresh at
    a time. Any failed mandatory requirement yields DENY.
    """
    evaluation_id = evaluation_id or str(uuid.uuid4())
    evaluated_at = now or context.evaluated_at or datetime.now(timezone.utc)
    checks: dict[str, PolicyCheck] = {}

    # -- 1. Policy exists --------------------------------------------------
    if policy is None:
        checks[gpc.CHECK_POLICY_EXISTS] = _check(
            gpc.CHECK_POLICY_EXISTS, schemas.FAIL,
            'No policy governs this operation, so nothing authorizes it.',
            gpc.POLICY_NOT_FOUND,
        )
        return _terminal(
            reason_code=gpc.POLICY_NOT_FOUND, checks=checks, policy=None, context=context,
            evaluation_id=evaluation_id, evaluated_at=evaluated_at,
        )
    checks[gpc.CHECK_POLICY_EXISTS] = _check(
        gpc.CHECK_POLICY_EXISTS, schemas.PASS,
        f'Policy {policy.policy_key} version {policy.version} governs this operation.',
    )

    # -- 2. Policy is ACTIVE -----------------------------------------------
    if not policy.is_active:
        reason = gpc.POLICY_DISABLED if policy.status == gpc.STATUS_DISABLED else gpc.POLICY_NOT_ACTIVE
        checks[gpc.CHECK_POLICY_ACTIVE] = _check(
            gpc.CHECK_POLICY_ACTIVE, schemas.FAIL,
            f'Policy status is {policy.status}. Only an ACTIVE policy can authorize an operation.',
            reason,
        )
        return _terminal(
            reason_code=reason, checks=checks, policy=policy, context=context,
            evaluation_id=evaluation_id, evaluated_at=evaluated_at,
        )
    checks[gpc.CHECK_POLICY_ACTIVE] = _check(
        gpc.CHECK_POLICY_ACTIVE, schemas.PASS, 'Policy status is ACTIVE.',
    )

    # -- 3. Operation matches ----------------------------------------------
    operation = gpc.normalize_operation(context.operation)
    if operation is None or operation != policy.operation:
        checks[gpc.CHECK_OPERATION_MATCHES] = _check(
            gpc.CHECK_OPERATION_MATCHES, schemas.FAIL,
            f'Policy governs {policy.operation}; the request is '
            f'{operation or "an unrecognized operation"}.',
            gpc.OPERATION_MISMATCH,
        )
        return _terminal(
            reason_code=gpc.OPERATION_MISMATCH, checks=checks, policy=policy, context=context,
            evaluation_id=evaluation_id, evaluated_at=evaluated_at,
        )
    checks[gpc.CHECK_OPERATION_MATCHES] = _check(
        gpc.CHECK_OPERATION_MATCHES, schemas.PASS, f'Operation is {operation}.',
    )

    # Every remaining failure is collected, in evaluation order.
    reason_codes: list[str] = []

    def fail(key: str, reason: str, detail: str) -> None:
        checks[key] = _check(key, schemas.FAIL, detail, reason)
        if reason not in reason_codes:
            reason_codes.append(reason)

    # -- 4 + 5. Required business event ------------------------------------
    required_event = gpc.normalize_business_event(policy.required_business_event)
    if policy.required_business_event is None:
        checks[gpc.CHECK_BUSINESS_EVENT] = _check(
            gpc.CHECK_BUSINESS_EVENT, schemas.NOT_APPLICABLE,
            'This policy does not require a business event.',
        )
    else:
        observed_event = gpc.normalize_business_event(context.business_event)
        if not str(context.business_event or '').strip():
            fail(gpc.CHECK_BUSINESS_EVENT, gpc.BUSINESS_EVENT_MISSING,
                 f'The policy requires a {policy.required_business_event} business event; none was supplied.')
        elif observed_event is None or observed_event != required_event:
            fail(gpc.CHECK_BUSINESS_EVENT, gpc.BUSINESS_EVENT_MISMATCH,
                 f'The policy requires {policy.required_business_event}; the request carries '
                 f'{observed_event or str(context.business_event).strip()}.')
        else:
            checks[gpc.CHECK_BUSINESS_EVENT] = _check(
                gpc.CHECK_BUSINESS_EVENT, schemas.PASS,
                f'Business event is {observed_event}.',
            )

    # -- 6. Settlement requirement -----------------------------------------
    requirement = gpc.normalize_settlement_requirement(policy.settlement_requirement)
    if policy.settlement_requirement is None:
        checks[gpc.CHECK_SETTLEMENT] = _check(
            gpc.CHECK_SETTLEMENT, schemas.NOT_APPLICABLE,
            'This policy does not impose a settlement requirement.',
        )
    elif requirement is None:
        # A stored requirement the engine does not recognize is never treated as
        # satisfied. Fail closed rather than skip the constraint.
        fail(gpc.CHECK_SETTLEMENT, gpc.SETTLEMENT_STATE_UNKNOWN,
             f'The policy names an unrecognized settlement requirement '
             f'({policy.settlement_requirement}), so it cannot be evaluated.')
    else:
        observed_settlement = gpc.normalize_settlement(context.settlement_status)
        if observed_settlement is None:
            fail(gpc.CHECK_SETTLEMENT, gpc.SETTLEMENT_STATE_UNKNOWN,
                 'The settlement state could not be established, so the requirement '
                 f'{gpc.SETTLEMENT_REQUIREMENT_LABELS[requirement]} cannot be shown to be met.')
        elif observed_settlement in gpc.SETTLEMENT_SATISFIES[requirement]:
            checks[gpc.CHECK_SETTLEMENT] = _check(
                gpc.CHECK_SETTLEMENT, schemas.PASS,
                f'Settlement is {observed_settlement}; the policy requires '
                f'{gpc.SETTLEMENT_REQUIREMENT_LABELS[requirement]}.',
            )
        else:
            fail(gpc.CHECK_SETTLEMENT, gpc.SETTLEMENT_NOT_CLEARED,
                 f'Settlement is {observed_settlement}; the policy requires '
                 f'{gpc.SETTLEMENT_REQUIREMENT_LABELS[requirement]}.')

    # -- 7. Allowed UTC window ---------------------------------------------
    if not policy.has_allowed_window:
        checks[gpc.CHECK_ALLOWED_WINDOW] = _check(
            gpc.CHECK_ALLOWED_WINDOW, schemas.NOT_APPLICABLE,
            'This policy does not restrict the time of day.',
        )
    else:
        start = _parse_hhmm(policy.allowed_window_start_utc)
        end = _parse_hhmm(policy.allowed_window_end_utc)
        moment = context.evaluated_at
        if start is None or end is None:
            fail(gpc.CHECK_ALLOWED_WINDOW, gpc.OUTSIDE_ALLOWED_WINDOW,
                 'The policy window is not a valid UTC time range, so the request '
                 'cannot be shown to fall inside it.')
        elif moment is None:
            fail(gpc.CHECK_ALLOWED_WINDOW, gpc.EVALUATION_TIMESTAMP_MISSING,
                 'No evaluation timestamp was supplied, so the allowed window cannot be checked.')
        else:
            observed_minute = _minutes_utc(moment)
            window = f'{policy.allowed_window_start_utc}-{policy.allowed_window_end_utc} UTC'
            if _in_window(observed_minute, start, end):
                checks[gpc.CHECK_ALLOWED_WINDOW] = _check(
                    gpc.CHECK_ALLOWED_WINDOW, schemas.PASS,
                    f'{observed_minute // 60:02d}:{observed_minute % 60:02d} UTC is inside {window}.',
                )
            else:
                fail(gpc.CHECK_ALLOWED_WINDOW, gpc.OUTSIDE_ALLOWED_WINDOW,
                     f'{observed_minute // 60:02d}:{observed_minute % 60:02d} UTC is outside {window}.')

    # -- 8. Daily issuance limit -------------------------------------------
    amount = _as_decimal(context.amount_usd)
    cap = _as_decimal(policy.maximum_daily_amount_usd)
    if cap is None:
        checks[gpc.CHECK_DAILY_LIMIT] = _check(
            gpc.CHECK_DAILY_LIMIT, schemas.NOT_APPLICABLE,
            'This policy does not cap daily issuance.',
        )
    elif amount is None or amount < 0:
        fail(gpc.CHECK_DAILY_LIMIT, gpc.AMOUNT_INVALID,
             'The request amount is missing or not a valid non-negative value, so it '
             'cannot be checked against the daily limit.')
    else:
        prior = _as_decimal(context.daily_total_usd)
        if prior is None:
            # The cap is real but today's total could not be established. Fail
            # closed: an unknown denominator can never satisfy a limit.
            fail(gpc.CHECK_DAILY_LIMIT, gpc.DAILY_TOTAL_UNAVAILABLE,
                 "Today's issuance total under this policy could not be established, so the "
                 'daily limit cannot be shown to hold.')
        elif prior + amount > cap:
            fail(gpc.CHECK_DAILY_LIMIT, gpc.AMOUNT_LIMIT_EXCEEDED,
                 f'{prior + amount} USD would exceed the {cap} USD daily limit '
                 f'({prior} USD already permitted today).')
        else:
            checks[gpc.CHECK_DAILY_LIMIT] = _check(
                gpc.CHECK_DAILY_LIMIT, schemas.PASS,
                f'{prior + amount} USD is within the {cap} USD daily limit.',
            )

    # -- 9 + 10. Required roles --------------------------------------------
    # A role is evidenced by canonical state the SERVER resolved, never by a
    # claim in the request body.
    required_roles = tuple(policy.required_roles)
    outstanding: list[str] = []

    if gpc.ROLE_TREASURY_OPERATOR in required_roles:
        if context.operator_has_treasury_role is True:
            checks[gpc.CHECK_OPERATOR_ROLE] = _check(
                gpc.CHECK_OPERATOR_ROLE, schemas.PASS,
                f'Operator {context.operator_id or "(unnamed)"} holds '
                f'{gpc.ROLE_PERMISSIONS[gpc.ROLE_TREASURY_OPERATOR]} in this workspace.',
            )
        else:
            outstanding.append(gpc.ROLE_TREASURY_OPERATOR)
            fail(gpc.CHECK_OPERATOR_ROLE, gpc.TREASURY_OPERATOR_MISSING,
                 f'The policy requires a {gpc.GOVERNANCE_ROLE_LABELS[gpc.ROLE_TREASURY_OPERATOR]}; '
                 f'{context.operator_id or "no operator"} does not hold '
                 f'{gpc.ROLE_PERMISSIONS[gpc.ROLE_TREASURY_OPERATOR]} in this workspace.')
    else:
        checks[gpc.CHECK_OPERATOR_ROLE] = _check(
            gpc.CHECK_OPERATOR_ROLE, schemas.NOT_APPLICABLE,
            'This policy does not require a Treasury Operator.',
        )

    if gpc.ROLE_COMPLIANCE_APPROVER in required_roles:
        if context.compliance_approval:
            checks[gpc.CHECK_COMPLIANCE_APPROVAL] = _check(
                gpc.CHECK_COMPLIANCE_APPROVAL, schemas.PASS,
                'A compliance approval is present for this operation.',
            )
        else:
            outstanding.append(gpc.ROLE_COMPLIANCE_APPROVER)
            fail(gpc.CHECK_COMPLIANCE_APPROVAL, gpc.COMPLIANCE_APPROVAL_MISSING,
                 f'The policy requires a {gpc.GOVERNANCE_ROLE_LABELS[gpc.ROLE_COMPLIANCE_APPROVER]} '
                 'before issuance; no compliance approval is present.')
    else:
        checks[gpc.CHECK_COMPLIANCE_APPROVAL] = _check(
            gpc.CHECK_COMPLIANCE_APPROVAL, schemas.NOT_APPLICABLE,
            'This policy does not require a compliance approval.',
        )

    # Any other role the policy names has no evidence source wired, so it can
    # never be shown satisfied. Fail closed rather than pass it silently.
    for role in required_roles:
        if role in (gpc.ROLE_TREASURY_OPERATOR, gpc.ROLE_COMPLIANCE_APPROVER):
            continue
        outstanding.append(role)
        reason = gpc.role_missing_reason(role)
        if reason not in reason_codes:
            reason_codes.append(reason)

    # -- 11. Decide ---------------------------------------------------------
    allowed = not reason_codes
    return PolicyDecision(
        decision=gpc.DECISION_ALLOW if allowed else gpc.DECISION_DENY,
        reason_codes=(gpc.POLICY_SATISFIED,) if allowed else tuple(reason_codes),
        checks=schemas.checks_in_order(checks),
        policy_id=policy.policy_id,
        policy_key=policy.policy_key,
        policy_version=policy.version,
        evaluation_id=evaluation_id,
        evaluated_at=evaluated_at,
        engine_version=gpc.ENGINE_VERSION,
        required_approvals=tuple(outstanding),
        required_roles=required_roles,
        operation=operation,
        asset_id=context.asset_id,
        incident_id=context.incident_id,
        canonical_event_id=context.canonical_event_id,
        amount_usd=amount,
        simulation=bool(context.simulation),
        violation_action=policy.violation_action,
    )
