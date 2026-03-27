from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any


@dataclass
class ActivityEvent:
    event_id: str
    kind: str
    observed_at: datetime
    ingestion_source: str
    cursor: str
    payload: dict[str, Any]


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


def fetch_wallet_activity(target: dict[str, Any], since_ts: datetime | None) -> list[ActivityEvent]:
    now = datetime.now(timezone.utc)
    window_start = since_ts or (now - timedelta(minutes=15))
    if window_start > now - timedelta(minutes=3):
        return []
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
    if target_type == 'wallet':
        return fetch_wallet_activity(target, since_ts)
    if target_type == 'contract':
        return fetch_contract_activity(target, since_ts)
    return fetch_market_activity(target, since_ts)
