from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.api.app import pilot
from services.api.app.activity_providers import fetch_contract_activity, fetch_market_activity, fetch_wallet_activity
from services.api.app import monitoring_runner
from services.api.app.monitoring_runner import _fallback_response, _normalize_event


def test_target_validation_persists_monitoring_fields() -> None:
    payload = {
        'name': 'Treasury Wallet',
        'target_type': 'wallet',
        'chain_network': 'ethereum',
        'wallet_address': '0x1111111111111111111111111111111111111111',
        'monitoring_enabled': True,
        'monitoring_mode': 'poll',
        'monitoring_interval_seconds': 120,
        'severity_threshold': 'high',
        'auto_create_alerts': True,
        'auto_create_incidents': True,
        'notification_channels': ['dashboard'],
    }
    validated = pilot._validate_target_payload(payload)
    assert validated['monitoring_enabled'] is True
    assert validated['monitoring_interval_seconds'] == 120
    assert validated['severity_threshold'] == 'high'
    assert validated['auto_create_incidents'] is True


def test_activity_providers_are_deterministic() -> None:
    target = {
        'id': 'target-1',
        'name': 'Target 1',
        'target_type': 'wallet',
        'wallet_address': '0x1111111111111111111111111111111111111111',
        'chain_network': 'ethereum',
        'asset_type': 'USDC',
    }
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    first = fetch_wallet_activity(target, since)
    second = fetch_wallet_activity(target, since)
    assert len(first) == len(second) == 1
    assert first[0].event_id == second[0].event_id

    contract_target = {**target, 'target_type': 'contract'}
    assert fetch_contract_activity(contract_target, since)[0].payload['contract_name']
    market_target = {**target, 'target_type': 'oracle'}
    assert fetch_market_activity(market_target, since)[0].payload['asset']


def test_monitoring_normalization_and_fallback_shape() -> None:
    target = {
        'id': '8d8eb228-42ba-4f11-a5d6-3c90166a4d70',
        'workspace_id': '5f230104-1481-4f7f-b176-4022fba95c4f',
        'name': 'Treasury Contract',
        'target_type': 'contract',
        'chain_network': 'ethereum',
        'contract_identifier': '0x2222222222222222222222222222222222222222',
        'severity_preference': 'medium',
        'severity_threshold': 'high',
        'auto_create_alerts': True,
        'auto_create_incidents': False,
    }
    event = fetch_contract_activity(target, datetime.now(timezone.utc) - timedelta(hours=1))[0]
    kind, payload = _normalize_event(target, event, 'run-id', {'name': 'Workspace'})
    assert kind == 'contract'
    assert payload['metadata']['monitoring_run_id'] == 'run-id'
    assert payload['metadata']['ingestion_source'] == 'demo'

    fallback = _fallback_response(
        'contract',
        payload,
        diagnostics={
            'fallback_reason': 'live_engine_exception',
            'fallback_exception_type': 'TimeoutError',
            'fallback_exception_message': 'timed out',
        },
    )
    assert fallback['analysis_type'] == 'contract'
    assert fallback['source'] == 'fallback'
    assert 'severity' in fallback
    assert fallback['metadata']['fallback_reason'] == 'live_engine_exception'
    assert fallback['metadata']['fallback_exception_type'] == 'TimeoutError'


def test_monitoring_threat_call_reuses_manual_proxy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        'wallet': '0x1111111111111111111111111111111111111111',
        'actor': 'Treasury Ops',
        'action_type': 'transfer',
        'protocol': 'erc20',
        'amount': 25000,
        'asset': 'USDC',
        'call_sequence': ['transfer'],
        'flags': {},
        'counterparty_reputation': 75,
        'actor_role': 'wallet',
        'expected_actor_roles': ['wallet'],
        'burst_actions_last_5m': 0,
        'metadata': {'event_id': 'evt-1'},
    }
    calls: list[tuple[str, dict[str, object]]] = []

    def _proxy(kind: str, body: dict[str, object]) -> dict[str, object]:
        calls.append((kind, body))
        return {
            'analysis_type': kind,
            'score': 18,
            'severity': 'low',
            'matched_patterns': [],
            'explanation': 'live',
            'recommended_action': 'allow',
            'reasons': [],
            'source': 'live',
            'degraded': False,
            'metadata': {'source': 'live'},
        }

    monkeypatch.setattr('services.api.app.main.proxy_threat', _proxy)
    response, diagnostics = monitoring_runner._threat_call('transaction', payload, target_id='target-1')
    assert response is not None
    assert response['source'] == 'live'
    assert diagnostics['live_invocation'] == 'proxy_threat'
    assert diagnostics['live_invocation_succeeded'] is True
    assert calls and calls[0][0] == 'transaction'
