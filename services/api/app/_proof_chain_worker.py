"""Pure proof chain builder — no fastapi dependency.

Contains _ensure_workspace_live_rpc_proof_chain() extracted from monitoring_runner
so it can be imported and unit-tested without a fastapi installation.
"""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

LIVE_RPC_PROOF_CHAIN_DEDUPE_WINDOW_HOURS: int = int(
    os.getenv('LIVE_RPC_PROOF_CHAIN_DEDUPE_WINDOW_HOURS', '6')
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, default=str)


def _inspect_detection_events_target_parent(connection: Any) -> str:
    """Query pg_constraint for detection_events_target_id_fkey and return the parent table.

    Returns 'targets', 'monitored_targets', or 'unknown'.
    Production has detection_events_target_id_fkey → targets(id) after migration 0090.
    """
    row = connection.execute(
        """
        SELECT c2.relname AS parent_table
        FROM pg_constraint c
        JOIN pg_class c1 ON c1.oid = c.conrelid
        JOIN pg_class c2 ON c2.oid = c.confrelid
        WHERE c.contype = 'f'
          AND c1.relname = 'detection_events'
          AND c.conname = 'detection_events_target_id_fkey'
        LIMIT 1
        """,
    ).fetchone()
    if row is None:
        return 'unknown'
    parent = str((row.get('parent_table') if isinstance(row, dict) else row[0]) or '')
    if parent in ('targets', 'monitored_targets'):
        return parent
    return 'unknown'


def _resolve_for_targets_fk(
    connection: Any,
    workspace_id: str,
    telemetry_target_id: str,
) -> str:
    """Resolve detection_events.target_id when FK → targets(id).

    telemetry_events.target_id stores targets.id values, so step A almost always
    succeeds in production.

    A. telemetry_target_id in targets.id for this workspace → return directly.
    B. Find the most recent targets row for this workspace → return its id.
    C. No targets row → raise RuntimeError.
    """
    direct = connection.execute(
        'SELECT id FROM targets WHERE id = %s::uuid AND workspace_id = %s::uuid LIMIT 1',
        (telemetry_target_id, workspace_id),
    ).fetchone()
    if direct:
        _id = direct.get('id') if isinstance(direct, dict) else direct[0]
        return str(_id or '')

    any_row = connection.execute(
        '''
        SELECT id FROM targets
        WHERE workspace_id = %s::uuid
        ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST
        LIMIT 1
        ''',
        (workspace_id,),
    ).fetchone()
    if any_row:
        _id = any_row.get('id') if isinstance(any_row, dict) else any_row[0]
        return str(_id or '')

    raise RuntimeError(
        f'_resolve_for_targets_fk: telemetry_target_id={telemetry_target_id!r} '
        f'is not in targets.id and no other targets row exists for '
        f'workspace_id={workspace_id!r}.'
    )


def _resolve_for_monitored_targets_fk(
    connection: Any,
    workspace_id: str,
    telemetry_target_id: str,
) -> str:
    """Resolve detection_events.target_id when FK → monitored_targets(id).

    A. telemetry_target_id already in monitored_targets.id → use directly.
    B. Find via monitored_targets.target_identifier = telemetry_target_id.
    D. Upsert with deterministic UUID5, RETURNING id.
    E. Raise with diagnostics if upsert returned nothing.
    """
    # A. Direct id match.
    direct = connection.execute(
        'SELECT id FROM monitored_targets WHERE id = %s::uuid AND workspace_id = %s::uuid LIMIT 1',
        (telemetry_target_id, workspace_id),
    ).fetchone()
    if direct:
        _id = direct.get('id') if isinstance(direct, dict) else direct[0]
        return str(_id or '')

    # B. Link via target_identifier.
    by_identifier = connection.execute(
        '''
        SELECT id FROM monitored_targets
        WHERE workspace_id = %s::uuid AND target_identifier = %s
        ORDER BY enabled DESC, created_at DESC
        LIMIT 1
        ''',
        (workspace_id, telemetry_target_id),
    ).fetchone()
    if by_identifier:
        _id = by_identifier.get('id') if isinstance(by_identifier, dict) else by_identifier[0]
        return str(_id or '')

    # D. Upsert using the deterministic UUID5 from pilot.py.
    canonical_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f'canonical-target:{workspace_id}:{telemetry_target_id}'))
    upserted = connection.execute(
        '''
        INSERT INTO monitored_targets (
            id, workspace_id, asset_id, provider_type,
            target_identifier, enabled, status, created_at, updated_at
        ) VALUES (
            %s::uuid, %s::uuid, NULL, 'evm_rpc',
            %s, TRUE, 'active', NOW(), NOW()
        )
        ON CONFLICT (workspace_id, provider_type, target_identifier)
        DO UPDATE SET
            enabled = TRUE,
            status  = 'active',
            updated_at = NOW()
        RETURNING id
        ''',
        (canonical_id, workspace_id, telemetry_target_id),
    ).fetchone()
    if upserted:
        _id = upserted.get('id') if isinstance(upserted, dict) else upserted[0]
        return str(_id or '')

    # E. Preflight failure.
    available = connection.execute(
        '''
        SELECT id, target_identifier, provider_type
        FROM monitored_targets
        WHERE workspace_id = %s::uuid
        ORDER BY created_at DESC
        LIMIT 5
        ''',
        (workspace_id,),
    ).fetchall() or []
    raise RuntimeError(
        f'_resolve_for_monitored_targets_fk: cannot resolve monitored_targets.id '
        f'for telemetry_target_id={telemetry_target_id!r} workspace_id={workspace_id!r}. '
        f'Upsert returned no row. Available rows: {list(available)}.'
    )


def _resolve_detection_event_target_id(
    connection: Any,
    workspace_id: str,
    telemetry_target_id: str,
) -> str:
    """Return a valid id for use as detection_events.target_id.

    Inspects the live FK constraint at runtime to determine the parent table:
    - targets          → _resolve_for_targets_fk
    - monitored_targets → _resolve_for_monitored_targets_fk
    - unknown          → _resolve_for_monitored_targets_fk (safe fallback)
    """
    parent_table = _inspect_detection_events_target_parent(connection)
    if parent_table == 'targets':
        return _resolve_for_targets_fk(connection, workspace_id, telemetry_target_id)
    return _resolve_for_monitored_targets_fk(connection, workspace_id, telemetry_target_id)


def _resolve_monitoring_run_id(
    connection: Any,
    workspace_id: str,
) -> str:
    """Return a valid monitoring_runs.id for use as detections.monitoring_run_id.

    Identical resolution logic to resolve_monitoring_run_id in the repair script.
    Never passes a random UUID that has no parent row in monitoring_runs.
    """
    existing = connection.execute(
        '''
        SELECT id FROM monitoring_runs
        WHERE workspace_id = %s::uuid
        ORDER BY started_at DESC
        LIMIT 1
        ''',
        (workspace_id,),
    ).fetchone()
    if existing:
        _id = existing.get('id') if isinstance(existing, dict) else existing[0]
        return str(_id or '')

    new_run_id = str(uuid.uuid4())
    inserted = connection.execute(
        '''
        INSERT INTO monitoring_runs (
            id, workspace_id, started_at, completed_at, status,
            trigger_type, systems_checked_count, assets_checked_count,
            detections_created_count, alerts_created_count,
            telemetry_records_seen_count, notes
        ) VALUES (
            %s::uuid, %s::uuid, NOW(), NOW(), 'completed',
            'repair_script', 0, 0, 1, 1, 1,
            'Created by _proof_chain_worker'
        )
        RETURNING id
        ''',
        (new_run_id, workspace_id),
    ).fetchone()

    resolved_id = new_run_id
    if inserted:
        _id = inserted.get('id') if isinstance(inserted, dict) else inserted[0]
        resolved_id = str(_id or '') or new_run_id

    confirmed = connection.execute(
        'SELECT 1 FROM monitoring_runs WHERE id = %s::uuid',
        (resolved_id,),
    ).fetchone()
    if confirmed:
        return resolved_id

    raise RuntimeError(
        f'_resolve_monitoring_run_id: monitoring_runs row id={resolved_id!r} '
        f'not found after INSERT for workspace_id={workspace_id!r}.'
    )


def _resolve_response_action_mode(connection: Any) -> str:
    """Query pg_constraint for response_actions_mode_check and return the best valid mode.

    Priority: 'live_enforcement' > 'live' > 'recommended' > 'advisory' > 'manual' >
    first non-simulated value > first allowed value.

    Falls back to 'live' if the constraint is absent or unparseable.
    """
    row = connection.execute(
        """
        SELECT pg_get_constraintdef(oid) AS def
        FROM pg_constraint
        WHERE conname = 'response_actions_mode_check'
          AND conrelid = 'response_actions'::regclass
        LIMIT 1
        """,
    ).fetchone()
    if row:
        defn = str((row.get('def') if isinstance(row, dict) else row[0]) or '')
        allowed = re.findall(r"'([^']+)'", defn)
        if allowed:
            for preferred in ('live_enforcement', 'live', 'recommended', 'advisory', 'manual'):
                if preferred in allowed:
                    return preferred
            non_simulated = [v for v in allowed if v != 'simulated']
            return non_simulated[0] if non_simulated else allowed[0]
    return 'live'


def _ensure_workspace_live_rpc_proof_chain(
    connection: Any,
    *,
    workspace_id: str,
    utc_now_fn: Any = None,
) -> dict[str, Any]:
    """Return an existing policy-created live detector chain without writing rows.

    Connectivity observations (including ``live_rpc_telemetry_proof`` and
    ``monitoring_proof`` records) never create enterprise evidence. The query is
    anchored to an enabled workspace monitoring target and requires persisted
    telemetry -> detection_event/detection -> alert linkage, provider receipt
    metadata, matching on-chain transaction activity, and a non-informational
    detector result. Incidents and response actions are optional and are only
    returned when the detector policy already created them.

    Production gate: if APP_ENV=production/prod/staging and LIVE_MODE is
    disabled, simulator evidence is explicitly rejected — it can never produce
    a verified live proof bundle.
    """
    _app_env = os.getenv('APP_ENV', '').strip().lower()
    _live_mode = os.getenv('LIVE_MODE', 'true').strip().lower()
    _is_prod = _app_env in {'production', 'prod', 'staging'}
    _is_sim_mode = _live_mode in {'false', '0', 'no', 'off', 'simulator', 'demo'}

    if _is_prod and _is_sim_mode:
        return {
            'created': False,
            'reason': 'simulator_rejected_in_production',
            'error': (
                'Cannot produce verified live proof bundle from simulator or fallback evidence. '
                'APP_ENV=production requires LIVE_MODE=true and live provider evidence.'
            ),
            'evidence_state': 'SIMULATOR_EVIDENCE',
            'verified_live': False,
        }

    row = connection.execute(
        r"""
        SELECT
            te.id AS telemetry_event_id,
            de.id AS detection_event_id,
            d.id AS detection_id,
            a.id AS alert_id,
            a.incident_id,
            ra.id AS response_action_id,
            e.id AS evidence_id,
            te.target_id,
            COALESCE(t.contract_identifier, t.wallet_address, mt.target_identifier) AS target_identifier,
            te.observed_at,
            te.payload_json,
            de.detection_type,
            de.severity AS detection_event_severity,
            d.severity AS detection_severity,
            d.raw_evidence_json,
            d.monitoring_run_id,
            e.tx_hash,
            e.block_number,
            e.raw_payload_json AS evidence_payload
        FROM telemetry_events te
        JOIN targets t
          ON t.workspace_id = te.workspace_id
         AND t.id = te.target_id
         AND t.enabled = TRUE
         AND t.deleted_at IS NULL
        JOIN monitored_targets mt
          ON mt.workspace_id = t.workspace_id
         AND mt.target_identifier = t.id::text
         AND mt.enabled = TRUE
         AND mt.status = 'active'
        JOIN detection_events de
          ON de.workspace_id = te.workspace_id
         AND de.telemetry_event_id = te.id
         AND de.target_id = te.target_id
        JOIN detections d
          ON d.workspace_id = de.workspace_id
         AND d.id = (
             SELECT a2.detection_id
             FROM alerts a2
             WHERE a2.workspace_id = de.workspace_id
               AND a2.detection_event_id = de.id
               AND a2.detection_id IS NOT NULL
             ORDER BY a2.created_at DESC
             LIMIT 1
         )
        JOIN alerts a
          ON a.workspace_id = d.workspace_id
         AND a.detection_id = d.id
         AND a.detection_event_id = de.id
         AND a.alert_type <> 'monitoring_proof'
        JOIN evidence e
          ON e.workspace_id = a.workspace_id
         AND e.alert_id = a.id
         AND e.target_id = te.target_id
        LEFT JOIN response_actions ra
          ON ra.workspace_id = a.workspace_id
         AND ra.alert_id = a.id
        WHERE te.workspace_id = %s::uuid
          AND te.evidence_source = 'live'
          AND COALESCE(te.payload_json->'provider_receipt', '{}'::jsonb) <> '{}'::jsonb
          AND COALESCE(e.tx_hash, '') <> ''
          AND COALESCE(e.raw_payload_json->>'target_identifier',
                       COALESCE(t.contract_identifier, t.wallet_address, mt.target_identifier))
              = COALESCE(t.contract_identifier, t.wallet_address, mt.target_identifier)
          AND COALESCE(e.raw_payload_json->>'activity_matched', 'false') = 'true'
          AND LOWER(COALESCE(de.severity, d.severity, ''))
              NOT IN ('', 'info', 'informational', 'none')
          AND LOWER(COALESCE(de.detection_type, d.detection_type, ''))
              NOT IN (
                  'live_rpc_block_observed', 'live_rpc_event_observed',
                  'live_rpc_telemetry_proof', 'monitoring_proof',
                  'monitoring_proof_chain'
              )
        ORDER BY te.observed_at DESC
        LIMIT 1
        """,
        (workspace_id,),
    ).fetchone()
    if row is None:
        return {'created': False, 'reason': 'no_qualifying_target_detector_chain'}

    record = dict(row)
    payload = record.get('payload_json') or {}
    evidence_payload = record.get('evidence_payload') or {}
    raw_evidence = record.get('raw_evidence_json') or {}
    for name, value in (
        ('payload_json', payload),
        ('evidence_payload', evidence_payload),
        ('raw_evidence_json', raw_evidence),
    ):
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except Exception:
                parsed = {}
            if name == 'payload_json':
                payload = parsed
            elif name == 'evidence_payload':
                evidence_payload = parsed
            else:
                raw_evidence = parsed

    provider_receipt = payload.get('provider_receipt') if isinstance(payload, dict) else None
    detector_result = raw_evidence.get('detector_result') if isinstance(raw_evidence, dict) else None
    if not isinstance(provider_receipt, dict) or not provider_receipt:
        return {'created': False, 'reason': 'provider_receipt_missing'}
    if not isinstance(detector_result, dict) or detector_result.get('triggered') is not True:
        return {'created': False, 'reason': 'detector_not_triggered'}

    telemetry_event_id = str(record.get('telemetry_event_id') or '')
    detection_event_id = str(record.get('detection_event_id') or '')
    detection_id = str(record.get('detection_id') or '')
    alert_id = str(record.get('alert_id') or '')
    return {
        'created': False,
        'reason': 'existing_policy_detector_chain',
        'workspace_id': workspace_id,
        'target_id': str(record.get('target_id') or ''),
        'target_identifier': str(record.get('target_identifier') or ''),
        'target_configured': True,
        'telemetry_event_id': telemetry_event_id,
        'detection_event_id': detection_event_id,
        'detection_id': detection_id,
        'alert_id': alert_id,
        'incident_id': str(record.get('incident_id') or '') or None,
        'response_action_id': str(record.get('response_action_id') or '') or None,
        'evidence_package_id': str(record.get('evidence_id') or ''),
        'monitoring_run_id': str(record.get('monitoring_run_id') or '') or None,
        'evidence_source': 'live',
        'source_type': 'rpc_polling',
        'observed_at': record.get('observed_at'),
        'detection_name': str(record.get('detection_type') or ''),
        'severity': str(
            record.get('detection_event_severity') or record.get('detection_severity') or ''
        ),
        'detector_result': detector_result,
        'provider_receipt': provider_receipt,
        'on_chain_activity': {
            'matched': True,
            'transaction_hash': str(record.get('tx_hash') or ''),
            'block_number': record.get('block_number'),
            'target_identifier': str(record.get('target_identifier') or ''),
        },
        'persisted_linkage': {
            'persisted': True,
            'telemetry_event_id': telemetry_event_id,
            'detection_event_id': detection_event_id,
            'detection_id': detection_id,
            'alert_id': alert_id,
        },
    }
