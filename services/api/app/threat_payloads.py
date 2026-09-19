from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal

from services.api.app import telemetry_privacy as privacy

ThreatKind = Literal['contract', 'transaction', 'market']


def _to_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {'1', 'true', 'yes', 'on'}:
            return True
        if normalized in {'0', 'false', 'no', 'off'}:
            return False
    return default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(',') if item.strip()]
    return []


def _dict_bool(value: Any) -> dict[str, bool]:
    if not isinstance(value, dict):
        return {}
    return {str(key): _to_bool(flag, False) for key, flag in value.items()}


def _safe_metadata(
    payload: dict[str, Any],
    module_config: dict[str, Any],
    *,
    original_payload: dict[str, Any] | None = None,
    policy: privacy.PrivacyPolicy = privacy.DEFAULT_POLICY,
) -> dict[str, Any]:
    metadata = deepcopy(payload.get('metadata') if isinstance(payload.get('metadata'), dict) else {})
    metadata.update(
        {
            'target_id': payload.get('target_id'),
            'target_name': payload.get('target_name'),
            'target_type': payload.get('target_type'),
            'chain_network': payload.get('chain_network'),
            'severity_preference': payload.get('severity_preference', 'medium'),
            'tags': _string_list(payload.get('tags')),
            'owner_notes': payload.get('owner_notes'),
            'module_config': module_config,
        }
    )
    if original_payload is not None:
        # ``original_ui_request`` is the one place in this module that keeps a
        # caller-shaped body rather than named fields, and it is persisted into
        # analysis_runs. It therefore goes through the central sanitizer BEFORE it
        # is copied in — not when it is later read or rendered. Public-chain fields
        # (tx hash, addresses, amounts, block numbers) are preserved verbatim; the
        # accompanying ``privacy_processing`` block states what was removed so the
        # stored copy is never mistaken for the untouched original.
        result = privacy.sanitize_ingested_payload(
            original_payload, source_type=privacy.SOURCE_CUSTOMER_REQUEST, policy=policy,
        )
        metadata['original_ui_request'] = result.payload
        metadata['privacy_processing'] = privacy.privacy_processing_metadata(result)
    return metadata


def _is_modern_contract(payload: dict[str, Any]) -> bool:
    return 'contract_name' in payload and 'function_summaries' in payload


def _is_modern_transaction(payload: dict[str, Any]) -> bool:
    return 'wallet' in payload and 'call_sequence' in payload


def _is_modern_market(payload: dict[str, Any]) -> bool:
    return 'asset' in payload and 'order_flow_summary' in payload


def normalize_threat_payload(
    kind: ThreatKind,
    payload: dict[str, Any],
    *,
    include_original: bool = False,
    policy: privacy.PrivacyPolicy = privacy.DEFAULT_POLICY,
) -> tuple[dict[str, Any], bool]:
    source_payload = deepcopy(payload)
    module_config = deepcopy(payload.get('module_config') if isinstance(payload.get('module_config'), dict) else {})
    metadata = _safe_metadata(payload, module_config, original_payload=source_payload if include_original else None, policy=policy)

    if kind == 'contract':
        if _is_modern_contract(payload):
            normalized = deepcopy(payload)
            normalized['metadata'] = _safe_metadata(normalized, module_config, original_payload=source_payload if include_original else None, policy=policy)
            return normalized, False

        function_summaries_raw = payload.get('function_summaries') if isinstance(payload.get('function_summaries'), list) else []
        function_summaries: list[dict[str, Any]] = []
        for item in function_summaries_raw:
            if not isinstance(item, dict):
                continue
            function_summaries.append(
                {
                    'name': str(item.get('name') or 'unknown-function'),
                    'summary': str(item.get('summary') or 'No summary provided.'),
                    'risk_flags': _string_list(item.get('risk_flags')),
                }
            )

        normalized = {
            'contract_name': str(payload.get('target_name') or payload.get('contract_name') or 'Monitored contract'),
            'address': payload.get('address') or payload.get('contract_identifier') or payload.get('wallet_address') or payload.get('target_id'),
            'verified_source': _to_bool(payload.get('verified_source')),
            'audit_count': max(0, _to_int(payload.get('audit_count'), 0)),
            'created_days_ago': max(0, _to_int(payload.get('created_days_ago'), 0)),
            'admin_roles': _string_list(payload.get('admin_roles')),
            'calling_actor': payload.get('calling_actor') or payload.get('target_name'),
            'function_summaries': function_summaries,
            'findings': _string_list(payload.get('findings')),
            'flags': _dict_bool(payload.get('flags')),
            'metadata': metadata,
        }
        return normalized, True

    if kind == 'transaction':
        if _is_modern_transaction(payload):
            normalized = deepcopy(payload)
            normalized['metadata'] = _safe_metadata(normalized, module_config, original_payload=source_payload if include_original else None, policy=policy)
            return normalized, False

        normalized = {
            'wallet': str(payload.get('wallet') or payload.get('wallet_address') or payload.get('target_id') or '0x0000000000000000000000000000000000000000'),
            'actor': str(payload.get('actor') or payload.get('target_name') or 'unknown-actor'),
            'action_type': str(payload.get('action_type') or 'monitor'),
            'protocol': str(payload.get('protocol') or payload.get('target_name') or 'unknown-protocol'),
            'amount': max(0.0, _to_float(payload.get('amount'), 0.0)),
            'asset': payload.get('asset') or payload.get('asset_type') or 'UNKNOWN',
            'call_sequence': _string_list(payload.get('call_sequence')),
            'flags': _dict_bool(payload.get('flags')),
            'counterparty_reputation': min(100, max(0, _to_int(payload.get('counterparty_reputation'), 50))),
            'actor_role': payload.get('actor_role') or payload.get('target_type'),
            'expected_actor_roles': _string_list(payload.get('expected_actor_roles')),
            'burst_actions_last_5m': max(0, _to_int(payload.get('burst_actions_last_5m'), 0)),
            'metadata': metadata,
        }
        return normalized, True

    if _is_modern_market(payload):
        normalized = deepcopy(payload)
        normalized['metadata'] = _safe_metadata(normalized, module_config, original_payload=source_payload if include_original else None, policy=policy)
        return normalized, False

    order_flow = payload.get('order_flow_summary') if isinstance(payload.get('order_flow_summary'), dict) else {}
    normalized = {
        'asset': str(payload.get('asset') or payload.get('asset_type') or payload.get('target_name') or 'UNKNOWN'),
        'venue': str(payload.get('venue') or payload.get('chain_network') or 'unknown-venue'),
        'timeframe_minutes': max(1, _to_int(payload.get('timeframe_minutes'), 15)),
        'current_volume': max(0.0, _to_float(payload.get('current_volume'), 0.0)),
        'baseline_volume': max(0.0, _to_float(payload.get('baseline_volume'), 0.0)),
        'participant_diversity': max(0, _to_int(payload.get('participant_diversity'), 0)),
        'dominant_cluster_share': min(1.0, max(0.0, _to_float(payload.get('dominant_cluster_share'), 0.0))),
        'order_flow_summary': {
            'large_orders': max(0, _to_int(order_flow.get('large_orders'), 0)),
            'rapid_cancellations': max(0, _to_int(order_flow.get('rapid_cancellations'), 0)),
            'rapid_swings': max(0, _to_int(order_flow.get('rapid_swings'), 0)),
            'circular_trade_loops': max(0, _to_int(order_flow.get('circular_trade_loops'), 0)),
            'self_trade_markers': max(0, _to_int(order_flow.get('self_trade_markers'), 0)),
        },
        'candles': payload.get('candles') if isinstance(payload.get('candles'), list) else [],
        'wallet_activity': payload.get('wallet_activity') if isinstance(payload.get('wallet_activity'), list) else [],
        'metadata': metadata,
    }
    return normalized, True
