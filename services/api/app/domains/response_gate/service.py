"""Workspace-scoped reads that resolve the facts the execution gate reasons over.

Nothing here decides anything: it reads canonical state and hands it to
``engine.evaluate_gate``. Every read is workspace-scoped, and every failure to
establish a fact is reported to the engine as the honest "not established"
value, never as a satisfied condition.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from services.api.app.domains.governance_policy import config as gpc
from services.api.app.domains.response_gate import config as rgc
from services.api.app.domains.response_gate.engine import (
    ApprovalRecord,
    ExecutionGate,
    GateInputs,
    evaluate_gate,
)

logger = logging.getLogger(__name__)

APPROVALS_TABLE = 'response_action_approvals'
EVALUATIONS_TABLE = 'governance_policy_evaluations'
POLICIES_TABLE = 'governance_policies'


def _row_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    try:
        return dict(row)
    except (TypeError, ValueError):
        return {}


#: Reserved cache key holding the names of facts a read could not establish.
#: "We could not look" is a DIFFERENT answer from "there is none", and the gate
#: must fail closed on the first, so the two are never collapsed into one False.
_UNREADABLE_KEY = '__unreadable__'


def _note_unreadable(cache: Optional[dict[str, Any]], fact: str) -> None:
    """Record that a canonical fact could not be read. Any entry closes the gate."""
    if cache is None:
        return
    facts = cache.setdefault(_UNREADABLE_KEY, [])
    if fact not in facts:
        facts.append(fact)


def unreadable_facts(cache: Optional[dict[str, Any]]) -> tuple[str, ...]:
    """The facts this read pass failed to establish, in the order they failed."""
    if not cache:
        return ()
    return tuple(cache.get(_UNREADABLE_KEY) or ())


def _table_exists(connection: Any, name: str, cache: Optional[dict[str, Any]] = None) -> bool:
    """Fail-closed table probe, mirroring the governance_policy convention.

    ``cache`` is an optional per-request memo so listing many actions does not
    re-probe the same tables once per row. A read failure recorded in it is
    deliberately shared across that request: a database that could not answer for
    one action could not answer for the others either, and every gate in the
    response should fail closed together rather than one at a time.
    """
    if cache is not None and (key := f'table:{name}') in cache:
        return bool(cache[key])
    try:
        row = connection.execute(
            'SELECT to_regclass(%s) IS NOT NULL AS present', (f'public.{name}',),
        ).fetchone()
    except Exception:  # pragma: no cover - a probe failure is UNREADABLE, not absent
        _note_unreadable(cache, f'table:{name}')
        if cache is not None:
            cache[f'table:{name}'] = False
        return False
    present = bool(_row_dict(row).get('present'))
    if cache is not None:
        cache[f'table:{name}'] = present
    return present


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        moment = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc).isoformat()
    text = str(value).strip()
    return text or None


def _json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        import json
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _column_exists(
    connection: Any, table: str, column: str, cache: Optional[dict[str, Any]] = None,
) -> bool:
    """Whether an optional column has been migrated in yet. Fail-closed."""
    if cache is not None and (key := f'column:{table}.{column}') in cache:
        return bool(cache[key])
    try:
        row = connection.execute(
            '''SELECT 1 AS present FROM information_schema.columns
               WHERE table_name = %s AND column_name = %s''',
            (table, column),
        ).fetchone()
    except Exception:  # pragma: no cover - probe failure is treated as absent
        if cache is not None:
            cache[f'column:{table}.{column}'] = False
        return False
    present = bool(_row_dict(row).get('present'))
    if cache is not None:
        cache[f'column:{table}.{column}'] = present
    return present


# --------------------------------------------------------------------------
# Policy evaluation
# --------------------------------------------------------------------------
def latest_policy_evaluation(
    connection: Any,
    *,
    workspace_id: str,
    incident_id: Optional[str] = None,
    canonical_event_id: Optional[str] = None,
    asset_id: Optional[str] = None,
    response_action_id: Optional[str] = None,
    cache: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    """The most recent ENFORCEMENT decision Screen 11 recorded for this context.

    Simulations are excluded: a Screen 11 what-if is predictive and authorizes
    nothing, so it can never become Screen 8's execution gate. The lookup is
    workspace-scoped and matches on the canonical lifecycle identifiers the
    evaluation already carries, so Screen 8 reads the SAME event object the rest
    of the workflow uses rather than creating a parallel one.
    """
    if not _table_exists(connection, EVALUATIONS_TABLE, cache):
        return None
    event_id = str(canonical_event_id or '').strip() or None
    incident = str(incident_id or '').strip() or None
    asset = str(asset_id or '').strip() or None
    # The evaluation produced FOR THIS ACTION, when one exists. The lifecycle
    # identifiers below are shared: two actions on one incident, or two on one
    # asset, match the same rows, so an action could otherwise be shown a verdict
    # reached for a sibling. The producer stamps the action it evaluated into the
    # snapshot, so the specific match is PREFERRED over the shared ones rather
    # than replacing them — a row without that stamp still resolves as before.
    action = str(response_action_id or '').strip() or None
    if not any((event_id, incident, asset, action)):
        return None
    # 0149 added the authoritative role list. Degrade to the pre-migration shape
    # rather than failing the read; build_gate_inputs then falls back to the
    # outstanding `required_approvals` list, which is exactly the old behavior.
    has_required_roles = _column_exists(connection, EVALUATIONS_TABLE, 'required_roles', cache)
    roles_select = 'required_roles' if has_required_roles else "'[]'::jsonb AS required_roles"
    try:
        row = connection.execute(
            f'''SELECT id, policy_id, policy_key, policy_version, decision, reason_codes,
                       required_approvals, {roles_select}, asset_id, incident_id,
                       canonical_event_id, operation, evaluated_at
                FROM {EVALUATIONS_TABLE}
                WHERE workspace_id = %s
                  AND simulation = FALSE
                  AND (
                        (%s::text IS NOT NULL AND input_snapshot->>'response_action_id' = %s::text)
                     OR (%s::text IS NOT NULL AND canonical_event_id = %s::text)
                     OR (%s::uuid IS NOT NULL AND incident_id = %s::uuid)
                     OR (%s::uuid IS NOT NULL AND asset_id = %s::uuid)
                  )
                ORDER BY (%s::text IS NOT NULL
                          AND input_snapshot->>'response_action_id' = %s::text) DESC,
                         evaluated_at DESC
                LIMIT 1''',
            (workspace_id, action, action, event_id, event_id, incident, incident,
             asset, asset, action, action),
        ).fetchone()
    except Exception:
        logger.exception('response_gate_evaluation_read_failed workspace_id=%s', workspace_id)
        _note_unreadable(cache, 'policy_evaluation')
        return None
    return _row_dict(row) or None


def _current_policy_version(
    connection: Any, *, workspace_id: str, policy_id: Any,
    cache: Optional[dict[str, Any]] = None,
) -> Optional[int]:
    """The version the governing policy carries NOW, for the staleness check."""
    key = str(policy_id or '').strip()
    if not key or not _table_exists(connection, POLICIES_TABLE, cache):
        return None
    try:
        row = connection.execute(
            f'SELECT version FROM {POLICIES_TABLE} WHERE id = %s::uuid AND workspace_id = %s',
            (key, workspace_id),
        ).fetchone()
    except Exception:
        logger.exception('response_gate_policy_version_read_failed workspace_id=%s', workspace_id)
        _note_unreadable(cache, 'policy_current_version')
        return None
    raw = _row_dict(row).get('version')
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def resolve_action_asset_id(
    connection: Any,
    *,
    workspace_id: str,
    alert_id: Optional[str],
    incident_id: Optional[str],
    cache: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    """The asset this action's operation concerns, read from canonical rows.

    Screen 8 took the asset ONLY from the action's own ``chain_linked_ids``, and
    no writer populates that key. An ASSET-SCOPED governance policy was therefore
    invisible to ``_policy_governs``, which matched workspace-wide policies alone
    — so an action such a policy governs was reported NOT_APPLICABLE ("no policy
    applies"), and the engine treats NOT_APPLICABLE as passing. That is the
    fail-open direction: an asset nobody resolved is not evidence that nothing
    governs the action.

    Walks the same canonical links the enforcement resolver walks, all
    workspace-scoped:

        alert -> target -> asset
        threat_detections.primary_asset_id, via that table's own
        linked_alert_id / linked_incident_id

    Fail-closed: a read that RAISES is recorded as unreadable, which makes
    ``build_gate_inputs`` report NOT_EVALUATED rather than NOT_APPLICABLE.
    """
    alert = str(alert_id or '').strip() or None
    incident = str(incident_id or '').strip() or None
    if not (alert or incident):
        return None
    key = f'asset_for:{alert or ""}:{incident or ""}'
    if cache is not None and key in cache:
        return cache[key]

    asset: Optional[str] = None
    if alert and _table_exists(connection, 'alerts', cache):
        try:
            row = _row_dict(connection.execute(
                'SELECT target_id FROM alerts WHERE id = %s::uuid AND workspace_id = %s',
                (alert, workspace_id),
            ).fetchone())
        except Exception:
            logger.exception('response_gate_alert_read_failed workspace_id=%s', workspace_id)
            _note_unreadable(cache, 'action_asset')
            row = {}
        target_id = str(row.get('target_id') or '').strip() or None
        if target_id and _table_exists(connection, 'targets', cache):
            try:
                target = _row_dict(connection.execute(
                    'SELECT asset_id FROM targets WHERE id = %s::uuid AND workspace_id = %s',
                    (target_id, workspace_id),
                ).fetchone())
            except Exception:
                logger.exception('response_gate_target_read_failed workspace_id=%s', workspace_id)
                _note_unreadable(cache, 'action_asset')
                target = {}
            asset = str(target.get('asset_id') or '').strip() or None

    if not asset and _table_exists(connection, 'threat_detections', cache):
        # The detection's own linkage columns — never alerts.detection_id, which
        # references the unrelated `detections` table (migration 0042).
        for column, value in (('linked_alert_id', alert), ('linked_incident_id', incident)):
            if not value:
                continue
            try:
                row = _row_dict(connection.execute(
                    f"""SELECT primary_asset_id FROM threat_detections
                        WHERE {column} = %s::uuid AND workspace_id = %s
                        ORDER BY detected_at DESC, id ASC LIMIT 1""",
                    (value, workspace_id),
                ).fetchone())
            except Exception:
                logger.exception('response_gate_detection_read_failed workspace_id=%s', workspace_id)
                _note_unreadable(cache, 'action_asset')
                break
            asset = str(row.get('primary_asset_id') or '').strip() or None
            if asset:
                break

    if cache is not None:
        cache[key] = asset
    return asset


def _policy_governs(
    connection: Any, *, workspace_id: str, asset_id: Optional[str],
    cache: Optional[dict[str, Any]] = None,
) -> bool:
    """Whether ANY active policy governs this workspace/asset.

    This is what separates the two honest "no ALLOW" states: when a policy
    governs but no enforcement decision exists, the gate reports
    POLICY_EVALUATION_MISSING and stays locked; when nothing governs, it reports
    NOT_APPLICABLE — never ALLOW in either case.
    """
    if not _table_exists(connection, POLICIES_TABLE, cache):
        return False
    asset = str(asset_id or '').strip() or None
    try:
        row = connection.execute(
            f'''SELECT 1 AS present FROM {POLICIES_TABLE}
                WHERE workspace_id = %s AND status = %s
                  AND (asset_id IS NULL OR (%s::uuid IS NOT NULL AND asset_id = %s::uuid))
                LIMIT 1''',
            (workspace_id, gpc.STATUS_ACTIVE, asset, asset),
        ).fetchone()
    except Exception:
        logger.exception('response_gate_policy_scope_read_failed workspace_id=%s', workspace_id)
        _note_unreadable(cache, 'policy_scope')
        return False
    return bool(_row_dict(row).get('present'))


# --------------------------------------------------------------------------
# Approvals
# --------------------------------------------------------------------------
def approval_records(
    connection: Any,
    *,
    workspace_id: str,
    subject_domain: str,
    subject_id: str,
    action_version: int,
    cache: Optional[dict[str, Any]] = None,
) -> tuple[ApprovalRecord, ...]:
    """Every persisted decision for ONE action version, with its approver role.

    Reads the dedicated approval store only. Degrades to role-less records when
    migration 0148 has not been applied yet, so a decision is never lost — but a
    role is never INFERRED for one, because an inferred role would satisfy a
    quorum nobody actually signed.
    """
    if not _table_exists(connection, APPROVALS_TABLE, cache):
        return ()
    has_role_column = _column_exists(connection, APPROVALS_TABLE, 'approval_role', cache)
    role_select = 'approval_role' if has_role_column else 'NULL AS approval_role'
    try:
        rows = connection.execute(
            f'''SELECT approver_user_id, approver_role, decision, created_at, {role_select}
                FROM {APPROVALS_TABLE}
                WHERE workspace_id = %s AND subject_domain = %s AND subject_id = %s::uuid
                  AND action_version = %s
                ORDER BY created_at ASC''',
            (workspace_id, subject_domain, subject_id, action_version),
        ).fetchall()
    except Exception:
        logger.exception('response_gate_approval_read_failed workspace_id=%s', workspace_id)
        _note_unreadable(cache, 'approvals')
        return ()
    records: list[ApprovalRecord] = []
    for raw in rows or []:
        row = _row_dict(raw)
        records.append(
            ApprovalRecord(
                approver_user_id=str(row.get('approver_user_id') or ''),
                decision=str(row.get('decision') or ''),
                role=rgc.normalize_approver_role(row.get('approval_role')),
                approver_label=str(row.get('approver_role') or '') or None,
                decided_at=_iso(row.get('created_at')),
            )
        )
    return tuple(records)


def approver_holds_role(
    connection: Any, *, workspace_id: str, workspace_role: Any, role: str,
) -> bool:
    """Does this workspace role evidence the named GOVERNANCE role?

    Resolved server-side from the canonical permission map — the client's claim
    about which role it is approving for is verified here, never trusted. Fails
    closed on any unreadable permission.
    """
    from services.api.app import pilot  # local import: matches the domain convention

    permission = rgc.APPROVER_ROLE_PERMISSIONS.get(str(role or '').strip().upper())
    if not permission:
        return False
    role_key = str(workspace_role or '').strip()
    if not role_key:
        return False
    try:
        canonical = pilot._normalize_workspace_role(role_key)
        return bool(pilot._workspace_permission_granted(connection, workspace_id, canonical, permission))
    except Exception:
        logger.exception('response_gate_role_permission_read_failed workspace_id=%s', workspace_id)
        return False


# --------------------------------------------------------------------------
# Incident state
# --------------------------------------------------------------------------
def incident_status(
    connection: Any, *, workspace_id: str, incident_id: Optional[str],
    cache: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    """The linked incident's canonical status, or None when there is no incident.

    A read that FAILS is recorded as an unreadable fact so the gate closes. The
    ``cache`` this writes into is the caller's; without it the failure would be
    indistinguishable from "this action has no incident", which is an answer that
    permits execution. (The handler previously named a ``cache`` this function
    never took, so a real outage raised NameError instead of failing closed.)
    """
    key = str(incident_id or '').strip()
    if not key:
        return None
    try:
        row = connection.execute(
            'SELECT status FROM incidents WHERE id = %s::uuid AND workspace_id = %s',
            (key, workspace_id),
        ).fetchone()
    except Exception:
        logger.exception('response_gate_incident_read_failed workspace_id=%s', workspace_id)
        _note_unreadable(cache, 'incident_status')
        return None
    value = _row_dict(row).get('status')
    return str(value).strip().lower() if value else None


# --------------------------------------------------------------------------
# Composition
# --------------------------------------------------------------------------
def build_gate_inputs(
    connection: Any,
    *,
    workspace_id: str,
    action: dict[str, Any],
    lifecycle: dict[str, Any],
    required_quorum: int,
    action_version: int,
    subject_domain: str = 'response_action',
    approval_required: bool = True,
    requester_authorized: bool = True,
    requester_permission_reason: Optional[str] = None,
    execution_authority_available: bool = True,
    execution_adapter_label: Optional[str] = None,
    execution_adapter_required: bool = False,
    quorum_authority: str = 'workspace_approvers',
    now: Optional[datetime] = None,
    cache: Optional[dict[str, Any]] = None,
) -> GateInputs:
    """Resolve every fact for ONE action and package it for the engine."""
    # Always work through a cache, even when the caller supplied none: it is also
    # where a read that FAILED (rather than came back empty) is recorded, and the
    # engine closes the gate on any such entry.
    cache = {} if cache is None else cache
    action_id = str(action.get('id') or '')
    metadata = action.get('execution_metadata') if isinstance(action.get('execution_metadata'), dict) else {}
    chain = metadata.get('chain_linked_ids') if isinstance(metadata.get('chain_linked_ids'), dict) else {}
    incident_id = str(action.get('incident_id') or chain.get('incident_id') or '').strip() or None
    alert_id = str(action.get('alert_id') or chain.get('alert_id') or '').strip() or None
    asset_id = str(metadata.get('asset_id') or chain.get('asset_id') or '').strip() or None
    canonical_event_id = str(metadata.get('event_id') or chain.get('event_id') or '').strip() or None
    if not asset_id:
        # Resolved from canonical rows, because no writer fills chain_linked_ids
        # with an asset — and without it an asset-scoped policy is invisible to
        # the scope probe below, which reports NOT_APPLICABLE and passes.
        asset_id = resolve_action_asset_id(
            connection, workspace_id=workspace_id, alert_id=alert_id,
            incident_id=incident_id, cache=cache,
        )

    evaluation = latest_policy_evaluation(
        connection, workspace_id=workspace_id, incident_id=incident_id,
        canonical_event_id=canonical_event_id, asset_id=asset_id,
        response_action_id=action_id, cache=cache,
    )
    if evaluation:
        policy_decision = str(evaluation.get('decision') or '').strip().upper() or rgc.POLICY_NOT_EVALUATED
        policy_id = str(evaluation.get('policy_id') or '') or None
        current_version = _current_policy_version(
            connection, workspace_id=workspace_id, policy_id=policy_id, cache=cache,
        )
        # The AUTHORITATIVE role list is every role the governing policy names
        # (0149). `required_approvals` is only what the policy engine could not
        # evidence at evaluation time — empty on an ALLOW — so reading it alone
        # made the role-scoped human quorum unreachable in the one case it
        # matters: a policy that permits the operation but still demands named
        # sign-offs before the response runs. Falls back to the outstanding list
        # for a pre-0149 evaluation row, which is the previous behavior.
        policy_roles = tuple(str(r) for r in _json_list(evaluation.get('required_roles')))
        required_roles = policy_roles or tuple(
            str(r) for r in _json_list(evaluation.get('required_approvals'))
        )
        policy_reason_codes = tuple(str(c) for c in _json_list(evaluation.get('reason_codes')))
        try:
            policy_version = int(evaluation.get('policy_version'))
        except (TypeError, ValueError):
            policy_version = None
        evaluation_id = str(evaluation.get('id') or '') or None
        evaluated_at = _iso(evaluation.get('evaluated_at'))
        policy_key = str(evaluation.get('policy_key') or '') or None
    else:
        # No enforcement decision. Distinguish "a policy governs this and none was
        # recorded" (locked) from "nothing governs this action" (not applicable).
        governed = _policy_governs(
            connection, workspace_id=workspace_id, asset_id=asset_id, cache=cache,
        )
        # ...and never claim the SECOND when the read itself failed. "No policy
        # applies" is a positive finding about the workspace; a query that could
        # not run establishes nothing, so it reports NOT_EVALUATED. The gate is
        # closed either way by GATE_FACTS_UNAVAILABLE, but the two states are
        # exported and audited, and only one of them is true.
        policy_read_failed = any(
            fact in {'policy_evaluation', 'policy_scope', 'action_asset',
                     f'table:{EVALUATIONS_TABLE}', f'table:{POLICIES_TABLE}'}
            for fact in unreadable_facts(cache)
        )
        policy_decision = (
            rgc.POLICY_NOT_EVALUATED if (governed or policy_read_failed) else rgc.POLICY_NOT_APPLICABLE
        )
        policy_id = policy_key = evaluation_id = evaluated_at = None
        policy_version = current_version = None
        required_roles = ()
        policy_reason_codes = ()

    approvals = approval_records(
        connection, workspace_id=workspace_id, subject_domain=subject_domain,
        subject_id=action_id, action_version=action_version, cache=cache,
    )

    # An expiry is only enforced when one was AUTHORED on the action. A NULL
    # expiry is an authored decision ("this action does not expire"), the same
    # convention migration 0147 uses for a NULL policy constraint — it is never
    # read as "unknown".
    expires_at = _iso(metadata.get('expires_at') or action.get('expires_at'))

    # Resolved BEFORE the GateInputs literal so `unreadable_facts` below observes
    # a failure here too. Relying on keyword-argument evaluation order for that
    # would make a fail-closed guarantee depend on the order of the lines.
    linked_incident_status = incident_status(
        connection, workspace_id=workspace_id, incident_id=incident_id, cache=cache,
    )

    return GateInputs(
        action_id=action_id,
        policy_decision=policy_decision,
        policy_reason_codes=policy_reason_codes,
        policy_id=policy_id,
        policy_key=policy_key,
        policy_version=policy_version,
        policy_current_version=current_version,
        evaluation_id=evaluation_id,
        evaluated_at=evaluated_at,
        required_roles=required_roles,
        approvals=approvals,
        required_quorum=int(required_quorum or 0),
        approval_required=bool(approval_required),
        lifecycle_approval_status=str(lifecycle.get('approval_status') or 'not_required'),
        quorum_authority=str(quorum_authority or 'workspace_approvers'),
        action_status=str(action.get('status') or 'pending'),
        execution_status=str(lifecycle.get('execution_status') or 'not_started'),
        rejected=str(lifecycle.get('approval_status') or '') == 'rejected',
        cancelled=bool(action.get('rolled_back_at')) or str(action.get('status') or '') in {'canceled', 'cancelled'},
        expires_at=expires_at,
        now=now,
        incident_id=incident_id,
        incident_status=linked_incident_status,
        requester_authorized=bool(requester_authorized),
        requester_permission_reason=requester_permission_reason,
        unreadable_facts=unreadable_facts(cache),
        execution_authority_available=bool(execution_authority_available),
        execution_adapter_configured=rgc.live_execution_configured(),
        execution_adapter_required=bool(execution_adapter_required),
        execution_adapter_label=execution_adapter_label,
        chain={
            'event_id': canonical_event_id,
            'asset_id': asset_id,
            'detection_id': str(chain.get('detection_id') or '') or None,
            'alert_id': str(action.get('alert_id') or chain.get('alert_id') or '') or None,
            'incident_id': incident_id,
            'action_id': action_id or None,
            'policy_id': policy_id,
            'playbook_id': str((action.get('playbook') or {}).get('runbook_id') or '') or None
            if isinstance(action.get('playbook'), dict) else None,
        },
    )


def build_gate(connection: Any, **kwargs: Any) -> ExecutionGate:
    """Resolve the facts and evaluate the gate. The only entry point callers need."""
    return evaluate_gate(build_gate_inputs(connection, **kwargs))
