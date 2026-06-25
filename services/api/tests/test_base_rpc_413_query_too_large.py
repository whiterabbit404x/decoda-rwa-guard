"""
Base RPC eth_getLogs HTTP 413 (Request Entity Too Large) handling.

Production blocker: during Base wallet polling QuickNode rejected the eth_getLogs
request with HTTP 413 and failover then hit Alchemy 429, collapsing the whole poll
into ``all_rpc_providers_unavailable``. A 413 is a QUERY-SIZE problem, not a provider
outage, so it must reduce the scan window instead of benching the provider.

These tests cover:
  1. HTTP 413 from eth_getLogs reduces the chunk size (adaptive halving).
  2. HTTP 413 does NOT arm a (long) provider backoff.
  3. HTTP 429 still arms provider backoff (the contrast case).
  4. The cursor is NOT advanced past a chunk that stayed too large at the min range.
  5. The cursor advances to the last successfully scanned chunk.
  6. A large Base catch-up backlog is capped (gradual, not cleared in one cycle).
  7. QuickNode 413 + Alchemy 429 does NOT mark QuickNode permanently unavailable.
  8. No full RPC URL or API key leaks into the 413 logs.
  9. System Health reports "log scan query too large / scan window reduced" (not a
     generic outage) when eth_blockNumber still works.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

import pytest

from services.api.app import evm_activity_provider as eap

WALLET_ADDR = '0x5f6f35fd8b10c5576089f99c7c8c351deb851d1f'

ALCHEMY_KEY = 'ALCHEMY-SUPER-SECRET-KEY'
QUICKNODE_KEY = 'QUICKNODE-SECRET-TOKEN'
ALCHEMY_URL = f'https://base-mainnet.g.alchemy.com/v2/{ALCHEMY_KEY}'
QUICKNODE_URL = f'https://base.quicknode.example/{QUICKNODE_KEY}'
ALCHEMY_HOST = 'base-mainnet.g.alchemy.com'
QUICKNODE_HOST = 'base.quicknode.example'

_RPC_ENV_VARS = (
    'EVM_RPC_URL', 'STAGING_EVM_RPC_URL', 'EVM_RPC_URLS',
    'EVM_RPC_URL_8453', 'EVM_RPC_URL_1', 'EVM_RPC_URL_42161',
    'BASE_EVM_RPC_URL', 'EVM_BASE_RPC_URL', 'EVM_RPC_FAILOVER_URLS',
    'EVM_CHAIN_ID', 'STAGING_EVM_CHAIN_ID', 'LIVE_MONITORING_CHAINS',
    'EVM_WS_URL', 'EVM_RPC_MAX_RETRIES', 'APP_ENV', 'APP_MODE',
    'RPC_PROVIDER_BACKOFF_JITTER_SECONDS', 'RPC_PROVIDER_BACKOFF_MIN_SECONDS',
    'MAX_BLOCKS_PER_CYCLE', 'BASE_CATCHUP_MAX_BLOCKS_PER_CYCLE',
    'BASE_MAX_LOGS_BLOCK_RANGE', 'BASE_MIN_LOGS_BLOCK_RANGE',
    'BASE_LIVE_TAIL_BLOCKS', 'EVM_LIVE_TAIL_BLOCKS',
    'MONITOR_REPLAY_BLOCKS', 'MONITOR_SAFE_BACKFILL', 'MONITOR_BATCH_BLOCKS',
    'EVM_CONFIRMATIONS_REQUIRED',
)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for name in _RPC_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('EVM_RPC_MAX_RETRIES', '0')
    monkeypatch.setenv('RPC_PROVIDER_BACKOFF_JITTER_SECONDS', '0')
    eap.reset_rpc_provider_state()
    yield
    eap.reset_rpc_provider_state()


def _now() -> datetime:
    return datetime(2026, 6, 15, 4, 0, tzinfo=timezone.utc)


def _setup_base_env(monkeypatch):
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')
    monkeypatch.setenv('EVM_CONFIRMATIONS_REQUIRED', '3')
    monkeypatch.setenv('MONITOR_REPLAY_BLOCKS', '25')
    monkeypatch.setenv('BASE_MAX_LOGS_BLOCK_RANGE', '100')
    monkeypatch.setenv('BASE_MIN_LOGS_BLOCK_RANGE', '10')
    monkeypatch.setenv('EVM_LIVE_TAIL_BLOCKS', '0')


def _make_target(cursor: str | None = None) -> dict:
    return {
        'id': str(uuid.uuid4()),
        'workspace_id': str(uuid.uuid4()),
        'name': 'Base Wallet Target',
        'target_type': 'wallet',
        'chain_network': 'base',
        'wallet_address': WALLET_ADDR,
        'contract_identifier': None,
        'monitoring_checkpoint_cursor': cursor,
        'monitoring_interval_seconds': 300,
    }


# ---------------------------------------------------------------------------
# Injected-client stubs (fetch_evm_activity rpc_client= path)
# ---------------------------------------------------------------------------

class _BaseRpc:
    """Minimal Base RPC stub; records every eth_getLogs (from, to) range requested."""

    def __init__(self, latest: int) -> None:
        self.latest = latest
        self.calls: list[tuple[str, list]] = []
        self.getlogs_ranges: list[tuple[int, int]] = []

    def call(self, method: str, params: list) -> object:
        self.calls.append((method, params))
        if method == 'eth_chainId':
            return hex(8453)
        if method == 'eth_blockNumber':
            return hex(self.latest)
        if method == 'eth_getBlockByNumber':
            block_number = int(str(params[0]), 16)
            return {
                'hash': f'0xblock{block_number}',
                'number': hex(block_number),
                'timestamp': hex(int(_now().timestamp()) + block_number),
                'transactions': [],
            }
        if method == 'eth_getLogs':
            frm = int(params[0]['fromBlock'], 16)
            to = int(params[0]['toBlock'], 16)
            self.getlogs_ranges.append((frm, to))
            return self._getlogs(frm, to)
        return {}

    def _getlogs(self, frm: int, to: int) -> object:
        return []


class _Rpc413WhenLargerThan(_BaseRpc):
    """eth_getLogs raises 413 while (to-from+1) > threshold, else returns []."""

    def __init__(self, latest: int, threshold: int, *, as_runtime_error: bool = False) -> None:
        super().__init__(latest)
        self.threshold = threshold
        self.as_runtime_error = as_runtime_error

    def _getlogs(self, frm: int, to: int) -> object:
        if (to - frm + 1) > self.threshold:
            if self.as_runtime_error:
                raise RuntimeError('HTTP Error 413: Request Entity Too Large')
            raise eap.RpcRequestTooLargeError('request_too_large:HTTP Error 413')
        return []


class _Rpc413Always(_BaseRpc):
    """eth_getLogs always raises 413, even at the minimum chunk size."""

    def _getlogs(self, frm: int, to: int) -> object:
        raise eap.RpcRequestTooLargeError('request_too_large:HTTP Error 413')


class _Rpc413Poison(_BaseRpc):
    """eth_getLogs raises 413 only for ranges containing poison_block (any size)."""

    def __init__(self, latest: int, poison_block: int) -> None:
        super().__init__(latest)
        self.poison_block = poison_block

    def _getlogs(self, frm: int, to: int) -> object:
        if frm <= self.poison_block <= to:
            raise eap.RpcRequestTooLargeError('request_too_large:HTTP Error 413')
        return []


# ---------------------------------------------------------------------------
# 1. HTTP 413 reduces the eth_getLogs chunk size (adaptive halving)
# ---------------------------------------------------------------------------

def test_http_413_reduces_eth_getlogs_chunk_size(monkeypatch):
    _setup_base_env(monkeypatch)
    from services.api.app.evm_activity_provider import fetch_evm_activity

    # 100-block requests are rejected; <=50 succeed → the worker must reduce.
    rpc = _Rpc413WhenLargerThan(latest=10_000, threshold=50, as_runtime_error=True)
    target = _make_target(cursor=f'{10_000 - 110}:checkpoint:-1')
    monkeypatch.setenv('MAX_BLOCKS_PER_CYCLE', '200')

    events = fetch_evm_activity(target, None, rpc_client=rpc)

    spans = sorted({to - frm + 1 for frm, to in rpc.getlogs_ranges})
    assert spans, 'eth_getLogs must be attempted'
    assert max(spans) == 100, f'first attempt must use the 100-block max range; spans={spans}'
    assert min(spans) <= 50, f'413 must halve the chunk size to <=50; spans={spans}'
    # The whole poll did not fail — events is a (possibly empty) list, not an exception.
    assert isinstance(events, list)
    # No huge 1000-block eth_getLogs request was ever issued on Base.
    assert max(spans) <= 100, f'Base eth_getLogs must never exceed 100 blocks; spans={spans}'


# ---------------------------------------------------------------------------
# 2 + 3. 413 does not bench the provider; 429 does
# ---------------------------------------------------------------------------

def _ok_resp(result: str):
    resp = MagicMock()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    resp.read.return_value = json.dumps({'jsonrpc': '2.0', 'id': 1, 'result': result}).encode()
    return resp


def _dispatch(*, getlogs_status: dict[str, int], block: int = 47_000_000):
    """urlopen side_effect: raise the configured HTTP status for eth_getLogs per host."""
    def _fn(req, timeout=None):  # noqa: ARG001
        url = req.full_url if hasattr(req, 'full_url') else req.get_full_url()
        host = (urlparse(url).hostname or '').lower()
        try:
            method = json.loads(req.data.decode('utf-8')).get('method', '')
        except Exception:
            method = ''
        if method == 'eth_getLogs' and host in getlogs_status:
            raise urllib.error.HTTPError(url, getlogs_status[host], 'err', {}, None)
        if method == 'eth_chainId':
            return _ok_resp(hex(8453))
        if method == 'eth_blockNumber':
            return _ok_resp(hex(block))
        return _ok_resp('0x0')
    return _fn


def test_http_413_does_not_set_provider_backoff(monkeypatch):
    monkeypatch.setenv('EVM_RPC_URLS', QUICKNODE_URL)
    client = eap.FailoverJsonRpcClient(eap._resolve_evm_rpc_urls())
    with patch.object(eap.request, 'urlopen', side_effect=_dispatch(getlogs_status={QUICKNODE_HOST: 413})):
        with pytest.raises(eap.RpcRequestTooLargeError):
            client.call('eth_getLogs', [{'fromBlock': '0x0', 'toBlock': '0x3e7', 'topics': []}])

    # 413 is a query-size error — the provider must NOT be benched, so polling continues.
    assert eap.host_backoff_active(QUICKNODE_HOST) is False
    assert eap.rpc_provider_backoff_active() is False


def test_http_429_still_sets_provider_backoff(monkeypatch):
    monkeypatch.setenv('EVM_RPC_URLS', ALCHEMY_URL)
    client = eap.FailoverJsonRpcClient(eap._resolve_evm_rpc_urls())
    with patch.object(eap.request, 'urlopen', side_effect=_dispatch(getlogs_status={ALCHEMY_HOST: 429})):
        with pytest.raises(RuntimeError):
            client.call('eth_getLogs', [{'fromBlock': '0x0', 'toBlock': '0x3e7', 'topics': []}])

    # 429 means rate-limited — the provider IS benched (contrast with 413).
    assert eap.host_backoff_active(ALCHEMY_HOST) is True


# ---------------------------------------------------------------------------
# 4 + 5. Cursor behavior
# ---------------------------------------------------------------------------

def test_cursor_not_advanced_after_failed_413_chunk(monkeypatch):
    _setup_base_env(monkeypatch)
    monkeypatch.setenv('MAX_BLOCKS_PER_CYCLE', '200')
    from services.api.app.evm_activity_provider import fetch_evm_activity

    latest = 10_000
    cursor_block = 9_900  # safe_to = 9997
    target = _make_target(cursor=f'{cursor_block}:checkpoint:-1')
    rpc = _Rpc413Always(latest=latest)

    fetch_evm_activity(target, None, rpc_client=rpc)

    # Every logs chunk stayed too large even at the min range → cursor must NOT advance
    # past the prior cursor (no unscanned blocks are skipped).
    assert target['_evm_scan_to_block'] == cursor_block, (
        f'cursor must not advance past unscanned blocks; '
        f'got {target["_evm_scan_to_block"]} (cursor was {cursor_block})'
    )


def test_cursor_advances_to_last_successful_chunk(monkeypatch):
    _setup_base_env(monkeypatch)
    monkeypatch.setenv('MAX_BLOCKS_PER_CYCLE', '500')
    from services.api.app.evm_activity_provider import fetch_evm_activity

    latest = 10_000           # safe_to = 9997
    cursor_block = 9_000      # from_block = 8975
    poison = 9_100            # a chunk containing this block stays too large
    scan_ceiling = (cursor_block - 25) + 500 - 1  # 9474
    target = _make_target(cursor=f'{cursor_block}:checkpoint:-1')
    rpc = _Rpc413Poison(latest=latest, poison_block=poison)

    fetch_evm_activity(target, None, rpc_client=rpc)

    scan_to = target['_evm_scan_to_block']
    # Earlier chunks succeeded → cursor advances beyond the prior cursor, but is capped
    # below the poison block (and the full scan ceiling) so the bad range is re-scanned.
    assert cursor_block < scan_to < poison, (
        f'cursor must advance to the last successful chunk: '
        f'{cursor_block} < {scan_to} < {poison}'
    )
    assert scan_to < scan_ceiling, (
        f'cursor must be capped below the scan ceiling {scan_ceiling}; got {scan_to}'
    )


# ---------------------------------------------------------------------------
# 6. Large Base catch-up backlog is capped (gradual, not cleared in one cycle)
# ---------------------------------------------------------------------------

def test_large_base_catchup_capped_per_cycle(monkeypatch):
    _setup_base_env(monkeypatch)
    from services.api.app.evm_activity_provider import fetch_evm_activity

    latest = 50_000_000
    cursor_block = latest - 160_000  # ~160k blocks behind (reproduces blocks_deferred)
    safe_to = latest - 3
    target = _make_target(cursor=f'{cursor_block}:checkpoint:-1')
    rpc = _BaseRpc(latest=latest)

    fetch_evm_activity(target, None, rpc_client=rpc)

    scan_to = target['_evm_scan_to_block']
    # Default Base catch-up cap is 100 blocks/cycle (+replay overlap) — not 160k.
    assert scan_to - cursor_block <= 100 + 25, (
        f'catch-up must advance ~100 blocks/cycle; advanced {scan_to - cursor_block}'
    )
    # The huge backlog must NOT be cleared in one cycle.
    assert safe_to - scan_to > 100_000, (
        f'huge backlog must catch up gradually; remaining deferred {safe_to - scan_to}'
    )


# ---------------------------------------------------------------------------
# 7. QuickNode 413 + Alchemy 429 does not mark QuickNode permanently unavailable
# ---------------------------------------------------------------------------

def test_quicknode_413_plus_alchemy_429_keeps_quicknode_usable(monkeypatch):
    monkeypatch.setenv('EVM_RPC_URLS', f'{QUICKNODE_URL},{ALCHEMY_URL}')
    client = eap.FailoverJsonRpcClient(eap._resolve_evm_rpc_urls())

    dispatch = _dispatch(getlogs_status={QUICKNODE_HOST: 413, ALCHEMY_HOST: 429})
    with patch.object(eap.request, 'urlopen', side_effect=dispatch):
        # A normal call establishes QuickNode as the active provider.
        assert int(client.call('eth_blockNumber', []), 16) == 47_000_000
        assert client.active_host == QUICKNODE_HOST
        # The oversized log query: QuickNode 413, Alchemy 429.
        with pytest.raises(eap.RpcRequestTooLargeError):
            client.call('eth_getLogs', [{'fromBlock': '0x0', 'toBlock': '0x3e7', 'topics': []}])

    # QuickNode (413) stays usable; only Alchemy (429) is benched.
    assert eap.host_backoff_active(QUICKNODE_HOST) is False
    assert eap.host_backoff_active(ALCHEMY_HOST) is True
    # Not every provider is benched, so the worker keeps polling via QuickNode.
    assert eap.rpc_provider_backoff_active() is False
    # The failover snapshot still names QuickNode active — NOT a generic outage.
    fields = eap.rpc_provider_log_fields()
    assert fields['active_rpc_host'] == QUICKNODE_HOST


# ---------------------------------------------------------------------------
# 8. No full RPC URL / API key leaks into the 413 logs
# ---------------------------------------------------------------------------

def test_no_secret_in_413_logs(monkeypatch, caplog):
    monkeypatch.setenv('EVM_RPC_URLS', f'{QUICKNODE_URL},{ALCHEMY_URL}')
    client = eap.FailoverJsonRpcClient(eap._resolve_evm_rpc_urls())
    dispatch = _dispatch(getlogs_status={QUICKNODE_HOST: 413, ALCHEMY_HOST: 413})

    with caplog.at_level(logging.WARNING, logger='services.api.app.evm_activity_provider'):
        with patch.object(eap.request, 'urlopen', side_effect=dispatch):
            client.call('eth_blockNumber', [])  # set active_host = QuickNode
            result = eap._fetch_wallet_logs_adaptive(
                client, WALLET_ADDR, 1_000, 1_099,
                network='base', target_id='t1', max_range=100, min_range=10,
            )

    assert result['status'] == 'degraded'  # every chunk stayed too large
    text = '\n'.join(r.getMessage() for r in caplog.records)
    assert 'rpc_query_too_large' in text
    assert 'request_too_large' in text
    # The provider host is surfaced (sanitized) — never the URL path or key.
    assert QUICKNODE_HOST in text
    for secret in (QUICKNODE_KEY, ALCHEMY_KEY, '/v2/'):
        assert secret not in text, f'{secret!r} leaked into the 413 logs'


# ---------------------------------------------------------------------------
# 9. System Health: provider reachable but log scan query too large
# ---------------------------------------------------------------------------

def _ok_block_dispatch():
    def _fn(req, timeout=None):  # noqa: ARG001
        return _ok_resp(hex(47_000_000))
    return _fn


def test_system_health_reports_query_too_large_when_block_number_works(monkeypatch):
    from services.api.app import system_health as sh
    monkeypatch.setenv('EVM_RPC_URL', QUICKNODE_URL)
    eap.reset_rpc_provider_state()
    sh._reset_rpc_health_cache()

    # The worker reduced the scan window after an HTTP 413 (provider still reachable).
    eap.record_rpc_query_too_large(QUICKNODE_HOST, reduced_chunk_size=50)
    with patch.object(sh, 'urlopen', side_effect=_ok_block_dispatch()):
        comp = sh._check_rpc()

    assert comp['status'] == 'degraded'
    msg = comp['message'].lower()
    assert 'reachable' in msg
    assert 'log scan query is too large' in msg
    assert 'scan window reduced' in msg
    assert QUICKNODE_HOST in comp['message']
    # Secret-free: host only, never the URL path or key.
    assert QUICKNODE_KEY not in str(comp)

    # Once a full (un-reduced) scan succeeds the signal clears → RPC is healthy again.
    eap.clear_rpc_query_too_large()
    sh._reset_rpc_health_cache()
    with patch.object(sh, 'urlopen', side_effect=_ok_block_dispatch()):
        comp2 = sh._check_rpc()
    assert comp2['status'] == 'healthy'
