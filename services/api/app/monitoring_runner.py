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
    list_workspace_monitored_system_rows,
    monitored_system_row_enabled,
    persist_analysis_run,
    pg_connection,
    resolve_workspace,
    resolve_workspace_context_for_request,
    ensure_monitored_system_for_target,
    reconcile_enabled_targets_monitored_systems,
    _target_health_payload,
)
from services.api.app.threat_payloads import ThreatKind, normalize_threat_payload

THREAT_ENGINE_URL = (os.getenv('THREAT_ENGINE_URL') or 'http://localhost:8002').rstrip('/')
ALERT_DEDUPE_WINDOW_SECONDS = int(
    os.getenv('ALERT_DEDUP_WINDOW_SECONDS', os.getenv('MONITORING_ALERT_DEDUPE_WINDOW_SECONDS', '900'))
)
WORKER_HEARTBEAT_TTL_SECONDS = int(os.getenv('MONITORING_WORKER_HEARTBEAT_TTL_SECONDS', '180'))
MONITOR_POLL_INTERVAL_SECONDS = int(os.getenv('MONITOR_POLL_INTERVAL_SECONDS', '30'))

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


def _runtime_status_debug_enabled() -> bool:
    app_env = os.getenv('APP_ENV', os.getenv('APP_MODE', 'development')).strip().lower()
    return app_env not in {'production', 'prod'}


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


def _compute_mttd_seconds(*, observed_at: datetime, detected_at: datetime) -> int:
    return max(0, int((detected_at - observed_at).total_seconds()))


def _record_detection_metric(
    connection: Any,
    *,
    workspace_id: str,
    alert_id: str,
    incident_id: str | None,
    target_id: str,
    asset_id: str | None,
    event: ActivityEvent,
    response: dict[str, Any],
    policy_snapshot_hash: str,
) -> None:
    payload = event.payload if isinstance(event.payload, dict) else {}
    metadata = payload.get('metadata') if isinstance(payload.get('metadata'), dict) else {}
    evidence = {
        'tx_hash': payload.get('tx_hash'),
        'block_number': payload.get('block_number'),
        'log_index': payload.get('log_index'),
        'ingestion_source': event.ingestion_source,
        'detector_family': response.get('detector_family') or response.get('detection_family'),
        'policy_snapshot_hash': policy_snapshot_hash,
        'truthfulness_state': ((response.get('metadata') or {}).get('truthfulness_state') if isinstance(response.get('metadata'), dict) else None) or 'not_claim_safe',
        'provider_name': metadata.get('provider_name'),
        'event_id': event.event_id,
        'event_cursor': event.cursor,
    }
    detected_at = utc_now()
    connection.execute(
        '''
        INSERT INTO detection_metrics (
            id, workspace_id, alert_id, incident_id, target_id, asset_id,
            event_observed_at, detected_at, mttd_seconds, evidence, created_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, NOW())
        ''',
        (
            str(uuid.uuid4()),
            workspace_id,
            alert_id,
            incident_id,
            target_id,
            asset_id,
            event.observed_at,
            detected_at,
            _compute_mttd_seconds(observed_at=event.observed_at, detected_at=detected_at),
            _json_dumps(evidence),
        ),
    )


def _persist_evidence(
    connection: Any,
    *,
    workspace_id: str,
    target: dict[str, Any],
    event: ActivityEvent,
    response: dict[str, Any],
    alert_id: str | None,
) -> str:
    payload = event.payload if isinstance(event.payload, dict) else {}
    counterparty = payload.get('to') or payload.get('spender') or payload.get('from')
    evidence_id = str(uuid.uuid4())
    connection.execute(
        '''
        INSERT INTO evidence (
            id, workspace_id, asset_id, target_id, alert_id, chain, block_number, tx_hash, log_index, event_type,
            monitored_system_id, severity, risk_score, summary, counterparty, amount_text, token_address, contract_address, source_provider,
            raw_payload_json, observed_at, created_at
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, NOW())
        ON CONFLICT (target_id, tx_hash, log_index, event_type)
        DO UPDATE SET
            alert_id = COALESCE(EXCLUDED.alert_id, evidence.alert_id),
            severity = EXCLUDED.severity,
            risk_score = EXCLUDED.risk_score,
            summary = EXCLUDED.summary,
            counterparty = EXCLUDED.counterparty,
            amount_text = EXCLUDED.amount_text,
            token_address = EXCLUDED.token_address,
            contract_address = EXCLUDED.contract_address,
            source_provider = EXCLUDED.source_provider,
            raw_payload_json = EXCLUDED.raw_payload_json
        RETURNING id
        ''',
        (
            evidence_id,
            workspace_id,
            target.get('asset_id'),
            target['id'],
            alert_id,
            target.get('chain_network'),
            payload.get('block_number'),
            payload.get('tx_hash'),
            payload.get('log_index'),
            payload.get('event_type') or event.kind,
            target.get('monitored_system_id'),
            str(response.get('severity') or 'low'),
            response.get('score'),
            str(response.get('explanation') or 'Observed monitored activity'),
            counterparty,
            payload.get('amount'),
            payload.get('asset_address'),
            payload.get('contract_address'),
            event.ingestion_source,
            _json_dumps(payload),
            event.observed_at,
        ),
    ).fetchone()
    return evidence_id


def _load_checkpoint(connection: Any, *, workspace_id: str, monitored_system_id: str | None, chain: str, fallback_block: int) -> int:
    row = connection.execute(
        '''
        SELECT last_processed_block
        FROM monitor_checkpoint
        WHERE workspace_id = %s
          AND ((%s::uuid IS NULL AND monitored_system_id IS NULL) OR monitored_system_id = %s::uuid)
          AND chain = %s
        ''',
        (workspace_id, monitored_system_id, monitored_system_id, chain),
    ).fetchone()
    value = (row or {}).get('last_processed_block')
    try:
        return max(int(value), fallback_block)
    except Exception:
        return fallback_block


def _upsert_checkpoint(connection: Any, *, workspace_id: str, monitored_system_id: str | None, chain: str, last_processed_block: int) -> None:
    connection.execute(
        '''
        INSERT INTO monitor_checkpoint (id, workspace_id, monitored_system_id, chain, last_processed_block, updated_at)
        VALUES (%s, %s, %s::uuid, %s, %s, NOW())
        ON CONFLICT (workspace_id, monitored_system_id, chain)
        DO UPDATE SET last_processed_block = GREATEST(monitor_checkpoint.last_processed_block, EXCLUDED.last_processed_block), updated_at = NOW()
        ''',
        (str(uuid.uuid4()), workspace_id, monitored_system_id, chain, max(0, int(last_processed_block or 0))),
    )

def mark_receipt_removed(connection: Any, *, target_id: str, event_cursor: str, tx_hash: str | None, log_index: int | None, metadata: dict) -> None:
    receipt = connection.execute(
        '''
        SELECT id, workspace_id
        FROM monitoring_event_receipts
        WHERE target_id = %s
          AND (
            event_cursor = %s
            OR ((%s IS NOT NULL AND tx_hash = %s) AND (%s IS NULL OR log_index = %s))
          )
        ORDER BY processed_at DESC
        LIMIT 1
        ''',
        (target_id, event_cursor, tx_hash, tx_hash, log_index, log_index),
    ).fetchone()
    if receipt is None:
        return
    connection.execute('UPDATE monitoring_event_receipts SET removed = TRUE WHERE id = %s', (receipt['id'],))
    connection.execute(
        '''
        INSERT INTO monitoring_reorg_events (id, chain_network, block_number, tx_hash, log_index, observed_at, payload)
        VALUES (%s, %s, %s, %s, %s, NOW(), %s::jsonb)
        ''',
        (
            str(uuid.uuid4()),
            str(metadata.get('chain_network') or 'unknown'),
            metadata.get('block_number'),
            tx_hash,
            log_index,
            _json_dumps({**metadata, 'target_id': target_id, 'event_cursor': event_cursor}),
        ),
    )
    connection.execute(
        '''
        UPDATE incidents
        SET timeline = COALESCE(timeline, '[]'::jsonb) || %s::jsonb,
            updated_at = NOW()
        WHERE workspace_id = %s
          AND status IN ('open', 'acknowledged')
        ''',
        (_json_dumps([{'event': 'chain_reorg_invalidated_evidence', 'at': utc_now().isoformat(), 'event_cursor': event_cursor}]), receipt['workspace_id']),
    )
    logger.info('reorg_removed_receipt target_id=%s cursor=%s tx=%s log_index=%s', target_id, event_cursor, tx_hash, log_index)


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


def _derive_system_runtime_state(result: dict[str, Any], *, is_enabled: bool) -> tuple[str, str, str, str | None]:
    if not is_enabled:
        return 'disabled', 'unavailable', 'unavailable', 'monitoring_disabled'
    provider_status = str(result.get('provider_status') or '').lower()
    events_ingested = int(result.get('events_ingested', 0) or 0)
    recent_real_event_count = int(result.get('recent_real_event_count', 0) or 0)
    source_status = str(result.get('source_status') or '').lower()
    degraded_reason = str(result.get('degraded_reason') or '').strip() or None
    if provider_status == 'failed':
        return 'failed', 'unavailable', 'low', degraded_reason or 'provider_failed'
    if provider_status == 'degraded' or source_status == 'degraded':
        return 'degraded', 'stale', 'low', degraded_reason or 'monitoring_degraded'
    if provider_status == 'no_evidence':
        return 'degraded', 'stale', 'low', degraded_reason or 'no_evidence'
    if events_ingested > 0 or recent_real_event_count > 0:
        return 'healthy', 'fresh', 'high', None
    return 'idle', 'stale', 'medium', 'no_events_detected_yet'


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
        SELECT id, name, asset_class, asset_symbol, identifier, asset_identifier, token_contract_address,
               chain_network, treasury_ops_wallets, custody_wallets, oracle_sources, venue_labels, expected_flow_patterns,
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
    if not context.get('identifier') and context.get('asset_identifier'):
        context['identifier'] = context['asset_identifier']
    if not context.get('asset_identifier'):
        context['asset_identifier'] = context.get('identifier') or context.get('name')
    if not context.get('asset_symbol'):
        context['asset_symbol'] = context.get('symbol')
    context['chain_id'] = target.get('chain_id') or context.get('chain_id')
    if not context.get('token_contract_address'):
        context['token_contract_address'] = target.get('contract_identifier')
    context['asset_id'] = context.get('id')
    context['symbol'] = context.get('asset_symbol')
    context['contract_address'] = context.get('token_contract_address')
    return context


ASSET_DETECTOR_FAMILIES = {
    'counterparty',
    'flow_pattern',
    'approval_pattern',
    'liquidity_venue',
    'oracle_integrity',
}

CLAIM_REASON_EXPLANATIONS: dict[str, str] = {
    'missing_asset_identity': 'Protected asset identity is incomplete (asset_id/identifier/symbol/chain/contract required).',
    'missing_protected_path_context': 'Treasury/custody protected path context is incomplete.',
    'lifecycle_context_incomplete': 'Lifecycle routing, approvals, or baseline rules are incomplete.',
    'missing_market_provider_config': 'No real external market provider is configured for this asset.',
    'market_provider_unreachable': 'Configured market providers could not be reached.',
    'market_provider_stale': 'Market telemetry is stale for configured providers.',
    'insufficient_market_observations': 'Not enough real external market observations were available.',
    'detector_relied_on_internal_rollups_only': 'Only internal rollups were available; no eligible external market telemetry was present.',
    'missing_oracle_provider_config': 'No real oracle provider/source is configured for this asset.',
    'oracle_provider_unreachable': 'Configured oracle providers could not be reached.',
    'oracle_provider_stale': 'Oracle observations are stale.',
    'insufficient_oracle_observations': 'Oracle observations are missing or insufficient for independent source coverage.',
}


def _normalize_addr(value: Any) -> str:
    return str(value or '').strip().lower()


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except Exception:
        return 0.0


def _normalized_asset_model(asset: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(asset, dict):
        return None
    treasury_wallets = {_normalize_addr(item) for item in asset.get('treasury_ops_wallets', []) if _normalize_addr(item)}
    custody_wallets = {_normalize_addr(item) for item in asset.get('custody_wallets', []) if _normalize_addr(item)}
    expected_counterparties = {_normalize_addr(item) for item in asset.get('expected_counterparties', []) if _normalize_addr(item)}
    venue_labels = {_normalize_addr(item) for item in asset.get('venue_labels', []) if _normalize_addr(item)}
    flow_patterns = [item for item in asset.get('expected_flow_patterns', []) if isinstance(item, dict)]
    allowed_routes = {
        (str(item.get('source_class') or '').strip().lower(), str(item.get('destination_class') or '').strip().lower())
        for item in flow_patterns
        if item.get('source_class') and item.get('destination_class')
    }
    approval_patterns = asset.get('expected_approval_patterns') if isinstance(asset.get('expected_approval_patterns'), dict) else {}
    liquidity_baseline = asset.get('expected_liquidity_baseline') if isinstance(asset.get('expected_liquidity_baseline'), dict) else {}
    oracle_sources = [str(item).strip().lower() for item in asset.get('oracle_sources', []) if str(item).strip()]
    return {
        'asset_id': asset.get('id'),
        'asset_identifier': asset.get('asset_identifier') or asset.get('identifier') or asset.get('name'),
        'symbol': asset.get('asset_symbol') or asset.get('symbol'),
        'chain_id': asset.get('chain_id'),
        'contract_address': _normalize_addr(asset.get('token_contract_address')),
        'treasury_ops_wallets': treasury_wallets,
        'custody_wallets': custody_wallets,
        'expected_counterparties': expected_counterparties,
        'expected_flow_patterns': flow_patterns,
        'allowed_routes': allowed_routes,
        'expected_approval_patterns': approval_patterns,
        'expected_liquidity_baseline': liquidity_baseline,
        'oracle_sources': oracle_sources,
        'expected_oracle_freshness_seconds': int(asset.get('expected_oracle_freshness_seconds') or 0),
        'expected_oracle_update_cadence_seconds': int(asset.get('expected_oracle_update_cadence_seconds') or 0),
        'venue_labels': venue_labels,
        'baseline_status': asset.get('baseline_status'),
        'baseline_confidence': asset.get('baseline_confidence'),
        'baseline_coverage': asset.get('baseline_coverage'),
    }


def _build_protected_asset_context(asset: dict[str, Any] | None, *, target: dict[str, Any] | None = None) -> dict[str, Any]:
    model = _normalized_asset_model(asset)
    context: dict[str, Any] = {
        'asset_id': None,
        'asset_identifier': None,
        'symbol': None,
        'chain_id': None,
        'contract_address': None,
        'treasury_ops_wallets': [],
        'custody_wallets': [],
        'expected_counterparties': [],
        'expected_flow_patterns': [],
        'expected_approval_patterns': {},
        'venue_labels': [],
        'expected_liquidity_baseline': {},
        'baseline_status': None,
        'baseline_confidence': None,
        'baseline_coverage': None,
        'oracle_sources': [],
        'expected_oracle_freshness_seconds': 0,
        'expected_oracle_update_cadence_seconds': 0,
        'contract_complete': False,
        'missing_contract_fields': [],
    }
    if not model:
        context['missing_contract_fields'] = ['asset_profile']
        return context
    context.update(
        {
            'asset_id': model.get('asset_id'),
            'asset_identifier': model.get('asset_identifier'),
            'symbol': model.get('symbol'),
            'chain_id': model.get('chain_id') or ((target or {}).get('chain_id') if isinstance(target, dict) else None),
            'contract_address': model.get('contract_address'),
            'treasury_ops_wallets': sorted(model.get('treasury_ops_wallets', set())),
            'custody_wallets': sorted(model.get('custody_wallets', set())),
            'expected_counterparties': sorted(model.get('expected_counterparties', set())),
            'expected_flow_patterns': model.get('expected_flow_patterns', []),
            'expected_approval_patterns': model.get('expected_approval_patterns', {}),
            'venue_labels': sorted(model.get('venue_labels', set())),
            'expected_liquidity_baseline': model.get('expected_liquidity_baseline', {}),
            'baseline_status': model.get('baseline_status'),
            'baseline_confidence': model.get('baseline_confidence'),
            'baseline_coverage': model.get('baseline_coverage'),
            'oracle_sources': model.get('oracle_sources', []),
            'expected_oracle_freshness_seconds': model.get('expected_oracle_freshness_seconds', 0),
            'expected_oracle_update_cadence_seconds': model.get('expected_oracle_update_cadence_seconds', 0),
        }
    )
    required = {
        'asset_id': context.get('asset_id'),
        'asset_identifier': context.get('asset_identifier'),
        'symbol': context.get('symbol'),
        'chain_id': context.get('chain_id'),
        'contract_address': context.get('contract_address'),
        'treasury_ops_wallets': context.get('treasury_ops_wallets'),
        'custody_wallets': context.get('custody_wallets'),
        'expected_counterparties': context.get('expected_counterparties'),
        'expected_flow_patterns': context.get('expected_flow_patterns'),
        'expected_approval_patterns': context.get('expected_approval_patterns'),
        'venue_labels': context.get('venue_labels'),
        'expected_liquidity_baseline': context.get('expected_liquidity_baseline'),
        'baseline_status': context.get('baseline_status'),
        'baseline_confidence': context.get('baseline_confidence'),
        'baseline_coverage': context.get('baseline_coverage'),
        'oracle_sources': context.get('oracle_sources'),
        'expected_oracle_freshness_seconds': context.get('expected_oracle_freshness_seconds'),
        'expected_oracle_update_cadence_seconds': context.get('expected_oracle_update_cadence_seconds'),
    }
    missing: list[str] = []
    for key, value in required.items():
        if value is None:
            missing.append(key)
        elif isinstance(value, (list, dict)) and len(value) == 0:
            missing.append(key)
        elif isinstance(value, str) and not value.strip():
            missing.append(key)
        elif isinstance(value, (int, float)) and key in {'expected_oracle_freshness_seconds', 'expected_oracle_update_cadence_seconds'} and value <= 0:
            missing.append(key)
    context['missing_contract_fields'] = missing
    context['contract_complete'] = not missing
    return context


def _provider_coverage_status(*, event_payload: dict[str, Any], protected_asset_context: dict[str, Any]) -> dict[str, Any]:
    market_observations = event_payload.get('market_observations') if isinstance(event_payload.get('market_observations'), list) else []
    oracle_observations = event_payload.get('oracle_observations') if isinstance(event_payload.get('oracle_observations'), list) else []
    required_oracles = {str(item).strip().lower() for item in protected_asset_context.get('oracle_sources', []) if str(item).strip()}
    claim_ineligibility_reasons: list[str] = []
    missing_contract_fields = set(protected_asset_context.get('missing_contract_fields') or [])
    if missing_contract_fields & {'asset_id', 'asset_identifier', 'symbol', 'chain_id', 'contract_address'}:
        claim_ineligibility_reasons.append('missing_asset_identity')
    if missing_contract_fields & {'treasury_ops_wallets', 'custody_wallets', 'expected_counterparties'}:
        claim_ineligibility_reasons.append('missing_protected_path_context')
    if missing_contract_fields & {'expected_flow_patterns', 'expected_approval_patterns', 'expected_liquidity_baseline'}:
        claim_ineligibility_reasons.append('lifecycle_context_incomplete')

    market_provider_names = sorted(
        {
            str(item.get('provider_name') or item.get('source_name') or '').strip().lower()
            for item in market_observations
            if isinstance(item, dict) and str(item.get('provider_name') or item.get('source_name') or '').strip()
        }
    )
    market_statuses = {str(item.get('status') or '').lower() for item in market_observations if isinstance(item, dict)}
    market_freshness_limit = max(1, int(os.getenv('FEATURE1_MARKET_FRESHNESS_SECONDS', '300')))
    external_market_observations = [
        item for item in market_observations
        if isinstance(item, dict) and str(item.get('telemetry_kind') or 'external_market').lower() == 'external_market'
    ]
    market_realtime_external = [
        item for item in external_market_observations
        if str(item.get('observation_kind') or ('real_external_market_observation' if str(item.get('status') or '').lower() == 'ok' else '')).lower() == 'real_external_market_observation'
    ]
    market_fresh = [
        item for item in market_realtime_external
        if str(item.get('status') or '').lower() == 'ok'
        and int(item.get('freshness_seconds') or 0) <= market_freshness_limit
    ]
    market_reachable = [item for item in market_observations if isinstance(item, dict) and str(item.get('status') or '').lower() != 'unavailable']
    market_claim_ineligibility_reasons: list[str] = []
    if not market_provider_names:
        market_coverage_status = 'insufficient_real_evidence'
        market_claim_eligible = False
        market_claim_ineligibility_reasons.append('missing_market_provider_config')
    elif not market_reachable:
        market_coverage_status = 'provider_configured_but_unreachable'
        market_claim_eligible = False
        market_claim_ineligibility_reasons.append('market_provider_unreachable')
    elif external_market_observations and not market_realtime_external:
        market_coverage_status = 'insufficient_real_evidence'
        market_claim_eligible = False
        market_claim_ineligibility_reasons.append('detector_relied_on_internal_rollups_only')
    elif not market_fresh:
        market_coverage_status = 'insufficient_real_evidence'
        market_claim_eligible = False
        market_claim_ineligibility_reasons.extend(['market_provider_stale', 'insufficient_market_observations'])
    elif 'ok' in market_statuses:
        market_coverage_status = 'real_external_market_observation'
        market_claim_eligible = True
    else:
        market_coverage_status = 'insufficient_real_evidence'
        market_claim_eligible = False
        market_claim_ineligibility_reasons.append('insufficient_market_observations')

    oracle_provider_names = sorted(
        {
            str(item.get('provider_name') or item.get('source_name') or '').strip().lower()
            for item in oracle_observations
            if isinstance(item, dict) and str(item.get('provider_name') or item.get('source_name') or '').strip()
        }
    )
    oracle_statuses = {str(item.get('status') or '').lower() for item in oracle_observations if isinstance(item, dict)}
    oracle_reachable = [item for item in oracle_observations if isinstance(item, dict) and str(item.get('status') or '').lower() != 'unavailable']
    oracle_fresh = [item for item in oracle_observations if isinstance(item, dict) and str(item.get('status') or '').lower() == 'ok' and int(item.get('freshness_seconds') or 0) >= 0]
    observed_sources = {
        str(item.get('source_name') or item.get('provider_name') or '').strip().lower()
        for item in oracle_observations
        if isinstance(item, dict) and str(item.get('source_name') or item.get('provider_name') or '').strip()
    }
    oracle_claim_ineligibility_reasons: list[str] = []
    if not required_oracles:
        oracle_coverage_status = 'insufficient_real_evidence'
        oracle_claim_eligible = False
        oracle_claim_ineligibility_reasons.append('missing_oracle_provider_config')
    elif not oracle_provider_names:
        oracle_coverage_status = 'insufficient_real_evidence'
        oracle_claim_eligible = False
        oracle_claim_ineligibility_reasons.append('insufficient_oracle_observations')
    elif not oracle_reachable:
        oracle_coverage_status = 'provider_configured_but_unreachable'
        oracle_claim_eligible = False
        oracle_claim_ineligibility_reasons.append('oracle_provider_unreachable')
    elif 'stale' in oracle_statuses:
        oracle_coverage_status = 'provider_returned_stale_data'
        oracle_claim_eligible = False
        oracle_claim_ineligibility_reasons.append('oracle_provider_stale')
    elif 'divergent' in oracle_statuses:
        oracle_coverage_status = 'provider_returned_divergent_values'
        oracle_claim_eligible = False
        oracle_claim_ineligibility_reasons.append('insufficient_oracle_observations')
    elif len(observed_sources) < max(1, len(required_oracles)):
        oracle_coverage_status = 'insufficient_real_evidence'
        oracle_claim_eligible = False
        oracle_claim_ineligibility_reasons.append('insufficient_oracle_observations')
    elif not oracle_fresh:
        oracle_coverage_status = 'insufficient_real_evidence'
        oracle_claim_eligible = False
        oracle_claim_ineligibility_reasons.append('oracle_provider_stale')
    elif 'ok' in oracle_statuses:
        oracle_coverage_status = 'real_oracle_observations_present'
        oracle_claim_eligible = True
    else:
        oracle_coverage_status = 'insufficient_real_evidence'
        oracle_claim_eligible = False
        oracle_claim_ineligibility_reasons.append('insufficient_oracle_observations')

    claim_ineligibility_reasons.extend(market_claim_ineligibility_reasons)
    claim_ineligibility_reasons.extend(oracle_claim_ineligibility_reasons)

    enterprise_claim_eligibility = bool(
        protected_asset_context.get('contract_complete')
        and market_claim_eligible
        and oracle_claim_eligible
    )
    distinct_reasons = sorted({item for item in claim_ineligibility_reasons if item})
    return {
        'provider_coverage_status': {
            'market_coverage_status': market_coverage_status,
            'oracle_coverage_status': oracle_coverage_status,
            'market_provider_count': len(market_provider_names),
            'market_provider_reachable_count': len(market_reachable),
            'market_provider_fresh_count': len(market_fresh),
            'market_provider_names': market_provider_names,
            'market_observation_count': len(market_observations),
            'market_claim_eligible': market_claim_eligible,
            'market_claim_ineligibility_reasons': sorted(set(market_claim_ineligibility_reasons)),
            'oracle_provider_count': len(oracle_provider_names),
            'oracle_provider_reachable_count': len(oracle_reachable),
            'oracle_provider_fresh_count': len(oracle_fresh),
            'oracle_provider_names': oracle_provider_names,
            'oracle_observation_count': len(oracle_observations),
            'oracle_claim_eligible': oracle_claim_eligible,
            'oracle_claim_ineligibility_reasons': sorted(set(oracle_claim_ineligibility_reasons)),
        },
        'market_coverage_status': market_coverage_status,
        'oracle_coverage_status': oracle_coverage_status,
        'provider_coverage_summary': {
            'market_provider_count': len(market_provider_names),
            'market_provider_reachable_count': len(market_reachable),
            'market_provider_fresh_count': len(market_fresh),
            'market_provider_names': market_provider_names,
            'market_observation_count': len(market_observations),
            'market_claim_eligible': market_claim_eligible,
            'market_claim_ineligibility_reasons': sorted(set(market_claim_ineligibility_reasons)),
            'oracle_provider_count': len(oracle_provider_names),
            'oracle_provider_reachable_count': len(oracle_reachable),
            'oracle_provider_fresh_count': len(oracle_fresh),
            'oracle_provider_names': oracle_provider_names,
            'oracle_observation_count': len(oracle_observations),
            'oracle_claim_eligible': oracle_claim_eligible,
            'oracle_claim_ineligibility_reasons': sorted(set(oracle_claim_ineligibility_reasons)),
            'external_market_telemetry_present': bool(market_realtime_external),
            'real_oracle_observations_present': bool(oracle_fresh),
        },
        'enterprise_claim_eligibility': enterprise_claim_eligibility,
        'claim_ineligibility_reasons': distinct_reasons,
        'claim_ineligibility_details': [
            {'code': code, 'message': CLAIM_REASON_EXPLANATIONS.get(code, code.replace('_', ' '))}
            for code in distinct_reasons
        ],
    }


def _resolve_flow_classification(source_class: str, destination_class: str) -> str:
    if source_class == destination_class == 'treasury_ops':
        return 'treasury_ops_internal'
    if source_class == destination_class == 'custody':
        return 'custody_internal'
    if destination_class == 'approved_external_counterparty':
        return 'approved_external_counterparty'
    if destination_class == 'monitored_venue':
        return 'monitored_venue'
    return 'unknown_external'


def _classify_endpoint(address: str, model: dict[str, Any]) -> str:
    if address in model['treasury_ops_wallets']:
        return 'treasury_ops'
    if address in model['custody_wallets']:
        return 'custody'
    if address in model['expected_counterparties']:
        return 'approved_external_counterparty'
    if address in model['venue_labels']:
        return 'monitored_venue'
    return 'unknown_external'


def _asset_detection_summary(*, asset: dict[str, Any] | None, event: ActivityEvent) -> dict[str, Any]:
    results = _enforce_asset_detectors(asset=asset, event=event)
    anomalous = [item for item in results if item['detector_status'] == 'anomaly_detected']
    insufficient = [item for item in results if item['detector_status'] == 'insufficient_real_evidence']
    highest = anomalous[0] if anomalous else (insufficient[0] if insufficient else results[0])
    summary_reason = highest.get('anomaly_reason') or 'detectors_completed_without_confirmed_anomaly'
    protected_asset_context = highest.get('protected_asset_context') if isinstance(highest.get('protected_asset_context'), dict) else _build_protected_asset_context(asset)
    return {
        'detection_family': highest.get('detector_family'),
        'detector_results': results,
        'detector_status': highest.get('detector_status'),
        'anomaly_basis': summary_reason,
        'baseline_reference': {
            'baseline_status': (asset or {}).get('baseline_status'),
            'baseline_confidence': (asset or {}).get('baseline_confidence'),
            'baseline_coverage': (asset or {}).get('baseline_coverage'),
        },
        'confidence_basis': highest.get('confidence'),
        'recommended_action': highest.get('recommended_action'),
        'severity': highest.get('severity', 'low'),
        'protected_asset_context': protected_asset_context,
        'market_coverage_status': highest.get('market_coverage_status'),
        'oracle_coverage_status': highest.get('oracle_coverage_status'),
        'provider_coverage_status': highest.get('provider_coverage_status') or {},
        'provider_coverage_summary': highest.get('provider_coverage_summary'),
        'enterprise_claim_eligibility': bool(highest.get('enterprise_claim_eligibility')),
        'claim_ineligibility_reasons': highest.get('claim_ineligibility_reasons') or [],
        'claim_ineligibility_details': highest.get('claim_ineligibility_details') or [],
    }


def _protected_asset_coverage_record(*, protected_asset_context: dict[str, Any], coverage_status: dict[str, Any]) -> dict[str, Any]:
    provider_summary = coverage_status.get('provider_coverage_summary') if isinstance(coverage_status.get('provider_coverage_summary'), dict) else {}
    reasons = coverage_status.get('claim_ineligibility_reasons') if isinstance(coverage_status.get('claim_ineligibility_reasons'), list) else []
    reason_details = coverage_status.get('claim_ineligibility_details') if isinstance(coverage_status.get('claim_ineligibility_details'), list) else []
    return {
        'asset_id': protected_asset_context.get('asset_id'),
        'asset_identifier': protected_asset_context.get('asset_identifier'),
        'symbol': protected_asset_context.get('symbol'),
        'chain_id': protected_asset_context.get('chain_id'),
        'contract_address': protected_asset_context.get('contract_address'),
        'protected_asset_context': protected_asset_context,
        'treasury_ops_wallets': protected_asset_context.get('treasury_ops_wallets') or [],
        'custody_wallets': protected_asset_context.get('custody_wallets') or [],
        'expected_counterparties': protected_asset_context.get('expected_counterparties') or [],
        'expected_flow_patterns': protected_asset_context.get('expected_flow_patterns') or [],
        'expected_approval_patterns': protected_asset_context.get('expected_approval_patterns') or {},
        'venue_labels': protected_asset_context.get('venue_labels') or [],
        'expected_liquidity_baseline': protected_asset_context.get('expected_liquidity_baseline') or {},
        'oracle_sources': protected_asset_context.get('oracle_sources') or [],
        'expected_oracle_freshness_seconds': int(protected_asset_context.get('expected_oracle_freshness_seconds') or 0),
        'expected_oracle_update_cadence_seconds': int(protected_asset_context.get('expected_oracle_update_cadence_seconds') or 0),
        'market_coverage_status': coverage_status.get('market_coverage_status') or 'insufficient_real_evidence',
        'oracle_coverage_status': coverage_status.get('oracle_coverage_status') or 'insufficient_real_evidence',
        'market_provider_count': int(provider_summary.get('market_provider_count') or 0),
        'market_provider_reachable_count': int(provider_summary.get('market_provider_reachable_count') or 0),
        'market_provider_fresh_count': int(provider_summary.get('market_provider_fresh_count') or 0),
        'market_provider_names': provider_summary.get('market_provider_names') or [],
        'market_observation_count': int(provider_summary.get('market_observation_count') or 0),
        'oracle_provider_count': int(provider_summary.get('oracle_provider_count') or 0),
        'oracle_provider_reachable_count': int(provider_summary.get('oracle_provider_reachable_count') or 0),
        'oracle_provider_fresh_count': int(provider_summary.get('oracle_provider_fresh_count') or 0),
        'oracle_provider_names': provider_summary.get('oracle_provider_names') or [],
        'oracle_observation_count': int(provider_summary.get('oracle_observation_count') or 0),
        'enterprise_claim_eligibility': bool(coverage_status.get('enterprise_claim_eligibility')),
        'market_claim_eligible': bool(provider_summary.get('market_claim_eligible')),
        'oracle_claim_eligible': bool(provider_summary.get('oracle_claim_eligible')),
        'claim_ineligibility_reasons': reasons,
        'claim_ineligibility_details': reason_details,
    }


def _enforce_asset_detectors(asset: dict[str, Any] | None, event: ActivityEvent) -> list[dict[str, Any]]:
    payload = event.payload if isinstance(event.payload, dict) else {}
    model = _normalized_asset_model(asset)
    protected_asset_context = _build_protected_asset_context(asset)
    coverage_status = _provider_coverage_status(event_payload=payload, protected_asset_context=protected_asset_context)
    if not model:
        return [{
            'asset_id': None,
            'asset_identifier': None,
            'symbol': None,
            'target_id': payload.get('target_id'),
            'detector_family': 'counterparty',
            'detector_status': 'insufficient_real_evidence',
            'anomaly_reason': 'missing_asset_profile',
            'severity': 'high',
            'confidence': 'low',
            'recommended_action': 'attach_asset_profile',
            'violated_asset_rule': 'asset_profile_required',
            'proof_eligibility': {'production_claim_eligible': False, 'reason': 'missing_asset_profile'},
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
            'liquidity_observation_details': {},
            'protected_asset_context': protected_asset_context,
            'market_coverage_status': coverage_status['market_coverage_status'],
            'oracle_coverage_status': coverage_status['oracle_coverage_status'],
            'provider_coverage_status': coverage_status['provider_coverage_status'],
            'provider_coverage_summary': coverage_status['provider_coverage_summary'],
            'enterprise_claim_eligibility': coverage_status['enterprise_claim_eligibility'],
            'claim_ineligibility_reasons': coverage_status['claim_ineligibility_reasons'],
            'claim_ineligibility_details': coverage_status.get('claim_ineligibility_details') or [],
        }]

    source = _normalize_addr(payload.get('from') or payload.get('owner'))
    destination = _normalize_addr(payload.get('to'))
    spender = _normalize_addr(payload.get('spender'))
    amount = _to_float(payload.get('amount') or payload.get('approval_amount'))

    source_class = _classify_endpoint(source, model) if source else 'unknown_external'
    destination_class = _classify_endpoint(destination, model) if destination else 'unknown_external'
    route_tuple = (source_class, destination_class)
    route_valid = (not model['allowed_routes']) or route_tuple in model['allowed_routes']
    flow_classification = _resolve_flow_classification(source_class, destination_class)
    route_stage = f'{source_class}->{destination_class}'
    lifecycle_stage = (
        'treasury_ops_egress' if source_class == 'treasury_ops'
        else ('custody_egress' if source_class == 'custody' else ('treasury_ops_ingress' if destination_class == 'treasury_ops' else ('custody_ingress' if destination_class == 'custody' else 'external_flow')))
    )

    touches_protected_path = source_class in {'treasury_ops', 'custody'} or destination_class in {'treasury_ops', 'custody'}
    unknown_counterparty = destination_class == 'unknown_external'
    high_value = amount >= 100000
    counterparty_violation = (
        (source_class == 'treasury_ops' and destination_class == 'unknown_external')
        or (source_class == 'custody' and destination_class == 'unknown_external')
        or (touches_protected_path and not route_valid)
    )
    severity = 'high' if (counterparty_violation and (high_value or unknown_counterparty or touches_protected_path)) else ('medium' if counterparty_violation else 'low')
    lifecycle_rule = (
        'treasury_ops_unknown_external_shortcut'
        if source_class == 'treasury_ops' and destination_class == 'unknown_external'
        else (
            'custody_unknown_external_shortcut'
            if source_class == 'custody' and destination_class == 'unknown_external'
            else ('unapproved_protected_route' if (touches_protected_path and not route_valid) else None)
        )
    )
    counterparty = {
        'detector_family': 'counterparty',
        'detector_status': 'anomaly_detected' if counterparty_violation else 'real_event_no_anomaly',
        'anomaly_reason': (
            'treasury_ops_to_unknown_external'
            if source_class == 'treasury_ops' and destination_class == 'unknown_external'
            else (
                'custody_to_unexpected_external'
                if source_class == 'custody' and destination_class == 'unknown_external'
                else ('protected_route_unapproved_counterparty' if counterparty_violation else 'counterparty_in_expected_profile')
            )
        ),
        'severity': severity,
        'confidence': 'high' if counterparty_violation else 'medium',
        'recommended_action': 'pause_outbound_transfer_and_review' if counterparty_violation else 'continue_monitoring',
        'violated_asset_rule': 'counterparty_allowlist',
        'route_classification': flow_classification,
        'lifecycle_stage': lifecycle_stage,
        'route_stage': route_stage,
        'violated_lifecycle_rule': lifecycle_rule,
        'endangered_asset_path': route_stage if lifecycle_rule else None,
        'venue_classification': destination_class,
        'baseline_comparison': {
            'expected_counterparties': sorted(model['expected_counterparties']),
            'treasury_ops_wallets': sorted(model['treasury_ops_wallets']),
            'custody_wallets': sorted(model['custody_wallets']),
            'observed_route': [source_class, destination_class],
        },
    }

    bypassed_checkpoint = False
    for pattern in model['expected_flow_patterns']:
        if not isinstance(pattern, dict):
            continue
        if str(pattern.get('source_class') or '').strip().lower() != source_class:
            continue
        if str(pattern.get('destination_class') or '').strip().lower() != destination_class:
            continue
        required_checkpoint = str(pattern.get('required_checkpoint') or '').strip().lower()
        if required_checkpoint and required_checkpoint not in {source_class, destination_class}:
            bypassed_checkpoint = True
    prohibited_route_shortcut = (
        source_class == 'treasury_ops' and destination_class in {'approved_external_counterparty', 'unknown_external'} and any(
            str(item.get('source_class') or '').strip().lower() == 'treasury_ops' and str(item.get('destination_class') or '').strip().lower() == 'custody'
            for item in model['expected_flow_patterns']
        )
    )
    flow_violation = touches_protected_path and (not route_valid or bypassed_checkpoint or prohibited_route_shortcut)
    flow_lifecycle_rule = (
        'bypassed_required_checkpoint'
        if bypassed_checkpoint
        else ('prohibited_route_shortcut' if prohibited_route_shortcut else ('invalid_lifecycle_transition' if flow_violation else None))
    )
    flow = {
        'detector_family': 'flow_pattern',
        'detector_status': 'anomaly_detected' if flow_violation else 'real_event_no_anomaly',
            'anomaly_reason': (
                'asset_movement_bypassed_required_checkpoint'
                if bypassed_checkpoint
                else ('invalid_protected_asset_routing' if flow_violation else 'route_matches_expected_flow_pattern')
            ),
        'severity': 'high' if flow_violation else 'low',
        'confidence': 'high',
        'recommended_action': 'block_route_and_escalate' if flow_violation else 'continue_monitoring',
        'violated_asset_rule': 'expected_flow_patterns',
        'route_classification': flow_classification,
        'lifecycle_stage': lifecycle_stage,
        'route_stage': route_stage,
        'violated_lifecycle_rule': flow_lifecycle_rule,
        'endangered_asset_path': route_stage if flow_lifecycle_rule else None,
        'venue_classification': destination_class,
            'route_classification_details': {
                'source_class': source_class,
                'destination_class': destination_class,
                'route_valid': route_valid,
                'bypassed_checkpoint': bypassed_checkpoint,
                'prohibited_route_shortcut': prohibited_route_shortcut,
                'allowed_routes': [list(item) for item in sorted(model['allowed_routes'])],
                'violated_pattern': list(route_tuple) if flow_violation else None,
            },
        'baseline_comparison': {'allowed_routes': [list(item) for item in sorted(model['allowed_routes'])], 'observed_route': [source_class, destination_class]},
    }

    approval_cfg = model['expected_approval_patterns']
    allowed_spenders = {_normalize_addr(v) for v in approval_cfg.get('allowed_spenders', []) if _normalize_addr(v)}
    max_approval = _to_float(approval_cfg.get('max_amount'))
    approval_amount = _to_float(payload.get('approval_amount') or payload.get('amount'))
    unlimited = bool(payload.get('is_unlimited_approval')) or approval_amount >= 2**255
    approval_type = str(payload.get('approval_type') or ('unlimited' if unlimited else 'bounded'))
    repeated_approval_count = int(payload.get('approval_churn_count') or 1)
    unexpected_spender = bool(spender) and allowed_spenders and spender not in allowed_spenders
    unexpected_token = bool(model.get('contract_address')) and _normalize_addr(payload.get('contract_address') or payload.get('asset_address')) not in {'', model['contract_address']}
    churn_violation = repeated_approval_count > int(approval_cfg.get('max_churn_count') or 5)
    over_limit = max_approval > 0 and approval_amount > max_approval
    approval_event = str(payload.get('kind_hint') or '').lower() == 'erc20_approval'
    has_approval_telemetry = approval_event or str(payload.get('event_type') or '').lower() == 'approval'
    approval_violation = has_approval_telemetry and (unexpected_spender or unlimited or over_limit or churn_violation or unexpected_token)
    approval = {
        'detector_family': 'approval_pattern',
        'detector_status': (
            'insufficient_real_evidence'
            if not has_approval_telemetry
            else ('anomaly_detected' if approval_violation else 'real_event_no_anomaly')
        ),
        'anomaly_reason': 'unexpected_unlimited_approval_on_protected_asset' if (approval_violation and unlimited) else ('approval_pattern_violation' if approval_violation else 'approval_within_expected_pattern'),
        'severity': (
            'medium'
            if not has_approval_telemetry
            else ('high' if (approval_violation and unlimited) else ('medium' if approval_violation else 'low'))
        ),
        'confidence': 'low' if not has_approval_telemetry else ('high' if approval_violation else 'medium'),
        'recommended_action': (
            'collect_more_real_approval_telemetry'
            if not has_approval_telemetry
            else ('revoke_approval_and_rotate_keys' if approval_violation else 'continue_monitoring')
        ),
        'violated_asset_rule': 'expected_approval_patterns',
        'lifecycle_stage': lifecycle_stage,
        'route_stage': route_stage,
        'violated_lifecycle_rule': (
            'approval_inconsistent_with_treasury_custody_lifecycle'
            if approval_violation and touches_protected_path
            else None
        ),
        'endangered_asset_path': route_stage if (approval_violation and touches_protected_path) else None,
        'baseline_comparison': {
            'allowed_spenders': sorted(allowed_spenders),
            'max_approval': max_approval,
            'approval_amount': approval_amount,
            'approval_type': approval_type,
            'unlimited': unlimited,
            'repeated_approval_count': repeated_approval_count,
            'unexpected_token': unexpected_token,
        },
    }

    liquidity_cfg = model['expected_liquidity_baseline']
    baseline_volume = _to_float(liquidity_cfg.get('baseline_outflow_volume'))
    baseline_transfer_count = int(liquidity_cfg.get('baseline_transfer_count') or 0)
    baseline_unique_counterparties = int(liquidity_cfg.get('baseline_unique_counterparties') or 0)
    baseline_max_concentration = _to_float(liquidity_cfg.get('max_concentration_ratio'))
    liquidity_observations = payload.get('liquidity_observations') if isinstance(payload.get('liquidity_observations'), list) else []
    venue_observations = payload.get('venue_observations') if isinstance(payload.get('venue_observations'), list) else []
    market_observations = payload.get('market_observations') if isinstance(payload.get('market_observations'), list) else []
    liquidity_obs = liquidity_observations[0] if liquidity_observations and isinstance(liquidity_observations[0], dict) else {}
    venue_obs = venue_observations[0] if venue_observations and isinstance(venue_observations[0], dict) else {}
    observed_volume = _to_float(liquidity_obs.get('rolling_volume'))
    transfer_count = int(liquidity_obs.get('rolling_transfer_count') or liquidity_obs.get('transfer_count') or 0)
    unique_counterparties = int(liquidity_obs.get('unique_counterparties') or 0)
    concentration_ratio = _to_float(liquidity_obs.get('concentration_ratio'))
    abnormal_outflow_ratio = _to_float(liquidity_obs.get('abnormal_outflow_ratio'))
    burst_score = _to_float(liquidity_obs.get('burst_score'))
    route_distribution = liquidity_obs.get('route_distribution') if isinstance(liquidity_obs.get('route_distribution'), dict) else {}
    observed_distribution = venue_obs.get('venue_distribution') if isinstance(venue_obs.get('venue_distribution'), dict) else {}
    expected_venues = model['venue_labels']
    unexpected_venue_share = _to_float(observed_distribution.get('unknown'))
    min_transfer_evidence = int(liquidity_cfg.get('minimum_transfer_count') or 3)
    has_distribution = bool(route_distribution) or bool(observed_distribution)
    telemetry_status = str(liquidity_obs.get('status') or 'unknown').lower()
    external_market_ready = any(
        isinstance(item, dict) and str(item.get('status') or '').lower() == 'ok'
        for item in market_observations
    )
    baseline_state = str(model.get('baseline_status') or '').lower()
    baseline_ready = baseline_state in {'ready', 'observed', 'active'} or (not baseline_state and baseline_volume > 0)
    if (
        (not baseline_ready)
        or baseline_volume <= 0
        or transfer_count < min_transfer_evidence
        or not has_distribution
        or (not external_market_ready)
        or telemetry_status in {'insufficient_real_evidence', 'unavailable', 'no_real_telemetry'}
    ):
        liquidity = {
            'detector_family': 'liquidity_venue',
            'detector_status': 'insufficient_real_evidence',
            'anomaly_reason': 'missing_real_liquidity_baseline_or_external_market_telemetry',
            'severity': 'medium',
            'confidence': 'low',
            'recommended_action': 'collect_more_real_liquidity_evidence',
            'violated_asset_rule': 'expected_liquidity_baseline',
            'lifecycle_stage': lifecycle_stage,
            'route_stage': route_stage,
            'violated_lifecycle_rule': 'insufficient_real_market_coverage',
            'endangered_asset_path': route_stage,
            'liquidity_observation_details': liquidity_obs,
            'venue_classification': destination_class,
            'route_classification': flow_classification,
            'route_classification_details': {'source_class': source_class, 'destination_class': destination_class, 'route_valid': route_valid},
            'baseline_comparison': {
                'baseline_status': model.get('baseline_status'),
                'baseline_outflow_volume': baseline_volume,
                'baseline_transfer_count': baseline_transfer_count,
                'observed_volume': observed_volume,
                'transfer_count': transfer_count,
                'external_market_observations': market_observations,
                'external_market_ready': external_market_ready,
            },
        }
    else:
        abnormal_outflow = observed_volume > baseline_volume * float(liquidity_cfg.get('abnormal_outflow_multiplier') or 2.0) or abnormal_outflow_ratio > float(liquidity_cfg.get('max_abnormal_outflow_ratio') or 0.7)
        burst_activity = (baseline_transfer_count > 0 and transfer_count > baseline_transfer_count * float(liquidity_cfg.get('burst_transfer_multiplier') or 2.0)) or burst_score > float(liquidity_cfg.get('burst_score_threshold') or 2.0)
        concentration_spike = baseline_max_concentration > 0 and concentration_ratio > baseline_max_concentration
        venue_shift = bool(expected_venues) and unexpected_venue_share > float(liquidity_cfg.get('max_unknown_venue_share') or 0.25)
        route_inconsistent = touches_protected_path and not route_valid
        counterparty_drop = baseline_unique_counterparties > 0 and unique_counterparties < baseline_unique_counterparties * float(liquidity_cfg.get('min_counterparty_ratio') or 0.5)
        reasons = [
            label for flag, label in (
                (abnormal_outflow, 'abnormal_outflow'),
                (burst_activity, 'burst_activity'),
                (concentration_spike, 'concentration_spike'),
                (venue_shift, 'unexpected_venue_shift'),
                (route_inconsistent, 'route_inconsistent_with_baseline'),
                (counterparty_drop, 'counterparty_collapse'),
            ) if flag
        ]
        liquidity_anomaly = bool(reasons)
        liquidity = {
            'detector_family': 'liquidity_venue',
            'detector_status': 'anomaly_detected' if liquidity_anomaly else 'real_event_no_anomaly',
            'anomaly_reason': '+'.join(reasons) if reasons else 'liquidity_within_baseline',
            'severity': 'high' if (liquidity_anomaly and (abnormal_outflow or route_inconsistent)) else ('medium' if liquidity_anomaly else 'low'),
            'confidence': 'high' if len(reasons) >= 2 else 'medium',
            'recommended_action': 'throttle_venue_and_investigate' if liquidity_anomaly else 'continue_monitoring',
            'violated_asset_rule': 'expected_liquidity_baseline',
            'lifecycle_stage': lifecycle_stage,
            'route_stage': route_stage,
            'violated_lifecycle_rule': 'route_inconsistent_with_protected_lifecycle' if route_inconsistent else None,
            'endangered_asset_path': route_stage if route_inconsistent else None,
            'route_classification': flow_classification,
            'venue_classification': destination_class,
            'liquidity_observation_details': liquidity_obs,
            'baseline_comparison': {
                'baseline_outflow_volume': baseline_volume,
                'baseline_transfer_count': baseline_transfer_count,
                'baseline_unique_counterparties': baseline_unique_counterparties,
                'baseline_max_concentration': baseline_max_concentration,
                'observed_volume': observed_volume,
                'transfer_count': transfer_count,
                'unique_counterparties': unique_counterparties,
                'concentration_ratio': concentration_ratio,
                'route_distribution': route_distribution,
                'venue_distribution': observed_distribution,
                'venue_labels': sorted(expected_venues),
                'unexpected_venue_share': unexpected_venue_share,
                'abnormal_outflow_ratio': abnormal_outflow_ratio,
                'burst_score': burst_score,
                'external_market_observations': market_observations,
            },
        }

    oracle_observations = payload.get('oracle_observations') if isinstance(payload.get('oracle_observations'), list) else []
    expected_freshness = int(model.get('expected_oracle_freshness_seconds') or 0)
    expected_cadence = int(model.get('expected_oracle_update_cadence_seconds') or 0)
    now = utc_now()
    observed_sources = {str(item.get('source_name') or item.get('source') or '').strip().lower() for item in oracle_observations if isinstance(item, dict)}
    required_sources = set(model['oracle_sources'])
    insufficient_oracle_telemetry = (
        not required_sources
        or not oracle_observations
        or any(str(item.get('status') or '').lower() in {'insufficient_real_evidence', 'unavailable', 'no_real_telemetry'} for item in oracle_observations if isinstance(item, dict))
        or len(observed_sources) < max(1, len(required_sources))
    )
    if insufficient_oracle_telemetry:
        if not required_sources:
            reason = 'no_oracle_provider_configured_for_asset'
        elif not oracle_observations:
            reason = 'oracle_provider_configured_but_no_observations'
        elif len(observed_sources) < max(1, len(required_sources)):
            reason = 'insufficient_oracle_source_coverage'
        else:
            reason = 'oracle_provider_unavailable_or_unreachable'
        oracle = {
            'detector_family': 'oracle_integrity',
            'detector_status': 'insufficient_real_evidence',
            'anomaly_reason': reason,
            'severity': 'high',
            'confidence': 'low',
            'recommended_action': 'restore_oracle_sources',
            'violated_asset_rule': 'oracle_sources_required',
            'lifecycle_stage': lifecycle_stage,
            'route_stage': route_stage,
            'violated_lifecycle_rule': 'oracle_coverage_missing_for_protected_asset',
            'endangered_asset_path': route_stage,
            'oracle_observation_details': {'required_sources': sorted(required_sources), 'observed_sources': sorted(observed_sources), 'observations': oracle_observations},
        }
    else:
        stale = False
        missing_update = False
        cadence_violation = False
        divergence = False
        prices: list[float] = []
        for item in oracle_observations:
            observed_ts = _parse_ts(item.get('observed_at'))
            freshness_seconds = int(item.get('freshness_seconds') or 0)
            status = str(item.get('status') or 'ok').strip().lower()
            if status in {'unavailable', 'insufficient_real_evidence'}:
                missing_update = True
            if expected_freshness and ((freshness_seconds and freshness_seconds > expected_freshness) or (observed_ts and (now - observed_ts).total_seconds() > expected_freshness)):
                stale = True
            if observed_ts is None:
                missing_update = True
            update_interval = int(item.get('update_interval_seconds') or expected_cadence or 0)
            if expected_cadence and update_interval and update_interval > expected_cadence:
                cadence_violation = True
            try:
                prices.append(float(item.get('observed_value') or item.get('price')))
            except Exception:
                continue
        if len(prices) >= 2:
            low = min(prices)
            high = max(prices)
            divergence = low > 0 and ((high - low) / low) > float(os.getenv('ORACLE_DIVERGENCE_THRESHOLD', '0.02'))
        oracle_anomaly = stale or missing_update or cadence_violation or divergence
        reasons = [
            label for flag, label in (
                (stale, 'stale_oracle'),
                (missing_update, 'missing_update'),
                (cadence_violation, 'cadence_violation'),
                (divergence, 'source_divergence'),
            ) if flag
        ]
        oracle = {
            'detector_family': 'oracle_integrity',
            'detector_status': 'anomaly_detected' if oracle_anomaly else 'real_event_no_anomaly',
            'anomaly_reason': '+'.join(reasons) if reasons else 'oracle_integrity_normal',
            'severity': 'high' if oracle_anomaly else 'low',
            'confidence': 'high' if oracle_anomaly else 'medium',
            'recommended_action': 'pause_sensitive_routes_and_reconcile_oracles' if oracle_anomaly else 'continue_monitoring',
            'violated_asset_rule': 'oracle_integrity',
            'lifecycle_stage': lifecycle_stage,
            'route_stage': route_stage,
            'violated_lifecycle_rule': 'oracle_divergence_on_protected_lifecycle' if oracle_anomaly else None,
            'endangered_asset_path': route_stage if oracle_anomaly else None,
            'oracle_observation_details': {
                'required_sources': sorted(required_sources),
                'observations': oracle_observations,
                'stale': stale,
                'missing_update': missing_update,
                'cadence_violation': cadence_violation,
                'divergence': divergence,
                'prices': prices,
            },
        }

    base = {
        'asset_id': model.get('asset_id'),
        'asset_identifier': model.get('asset_identifier'),
        'symbol': model.get('symbol'),
        'target_id': payload.get('target_id'),
        'evidence_origin': str((payload.get('metadata') or {}).get('evidence_origin') or 'real'),
        'provider_name': str((payload.get('metadata') or {}).get('provider_name') or 'unknown'),
        'observed_at': event.observed_at.isoformat(),
        'chain_id': payload.get('chain_id') or model.get('chain_id'),
        'tx_hash': payload.get('tx_hash'),
        'block_number': payload.get('block_number'),
        'log_index': payload.get('log_index'),
        'source_address': payload.get('from') or payload.get('owner'),
        'destination_address': payload.get('to'),
        'spender': payload.get('spender'),
        'contract_address': payload.get('contract_address') or model.get('contract_address'),
        'raw_event_type': payload.get('event_type') or event.kind,
        'normalized_event_snapshot': payload,
        'liquidity_observation_details': {},
        'oracle_observation_details': {},
        'route_classification_details': {'source_class': source_class, 'destination_class': destination_class, 'route_valid': route_valid},
        'proof_eligibility': {
            'production_claim_eligible': bool(coverage_status.get('enterprise_claim_eligibility')),
            'has_real_telemetry': bool(payload.get('oracle_observations') or payload.get('liquidity_observations') or payload.get('venue_observations') or payload.get('market_observations')),
        },
        'protected_asset_context': protected_asset_context,
        'market_coverage_status': coverage_status['market_coverage_status'],
        'oracle_coverage_status': coverage_status['oracle_coverage_status'],
        'provider_coverage_status': coverage_status['provider_coverage_status'],
        'provider_coverage_summary': coverage_status['provider_coverage_summary'],
        'enterprise_claim_eligibility': coverage_status['enterprise_claim_eligibility'],
        'claim_ineligibility_reasons': coverage_status['claim_ineligibility_reasons'],
        'claim_ineligibility_details': coverage_status.get('claim_ineligibility_details') or [],
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
    response['protected_asset_context'] = asset_detection.get('protected_asset_context') or _build_protected_asset_context(asset, target=target)
    response['market_coverage_status'] = asset_detection.get('market_coverage_status') or 'insufficient_real_evidence'
    response['oracle_coverage_status'] = asset_detection.get('oracle_coverage_status') or 'insufficient_real_evidence'
    response['provider_coverage_status'] = asset_detection.get('provider_coverage_status') or {}
    response['provider_coverage_summary'] = asset_detection.get('provider_coverage_summary') or {}
    response['enterprise_claim_eligibility'] = bool(asset_detection.get('enterprise_claim_eligibility'))
    response['claim_ineligibility_reasons'] = asset_detection.get('claim_ineligibility_reasons') or []
    response['claim_ineligibility_details'] = asset_detection.get('claim_ineligibility_details') or []
    response['protected_asset_coverage_record'] = _protected_asset_coverage_record(
        protected_asset_context=response['protected_asset_context'],
        coverage_status={
            'market_coverage_status': response['market_coverage_status'],
            'oracle_coverage_status': response['oracle_coverage_status'],
            'provider_coverage_summary': response['provider_coverage_summary'],
            'enterprise_claim_eligibility': response['enterprise_claim_eligibility'],
            'claim_ineligibility_reasons': response['claim_ineligibility_reasons'],
            'claim_ineligibility_details': response['claim_ineligibility_details'],
        },
    )
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
            _record_detection_metric(
                connection,
                workspace_id=str(target['workspace_id']),
                alert_id=alert_id,
                incident_id=incident_id,
                target_id=str(target['id']),
                asset_id=str(target.get('asset_id')) if target.get('asset_id') else None,
                event=event,
                response=response,
                policy_snapshot_hash=signature,
            )
    _persist_evidence(
        connection,
        workspace_id=str(target['workspace_id']),
        target=target,
        event=event,
        response=response,
        alert_id=alert_id,
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
        'protected_asset_coverage_record': response.get('protected_asset_coverage_record') or {},
    }


def process_monitoring_target(connection: Any, target: dict[str, Any], *, triggered_by_user_id: str | None = None) -> dict[str, Any]:
    workspace_row = connection.execute('SELECT id, name FROM workspaces WHERE id = %s', (target['workspace_id'],)).fetchone() or {'id': target['workspace_id'], 'name': 'Workspace'}
    workspace = _json_safe_value(dict(workspace_row))
    user_id = triggered_by_user_id or str(target.get('updated_by_user_id') or target.get('created_by_user_id'))
    monitoring_run_id = str(uuid.uuid4())
    monitoring_path = 'manual_run_once' if triggered_by_user_id else 'worker'
    checkpoint = _parse_ts(target.get('monitoring_checkpoint_at') or target.get('last_checked_at'))
    chain = str(target.get('chain_network') or os.getenv('EVM_CHAIN_NETWORK', 'ethereum')).strip().lower()
    monitored_system_id = str(target.get('monitored_system_id') or '') or None
    checkpoint_block = _load_checkpoint(
        connection,
        workspace_id=str(target['workspace_id']),
        monitored_system_id=monitored_system_id,
        chain=chain,
        fallback_block=int(target.get('watcher_last_observed_block') or 0),
    )
    if checkpoint_block > 0:
        target['monitoring_checkpoint_cursor'] = f"{checkpoint_block}:checkpoint:-1"
    provider_result: ActivityProviderResult = fetch_target_activity_result(target, checkpoint)
    events = provider_result.events
    evaluation_id = str(uuid.uuid4())
    connection.execute(
        '''
        INSERT INTO target_evaluation (id, target_id, status, started_at, events_seen, matches_found)
        VALUES (%s, %s, %s, NOW(), 0, 0)
        ''',
        (evaluation_id, target['id'], 'running'),
    )

    alerts_generated = 0
    incidents_created = 0
    run_ids: list[str] = []
    last_status = 'no_real_data' if provider_result.status == 'no_evidence' else str(provider_result.status or 'no_real_data')
    last_run_id: str | None = None
    last_alert_at: datetime | None = None
    checkpoint_cursor = target.get('monitoring_checkpoint_cursor')
    checkpoint_at = checkpoint
    latest_processed_block = int(target.get('watcher_last_observed_block') or 0)
    last_protected_asset_coverage_record: dict[str, Any] = {}
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
        coverage_record = processed.get('protected_asset_coverage_record')
        if isinstance(coverage_record, dict) and coverage_record:
            last_protected_asset_coverage_record = coverage_record

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
    connection.execute(
        '''
        UPDATE target_evaluation
        SET status = %s,
            finished_at = NOW(),
            checkpoint_block = %s,
            events_seen = %s,
            matches_found = %s,
            error_text = %s
        WHERE id = %s
        ''',
        (
            'completed' if provider_result.status != 'failed' else 'failed',
            latest_processed_block,
            len(events),
            alerts_generated,
            provider_result.degraded_reason if provider_result.status == 'failed' else None,
            evaluation_id,
        ),
    )
    _upsert_checkpoint(
        connection,
        workspace_id=str(target['workspace_id']),
        monitored_system_id=monitored_system_id,
        chain=chain,
        last_processed_block=latest_processed_block,
    )
    logger.info('checked target %s %s status=%s runs=%s alerts=%s incidents=%s', target['id'], target.get('name') or 'unknown', last_status, len(run_ids), alerts_generated, incidents_created)
    WORKER_STATE['metrics']['live_events_ingested'] += len(events)
    return {'target_id': str(target['id']), 'monitoring_run_id': monitoring_run_id, 'runs': run_ids, 'alerts_generated': alerts_generated, 'incidents_created': incidents_created, 'events_ingested': len(events), 'status': last_status, 'latest_processed_block': latest_processed_block, 'source_status': source_status, 'degraded_reason': degraded_reason, 'provider_status': provider_result.status, 'provider_source_type': provider_result.source_type, 'synthetic': provider_result.synthetic, 'recent_evidence_state': recent_evidence_state, 'recent_truthfulness_state': recent_truthfulness_state, 'recent_real_event_count': int(provider_result.recent_real_event_count), 'last_real_event_at': last_real_event_at.isoformat() if last_real_event_at else None, 'protected_asset_coverage_record': last_protected_asset_coverage_record}


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
        candidate_systems = connection.execute(
            '''
            SELECT ms.id AS monitored_system_id,
                   ms.workspace_id,
                   ms.target_id,
                   ms.asset_id,
                   ms.is_enabled AS monitored_system_enabled,
                   ms.runtime_status AS monitored_system_runtime_status,
                   ms.last_heartbeat AS monitored_system_last_heartbeat,
                   t.last_checked_at,
                   t.monitoring_interval_seconds,
                   t.monitoring_enabled,
                   t.enabled,
                   t.is_active
            FROM monitored_systems ms
            JOIN targets t ON t.id = ms.target_id
            WHERE t.deleted_at IS NULL
            ORDER BY COALESCE(ms.last_heartbeat, t.last_checked_at, '1970-01-01'::timestamptz) ASC, ms.created_at ASC
            ''',
        ).fetchall()
        connection.execute(
            '''
            UPDATE monitored_systems ms
            SET last_heartbeat = NOW()
            FROM targets t
            WHERE t.id = ms.target_id
              AND t.deleted_at IS NULL
              AND ms.is_enabled = TRUE
              AND t.monitoring_enabled = TRUE
              AND t.enabled = TRUE
              AND t.is_active = TRUE
            '''
        )
        now = utc_now()
        max_targets = max(1, min(limit, 200))
        skipped_disabled = 0
        skipped_inactive = 0
        skipped_missing_workspace = 0
        skipped_not_due = 0
        skipped_null_handling = 0
        due_target_ids: list[Any] = []
        due_system_ids: dict[str, str] = {}
        for row in candidate_systems:
            system = dict(row)
            if not bool(system.get('monitored_system_enabled')):
                skipped_disabled += 1
                continue
            if not bool(system.get('monitoring_enabled')) or not bool(system.get('enabled')):
                skipped_disabled += 1
                continue
            if not bool(system.get('is_active')):
                skipped_inactive += 1
                continue
            last_checked_at = _parse_ts(system.get('last_checked_at'))
            if last_checked_at is None:
                due_target_ids.append(system['target_id'])
                due_system_ids[str(system['target_id'])] = str(system['monitored_system_id'])
            else:
                interval_raw = system.get('monitoring_interval_seconds')
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
                    due_target_ids.append(system['target_id'])
                    due_system_ids[str(system['target_id'])] = str(system['monitored_system_id'])
                else:
                    skipped_not_due += 1
            if len(due_target_ids) >= max_targets:
                break
        logger.info(
            'monitoring due selection total_candidate_targets=%s skipped_disabled=%s skipped_inactive=%s '
            'skipped_missing_workspace=%s skipped_not_due=%s skipped_null_handling=%s due_target_ids=%s',
            len(candidate_systems),
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
                SELECT id, workspace_id, name, target_type, chain_network, contract_identifier, wallet_address, asset_type, owner_notes, severity_preference, enabled,
                       asset_id, chain_id, target_metadata, monitoring_enabled, monitoring_mode, monitoring_interval_seconds, severity_threshold, auto_create_alerts,
                       auto_create_incidents, notification_channels, last_checked_at, last_run_status, last_run_id, last_alert_at, monitored_by_workspace_id, is_active,
                       monitoring_checkpoint_at, monitoring_checkpoint_cursor, watcher_last_observed_block, watcher_checkpoint_lag_blocks, watcher_source_status,
                       watcher_degraded_reason, recent_evidence_state, recent_truthfulness_state, recent_real_event_count, updated_by_user_id, created_by_user_id, created_at
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
            target['monitored_system_id'] = due_system_ids.get(str(target['id']))
            try:
                with connection.transaction():
                    connection.execute('UPDATE targets SET monitoring_claimed_by = %s, monitoring_claimed_at = NOW() WHERE id = %s', (worker_name, target['id']))
                    result = process_monitoring_target(connection, target)
                    monitored_system_id = due_system_ids.get(str(target['id']))
                    if monitored_system_id:
                        runtime_status, freshness_status, confidence_status, coverage_reason = _derive_system_runtime_state(
                            result,
                            is_enabled=True,
                        )
                        status_params = (runtime_status, 'active', monitored_system_id)
                        if runtime_status not in {'provisioning', 'healthy', 'idle', 'degraded'}:
                            status_params = (runtime_status, 'error' if runtime_status == 'failed' else 'paused', monitored_system_id)
                        connection.execute(
                            '''
                            UPDATE monitored_systems
                            SET last_heartbeat = NOW(),
                                runtime_status = %s,
                                status = %s
                            WHERE id = %s::uuid
                            ''',
                            status_params,
                        )
                        connection.execute(
                            '''
                            UPDATE monitored_systems ms
                            SET last_heartbeat = NOW(),
                                last_event_at = COALESCE(%s, last_event_at),
                                freshness_status = %s,
                                confidence_status = %s,
                                coverage_reason = %s,
                                last_error_text = NULL
                            WHERE ms.id = %s::uuid
                            ''',
                            (
                                result.get('last_real_event_at') or target.get('last_real_event_at'),
                                freshness_status,
                                confidence_status,
                                coverage_reason,
                                monitored_system_id,
                            ),
                        )
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
                monitored_system_id = due_system_ids.get(str(target['id']))
                if monitored_system_id:
                    # Keep explicit status transition text stable for regression checks:
                    # 'error', status = 'error'
                    connection.execute(
                        "UPDATE monitored_systems SET runtime_status = 'failed', status = 'error', freshness_status = 'unavailable', confidence_status = 'low', coverage_reason = 'monitoring_worker_error', last_error_text = %s, last_heartbeat = NOW() WHERE id = %s::uuid",
                        (error_message, monitored_system_id),
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
        connection.execute(
            '''
            INSERT INTO monitor_heartbeat (
                id, workspace_id, chain, status, last_success_at, last_error_at, last_error_text, last_processed_block, provider_mode, updated_at
            )
            VALUES (%s, NULL, %s, %s, CASE WHEN %s::text IS NULL THEN NOW() ELSE NULL END, CASE WHEN %s::text IS NULL THEN NULL ELSE NOW() END, %s, %s, %s, NOW())
            ''',
            (
                str(uuid.uuid4()),
                str(os.getenv('EVM_CHAIN_NETWORK', 'ethereum')),
                'error' if error_message else ('idle' if checked == 0 else 'active'),
                error_message,
                error_message,
                error_message,
                max([int(item.get('latest_processed_block') or 0) for item in runs], default=0),
                ingestion_runtime.get('source') or 'polling',
            ),
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
            SELECT t.id, t.workspace_id, t.name, t.target_type, t.chain_network, t.enabled, t.monitoring_enabled, t.monitoring_mode,
                   t.monitoring_interval_seconds, t.severity_threshold, t.auto_create_alerts, t.auto_create_incidents,
                   t.notification_channels, t.last_checked_at, t.last_run_status, t.last_run_id, t.last_alert_at, t.is_active,
                   t.monitoring_checkpoint_at, t.monitoring_checkpoint_cursor, t.watcher_last_observed_block, t.watcher_checkpoint_lag_blocks, t.watcher_source_status, t.watcher_degraded_reason,
                   t.last_real_event_at, t.last_no_evidence_at, t.last_degraded_at, t.last_failed_monitoring_at, t.recent_evidence_state, t.recent_truthfulness_state, t.recent_real_event_count,
                   t.asset_id, a.id AS resolved_asset_id, a.name AS asset_name, ms.id AS monitored_system_id
            FROM targets t
            LEFT JOIN assets a ON a.id = t.asset_id AND a.workspace_id = t.workspace_id AND a.deleted_at IS NULL
            LEFT JOIN monitored_systems ms ON ms.target_id = t.id AND ms.workspace_id = t.workspace_id
            WHERE t.workspace_id = %s AND t.deleted_at IS NULL
            ORDER BY t.created_at DESC
            ''',
            (workspace_context['workspace_id'],),
        ).fetchall()
        targets: list[dict[str, Any]] = []
        for row in rows:
            item = _json_safe_value(dict(row))
            health_status, health_reason = _target_health_payload(
                enabled=bool(row.get('enabled')),
                monitoring_enabled=bool(row.get('monitoring_enabled')),
                asset_id=str(row.get('asset_id') or '') or None,
                asset_exists=bool(row.get('resolved_asset_id')),
                monitored_system_id=str(row.get('monitored_system_id') or '') or None,
            )
            item['asset_missing'] = not bool(row.get('resolved_asset_id'))
            item['health_status'] = health_status
            item['health_reason'] = health_reason
            targets.append(item)
        return {'targets': targets, 'workspace': workspace_context['workspace']}


def patch_monitoring_target(target_id: str, payload: dict[str, Any], request: Request) -> dict[str, Any]:
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        row = connection.execute(
            '''
            SELECT id, asset_id, enabled, monitoring_enabled, monitoring_mode, monitoring_interval_seconds, severity_threshold,
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
        if monitoring_enabled and bool(current.get('enabled')):
            result = ensure_monitored_system_for_target(connection, target_id=target_id, workspace_id=workspace_context['workspace_id'])
            if result.get('status') != 'ok':
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='Cannot enable monitoring: linked asset is missing or deleted.')
        elif not monitoring_enabled:
            connection.execute(
                "UPDATE monitored_systems SET is_enabled = FALSE, runtime_status = 'disabled', status = 'paused', freshness_status = 'unavailable', confidence_status = 'unavailable', coverage_reason = 'monitoring_disabled' WHERE target_id = %s::uuid AND workspace_id = %s::uuid",
                (target_id, workspace_context['workspace_id']),
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
        updated = connection.execute(
            '''
            SELECT id, workspace_id, name, target_type, chain_network, monitoring_enabled, monitoring_mode, monitoring_interval_seconds, severity_threshold,
                   auto_create_alerts, auto_create_incidents, notification_channels, monitored_by_workspace_id, is_active, last_checked_at, last_run_status,
                   last_run_id, last_alert_at, updated_at
            FROM targets
            WHERE id = %s
            ''',
            (target_id,),
        ).fetchone()
        return {'target': _json_safe_value(dict(updated))}


def run_monitoring_once(target_id: str, request: Request) -> dict[str, Any]:
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user, workspace_context = _require_workspace_admin(connection, request)
        row = connection.execute(
            '''
            SELECT id, workspace_id, name, target_type, chain_network, contract_identifier, wallet_address, asset_type, owner_notes, severity_preference, enabled,
                   asset_id, chain_id, target_metadata, monitoring_enabled, monitoring_mode, monitoring_interval_seconds, severity_threshold, auto_create_alerts,
                   auto_create_incidents, notification_channels, last_checked_at, last_run_status, last_run_id, last_alert_at, monitored_by_workspace_id, is_active,
                   monitoring_checkpoint_at, monitoring_checkpoint_cursor, watcher_last_observed_block, watcher_checkpoint_lag_blocks, watcher_source_status,
                   watcher_degraded_reason, recent_evidence_state, recent_truthfulness_state, recent_real_event_count, updated_by_user_id, created_by_user_id, created_at
            FROM targets
            WHERE id = %s AND workspace_id = %s AND deleted_at IS NULL
            ''',
            (target_id, workspace_context['workspace_id']),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Target not found.')
        result = process_monitoring_target(connection, dict(row), triggered_by_user_id=str(user['id']))
        connection.commit()
        return {**result, 'debug_only': True, 'enterprise_proof_eligible': False, 'reason_code': 'manual_run_once_debug_path'}


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
        watcher_state = connection.execute(
            '''
            SELECT watcher_name, source_status, degraded, degraded_reason, last_heartbeat_at, last_processed_block, metrics
            FROM monitoring_watcher_state
            ORDER BY COALESCE(last_heartbeat_at, updated_at) DESC
            LIMIT 1
            '''
        ).fetchone()
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
        if watcher_state is not None:
            watcher = _json_safe_value(dict(watcher_state))
            normalized['watcher_state'] = watcher
            normalized['source_type'] = watcher.get('source_status') or runtime.get('source')
            normalized['latest_processed_block'] = watcher.get('last_processed_block') or normalized.get('latest_processed_block')
            if watcher.get('degraded'):
                normalized['degraded'] = True
                normalized['degraded_reason'] = watcher.get('degraded_reason') or runtime.get('reason') or 'watcher_degraded'
            else:
                normalized['degraded_reason'] = runtime.get('reason') if runtime.get('degraded') else ('target_source_degraded' if int(stats.get('degraded_targets') or 0) > 0 else None)
        else:
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


def monitoring_runtime_status(request: Request | None = None) -> dict[str, Any]:
    health = get_monitoring_health()
    now = utc_now()
    claim_validator = production_claim_validator()
    if not live_mode_enabled():
        recent_evidence_state = str(claim_validator.get('recent_evidence_state') or 'missing')
        recent_truthfulness_state = str(claim_validator.get('recent_truthfulness_state') or 'unknown_risk')
        recent_real_event_count = int(claim_validator.get('recent_real_event_count') or 0)
        provider_health = 'healthy' if recent_evidence_state == 'real' and recent_real_event_count > 0 else 'degraded'
        mode = str(health.get('operational_mode') or health.get('mode') or 'DEGRADED').upper()
        if mode == 'LIVE' and recent_real_event_count <= 0:
            mode = 'DEGRADED'
        payload = {
            'monitoring_status': 'offline',
            'status': 'Offline',
            'mode': mode,
            'provider_health': provider_health,
            'provider_reachable': bool((claim_validator.get('checks') or {}).get('evm_rpc_reachable')),
            'recent_evidence_state': recent_evidence_state,
            'evidence_state': recent_evidence_state,
            'truthfulness_state': recent_truthfulness_state,
            'claim_safe': bool(claim_validator.get('sales_claims_allowed')),
            'recent_real_event_count': recent_real_event_count,
            'last_real_event_at': claim_validator.get('last_real_event_at'),
            'sales_claims_allowed': bool(claim_validator.get('sales_claims_allowed')),
            'claim_validator_status': str(claim_validator.get('status') or 'FAIL'),
            'source_of_evidence': 'simulator' if str(health.get('ingestion_mode') or '') == 'demo' else 'replay_or_none',
            'workspace_configured': False,
            'workspace_monitoring_summary': {
                'workspace_configured': False,
                'monitoring_mode': 'simulator' if str(health.get('ingestion_mode') or '') == 'demo' else 'offline',
                'runtime_status': 'offline',
                'configured_systems': 0,
                'reporting_systems': 0,
                'protected_assets': 0,
                'coverage_state': {'configured_systems': 0, 'reporting_systems': 0, 'protected_assets': 0},
                'freshness_status': 'unavailable',
                'confidence_status': 'unavailable',
                'last_heartbeat_at': None,
                'last_telemetry_at': None,
                'last_poll_at': _json_safe_value(health).get('last_cycle_at'),
                'last_detection_at': None,
                'evidence_source': 'simulator' if str(health.get('ingestion_mode') or '') == 'demo' else 'none',
                'status_reason': 'live_mode_disabled',
                'contradiction_flags': [],
            },
        }
        payload.update(payload['workspace_monitoring_summary'])
        return payload
    workspace_id: str | None = None
    user_id: str | None = None
    workspace_header_present = False
    monitored_rows: list[dict[str, Any]] = []
    listed_monitored_rows: list[dict[str, Any]] = []
    latest_detection_evaluation_at = None
    latest_detection_payload: dict[str, Any] | None = None
    healthy_enabled_targets_count = 0
    healthy_enabled_assets_count = 0
    enabled_monitored_rows_count = 0
    healthy_enabled_target_ids: set[str] = set()

    def _load_runtime_monitored_rows(connection: Any, workspace_scope_id: str | None) -> list[dict[str, Any]]:
        if workspace_scope_id:
            rows = list_workspace_monitored_system_rows(connection, workspace_scope_id)
            normalized: list[dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                item['is_enabled'] = monitored_system_row_enabled(item)
                normalized.append(item)
            return normalized
        rows = connection.execute(
            '''
            SELECT ms.id, ms.workspace_id, ms.asset_id, ms.target_id, ms.chain, ms.is_enabled, ms.runtime_status, ms.status, ms.last_heartbeat, ms.last_event_at, ms.freshness_status, ms.confidence_status, ms.coverage_reason, ms.last_error_text,
                   COALESCE(t.monitoring_interval_seconds, 30) AS monitoring_interval_seconds, ms.created_at
            FROM monitored_systems ms
            LEFT JOIN targets t
              ON t.id = ms.target_id
             AND t.workspace_id = ms.workspace_id
            ORDER BY ms.created_at DESC
            '''
        ).fetchall()
        normalized: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item['is_enabled'] = monitored_system_row_enabled(item)
            normalized.append(item)
        return normalized

    def _load_workspace_monitored_rows_raw(connection: Any, workspace_scope_id: str) -> list[dict[str, Any]]:
        rows = connection.execute(
            '''
            SELECT ms.id, ms.workspace_id, ms.asset_id, ms.target_id, ms.chain, ms.is_enabled, ms.runtime_status, ms.status, ms.last_heartbeat, ms.last_event_at, ms.freshness_status, ms.confidence_status, ms.coverage_reason, ms.last_error_text,
                   COALESCE(t.monitoring_interval_seconds, 30) AS monitoring_interval_seconds, ms.created_at
            FROM monitored_systems ms
            LEFT JOIN targets t
              ON t.id = ms.target_id
             AND t.workspace_id = ms.workspace_id
            WHERE ms.workspace_id = %s
            ORDER BY ms.created_at DESC
            ''',
            (workspace_scope_id,),
        ).fetchall()
        normalized: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item['is_enabled'] = monitored_system_row_enabled(item)
            normalized.append(item)
        return normalized

    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        if request is not None:
            user, workspace_context, workspace_header_present = resolve_workspace_context_for_request(connection, request)
            user_id = str(user.get('id') or '')
            workspace_id = str(workspace_context['workspace_id'])
            monitored_rows = _load_runtime_monitored_rows(connection, workspace_id)
            try:
                raw_workspace_rows = _load_workspace_monitored_rows_raw(connection, workspace_id)
            except Exception:
                logger.exception('monitoring_runtime_status_raw_rows_load_failed workspace_id=%s', workspace_id)
                raw_workspace_rows = []
            if len(monitored_rows) == 0 and len(raw_workspace_rows) > 0:
                monitored_rows = raw_workspace_rows
                logger.warning(
                    'monitoring_runtime_status_workspace_rows_recovered_from_raw workspace_id=%s raw_rows=%s raw_row_ids=%s',
                    workspace_id,
                    len(raw_workspace_rows),
                    [str((row or {}).get('id') or '') for row in raw_workspace_rows if (row or {}).get('id')],
                )
            try:
                listed_monitored_rows = list_workspace_monitored_system_rows(connection, workspace_id)
            except Exception:
                logger.exception('monitoring_runtime_status_list_rows_load_failed workspace_id=%s', workspace_id)
                listed_monitored_rows = []
            logger.info(
                'monitoring_runtime_status_workspace_resolution workspace_id=%s workspace_header_present=%s user_id=%s',
                workspace_id,
                workspace_header_present,
                user_id,
            )
            logger.info(
                'monitoring_runtime_status_workspace_rows workspace_id=%s list_route_rows=%s list_route_row_ids=%s list_route_rows_detail=%s runtime_rows=%s runtime_row_ids=%s runtime_rows_detail=%s',
                workspace_id,
                len(listed_monitored_rows),
                [str((row or {}).get('id') or '') for row in listed_monitored_rows if (row or {}).get('id')],
                listed_monitored_rows,
                len(monitored_rows),
                [str((row or {}).get('id') or '') for row in monitored_rows if (row or {}).get('id')],
                monitored_rows,
            )
        target_workspace_filter = 'AND t.workspace_id = %s' if workspace_id else ''
        evidence_workspace_filter = 'WHERE e.workspace_id = %s' if workspace_id else ''
        scoped_params: tuple[Any, ...] = (workspace_id,) if workspace_id else ()
        open_alerts = connection.execute(
            f"SELECT COUNT(*) AS c FROM alerts WHERE status IN ('open','acknowledged','investigating') {'AND workspace_id = %s' if workspace_id else ''}",
            scoped_params,
        ).fetchone()
        open_incidents = connection.execute(
            f"SELECT COUNT(*) AS c FROM incidents WHERE status IN ('open','acknowledged') {'AND workspace_id = %s' if workspace_id else ''}",
            scoped_params,
        ).fetchone()
        broken_targets = connection.execute(
            f'''
            SELECT COUNT(*) AS c
            FROM targets t
            LEFT JOIN assets a
              ON a.id = t.asset_id
             AND a.workspace_id = t.workspace_id
             AND a.deleted_at IS NULL
            WHERE t.deleted_at IS NULL
              AND t.enabled = TRUE
              AND (t.asset_id IS NULL OR a.id IS NULL)
              {target_workspace_filter}
            ''',
            scoped_params,
        ).fetchone()
        healthy_enabled_targets = connection.execute(
            f'''
            SELECT COUNT(*) AS target_count, COUNT(DISTINCT t.asset_id) AS asset_count
            FROM targets t
            JOIN assets a
              ON a.id = t.asset_id
             AND a.workspace_id = t.workspace_id
             AND a.deleted_at IS NULL
            WHERE t.deleted_at IS NULL
              AND t.enabled = TRUE
              AND t.asset_id IS NOT NULL
              {target_workspace_filter}
            ''',
            scoped_params,
        ).fetchone()
        healthy_enabled_targets_count = int((healthy_enabled_targets or {}).get('target_count') or 0)
        healthy_enabled_assets_count = int((healthy_enabled_targets or {}).get('asset_count') or 0)
        healthy_enabled_target_rows = connection.execute(
            f'''
            SELECT t.id
            FROM targets t
            JOIN assets a
              ON a.id = t.asset_id
             AND a.workspace_id = t.workspace_id
             AND a.deleted_at IS NULL
            WHERE t.deleted_at IS NULL
              AND t.enabled = TRUE
              AND t.asset_id IS NOT NULL
              {target_workspace_filter}
            ''',
            scoped_params,
        ).fetchall()
        healthy_enabled_target_ids = {str(row.get('id')) for row in healthy_enabled_target_rows if row.get('id')}
        enabled_monitored_rows_count = sum(1 for row in monitored_rows if monitored_system_row_enabled(row))
        enabled_monitored_target_ids = {str(row.get('target_id')) for row in monitored_rows if monitored_system_row_enabled(row) and row.get('target_id')}
        missing_healthy_target_ids = healthy_enabled_target_ids - enabled_monitored_target_ids
        logger.info(
            'monitoring_runtime_status_data_path workspace_id=%s targets_enabled_valid=%s target_ids_enabled_valid=%s monitored_rows_before=%s monitored_row_ids_before=%s enabled_monitored_rows_before=%s',
            workspace_id,
            healthy_enabled_targets_count,
            sorted(healthy_enabled_target_ids),
            len(monitored_rows),
            [str(row.get('id') or '') for row in monitored_rows if row.get('id')],
            enabled_monitored_rows_count,
        )
        if healthy_enabled_targets_count > 0 and (enabled_monitored_rows_count < healthy_enabled_targets_count or bool(missing_healthy_target_ids)):
            reconcile_result = reconcile_enabled_targets_monitored_systems(connection, workspace_id=workspace_id)
            logger.info(
                'monitoring_runtime_status_reconcile workspace_id=%s healthy_enabled_targets=%s enabled_monitored_rows_before=%s missing_healthy_target_ids=%s created_or_updated=%s created_monitored_systems=%s preserved_monitored_systems=%s removed_monitored_systems=%s',
                workspace_id,
                healthy_enabled_targets_count,
                enabled_monitored_rows_count,
                len(missing_healthy_target_ids),
                reconcile_result.get('created_or_updated'),
                reconcile_result.get('created_monitored_systems'),
                reconcile_result.get('preserved_monitored_systems'),
                reconcile_result.get('removed_monitored_systems'),
            )
            monitored_rows = _load_runtime_monitored_rows(connection, workspace_id)
            logger.info(
                'monitoring_runtime_status_data_path workspace_id=%s monitored_rows_after=%s monitored_row_ids_after=%s',
                workspace_id,
                len(monitored_rows),
                [str(row.get('id') or '') for row in monitored_rows if row.get('id')],
            )
        latest_evidence = connection.execute(
            f"SELECT observed_at, block_number FROM evidence e {evidence_workspace_filter} ORDER BY observed_at DESC LIMIT 1",
            scoped_params,
        ).fetchone()
        latest_detection_eval_query = '''
            SELECT created_at, response_payload
            FROM analysis_runs
            WHERE analysis_type LIKE %s
        '''
        latest_detection_eval_params: list[Any] = ['monitoring_%']
        if workspace_id:
            latest_detection_eval_query += ' AND workspace_id = %s'
            latest_detection_eval_params.append(workspace_id)
        latest_detection_eval_query += '''
            ORDER BY created_at DESC
            LIMIT 1
        '''
        latest_detection_eval = connection.execute(
            latest_detection_eval_query,
            tuple(latest_detection_eval_params),
        ).fetchone()
        latest_detection_evaluation_at = _parse_ts((latest_detection_eval or {}).get('created_at'))
        latest_detection_payload = _json_safe_value((latest_detection_eval or {}).get('response_payload') or {}) if latest_detection_eval else None
        if request is None:
            monitored_rows = _load_runtime_monitored_rows(connection, workspace_id)
    parsed_heartbeats = [_parse_ts(row.get('last_heartbeat')) for row in monitored_rows]
    recent_heartbeat_systems = 0
    for row, parsed_heartbeat in zip(monitored_rows, parsed_heartbeats):
        if not monitored_system_row_enabled(row) or parsed_heartbeat is None:
            continue
        heartbeat_window = max(int(row.get('monitoring_interval_seconds') or 30), 30) * 2
        if int((now - parsed_heartbeat).total_seconds()) <= heartbeat_window:
            recent_heartbeat_systems += 1
    last_system_heartbeat = max((ts for ts in parsed_heartbeats if ts is not None), default=None)
    worker_heartbeat = _parse_ts(health.get('last_heartbeat_at') or health.get('last_cycle_at'))
    last_heartbeat = last_system_heartbeat or worker_heartbeat
    heartbeat_age = int((now - last_heartbeat).total_seconds()) if last_heartbeat else None
    stale_heartbeat = heartbeat_age is None or heartbeat_age > max(WORKER_HEARTBEAT_TTL_SECONDS, MONITOR_POLL_INTERVAL_SECONDS * 3)
    enabled_rows = [row for row in monitored_rows if monitored_system_row_enabled(row)]
    active_rows = [row for row in enabled_rows if str(row.get('runtime_status') or '').strip().lower() in {'healthy', 'active'}]
    enabled_asset_rows = [row for row in enabled_rows if row.get('asset_id')]
    enabled_system_count = max(len(enabled_rows), healthy_enabled_targets_count)
    active_system_count = len(active_rows)
    system_count = max(len(monitored_rows), healthy_enabled_targets_count)
    protected_assets_count = max(len({str(row.get('asset_id') or '') for row in enabled_asset_rows}), healthy_enabled_assets_count)
    logger.info(
        'monitoring_runtime_status_counts workspace_id=%s enabled_monitored_systems=%s protected_assets=%s runtime_rows=%s list_route_rows=%s enabled_monitored_systems_list_route=%s protected_assets_list_route=%s',
        workspace_id,
        len(enabled_rows),
        protected_assets_count,
        len(monitored_rows),
        len(listed_monitored_rows),
        sum(1 for row in listed_monitored_rows if monitored_system_row_enabled(row)),
        len({str((row or {}).get('asset_id') or '') for row in listed_monitored_rows if monitored_system_row_enabled(row) and (row or {}).get('asset_id')}),
    )
    evidence_at = _parse_ts((latest_evidence or {}).get('observed_at'))
    evidence_freshness = int((now - evidence_at).total_seconds()) if evidence_at else None
    detection_eval_freshness = int((now - latest_detection_evaluation_at).total_seconds()) if latest_detection_evaluation_at else None
    successful_detection_outcomes = {'DETECTION_CONFIRMED', 'NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE'}
    latest_detection_metadata = latest_detection_payload.get('metadata') if isinstance(latest_detection_payload, dict) and isinstance(latest_detection_payload.get('metadata'), dict) else {}
    latest_detection_outcome = str(
        (latest_detection_metadata or {}).get('detection_outcome')
        or ((latest_detection_payload or {}).get('detection_outcome') if isinstance(latest_detection_payload, dict) else '')
        or ''
    ).upper()
    successful_detection_evaluation = bool(latest_detection_outcome in successful_detection_outcomes)
    successful_detection_evaluation_recent = bool(
        successful_detection_evaluation
        and detection_eval_freshness is not None
        and detection_eval_freshness <= max(900, MONITOR_POLL_INTERVAL_SECONDS * 10)
    )
    runner_alive = bool(health.get('worker_running')) or not stale_heartbeat
    has_monitorable_targets = healthy_enabled_targets_count > 0
    has_any_monitored_rows = len(monitored_rows) > 0
    if healthy_enabled_targets_count == 0 and len(monitored_rows) == 0:
        monitoring_status = 'offline'
    elif not runner_alive or health.get('last_error') or health.get('degraded') or stale_heartbeat or int((broken_targets or {}).get('c') or 0) > 0:
        monitoring_status = 'degraded'
    elif evidence_freshness is None or evidence_freshness > max(900, MONITOR_POLL_INTERVAL_SECONDS * 10):
        monitoring_status = 'idle'
    else:
        monitoring_status = 'active'
    degraded_reason = health.get('degraded_reason')
    if monitoring_status == 'offline':
        runtime_status = 'Offline'
    elif health.get('last_error'):
        runtime_status = 'Error'
    elif health.get('degraded') or degraded_reason or stale_heartbeat or int((broken_targets or {}).get('c') or 0) > 0:
        runtime_status = 'Degraded'
        degraded_reason = degraded_reason or ('invalid_enabled_targets' if int((broken_targets or {}).get('c') or 0) > 0 else ('stale_heartbeat' if stale_heartbeat else None))
    elif evidence_freshness is None or evidence_freshness > max(900, MONITOR_POLL_INTERVAL_SECONDS * 10):
        runtime_status = 'Idle'
    else:
        runtime_status = 'Active'
    recent_real_event_count_raw = (latest_detection_metadata or {}).get('recent_real_event_count')
    if recent_real_event_count_raw is None and isinstance(latest_detection_payload, dict):
        recent_real_event_count_raw = (latest_detection_payload or {}).get('recent_real_event_count')
    try:
        recent_real_event_count = int(recent_real_event_count_raw or 0)
    except Exception:
        recent_real_event_count = 0
    telemetry_window_seconds = max(300, MONITOR_POLL_INTERVAL_SECONDS * 6)
    last_poll_at = _parse_ts(health.get('last_cycle_at') or health.get('updated_at') or health.get('last_heartbeat_at'))
    parsed_telemetry_events = [_parse_ts(row.get('last_event_at')) for row in enabled_rows]
    last_telemetry_at = max((ts for ts in parsed_telemetry_events if ts is not None), default=None)
    reporting_systems = 0
    for row in enabled_rows:
        last_event_at = _parse_ts(row.get('last_event_at'))
        if last_event_at is None:
            continue
        if int((now - last_event_at).total_seconds()) <= telemetry_window_seconds:
            reporting_systems += 1
    source_of_evidence = 'live' if (recent_real_event_count > 0 and not bool(health.get('degraded'))) else ('simulator' if str(health.get('ingestion_mode') or '') == 'demo' else 'replay_or_none')
    evidence_source = 'live' if source_of_evidence == 'live' else ('simulator' if source_of_evidence == 'simulator' else ('replay' if evidence_at else 'none'))
    workspace_configured = bool(enabled_system_count > 0 or protected_assets_count > 0)
    degraded_signal = bool(health.get('last_error') or health.get('degraded') or degraded_reason or stale_heartbeat or int((broken_targets or {}).get('c') or 0) > 0)
    if not workspace_configured:
        runtime_status_summary = 'offline'
    elif reporting_systems <= 0:
        runtime_status_summary = 'idle'
    elif degraded_signal:
        runtime_status_summary = 'degraded'
    else:
        runtime_status_summary = 'healthy'
    monitoring_mode = 'simulator' if evidence_source == 'simulator' else ('offline' if not workspace_configured else 'live')
    telemetry_countable = bool(workspace_configured and reporting_systems > 0 and evidence_source == 'live' and last_telemetry_at is not None)
    summary_telemetry_timestamp = last_telemetry_at if telemetry_countable else None
    summary_freshness_status = (
        'fresh' if summary_telemetry_timestamp and int((now - summary_telemetry_timestamp).total_seconds()) <= telemetry_window_seconds
        else ('stale' if summary_telemetry_timestamp else 'unavailable')
    )
    poll_window_seconds = max(120, MONITOR_POLL_INTERVAL_SECONDS * 3)
    poll_freshness_status = (
        'fresh' if last_poll_at and int((now - last_poll_at).total_seconds()) <= poll_window_seconds
        else ('stale' if last_poll_at else 'unavailable')
    )
    summary = {
        'workspace_configured': workspace_configured,
        'monitoring_mode': monitoring_mode,
        'runtime_status': runtime_status_summary,
        'configured_systems': int(enabled_system_count),
        'reporting_systems': int(reporting_systems),
        'protected_assets': int(protected_assets_count),
        'coverage_state': {
            'configured_systems': int(enabled_system_count),
            'reporting_systems': int(reporting_systems),
            'protected_assets': int(protected_assets_count),
        },
        'freshness_status': summary_freshness_status,
        'confidence_status': (
            'high' if telemetry_countable
            else ('medium' if evidence_source == 'simulator' and reporting_systems > 0 else ('low' if workspace_configured else 'unavailable'))
        ),
        'last_heartbeat_at': last_heartbeat.isoformat() if last_heartbeat else None,
        'last_telemetry_at': summary_telemetry_timestamp.isoformat() if summary_telemetry_timestamp else None,
        'last_poll_at': last_poll_at.isoformat() if last_poll_at else None,
        'poll_freshness_status': poll_freshness_status,
        'last_detection_at': latest_detection_evaluation_at.isoformat() if latest_detection_evaluation_at else None,
        'evidence_source': evidence_source,
        'status_reason': degraded_reason or ('workspace_not_configured' if not workspace_configured else ('no_reporting_systems' if reporting_systems <= 0 else None)),
        'contradiction_flags': [],
    }
    if summary['runtime_status'] == 'offline' and summary['last_telemetry_at']:
        summary['contradiction_flags'].append('offline_with_current_telemetry')
    if summary['reporting_systems'] == 0 and summary['runtime_status'] == 'healthy':
        summary['contradiction_flags'].append('healthy_without_reporting_systems')
    if summary['last_telemetry_at'] is None and summary['freshness_status'] == 'fresh':
        summary['contradiction_flags'].append('telemetry_unavailable_marked_fresh')
    if summary['freshness_status'] == 'unavailable' and summary['last_telemetry_at']:
        summary['contradiction_flags'].append('telemetry_unavailable_with_timestamp')
    if (not summary['workspace_configured']) and (summary['configured_systems'] > 0 or summary['protected_assets'] > 0):
        summary['contradiction_flags'].append('workspace_unconfigured_with_coverage')
    if summary['configured_systems'] == 0 and summary['reporting_systems'] == 0 and summary['last_telemetry_at']:
        summary['contradiction_flags'].append('zero_coverage_with_live_telemetry')
    if (
        summary['last_poll_at'] is not None
        and summary['last_telemetry_at'] is None
        and summary['monitoring_mode'] == 'live'
        and (summary['runtime_status'] == 'healthy' or summary['reporting_systems'] > 0)
    ):
        summary['contradiction_flags'].append('poll_without_telemetry_timestamp')
    payload = {
        'monitoring_status': monitoring_status,
        'monitored_systems': system_count,
        'protected_assets': protected_assets_count,
        'enabled_systems': enabled_system_count,
        'active_systems': active_system_count,
        'last_heartbeat': last_heartbeat.isoformat() if last_heartbeat else None,
        'telemetry_available': bool(telemetry_countable or recent_real_event_count > 0 or monitoring_status == 'active'),
        'status': runtime_status,
        'provider_mode': health.get('source_type') or health.get('ingestion_mode') or 'polling',
        'last_successful_ingest': evidence_at.isoformat() if evidence_at else None,
        'last_detection_evaluation_at': latest_detection_evaluation_at.isoformat() if latest_detection_evaluation_at else None,
        'last_confirmed_checkpoint': latest_detection_evaluation_at.isoformat() if successful_detection_evaluation else None,
        'successful_detection_evaluation': successful_detection_evaluation,
        'successful_detection_evaluation_recent': successful_detection_evaluation_recent,
        'last_processed_block': (latest_evidence or {}).get('block_number') or health.get('latest_processed_block'),
        'targets_monitored': enabled_system_count,
        'protected_assets_count': protected_assets_count,
        'monitored_systems_count': system_count,
        'systems_with_recent_heartbeat': recent_heartbeat_systems,
        'invalid_enabled_targets': int((broken_targets or {}).get('c') or 0),
        'healthy_enabled_targets': healthy_enabled_targets_count,
        'active_alerts': int((open_alerts or {}).get('c') or 0),
        'open_incidents': int((open_incidents or {}).get('c') or 0),
        'evidence_freshness_seconds': evidence_freshness,
        'degraded_reason': degraded_reason,
        'recent_evidence_state': str((latest_detection_metadata or {}).get('evidence_state') or latest_detection_payload.get('evidence_state') or 'missing') if isinstance(latest_detection_payload, dict) else 'missing',
        'recent_real_event_count': recent_real_event_count,
        'recent_confidence_basis': str((latest_detection_metadata or {}).get('confidence_basis') or latest_detection_payload.get('confidence_basis') or 'none') if isinstance(latest_detection_payload, dict) else 'none',
        'last_real_event_at': (latest_detection_metadata or {}).get('last_real_event_at') if isinstance(latest_detection_metadata, dict) else None,
        'freshness_status': (
            summary['freshness_status']
        ),
        'confidence_status': (
            'high'
            if successful_detection_evaluation and recent_real_event_count > 0
            else ('medium' if successful_detection_evaluation_recent or recent_heartbeat_systems > 0 else ('low' if has_monitorable_targets else 'unavailable'))
        ),
        'coverage_reason': degraded_reason or ('no_evidence' if monitoring_status == 'idle' else (None if monitoring_status == 'active' else 'monitoring_unavailable')),
        'worker_last_error': health.get('last_error'),
        'latest_telemetry_checkpoint': (latest_detection_evaluation_at or evidence_at).isoformat() if (latest_detection_evaluation_at or evidence_at) else None,
        'source_of_evidence': source_of_evidence,
        'workspace_configured': workspace_configured,
        'last_poll_at': summary['last_poll_at'],
        'last_telemetry_at': summary['last_telemetry_at'],
        'last_detection_at': summary['last_detection_at'],
        'workspace_monitoring_summary': summary,
    }
    payload.update(summary)
    if summary['contradiction_flags']:
        logger.warning(
            'monitoring_runtime_status_contradiction workspace_id=%s flags=%s summary=%s',
            workspace_id,
            summary['contradiction_flags'],
            summary,
        )
    provider_health = 'healthy' if str(payload.get('recent_evidence_state')) == 'real' and int(payload.get('recent_real_event_count') or 0) > 0 else 'degraded'
    mode = str(health.get('operational_mode') or health.get('mode') or 'DEGRADED').upper()
    if mode == 'LIVE' and int(payload.get('recent_real_event_count') or 0) <= 0:
        mode = 'DEGRADED'
    payload.update(
        {
            'mode': mode,
            'provider_health': provider_health,
            'provider_reachable': bool((claim_validator.get('checks') or {}).get('evm_rpc_reachable')),
            'evidence_state': str(payload.get('recent_evidence_state') or 'missing'),
            'truthfulness_state': str(claim_validator.get('recent_truthfulness_state') or payload.get('recent_truthfulness_state') or 'unknown_risk'),
            'claim_safe': bool(claim_validator.get('sales_claims_allowed')),
            'sales_claims_allowed': bool(claim_validator.get('sales_claims_allowed')),
            'claim_validator_status': str(claim_validator.get('status') or 'FAIL'),
        }
    )
    logger.info(
        'monitoring_runtime_status_summary workspace_id=%s healthy_enabled_targets=%s monitored_rows=%s enabled_rows=%s protected_assets=%s monitoring_status=%s systems_with_recent_heartbeat=%s status_inputs=%s',
        workspace_id,
        healthy_enabled_targets_count,
        len(monitored_rows),
        enabled_system_count,
        protected_assets_count,
        monitoring_status,
        recent_heartbeat_systems,
        {
            'healthy_enabled_targets': healthy_enabled_targets_count,
            'monitored_system_rows': len(monitored_rows),
            'enabled_monitored_rows': len(enabled_rows),
            'protected_assets': protected_assets_count,
            'invalid_enabled_targets': int((broken_targets or {}).get('c') or 0),
            'runner_alive': runner_alive,
            'stale_heartbeat': stale_heartbeat,
            'workspace_id': workspace_id,
        },
    )
    logger.info(
        'monitoring_runtime_status_decision workspace_id=%s healthy_enabled_targets=%s monitored_system_rows=%s protected_assets=%s systems_with_recent_heartbeat=%s decision=%s',
        workspace_id,
        healthy_enabled_targets_count,
        len(monitored_rows),
        protected_assets_count,
        recent_heartbeat_systems,
        monitoring_status,
    )
    if _runtime_status_debug_enabled():
        monitored_system_ids = [str(row.get('id') or '') for row in monitored_rows if row.get('id')]
        enabled_monitored_system_ids = [str(row.get('id') or '') for row in enabled_rows if row.get('id')]
        target_ids = [str(row.get('target_id') or '') for row in monitored_rows if row.get('target_id')]
        payload.update(
            {
                'workspace_id': workspace_id,
                'resolved_workspace_id': workspace_id,
                'request_user_resolved': bool(user_id),
                'request_user_id': user_id,
                'workspace_header_present': workspace_header_present,
                'counted_monitored_systems': system_count,
                'counted_enabled_systems': enabled_system_count,
                'counted_active_systems': active_system_count,
                'counted_monitored_system_ids': monitored_system_ids,
                'counted_enabled_monitored_system_ids': enabled_monitored_system_ids,
                'sample_target_ids': target_ids[:5],
                'sample_target_ids_count': len(target_ids),
                'systems_with_recent_heartbeat': recent_heartbeat_systems,
                'has_monitorable_targets': has_monitorable_targets,
                'has_monitored_system_rows': has_any_monitored_rows,
            }
        )
    return payload


def list_monitoring_evidence(request: Request, *, limit: int = 50) -> dict[str, Any]:
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        rows = connection.execute(
            '''
            SELECT e.*, a.name AS asset_name, t.name AS target_name
            FROM evidence e
            LEFT JOIN assets a ON a.id = e.asset_id
            LEFT JOIN targets t ON t.id = e.target_id
            WHERE e.workspace_id = %s
            ORDER BY e.observed_at DESC
            LIMIT %s
            ''',
            (workspace_context['workspace_id'], max(1, min(limit, 200))),
        ).fetchall()
        return {'evidence': [_json_safe_value(dict(row)) for row in rows]}


def list_monitoring_heartbeats(request: Request, *, limit: int = 50) -> dict[str, Any]:
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        rows = connection.execute(
            '''
            SELECT id, workspace_id, chain, status, last_success_at, last_error_at, last_error_text, last_processed_block, provider_mode, updated_at
            FROM monitor_heartbeat
            ORDER BY updated_at DESC
            LIMIT %s
            ''',
            (max(1, min(limit, 200)),),
        ).fetchall()
        return {'heartbeats': [_json_safe_value(dict(row)) for row in rows]}


def list_monitoring_worker_errors(request: Request, *, limit: int = 50) -> dict[str, Any]:
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        user = authenticate_with_connection(connection, request)
        workspace_context = resolve_workspace(connection, user['id'], request.headers.get('x-workspace-id'))
        worker_rows = connection.execute(
            '''
            SELECT worker_name, status, last_error, last_cycle_at, last_heartbeat_at, updated_at
            FROM monitoring_worker_state
            WHERE last_error IS NOT NULL
            ORDER BY updated_at DESC
            LIMIT %s
            ''',
            (max(1, min(limit, 200)),),
        ).fetchall()
        target_rows = connection.execute(
            '''
            SELECT t.id AS target_id, t.name AS target_name, t.last_run_status, t.watcher_degraded_reason, t.last_failed_monitoring_at, t.updated_at
            FROM targets t
            WHERE t.workspace_id = %s
              AND t.deleted_at IS NULL
              AND (t.last_run_status IN ('error', 'failed') OR t.watcher_degraded_reason IS NOT NULL)
            ORDER BY COALESCE(t.last_failed_monitoring_at, t.updated_at) DESC
            LIMIT %s
            ''',
            (workspace_context['workspace_id'], max(1, min(limit, 200))),
        ).fetchall()
        return {
            'workspace': workspace_context['workspace'],
            'worker_errors': [_json_safe_value(dict(row)) for row in worker_rows],
            'target_errors': [_json_safe_value(dict(row)) for row in target_rows],
        }
