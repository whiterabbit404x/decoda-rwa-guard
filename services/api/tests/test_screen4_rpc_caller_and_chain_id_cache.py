"""Screen 4 Section 4: caller categories + per-provider chain-id cache.

Production evidence showed ~13 RPC calls in a 61s window with 7 eth_chainId dials, all
attributed to the 'unspecified' caller. These tests pin the two fixes:

  * probe_rpc_health tags every RPC request with a caller category (scheduled_poll,
    worker_health_check, startup_validation, source_diagnostic, ...) so the periodic
    rpc_request_volume_summary can attribute the load — never 'unspecified'.
  * A provider's chain id is cached after the first successful validation, so a repeat
    probe of the same host reuses it instead of re-dialing eth_chainId (no repeated
    eth_chainId inside the same poll).
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from services.api.app import evm_activity_provider as eap

ALCHEMY_HOST = 'base-mainnet.g.alchemy.com'


@pytest.fixture(autouse=True)
def _reset_state():
    eap.reset_rpc_provider_state()
    yield
    eap.reset_rpc_provider_state()


def _counting_client(counts: dict[str, int]):
    class _Fake:
        def __init__(self, url):
            self.rpc_url = url

        def call(self, method, params):
            counts[method] = counts.get(method, 0) + 1
            return '0x2105' if method == 'eth_chainId' else '0x10'

    return _Fake


# ---------------------------------------------------------------------------
# Test 5: chain id is cached after validation
# ---------------------------------------------------------------------------

def test_chain_id_is_cached_after_validation(monkeypatch):
    counts: dict[str, int] = {}
    monkeypatch.setattr(eap, 'JsonRpcClient', _counting_client(counts))
    url = f'https://{ALCHEMY_HOST}/v2/k'

    assert eap.cached_provider_chain_id(ALCHEMY_HOST) is None
    result = eap.probe_rpc_health(url, caller='scheduled_poll')
    assert result['ok'] is True
    assert result['chain_id_int'] == 0x2105
    # The validated chain id is now cached for this provider host.
    assert eap.cached_provider_chain_id(ALCHEMY_HOST) == 0x2105


# ---------------------------------------------------------------------------
# Test 6: one poll does not repeatedly call eth_chainId
# ---------------------------------------------------------------------------

def test_repeat_probe_reuses_cached_chain_id_no_second_eth_chainid(monkeypatch):
    counts: dict[str, int] = {}
    monkeypatch.setattr(eap, 'JsonRpcClient', _counting_client(counts))
    url = f'https://{ALCHEMY_HOST}/v2/k'

    eap.probe_rpc_health(url, caller='scheduled_poll')
    eap.probe_rpc_health(url, caller='scheduled_poll')
    eap.probe_rpc_health(url, caller='scheduled_poll')

    # eth_chainId is dialed exactly once (first validation); afterwards the cached value
    # is reused. eth_blockNumber (the liveness / latest-block check) still runs each poll.
    assert counts.get('eth_chainId', 0) == 1
    assert counts.get('eth_blockNumber', 0) == 3


def test_cache_cleared_by_reset_re_dials_chain_id(monkeypatch):
    counts: dict[str, int] = {}
    monkeypatch.setattr(eap, 'JsonRpcClient', _counting_client(counts))
    url = f'https://{ALCHEMY_HOST}/v2/k'

    eap.probe_rpc_health(url, caller='scheduled_poll')
    assert counts.get('eth_chainId', 0) == 1
    eap.reset_rpc_provider_state()
    assert eap.cached_provider_chain_id(ALCHEMY_HOST) is None
    eap.probe_rpc_health(url, caller='scheduled_poll')
    assert counts.get('eth_chainId', 0) == 2


# ---------------------------------------------------------------------------
# Test 9: every probe RPC request records a caller category (never 'unspecified')
# ---------------------------------------------------------------------------

def test_probe_records_caller_category_not_unspecified(monkeypatch):
    url = f'https://{ALCHEMY_HOST}/v2/k'
    monkeypatch.setenv('EVM_RPC_MAX_RETRIES', '0')

    bodies = iter([
        b'{"jsonrpc":"2.0","id":1,"result":"0x2105"}',   # eth_chainId
        b'{"jsonrpc":"2.0","id":1,"result":"0x10"}',      # eth_blockNumber
    ])

    class _Resp:
        def __init__(self, body):
            self._body = body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return self._body

    def _fake_urlopen(*_a, **_k):
        return _Resp(next(bodies))

    with patch.object(eap.request, 'urlopen', _fake_urlopen):
        result = eap.probe_rpc_health(url, caller='scheduled_poll')

    assert result['ok'] is True
    snap = eap.rpc_request_volume_snapshot()
    by_caller = snap['hosts'][ALCHEMY_HOST]['by_caller']
    assert 'unspecified' not in by_caller
    assert by_caller.get('scheduled_poll', 0) >= 1


def test_default_caller_is_worker_health_check(monkeypatch):
    """When a caller is not specified explicitly, the probe still records a concrete
    category (worker_health_check) rather than 'unspecified'."""
    url = f'https://{ALCHEMY_HOST}/v2/k'
    monkeypatch.setenv('EVM_RPC_MAX_RETRIES', '0')

    bodies = iter([
        b'{"jsonrpc":"2.0","id":1,"result":"0x2105"}',
        b'{"jsonrpc":"2.0","id":1,"result":"0x10"}',
    ])

    class _Resp:
        def __init__(self, body):
            self._body = body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return self._body

    def _fake_urlopen(*_a, **_k):
        return _Resp(next(bodies))

    with patch.object(eap.request, 'urlopen', _fake_urlopen):
        eap.probe_rpc_health(url)

    snap = eap.rpc_request_volume_snapshot()
    by_caller = snap['hosts'][ALCHEMY_HOST]['by_caller']
    assert 'unspecified' not in by_caller
    assert by_caller.get('worker_health_check', 0) >= 1
