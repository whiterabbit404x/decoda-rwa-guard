from __future__ import annotations

from services.api.app import activity_providers


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
    assert result.reason_code == 'NO_PROVIDER_EVIDENCE'


def test_monitoring_demo_scenario_is_ignored_in_hybrid(monkeypatch):
    monkeypatch.setenv('MONITORING_INGESTION_MODE', 'hybrid')
    monkeypatch.setenv('LIVE_MONITORING_ENABLED', 'true')
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setattr(activity_providers, 'fetch_evm_activity', lambda *_args, **_kwargs: [])

    result = activity_providers.fetch_target_activity_result(_wallet_target(), None)

    assert result.mode == 'hybrid'
    assert result.synthetic is False
    assert result.status == 'no_evidence'
    assert result.evidence_state == 'NO_EVIDENCE'


def test_degraded_mode_stays_degraded_not_demo(monkeypatch):
    monkeypatch.setenv('MONITORING_INGESTION_MODE', 'degraded')
    monkeypatch.setenv('LIVE_MONITORING_ENABLED', 'false')
    monkeypatch.delenv('EVM_RPC_URL', raising=False)

    result = activity_providers.fetch_target_activity_result(_wallet_target(), None)

    assert result.mode == 'degraded'
    assert result.synthetic is False
    assert result.status == 'degraded'
    assert result.evidence_state == 'DEGRADED_EVIDENCE'
