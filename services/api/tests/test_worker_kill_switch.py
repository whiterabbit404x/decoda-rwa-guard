"""Worker kill-switch semantics — production/staging flag precedence + no network when killed.

Production incident (2026-07-22): the worker was expected disabled, but logs showed
``worker_enabled=True worker_enabled_source=WORKER_ENABLED=true`` because the enable
resolution was OR-based — ``STAGING_WORKER_ENABLED=false`` could not disable a worker while
``WORKER_ENABLED`` (or another alias) was truthy. The fix makes an EXPLICIT
``WORKER_ENABLED=false`` the unambiguous kill switch: authoritative over every other flag,
stopping ALL monitoring work and ALL provider network calls, while the process may still
expose ``/health``.

Maps to task tests 1 (WORKER_ENABLED=false prevents all RPC calls), 2 (production flag
precedence cannot re-enable the worker) and 12 (normal production mode remains available
after the kill switch is removed), plus staging precedence.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.api.app import run_monitoring_worker
from services.api.app.worker_enable import resolve_worker_enabled, worker_explicitly_disabled


_RPC_ENV_VARS = (
    'EVM_RPC_URL', 'STAGING_EVM_RPC_URL', 'EVM_RPC_URLS', 'EVM_RPC_URL_8453',
    'BASE_EVM_RPC_URL', 'EVM_BASE_RPC_URL',
)
_ENABLE_ENV_VARS = (
    'STAGING_WORKER_ENABLED', 'WORKER_ENABLED', 'MONITORING_WORKER_ENABLED', 'LIVE_MODE_ENABLED',
)


def _clear(monkeypatch) -> None:
    for name in (
        *_RPC_ENV_VARS, *_ENABLE_ENV_VARS,
        'DATABASE_URL', 'EVM_CHAIN_ID', 'STAGING_EVM_CHAIN_ID', 'APP_ENV', 'APP_MODE',
    ):
        monkeypatch.delenv(name, raising=False)


class _NetworkTouched(RuntimeError):
    pass


def _guard_network(monkeypatch, reached: dict) -> None:
    """Make any provider network call blow up, so "no network" is proven, not assumed."""
    def _boom(*_a, **_k):
        reached['network'] = True
        raise _NetworkTouched('provider network must not be reached when the worker is killed')

    monkeypatch.setattr('services.api.app.evm_activity_provider.probe_rpc_health', _boom)
    monkeypatch.setattr(
        run_monitoring_worker, 'run_monitoring_cycle',
        lambda *a, **k: reached.__setitem__('cycle', True) or {},
    )


def _args(once: bool) -> SimpleNamespace:
    return SimpleNamespace(worker_name='w', interval_seconds=60, limit=5, once=once)


# ===========================================================================
# Flag precedence (task tests 2 + 12, plus staging precedence).
# ===========================================================================
def test_explicit_worker_enabled_false_overrides_all_other_flags(monkeypatch):
    """Task test 2: no other flag can re-enable an explicit WORKER_ENABLED=false."""
    _clear(monkeypatch)
    monkeypatch.setenv('WORKER_ENABLED', 'false')
    monkeypatch.setenv('STAGING_WORKER_ENABLED', 'true')
    monkeypatch.setenv('MONITORING_WORKER_ENABLED', 'true')
    monkeypatch.setenv('LIVE_MODE_ENABLED', 'true')
    state = resolve_worker_enabled()
    assert state['enabled'] is False
    assert state['explicit_disable'] is True
    assert state['source'] == 'WORKER_ENABLED=false'
    assert worker_explicitly_disabled() is True


@pytest.mark.parametrize('falsy', ['false', '0', 'no', 'off', 'FALSE', 'Off'])
def test_all_recognized_false_values_kill(monkeypatch, falsy):
    _clear(monkeypatch)
    monkeypatch.setenv('WORKER_ENABLED', falsy)
    monkeypatch.setenv('LIVE_MODE_ENABLED', 'true')
    assert resolve_worker_enabled()['enabled'] is False


def test_staging_worker_enabled_true_enables_when_master_unset(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv('STAGING_WORKER_ENABLED', 'true')
    state = resolve_worker_enabled()
    assert state['enabled'] is True
    assert state['source'] == 'STAGING_WORKER_ENABLED=true'
    assert state['explicit_disable'] is False


def test_staging_false_alone_does_not_kill_a_truthy_master(monkeypatch):
    """The exact production gap: STAGING_WORKER_ENABLED=false does NOT disable while
    WORKER_ENABLED=true; the operator must use WORKER_ENABLED=false."""
    _clear(monkeypatch)
    monkeypatch.setenv('STAGING_WORKER_ENABLED', 'false')
    monkeypatch.setenv('WORKER_ENABLED', 'true')
    assert resolve_worker_enabled()['enabled'] is True
    monkeypatch.setenv('WORKER_ENABLED', 'false')  # the unambiguous kill
    assert resolve_worker_enabled()['enabled'] is False


def test_normal_production_mode_available_after_kill_removed(monkeypatch):
    """Task test 12: with the kill switch removed, WORKER_ENABLED=true restores normal
    production monitoring."""
    _clear(monkeypatch)
    monkeypatch.setenv('WORKER_ENABLED', 'false')
    assert resolve_worker_enabled()['enabled'] is False
    monkeypatch.setenv('WORKER_ENABLED', 'true')
    state = resolve_worker_enabled()
    assert state['enabled'] is True
    assert state['explicit_disable'] is False


def test_unset_master_is_not_an_explicit_kill(monkeypatch):
    """A worker disabled merely because no flag is set is NOT an explicit kill (it is a
    misconfiguration that still fails the deploy loudly in production)."""
    _clear(monkeypatch)
    state = resolve_worker_enabled()
    assert state['enabled'] is False
    assert state['explicit_disable'] is False
    assert state['source'] == 'none'
    assert worker_explicitly_disabled() is False


# ===========================================================================
# No network when killed (task test 1).
# ===========================================================================
def test_worker_enabled_false_once_prevents_all_rpc_calls(monkeypatch, caplog):
    """Task test 1: WORKER_ENABLED=false makes main() return without any RPC/provider call,
    even in production, even with everything else configured and LIVE_MODE_ENABLED=true."""
    _clear(monkeypatch)
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('WORKER_ENABLED', 'false')
    monkeypatch.setenv('LIVE_MODE_ENABLED', 'true')  # must not re-enable
    monkeypatch.setenv('DATABASE_URL', 'postgres://x')
    monkeypatch.setenv('EVM_RPC_URL', 'http://base-rpc.example')
    monkeypatch.setenv('EVM_CHAIN_ID', '8453')
    reached = {'network': False, 'cycle': False, 'health': False}
    _guard_network(monkeypatch, reached)
    monkeypatch.setattr(run_monitoring_worker, '_start_health_server', lambda *a, **k: reached.__setitem__('health', True))
    monkeypatch.setattr(run_monitoring_worker, 'parse_args', lambda: _args(once=True))

    with caplog.at_level('INFO'):
        rc = run_monitoring_worker.main()

    assert rc == 0
    assert reached['network'] is False, 'no provider RPC call may occur when killed'
    assert reached['cycle'] is False, 'no monitoring cycle may run when killed'
    text = caplog.text
    assert 'event=monitoring_worker_start_blocked reason=worker_disabled' in text
    assert 'worker_enabled_source=WORKER_ENABLED=false' in text
    assert 'network_attempted=false' in text


def test_worker_enabled_false_idle_serves_health_without_network(monkeypatch, caplog):
    """The production (non --once) kill path: expose /health, idle, NEVER touch the network."""
    _clear(monkeypatch)
    monkeypatch.setenv('APP_ENV', 'production')
    monkeypatch.setenv('WORKER_ENABLED', 'false')
    monkeypatch.setenv('DATABASE_URL', 'postgres://x')
    monkeypatch.setenv('EVM_RPC_URL', 'http://base-rpc.example')
    reached = {'network': False, 'cycle': False, 'health': False}
    _guard_network(monkeypatch, reached)
    monkeypatch.setattr(run_monitoring_worker, '_start_health_server', lambda *a, **k: reached.__setitem__('health', True))
    monkeypatch.setattr(run_monitoring_worker, 'parse_args', lambda: _args(once=False))

    class _StopIdle(RuntimeError):
        pass

    def _stop_sleep(_seconds):
        raise _StopIdle()

    monkeypatch.setattr(run_monitoring_worker.time, 'sleep', _stop_sleep)

    with caplog.at_level('INFO'), pytest.raises(_StopIdle):
        run_monitoring_worker.main()

    assert reached['health'] is True, 'a killed worker MAY expose /health'
    assert reached['network'] is False
    assert reached['cycle'] is False
    assert 'event=monitoring_worker_idle_disabled' in caplog.text


def test_worker_disabled_by_omission_still_hard_exits_in_production(monkeypatch, caplog):
    """Regression guard: NO enabling flag set (source=none) is a misconfiguration, not an
    intentional kill — production still exits non-zero (existing fail-loud behavior)."""
    _clear(monkeypatch)
    monkeypatch.setenv('APP_ENV', 'production')
    reached = {'network': False, 'cycle': False, 'health': False}
    _guard_network(monkeypatch, reached)
    monkeypatch.setattr(run_monitoring_worker, '_start_health_server', lambda *a, **k: reached.__setitem__('health', True))
    monkeypatch.setattr(run_monitoring_worker, 'parse_args', lambda: _args(once=False))

    with caplog.at_level('INFO'):
        rc = run_monitoring_worker.main()

    assert rc == 3, 'a production worker with no enabling flag must fail the deploy loudly'
    assert reached['network'] is False
    assert 'event=monitoring_worker_start_aborted' in caplog.text
