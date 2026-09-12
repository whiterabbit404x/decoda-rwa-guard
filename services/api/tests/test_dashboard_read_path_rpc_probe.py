"""Provider reachability on the dashboard read path stays bounded and fail-closed.

``monitoring_runtime_status`` runs on every dashboard request, and it used to
issue an ``eth_chainId`` call carrying the worker's RPC budget
(EVM_RPC_TIMEOUT_SECONDS=10 x 4 attempts + 1/2/4s backoff). These tests pin the
two properties that must hold instead:

  * a read-path probe is ONE attempt bounded to <=1s, and
  * no answer -> UNKNOWN -> not reachable. A missing measurement must never be
    rendered as a connected or healthy provider.
"""

from __future__ import annotations

import socket
import threading
import time

import pytest

from services.api.app import evm_activity_provider as provider


@pytest.fixture(autouse=True)
def _reset_provider_state():
    provider.reset_rpc_provider_state()
    yield
    provider.reset_rpc_provider_state()


@pytest.fixture
def blackhole_url():
    """A listening socket that accepts connections and then never responds.

    This is the shape that actually cost 10s in production: the TCP connect
    succeeds, so the client waits out its read timeout rather than failing fast.
    """
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(('127.0.0.1', 0))
    listener.listen(8)
    host, port = listener.getsockname()
    held: list[socket.socket] = []
    stop = threading.Event()

    def _accept_and_hang():
        while not stop.is_set():
            try:
                conn, _ = listener.accept()
            except OSError:
                return
            held.append(conn)

    thread = threading.Thread(target=_accept_and_hang, daemon=True)
    thread.start()
    try:
        yield f'http://{host}:{port}'
    finally:
        stop.set()
        listener.close()
        for conn in held:
            try:
                conn.close()
            except OSError:
                pass


def test_read_path_timeout_is_clamped_to_one_second(monkeypatch):
    monkeypatch.setenv('EVM_RPC_READ_PATH_TIMEOUT_SECONDS', '30')
    assert provider.read_path_rpc_timeout_seconds() == 1.0

    monkeypatch.setenv('EVM_RPC_READ_PATH_TIMEOUT_SECONDS', '0.25')
    assert provider.read_path_rpc_timeout_seconds() == 0.25

    monkeypatch.setenv('EVM_RPC_READ_PATH_TIMEOUT_SECONDS', 'not-a-number')
    assert provider.read_path_rpc_timeout_seconds() == 1.0


def test_bounded_probe_returns_within_one_second_and_reports_unknown(blackhole_url, monkeypatch):
    """The worker budget must not leak into the read path."""
    # Worker-path settings that previously applied here: 10s x 4 attempts.
    monkeypatch.setenv('EVM_RPC_TIMEOUT_SECONDS', '10')
    monkeypatch.setenv('EVM_RPC_MAX_RETRIES', '3')

    started = time.monotonic()
    result = provider.probe_rpc_reachable_bounded(blackhole_url)
    elapsed = time.monotonic() - started

    # None == UNKNOWN, never False-as-fact and never True.
    assert result is None
    assert elapsed < 3.0, f'read-path probe took {elapsed:.2f}s; it must stay bounded near 1s'


def test_bounded_probe_makes_exactly_one_attempt(monkeypatch):
    attempts: list[str] = []

    class _OneShotClient:
        def __init__(self, url, timeout_seconds_override=None, max_attempts_override=None):
            attempts.append(url)
            assert max_attempts_override == 1, 'read-path probe must not retry'
            assert timeout_seconds_override is not None and timeout_seconds_override <= 1.0

        def call(self, method, params):
            raise TimeoutError('provider did not answer')

    monkeypatch.setattr(provider, 'JsonRpcClient', _OneShotClient)
    assert provider.probe_rpc_reachable_bounded('http://rpc.test') is None
    assert len(attempts) == 1


def test_bounded_probe_skips_the_network_while_every_provider_is_backed_off(monkeypatch):
    called: list[str] = []

    class _ShouldNotBeCalled:
        def __init__(self, *args, **kwargs):
            called.append('constructed')

        def call(self, method, params):  # pragma: no cover - must not run
            called.append('called')
            return '0x1'

    monkeypatch.setattr(provider, 'JsonRpcClient', _ShouldNotBeCalled)
    monkeypatch.setattr(provider, 'rpc_provider_backoff_active', lambda: True)

    # Backoff means no fresh evidence, so the answer is unknown -- not reachable.
    assert provider.probe_rpc_reachable_bounded('http://rpc.test') is None
    assert called == []


def test_cached_reachability_is_none_without_a_recorded_probe():
    assert provider.cached_rpc_reachability() is None


def test_cached_reachability_replays_a_fresh_probe_result():
    provider._store_rpc_health({'ok': True, 'chain_id_int': 8453})
    assert provider.cached_rpc_reachability() is True

    provider._store_rpc_health({'ok': False, 'error': 'unreachable'})
    assert provider.cached_rpc_reachability() is False


def test_cached_reachability_expires_and_reports_unknown_not_reachable():
    provider._store_rpc_health({'ok': True, 'chain_id_int': 8453})
    # A stale positive must decay to UNKNOWN rather than keep asserting reachable.
    assert provider.cached_rpc_reachability(max_age_seconds=0) is None


def test_claim_validator_never_reports_reachable_without_a_definite_result(monkeypatch):
    """Unknown reachability leaves the claim check False (fail closed)."""
    from services.api.app import monitoring_runner

    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc.test')
    monkeypatch.setenv('LIVE_MONITORING_ENABLED', 'true')
    monkeypatch.setattr(monitoring_runner, 'monitoring_ingestion_runtime', lambda: {'mode': 'live', 'degraded': False, 'source': 'polling'})
    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: False)
    monkeypatch.setattr(monitoring_runner, 'cached_rpc_reachability', lambda **_: None)
    monkeypatch.setattr(monitoring_runner, 'probe_rpc_reachable_bounded', lambda _url: None)

    result = monitoring_runner.production_claim_validator()

    assert result['checks']['evm_rpc_reachable'] is False
    assert result['checks']['provider_reachable_or_backfilling'] is False
    assert result['reason'] == 'evm_rpc_reachability_unknown'
    assert result['status'] != 'PASS'


def test_claim_validator_uses_the_cached_fact_without_touching_the_network(monkeypatch):
    from services.api.app import monitoring_runner

    probed: list[str] = []

    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc.test')
    monkeypatch.setenv('LIVE_MONITORING_ENABLED', 'true')
    monkeypatch.setattr(monitoring_runner, 'monitoring_ingestion_runtime', lambda: {'mode': 'live', 'degraded': False, 'source': 'polling'})
    monkeypatch.setattr(monitoring_runner, 'live_mode_enabled', lambda: False)
    monkeypatch.setattr(monitoring_runner, 'cached_rpc_reachability', lambda **_: True)
    monkeypatch.setattr(
        monitoring_runner,
        'probe_rpc_reachable_bounded',
        lambda url: probed.append(url) or None,
    )

    result = monitoring_runner.production_claim_validator()

    assert result['checks']['evm_rpc_reachable'] is True
    assert probed == [], 'a fresh cached fact must not trigger a network probe'
