"""Workspace-scoped persistence for Governance & Policy.

Every statement in this module carries the workspace id. There is no unscoped
read and no cross-tenant join: a user in workspace A can never reach workspace
B's policies, versions, or evaluations.

Responsibilities:
  * load policies and their immutable version history,
  * resolve the parts of an EvaluationContext that the client must not assert —
    the operator's authority and today's issuance total (§19),
  * persist an evaluation record (the object Screen 8 consumes),
  * apply a versioned policy edit: bump the version, append the immutable
    history row, and write the canonical audit event, all in one transaction.

This module does not decide anything. ALLOW/DENY comes from ``engine`` alone.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from services.api.app import pilot
from services.api.app.domains.governance_policy import config as gpc
from services.api.app.domains.governance_policy import schemas
from services.api.app.domains.governance_policy.schemas import (
    EvaluationContext,
    PolicyDecision,
    PolicyDefinition,
)

logger = logging.getLogger(__name__)

POLICIES_TABLE = 'governance_policies'
VERSIONS_TABLE = 'governance_policy_versions'
EVALUATIONS_TABLE = 'governance_policy_evaluations'

#: The governance fields whose change is MATERIAL — a change to any of them
#: bumps the policy version and appends an immutable history row. Renaming a
#: policy is not a governance change and does not consume a version.
MATERIAL_FIELDS = (
    'operation',
    'status',
    'asset_id',
    'required_business_event',
    'settlement_requirement',
    'allowed_window_start_utc',
    'allowed_window_end_utc',
    'maximum_daily_amount_usd',
    'required_roles',
    'violation_action',
)

#: Editable through the Screen 11 policy editor. ``name`` is editable but not
#: material; ``policy_key`` is immutable so an evaluation record can never be
#: orphaned from the policy that produced it.
EDITABLE_FIELDS = ('name',) + MATERIAL_FIELDS


def log_event(event: str, **fields: Any) -> None:
    ordered = ' '.join(f'{k}={v}' for k, v in fields.items() if v is not None)
    logger.info('event=%s %s', event, ordered)


def _table_exists(connection: Any, name: str) -> bool:
    try:
        row = connection.execute('SELECT to_regclass(%s) IS NOT NULL AS ok', (f'public.{name}',)).fetchone()
    except Exception:
        return False
    return bool((row or {}).get('ok'))


def storage_ready(connection: Any) -> bool:
    """True only when every policy table this domain writes exists."""
    return all(_table_exists(connection, t) for t in (POLICIES_TABLE, VERSIONS_TABLE, EVALUATIONS_TABLE))


def _decimal(value: Any) -> Optional[Decimal]:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError, TypeError):
        return None


def _roles_tuple(value: Any) -> tuple[str, ...]:
    """Normalize the stored required_roles JSON into canonical role keys.

    An unrecognized stored role is KEPT, not dropped: the engine fails closed on
    a role it cannot evidence, and silently discarding it here would turn an
    unsatisfiable requirement into no requirement at all.
    """
    if value is None:
        return ()
    raw = value
    if isinstance(raw, (str, bytes)):
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return ()
    if not isinstance(raw, (list, tuple)):
        return ()
    seen: list[str] = []
    for item in raw:
        key = str(item or '').strip().upper()
        if key and key not in seen:
            seen.append(key)
    return tuple(seen)


def _is_uuid(value: Any) -> bool:
    try:
        uuid.UUID(str(value))
        return True
    except (ValueError, TypeError, AttributeError):
        return False


def policy_from_row(row: Any) -> PolicyDefinition:
    """Map a governance_policies row onto the pure PolicyDefinition."""
    data = dict(row)
    return PolicyDefinition(
        policy_id=str(data.get('id') or ''),
        policy_key=str(data.get('policy_key') or ''),
        name=str(data.get('name') or ''),
        operation=str(data.get('operation') or '').upper(),
        status=str(data.get('status') or '').upper(),
        version=int(data.get('version') or 1),
        workspace_id=str(data.get('workspace_id') or ''),
        asset_id=str(data['asset_id']) if data.get('asset_id') else None,
        required_business_event=(str(data['required_business_event']).upper()
                                 if data.get('required_business_event') else None),
        settlement_requirement=(str(data['settlement_requirement']).upper()
                                if data.get('settlement_requirement') else None),
        allowed_window_start_utc=(str(data['allowed_window_start_utc'])
                                  if data.get('allowed_window_start_utc') else None),
        allowed_window_end_utc=(str(data['allowed_window_end_utc'])
                                if data.get('allowed_window_end_utc') else None),
        maximum_daily_amount_usd=_decimal(data.get('maximum_daily_amount_usd')),
        required_roles=_roles_tuple(data.get('required_roles')),
        violation_action=str(data.get('violation_action') or gpc.VIOLATION_ACTION_DENY).upper(),
        origin=str(data.get('origin') or gpc.ORIGIN_CUSTOMER),
        created_at=data.get('created_at'),
        updated_at=data.get('updated_at'),
        updated_by=str(data['updated_by_user_id']) if data.get('updated_by_user_id') else None,
    )


_POLICY_COLUMNS = '''
    id, workspace_id, policy_key, name, operation, status, version, asset_id,
    required_business_event, settlement_requirement, allowed_window_start_utc,
    allowed_window_end_utc, maximum_daily_amount_usd, required_roles,
    violation_action, origin, created_by_user_id, updated_by_user_id,
    created_at, updated_at
'''


# --------------------------------------------------------------------------
# Workspace-scoped reads
# --------------------------------------------------------------------------
def list_policies(connection: Any, *, workspace_id: str) -> list[PolicyDefinition]:
    """Every policy in the workspace, ACTIVE first then newest."""
    if not _table_exists(connection, POLICIES_TABLE):
        return []
    rows = connection.execute(
        f'''SELECT {_POLICY_COLUMNS} FROM {POLICIES_TABLE}
            WHERE workspace_id = %s
            ORDER BY (status = 'ACTIVE') DESC, updated_at DESC, policy_key ASC''',
        (workspace_id,),
    ).fetchall()
    return [policy_from_row(r) for r in (rows or [])]


def get_policy(connection: Any, *, workspace_id: str, policy_ref: str) -> Optional[PolicyDefinition]:
    """One policy by UUID id or by customer-facing policy_key.

    Both lookups are workspace-scoped, so a policy id guessed from another
    tenant resolves to nothing rather than to that tenant's policy.
    """
    if not _table_exists(connection, POLICIES_TABLE) or not str(policy_ref or '').strip():
        return None
    ref = str(policy_ref).strip()
    if _is_uuid(ref):
        row = connection.execute(
            f'SELECT {_POLICY_COLUMNS} FROM {POLICIES_TABLE} WHERE id = %s::uuid AND workspace_id = %s',
            (ref, workspace_id),
        ).fetchone()
    else:
        row = connection.execute(
            f'SELECT {_POLICY_COLUMNS} FROM {POLICIES_TABLE} WHERE policy_key = %s AND workspace_id = %s',
            (ref, workspace_id),
        ).fetchone()
    return policy_from_row(row) if row else None


def list_versions(connection: Any, *, workspace_id: str, policy_id: str, limit: int = 50) -> list[dict[str, Any]]:
    """Immutable version history, newest first. Empty list means NO history has
    been recorded — the caller renders that as an honest empty state, never as
    an invented trail."""
    if not _table_exists(connection, VERSIONS_TABLE):
        return []
    rows = connection.execute(
        f'''SELECT id, version, status, snapshot, previous_values, new_values, change_summary,
                   changed_by_user_id, changed_at
            FROM {VERSIONS_TABLE}
            WHERE workspace_id = %s AND policy_id = %s::uuid
            ORDER BY version DESC
            LIMIT %s''',
        (workspace_id, policy_id, int(limit)),
    ).fetchall()
    return [dict(r) for r in (rows or [])]


def resolve_operator_authority(
    connection: Any, *, workspace_id: str, operator_user_id: Optional[str],
) -> Optional[bool]:
    """Does this operator hold Treasury Operator authority IN THIS WORKSPACE?

    Resolved server-side from ``workspace_members`` + the canonical permission
    map. The client's claim about a role is never consulted (§19).

    Returns True/False when the membership was read, and None when it could not
    be — which the engine treats as "not evidenced" and therefore fails closed.
    """
    operator = str(operator_user_id or '').strip()
    if not operator or not _is_uuid(operator):
        return False
    try:
        row = connection.execute(
            'SELECT role FROM workspace_members WHERE workspace_id = %s AND user_id = %s::uuid',
            (workspace_id, operator),
        ).fetchone()
    except Exception:
        logger.exception('governance_policy_operator_lookup_failed workspace_id=%s', workspace_id)
        return None
    if row is None:
        # Not a member of this workspace: the authority cannot be evidenced.
        return False
    role = pilot._normalize_workspace_role(str(dict(row).get('role') or ''))
    permission = gpc.ROLE_PERMISSIONS[gpc.ROLE_TREASURY_OPERATOR]
    try:
        return bool(pilot._workspace_permission_granted(connection, workspace_id, role, permission))
    except Exception:
        logger.exception('governance_policy_permission_lookup_failed workspace_id=%s', workspace_id)
        return None


def daily_total_usd(
    connection: Any, *, workspace_id: str, policy_id: str, now: datetime,
) -> Optional[Decimal]:
    """Amount already permitted under this policy in the current UTC day.

    Counts ENFORCEMENT decisions only (``simulation = FALSE`` and
    ``decision = 'ALLOW'``): a Screen 11 what-if must never move a production
    counter (§7).

    Returns None when the total cannot be established — the engine then fails
    closed on any policy that actually imposes a daily cap.
    """
    if not _table_exists(connection, EVALUATIONS_TABLE):
        return None
    moment = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    day_start = moment.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    try:
        row = connection.execute(
            f'''SELECT COALESCE(SUM(amount_usd), 0) AS total
                FROM {EVALUATIONS_TABLE}
                WHERE workspace_id = %s AND policy_id = %s::uuid
                  AND simulation = FALSE AND decision = 'ALLOW'
                  AND evaluated_at >= %s AND evaluated_at < %s''',
            (workspace_id, policy_id, day_start, day_end),
        ).fetchone()
    except Exception:
        logger.exception('governance_policy_daily_total_failed workspace_id=%s', workspace_id)
        return None
    if row is None:
        return None
    return _decimal(dict(row).get('total')) or Decimal('0')


def build_context(
    connection: Any,
    *,
    workspace_id: str,
    policy: Optional[PolicyDefinition],
    payload: dict[str, Any],
    now: datetime,
    simulation: bool,
) -> EvaluationContext:
    """Assemble the EvaluationContext from the request plus SERVER-RESOLVED facts.

    The request may name an operation, an amount, a business event, a settlement
    state, and an operator. It may NOT assert the operator's role, the daily
    total, the policy version, or the decision — those are read here from
    canonical state.
    """
    operator_id = str(payload.get('operator_id') or '').strip() or None
    requires_operator = bool(policy and gpc.ROLE_TREASURY_OPERATOR in policy.required_roles)
    operator_authority = (
        resolve_operator_authority(connection, workspace_id=workspace_id, operator_user_id=operator_id)
        if requires_operator else None
    )
    prior_total = (
        daily_total_usd(connection, workspace_id=workspace_id, policy_id=policy.policy_id, now=now)
        if policy and policy.maximum_daily_amount_usd is not None else None
    )
    return EvaluationContext(
        operation=payload.get('operation'),
        amount_usd=_decimal(payload.get('amount_usd')),
        operator_id=operator_id,
        operator_has_treasury_role=operator_authority,
        business_event=payload.get('business_event'),
        settlement_status=payload.get('settlement_status'),
        compliance_approval=bool(payload.get('compliance_approval')),
        evaluated_at=now,
        daily_total_usd=prior_total,
        asset_id=str(payload.get('asset_id')).strip() if payload.get('asset_id') else None,
        incident_id=str(payload.get('incident_id')).strip() if payload.get('incident_id') else None,
        canonical_event_id=str(payload.get('event_id')).strip() if payload.get('event_id') else None,
        simulation=bool(simulation),
    )


# --------------------------------------------------------------------------
# Writes
# --------------------------------------------------------------------------
def record_evaluation(
    connection: Any,
    *,
    workspace_id: str,
    decision: PolicyDecision,
    context: EvaluationContext,
    user_id: Optional[str],
) -> bool:
    """Persist one evaluation record. Returns False when storage is absent.

    The row is the object Screen 8 consumes. ``simulation`` is written from the
    decision itself, so a simulation can never be stored as an enforcement
    decision by a caller passing the wrong flag.
    """
    if not _table_exists(connection, EVALUATIONS_TABLE):
        return False
    connection.execute(
        f'''INSERT INTO {EVALUATIONS_TABLE} (
                id, workspace_id, policy_id, policy_key, policy_version, asset_id, incident_id,
                canonical_event_id, operation, decision, reason_codes, required_approvals, checks,
                amount_usd, input_snapshot, simulation, engine_version, evaluated_by_user_id, evaluated_at
            ) VALUES (
                %s::uuid, %s, %s::uuid, %s, %s, %s::uuid, %s::uuid, %s, %s, %s,
                %s::jsonb, %s::jsonb, %s::jsonb, %s, %s::jsonb, %s, %s, %s::uuid, %s
            )''',
        (
            decision.evaluation_id, workspace_id, decision.policy_id, decision.policy_key,
            decision.policy_version, decision.asset_id, decision.incident_id,
            decision.canonical_event_id, decision.operation, decision.decision,
            json.dumps(list(decision.reason_codes)),
            json.dumps(list(decision.required_approvals)),
            json.dumps([c.as_dict() for c in decision.checks]),
            str(decision.amount_usd) if decision.amount_usd is not None else None,
            json.dumps(context.as_snapshot(), default=str),
            bool(decision.simulation), decision.engine_version, user_id, decision.evaluated_at,
        ),
    )
    return True


def _material_diff(before: PolicyDefinition, changes: dict[str, Any]) -> dict[str, tuple[Any, Any]]:
    """Which MATERIAL governance fields this edit actually changes."""
    current = _editable_values(before)
    diff: dict[str, tuple[Any, Any]] = {}
    for field in MATERIAL_FIELDS:
        if field not in changes:
            continue
        old, new = current.get(field), changes[field]
        if _comparable(old) != _comparable(new):
            diff[field] = (old, new)
    return diff


def _comparable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return value


def _editable_values(policy: PolicyDefinition) -> dict[str, Any]:
    return {
        'name': policy.name,
        'operation': policy.operation,
        'status': policy.status,
        'asset_id': policy.asset_id,
        'required_business_event': policy.required_business_event,
        'settlement_requirement': policy.settlement_requirement,
        'allowed_window_start_utc': policy.allowed_window_start_utc,
        'allowed_window_end_utc': policy.allowed_window_end_utc,
        'maximum_daily_amount_usd': policy.maximum_daily_amount_usd,
        'required_roles': list(policy.required_roles),
        'violation_action': policy.violation_action,
    }


def _json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, tuple):
        return list(value)
    return value


def apply_policy_update(
    connection: Any,
    *,
    workspace_id: str,
    policy: PolicyDefinition,
    changes: dict[str, Any],
    user_id: str,
    expected_version: Optional[int],
    now: datetime,
) -> dict[str, Any]:
    """Apply a validated edit, versioning it.

    Contract:
      * a MATERIAL change bumps ``version`` and appends ONE immutable
        governance_policy_versions row in the SAME statement batch, so history
        can never disagree with the current row;
      * a non-material change (rename only) updates in place and does NOT
        consume a version number;
      * ``expected_version`` is optimistic concurrency: a stale editor is
        rejected with a conflict rather than silently overwriting a newer policy.
        The guard is enforced TWICE — once against the row this request read, and
        again in the UPDATE's own WHERE clause, so a writer that commits between
        those two moments still loses the race instead of forking the history.

    Returns {'status': 'updated'|'unchanged'|'conflict', ...}. The caller commits.
    """
    if expected_version is not None and int(expected_version) != int(policy.version):
        return {'status': 'conflict', 'current_version': policy.version}

    material = _material_diff(policy, changes)
    current = _editable_values(policy)
    renamed = 'name' in changes and str(changes['name']) != policy.name
    if not material and not renamed:
        return {'status': 'unchanged', 'version': policy.version}

    merged = dict(current)
    for field in EDITABLE_FIELDS:
        if field in changes:
            merged[field] = changes[field]

    next_version = policy.version + (1 if material else 0)
    # The WHERE clause carries the version this edit was built on, so two writers
    # who both read version 7 cannot both publish version 8: the second one
    # matches no row. RETURNING is how we learn that happened — the
    # expected_version check above only compares against the row THIS request
    # read, which a concurrent commit can invalidate before we write.
    updated = connection.execute(
        f'''UPDATE {POLICIES_TABLE}
            SET name = %s, operation = %s, status = %s, asset_id = %s::uuid,
                required_business_event = %s, settlement_requirement = %s,
                allowed_window_start_utc = %s, allowed_window_end_utc = %s,
                maximum_daily_amount_usd = %s, required_roles = %s::jsonb,
                violation_action = %s, version = %s, updated_by_user_id = %s::uuid, updated_at = %s
            WHERE id = %s::uuid AND workspace_id = %s AND version = %s
            RETURNING version''',
        (
            merged['name'], merged['operation'], merged['status'], merged['asset_id'],
            merged['required_business_event'], merged['settlement_requirement'],
            merged['allowed_window_start_utc'], merged['allowed_window_end_utc'],
            str(merged['maximum_daily_amount_usd']) if merged['maximum_daily_amount_usd'] is not None else None,
            json.dumps(list(merged['required_roles'] or [])),
            merged['violation_action'], next_version, user_id, now,
            policy.policy_id, workspace_id, policy.version,
        ),
    ).fetchone()
    if updated is None:
        # Nothing was written. Report the conflict rather than appending a
        # history row for an edit that did not happen — which would both
        # fabricate an audit trail and collide with the concurrent writer's
        # (policy_id, version) row.
        current = get_policy(connection, workspace_id=workspace_id, policy_ref=policy.policy_id)
        return {'status': 'conflict', 'current_version': current.version if current else policy.version}

    if material and _table_exists(connection, VERSIONS_TABLE):
        snapshot = dict(policy.as_dict())
        snapshot.update({k: _json_value(v) for k, v in merged.items()})
        snapshot['version'] = next_version
        connection.execute(
            f'''INSERT INTO {VERSIONS_TABLE} (
                    id, workspace_id, policy_id, version, status, snapshot,
                    previous_values, new_values, change_summary, changed_by_user_id, changed_at
                ) VALUES (%s::uuid, %s, %s::uuid, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s::uuid, %s)''',
            (
                str(uuid.uuid4()), workspace_id, policy.policy_id, next_version, merged['status'],
                json.dumps(snapshot, default=str),
                json.dumps({k: _json_value(v[0]) for k, v in material.items()}, default=str),
                json.dumps({k: _json_value(v[1]) for k, v in material.items()}, default=str),
                summarize_change(material), user_id, now,
            ),
        )

    return {
        'status': 'updated',
        'version': next_version,
        'material': sorted(material.keys()),
        'renamed': renamed,
    }


_FIELD_LABELS: dict[str, str] = {
    'operation': 'Operation',
    'status': 'Status',
    'asset_id': 'Scoped asset',
    'required_business_event': 'Required business event',
    'settlement_requirement': 'Settlement requirement',
    'allowed_window_start_utc': 'Allowed window start (UTC)',
    'allowed_window_end_utc': 'Allowed window end (UTC)',
    'maximum_daily_amount_usd': 'Maximum issuance',
    'required_roles': 'Required roles',
    'violation_action': 'On violation',
}


def summarize_change(material: dict[str, tuple[Any, Any]]) -> str:
    """A short, factual description of what changed. Rendered on the history
    row; derived from the diff, never written by a model."""
    if not material:
        return 'No material governance change.'
    parts = []
    for field in MATERIAL_FIELDS:
        if field not in material:
            continue
        old, new = material[field]
        label = _FIELD_LABELS.get(field, field.replace('_', ' ').capitalize())
        parts.append(f'{label} changed: {_render(old)} → {_render(new)}')
    return '; '.join(parts)


def _render(value: Any) -> str:
    if value is None:
        return 'not set'
    if isinstance(value, (list, tuple)):
        return ', '.join(str(v) for v in value) if value else 'none'
    return str(value)


def audit_policy_event(
    connection: Any,
    *,
    action: str,
    policy: PolicyDefinition,
    workspace_id: str,
    user_id: str,
    request: Any,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Write the canonical audit event for a policy change.

    Reuses ``pilot.log_audit`` — the hash-chained, append-only audit_logs the
    existing Screen 11 change log already reads. No parallel audit system.
    """
    payload = {
        'policy_key': policy.policy_key,
        'policy_id': policy.policy_id,
        'operation': policy.operation,
        'status': policy.status,
        'version': policy.version,
    }
    payload.update(metadata or {})
    pilot.log_audit(
        connection,
        action=action,
        entity_type='governance_policy',
        entity_id=policy.policy_id,
        request=request,
        user_id=user_id,
        workspace_id=workspace_id,
        metadata=payload,
    )
