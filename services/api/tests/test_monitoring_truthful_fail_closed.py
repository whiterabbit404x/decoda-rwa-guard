from __future__ import annotations

from datetime import datetime, timedelta, timezone
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


def test_health_marks_stale_heartbeat_and_live_confirmation(monkeypatch):
    stale = datetime.now(timezone.utc) - timedelta(seconds=monitoring_runner.WORKER_HEARTBEAT_TTL_SECONDS + 30)

    class _Result:
        def __init__(self, row):
            self._row = row

        def fetchone(self):
            return self._row

    class _Conn:
        def execute(self, query, params=None):
            if 'FROM monitoring_worker_state' in query:
                return _Result(
                    {
                        'worker_name': 'w',
                        'running': False,
                        'status': 'idle',
                        'last_cycle_at': stale.isoformat(),
                        'last_heartbeat_at': stale.isoformat(),
                        'last_cycle_targets_checked': 1,
                        'last_cycle_alerts_generated': 0,
                    }
                )
            if 'FROM monitoring_watcher_state' in query:
                return _Result({'watcher_name': 'watcher', 'source_status': 'polling', 'degraded': False, 'degraded_reason': None, 'last_processed_block': 10, 'metrics': {}})
            if 'FROM targets' in query:
                return _Result({'latest_processed_block': 10, 'max_checkpoint_lag_blocks': 0, 'latest_checkpoint_at': stale.isoformat(), 'degraded_targets': 0, 'active_targets': 1})
            if 'FROM monitoring_event_receipts' in query:
                return _Result({'event_count': 1})
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
    monkeypatch.setattr(monitoring_runner, 'monitoring_ingestion_runtime', lambda: {'mode': 'live', 'source': 'polling', 'degraded': False, 'reason': None})
    payload = monitoring_runner.get_monitoring_health()
    assert payload['heartbeat_stale'] is True
    assert payload['heartbeat_age_seconds'] > monitoring_runner.WORKER_HEARTBEAT_TTL_SECONDS
    assert payload['ingestion_live_confirmed'] is True


def test_runtime_summary_healthy_path_requires_live_coverage_proof_guards():
    source = open('services/api/app/monitoring_runner.py', encoding='utf-8').read()
    assert "and evidence_source == 'live'" in source
    assert 'and reporting_systems > 0' in source
    assert 'and coverage_fresh' in source
    assert "summary_freshness_status not in {'', 'unavailable'}" in source
    assert "summary_confidence_status not in {'', 'unavailable'}" in source


def test_worker_and_checkpoint_updates_are_workspace_scoped():
    source = open('services/api/app/monitoring_runner.py', encoding='utf-8').read()
    assert 'WHERE id = %s AND workspace_id = %s' in source
    assert 'WHERE id = %s::uuid\n                              AND workspace_id = %s::uuid' in source
    assert 'WHERE ms.id = %s::uuid\n                              AND ms.workspace_id = %s::uuid' in source
    assert 'WHERE workspace_id = %s AND target_id = %s AND event_id = %s' in source
