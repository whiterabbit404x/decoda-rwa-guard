from __future__ import annotations

import pytest

from services.api.app import activity_providers
from services.api.app.monitoring_mode import MonitoringModeError, assert_no_synthetic_path, is_degraded_mode


def _wallet_target() -> dict[str, str]:
    return {
        'id': 'target-1',
        'target_type': 'wallet',
        'chain_network': 'ethereum',
        'wallet_address': '0x' + '1' * 40,
    }


def test_live_mode_returns_no_evidence_not_live(monkeypatch):
    monkeypatch.setenv('MONITORING_INGESTION_MODE', 'live')
    monkeypatch.setenv('LIVE_MONITORING_ENABLED', 'true')
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setattr(activity_providers, 'fetch_evm_activity', lambda *_args, **_kwargs: [])
    result = activity_providers.fetch_target_activity_result(_wallet_target(), None)
    assert result.status == 'no_evidence'
    assert result.evidence_present is False
    assert result.synthetic is False
    assert result.claim_safe is False


def test_live_mode_provider_failure_is_failed(monkeypatch):
    monkeypatch.setenv('MONITORING_INGESTION_MODE', 'live')
    monkeypatch.setenv('LIVE_MONITORING_ENABLED', 'true')
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')

    def _boom(*_args, **_kwargs):
        raise RuntimeError('rpc down')

    monkeypatch.setattr(activity_providers, 'fetch_evm_activity', _boom)
    result = activity_providers.fetch_target_activity_result(_wallet_target(), None)
    assert result.status == 'failed'
    assert result.reason_code == 'PROVIDER_FAILED'
    assert result.error_code == 'RuntimeError'
    assert result.claim_safe is False


def test_hybrid_blocks_demo_synthetic_path(monkeypatch):
    monkeypatch.setenv('MONITORING_INGESTION_MODE', 'hybrid')
    with pytest.raises(MonitoringModeError):
        assert_no_synthetic_path('hybrid', attempted=True, context='test')


def test_degraded_mode_helper():
    assert is_degraded_mode('degraded') is True
