"""Request-level handlers for Screen 11's Policies workspace.

Contract:
  * GET  /workspace/governance/policies                    — policies in the
                                                             workspace, plus the
                                                             vocabulary the
                                                             simulator renders.
  * GET  /workspace/governance/policies/{ref}              — one policy.
  * GET  /workspace/governance/policies/{ref}/history      — immutable version
                                                             history, newest
                                                             first. An empty
                                                             history is reported
                                                             as empty, never
                                                             fabricated.
  * POST /workspace/governance/policies/{ref}/simulate     — READ-ONLY
                                                             deterministic
                                                             evaluation.
  * PATCH /workspace/governance/policies/{ref}             — versioned edit.
                                                             Requires
                                                             ``security.manage``.

Every query is workspace-scoped through the resolved workspace context, so a
user in workspace A can never read or edit workspace B's policies.

What the simulate endpoint deliberately does NOT do
---------------------------------------------------
It does not import the response-action executor, the chain adapters, the
incident writer, or any approval command. Its ONLY write is one
``governance_policy_evaluations`` row stamped ``simulation = TRUE``, which is
excluded from every production counter. It cannot execute a transaction, pause a
contract, create or approve a response action, or mutate incident, policy, or
settlement state (§7).

Fail-closed (§16)
-----------------
A backend failure is an HTTP error, never an ALLOW. Missing storage is 503.
Invalid simulator input is 400. A policy that cannot be found still produces a
deterministic DENY / POLICY_NOT_FOUND rather than a silent success.
"""

from __future__ import annotations

import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from services.api.app import governance as governance_guard
from services.api.app import pilot
from services.api.app.domains.governance_policy import config as gpc
from services.api.app.domains.governance_policy import engine, explanation, service
from services.api.app.domains.governance_policy import schemas

try:  # fastapi is stubbed in the offline test runner
    from fastapi import HTTPException, status
except Exception:  # pragma: no cover
    HTTPException = pilot.HTTPException  # type: ignore
    status = pilot.status  # type: ignore

logger = logging.getLogger(__name__)

#: Upper bound on a simulated amount. Guards the NUMERIC(38, 2) column; a value
#: past it is a validation error, never a silently truncated number.
MAX_AMOUNT_USD = Decimal('9999999999999999999999999999999999.99')


def _storage_unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            'code': 'policy_storage_unavailable',
            'message': 'Policy storage is provisioning. Try again shortly.',
        },
    )


def _bad_request(code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={'code': code, 'message': message})


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={'code': 'policy_not_found', 'message': 'Policy not found in this workspace.'},
    )


def _conflict(code: str, message: str, **extra: Any) -> HTTPException:
    detail: dict[str, Any] = {'code': code, 'message': message}
    detail.update(extra)
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def _read_context(connection: Any, request: Any) -> tuple[dict[str, Any], dict[str, Any], bool]:
    """Authenticate, resolve the workspace, and report edit authority.

    Reading a policy and running a simulation need no special permission beyond
    workspace membership; EDITING needs ``security.manage``. ``can_manage`` is
    computed from the canonical permission model so the UI reflects what the
    backend will actually allow — and the backend still enforces it
    independently on every write.
    """
    user = pilot.authenticate_with_connection(connection, request)
    workspace_context = pilot.resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
    role = pilot._normalize_workspace_role(str(workspace_context['role']))
    can_manage = pilot._workspace_permission_granted(
        connection, workspace_context['workspace_id'], role, gpc.POLICY_EDIT_PERMISSION,
    )
    return user, workspace_context, bool(can_manage)


def simulator_vocabulary() -> dict[str, Any]:
    """The option lists the simulator renders.

    Served from the backend so the UI can never offer an operation, settlement
    state, or business event the engine does not understand.
    """
    return {
        'operations': [{'value': v, 'label': gpc.OPERATION_LABELS[v]} for v in gpc.OPERATIONS],
        'business_events': [{'value': v, 'label': gpc.BUSINESS_EVENT_LABELS[v]} for v in gpc.BUSINESS_EVENTS],
        'settlement_states': [{'value': v, 'label': gpc.SETTLEMENT_LABELS[v]} for v in gpc.SETTLEMENT_STATES],
        'settlement_requirements': [
            {'value': v, 'label': gpc.SETTLEMENT_REQUIREMENT_LABELS[v]} for v in gpc.SETTLEMENT_REQUIREMENTS
        ],
        'governance_roles': [
            {'value': v, 'label': gpc.GOVERNANCE_ROLE_LABELS[v], 'permission': gpc.ROLE_PERMISSIONS[v]}
            for v in gpc.GOVERNANCE_ROLES
        ],
        'statuses': [{'value': v, 'label': gpc.STATUS_LABELS[v]} for v in gpc.STATUSES],
        'decision_authority': schemas.DECISION_AUTHORITY,
        'ai_authority': schemas.AI_AUTHORITY,
        'engine_version': gpc.ENGINE_VERSION,
        # Starter values for the Create Policy FORM only. Not a policy, not
        # policy state, and never persisted until an authorized operator submits.
        'policy_templates': gpc.policy_templates_payload(),
    }


# --------------------------------------------------------------------------
# Reads
# --------------------------------------------------------------------------
def list_policies_endpoint(request: Any) -> dict[str, Any]:
    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        _user, workspace_context, can_manage = _read_context(connection, request)
        workspace_id = workspace_context['workspace_id']
        if not service.storage_ready(connection):
            raise _storage_unavailable()
        policies = service.list_policies(connection, workspace_id=workspace_id)
        return {
            'workspace_id': workspace_id,
            'policies': [p.as_dict() for p in policies],
            'can_manage': can_manage,
            'edit_permission': gpc.POLICY_EDIT_PERMISSION,
            'vocabulary': simulator_vocabulary(),
        }


def policy_detail_endpoint(policy_ref: str, request: Any) -> dict[str, Any]:
    pilot.require_live_mode()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        _user, workspace_context, can_manage = _read_context(connection, request)
        workspace_id = workspace_context['workspace_id']
        if not service.storage_ready(connection):
            raise _storage_unavailable()
        policy = service.get_policy(connection, workspace_id=workspace_id, policy_ref=policy_ref)
        if policy is None:
            raise _not_found()
        return {
            'workspace_id': workspace_id,
            'policy': policy.as_dict(),
            'can_manage': can_manage,
            'edit_permission': gpc.POLICY_EDIT_PERMISSION,
            'vocabulary': simulator_vocabulary(),
        }


def policy_history_endpoint(policy_ref: str, request: Any) -> dict[str, Any]:
    pilot.require_live_mode()
    cfg = gpc.engine_config()
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        _user, workspace_context, _can_manage = _read_context(connection, request)
        workspace_id = workspace_context['workspace_id']
        if not service.storage_ready(connection):
            raise _storage_unavailable()
        policy = service.get_policy(connection, workspace_id=workspace_id, policy_ref=policy_ref)
        if policy is None:
            raise _not_found()
        rows = service.list_versions(
            connection, workspace_id=workspace_id, policy_id=policy.policy_id,
            limit=int(cfg['history_limit']),
        )
        # Reuses the EXISTING Screen 11 actor lookup rather than adding a second one.
        actors = governance_guard._lookup_users(connection, {
            str(r['changed_by_user_id']) for r in rows if r.get('changed_by_user_id')
        }) if rows else {}
        versions = []
        for row in rows:
            actor = actors.get(str(row.get('changed_by_user_id') or ''), {})
            versions.append({
                'version': int(row.get('version') or 0),
                'status': str(row.get('status') or ''),
                'change_summary': str(row.get('change_summary') or ''),
                'previous_values': pilot._json_safe_value(row.get('previous_values') or {}),
                'new_values': pilot._json_safe_value(row.get('new_values') or {}),
                'snapshot': pilot._json_safe_value(row.get('snapshot') or {}),
                'changed_by': actor.get('email') or actor.get('full_name') or 'Unknown user',
                'changed_by_user_id': str(row['changed_by_user_id']) if row.get('changed_by_user_id') else None,
                'changed_at': pilot._json_safe_value(row.get('changed_at')),
            })
        return {
            'workspace_id': workspace_id,
            'policy_id': policy.policy_id,
            'policy_key': policy.policy_key,
            'current_version': policy.version,
            'current_status': policy.status,
            # An empty list means NO history has been recorded — the UI renders an
            # honest empty state. It never means "no changes were made".
            'versions': versions,
        }


# --------------------------------------------------------------------------
# Simulation — read-only, deterministic
# --------------------------------------------------------------------------
def _validate_simulation_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize simulator input.

    Rejects anything the engine cannot evaluate rather than coercing it into a
    guess. Note what is NOT read from the body: the operator's role, the daily
    issuance total, the policy version, and the decision — those are resolved
    server-side (§19).
    """
    if not isinstance(payload, dict):
        raise _bad_request('invalid_payload', 'A JSON object is required.')

    operation = gpc.normalize_operation(payload.get('operation'))
    if operation is None:
        raise _bad_request(
            'invalid_operation',
            f'operation must be one of: {", ".join(gpc.OPERATIONS)}.',
        )

    amount_raw = payload.get('amount_usd', payload.get('amount'))
    amount: Optional[Decimal] = None
    if amount_raw is not None and str(amount_raw).strip() != '':
        try:
            amount = Decimal(str(amount_raw).strip().replace(',', ''))
        except (InvalidOperation, ValueError, TypeError):
            raise _bad_request('invalid_amount', 'amount_usd must be a decimal number.')
        if amount.is_nan() or amount.is_infinite():
            raise _bad_request('invalid_amount', 'amount_usd must be a finite decimal number.')
        if amount < 0:
            raise _bad_request('invalid_amount', 'amount_usd must not be negative.')
        if amount > MAX_AMOUNT_USD:
            raise _bad_request('invalid_amount', 'amount_usd exceeds the supported range.')

    business_event = payload.get('business_event')
    if business_event is not None and str(business_event).strip() and gpc.normalize_business_event(business_event) is None:
        raise _bad_request(
            'invalid_business_event',
            f'business_event must be one of: {", ".join(gpc.BUSINESS_EVENTS)}.',
        )

    settlement = payload.get('settlement_status')
    if settlement is not None and str(settlement).strip() and gpc.normalize_settlement(settlement) is None:
        raise _bad_request(
            'invalid_settlement_status',
            f'settlement_status must be one of: {", ".join(gpc.SETTLEMENT_STATES)}.',
        )

    # Canonical lifecycle identifiers travel through to the stored evaluation, so
    # they are validated here rather than handed to the database as-is. event_id
    # is the canonical event key (free-form text); asset_id and incident_id are
    # UUID foreign keys and a malformed one is a client error, not a 500.
    return {
        'operation': operation,
        'amount_usd': amount,
        'operator_id': str(payload.get('operator_id') or '').strip() or None,
        'business_event': gpc.normalize_business_event(business_event),
        'settlement_status': gpc.normalize_settlement(settlement),
        'compliance_approval': bool(payload.get('compliance_approval')),
        'asset_id': _optional_uuid(payload.get('asset_id'), 'asset_id'),
        'incident_id': _optional_uuid(payload.get('incident_id'), 'incident_id'),
        'event_id': str(payload.get('event_id') or '').strip()[:200] or None,
    }


def _optional_uuid(value: Any, field: str) -> Optional[str]:
    text = str(value or '').strip()
    if not text:
        return None
    if not service._is_uuid(text):
        raise _bad_request(f'invalid_{field}', f'{field} must be a UUID.')
    return text


def simulate_endpoint(policy_ref: str, payload: dict[str, Any], request: Any) -> dict[str, Any]:
    """Evaluate a hypothetical operation against a stored policy.

    Predictive and read-only. The decision comes from ``engine.evaluate_policy``
    and from nothing else; the AI layer is handed the finished decision and can
    only attach a sentence to it.
    """
    pilot.require_live_mode()
    cfg = gpc.engine_config()
    normalized = _validate_simulation_payload(payload)
    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        user, workspace_context, can_manage = _read_context(connection, request)
        workspace_id = workspace_context['workspace_id']
        if not service.storage_ready(connection):
            raise _storage_unavailable()

        policy = service.get_policy(connection, workspace_id=workspace_id, policy_ref=policy_ref)
        now = pilot.utc_now()
        context = service.build_context(
            connection, workspace_id=workspace_id, policy=policy,
            payload=normalized, now=now, simulation=True,
        )
        # THE decision. Deterministic code, pure function, no I/O, no model.
        decision = engine.evaluate_policy(policy, context, now=now)
        decision_dict = decision.as_dict()

        recorded = False
        if cfg['record_simulations']:
            recorded = service.record_evaluation(
                connection, workspace_id=workspace_id, decision=decision,
                context=context, user_id=str(user['id']),
            )
            if recorded:
                connection.commit()

        service.log_event(
            'governance_policy_simulation_run', workspace_id=workspace_id,
            policy_key=decision.policy_key, policy_version=decision.policy_version,
            decision=decision.decision, evaluation_id=decision.evaluation_id,
            reason_codes=','.join(decision.reason_codes) or None,
        )
        # Narrative only. merge_ai_explanation rejects every deterministic key,
        # so nothing below can change what was decided above.
        explained = explanation.explain(decision_dict)
        explained['policy'] = policy.as_dict() if policy else None
        explained['can_manage'] = can_manage
        explained['recorded'] = recorded
        explained['audit_event'] = 'POLICY_SIMULATION_RUN'
        return explained


# --------------------------------------------------------------------------
# Edit — versioned, RBAC-gated
# --------------------------------------------------------------------------
def _validate_policy_fields(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize the governance fields present in a payload.

    Shared by create and update so one vocabulary, one range check and one error
    code govern both — a value the editor rejects cannot enter through create.
    Only known governance fields are read; an unknown key is ignored rather than
    written blindly.
    """
    if not isinstance(payload, dict):
        raise _bad_request('invalid_payload', 'A JSON object is required.')
    changes: dict[str, Any] = {}

    if 'name' in payload:
        name = str(payload.get('name') or '').strip()
        if not name:
            raise _bad_request('invalid_name', 'name must not be empty.')
        changes['name'] = name[:200]

    if 'operation' in payload:
        operation = gpc.normalize_operation(payload.get('operation'))
        if operation is None:
            raise _bad_request('invalid_operation', f'operation must be one of: {", ".join(gpc.OPERATIONS)}.')
        changes['operation'] = operation

    if 'status' in payload:
        status_value = gpc.normalize_status(payload.get('status'))
        if status_value is None:
            raise _bad_request('invalid_status', f'status must be one of: {", ".join(gpc.STATUSES)}.')
        changes['status'] = status_value

    if 'required_business_event' in payload:
        raw = payload.get('required_business_event')
        if raw is None or str(raw).strip() == '':
            changes['required_business_event'] = None
        else:
            event = gpc.normalize_business_event(raw)
            if event is None:
                raise _bad_request('invalid_business_event',
                                   f'required_business_event must be one of: {", ".join(gpc.BUSINESS_EVENTS)}.')
            changes['required_business_event'] = event

    if 'settlement_requirement' in payload:
        raw = payload.get('settlement_requirement')
        if raw is None or str(raw).strip() == '':
            changes['settlement_requirement'] = None
        else:
            requirement = gpc.normalize_settlement_requirement(raw)
            if requirement is None:
                raise _bad_request('invalid_settlement_requirement',
                                   f'settlement_requirement must be one of: {", ".join(gpc.SETTLEMENT_REQUIREMENTS)}.')
            changes['settlement_requirement'] = requirement

    if 'allowed_window_utc' in payload:
        window = payload.get('allowed_window_utc')
        if window is None:
            changes['allowed_window_start_utc'] = None
            changes['allowed_window_end_utc'] = None
        elif isinstance(window, dict):
            start = str(window.get('start') or '').strip()
            end = str(window.get('end') or '').strip()
            if not start and not end:
                changes['allowed_window_start_utc'] = None
                changes['allowed_window_end_utc'] = None
            elif engine._parse_hhmm(start) is None or engine._parse_hhmm(end) is None:
                raise _bad_request('invalid_allowed_window',
                                   'allowed_window_utc start and end must be HH:MM in 24-hour UTC.')
            else:
                changes['allowed_window_start_utc'] = start
                changes['allowed_window_end_utc'] = end
        else:
            raise _bad_request('invalid_allowed_window',
                               'allowed_window_utc must be an object with start and end, or null.')

    if 'maximum_daily_amount_usd' in payload:
        raw = payload.get('maximum_daily_amount_usd')
        if raw is None or str(raw).strip() == '':
            changes['maximum_daily_amount_usd'] = None
        else:
            try:
                cap = Decimal(str(raw).strip().replace(',', ''))
            except (InvalidOperation, ValueError, TypeError):
                raise _bad_request('invalid_maximum_daily_amount', 'maximum_daily_amount_usd must be a decimal number.')
            if cap.is_nan() or cap.is_infinite() or cap < 0 or cap > MAX_AMOUNT_USD:
                raise _bad_request('invalid_maximum_daily_amount',
                                   'maximum_daily_amount_usd must be a finite, non-negative value within range.')
            changes['maximum_daily_amount_usd'] = cap

    if 'required_roles' in payload:
        raw = payload.get('required_roles') or []
        if not isinstance(raw, (list, tuple)):
            raise _bad_request('invalid_required_roles', 'required_roles must be a list.')
        roles: list[str] = []
        for item in raw:
            role = gpc.normalize_role(item)
            if role is None:
                raise _bad_request('invalid_required_roles',
                                   f'required_roles entries must be one of: {", ".join(gpc.GOVERNANCE_ROLES)}.')
            if role not in roles:
                roles.append(role)
        changes['required_roles'] = roles

    if 'violation_action' in payload:
        action = str(payload.get('violation_action') or '').strip().upper()
        if action not in gpc.VIOLATION_ACTIONS:
            raise _bad_request('invalid_violation_action',
                               f'violation_action must be one of: {", ".join(gpc.VIOLATION_ACTIONS)}.')
        changes['violation_action'] = action

    return changes


def _validate_update_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a policy edit. At least one editable field must be supplied."""
    changes = _validate_policy_fields(payload)
    if not changes:
        raise _bad_request('no_changes', 'No editable policy field was supplied.')
    return changes


#: A customer-facing policy identifier. Constrained so it stays quotable in an
#: audit record and in a URL path segment.
_POLICY_KEY_PATTERN = re.compile(r'^[A-Z0-9][A-Z0-9._-]{2,63}$')


def _validate_create_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Validate a new policy.

    Note what is NOT read from the body: ``workspace_id`` (the tenant comes from
    the authenticated session), ``origin`` (a created policy is always
    ``customer`` — a client cannot label its own row as a demo seed, or launder a
    seeded one into customer configuration), ``version`` and ``id``. Supplying
    them is not an error; they are simply ignored, as unknown keys already are.
    """
    values = _validate_policy_fields(payload)

    policy_key = str(payload.get('policy_key') or payload.get('policy_id') or '').strip().upper()
    if not _POLICY_KEY_PATTERN.match(policy_key):
        raise _bad_request(
            'invalid_policy_key',
            'policy_id must be 3-64 characters of A-Z, 0-9, dot, underscore or hyphen (for example POL-MINT-007).',
        )
    values['policy_key'] = policy_key

    if 'name' not in values:
        raise _bad_request('invalid_name', 'name is required.')
    if 'operation' not in values:
        raise _bad_request(
            'invalid_operation', f'operation is required and must be one of: {", ".join(gpc.OPERATIONS)}.',
        )
    # A policy that does not say otherwise starts as a DRAFT, which the engine
    # denies. An unstated status must never default to one that can authorize.
    values.setdefault('status', gpc.STATUS_DRAFT)
    values.setdefault('violation_action', gpc.VIOLATION_ACTION_DENY)
    values.setdefault('required_roles', [])
    for field in (
        'asset_id', 'required_business_event', 'settlement_requirement',
        'allowed_window_start_utc', 'allowed_window_end_utc', 'maximum_daily_amount_usd',
    ):
        values.setdefault(field, None)
    return values


def create_policy_endpoint(payload: dict[str, Any], request: Any) -> dict[str, Any]:
    """Create a governance policy.

    Backend-enforced RBAC: ``security.manage`` is required here regardless of
    what the frontend rendered, and the gate runs before anything is read or
    written. The workspace is the one the SESSION resolves to, never one the
    body names, so a policy cannot be planted in another tenant.

    Nothing about this path runs on page load: a policy exists because an
    authorized person submitted this request.
    """
    pilot.require_live_mode()
    cfg = gpc.engine_config()

    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        # RBAC gate FIRST, before the payload is even validated. Note this
        # deliberately differs from update_policy_endpoint, which validates
        # first: a caller without security.manage must learn that they may not
        # create a policy, not which of their fields were malformed.
        user, workspace_context = pilot._require_workspace_permission(
            connection, request, gpc.POLICY_EDIT_PERMISSION,
        )
        workspace_id = workspace_context['workspace_id']
        if not service.storage_ready(connection):
            raise _storage_unavailable()
        values = _validate_create_payload(payload)

        # A guard rail, not a business limit: two concurrent creators can both
        # pass this count and overshoot by one. The unique index remains the only
        # hard constraint, which is the one that actually protects correctness.
        limit = int(cfg['max_policies_per_workspace'])
        if service.count_policies(connection, workspace_id=workspace_id) >= limit:
            raise _conflict(
                'policy_limit_reached',
                f'This workspace already holds the maximum of {limit} governance policies.',
                limit=limit,
            )

        outcome = service.create_policy(
            connection, workspace_id=workspace_id, values=values,
            user_id=str(user['id']), now=pilot.utc_now(),
        )
        if outcome['status'] == 'duplicate':
            raise _conflict(
                'policy_already_exists',
                'A policy with this ID already exists in this workspace. Open it instead of creating a second one.',
                policy_key=outcome['policy_key'],
            )

        created = service.get_policy(
            connection, workspace_id=workspace_id, policy_ref=outcome['policy_id'],
        )
        if created is None:
            raise _storage_unavailable()
        service.audit_policy_event(
            connection, action='governance_policy.created', policy=created,
            workspace_id=workspace_id, user_id=str(user['id']), request=request,
            metadata={'origin': created.origin, 'version': created.version},
        )
        connection.commit()
        service.log_event(
            'governance_policy_created', workspace_id=workspace_id,
            policy_key=created.policy_key, version=created.version, origin=created.origin,
        )
        return {'status': 'created', 'policy': created.as_dict(), 'can_manage': True}


def update_policy_endpoint(policy_ref: str, payload: dict[str, Any], request: Any) -> dict[str, Any]:
    """Apply a versioned policy edit.

    Backend-enforced RBAC: ``security.manage`` is required here regardless of
    what the frontend rendered. A material governance change bumps the version
    and appends an immutable history row; the audit event goes to the canonical
    hash-chained ``audit_logs``.
    """
    pilot.require_live_mode()
    changes = _validate_update_payload(payload)
    expected_version = payload.get('expected_version')
    if expected_version is not None:
        try:
            expected_version = int(expected_version)
        except (ValueError, TypeError):
            raise _bad_request('invalid_expected_version', 'expected_version must be an integer.')

    with pilot.pg_connection() as connection:
        pilot.ensure_pilot_schema(connection)
        # RBAC gate. Raises 403 before anything is read or written.
        user, workspace_context = pilot._require_workspace_permission(
            connection, request, gpc.POLICY_EDIT_PERMISSION,
        )
        workspace_id = workspace_context['workspace_id']
        if not service.storage_ready(connection):
            raise _storage_unavailable()
        policy = service.get_policy(connection, workspace_id=workspace_id, policy_ref=policy_ref)
        if policy is None:
            raise _not_found()

        now = pilot.utc_now()
        outcome = service.apply_policy_update(
            connection, workspace_id=workspace_id, policy=policy, changes=changes,
            user_id=str(user['id']), expected_version=expected_version, now=now,
        )
        if outcome['status'] == 'conflict':
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    'code': 'policy_version_conflict',
                    'message': 'This policy changed since you opened it. Reload the current version before saving.',
                    'current_version': outcome['current_version'],
                },
            )
        if outcome['status'] == 'unchanged':
            return {'status': 'unchanged', 'policy': policy.as_dict(), 'can_manage': True}

        updated = service.get_policy(connection, workspace_id=workspace_id, policy_ref=policy.policy_id)
        target = updated or policy
        action = 'governance_policy.updated'
        if 'status' in changes and changes['status'] != policy.status:
            if changes['status'] == gpc.STATUS_ACTIVE:
                action = 'governance_policy.activated'
            elif changes['status'] == gpc.STATUS_DISABLED:
                action = 'governance_policy.disabled'
        service.audit_policy_event(
            connection, action=action, policy=target, workspace_id=workspace_id,
            user_id=str(user['id']), request=request,
            metadata={
                'material_fields': outcome['material'],
                'previous_version': policy.version,
                'change_summary': service.summarize_change(service._material_diff(policy, changes)),
            },
        )
        connection.commit()
        service.log_event(
            'governance_policy_updated', workspace_id=workspace_id, policy_key=target.policy_key,
            version=target.version, action=action,
        )
        return {'status': 'updated', 'policy': target.as_dict(), 'can_manage': True}
