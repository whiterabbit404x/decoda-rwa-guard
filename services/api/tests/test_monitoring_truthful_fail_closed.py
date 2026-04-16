from __future__ import annotations

import pytest

from services.api.app import activity_providers
from services.api.app.monitoring_mode import MonitoringModeError, assert_no_synthetic_path, is_degraded_mode
from services.api.app import monitoring_runner


def _wallet_target() -> dict[str, str]:
    return {
        'id': 'target-1',
        'target_type': 'wallet',
        'chain_network': 'ethereum',
        'wallet_address': '0x' + '1' * 40,
    }


def test_live_mode_coverage_proof_without_target_events(monkeypatch):
    monkeypatch.setenv('MONITORING_INGESTION_MODE', 'live')
    monkeypatch.setenv('LIVE_MONITORING_ENABLED', 'true')
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setattr(activity_providers, 'fetch_evm_activity', lambda *_args, **_kwargs: [])
    result = activity_providers.fetch_target_activity_result(_wallet_target(), None)
    assert result.status == 'live'
    assert result.evidence_present is True
    assert result.recent_real_event_count == 0
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


def test_health_reflects_watcher_degraded_reason(monkeypatch):
    class _Result:
        def __init__(self, row):
            self._row = row

        def fetchone(self):
            return self._row

    class _Conn:
        def execute(self, query, params=None):
            if 'FROM monitoring_worker_state' in query:
                return _Result({'worker_name': 'w', 'running': True, 'status': 'running', 'last_cycle_at': None, 'last_cycle_targets_checked': 0, 'last_cycle_alerts_generated': 0})
            if 'FROM monitoring_watcher_state' in query:
                return _Result({'watcher_name': 'watcher', 'source_status': 'degraded', 'degraded': True, 'degraded_reason': 'ws_rpc_down', 'last_processed_block': 10, 'metrics': {}})
            if 'FROM targets' in query:
                return _Result({'latest_processed_block': 10, 'max_checkpoint_lag_blocks': 0, 'latest_checkpoint_at': None, 'degraded_targets': 0, 'active_targets': 1})
            if 'FROM monitoring_event_receipts' in query:
                return _Result({'event_count': 0})
            if 'FROM background_jobs' in query:
                return _Result({'queued': 0, 'running': 0, 'failed': 0})
            return _Result({'overdue_count': 0})

    class _Ctx:
        def __enter__(self):
            return _Conn()

        def __exit__(self, exc_type, exc, tb):
            return False

    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: True)
    monkeypatch.setattr(monitoring_runner, 'pg_connection', lambda: _Ctx())
    monkeypatch.setattr(monitoring_runner, 'ensure_pilot_schema', lambda _connection: None)
    monkeypatch.setattr(monitoring_runner, 'monitoring_ingestion_runtime', lambda: {'mode': 'live', 'source': 'websocket', 'degraded': False, 'reason': None})
    payload = monitoring_runner.get_monitoring_health()
    assert payload['degraded'] is True
    assert payload['degraded_reason'] == 'ws_rpc_down'
