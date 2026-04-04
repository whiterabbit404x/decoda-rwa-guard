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
    ActivityProviderResult,
    fetch_target_activity_result,
    monitoring_ingestion_runtime,
)
from services.api.app.evm_activity_provider import JsonRpcClient
from services.api.app.monitoring_truth import ui_evidence_state, ui_truthfulness_state
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
    'ingestion_mode': None,
    'degraded': False,
    'metrics': {
        'live_events_ingested': 0,
        'analysis_failures': 0,
        'degraded_runs': 0,
    },
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


def monitoring_operational_mode(runtime: dict[str, Any], *, degraded: bool, degraded_reason: str | None) -> str:
    if degraded or degraded_reason:
        return 'DEGRADED'
    mode = str(runtime.get('mode') or 'demo').strip().lower()
    if mode == 'live':
        return 'LIVE'
    if mode == 'hybrid':
        return 'HYBRID'
    return 'DEMO'


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


def _load_target_asset_context(connection: Any, *, workspace_id: str, target: dict[str, Any]) -> dict[str, Any] | None:
    asset_id = target.get('asset_id')
    if not asset_id:
        return None
    row = connection.execute(
        '''
        SELECT id, name, asset_class, asset_symbol, identifier, token_contract_address,
               treasury_ops_wallets, custody_wallets, oracle_sources, venue_labels, expected_flow_patterns,
               expected_counterparties, expected_approval_patterns, expected_liquidity_baseline,
               expected_oracle_freshness_seconds, expected_oracle_update_cadence_seconds,
               baseline_status, baseline_source, baseline_updated_at, baseline_confidence, baseline_coverage
        FROM assets
        WHERE id = %s AND workspace_id = %s AND deleted_at IS NULL
        ''',
        (asset_id, workspace_id),
    ).fetchone()
    if row is None:
        return None
    context = _json_safe_value(dict(row))
    for key in ('treasury_ops_wallets', 'custody_wallets', 'oracle_sources', 'venue_labels', 'expected_flow_patterns', 'expected_counterparties'):
        if not isinstance(context.get(key), list):
            context[key] = []
    for key in ('expected_approval_patterns', 'expected_liquidity_baseline'):
        if not isinstance(context.get(key), dict):
            context[key] = {}
    return context


ASSET_DETECTOR_FAMILIES = {
    'counterparty',
    'flow_pattern',
    'approval_pattern',
    'liquidity_venue',
    'oracle_integrity',
}


def _normalize_addr(value: Any) -> str:
    return str(value or '').strip().lower()


def _asset_detection_summary(*, asset: dict[str, Any] | None, event: ActivityEvent) -> dict[str, Any]:
    results = _enforce_asset_detectors(asset=asset, event=event)
    anomalous = [item for item in results if item['detector_status'] == 'anomaly_detected']
    insufficient = [item for item in results if item['detector_status'] == 'insufficient_real_evidence']
    highest = anomalous[0] if anomalous else (insufficient[0] if insufficient else results[0])
    summary_reason = highest.get('anomaly_reason') or 'detectors_completed_without_confirmed_anomaly'
    return {
        'detection_family': highest.get('detector_family'),
        'detector_results': results,
        'detector_status': highest.get('detector_status'),
        'anomaly_basis': summary_reason,
        'confidence_basis': highest.get('confidence'),
        'recommended_action': highest.get('recommended_action'),
        'severity': highest.get('severity', 'low'),
    }


def _enforce_asset_detectors(asset: dict[str, Any] | None, event: ActivityEvent) -> list[dict[str, Any]]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    if not asset:
        return [{
            'asset_id': None,
            'asset_identifier': None,
            'symbol': None,
            'target_id': payload.get('target_id'),
            'detector_family': 'counterparty',
            'detector_status': 'insufficient_real_evidence',
            'anomaly_reason': 'missing_asset_profile',
            'severity': 'medium',
            'confidence': 'low',
            'recommended_action': 'attach_asset_profile',
            'evidence_origin': 'fallback',
            'provider_name': 'asset_detector',
            'observed_at': event.observed_at.isoformat(),
            'chain_id': payload.get('chain_id'),
            'tx_hash': payload.get('tx_hash'),
            'block_number': payload.get('block_number'),
            'log_index': payload.get('log_index'),
            'source_address': payload.get('from') or payload.get('owner'),
            'destination_address': payload.get('to'),
            'spender': payload.get('spender'),
            'contract_address': payload.get('contract_address'),
            'raw_event_type': payload.get('event_type') or event.kind,
            'normalized_event_snapshot': payload,
            'baseline_comparison': {'status': 'missing_asset_profile'},
            'oracle_observation_details': {},
        }]
    expected_counterparties = {_normalize_addr(v) for v in asset.get('expected_counterparties', []) if _normalize_addr(v)}
    treasury_wallets = {_normalize_addr(v) for v in asset.get('treasury_ops_wallets', []) if _normalize_addr(v)}
    custody_wallets = {_normalize_addr(v) for v in asset.get('custody_wallets', []) if _normalize_addr(v)}
    source = _normalize_addr(payload.get('from') or payload.get('owner'))
    destination = _normalize_addr(payload.get('to'))
    spender = _normalize_addr(payload.get('spender'))
    amount = float(payload.get('amount') or 0)
    severity = 'low'
    counterparty_class = 'approved_external_counterparty'
    if destination and destination not in expected_counterparties and destination not in treasury_wallets and destination not in custody_wallets:
        counterparty_class = 'unknown_external_counterparty'
        severity = 'high' if amount > 100000 or source in treasury_wallets or source in custody_wallets else 'medium'
    elif destination in treasury_wallets:
        counterparty_class = 'treasury_ops_wallet'
    elif destination in custody_wallets:
        counterparty_class = 'custody_wallet'
    counterparty = {
        'detector_family': 'counterparty',
        'detector_status': 'anomaly_detected' if counterparty_class == 'unknown_external_counterparty' else 'real_event_no_anomaly',
        'anomaly_reason': 'unknown_counterparty_on_protected_path' if counterparty_class == 'unknown_external_counterparty' else 'counterparty_in_expected_profile',
        'severity': severity,
        'confidence': 'high' if counterparty_class == 'unknown_external_counterparty' else 'medium',
        'recommended_action': 'pause_outbound_transfer_and_review' if counterparty_class == 'unknown_external_counterparty' else 'continue_monitoring',
        'baseline_comparison': {'expected_counterparties': sorted(expected_counterparties), 'counterparty_classification': counterparty_class},
    }
    allowed_routes = {(str(item.get('source_class')).lower(), str(item.get('destination_class')).lower()) for item in asset.get('expected_flow_patterns', []) if isinstance(item, dict)}
    source_class = 'treasury_ops' if source in treasury_wallets else ('custody' if source in custody_wallets else 'external')
    destination_class = 'treasury_ops' if destination in treasury_wallets else ('custody' if destination in custody_wallets else ('approved_external' if destination in expected_counterparties else 'unknown_external'))
    route_tuple = (source_class, destination_class)
    route_valid = (not allowed_routes) or route_tuple in allowed_routes
    flow = {
        'detector_family': 'flow_pattern',
        'detector_status': 'real_event_no_anomaly' if route_valid else 'anomaly_detected',
        'anomaly_reason': 'route_matches_expected_flow_pattern' if route_valid else 'invalid_protected_asset_routing',
        'severity': 'high' if not route_valid and source_class in {'treasury_ops', 'custody'} else 'medium',
        'confidence': 'high',
        'recommended_action': 'block_route_and_escalate' if not route_valid else 'continue_monitoring',
        'baseline_comparison': {'allowed_routes': [list(item) for item in sorted(allowed_routes)], 'observed_route': [source_class, destination_class]},
    }
    approval_cfg = asset.get('expected_approval_patterns') if isinstance(asset.get('expected_approval_patterns'), dict) else {}
    allowed_spenders = {_normalize_addr(v) for v in approval_cfg.get('allowed_spenders', []) if _normalize_addr(v)}
    max_approval = float(approval_cfg.get('max_amount') or 0)
    approval_amount = float(payload.get('approval_amount') or payload.get('amount') or 0)
    unlimited = bool(payload.get('is_unlimited_approval')) or approval_amount >= 2**255
    unexpected_spender = bool(spender) and allowed_spenders and spender not in allowed_spenders
    approval_violation = str(payload.get('kind_hint') or '').lower() == 'erc20_approval' and (unexpected_spender or unlimited or (max_approval > 0 and approval_amount > max_approval))
    approval = {
        'detector_family': 'approval_pattern',
        'detector_status': 'anomaly_detected' if approval_violation else 'real_event_no_anomaly',
        'anomaly_reason': 'unexpected_unlimited_approval_on_protected_asset' if approval_violation and unlimited else ('approval_pattern_violation' if approval_violation else 'approval_within_expected_pattern'),
        'severity': 'high' if approval_violation and unlimited else ('medium' if approval_violation else 'low'),
        'confidence': 'high' if approval_violation else 'medium',
        'recommended_action': 'revoke_approval_and_rotate_keys' if approval_violation else 'continue_monitoring',
        'baseline_comparison': {'allowed_spenders': sorted(allowed_spenders), 'max_approval': max_approval, 'approval_amount': approval_amount, 'unlimited': unlimited},
    }
    liquidity_cfg = asset.get('expected_liquidity_baseline') if isinstance(asset.get('expected_liquidity_baseline'), dict) else {}
    baseline_volume = float(liquidity_cfg.get('baseline_outflow_volume') or 0)
    observed_volume = float(payload.get('current_volume') or payload.get('amount') or 0)
    venues = {_normalize_addr(v) for v in asset.get('venue_labels', []) if _normalize_addr(v)}
    observed_venue = _normalize_addr(payload.get('venue'))
    if baseline_volume <= 0 or observed_volume <= 0:
        liquidity = {
            'detector_family': 'liquidity_venue',
            'detector_status': 'insufficient_real_evidence',
            'anomaly_reason': 'missing_real_liquidity_baseline_or_observation',
            'severity': 'medium',
            'confidence': 'low',
            'recommended_action': 'collect_more_real_liquidity_evidence',
            'baseline_comparison': {'baseline_outflow_volume': baseline_volume, 'observed_volume': observed_volume},
        }
    else:
        liquidity_anomaly = observed_volume > baseline_volume * 2 or (venues and observed_venue and observed_venue not in venues)
        liquidity = {
            'detector_family': 'liquidity_venue',
            'detector_status': 'anomaly_detected' if liquidity_anomaly else 'real_event_no_anomaly',
            'anomaly_reason': 'abnormal_outflow_or_venue_shift' if liquidity_anomaly else 'liquidity_within_baseline',
            'severity': 'high' if observed_volume > baseline_volume * 3 else ('medium' if liquidity_anomaly else 'low'),
            'confidence': 'medium',
            'recommended_action': 'throttle_venue_and_investigate' if liquidity_anomaly else 'continue_monitoring',
            'baseline_comparison': {'baseline_outflow_volume': baseline_volume, 'observed_volume': observed_volume, 'venue_labels': sorted(venues), 'observed_venue': observed_venue},
        }
    oracle_sources = asset.get('oracle_sources', []) if isinstance(asset.get('oracle_sources'), list) else []
    oracle_observations = payload.get('oracle_observations') if isinstance(payload.get('oracle_observations'), list) else []
    expected_freshness = int(asset.get('expected_oracle_freshness_seconds') or 0)
    expected_cadence = int(asset.get('expected_oracle_update_cadence_seconds') or 0)
    now = utc_now()
    if len(oracle_observations) < max(1, len(oracle_sources)):
        oracle = {
            'detector_family': 'oracle_integrity',
            'detector_status': 'insufficient_real_evidence',
            'anomaly_reason': 'insufficient_real_oracle_sources',
            'severity': 'high',
            'confidence': 'low',
            'recommended_action': 'restore_oracle_sources',
            'oracle_observation_details': {'required_sources': oracle_sources, 'observed_sources': oracle_observations},
        }
    else:
        stale = False
        divergence = False
        prices: list[float] = []
        for item in oracle_observations:
            observed_ts = _parse_ts(item.get('observed_at'))
            if expected_freshness and observed_ts and (now - observed_ts).total_seconds() > expected_freshness:
                stale = True
            update_interval = int(item.get('update_interval_seconds') or 0)
            if expected_cadence and update_interval and update_interval > expected_cadence:
                stale = True
            try:
                prices.append(float(item.get('price')))
            except Exception:
                continue
        if len(prices) >= 2:
            low = min(prices)
            high = max(prices)
            divergence = low > 0 and ((high - low) / low) > 0.02
        oracle_anomaly = stale or divergence
        oracle = {
            'detector_family': 'oracle_integrity',
            'detector_status': 'anomaly_detected' if oracle_anomaly else 'real_event_no_anomaly',
            'anomaly_reason': 'oracle_stale_or_divergent' if oracle_anomaly else 'oracle_integrity_normal',
            'severity': 'high' if oracle_anomaly else 'low',
            'confidence': 'high' if oracle_anomaly else 'medium',
            'recommended_action': 'pause_sensitive_routes_and_reconcile_oracles' if oracle_anomaly else 'continue_monitoring',
            'oracle_observation_details': {'observations': oracle_observations, 'prices': prices, 'divergence': divergence},
        }
    base = {
        'asset_id': asset.get('id'),
        'asset_identifier': asset.get('identifier'),
        'symbol': asset.get('asset_symbol'),
        'target_id': payload.get('target_id'),
        'evidence_origin': str((payload.get('metadata') or {}).get('evidence_origin') or 'real'),
        'provider_name': str((payload.get('metadata') or {}).get('provider_name') or 'unknown'),
        'observed_at': event.observed_at.isoformat(),
        'chain_id': payload.get('chain_id'),
        'tx_hash': payload.get('tx_hash'),
        'block_number': payload.get('block_number'),
        'log_index': payload.get('log_index'),
        'source_address': payload.get('from') or payload.get('owner'),
        'destination_address': payload.get('to'),
        'spender': payload.get('spender'),
        'contract_address': payload.get('contract_address'),
        'raw_event_type': payload.get('event_type') or event.kind,
        'normalized_event_snapshot': payload,
        'oracle_observation_details': {},
    }
    return [{**base, **item} for item in (counterparty, flow, approval, liquidity, oracle)]


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
    suppression = connection.execute(
        '''
        SELECT id
        FROM alert_suppression_rules
        WHERE workspace_id = %s
          AND (target_id IS NULL OR target_id = %s::uuid)
          AND (dedupe_signature IS NULL OR dedupe_signature = %s)
          AND (mute_until IS NULL OR mute_until >= NOW())
        LIMIT 1
        ''',
        (workspace_id, target_id, signature),
    ).fetchone()
    if suppression is not None:
        return ''
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


def _process_single_event(
    connection: Any,
    *,
    target: dict[str, Any],
    workspace: dict[str, Any],
    user_id: str,
    monitoring_run_id: str,
    event: ActivityEvent,
    monitoring_path: str = 'worker',
    configured_scenario: str | None = None,
) -> dict[str, Any]:
    asset = _load_target_asset_context(connection, workspace_id=str(target['workspace_id']), target=target)
    kind, normalized = _normalize_event(target, event, monitoring_run_id, workspace)
    ingestion_runtime = monitoring_ingestion_runtime()
    if ingestion_runtime.get('mode') in {'live', 'hybrid'} and str(event.ingestion_source or '').lower() == 'demo':
        raise RuntimeError('synthetic event leakage blocked in live/hybrid monitoring')
    response, diagnostics = _threat_call(kind, normalized, target_id=str(target['id']))
    if response is None:
        WORKER_STATE['metrics']['analysis_failures'] += 1
        raise RuntimeError(f"analysis_unavailable:{diagnostics.get('fallback_reason') or 'threat_engine_unavailable'}")
    else:
        response['analysis_source'] = str(response.get('source') or 'live')
        response['analysis_status'] = 'completed'
        response['degraded_reason'] = None
    response['ingestion_mode'] = ingestion_runtime.get('mode')
    response['monitoring_path'] = monitoring_path
    response_metadata = response.get('metadata') if isinstance(response.get('metadata'), dict) else {}
    has_confirmed_anomaly = bool(response.get('matched_patterns')) or str(response.get('severity') or '').lower() in {'high', 'critical'}
    detection_outcome = (
        'DEMO_ONLY'
        if response_metadata.get('ingestion_source') == 'demo'
        else (
            'ANALYSIS_FAILED'
            if response.get('analysis_status') == 'analysis_failed'
            else ('DETECTION_CONFIRMED' if has_confirmed_anomaly else 'NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE')
        )
    )
    truthfulness_state = 'not_claim_safe'
    response['claim_safe'] = False
    response_metadata.update(
        {
            'monitoring_analysis_type': f'monitoring_{kind}',
            'monitoring_request_keys': sorted(normalized.keys()),
            'monitoring_request_metadata_keys': sorted((normalized.get('metadata') or {}).keys()) if isinstance(normalized.get('metadata'), dict) else [],
            'evidence_state': 'demo' if response_metadata.get('ingestion_source') == 'demo' else ('degraded' if response.get('degraded') else 'real'),
            'confidence_basis': 'demo_scenario' if response_metadata.get('ingestion_source') == 'demo' else ('none' if response.get('degraded') else 'provider_evidence'),
            'truthfulness_state': truthfulness_state,
            'detection_outcome': detection_outcome,
        }
    )
    response['metadata'] = response_metadata
    detector_results = _enforce_asset_detectors(asset=asset, event=event)
    asset_detection = _asset_detection_summary(asset=asset, event=event)
    response['asset_profile_id'] = (asset or {}).get('id')
    response['asset_label'] = (asset or {}).get('name') or target.get('name')
    response['detection_family'] = asset_detection.get('detection_family')
    response['detector_family'] = asset_detection.get('detection_family')
    response['detector_status'] = asset_detection.get('detector_status')
    response['detector_results'] = detector_results
    response['anomaly_basis'] = asset_detection.get('anomaly_basis')
    response['baseline_reference'] = asset_detection.get('baseline_reference') or {
        'status': (asset or {}).get('baseline_status', 'missing'),
        'source': (asset or {}).get('baseline_source'),
        'updated_at': (asset or {}).get('baseline_updated_at'),
        'confidence': (asset or {}).get('baseline_confidence'),
        'coverage': (asset or {}).get('baseline_coverage'),
    }
    response['confidence_basis'] = asset_detection.get('confidence_basis')
    response['recommended_action'] = asset_detection.get('recommended_action') or response.get('recommended_action')
    if asset_detection.get('severity'):
        response['severity'] = asset_detection['severity']
    payload = event.payload if isinstance(event.payload, dict) else {}
    response['observed_evidence'] = {
        'event_id': event.event_id,
        'tx_hash': payload.get('tx_hash'),
        'block_number': payload.get('block_number'),
        'log_index': payload.get('log_index'),
        'observed_at': event.observed_at.isoformat(),
        'ingestion_source': event.ingestion_source,
        'evidence_origin': str((payload.get('metadata') or {}).get('evidence_origin') or event.ingestion_source),
        'provider_name': str((payload.get('metadata') or {}).get('provider_name') or 'unknown'),
    }
    response['evidence_window'] = {'start': event.observed_at.isoformat(), 'end': event.observed_at.isoformat()}
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
    alert_id = None
    incident_id = None
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
        if alert_id:
            incident_id = _maybe_create_incident(
                connection,
                workspace_id=str(target['workspace_id']),
                user_id=user_id,
                target_id=str(target['id']),
                analysis_run_id=analysis_run_id,
                alert_id=alert_id,
                response=response,
                auto_create=bool(target.get('auto_create_incidents'))
                and str(response.get('severity') or 'low').lower() in {'high', 'critical'}
                and _severity_meets_threshold(str(response.get('severity') or 'low'), severity_threshold),
            )
    response['monitoring_state'] = (
        'anomaly_escalated_to_incident' if incident_id else (
            'real_event_anomaly_detected' if asset_detection.get('detector_status') == 'anomaly_detected' else (
                'insufficient_real_evidence' if asset_detection.get('detector_status') == 'insufficient_real_evidence' else 'real_event_no_anomaly'
            )
        )
    )
    return {
        'analysis_run_id': analysis_run_id,
        'alert_id': alert_id,
        'incident_id': incident_id,
        'monitoring_state': response.get('monitoring_state'),
    }


def process_monitoring_target(connection: Any, target: dict[str, Any], *, triggered_by_user_id: str | None = None) -> dict[str, Any]:
    workspace_row = connection.execute('SELECT id, name FROM workspaces WHERE id = %s', (target['workspace_id'],)).fetchone() or {'id': target['workspace_id'], 'name': 'Workspace'}
    workspace = _json_safe_value(dict(workspace_row))
    user_id = triggered_by_user_id or str(target.get('updated_by_user_id') or target.get('created_by_user_id'))
    monitoring_run_id = str(uuid.uuid4())
    monitoring_path = 'manual_run_once' if triggered_by_user_id else 'worker'
    checkpoint = _parse_ts(target.get('monitoring_checkpoint_at') or target.get('last_checked_at'))
    provider_result: ActivityProviderResult = fetch_target_activity_result(target, checkpoint)
    events = provider_result.events

    alerts_generated = 0
    incidents_created = 0
    run_ids: list[str] = []
    last_status = 'no_real_data' if provider_result.status == 'no_evidence' else str(provider_result.status or 'no_real_data')
    last_run_id: str | None = None
    last_alert_at: datetime | None = None
    checkpoint_cursor = target.get('monitoring_checkpoint_cursor')
    checkpoint_at = checkpoint
    latest_processed_block = int(target.get('watcher_last_observed_block') or 0)
    source_status = (
        'active'
        if provider_result.evidence_state in {'REAL_EVIDENCE', 'DEMO_EVIDENCE'}
        else ('no_evidence' if provider_result.evidence_state == 'NO_EVIDENCE' else ('failed' if provider_result.evidence_state == 'FAILED_EVIDENCE' else 'degraded'))
    )
    degraded_reason: str | None = provider_result.degraded_reason
    logger.info('monitoring target fetched target=%s threshold=%s auto_create_alerts=%s', target.get('id'), str(target.get('severity_threshold') or 'medium'), bool(target.get('auto_create_alerts', True)))

    for event in events:
        processed = _process_single_event(
            connection,
            target=target,
            workspace=workspace,
            user_id=user_id,
            monitoring_run_id=monitoring_run_id,
            event=event,
            monitoring_path=monitoring_path,
        )
        analysis_run_id = str(processed['analysis_run_id'])
        run_ids.append(analysis_run_id)
        event_state = str(processed.get('monitoring_state') or 'real_event_no_anomaly')
        if event_state in {'anomaly_escalated_to_incident', 'real_event_anomaly_detected'}:
            last_status = event_state
        elif last_status not in {'anomaly_escalated_to_incident', 'real_event_anomaly_detected'}:
            last_status = event_state
        last_run_id = analysis_run_id
        checkpoint_at = event.observed_at
        checkpoint_cursor = event.cursor
        block_number = event.payload.get('block_number') if isinstance(event.payload, dict) else None
        if block_number is not None:
            try:
                latest_processed_block = max(latest_processed_block, int(block_number))
            except Exception:
                pass
        alert_id = processed.get('alert_id')
        if alert_id:
            alerts_generated += 1
            last_alert_at = utc_now()
        if processed.get('incident_id'):
            incidents_created += 1

    if not events and provider_result.mode in {'live', 'hybrid'} and str(target.get('target_type') or '').lower() in {'wallet', 'contract'}:
        if provider_result.status == 'failed':
            source_status = 'failed'
            degraded_reason = provider_result.degraded_reason or 'provider_failed'
            last_status = 'insufficient_real_evidence'
        elif provider_result.status == 'no_evidence':
            source_status = 'no_evidence'
            degraded_reason = provider_result.degraded_reason or 'no_live_events_observed'
            last_status = 'no_real_data'
        else:
            source_status = 'degraded'
            degraded_reason = provider_result.degraded_reason or 'monitoring_degraded'
            last_status = 'insufficient_real_evidence'
    if events and provider_result.synthetic and provider_result.mode in {'live', 'hybrid'}:
        source_status = 'degraded'
        degraded_reason = 'synthetic_leak_detected'
        last_status = 'degraded'

    recent_evidence_state = ui_evidence_state(provider_result.evidence_state)
    recent_truthfulness_state = ui_truthfulness_state(provider_result.truthfulness_state)
    recent_confidence_basis = (
        'demo_scenario'
        if provider_result.synthetic
        else ('provider_evidence' if bool(events) else 'none')
    )
    last_real_event_at = provider_result.last_real_event_at
    last_no_evidence_at = utc_now() if provider_result.status == 'no_evidence' else None
    last_degraded_at = utc_now() if provider_result.status == 'degraded' else None
    last_failed_monitoring_at = utc_now() if provider_result.status == 'failed' else None
    last_synthetic_event_at = checkpoint_at if provider_result.synthetic else None

    connection.execute(
        '''
        UPDATE targets
        SET last_checked_at = NOW(),
            last_run_status = %s,
            last_run_id = %s,
            last_alert_at = COALESCE(%s, last_alert_at),
            monitoring_checkpoint_at = COALESCE(%s, monitoring_checkpoint_at),
            monitoring_checkpoint_cursor = COALESCE(%s, monitoring_checkpoint_cursor),
            watcher_last_observed_block = NULLIF(%s, 0),
            watcher_checkpoint_lag_blocks = CASE WHEN NULLIF(%s, 0) IS NULL THEN watcher_checkpoint_lag_blocks ELSE GREATEST(0, %s - %s) END,
            watcher_source_status = %s,
            watcher_degraded_reason = %s,
            watcher_last_event_at = %s,
            last_real_event_at = COALESCE(%s, last_real_event_at),
            last_no_evidence_at = COALESCE(%s, last_no_evidence_at),
            last_degraded_at = COALESCE(%s, last_degraded_at),
            last_failed_monitoring_at = COALESCE(%s, last_failed_monitoring_at),
            last_synthetic_event_at = %s,
            recent_evidence_state = %s,
            recent_truthfulness_state = %s,
            recent_real_event_count = %s,
            recent_confidence_basis = %s,
            monitoring_claimed_by = NULL,
            monitoring_claimed_at = NULL,
            updated_at = NOW()
        WHERE id = %s
        ''',
        (
            last_status,
            last_run_id,
            last_alert_at,
            checkpoint_at,
            checkpoint_cursor,
            latest_processed_block,
            latest_processed_block,
            latest_processed_block,
            latest_processed_block,
            source_status,
            degraded_reason,
            checkpoint_at,
            last_real_event_at,
            last_no_evidence_at,
            last_degraded_at,
            last_failed_monitoring_at,
            last_synthetic_event_at,
            recent_evidence_state,
            recent_truthfulness_state,
            int(provider_result.recent_real_event_count),
            recent_confidence_basis,
            target['id'],
        ),
    )
    logger.info('checked target %s %s status=%s runs=%s alerts=%s incidents=%s', target['id'], target.get('name') or 'unknown', last_status, len(run_ids), alerts_generated, incidents_created)
    WORKER_STATE['metrics']['live_events_ingested'] += len(events)
    return {'target_id': str(target['id']), 'monitoring_run_id': monitoring_run_id, 'runs': run_ids, 'alerts_generated': alerts_generated, 'incidents_created': incidents_created, 'events_ingested': len(events), 'status': last_status, 'latest_processed_block': latest_processed_block, 'source_status': source_status, 'degraded_reason': degraded_reason, 'provider_status': provider_result.status, 'provider_source_type': provider_result.source_type, 'synthetic': provider_result.synthetic, 'recent_evidence_state': recent_evidence_state, 'recent_truthfulness_state': recent_truthfulness_state, 'recent_real_event_count': int(provider_result.recent_real_event_count)}


def process_ingested_event(connection: Any, *, target: dict[str, Any], event: ActivityEvent, ingestion_mode: str = 'live') -> dict[str, Any]:
    workspace_row = connection.execute('SELECT id, name FROM workspaces WHERE id = %s', (target['workspace_id'],)).fetchone() or {'id': target['workspace_id'], 'name': 'Workspace'}
    workspace = _json_safe_value(dict(workspace_row))
    user_id = str(target.get('updated_by_user_id') or target.get('created_by_user_id'))
    monitoring_run_id = str(uuid.uuid4())
    receipt = connection.execute(
        '''
        SELECT id FROM monitoring_event_receipts WHERE target_id = %s AND event_id = %s
        ''',
        (target['id'], event.event_id),
    ).fetchone()
    if receipt is not None:
        return {'status': 'duplicate_suppressed', 'event_id': event.event_id}
    processed = _process_single_event(connection, target=target, workspace=workspace, user_id=user_id, monitoring_run_id=monitoring_run_id, event=event, monitoring_path='worker')
    payload = event.payload if isinstance(event.payload, dict) else {}
    connection.execute(
        '''
        INSERT INTO monitoring_event_receipts (id, workspace_id, target_id, event_id, event_cursor, tx_hash, block_number, log_index, ingestion_source)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ''',
        (
            str(uuid.uuid4()),
            target['workspace_id'],
            target['id'],
            event.event_id,
            event.cursor,
            payload.get('tx_hash'),
            payload.get('block_number'),
            payload.get('log_index'),
            event.ingestion_source,
        ),
    )
    connection.execute(
        '''
        UPDATE targets
        SET monitoring_checkpoint_at = %s,
            monitoring_checkpoint_cursor = %s,
            last_checked_at = NOW(),
            last_run_status = 'completed',
            last_run_id = %s,
            updated_at = NOW()
        WHERE id = %s
        ''',
        (event.observed_at, event.cursor, processed['analysis_run_id'], target['id']),
    )
    return {'status': 'processed', 'event_id': event.event_id, 'analysis_run_id': processed['analysis_run_id'], 'alert_id': processed.get('alert_id')}


def run_monitoring_cycle(*, worker_name: str = 'monitoring-worker', limit: int = 50) -> dict[str, Any]:
    ingestion_runtime = monitoring_ingestion_runtime()
    if not live_mode_enabled():
        return {'checked': 0, 'alerts_generated': 0, 'runs': [], 'live_mode': False, 'ingestion_mode': ingestion_runtime.get('source', 'demo')}

    checked = 0
    due_count = 0
    alerts_generated = 0
    live_targets_checked = 0
    events_ingested = 0
    incidents_created = 0
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
                status,
                last_started_at,
                last_heartbeat_at,
                last_cycle_at,
                last_cycle_due_targets,
                last_cycle_targets_checked,
                last_cycle_alerts_generated,
                last_error,
                updated_at
            )
            VALUES (%s, TRUE, 'running', NOW(), NOW(), NOW(), 0, 0, 0, NULL, NOW())
            ON CONFLICT (worker_name)
            DO UPDATE SET running = TRUE, status = 'running', last_started_at = COALESCE(monitoring_worker_state.last_started_at, NOW()), last_heartbeat_at = NOW(), last_cycle_at = NOW(), last_error = NULL, updated_at = NOW()
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
                live_targets_checked += 1 if str(target.get('target_type') or '').lower() in {'wallet','contract'} else 0
                events_ingested += int(result.get('events_ingested', 0))
                incidents_created += int(result.get('incidents_created', 0))
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
                status = CASE WHEN CAST(%s AS text) IS NULL THEN 'idle' ELSE 'error' END,
                last_heartbeat_at = NOW(),
                last_cycle_at = NOW(),
                last_cycle_due_targets = CAST(%s AS integer),
                last_cycle_targets_checked = CAST(%s AS integer),
                last_cycle_alerts_generated = CAST(%s AS integer),
                last_error = CAST(%s AS text),
                updated_at = NOW()
            WHERE worker_name = %s
            ''',
            (error_message, due_count, checked, alerts_generated, error_message, worker_name),
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
    cycle_duration_ms = int((utc_now() - cycle_started_at).total_seconds() * 1000)
    logger.info('monitoring cycle finished worker=%s due=%s checked=%s live_targets=%s events=%s alerts=%s incidents=%s duration_ms=%s', worker_name, due_count, checked, live_targets_checked, events_ingested, alerts_generated, incidents_created, cycle_duration_ms)
    WORKER_STATE['ingestion_mode'] = ingestion_runtime.get('source')
    WORKER_STATE['degraded'] = bool(ingestion_runtime.get('degraded'))
    return {'due_targets': due_count, 'checked': checked, 'live_targets_checked': live_targets_checked, 'events_ingested': events_ingested, 'alerts_generated': alerts_generated, 'incidents_created': incidents_created, 'cycle_duration_ms': cycle_duration_ms, 'runs': runs, 'live_mode': True, 'ingestion_mode': ingestion_runtime.get('source'), 'degraded': bool(ingestion_runtime.get('degraded'))}


def list_monitoring_targets(request: Request) -> dict[str, Any]:
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        rows = connection.execute(
            '''
            SELECT id, workspace_id, name, target_type, chain_network, enabled, monitoring_enabled, monitoring_mode,
                   monitoring_interval_seconds, severity_threshold, auto_create_alerts, auto_create_incidents,
                   notification_channels, last_checked_at, last_run_status, last_run_id, last_alert_at, is_active,
                   monitoring_checkpoint_at, monitoring_checkpoint_cursor, watcher_last_observed_block, watcher_checkpoint_lag_blocks, watcher_source_status, watcher_degraded_reason,
                   last_real_event_at, last_no_evidence_at, last_degraded_at, last_failed_monitoring_at, recent_evidence_state, recent_truthfulness_state, recent_real_event_count
            FROM targets
            WHERE workspace_id = %s AND deleted_at IS NULL
            ORDER BY created_at DESC
            ''',
            (workspace_context['workspace_id'],),
        ).fetchall()
        targets: list[dict[str, Any]] = []
        for row in rows:
            item = _json_safe_value(dict(row))
            targets.append(item)
        return {'targets': targets, 'workspace': workspace_context['workspace']}


def patch_monitoring_target(target_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        row = connection.execute(
            '''
            SELECT id, monitoring_enabled, monitoring_mode, monitoring_interval_seconds, severity_threshold,
                   auto_create_alerts, auto_create_incidents, notification_channels, is_active
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
        if any(key in payload for key in ('monitoring_demo_scenario', 'monitoring_profile', 'monitoring_scenario')):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='monitoring_demo_scenario is deprecated and cannot be patched.')
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
                monitoring_demo_scenario = NULL,
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
                workspace_context['workspace_id'],
                is_active,
                user['id'],
                target_id,
            ),
        )
        logger.info('monitoring config persisted target=%s monitoring_enabled=%s threshold=%s', target_id, monitoring_enabled, threshold)
        log_audit(
            connection,
            action='target.monitoring.update',
            entity_type='target',
            entity_id=target_id,
            request=request,
            user_id=user['id'],
            workspace_id=workspace_context['workspace_id'],
            metadata={'monitoring_enabled': monitoring_enabled},
        )
        connection.commit()
        updated = connection.execute('SELECT * FROM targets WHERE id = %s', (target_id,)).fetchone()
        return {'target': _json_safe_value(dict(updated))}


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
        runtime = monitoring_ingestion_runtime()
        degraded_reason = str(runtime.get('reason')) if runtime.get('degraded') else None
        return {
            **WORKER_STATE,
            'live_mode': False,
            'mode': runtime.get('mode'),
            'operational_mode': monitoring_operational_mode(runtime, degraded=bool(runtime.get('degraded')), degraded_reason=degraded_reason),
            'ingestion_mode': runtime.get('source'),
            'source_type': runtime.get('source'),
            'degraded': runtime.get('degraded'),
            'degraded_reason': degraded_reason,
        }
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        worker_name = WORKER_STATE['worker_name']
        row = connection.execute(
            '''
            SELECT worker_name, running, status, last_started_at, last_heartbeat_at, last_cycle_at, last_cycle_due_targets,
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
        overdue = connection.execute(
            '''
            SELECT COUNT(*) AS overdue_count
            FROM targets
            WHERE deleted_at IS NULL
              AND monitoring_enabled = TRUE
              AND enabled = TRUE
              AND is_active = TRUE
              AND last_checked_at IS NOT NULL
              AND last_checked_at < NOW() - (GREATEST(monitoring_interval_seconds, 30) * INTERVAL '1 second')
            '''
        ).fetchone()
        job_state = connection.execute(
            '''
            SELECT
                COUNT(*) FILTER (WHERE status = 'queued') AS queued,
                COUNT(*) FILTER (WHERE status = 'running') AS running,
                COUNT(*) FILTER (WHERE status = 'failed') AS failed
            FROM background_jobs
            '''
        ).fetchone()
        normalized['overdue_targets'] = int((overdue or {}).get('overdue_count') or 0)
        normalized['job_delivery_state'] = _json_safe_value(dict(job_state)) if job_state is not None else {'queued': 0, 'running': 0, 'failed': 0}
        runtime = monitoring_ingestion_runtime()
        normalized['ingestion_mode'] = runtime.get('source')
        normalized['degraded'] = runtime.get('degraded')
        checkpoint_stats = connection.execute(
            '''
            SELECT
                MAX(watcher_last_observed_block) AS latest_processed_block,
                MAX(watcher_checkpoint_lag_blocks) AS max_checkpoint_lag_blocks,
                MAX(monitoring_checkpoint_at) AS latest_checkpoint_at,
                COALESCE(SUM(CASE WHEN watcher_source_status = 'degraded' THEN 1 ELSE 0 END), 0) AS degraded_targets,
                COALESCE(SUM(CASE WHEN watcher_source_status = 'active' THEN 1 ELSE 0 END), 0) AS active_targets
            FROM targets
            WHERE deleted_at IS NULL AND monitoring_enabled = TRUE AND enabled = TRUE AND is_active = TRUE
            '''
        ).fetchone()
        last_15m_events = connection.execute(
            '''
            SELECT COUNT(*) AS event_count
            FROM monitoring_event_receipts
            WHERE processed_at >= NOW() - INTERVAL '15 minutes'
            '''
        ).fetchone()
        stats = _json_safe_value(dict(checkpoint_stats or {}))
        latest_checkpoint_at = _parse_ts(stats.get('latest_checkpoint_at'))
        normalized['source_type'] = runtime.get('source')
        normalized['latest_processed_block'] = stats.get('latest_processed_block')
        normalized['checkpoint_lag_blocks'] = stats.get('max_checkpoint_lag_blocks')
        normalized['checkpoint_age_seconds'] = int((utc_now() - latest_checkpoint_at).total_seconds()) if latest_checkpoint_at else None
        normalized['event_count_last_15m'] = int((last_15m_events or {}).get('event_count') or 0)
        normalized['degraded_reason'] = runtime.get('reason') if runtime.get('degraded') else ('target_source_degraded' if int(stats.get('degraded_targets') or 0) > 0 else None)
        normalized['mode'] = runtime.get('mode')
        normalized['operational_mode'] = monitoring_operational_mode(
            runtime,
            degraded=bool(normalized.get('degraded')) or bool(normalized.get('degraded_reason')),
            degraded_reason=normalized.get('degraded_reason'),
        )
        return {**normalized, 'live_mode': True}


def production_claim_validator() -> dict[str, Any]:
    runtime = monitoring_ingestion_runtime()
    evidence_window_seconds = max(60, int(os.getenv('MONITORING_EVIDENCE_WINDOW_SECONDS', '900')))
    checks: dict[str, bool] = {
        'live_or_hybrid_mode': runtime.get('mode') in {'live', 'hybrid'},
        'live_monitoring_enabled': str(os.getenv('LIVE_MONITORING_ENABLED', 'true')).strip().lower() in {'1', 'true', 'yes', 'on'},
        'evm_rpc_reachable': False,
        'watcher_source_active': False,
        'provider_reachable_or_backfilling': False,
        'checkpoints_advancing': False,
        'no_silent_demo_fallback': not bool(runtime.get('degraded')),
        'no_synthetic_evidence_window': False,
        'real_target_exists': False,
        'analysis_evidence_real': False,
        'recent_evidence_state_real': False,
        'no_recent_degraded_or_missing': False,
        'truthfulness_not_unknown': False,
        'recent_real_event_count_positive': False,
        'evidence_window_recent_real_events': False,
        'oracle_sources_configured': False,
        'no_fallback_or_synthetic_sources': False,
    }
    reason = None
    if checks['live_or_hybrid_mode'] and checks['live_monitoring_enabled'] and (os.getenv('EVM_RPC_URL') or '').strip():
        try:
            chain_id_hex = JsonRpcClient((os.getenv('EVM_RPC_URL') or '').strip()).call('eth_chainId', [])
            checks['evm_rpc_reachable'] = bool(chain_id_hex)
        except Exception as exc:
            reason = f'evm_rpc_unreachable:{exc.__class__.__name__}'
    checks['oracle_sources_configured'] = bool((os.getenv('ORACLE_SOURCE_URLS') or '').strip() or (os.getenv('ORACLE_API_URL') or '').strip())
    if live_mode_enabled():
        health = get_monitoring_health()
        checks['watcher_source_active'] = bool((health.get('source_type') in {'websocket', 'polling', 'rpc_backfill'}) and not health.get('degraded'))
        checks['provider_reachable_or_backfilling'] = bool(
            checks['evm_rpc_reachable'] or health.get('source_type') in {'rpc_backfill', 'polling', 'websocket'}
        )
        age = health.get('checkpoint_age_seconds')
        checks['checkpoints_advancing'] = isinstance(age, int) and age <= 900
        if health.get('degraded_reason'):
            reason = str(health.get('degraded_reason'))
    else:
        checks['provider_reachable_or_backfilling'] = checks['evm_rpc_reachable']
    last_real_event_at = None
    last_demo_event_at = None
    recent_evidence_state = 'missing'
    recent_truthfulness_state = 'unknown_risk'
    recent_real_event_count = 0
    recent_confidence_basis = 'none'
    recent_claim_safe_window_passed = False
    try:
        with pg_connection() as connection:
            ensure_pilot_schema(connection)
            target_row = connection.execute(
                '''
                SELECT COUNT(*) AS total
                FROM targets
                WHERE deleted_at IS NULL
                  AND monitoring_enabled = TRUE
                  AND enabled = TRUE
                  AND is_active = TRUE
                  AND target_type IN ('wallet', 'contract')
                '''
            ).fetchone()
            checks['real_target_exists'] = int((target_row or {}).get('total') or 0) > 0
            recent = connection.execute(
                '''
                SELECT created_at, response_payload
                FROM analysis_runs
                WHERE analysis_type LIKE 'monitoring_%'
                ORDER BY created_at DESC
                LIMIT 1
                '''
            ).fetchone()
            if recent is not None:
                payload = _json_safe_value(dict(recent)).get('response_payload') or {}
                meta = payload.get('metadata') if isinstance(payload, dict) else {}
                if isinstance(meta, dict):
                    recent_evidence_state = str(meta.get('evidence_state') or 'missing')
                    recent_confidence_basis = str(meta.get('confidence_basis') or 'none')
                    recent_truthfulness_state = str(meta.get('truthfulness_state') or 'unknown_risk')
                    checks['no_fallback_or_synthetic_sources'] = (
                        str(payload.get('source') or '').lower() not in {'fallback', 'demo', 'synthetic', 'degraded'}
                        and not bool(payload.get('degraded'))
                        and str(meta.get('ingestion_source') or '').lower() not in {'demo', 'synthetic', 'fallback'}
                    )
            evidence_rollup = connection.execute(
                '''
                SELECT
                    COALESCE(SUM(CASE WHEN recent_evidence_state = 'real' THEN 1 ELSE 0 END), 0) AS real_evidence_targets,
                    COALESCE(SUM(CASE WHEN recent_evidence_state IN ('degraded', 'no_evidence', 'failed', 'missing') THEN 1 ELSE 0 END), 0) AS degraded_or_missing_targets,
                    COALESCE(SUM(CASE WHEN recent_truthfulness_state = 'unknown_risk' THEN 1 ELSE 0 END), 0) AS unknown_risk_targets,
                    COALESCE(SUM(COALESCE(recent_real_event_count, 0)), 0) AS real_event_count_total,
                    MAX(last_real_event_at) AS latest_real_event_at
                FROM targets
                WHERE deleted_at IS NULL
                  AND monitoring_enabled = TRUE
                  AND enabled = TRUE
                  AND is_active = TRUE
                  AND target_type IN ('wallet', 'contract')
                '''
            ).fetchone()
            evidence_stats = _json_safe_value(dict(evidence_rollup or {}))
            recent_real_event_count = int(evidence_stats.get('real_event_count_total') or 0)
            if evidence_stats.get('latest_real_event_at'):
                last_real_event_at = evidence_stats.get('latest_real_event_at')
            unknown_risk_detected = int(evidence_stats.get('unknown_risk_targets') or 0) > 0
            no_evidence_detected = int(evidence_stats.get('degraded_or_missing_targets') or 0) > 0
            degraded_window_detected = no_evidence_detected
            checks['truthfulness_not_unknown'] = not unknown_risk_detected and recent_truthfulness_state != 'unknown_risk'
            checks['recent_real_event_count_positive'] = recent_real_event_count > 0
            last_real = connection.execute(
                '''
                SELECT MAX(processed_at) AS ts
                FROM monitoring_event_receipts
                WHERE ingestion_source <> 'demo'
                '''
            ).fetchone()
            last_demo = connection.execute(
                '''
                SELECT MAX(processed_at) AS ts
                FROM monitoring_event_receipts
                WHERE ingestion_source = 'demo'
                '''
            ).fetchone()
            last_real_event_at = _json_safe_value(dict(last_real or {})).get('ts')
            last_demo_event_at = _json_safe_value(dict(last_demo or {})).get('ts')
    except Exception:
        checks['real_target_exists'] = False
    parsed_last_real = _parse_ts(last_real_event_at)
    evidence_window_passed = bool(parsed_last_real and int((utc_now() - parsed_last_real).total_seconds()) <= evidence_window_seconds)
    checks['evidence_window_recent_real_events'] = evidence_window_passed
    synthetic_leak_detected = last_demo_event_at is not None
    checks['no_synthetic_evidence_window'] = not synthetic_leak_detected
    checks['analysis_evidence_real'] = recent_evidence_state == 'real' and recent_confidence_basis in {'provider_evidence', 'backfill_evidence'}
    checks['recent_evidence_state_real'] = recent_evidence_state == 'real'
    checks['no_recent_degraded_or_missing'] = recent_evidence_state == 'real' and checks['recent_real_event_count_positive']
    recent_claim_safe_window_passed = (
        checks['analysis_evidence_real']
        and checks['recent_evidence_state_real']
        and checks['recent_real_event_count_positive']
        and checks['truthfulness_not_unknown']
        and checks['evidence_window_recent_real_events']
        and checks['no_synthetic_evidence_window']
        and checks['no_recent_degraded_or_missing']
        and checks['oracle_sources_configured']
        and checks['no_fallback_or_synthetic_sources']
    )
    passed = all(checks.values())
    if 'unknown_risk_detected' not in locals():
        unknown_risk_detected = recent_truthfulness_state == 'unknown_risk'
    if 'no_evidence_detected' not in locals():
        no_evidence_detected = recent_evidence_state in {'missing', 'no_evidence', 'degraded', 'failed'}
    if 'degraded_window_detected' not in locals():
        degraded_window_detected = recent_evidence_state in {'degraded', 'failed'}
    return {
        'status': 'PASS' if passed else 'FAIL',
        'sales_claims_allowed': passed,
        'checked_at': utc_now().isoformat(),
        'mode': runtime.get('mode'),
        'operational_mode': monitoring_operational_mode(runtime, degraded=bool(runtime.get('degraded')), degraded_reason=reason),
        'source_type': runtime.get('source'),
        'checks': checks,
        'reason': reason,
        'synthetic_leak_detected': synthetic_leak_detected,
        'last_real_event_at': last_real_event_at,
        'last_demo_event_at': last_demo_event_at,
        'recent_evidence_state': recent_evidence_state,
        'recent_truthfulness_state': recent_truthfulness_state,
        'recent_real_event_count': recent_real_event_count,
        'recent_confidence_basis': recent_confidence_basis,
        'recent_claim_safe_window_passed': recent_claim_safe_window_passed,
        'evidence_window_passed': checks['evidence_window_recent_real_events'],
        'evidence_window_seconds': evidence_window_seconds,
        'unknown_risk_detected': unknown_risk_detected,
        'no_evidence_detected': no_evidence_detected,
        'degraded_window_detected': degraded_window_detected,
    }


def monitoring_runtime_status() -> dict[str, Any]:
    health = get_monitoring_health()
    claim = production_claim_validator()
    recent_evidence_state = claim.get('recent_evidence_state')
    recent_real_event_count = int(claim.get('recent_real_event_count') or 0)
    recent_truthfulness_state = str(claim.get('recent_truthfulness_state') or 'unknown_risk')
    evidence_gap = recent_evidence_state in {'missing', 'no_evidence', 'degraded', 'failed'} or recent_real_event_count <= 0 or recent_truthfulness_state == 'unknown_risk'
    provider_health = 'degraded' if health.get('degraded') or evidence_gap else 'healthy'
    runtime_mode = str(health.get('operational_mode') or claim.get('operational_mode') or 'DEMO').upper()
    configured_mode = str(health.get('mode') or claim.get('mode') or 'demo').upper()
    if configured_mode in {'LIVE', 'HYBRID'} and evidence_gap:
        runtime_mode = 'DEGRADED'
    claim_safe = bool(
        claim.get('status') == 'PASS'
        and str(claim.get('recent_evidence_state') or '') == 'real'
        and int(claim.get('recent_real_event_count') or 0) > 0
        and str(claim.get('recent_truthfulness_state') or '') != 'unknown_risk'
    )
    evidence_state = str(claim.get('recent_evidence_state') or 'missing')
    truthfulness_state = str(claim.get('recent_truthfulness_state') or 'unknown_risk')
    error_code = 'UNKNOWN_RISK' if evidence_gap else None
    return {
        'mode': runtime_mode,
        'configured_mode': configured_mode,
        'status': 'MONITORING_DEGRADED' if evidence_gap else 'NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE',
        'detection_outcome': 'MONITORING_DEGRADED' if evidence_gap else 'NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE',
        'source_type': health.get('source_type') or claim.get('source_type'),
        'provider_health': provider_health,
        'provider_reachable': bool(claim.get('checks', {}).get('evm_rpc_reachable')),
        'claim_safe': claim_safe,
        'synthetic': bool(claim.get('synthetic_leak_detected')),
        'evidence_present': not evidence_gap,
        'evidence_state': evidence_state,
        'truthfulness_state': truthfulness_state,
        'latest_processed_block': health.get('latest_processed_block'),
        'latest_block': health.get('latest_processed_block'),
        'checkpoint_lag_blocks': health.get('checkpoint_lag_blocks'),
        'checkpoint_age_seconds': health.get('checkpoint_age_seconds'),
        'provider_name': 'evm_activity_provider',
        'provider_kind': 'rpc',
        'degraded_reason': health.get('degraded_reason') or claim.get('reason'),
        'error_code': error_code,
        'sales_claims_allowed': bool(claim.get('sales_claims_allowed')),
        'claim_validator_status': claim.get('status'),
        'recent_evidence_state': claim.get('recent_evidence_state'),
        'recent_truthfulness_state': claim.get('recent_truthfulness_state'),
        'recent_real_event_count': claim.get('recent_real_event_count'),
        'last_real_event_at': claim.get('last_real_event_at'),
        'recent_confidence_basis': claim.get('recent_confidence_basis'),
        'synthetic_leak_detected': claim.get('synthetic_leak_detected'),
    }
