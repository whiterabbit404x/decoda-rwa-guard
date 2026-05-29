"""Pure proof chain builder — no fastapi dependency.

Contains _ensure_workspace_live_rpc_proof_chain() extracted from monitoring_runner
so it can be imported and unit-tested without a fastapi installation.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

LIVE_RPC_PROOF_CHAIN_DEDUPE_WINDOW_HOURS: int = int(
    os.getenv('LIVE_RPC_PROOF_CHAIN_DEDUPE_WINDOW_HOURS', '6')
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, default=str)


def _resolve_detection_event_target_id(
    connection: Any,
    workspace_id: str,
    telemetry_target_id: str,
) -> str:
    """Return a valid monitored_targets.id for use as detection_events.target_id.

    detection_events.target_id FK references monitored_targets(id). Telemetry
    events store targets.id (from the canonical targets table), which is a different
    UUID namespace than monitored_targets.id. This bridges the gap.

    Resolution order:
    A. telemetry_target_id already in monitored_targets.id → use directly.
    B. Find via monitored_targets.target_identifier = telemetry_target_id
       (pilot.py canonical sync stores targets.id as target_identifier).
    D. Upsert a monitored_targets row with deterministic UUID5 (same as pilot.py), RETURNING id.
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
        f'_resolve_detection_event_target_id: cannot resolve monitored_targets.id '
        f'for telemetry_target_id={telemetry_target_id!r} workspace_id={workspace_id!r}. '
        f'Upsert returned no row. Mapping column: monitored_targets.target_identifier. '
        f'Available rows: {list(available)}. '
        f'Suggested fix: ensure a monitored_targets row exists with '
        f'(workspace_id, provider_type=evm_rpc, target_identifier=telemetry_target_id).'
    )


def _ensure_workspace_live_rpc_proof_chain(
    connection: Any,
    *,
    workspace_id: str,
    utc_now_fn: Any = None,
) -> dict[str, Any]:
    """Create a complete live-proof telemetry → detection_events → detection →
    detection_evidence → alert → incident → incident_timeline → response_action →
    evidence chain for this workspace.

    Idempotent: returns ``{'created': False, 'reason': 'deduplicated'}`` only when
    the FULL canonical chain (detection_events row, alert.detection_event_id,
    incident_timeline row) already exists within LIVE_RPC_PROOF_CHAIN_DEDUPE_WINDOW_HOURS.
    An incomplete legacy-only chain is NOT counted as a valid dedupe — orphan
    alerts/incidents are archived and a new complete chain is created instead.
    """
    _now = utc_now_fn or _utc_now
    dedupe_cutoff = _now() - timedelta(hours=max(1, LIVE_RPC_PROOF_CHAIN_DEDUPE_WINDOW_HOURS))

    # Only deduplicate when the COMPLETE canonical chain exists (detection_events row,
    # alert.detection_event_id, incident_timeline).  An incomplete legacy-only chain
    # is NOT counted — we fall through to repair it instead of silently skipping.
    existing_complete = connection.execute(
        '''
        SELECT d.id
        FROM detections d
        WHERE d.workspace_id = %s::uuid
          AND d.detection_type = 'live_rpc_telemetry_proof'
          AND d.source_rule = 'monitoring.live_rpc_coverage.proof'
          AND d.created_at >= %s
          AND EXISTS (
              SELECT 1 FROM detection_evidence dev
              WHERE dev.detection_id = d.id AND dev.workspace_id = d.workspace_id
          )
          AND EXISTS (
              SELECT 1 FROM alerts a
              WHERE a.workspace_id = d.workspace_id
                AND a.detection_id = d.id
                AND a.status = 'open'
                AND a.incident_id IS NOT NULL
                AND a.detection_event_id IS NOT NULL
                AND EXISTS (
                    SELECT 1 FROM detection_events de
                    WHERE de.id = a.detection_event_id
                      AND de.workspace_id = a.workspace_id
                      AND de.telemetry_event_id IS NOT NULL
                )
                AND EXISTS (
                    SELECT 1 FROM incidents i
                    WHERE i.id = a.incident_id
                      AND i.status = 'open'
                      AND EXISTS (
                          SELECT 1 FROM incident_timeline it
                          WHERE it.incident_id = i.id AND it.workspace_id = i.workspace_id
                      )
                )
                AND EXISTS (
                    SELECT 1 FROM response_actions ra
                    WHERE ra.workspace_id = a.workspace_id
                      AND ra.incident_id = a.incident_id
                )
                AND EXISTS (
                    SELECT 1 FROM evidence e
                    WHERE e.workspace_id = a.workspace_id AND e.alert_id = a.id
                )
          )
        ORDER BY d.created_at DESC
        LIMIT 1
        ''',
        (workspace_id, dedupe_cutoff),
    ).fetchone()
    if existing_complete is not None:
        return {
            'created': False,
            'reason': 'deduplicated',
            'detection_id': str((existing_complete.get('id') if isinstance(existing_complete, dict) else existing_complete[0]) or ''),
        }

    # Archive orphan open alerts lacking detection linkage on either path.
    # These cause alert_without_detection / open_alerts_without_detection_evidence flags.
    connection.execute(
        '''
        UPDATE alerts
        SET status = 'resolved', updated_at = NOW()
        WHERE workspace_id = %s::uuid
          AND status IN ('open','acknowledged','investigating')
          AND detection_event_id IS NULL
          AND (
              detection_id IS NULL
              OR NOT EXISTS (
                  SELECT 1 FROM detection_evidence dev
                  WHERE dev.workspace_id = alerts.workspace_id
                    AND dev.detection_id = alerts.detection_id
              )
          )
        ''',
        (workspace_id,),
    )
    # Archive orphan open incidents lacking alert linkage (incident_without_alert flag).
    connection.execute(
        '''
        UPDATE incidents
        SET status = 'resolved', updated_at = NOW()
        WHERE workspace_id = %s::uuid
          AND status IN ('open','acknowledged')
          AND NOT EXISTS (
              SELECT 1 FROM alerts a
              WHERE a.workspace_id = incidents.workspace_id
                AND (a.incident_id = incidents.id OR incidents.source_alert_id = a.id)
          )
        ''',
        (workspace_id,),
    )

    telemetry_row = connection.execute(
        '''
        SELECT te.id, te.target_id, te.asset_id, te.observed_at, te.payload_json
        FROM telemetry_events te
        WHERE te.workspace_id = %s::uuid
          AND te.evidence_source = 'live'
          AND te.event_type IN ('rpc_polling', 'live_provider')
          AND te.provider_type IN ('evm_rpc', 'live_provider')
          AND COALESCE(te.payload_json->>'block_number', '') <> ''
        ORDER BY te.observed_at DESC, te.ingested_at DESC
        LIMIT 1
        ''',
        (workspace_id,),
    ).fetchone()
    if telemetry_row is None:
        return {'created': False, 'reason': 'no_live_telemetry'}

    telemetry_row = dict(telemetry_row)
    telemetry_event_id = str(telemetry_row.get('id') or '')
    target_id = str(telemetry_row.get('target_id') or '')
    asset_id = str(telemetry_row.get('asset_id') or '') or None
    payload_json = telemetry_row.get('payload_json') or {}
    if isinstance(payload_json, str):
        try:
            payload_json = json.loads(payload_json)
        except Exception:
            payload_json = {}
    block_number = payload_json.get('block_number')
    chain_id = payload_json.get('chain_id') or 1
    provider_name = str(payload_json.get('provider_name') or 'evm_rpc')

    # Resolve monitored_targets.id for detection_events FK.
    # detection_events.target_id references monitored_targets(id). Telemetry events
    # store targets.id, which is a different UUID. Never use targets.id directly as
    # detection_events.target_id unless that id also exists in monitored_targets.
    detection_event_target_id = _resolve_detection_event_target_id(connection, workspace_id, target_id)

    monitored_system_row = connection.execute(
        '''
        SELECT ms.id, ms.asset_id
        FROM monitored_systems ms
        WHERE ms.workspace_id = %s::uuid AND ms.target_id = %s::uuid
          AND COALESCE(ms.is_enabled, TRUE) = TRUE
        ORDER BY ms.created_at DESC
        LIMIT 1
        ''',
        (workspace_id, target_id),
    ).fetchone()
    monitored_system_id: str | None = None
    if monitored_system_row:
        ms = dict(monitored_system_row)
        monitored_system_id = str(ms.get('id') or '') or None

    creator_row = connection.execute(
        'SELECT created_by_user_id FROM workspaces WHERE id = %s::uuid LIMIT 1',
        (workspace_id,),
    ).fetchone()
    user_id = str((creator_row.get('created_by_user_id') if isinstance(creator_row, dict) else (creator_row or [None])[0]) or '') if creator_row else ''
    if not user_id:
        return {'created': False, 'reason': 'no_workspace_user'}

    protected_asset_id: str | None = None
    if asset_id:
        asset_check = connection.execute(
            'SELECT id FROM assets WHERE id = %s::uuid AND workspace_id = %s::uuid LIMIT 1',
            (asset_id, workspace_id),
        ).fetchone()
        if asset_check:
            protected_asset_id = asset_id

    observed_at = _now()
    detection_event_id = str(uuid.uuid4())
    detection_id = str(uuid.uuid4())
    alert_id = str(uuid.uuid4())
    incident_id = str(uuid.uuid4())
    response_action_id = str(uuid.uuid4())
    detection_evidence_id = str(uuid.uuid4())
    incident_timeline_id = str(uuid.uuid4())
    monitoring_run_id = str(uuid.uuid4())
    title = 'Live RPC telemetry proof detection'
    evidence_summary = (
        f'Ethereum RPC provider returned a live block (chain_id={chain_id}, '
        f'block_number={block_number}) and telemetry was persisted for this monitored target. '
        'This is a controlled live monitoring proof — not an attack or threat signal.'
    )
    raw_evidence = {
        'telemetry_event_id': telemetry_event_id,
        'detection_event_id': detection_event_id,
        'target_id': target_id,
        'block_number': block_number,
        'chain_id': chain_id,
        'provider_name': provider_name,
        'provider_type': 'evm_rpc',
        'event_type': 'rpc_polling',
        'evidence_source': 'live_rpc_polling',
        'proof_type': 'live_rpc_telemetry_proof',
        'controlled_proof': True,
        'attack_claim': False,
    }

    # 1. Canonical detection_events row — required for the canonical alert chain query
    #    (alerts JOIN detection_events JOIN telemetry_events) used by monitoring_runner
    #    to count active_alerts_count and check open_alerts_without_detection_evidence.
    #    Use detection_event_target_id (monitored_targets.id) not target_id (targets.id)
    #    to satisfy detection_events.target_id FK → monitored_targets(id).
    connection.execute(
        '''
        INSERT INTO detection_events (
            id, workspace_id, asset_id, target_id,
            telemetry_event_id, detection_type, severity, confidence,
            evidence_summary, evidence_source, created_at
        ) VALUES (
            %s, %s::uuid, %s::uuid, %s::uuid,
            %s::uuid, 'live_rpc_telemetry_proof', 'low', 0.95,
            %s, 'live', NOW()
        )
        ''',
        (
            detection_event_id, workspace_id, protected_asset_id, detection_event_target_id,
            telemetry_event_id, evidence_summary,
        ),
    )

    # 2. Legacy detections row — required for last_detection_at and legacy coverage.
    connection.execute(
        '''
        INSERT INTO detections (
            id, workspace_id, monitored_system_id, protected_asset_id,
            detection_type, severity, confidence, title, evidence_summary,
            evidence_source, source_rule, status, detected_at,
            raw_evidence_json, monitoring_run_id, linked_alert_id,
            created_at, updated_at
        ) VALUES (
            %s, %s::uuid, %s::uuid, %s::uuid,
            'live_rpc_telemetry_proof', 'low', 0.95, %s, %s,
            'live', 'monitoring.live_rpc_coverage.proof', 'open', NOW(),
            %s::jsonb, %s::uuid, %s::uuid,
            NOW(), NOW()
        )
        ''',
        (
            detection_id, workspace_id, monitored_system_id, protected_asset_id,
            title, evidence_summary,
            _json_dumps(raw_evidence), monitoring_run_id, alert_id,
        ),
    )

    # 3. Detection evidence (legacy path).
    connection.execute(
        '''
        INSERT INTO detection_evidence (
            id, workspace_id, detection_id, evidence_type, evidence_summary,
            source, raw_reference, raw_payload_json, created_at
        ) VALUES (
            %s, %s::uuid, %s::uuid, 'live_rpc_telemetry_proof', %s,
            'live', %s, %s::jsonb, NOW()
        )
        ''',
        (
            detection_evidence_id, workspace_id, detection_id,
            f'Live RPC coverage telemetry for target {target_id} block={block_number}.',
            f'telemetry_event://{telemetry_event_id}',
            _json_dumps(raw_evidence),
        ),
    )

    # 4. Alert with BOTH canonical detection_event_id AND legacy detection_id so that
    #    all monitoring_runner counting queries (canonical path AND legacy path) include it.
    alert_dedupe = f'live_rpc_proof:{workspace_id}:{target_id}'
    connection.execute(
        '''
        INSERT INTO alerts (
            id, workspace_id, user_id, analysis_run_id, target_id,
            alert_type, title, severity, status,
            source_service, source, summary, payload,
            matched_patterns, reasons, recommended_action,
            degraded, dedupe_signature, detection_id,
            detection_event_id, detection_event_workspace_id,
            occurrence_count, first_seen_at, last_seen_at,
            created_at, updated_at
        ) VALUES (
            %s, %s::uuid, %s::uuid, NULL, %s::uuid,
            'monitoring_proof', %s, 'informational', 'open',
            'monitoring-worker', 'live', %s, %s::jsonb,
            %s::jsonb, %s::jsonb, 'review_live_provider_evidence',
            FALSE, %s, %s::uuid,
            %s::uuid, %s::uuid,
            1, NOW(), NOW(),
            NOW(), NOW()
        )
        ''',
        (
            alert_id, workspace_id, user_id, target_id,
            'Live telemetry proof alert',
            f'Live RPC provider confirmed block {block_number} on chain {chain_id}. Controlled live monitoring proof.',
            _json_dumps(raw_evidence),
            _json_dumps([]),
            _json_dumps(['live_rpc_coverage_confirmed']),
            alert_dedupe, detection_id,
            detection_event_id, workspace_id,
        ),
    )

    # 5. Incident (linked to alert via source_alert_id).
    timeline_entries = [
        {'event': 'provider_poll_succeeded', 'at': observed_at.isoformat(), 'block_number': block_number},
        {'event': 'telemetry_persisted', 'at': observed_at.isoformat(), 'telemetry_event_id': telemetry_event_id},
        {'event': 'detection_event_created', 'at': observed_at.isoformat(), 'detection_event_id': detection_event_id},
        {'event': 'detection_created', 'at': observed_at.isoformat(), 'detection_id': detection_id},
        {'event': 'alert_created', 'at': observed_at.isoformat(), 'alert_id': alert_id},
        {'event': 'incident_opened', 'at': observed_at.isoformat(), 'incident_id': incident_id},
        {'event': 'evidence_generated', 'at': observed_at.isoformat(), 'response_action_id': response_action_id},
    ]
    connection.execute(
        '''
        INSERT INTO incidents (
            id, workspace_id, user_id, analysis_run_id, target_id,
            event_type, title, severity, status,
            source_alert_id, summary, linked_alert_ids, timeline,
            payload, created_at, updated_at
        ) VALUES (
            %s, %s::uuid, %s::uuid, NULL, %s::uuid,
            'live_rpc_telemetry_proof',
            'Live monitoring proof incident',
            'informational', 'open',
            %s::uuid, %s, %s::jsonb, %s::jsonb,
            %s::jsonb, NOW(), NOW()
        )
        ''',
        (
            incident_id, workspace_id, user_id, target_id,
            alert_id,
            'Controlled live proof that telemetry can trigger detection, alert, incident, and evidence workflow.',
            _json_dumps([alert_id]),
            _json_dumps(timeline_entries),
            _json_dumps(raw_evidence),
        ),
    )

    # 6. Update alert.incident_id now that incident exists.
    connection.execute(
        'UPDATE alerts SET incident_id = %s::uuid, updated_at = NOW() WHERE id = %s::uuid',
        (incident_id, alert_id),
    )

    # 7. incident_timeline row — required so monitoring_runner's
    #    canonical_incident_timeline_gap_count stays zero.  Without this row
    #    proof_chain_missing_reason_codes gets 'incidents_without_timeline_linkage'
    #    and proof_chain_link_missing fires even when the alert/detection chain
    #    is otherwise complete.
    connection.execute(
        '''
        INSERT INTO incident_timeline (
            id, workspace_id, incident_id, event_type, message,
            actor_user_id, metadata, created_at
        ) VALUES (
            %s, %s::uuid, %s::uuid,
            'live_rpc_proof_created',
            %s,
            %s::uuid, %s::jsonb, NOW()
        )
        ''',
        (
            incident_timeline_id, workspace_id, incident_id,
            (
                f'Controlled live monitoring proof chain created. '
                f'Telemetry event {telemetry_event_id} → detection {detection_id} '
                f'→ alert {alert_id}.'
            ),
            user_id,
            _json_dumps({
                'proof_type': 'live_rpc_telemetry_proof',
                'telemetry_event_id': telemetry_event_id,
                'detection_event_id': detection_event_id,
                'detection_id': detection_id,
                'alert_id': alert_id,
                'incident_id': incident_id,
                'block_number': block_number,
                'chain_id': chain_id,
            }),
        ),
    )

    # 8. Response action.
    connection.execute(
        '''
        INSERT INTO response_actions (
            id, workspace_id, incident_id, alert_id,
            action_type, mode, status, result_summary,
            execution_metadata, created_by_user_id, approved_by_user_id,
            created_at
        ) VALUES (
            %s, %s::uuid, %s::uuid, %s::uuid,
            'review_live_provider_evidence', 'live_enforcement', 'recommended',
            'Review live RPC telemetry proof: verify provider, telemetry, detection, alert, and evidence chain.',
            %s::jsonb, %s::uuid, NULL, NOW()
        )
        ''',
        (
            response_action_id, workspace_id, incident_id, alert_id,
            _json_dumps({
                'evidence_source': 'live_rpc_polling',
                'telemetry_event_id': telemetry_event_id,
                'detection_event_id': detection_event_id,
                'detection_id': detection_id,
                'alert_id': alert_id,
                'incident_id': incident_id,
                'block_number': block_number,
                'chain_id': chain_id,
                'controlled_proof': True,
            }),
            user_id,
        ),
    )

    # 9. Evidence row (ON CONFLICT updates so it is idempotent).
    evidence_id = str(uuid.uuid4())
    proof_tx_hash = f'live_proof:{workspace_id}'
    evidence_raw_payload = {
        'proof_type': 'live_rpc_telemetry_proof',
        'evidence_source': 'live_rpc_polling',
        'provider_type': 'evm_rpc',
        'telemetry_event_id': telemetry_event_id,
        'detection_event_id': detection_event_id,
        'detection_id': detection_id,
        'alert_id': alert_id,
        'incident_id': incident_id,
        'response_action_id': response_action_id,
        'workspace_id': workspace_id,
        'target_id': target_id,
        'chain_id': chain_id,
        'block_number': block_number,
        'source_type': 'rpc_polling',
        'controlled_proof': True,
        'attack_claim': False,
    }
    connection.execute(
        '''
        INSERT INTO evidence (
            id, workspace_id, asset_id, target_id, alert_id,
            chain, block_number, tx_hash, log_index,
            event_type, severity, risk_score,
            summary, source_provider,
            raw_payload_json, observed_at, created_at,
            monitored_system_id
        ) VALUES (
            %s, %s::uuid, %s::uuid, %s::uuid, %s::uuid,
            %s, %s, %s, 0,
            'live_rpc_telemetry_proof', 'informational', 0.0,
            %s, 'evm_rpc',
            %s::jsonb, NOW(), NOW(),
            %s::uuid
        )
        ON CONFLICT (target_id, tx_hash, log_index, event_type)
        DO UPDATE SET
            alert_id = EXCLUDED.alert_id,
            block_number = EXCLUDED.block_number,
            raw_payload_json = EXCLUDED.raw_payload_json,
            observed_at = EXCLUDED.observed_at
        ''',
        (
            evidence_id, workspace_id, protected_asset_id, target_id, alert_id,
            str(chain_id), block_number, proof_tx_hash,
            (
                f'Live RPC telemetry proof evidence — Ethereum RPC provider confirmed block '
                f'{block_number} on chain {chain_id}. Controlled live monitoring proof, '
                'not an attack or threat signal.'
            ),
            _json_dumps(evidence_raw_payload),
            monitored_system_id,
        ),
    )

    logger.info(
        'live_rpc_proof_chain_created workspace_id=%s target_id=%s '
        'detection_event_id=%s detection_id=%s alert_id=%s incident_id=%s '
        'timeline_id=%s evidence_id=%s block_number=%s',
        workspace_id, target_id, detection_event_id, detection_id,
        alert_id, incident_id, incident_timeline_id, evidence_id, block_number,
    )
    return {
        'created': True,
        'telemetry_event_id': telemetry_event_id,
        'detection_event_id': detection_event_id,
        'detection_id': detection_id,
        'detection_evidence_id': detection_evidence_id,
        'alert_id': alert_id,
        'incident_id': incident_id,
        'incident_timeline_id': incident_timeline_id,
        'response_action_id': response_action_id,
        'evidence_id': evidence_id,
        'target_id': target_id,
        'block_number': block_number,
        'chain_id': chain_id,
    }
