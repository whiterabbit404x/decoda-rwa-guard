from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

from services.api.app.activity_providers import ActivityEvent, ActivityProviderResult

DEMO_SCENARIOS = {
    'safe',
    'low_risk',
    'medium_risk',
    'high_risk',
    'flash_loan_like',
    'admin_abuse_like',
    'risky_approval_like',
}


def _seed(target_id: str, slot: str) -> int:
    digest = hashlib.sha256(f'{target_id}:{slot}'.encode('utf-8')).hexdigest()
    return int(digest[:8], 16)


def _build_demo_event(target: dict[str, Any], *, kind: str, observed_at: datetime, payload: dict[str, Any], provider_name: str) -> ActivityEvent:
    slot = observed_at.replace(second=0, microsecond=0).isoformat()
    event_id = hashlib.sha256(f"{target['id']}:{kind}:{slot}".encode('utf-8')).hexdigest()[:20]
    metadata = payload.get('metadata') if isinstance(payload.get('metadata'), dict) else {}
    payload['metadata'] = {
        **metadata,
        'evidence_origin': 'demo',
        'provider_name': provider_name,
        'production_claim_eligible': False,
    }
    return ActivityEvent(
        event_id=event_id,
        kind=kind,
        observed_at=observed_at,
        ingestion_source='demo',
        cursor=slot,
        payload=payload,
    )


def _events_for_target(target: dict[str, Any], since_ts: datetime | None) -> tuple[list[ActivityEvent], str]:
    target_type = str(target.get('target_type') or '').lower()
    now = datetime.now(timezone.utc)
    seed = _seed(str(target['id']), target_type or 'target')
    observed_at = now - timedelta(minutes=2)
    if since_ts and since_ts > observed_at:
        return [], 'demo_provider'
    if target_type == 'contract':
        return [
            _build_demo_event(
                target,
                kind='contract',
                observed_at=observed_at,
                provider_name='demo_contract_provider',
                payload={'event_type': 'approval_changed', 'spender': '0x' + 'a' * 40, 'kind_hint': 'erc20_approval', 'amount': float(1000 + (seed % 10000))},
            )
        ], 'demo_contract_provider'
    if target_type == 'market':
        return [
            _build_demo_event(
                target,
                kind='market',
                observed_at=observed_at,
                provider_name='demo_market_provider',
                payload={'event_type': 'market_snapshot', 'venue': 'demo_venue', 'current_volume': float(100000 + (seed % 100000)), 'baseline_volume': 100000.0},
            )
        ], 'demo_market_provider'
    return [
        _build_demo_event(
            target,
            kind='transaction',
            observed_at=observed_at,
            provider_name='demo_wallet_provider',
            payload={
                'event_type': 'transfer',
                'from': target.get('wallet_address') or '0x' + '1' * 40,
                'to': '0x' + 'a' * 40,
                'amount': float(50000 + (seed % 100000)),
            },
        )
    ], 'demo_wallet_provider'


def fetch_demo_target_activity_result(target: dict[str, Any], since_ts: datetime | None) -> ActivityProviderResult:
    events, provider_name = _events_for_target(target, since_ts)
    return ActivityProviderResult(
        mode='demo',
        status='demo',
        evidence_state='DEMO_EVIDENCE',
        truthfulness_state='NOT_CLAIM_SAFE',
        synthetic=True,
        provider_name=provider_name,
        provider_kind='demo',
        evidence_present=bool(events),
        recent_real_event_count=0,
        last_real_event_at=None,
        events=events,
        latest_block=None,
        checkpoint=events[-1].cursor if events else None,
        checkpoint_age_seconds=None,
        degraded_reason=None,
        error_code=None,
        source_type='demo',
        reason_code='DEMO_ONLY_MODE',
        claim_safe=False,
        detection_outcome='DEMO_ONLY',
    )
