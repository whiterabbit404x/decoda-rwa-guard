from __future__ import annotations

import pytest

from services.api.app import activity_providers
from services.api.app.monitoring_mode import MonitoringModeError


def _wallet_target() -> dict[str, str]:
    return {
        'id': 'target-1',
        'target_type': 'wallet',
        'chain_network': 'ethereum',
        'wallet_address': '0x' + '1' * 40,
        'monitoring_demo_scenario': 'flash_loan_like',
    }


def test_monitoring_demo_scenario_is_ignored_in_live(monkeypatch):
    monkeypatch.setenv('MONITORING_INGESTION_MODE', 'live')
    monkeypatch.setenv('LIVE_MONITORING_ENABLED', 'true')
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setattr(activity_providers, 'fetch_evm_activity', lambda *_args, **_kwargs: [])

    result = activity_providers.fetch_target_activity_result(_wallet_target(), None)

    assert result.mode == 'live'
    assert result.synthetic is False
    assert result.status == 'no_evidence'


def test_monitoring_demo_scenario_is_ignored_in_hybrid(monkeypatch):
    monkeypatch.setenv('MONITORING_INGESTION_MODE', 'hybrid')
    monkeypatch.setenv('LIVE_MONITORING_ENABLED', 'true')
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setattr(activity_providers, 'fetch_evm_activity', lambda *_args, **_kwargs: [])

    result = activity_providers.fetch_target_activity_result(_wallet_target(), None)

    assert result.mode == 'hybrid'
    assert result.synthetic is False
    assert result.status == 'no_evidence'


def test_demo_mode_blocked_without_explicit_gate(monkeypatch):
    monkeypatch.setenv('MONITORING_INGESTION_MODE', 'demo')
    monkeypatch.setenv('ENV', 'production')
    monkeypatch.setenv('ALLOW_DEMO_MODE', 'false')
    with pytest.raises(MonitoringModeError):
        activity_providers.fetch_target_activity_result(_wallet_target(), None)
