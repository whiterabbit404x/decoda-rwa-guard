from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from fastapi import HTTPException, Request, status

from services.api.app.activity_providers import (
    ActivityEvent,
    SCENARIO_EXPECTED_RISK,
    fetch_target_activity,
    monitoring_demo_scenario,
)
from services.api.app.pilot import (
    _json_dumps,
    _json_safe_value,
    _require_workspace_admin,
    _severity_meets_threshold,
    authenticate_with_connection,
    ensure_pilot_schema,
    live_mode_enabled,
    log_audit,
    persist_analysis_run,
    pg_connection,
    resolve_workspace,
)
from services.api.app.threat_payloads import ThreatKind, normalize_threat_payload

THREAT_ENGINE_URL = (os.getenv('THREAT_ENGINE_URL') or 'http://localhost:8002').rstrip('/')
ALERT_DEDUPE_WINDOW_SECONDS = int(os.getenv('MONITORING_ALERT_DEDUPE_WINDOW_SECONDS', '900'))
WORKER_HEARTBEAT_TTL_SECONDS = int(os.getenv('MONITORING_WORKER_HEARTBEAT_TTL_SECONDS', '180'))

logger = logging.getLogger(__name__)


WORKER_STATE: dict[str, Any] = {
    'worker_name': os.getenv('MONITORING_WORKER_NAME', 'monitoring-worker'),
    'worker_running': False,
    'last_cycle_at': None,
    'last_cycle_due_targets': 0,
    'last_cycle_targets_checked': 0,
    'last_cycle_alerts_generated': 0,
    'last_error': None,
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    raw = str(value).strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace('Z', '+00:00'))
    except ValueError:
        return None


def _safe_error_message(exc: Exception) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    return text[:240]


def _payload_shape(payload: dict[str, Any]) -> dict[str, Any]:
    metadata = payload.get('metadata') if isinstance(payload.get('metadata'), dict) else {}
    return {
        'top_level_keys': sorted(payload.keys()),
        'metadata_keys': sorted(metadata.keys()),
    }


def _threat_call(kind: ThreatKind, payload: dict[str, Any], *, target_id: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    logger.info('monitoring live analysis request target=%s analysis_type=%s payload_shape=%s', target_id, kind, _payload_shape(payload))
    try:
        from services.api.app import main as api_main

        response = api_main.proxy_threat(kind, payload)
        if isinstance(response, dict):
            logger.info(
                'monitoring live analysis succeeded target=%s source=%s score=%s',
                target_id,
                str(response.get('source') or 'live'),
                response.get('score'),
            )
            return response, {'live_invocation': 'proxy_threat', 'live_invocation_succeeded': True}
        logger.warning(
            'monitoring live analysis failed target=%s reason=%s; using fallback',
            target_id,
            'proxy_threat returned no payload',
        )
        return None, {
            'live_invocation': 'proxy_threat',
            'live_invocation_succeeded': False,
            'fallback_reason': 'live_engine_unavailable',
            'fallback_exception_type': 'NoLiveResponse',
            'fallback_exception_message': 'proxy_threat returned no payload',
        }
    except Exception as exc:  # pragma: no cover - defensive logging around runtime import/invocation
        logger.exception('monitoring live analysis failed target=%s reason=%s; using fallback', target_id, exc.__class__.__name__)
        return None, {
            'live_invocation': 'proxy_threat',
            'live_invocation_succeeded': False,
            'fallback_reason': 'live_engine_exception',
            'fallback_exception_type': exc.__class__.__name__,
            'fallback_exception_message': _safe_error_message(exc),
        }


def _fallback_response(kind: ThreatKind, payload: dict[str, Any], *, diagnostics: dict[str, Any] | None = None) -> dict[str, Any]:
    diagnostics = diagnostics or {}
    flags = payload.get('flags') if isinstance(payload.get('flags'), dict) else {}
    if kind == 'transaction':
        score = min(95, int(payload.get('amount') or 0) // 25000 + int(payload.get('burst_actions_last_5m') or 0) * 3 + (20 if flags.get('flash_loan_pattern') else 0))
    elif kind == 'contract':
        risk_flags = sum(len(item.get('risk_flags') or []) for item in payload.get('function_summaries', []) if isinstance(item, dict))
        score = min(98, 20 + risk_flags * 18 + (25 if flags.get('privileged_role_change') else 0))
    else:
        current_volume = float(payload.get('current_volume') or 0)
        baseline = max(1.0, float(payload.get('baseline_volume') or 1))
        multiplier = current_volume / baseline
        score = min(99, int(multiplier * 25) + int((payload.get('order_flow_summary') or {}).get('rapid_swings', 0)) * 8)
    if score >= 80:
        severity, action = 'critical', 'block'
    elif score >= 60:
        severity, action = 'high', 'review'
    elif score >= 35:
        severity, action = 'medium', 'review'
    else:
        severity, action = 'low', 'allow'
    return {
        'analysis_type': kind,
        'score': score,
        'severity': severity,
        'matched_patterns': [{'label': 'deterministic-monitoring-fallback'}],
        'explanation': 'Threat engine unavailable; deterministic fallback analysis applied.',
        'recommended_action': action,
        'reasons': ['fallback mode', f'score={score}'],
        'source': 'fallback',
        'degraded': True,
        'metadata': {
            'ingestion_source': payload.get('metadata', {}).get('ingestion_source', 'demo'),
            'fallback_reason': diagnostics.get('fallback_reason') or 'live_engine_unavailable',
            'fallback_exception_type': diagnostics.get('fallback_exception_type'),
            'fallback_exception_message': diagnostics.get('fallback_exception_message'),
            'live_invocation': diagnostics.get('live_invocation') or 'proxy_threat',
            'live_invocation_succeeded': bool(diagnostics.get('live_invocation_succeeded', False)),
        },
    }


def _normalize_event(target: dict[str, Any], event: ActivityEvent, monitoring_run_id: str, workspace: dict[str, Any]) -> tuple[ThreatKind, dict[str, Any]]:
    kind = event.kind if event.kind in {'contract', 'transaction', 'market'} else 'transaction'
    payload = {
        **event.payload,
        'target_id': str(target['id']),
        'target_name': str(target.get('name') or ''),
        'target_type': str(target.get('target_type') or ''),
        'chain_network': str(target.get('chain_network') or ''),
        'severity_preference': str(target.get('severity_preference') or 'medium'),
        'metadata': {
            'workspace_id': str(target['workspace_id']),
            'workspace_name': workspace.get('name'),
            'target_id': str(target['id']),
            'target_name': str(target.get('name') or ''),
            'target_type': str(target.get('target_type') or ''),
            'chain_network': str(target.get('chain_network') or ''),
            'monitoring_run_id': monitoring_run_id,
            'ingestion_source': event.ingestion_source,
            'observed_at': event.observed_at.isoformat(),
            'severity_threshold': str(target.get('severity_threshold') or 'medium'),
            'policy_snapshot': {
                'auto_create_alerts': bool(target.get('auto_create_alerts')),
                'auto_create_incidents': bool(target.get('auto_create_incidents')),
            },
            'provider_cursor': event.cursor,
            'event_id': event.event_id,
            'monitoring_demo_scenario': monitoring_demo_scenario(target),
            'expected_risk_class': SCENARIO_EXPECTED_RISK.get(monitoring_demo_scenario(target) or '', 'default'),
        },
    }
    normalized, _ = normalize_threat_payload(kind, payload, include_original=False)
    logger.info(
        'monitoring payload built target=%s event=%s analysis_type=%s payload_shape=%s',
        target.get('id'),
        event.event_id,
        kind,
        _payload_shape(normalized),
    )
    return kind, normalized


def _signature(target_id: str, payload: dict[str, Any], response: dict[str, Any]) -> str:
    marker = {
        'target_id': target_id,
        'severity': response.get('severity'),
        'action': response.get('recommended_action'),
        'patterns': [str(item.get('label') or item) for item in (response.get('matched_patterns') or [])],
        'reason': response.get('explanation'),
        'event_id': payload.get('metadata', {}).get('event_id'),
    }
    return uuid.uuid5(uuid.NAMESPACE_DNS, json.dumps(marker, sort_keys=True)).hex


def _upsert_alert(
    connection: Any,
    *,
    workspace_id: str,
    user_id: str,
    target_id: str,
    analysis_run_id: str,
    title: str,
    response: dict[str, Any],
    signature: str,
) -> str:
    cutoff = utc_now() - timedelta(seconds=ALERT_DEDUPE_WINDOW_SECONDS)
    existing = connection.execute(
        '''
        SELECT id, occurrence_count
        FROM alerts
        WHERE workspace_id = %s AND target_id = %s AND dedupe_signature = %s AND created_at >= %s
        ORDER BY created_at DESC
        LIMIT 1
        ''',
        (workspace_id, target_id, signature, cutoff),
    ).fetchone()
    if existing is not None:
        connection.execute(
            '''
            UPDATE alerts
            SET occurrence_count = COALESCE(occurrence_count, 1) + 1,
                last_seen_at = NOW(),
                updated_at = NOW(),
                summary = %s,
                reasons = %s::jsonb,
                matched_patterns = %s::jsonb,
                recommended_action = %s,
                degraded = %s
            WHERE id = %s
            ''',
            (
                str(response.get('explanation') or title),
                _json_dumps(response.get('reasons') or []),
                _json_dumps(response.get('matched_patterns') or []),
                str(response.get('recommended_action') or 'review'),
                bool(response.get('degraded', False)),
                existing['id'],
            ),
        )
        return str(existing['id'])

    alert_id = str(uuid.uuid4())
    connection.execute(
        '''
        INSERT INTO alerts (
            id, workspace_id, user_id, analysis_run_id, target_id, alert_type, title, severity, status,
            source_service, source, summary, payload, matched_patterns, reasons, recommended_action,
            degraded, dedupe_signature, occurrence_count, first_seen_at, last_seen_at, created_at, updated_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'open', %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, %s, 1, NOW(), NOW(), NOW(), NOW())
        ''',
        (
            alert_id,
            workspace_id,
            user_id,
            analysis_run_id,
            target_id,
            'threat_monitoring',
            title,
            str(response.get('severity') or 'medium'),
            'threat-engine',
            str(response.get('source') or 'live'),
            str(response.get('explanation') or title),
            _json_dumps(response),
            _json_dumps(response.get('matched_patterns') or []),
            _json_dumps(response.get('reasons') or []),
            str(response.get('recommended_action') or 'review'),
            bool(response.get('degraded', False)),
            signature,
        ),
    )
    return alert_id


def _maybe_create_incident(connection: Any, *, workspace_id: str, user_id: str, target_id: str, analysis_run_id: str, alert_id: str, response: dict[str, Any], auto_create: bool) -> str | None:
    severity = str(response.get('severity') or 'low').lower()
    if not (severity == 'critical' or auto_create):
        return None
    incident_id = str(uuid.uuid4())
    title = f"{severity.upper()} monitoring incident"
    connection.execute(
        '''
        INSERT INTO incidents (id, workspace_id, user_id, analysis_run_id, target_id, event_type, title, severity, status, summary, linked_alert_ids, timeline, payload, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'open', %s, %s::jsonb, %s::jsonb, %s::jsonb, NOW(), NOW())
        ''',
        (
            incident_id,
            workspace_id,
            user_id,
            analysis_run_id,
            target_id,
            'threat_monitoring_incident',
            title,
            severity,
            str(response.get('explanation') or title),
            _json_dumps([alert_id]),
            _json_dumps([{'event': 'incident.created', 'at': utc_now().isoformat(), 'alert_id': alert_id}]),
            _json_dumps(response),
        ),
    )
    return incident_id


def process_monitoring_target(connection: Any, target: dict[str, Any], *, triggered_by_user_id: str | None = None) -> dict[str, Any]:
    workspace_row = connection.execute('SELECT id, name FROM workspaces WHERE id = %s', (target['workspace_id'],)).fetchone() or {'id': target['workspace_id'], 'name': 'Workspace'}
    workspace = _json_safe_value(dict(workspace_row))
    user_id = triggered_by_user_id or str(target.get('updated_by_user_id') or target.get('created_by_user_id'))
    monitoring_run_id = str(uuid.uuid4())
    checkpoint = _parse_ts(target.get('monitoring_checkpoint_at') or target.get('last_checked_at'))
    events = fetch_target_activity(target, checkpoint)

    alerts_generated = 0
    run_ids: list[str] = []
    last_status = 'no_events'
    last_run_id: str | None = None
    last_alert_at: datetime | None = None
    checkpoint_cursor = target.get('monitoring_checkpoint_cursor')
    checkpoint_at = checkpoint
    configured_scenario = monitoring_demo_scenario(target)
    logger.info(
        'monitoring target fetched target=%s scenario=%s threshold=%s auto_create_alerts=%s',
        target.get('id'),
        configured_scenario or 'default',
        str(target.get('severity_threshold') or 'medium'),
        bool(target.get('auto_create_alerts', True)),
    )

    for event in events:
        logger.info(
            'monitoring event generated target_id=%s scenario=%s event_id=%s expected_risk_class=%s',
            target.get('id'),
            configured_scenario or 'default',
            event.event_id,
            SCENARIO_EXPECTED_RISK.get(configured_scenario or '', 'default'),
        )
        kind, normalized = _normalize_event(target, event, monitoring_run_id, workspace)
        response, diagnostics = _threat_call(kind, normalized, target_id=str(target['id']))
        if response is None:
            response = _fallback_response(kind, normalized, diagnostics=diagnostics)
        response_metadata = response.get('metadata') if isinstance(response.get('metadata'), dict) else {}
        response_metadata.update(
            {
                'monitoring_analysis_type': f'monitoring_{kind}',
                'monitoring_request_keys': sorted(normalized.keys()),
                'monitoring_request_metadata_keys': sorted((normalized.get('metadata') or {}).keys()) if isinstance(normalized.get('metadata'), dict) else [],
                'monitoring_demo_scenario': configured_scenario,
            }
        )
        response['metadata'] = response_metadata
        logger.info(
            'monitoring live result target=%s score=%s severity=%s source=%s',
            target.get('id'),
            response.get('score'),
            response.get('severity'),
            str(response.get('source') or 'live'),
        )
        last_status = 'completed'
        analysis_run_id = persist_analysis_run(
            connection,
            workspace_id=str(target['workspace_id']),
            user_id=user_id,
            analysis_type=f'monitoring_{kind}',
            service_name='threat-engine',
            title=f'Automatic {kind} monitoring run',
            status_value='completed',
            request_payload=normalized,
            response_payload=response,
            request=None,
        )
        run_ids.append(analysis_run_id)
        last_run_id = analysis_run_id
        checkpoint_at = event.observed_at
        checkpoint_cursor = event.cursor

        severity_threshold = str(target.get('severity_threshold') or 'medium')
        if bool(target.get('auto_create_alerts', True)) and _severity_meets_threshold(str(response.get('severity') or 'low'), severity_threshold):
            signature = _signature(str(target['id']), normalized, response)
            alert_id = _upsert_alert(
                connection,
                workspace_id=str(target['workspace_id']),
                user_id=user_id,
                target_id=str(target['id']),
                analysis_run_id=analysis_run_id,
                title=f"{target.get('name')}: {response.get('severity', 'medium')} risk",
                response=response,
                signature=signature,
            )
            alerts_generated += 1
            last_alert_at = utc_now()
            logger.info(
                'monitoring alert created alert_id=%s target_id=%s severity=%s scenario=%s',
                alert_id,
                target['id'],
                str(response.get('severity') or 'unknown'),
                configured_scenario or 'default',
            )
            _maybe_create_incident(
                connection,
                workspace_id=str(target['workspace_id']),
                user_id=user_id,
                target_id=str(target['id']),
                analysis_run_id=analysis_run_id,
                alert_id=alert_id,
                response=response,
                auto_create=bool(target.get('auto_create_incidents')) and _severity_meets_threshold(str(response.get('severity') or 'low'), severity_threshold),
            )

    connection.execute(
        '''
        UPDATE targets
        SET last_checked_at = NOW(),
            last_run_status = %s,
            last_run_id = %s,
            last_alert_at = COALESCE(%s, last_alert_at),
            monitoring_checkpoint_at = COALESCE(%s, monitoring_checkpoint_at),
            monitoring_checkpoint_cursor = COALESCE(%s, monitoring_checkpoint_cursor),
            monitoring_claimed_by = NULL,
            monitoring_claimed_at = NULL,
            updated_at = NOW()
        WHERE id = %s
        ''',
        (last_status, last_run_id, last_alert_at, checkpoint_at, checkpoint_cursor, target['id']),
    )
    logger.info('checked target %s %s status=%s runs=%s alerts=%s', target['id'], target.get('name') or 'unknown', last_status, len(run_ids), alerts_generated)
    return {'target_id': str(target['id']), 'monitoring_run_id': monitoring_run_id, 'runs': run_ids, 'alerts_generated': alerts_generated, 'status': last_status}


def run_monitoring_cycle(*, worker_name: str = 'monitoring-worker', limit: int = 50) -> dict[str, Any]:
    if not live_mode_enabled():
        return {'checked': 0, 'alerts_generated': 0, 'runs': [], 'live_mode': False}

    checked = 0
    due_count = 0
    alerts_generated = 0
    runs: list[dict[str, Any]] = []
    error_message: str | None = None
    cycle_started_at = utc_now()
    logger.info('monitoring cycle started worker=%s limit=%s', worker_name, limit)
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        connection.execute(
            '''
            INSERT INTO monitoring_worker_state (
                worker_name,
                running,
                last_cycle_at,
                last_cycle_due_targets,
                last_cycle_targets_checked,
                last_cycle_alerts_generated,
                last_error,
                updated_at
            )
            VALUES (%s, TRUE, NOW(), 0, 0, 0, NULL, NOW())
            ON CONFLICT (worker_name)
            DO UPDATE SET running = TRUE, last_cycle_at = NOW(), last_error = NULL, updated_at = NOW()
            ''',
            (worker_name,),
        )
        candidate_targets = connection.execute(
            '''
            SELECT targets.*,
                   workspace.id AS workspace_exists_id,
                   monitored_workspace.id AS monitored_workspace_exists_id
            FROM targets
            LEFT JOIN workspaces AS workspace ON workspace.id = targets.workspace_id
            LEFT JOIN workspaces AS monitored_workspace ON monitored_workspace.id = targets.monitored_by_workspace_id
            WHERE targets.deleted_at IS NULL
            ORDER BY COALESCE(targets.last_checked_at, '1970-01-01'::timestamptz) ASC, targets.created_at ASC
            ''',
        ).fetchall()
        now = utc_now()
        max_targets = max(1, min(limit, 200))
        skipped_disabled = 0
        skipped_inactive = 0
        skipped_missing_workspace = 0
        skipped_not_due = 0
        skipped_null_handling = 0
        due_target_ids: list[Any] = []
        for row in candidate_targets:
            target = dict(row)
            if not bool(target.get('monitoring_enabled')) or not bool(target.get('enabled')):
                skipped_disabled += 1
                continue
            if not bool(target.get('is_active')):
                skipped_inactive += 1
                continue
            if target.get('workspace_exists_id') is None or (
                target.get('monitored_by_workspace_id') is not None and target.get('monitored_workspace_exists_id') is None
            ):
                skipped_missing_workspace += 1
                continue
            last_checked_at = _parse_ts(target.get('last_checked_at'))
            if last_checked_at is None:
                due_target_ids.append(target['id'])
            else:
                interval_raw = target.get('monitoring_interval_seconds')
                if interval_raw is None:
                    skipped_null_handling += 1
                    interval_seconds = 30
                else:
                    try:
                        interval_seconds = max(30, int(interval_raw))
                    except (TypeError, ValueError):
                        skipped_null_handling += 1
                        interval_seconds = 30
                if last_checked_at <= now - timedelta(seconds=interval_seconds):
                    due_target_ids.append(target['id'])
                else:
                    skipped_not_due += 1
            if len(due_target_ids) >= max_targets:
                break
        logger.info(
            'monitoring due selection total_candidate_targets=%s skipped_disabled=%s skipped_inactive=%s '
            'skipped_missing_workspace=%s skipped_not_due=%s skipped_null_handling=%s due_target_ids=%s',
            len(candidate_targets),
            skipped_disabled,
            skipped_inactive,
            skipped_missing_workspace,
            skipped_not_due,
            skipped_null_handling,
            [str(target_id) for target_id in due_target_ids],
        )
        due_targets = []
        if due_target_ids:
            due_targets = connection.execute(
                '''
                SELECT *
                FROM targets
                WHERE id = ANY(%s)
                ORDER BY COALESCE(last_checked_at, '1970-01-01'::timestamptz) ASC, created_at ASC
                FOR UPDATE SKIP LOCKED
                ''',
                (due_target_ids,),
            ).fetchall()
            due_targets = [dict(row) for row in due_targets]
        else:
            due_targets = []
        due_count = len(due_targets)
        logger.info('due targets: %s', due_count)
        for row in due_targets:
            target = dict(row)
            try:
                with connection.transaction():
                    connection.execute('UPDATE targets SET monitoring_claimed_by = %s, monitoring_claimed_at = NOW() WHERE id = %s', (worker_name, target['id']))
                    result = process_monitoring_target(connection, target)
                alerts_generated += int(result['alerts_generated'])
                runs.append(result)
                checked += 1
            except Exception as exc:
                error_message = str(exc)
                logger.exception('monitoring target failed target=%s name=%s', target.get('id'), target.get('name'))
                connection.execute(
                    'UPDATE targets SET last_checked_at = NOW(), last_run_status = %s, monitoring_claimed_by = NULL, monitoring_claimed_at = NULL WHERE id = %s',
                    ('error', target['id']),
                )
        connection.execute(
            '''
            UPDATE monitoring_worker_state
            SET running = FALSE,
                last_cycle_at = NOW(),
                last_cycle_due_targets = %s,
                last_cycle_targets_checked = %s,
                last_cycle_alerts_generated = %s,
                last_error = %s,
                updated_at = NOW()
            WHERE worker_name = %s
            ''',
            (due_count, checked, alerts_generated, error_message, worker_name),
        )
        connection.commit()
    WORKER_STATE.update(
        {
            'worker_name': worker_name,
            'worker_running': False,
            'last_cycle_at': cycle_started_at.isoformat(),
            'last_cycle_due_targets': due_count,
            'last_cycle_targets_checked': checked,
            'last_cycle_alerts_generated': alerts_generated,
            'last_error': error_message,
        }
    )
    logger.info('monitoring cycle finished worker=%s due=%s checked=%s alerts=%s', worker_name, due_count, checked, alerts_generated)
    return {'due_targets': due_count, 'checked': checked, 'alerts_generated': alerts_generated, 'runs': runs, 'live_mode': True}


def list_monitoring_targets(request: Request) -> dict[str, Any]:
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        rows = connection.execute(
            '''
            SELECT id, workspace_id, name, target_type, chain_network, enabled, monitoring_enabled, monitoring_mode,
                   monitoring_interval_seconds, severity_threshold, auto_create_alerts, auto_create_incidents,
                   notification_channels, monitoring_demo_scenario, last_checked_at, last_run_status, last_run_id, last_alert_at, is_active
            FROM targets
            WHERE workspace_id = %s AND deleted_at IS NULL
            ORDER BY created_at DESC
            ''',
            (workspace_context['workspace_id'],),
        ).fetchall()
        targets: list[dict[str, Any]] = []
        for row in rows:
            item = _json_safe_value(dict(row))
            item['monitoring_profile'] = item.get('monitoring_demo_scenario')
            targets.append(item)
        return {'targets': targets, 'workspace': workspace_context['workspace']}


def patch_monitoring_target(target_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        row = connection.execute(
            '''
            SELECT id, monitoring_enabled, monitoring_mode, monitoring_interval_seconds, severity_threshold,
                   auto_create_alerts, auto_create_incidents, notification_channels, monitoring_demo_scenario, is_active
            FROM targets
            WHERE id = %s AND workspace_id = %s AND deleted_at IS NULL
            ''',
            (target_id, workspace_context['workspace_id']),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Target not found.')
        current = dict(row)
        mode = str(payload.get('monitoring_mode') if 'monitoring_mode' in payload else current.get('monitoring_mode') or 'poll').strip().lower()
        if mode not in {'poll', 'stream'}:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='monitoring_mode must be poll or stream.')
        threshold = str(payload.get('severity_threshold') if 'severity_threshold' in payload else current.get('severity_threshold') or 'medium').strip().lower()
        if threshold not in {'low', 'medium', 'high', 'critical'}:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='severity_threshold must be low/medium/high/critical.')
        channels = payload.get('notification_channels') if 'notification_channels' in payload else current.get('notification_channels')
        channels = channels if isinstance(channels, list) else []
        scenario_field_provided = 'monitoring_demo_scenario' in payload or 'monitoring_profile' in payload
        if 'monitoring_demo_scenario' in payload:
            raw_demo_scenario = payload.get('monitoring_demo_scenario')
        elif 'monitoring_profile' in payload:
            raw_demo_scenario = payload.get('monitoring_profile')
        else:
            raw_demo_scenario = current.get('monitoring_demo_scenario')
        demo_scenario = str(raw_demo_scenario or '').strip().lower() or None
        if demo_scenario is not None and demo_scenario not in SCENARIO_EXPECTED_RISK:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='monitoring_demo_scenario must be safe/low_risk/medium_risk/high_risk/flash_loan_like/admin_abuse_like/risky_approval_like.')
        monitoring_enabled = bool(payload.get('monitoring_enabled')) if 'monitoring_enabled' in payload else bool(current.get('monitoring_enabled'))
        interval_seconds_raw = payload.get('monitoring_interval_seconds') if 'monitoring_interval_seconds' in payload else current.get('monitoring_interval_seconds')
        interval_seconds = max(30, int(interval_seconds_raw or 300))
        auto_create_alerts = bool(payload.get('auto_create_alerts')) if 'auto_create_alerts' in payload else bool(current.get('auto_create_alerts', True))
        auto_create_incidents = bool(payload.get('auto_create_incidents')) if 'auto_create_incidents' in payload else bool(current.get('auto_create_incidents', False))
        is_active = bool(payload.get('is_active')) if 'is_active' in payload else bool(current.get('is_active', True))
        connection.execute(
            '''
            UPDATE targets
            SET monitoring_enabled = %s,
                monitoring_mode = %s,
                monitoring_interval_seconds = %s,
                severity_threshold = %s,
                auto_create_alerts = %s,
                auto_create_incidents = %s,
                notification_channels = %s::jsonb,
                monitoring_demo_scenario = %s,
                monitored_by_workspace_id = %s,
                is_active = %s,
                updated_by_user_id = %s,
                updated_at = NOW()
            WHERE id = %s
            ''',
            (
                monitoring_enabled,
                mode,
                interval_seconds,
                threshold,
                auto_create_alerts,
                auto_create_incidents,
                _json_dumps(channels),
                demo_scenario,
                workspace_context['workspace_id'],
                is_active,
                user['id'],
                target_id,
            ),
        )
        logger.info(
            'monitoring config persisted target=%s scenario=%s scenario_field_provided=%s monitoring_enabled=%s threshold=%s',
            target_id,
            demo_scenario or 'default',
            scenario_field_provided,
            monitoring_enabled,
            threshold,
        )
        log_audit(
            connection,
            action='target.monitoring.update',
            entity_type='target',
            entity_id=target_id,
            request=request,
            user_id=user['id'],
            workspace_id=workspace_context['workspace_id'],
            metadata={'monitoring_enabled': monitoring_enabled, 'monitoring_demo_scenario': demo_scenario},
        )
        connection.commit()
        updated = connection.execute('SELECT * FROM targets WHERE id = %s', (target_id,)).fetchone()
        updated_target = _json_safe_value(dict(updated))
        updated_target['monitoring_profile'] = updated_target.get('monitoring_demo_scenario')
        return {'target': updated_target}


def run_monitoring_once(target_id: str, request: Request) -> dict[str, Any]:
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        row = connection.execute('SELECT * FROM targets WHERE id = %s AND workspace_id = %s AND deleted_at IS NULL', (target_id, workspace_context['workspace_id'])).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Target not found.')
        result = process_monitoring_target(connection, dict(row), triggered_by_user_id=str(user['id']))
        connection.commit()
        return result


def list_incidents(request: Request, *, status_value: str | None = None, severity: str | None = None, target_id: str | None = None) -> dict[str, Any]:
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        clauses = ['workspace_id = %s']
        params: list[Any] = [workspace_context['workspace_id']]
        if status_value:
            clauses.append('status = %s')
            params.append(status_value)
        if severity:
            clauses.append('severity = %s')
            params.append(severity)
        if target_id:
            clauses.append('target_id = %s')
            params.append(target_id)
        query = f"""
            SELECT id, workspace_id, target_id, analysis_run_id, event_type, title, severity, status, summary,
                   linked_alert_ids, owner_user_id, timeline, created_at, updated_at
            FROM incidents
            WHERE {' AND '.join(clauses)}
            ORDER BY created_at DESC
            LIMIT 200
        """
        rows = connection.execute(query, tuple(params)).fetchall()
        return {'incidents': [_json_safe_value(dict(row)) for row in rows]}


def patch_incident(incident_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        row = connection.execute('SELECT id FROM incidents WHERE id = %s AND workspace_id = %s', (incident_id, workspace_context['workspace_id'])).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Incident not found.')
        next_status = str(payload.get('status') or 'open').strip().lower()
        if next_status not in {'open', 'acknowledged', 'resolved'}:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='status must be open/acknowledged/resolved.')
        owner_user_id = payload.get('owner_user_id')
        timeline = payload.get('timeline') if isinstance(payload.get('timeline'), list) else None
        connection.execute(
            '''
            UPDATE incidents
            SET status = %s,
                owner_user_id = %s,
                timeline = COALESCE(%s::jsonb, timeline),
                updated_at = NOW()
            WHERE id = %s
            ''',
            (next_status, owner_user_id, _json_dumps(timeline) if timeline is not None else None, incident_id),
        )
        log_audit(connection, action='incident.update', entity_type='incident', entity_id=incident_id, request=request, user_id=user['id'], workspace_id=workspace_context['workspace_id'], metadata={'status': next_status})
        connection.commit()
        return {'id': incident_id, 'status': next_status}


def get_monitoring_health() -> dict[str, Any]:
    if not live_mode_enabled():
        return {**WORKER_STATE, 'live_mode': False}
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        worker_name = WORKER_STATE['worker_name']
        row = connection.execute(
            '''
            SELECT worker_name, running, last_cycle_at, last_cycle_due_targets,
                   last_cycle_targets_checked, last_cycle_alerts_generated, last_error, updated_at
            FROM monitoring_worker_state
            WHERE worker_name = %s
            ''',
            (worker_name,),
        ).fetchone()
        if row is None:
            return {**WORKER_STATE, 'live_mode': True}
        normalized = _json_safe_value(dict(row))
        last_cycle_at = _parse_ts(normalized.get('last_cycle_at'))
        worker_running = bool(normalized.get('running'))
        if last_cycle_at is not None:
            worker_running = worker_running or (utc_now() - last_cycle_at) <= timedelta(seconds=max(30, WORKER_HEARTBEAT_TTL_SECONDS))
        normalized['worker_running'] = worker_running
        normalized['last_cycle_checked_targets'] = normalized.get('last_cycle_targets_checked', 0)
        normalized['last_cycle_alerts_created'] = normalized.get('last_cycle_alerts_generated', 0)
        return {**normalized, 'live_mode': True}
