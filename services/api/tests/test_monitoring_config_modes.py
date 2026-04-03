from __future__ import annotations

import pytest

from services.api.app.activity_providers import monitoring_ingestion_runtime, validate_monitoring_config_or_raise
from services.api.app.monitoring_mode import assert_no_demo_fallback


def test_live_mode_without_rpc_fails_fast(monkeypatch):
    monkeypatch.setenv('MONITORING_INGESTION_MODE', 'live')
    monkeypatch.setenv('LIVE_MONITORING_ENABLED', 'true')
    monkeypatch.delenv('EVM_RPC_URL', raising=False)
    with pytest.raises(RuntimeError):
        validate_monitoring_config_or_raise()


def test_demo_mode_still_supported(monkeypatch):
    monkeypatch.setenv('MONITORING_INGESTION_MODE', 'demo')
    monkeypatch.delenv('EVM_RPC_URL', raising=False)
    runtime = monitoring_ingestion_runtime()
    assert runtime['source'] == 'demo'
    assert runtime['degraded'] is False


def test_hybrid_mode_labeled(monkeypatch):
    monkeypatch.setenv('MONITORING_INGESTION_MODE', 'hybrid')
    monkeypatch.setenv('LIVE_MONITORING_ENABLED', 'true')
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    runtime = monitoring_ingestion_runtime()
    assert runtime['source'] == 'polling'


def test_live_mode_blocks_demo_fallback_guard():
    with pytest.raises(RuntimeError):
        assert_no_demo_fallback('live', attempted=True, context='unit-test')
