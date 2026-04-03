from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
import os

from services.api.app.evm_activity_provider import fetch_evm_activity

MONITORING_DEMO_SCENARIOS = {
    'safe',
    'low_risk',
    'medium_risk',
    'high_risk',
    'flash_loan_like',
    'admin_abuse_like',
    'risky_approval_like',
}

SCENARIO_EXPECTED_RISK = {
    'safe': 'low',
    'low_risk': 'low',
    'medium_risk': 'medium',
    'high_risk': 'high',
    'flash_loan_like': 'high',
    'admin_abuse_like': 'high',
    'risky_approval_like': 'medium',
}

logger = logging.getLogger(__name__)


@dataclass
class ActivityEvent:
    event_id: str
    kind: str
    observed_at: datetime
    ingestion_source: str
    cursor: str
    payload: dict[str, Any]




def monitoring_ingestion_mode() -> str:
    mode = str(os.getenv('MONITORING_INGESTION_MODE', 'hybrid')).strip().lower()
    return mode if mode in {'demo', 'live', 'hybrid'} else 'hybrid'


def live_monitoring_enabled() -> bool:
    return str(os.getenv('LIVE_MONITORING_ENABLED', 'true')).strip().lower() in {'1', 'true', 'yes', 'on'}


def live_monitoring_requirements() -> dict[str, bool]:
    return {
        'evm_rpc_url': bool((os.getenv('EVM_RPC_URL') or '').strip()),
    }


def monitoring_ingestion_runtime() -> dict[str, Any]:
    mode = monitoring_ingestion_mode()
    req = live_monitoring_requirements()
    live_enabled = live_monitoring_enabled()
    ws_url = bool((os.getenv('EVM_WS_URL') or '').strip())
    if mode == 'demo':
        return {'mode': mode, 'source': 'demo', 'degraded': False, 'reason': None}
    if not live_enabled:
        return {'mode': mode, 'source': 'degraded', 'degraded': True, 'reason': 'LIVE_MONITORING_ENABLED=false'}
    if not req['evm_rpc_url']:
        return {'mode': mode, 'source': 'degraded', 'degraded': True, 'reason': 'EVM_RPC_URL missing'}
    source = 'websocket' if ws_url else 'polling'
    return {'mode': mode, 'source': source, 'degraded': False, 'reason': None}


def validate_monitoring_config_or_raise() -> None:
    runtime = monitoring_ingestion_runtime()
    if runtime['mode'] == 'live' and runtime['degraded']:
        raise RuntimeError(f"Live monitoring mode is misconfigured: {runtime['reason']}")
def monitoring_scenario(target: dict[str, Any]) -> str | None:
    value = str(
        target.get('monitoring_scenario')
        or target.get('monitoring_demo_scenario')
        or target.get('monitoring_profile')
        or ''
    ).strip().lower()
    if value in MONITORING_DEMO_SCENARIOS:
        return value
    return None


def monitoring_demo_scenario(target: dict[str, Any]) -> str | None:
    # Backwards-compatible alias while `monitoring_scenario` is the canonical API field.
    return monitoring_scenario(target)


def _seed(target_id: str, slot: str) -> int:
    digest = hashlib.sha256(f'{target_id}:{slot}'.encode('utf-8')).hexdigest()
    return int(digest[:8], 16)


def _build_event(target: dict[str, Any], *, kind: str, observed_at: datetime, payload: dict[str, Any]) -> ActivityEvent:
    slot = observed_at.replace(second=0, microsecond=0).isoformat()
    event_id = hashlib.sha256(f"{target['id']}:{kind}:{slot}".encode('utf-8')).hexdigest()[:20]
    return ActivityEvent(
        event_id=event_id,
        kind=kind,
        observed_at=observed_at,
        ingestion_source='demo',
        cursor=slot,
        payload=payload,
    )


def _wallet_payload_for_scenario(target: dict[str, Any], scenario: str) -> dict[str, Any]:
    base = {
        'wallet': target.get('wallet_address') or '0x0000000000000000000000000000000000000000',
        'actor': target.get('name') or 'wallet-monitor',
        'action_type': 'transfer',
        'protocol': target.get('chain_network') or 'ethereum',
        'asset': target.get('asset_type') or 'USDC',
        'call_sequence': ['approve', 'swap', 'transfer'],
        'actor_role': target.get('target_type') or 'wallet',
        'expected_actor_roles': ['wallet', 'treasury'],
        'metadata': {
            'monitoring_demo_scenario': scenario,
            'expected_risk_class': SCENARIO_EXPECTED_RISK.get(scenario, 'low'),
            'deterministic_demo': True,
        },
    }
    scenario_map = {
        'safe': {
            'amount': 25000.0,
            'burst_actions_last_5m': 1,
            'counterparty_reputation': 92,
            'flags': {'flash_loan_pattern': False, 'contains_flash_loan': False, 'rapid_drain_indicator': False, 'untrusted_contract': False, 'new_counterparty': False},
        },
        'low_risk': {
            'amount': 70000.0,
            'burst_actions_last_5m': 2,
            'counterparty_reputation': 74,
            'flags': {'flash_loan_pattern': False, 'contains_flash_loan': False, 'rapid_drain_indicator': False, 'untrusted_contract': False, 'new_counterparty': True},
        },
        'medium_risk': {
            'amount': 240000.0,
            'burst_actions_last_5m': 8,
            'counterparty_reputation': 39,
            'call_sequence': ['approve', 'borrow', 'swap', 'bridge', 'transfer'],
            'flags': {'flash_loan_pattern': False, 'contains_flash_loan': False, 'risky_multistep_sequence': True, 'rapid_drain_indicator': False, 'untrusted_contract': True, 'new_counterparty': True},
        },
        'high_risk': {
            'amount': 900000.0,
            'burst_actions_last_5m': 14,
            'counterparty_reputation': 12,
            'call_sequence': ['approve', 'flashLoan', 'swap', 'drain'],
            'flags': {'flash_loan_pattern': True, 'contains_flash_loan': True, 'rapid_drain_indicator': True, 'untrusted_contract': True, 'new_counterparty': True},
        },
        'flash_loan_like': {
            'amount': 1250000.0,
            'burst_actions_last_5m': 18,
            'counterparty_reputation': 8,
            'call_sequence': ['approve', 'flashLoan', 'swap', 'swap', 'bridge', 'drain'],
            'flags': {'flash_loan_pattern': True, 'contains_flash_loan': True, 'rapid_drain_indicator': True, 'untrusted_contract': True, 'new_counterparty': True},
        },
        'admin_abuse_like': {
            'amount': 420000.0,
            'burst_actions_last_5m': 10,
            'counterparty_reputation': 24,
            'actor_role': 'operator',
            'expected_actor_roles': ['wallet'],
            'call_sequence': ['grantRole', 'setAdmin', 'upgradeTo', 'transfer'],
            'flags': {'unexpected_admin_call': True, 'actor_role_mismatch': True, 'privileged_action_sequence': True, 'flash_loan_pattern': False, 'contains_flash_loan': False, 'rapid_drain_indicator': False, 'untrusted_contract': True},
        },
        'risky_approval_like': {
            'amount': 350000.0,
            'burst_actions_last_5m': 9,
            'counterparty_reputation': 30,
            'call_sequence': ['approve', 'increaseAllowance', 'transferFrom'],
            'flags': {'unlimited_approval': True, 'risky_approval_target': True, 'flash_loan_pattern': False, 'contains_flash_loan': False, 'rapid_drain_indicator': False, 'untrusted_contract': True},
        },
    }
    overrides = scenario_map.get(scenario, scenario_map['safe'])
    return {**base, **overrides}


def fetch_wallet_activity(target: dict[str, Any], since_ts: datetime | None) -> list[ActivityEvent]:
    now = datetime.now(timezone.utc)
    window_start = since_ts or (now - timedelta(minutes=15))
    if window_start > now - timedelta(minutes=3):
        return []
    scenario = monitoring_scenario(target)
    if scenario is not None:
        observed_at = now - timedelta(minutes=2)
        logger.info(
            'monitoring scenario applied target=%s target_type=wallet scenario=%s expected_risk=%s',
            target.get('id'),
            scenario,
            SCENARIO_EXPECTED_RISK.get(scenario, 'low'),
        )
        payload = _wallet_payload_for_scenario(target, scenario)
        event = _build_event(target, kind='transaction', observed_at=observed_at, payload=payload)
        logger.info(
            'generated deterministic monitoring event target=%s scenario=%s expected_risk=%s event_id=%s',
            target.get('id'),
            scenario,
            SCENARIO_EXPECTED_RISK.get(scenario, 'low'),
            event.event_id,
        )
        return [event]
    seed = _seed(str(target['id']), 'wallet')
    amount = float(50000 + (seed % 250000))
    burst = seed % 14
    flags = {'flash_loan_pattern': seed % 3 == 0, 'new_counterparty': seed % 2 == 0, 'untrusted_contract': seed % 5 == 0}
    observed_at = now - timedelta(minutes=2)
    return [
        _build_event(
            target,
            kind='transaction',
            observed_at=observed_at,
            payload={
                'wallet': target.get('wallet_address') or '0x0000000000000000000000000000000000000000',
                'actor': target.get('name') or 'wallet-monitor',
                'action_type': 'transfer',
                'protocol': target.get('chain_network') or 'ethereum',
                'amount': amount,
                'asset': target.get('asset_type') or 'USDC',
                'call_sequence': ['approve', 'swap', 'transfer'],
                'flags': flags,
                'counterparty_reputation': 20 + (seed % 70),
                'actor_role': target.get('target_type') or 'wallet',
                'expected_actor_roles': ['wallet', 'treasury'],
                'burst_actions_last_5m': burst,
            },
        )
    ]


def fetch_contract_activity(target: dict[str, Any], since_ts: datetime | None) -> list[ActivityEvent]:
    now = datetime.now(timezone.utc)
    window_start = since_ts or (now - timedelta(minutes=30))
    if window_start > now - timedelta(minutes=10):
        return []
    scenario = monitoring_scenario(target)
    if scenario is not None:
        observed_at = now - timedelta(minutes=5)
        risky = scenario in {'medium_risk', 'high_risk', 'flash_loan_like', 'admin_abuse_like', 'risky_approval_like'}
        logger.info(
            'monitoring scenario applied target=%s target_type=contract scenario=%s expected_risk=%s',
            target.get('id'),
            scenario,
            SCENARIO_EXPECTED_RISK.get(scenario, 'low'),
        )
        event = _build_event(
                target,
                kind='contract',
                observed_at=observed_at,
                payload={
                    'contract_name': target.get('name') or 'Monitored contract',
                    'address': target.get('contract_identifier') or target.get('wallet_address') or target.get('id'),
                    'verified_source': scenario in {'safe', 'low_risk'},
                    'audit_count': 2 if scenario in {'safe', 'low_risk'} else 0,
                    'created_days_ago': 140 if scenario in {'safe', 'low_risk'} else 9,
                    'admin_roles': ['owner', 'guardian'],
                    'calling_actor': target.get('name') or 'automation-worker',
                    'function_summaries': [
                        {'name': 'upgradeTo', 'summary': 'Upgrades implementation contract.', 'risk_flags': ['privileged-role-change'] if risky else []},
                        {'name': 'approve', 'summary': 'Token approval call path.', 'risk_flags': ['unlimited-approval'] if scenario == 'risky_approval_like' else []},
                    ],
                    'findings': ['Unexpected privileged workflow observed.'] if risky else ['No critical findings.'],
                    'flags': {
                        'privileged_role_change': scenario in {'admin_abuse_like', 'high_risk'},
                        'unexpected_admin_call': scenario == 'admin_abuse_like',
                        'unlimited_approval': scenario in {'risky_approval_like', 'high_risk'},
                    },
                    'metadata': {
                        'monitoring_demo_scenario': scenario,
                        'expected_risk_class': SCENARIO_EXPECTED_RISK.get(scenario, 'low'),
                        'deterministic_demo': True,
                    },
                },
            )
        logger.info(
            'generated deterministic monitoring event target=%s scenario=%s expected_risk=%s event_id=%s',
            target.get('id'),
            scenario,
            SCENARIO_EXPECTED_RISK.get(scenario, 'low'),
            event.event_id,
        )
        return [event]
    seed = _seed(str(target['id']), 'contract')
    risky = seed % 2 == 0
    observed_at = now - timedelta(minutes=5)
    return [
        _build_event(
            target,
            kind='contract',
            observed_at=observed_at,
            payload={
                'contract_name': target.get('name') or 'Monitored contract',
                'address': target.get('contract_identifier') or target.get('wallet_address') or target.get('id'),
                'verified_source': seed % 4 != 0,
                'audit_count': seed % 3,
                'created_days_ago': 20 + seed % 300,
                'admin_roles': ['owner', 'guardian'],
                'calling_actor': target.get('name') or 'automation-worker',
                'function_summaries': [
                    {'name': 'setOracle', 'summary': 'Changes oracle dependency.', 'risk_flags': ['privileged-role-change'] if risky else []},
                    {'name': 'pause', 'summary': 'Pauses protocol actions.', 'risk_flags': ['circuit-breaker']},
                ],
                'findings': ['Admin key rotated outside maintenance window.'] if risky else ['No critical findings.'],
                'flags': {'privileged_role_change': risky, 'unlimited_approval': seed % 7 == 0},
            },
        )
    ]


def fetch_market_activity(target: dict[str, Any], since_ts: datetime | None) -> list[ActivityEvent]:
    now = datetime.now(timezone.utc)
    window_start = since_ts or (now - timedelta(minutes=20))
    if window_start > now - timedelta(minutes=6):
        return []
    scenario = monitoring_scenario(target)
    if scenario is not None:
        observed_at = now - timedelta(minutes=3)
        baseline = 120000.0
        multiplier = {
            'safe': 1.08,
            'low_risk': 1.3,
            'medium_risk': 2.2,
            'risky_approval_like': 2.0,
            'admin_abuse_like': 2.6,
            'high_risk': 3.4,
            'flash_loan_like': 4.0,
        }.get(scenario, 1.1)
        current = baseline * multiplier
        logger.info(
            'monitoring scenario applied target=%s target_type=market scenario=%s expected_risk=%s',
            target.get('id'),
            scenario,
            SCENARIO_EXPECTED_RISK.get(scenario, 'low'),
        )
        event = _build_event(
                target,
                kind='market',
                observed_at=observed_at,
                payload={
                    'asset': target.get('asset_type') or target.get('name') or 'RWA-TOKEN',
                    'venue': target.get('chain_network') or 'ethereum',
                    'timeframe_minutes': 15,
                    'current_volume': current,
                    'baseline_volume': baseline,
                    'participant_diversity': 28 if scenario in {'safe', 'low_risk'} else 8,
                    'dominant_cluster_share': 0.38 if scenario in {'safe', 'low_risk'} else 0.86,
                    'order_flow_summary': {
                        'large_orders': 1 if scenario in {'safe', 'low_risk'} else 9,
                        'rapid_cancellations': 1 if scenario in {'safe', 'low_risk'} else 6,
                        'rapid_swings': 1 if scenario in {'safe', 'low_risk'} else 5,
                        'circular_trade_loops': 0 if scenario in {'safe', 'low_risk'} else 4,
                        'self_trade_markers': 0 if scenario in {'safe', 'low_risk'} else 3,
                    },
                    'candles': [
                        {'timestamp': (observed_at - timedelta(minutes=15)).isoformat(), 'open': 100, 'high': 104, 'low': 99, 'close': 102, 'volume': baseline},
                        {'timestamp': observed_at.isoformat(), 'open': 102, 'high': 121, 'low': 91, 'close': 117 if scenario in {'high_risk', 'flash_loan_like'} else 108, 'volume': current},
                    ],
                    'wallet_activity': [
                        {'cluster_id': 'cluster-a', 'trade_count': 9 if scenario in {'safe', 'low_risk'} else 29, 'net_volume': current * 0.52},
                        {'cluster_id': 'cluster-b', 'trade_count': 7 if scenario in {'safe', 'low_risk'} else 22, 'net_volume': current * 0.34},
                    ],
                    'metadata': {
                        'monitoring_demo_scenario': scenario,
                        'expected_risk_class': SCENARIO_EXPECTED_RISK.get(scenario, 'low'),
                        'deterministic_demo': True,
                    },
                },
            )
        logger.info(
            'generated deterministic monitoring event target=%s scenario=%s expected_risk=%s event_id=%s',
            target.get('id'),
            scenario,
            SCENARIO_EXPECTED_RISK.get(scenario, 'low'),
            event.event_id,
        )
        return [event]
    seed = _seed(str(target['id']), 'market')
    observed_at = now - timedelta(minutes=3)
    baseline = 120000.0 + (seed % 20000)
    current = baseline * (1.2 + (seed % 5) * 0.35)
    return [
        _build_event(
            target,
            kind='market',
            observed_at=observed_at,
            payload={
                'asset': target.get('asset_type') or target.get('name') or 'RWA-TOKEN',
                'venue': target.get('chain_network') or 'ethereum',
                'timeframe_minutes': 15,
                'current_volume': current,
                'baseline_volume': baseline,
                'participant_diversity': 8 + seed % 30,
                'dominant_cluster_share': min(0.92, 0.33 + (seed % 45) / 100),
                'order_flow_summary': {
                    'large_orders': seed % 7,
                    'rapid_cancellations': seed % 5,
                    'rapid_swings': seed % 4,
                    'circular_trade_loops': seed % 3,
                    'self_trade_markers': seed % 2,
                },
                'candles': [
                    {'timestamp': (observed_at - timedelta(minutes=15)).isoformat(), 'open': 100, 'high': 108, 'low': 99, 'close': 104, 'volume': baseline},
                    {'timestamp': observed_at.isoformat(), 'open': 104, 'high': 112, 'low': 102, 'close': 111, 'volume': current},
                ],
                'wallet_activity': [
                    {'cluster_id': 'cluster-a', 'trade_count': 14 + seed % 8, 'net_volume': current * 0.34},
                    {'cluster_id': 'cluster-b', 'trade_count': 9 + seed % 7, 'net_volume': current * 0.21},
                ],
            },
        )
    ]


def fetch_target_activity(target: dict[str, Any], since_ts: datetime | None) -> list[ActivityEvent]:
    target_type = str(target.get('target_type') or '').lower()
    runtime = monitoring_ingestion_runtime()
    mode = runtime['mode']
    can_use_live = (not runtime['degraded']) and target_type in {'wallet', 'contract'}
    if mode in {'live', 'hybrid'} and can_use_live:
        live_events = fetch_evm_activity(target, since_ts)
        if live_events:
            return live_events
        return []
    if mode == 'live' and runtime['degraded']:
        raise RuntimeError(str(runtime.get('reason') or 'live ingestion degraded'))
    if mode == 'hybrid' and target_type in {'wallet', 'contract'}:
        # In hybrid mode, do not silently substitute deterministic demo payloads for
        # workspace-bound live wallet/contract targets.
        return []
    if target_type == 'wallet':
        return fetch_wallet_activity(target, since_ts)
    if target_type == 'contract':
        return fetch_contract_activity(target, since_ts)
    return fetch_market_activity(target, since_ts)
