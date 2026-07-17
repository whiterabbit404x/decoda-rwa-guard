"""Tests for the monitoring worker's self-evident boot logging and fail-loud
start-blocked behavior (task: make worker startup self-evident / fail loudly).

These prove, from logs alone, that:
- `event=monitoring_worker_process_boot` is the FIRST worker log (before any config
  resolution or early exit), so a service whose logs lack it is not running the worker.
- `event=monitoring_worker_configuration` reports resolved config with NO secrets.
- A production-like worker with a missing prerequisite emits
  `event=monitoring_worker_start_blocked reason=<...>` and exits non-zero (so Railway
  shows the deploy as failed, never false-healthy), while a non-production / --once run
  only warns and continues.
"""
from __future__ import annotations

from types import SimpleNamespace

from services.api.app import run_monitoring_worker


_RPC_ENV_VARS = (
    'EVM_RPC_URL',
    'STAGING_EVM_RPC_URL',
    'EVM_RPC_URLS',
    'EVM_RPC_URL_8453',
    'BASE_EVM_RPC_URL',
    'EVM_BASE_RPC_URL',
)
_ENABLE_ENV_VARS = (
    'STAGING_WORKER_ENABLED',
    'WORKER_ENABLED',
    'MONITORING_WORKER_ENABLED',
    'LIVE_MODE_ENABLED',
)


def _clear_worker_env(monkeypatch) -> None:
    for name in (*_RPC_ENV_VARS, *_ENABLE_ENV_VARS, 'DATABASE_URL', 'EVM_CHAIN_ID', 'STAGING_EVM_CHAIN_ID'):
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def test_start_blocked_reasons_pure() -> None:
    healthy = {
        'worker_enabled': True,
        'live_mode_enabled': True,
        'database_configured': True,
        'rpc_configured': True,
        'chain_id': 8453,
    }
    assert run_monitoring_worker._resolve_worker_start_blocked_reasons(healthy) == []

    assert 'worker_disabled' in run_monitoring_worker._resolve_worker_start_blocked_reasons(
        {**healthy, 'worker_enabled': False}
    )
    assert 'live_mode_disabled' in run_monitoring_worker._resolve_worker_start_blocked_reasons(
        {**healthy, 'live_mode_enabled': False}
    )
    assert 'database_missing' in run_monitoring_worker._resolve_worker_start_blocked_reasons(
        {**healthy, 'database_configured': False}
    )
    assert 'rpc_missing' in run_monitoring_worker._resolve_worker_start_blocked_reasons(
        {**healthy, 'rpc_configured': False}
    )
    assert 'unsupported_chain' in run_monitoring_worker._resolve_worker_start_blocked_reasons(
        {**healthy, 'chain_id': 137}
    )


def test_unset_chain_id_is_not_unsupported() -> None:
    healthy = {
        'worker_enabled': True,
        'live_mode_enabled': True,
        'database_configured': True,
        'rpc_configured': True,
        'chain_id': None,
    }
    assert run_monitoring_worker._resolve_worker_start_blocked_reasons(healthy) == []
    for chain_id in (1, 8453, 84532, 42161):
        assert 'unsupported_chain' not in run_monitoring_worker._resolve_worker_start_blocked_reasons(
            {**healthy, 'chain_id': chain_id}
        )


def test_boot_configuration_reports_host_not_secret(monkeypatch) -> None:
    _clear_worker_env(monkeypatch)
    monkeypatch.setenv('WORKER_ENABLED', 'true')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://user:pass@db-host/decoda')
    monkeypatch.setenv('EVM_RPC_URL', 'https://base-mainnet.example.com/v2/SUPERSECRETKEY')
    monkeypatch.setenv('EVM_CHAIN_ID', '8453')

    run_monitoring_worker._resolve_worker_enabled_env()
    config = run_monitoring_worker._resolve_boot_configuration()

    assert config['worker_enabled'] is True
    assert config['live_mode_enabled'] is True
    assert config['database_configured'] is True
    assert config['rpc_configured'] is True
    assert config['rpc_host'] == 'base-mainnet.example.com'
    assert config['chain_id'] == 8453
    # The resolved config must never carry the RPC key or the full DB URL.
    rendered = str(config)
    assert 'SUPERSECRETKEY' not in rendered
    assert 'postgresql://' not in rendered


# ---------------------------------------------------------------------------
# main() boot-log ordering (non-production, --once)
# ---------------------------------------------------------------------------

def test_process_boot_logged_before_configuration(monkeypatch, caplog) -> None:
    _clear_worker_env(monkeypatch)
    monkeypatch.setenv('APP_MODE', 'development')
    monkeypatch.setenv('WORKER_ENABLED', 'true')
    monkeypatch.setenv('LIVE_MODE_ENABLED', 'true')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://user:pass@db-host/decoda')
    monkeypatch.setenv('EVM_RPC_URL', 'https://base-rpc.example.com/KEY_ABC')
    monkeypatch.setattr(
        'services.api.app.evm_activity_provider._resolve_evm_rpc_url',
        lambda: 'https://base-rpc.example.com/KEY_ABC',
    )
    monkeypatch.setattr(
        'services.api.app.evm_activity_provider.probe_rpc_health',
        lambda *a, **k: {
            'ok': True, 'chain_id_hex': '0x2105', 'chain_id_int': 8453,
            'block_number_hex': '0x1', 'block_number_int': 1, 'error': None,
        },
    )
    monkeypatch.setattr(run_monitoring_worker, 'validate_monitoring_config_or_raise', lambda: None)
    monkeypatch.setattr(run_monitoring_worker, '_start_health_server', lambda *a, **k: None)
    monkeypatch.setattr(
        run_monitoring_worker, 'parse_args',
        lambda: SimpleNamespace(worker_name='test-worker', interval_seconds=60, limit=5, once=True),
    )
    monkeypatch.setattr(
        run_monitoring_worker, 'run_monitoring_cycle',
        lambda worker_name, limit, trigger_type='scheduler': {
            'due_targets': 0, 'checked': 0, 'alerts_generated': 0, 'live_mode': True,
        },
    )
    monkeypatch.setattr(run_monitoring_worker, 'evaluate_monitoring_system_alerts', lambda stale_after_seconds: {})
    monkeypatch.setattr(run_monitoring_worker, 'gauge', lambda *a, **k: None)

    with caplog.at_level('INFO'):
        assert run_monitoring_worker.main() == 0

    text = caplog.text
    assert 'event=monitoring_worker_process_boot' in text
    assert 'python_module=services.api.app.run_monitoring_worker' in text
    assert 'event=monitoring_worker_configuration' in text
    # boot marker must be emitted before configuration (it is the first executable line)
    assert text.index('monitoring_worker_process_boot') < text.index('monitoring_worker_configuration')
    # configuration line never leaks the RPC key
    config_line = next(m for m in caplog.messages if 'event=monitoring_worker_configuration' in m)
    assert 'KEY_ABC' not in config_line
    assert 'rpc_host=base-rpc.example.com' in config_line
    # not blocked → no start-blocked line
    assert 'event=monitoring_worker_start_blocked' not in text


def test_monitoring_worker_starting_line_carries_identity_and_no_secrets(monkeypatch, caplog) -> None:
    """event=monitoring_worker_starting is the single greppable startup line the runbook
    (task step 1) asks operators to look for. It must carry service_role, deployment
    commit sha, worker_enabled, database_configured, chain_id, rpc_configured, the RPC
    *host*, the poll interval, and BOTH the worker identity and heartbeat identity — and
    it must never leak the RPC key or the full DATABASE_URL."""
    _clear_worker_env(monkeypatch)
    monkeypatch.setenv('APP_MODE', 'development')
    monkeypatch.setenv('SERVICE_ROLE', 'worker')
    monkeypatch.setenv('WORKER_ENABLED', 'true')
    monkeypatch.setenv('LIVE_MODE_ENABLED', 'true')
    monkeypatch.setenv('DATABASE_URL', 'postgresql://user:pass@db-host/decoda')
    monkeypatch.setenv('EVM_RPC_URL', 'https://base-mainnet.g.alchemy.com/v2/SUPERSECRETKEY')
    monkeypatch.setenv('EVM_CHAIN_ID', '8453')
    monkeypatch.setattr(
        'services.api.app.evm_activity_provider._resolve_evm_rpc_url',
        lambda: 'https://base-mainnet.g.alchemy.com/v2/SUPERSECRETKEY',
    )
    monkeypatch.setattr(
        'services.api.app.evm_activity_provider.probe_rpc_health',
        lambda *a, **k: {
            'ok': True, 'chain_id_hex': '0x2105', 'chain_id_int': 8453,
            'block_number_hex': '0x1', 'block_number_int': 1, 'error': None,
        },
    )
    monkeypatch.setattr(run_monitoring_worker, 'validate_monitoring_config_or_raise', lambda: None)
    monkeypatch.setattr(run_monitoring_worker, '_start_health_server', lambda *a, **k: None)
    monkeypatch.setattr(
        run_monitoring_worker, 'parse_args',
        lambda: SimpleNamespace(worker_name='monitoring-worker-abc123', interval_seconds=60, limit=5, once=True),
    )
    monkeypatch.setattr(
        run_monitoring_worker, 'run_monitoring_cycle',
        lambda worker_name, limit, trigger_type='scheduler': {
            'due_targets': 0, 'checked': 0, 'alerts_generated': 0, 'live_mode': True,
        },
    )
    monkeypatch.setattr(run_monitoring_worker, 'evaluate_monitoring_system_alerts', lambda stale_after_seconds: {})
    monkeypatch.setattr(run_monitoring_worker, 'gauge', lambda *a, **k: None)

    with caplog.at_level('INFO'):
        assert run_monitoring_worker.main() == 0

    starting_line = next(
        (m for m in caplog.messages if 'event=monitoring_worker_starting' in m), None
    )
    assert starting_line is not None, 'worker must emit event=monitoring_worker_starting'
    assert 'service_role=worker' in starting_line
    assert 'worker_enabled=True' in starting_line
    assert 'database_configured=True' in starting_line
    assert 'chain_id=8453' in starting_line
    assert 'rpc_configured=True' in starting_line
    assert 'rpc_host=base-mainnet.g.alchemy.com' in starting_line
    assert 'poll_interval_seconds=60' in starting_line
    # Worker identity AND heartbeat identity are both present and equal (heartbeats are
    # keyed by worker_name, so the writer and the runtime-status reader agree).
    assert 'worker_id=monitoring-worker-abc123' in starting_line
    assert 'heartbeat_id=monitoring-worker-abc123' in starting_line
    # No secrets ever.
    assert 'SUPERSECRETKEY' not in starting_line
    assert 'postgresql://' not in starting_line


def test_resolve_service_role_defaults_to_worker(monkeypatch) -> None:
    monkeypatch.delenv('SERVICE_ROLE', raising=False)
    assert run_monitoring_worker._resolve_service_role() == 'worker'
    monkeypatch.setenv('SERVICE_ROLE', 'api')
    assert run_monitoring_worker._resolve_service_role() == 'api'
    monkeypatch.setenv('SERVICE_ROLE', '   ')
    assert run_monitoring_worker._resolve_service_role() == 'worker'


# ---------------------------------------------------------------------------
# Fail-loud: production exits non-zero, non-production continues
# ---------------------------------------------------------------------------

def test_production_worker_disabled_exits_nonzero(monkeypatch, caplog) -> None:
    _clear_worker_env(monkeypatch)
    monkeypatch.setenv('APP_ENV', 'production')
    # Everything required is missing → hard, non-recoverable misconfiguration.
    monkeypatch.setattr(
        run_monitoring_worker, 'parse_args',
        lambda: SimpleNamespace(worker_name='w', interval_seconds=60, limit=5, once=False),
    )
    reached = {'health': False, 'cycle': False}
    monkeypatch.setattr(
        run_monitoring_worker, '_start_health_server',
        lambda *a, **k: reached.__setitem__('health', True),
    )
    monkeypatch.setattr(
        run_monitoring_worker, 'run_monitoring_cycle',
        lambda *a, **k: reached.__setitem__('cycle', True) or {},
    )

    with caplog.at_level('INFO'):
        return_code = run_monitoring_worker.main()

    assert return_code == 3, 'production worker with missing prerequisites must exit non-zero'
    # Exited before starting the health server or entering the loop → no false-healthy.
    assert reached['health'] is False
    assert reached['cycle'] is False

    text = caplog.text
    assert 'event=monitoring_worker_start_blocked reason=worker_disabled' in text
    assert 'exit_nonzero_so_railway_shows_failed_not_false_healthy' in text
    assert 'event=monitoring_worker_start_aborted' in text
    # boot + configuration are STILL emitted before the abort (self-evident).
    assert 'event=monitoring_worker_process_boot' in text
    assert 'event=monitoring_worker_configuration' in text


def test_non_production_blocked_worker_continues(monkeypatch, caplog) -> None:
    _clear_worker_env(monkeypatch)
    monkeypatch.setenv('APP_MODE', 'development')
    # Worker enabled + live so the loop runs, but DATABASE_URL / RPC missing → blocked,
    # yet non-production must only warn and continue (single --once cycle here).
    monkeypatch.setenv('WORKER_ENABLED', 'true')
    monkeypatch.setenv('LIVE_MODE_ENABLED', 'true')
    monkeypatch.setattr(
        'services.api.app.evm_activity_provider._resolve_evm_rpc_url', lambda: '',
    )
    monkeypatch.setattr(run_monitoring_worker, 'validate_monitoring_config_or_raise', lambda: None)
    monkeypatch.setattr(run_monitoring_worker, '_start_health_server', lambda *a, **k: None)
    monkeypatch.setattr(
        run_monitoring_worker, 'parse_args',
        lambda: SimpleNamespace(worker_name='test-worker', interval_seconds=60, limit=5, once=True),
    )
    cycle_ran = {'count': 0}
    monkeypatch.setattr(
        run_monitoring_worker, 'run_monitoring_cycle',
        lambda worker_name, limit, trigger_type='scheduler': cycle_ran.__setitem__('count', cycle_ran['count'] + 1) or {
            'due_targets': 0, 'checked': 0, 'alerts_generated': 0, 'live_mode': True,
        },
    )
    monkeypatch.setattr(run_monitoring_worker, 'evaluate_monitoring_system_alerts', lambda stale_after_seconds: {})
    monkeypatch.setattr(run_monitoring_worker, 'gauge', lambda *a, **k: None)

    with caplog.at_level('INFO'):
        return_code = run_monitoring_worker.main()

    assert return_code == 0
    assert cycle_ran['count'] == 1, 'non-production worker must continue past a start-blocked reason'
    text = caplog.text
    assert 'event=monitoring_worker_start_blocked reason=rpc_missing' in text
    assert 'action=continue_degraded' in text
    assert 'event=monitoring_worker_start_aborted' not in text
