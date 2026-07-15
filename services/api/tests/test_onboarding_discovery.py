"""Unit tests for the deterministic Onboarding Agent discovery + benchmark engine.

Covers the task's required backend proofs using a fully offline fake RPC
transport (no real DB / network / LLM):
  * ERC-20, ERC-1155, ERC-4626 detection
  * proxy implementation resolution (EIP-1967)
  * invalid / zero address rejection, EOA -> no deployed contract
  * RPC chain-id mismatch rejection
  * reliability-weighted provider ranking (slow-healthy below fast-healthy)
  * fast-but-unreliable never primary; block-lag downgrade
  * a provider timeout does not stop the whole benchmark
  * SSRF guard blocks private / loopback / metadata endpoints
  * secret redaction of key-bearing RPC URLs
"""
from __future__ import annotations

import pytest

from services.api.app import onboarding_discovery as od


# ---------------------------------------------------------------------------
# ABI-return encoders + a configurable fake transport.
# ---------------------------------------------------------------------------
def enc_uint(n: int) -> str:
    return '0x' + format(n, 'x').rjust(64, '0')


def enc_bool(b: bool) -> str:
    return enc_uint(1 if b else 0)


def enc_address(addr: str) -> str:
    return '0x' + od._strip0x(addr).lower().rjust(64, '0')


def enc_string(s: str) -> str:
    raw = s.encode('utf-8')
    body = raw.hex().ljust(((len(raw) + 31) // 32) * 64, '0') if raw else ''
    return '0x' + enc_uint(32)[2:] + enc_uint(len(raw))[2:] + body


def slot_address(addr: str) -> str:
    return '0x' + od._strip0x(addr).lower().rjust(64, '0')


class FakeTransport(od.RpcTransport):
    """Fake JSON-RPC transport driven by a response spec."""

    def __init__(self, host='rpc.test', *, chain_id=8453, block=1000, code='0xabcd',
                 storage=None, calls=None, fail_methods=None, latency_ms=5.0, rate_limited=False):
        self.host = host
        self._chain_id = chain_id
        self._block = block
        self._code = code
        self._storage = storage or {}
        self._calls = calls or {}          # selector(10 chars) -> return hex or Exception
        self._fail = fail_methods or {}     # method -> RpcError kind
        self._latency = latency_ms
        self._rate_limited = rate_limited

    def call(self, method, params, timeout=None):
        if self._rate_limited:
            raise od.RpcError('http 429', kind='rate_limited', http_status=429)
        if method in self._fail:
            kind = self._fail[method]
            raise od.RpcError(kind, kind=kind, http_status=429 if kind == 'rate_limited' else None)
        if method == 'eth_chainId':
            return enc_uint(self._chain_id)
        if method == 'eth_blockNumber':
            return enc_uint(self._block)
        if method == 'eth_getCode':
            return self._code
        if method == 'eth_getStorageAt':
            slot = params[1]
            return self._storage.get(slot, enc_uint(0))
        if method == 'eth_call':
            data = params[0]['data']
            selector = data[:10]
            ret = self._calls.get(selector)
            if isinstance(ret, Exception):
                raise ret
            if ret is None:
                raise od.RpcError('execution reverted', kind='rpc_error')
            return ret
        raise od.RpcError('unsupported ' + method, kind='rpc_error')


def erc20_transport(**over):
    code = '0x' + od.SELECTORS['transfer'][2:] + od.SELECTORS['balanceOf'][2:] + \
           od.SELECTORS['mint'][2:] + od.SELECTORS['pause'][2:] + od.SELECTORS['paused'][2:]
    calls = {
        od.SELECTORS['name']: enc_string('USD Coin'),
        od.SELECTORS['symbol']: enc_string('USDC'),
        od.SELECTORS['decimals']: enc_uint(6),
        od.SELECTORS['totalSupply']: enc_uint(50_000_000_000000),
        od.SELECTORS['owner']: enc_address('0x1111111111111111111111111111111111111111'),
        od.SELECTORS['supportsInterface']: enc_bool(False),
    }
    kw = {'code': code, 'calls': calls}
    kw.update(over)
    return FakeTransport(**kw)


CONTRACT = '0xA0b86991C6218B36C1d19D4a2E9EB0CE3606EB48'


# ---------------------------------------------------------------------------
# Address validation
# ---------------------------------------------------------------------------
def test_invalid_address_rejected():
    with pytest.raises(od.AddressValidationError) as ei:
        od.validate_contract_address('0x123')
    assert ei.value.code == 'invalid_address_format'


def test_zero_address_rejected():
    with pytest.raises(od.AddressValidationError) as ei:
        od.validate_contract_address(od.ZERO_ADDRESS)
    assert ei.value.code == 'zero_address'


def test_discovery_rejects_invalid_address():
    res = od.discover_contract(erc20_transport(), address='not-an-address', selected_chain_id=8453)
    assert res.ok is False
    assert res.error_code == 'invalid_address_format'


# ---------------------------------------------------------------------------
# Standard detection
# ---------------------------------------------------------------------------
def test_erc20_detected():
    res = od.discover_contract(erc20_transport(), address=CONTRACT, selected_chain_id=8453)
    assert res.ok is True
    fmap = res.finding_map()
    assert fmap['token_standard'].value == 'ERC-20'
    assert fmap['token_standard'].confidence == od.PROBABLE
    assert fmap['token_symbol'].value == 'USDC'
    assert fmap['token_symbol'].confidence == od.CONFIRMED
    assert fmap['token_decimals'].value == 6
    assert fmap['owner_address'].value == '0x1111111111111111111111111111111111111111'
    assert fmap['pausable'].value == 'Pausable'
    assert fmap['mint_capability'].value == 'Mint'


def test_erc1155_detected_via_erc165():
    calls = {od.SELECTORS['supportsInterface']: None}  # default revert

    def supports(interface_hex_true):
        def _call(method, params, timeout=None):
            return None
        return _call

    class ERC1155Transport(FakeTransport):
        def call(self, method, params, timeout=None):
            if method == 'eth_call' and params[0]['data'][:10] == od.SELECTORS['supportsInterface']:
                iface = '0x' + params[0]['data'][10:18]
                if iface == od.INTERFACE_IDS['ERC1155']:
                    return enc_bool(True)
                return enc_bool(False)
            return super().call(method, params, timeout)

    t = ERC1155Transport(code='0x' + od.SELECTORS['balanceOf'][2:])
    res = od.discover_contract(t, address=CONTRACT, selected_chain_id=8453)
    assert res.ok is True
    fmap = res.finding_map()
    assert fmap['token_standard'].value == 'ERC-1155'
    assert fmap['token_standard'].confidence == od.CONFIRMED


def test_erc4626_detected():
    code = '0x' + od.SELECTORS['transfer'][2:] + od.SELECTORS['balanceOf'][2:]
    calls = {
        od.SELECTORS['totalSupply']: enc_uint(1000),
        od.SELECTORS['asset']: enc_address('0x2222222222222222222222222222222222222222'),
        od.SELECTORS['totalAssets']: enc_uint(999),
        od.SELECTORS['supportsInterface']: enc_bool(False),
    }
    res = od.discover_contract(FakeTransport(code=code, calls=calls), address=CONTRACT, selected_chain_id=8453)
    fmap = res.finding_map()
    assert fmap['token_standard'].value == 'ERC-4626'
    assert fmap['vault_asset'].value == '0x2222222222222222222222222222222222222222'


# ---------------------------------------------------------------------------
# Proxy resolution
# ---------------------------------------------------------------------------
def test_transparent_proxy_resolved():
    impl = '0x3333333333333333333333333333333333333333'
    admin = '0x4444444444444444444444444444444444444444'
    storage = {
        od.EIP1967_IMPLEMENTATION_SLOT: slot_address(impl),
        od.EIP1967_ADMIN_SLOT: slot_address(admin),
    }
    res = od.discover_contract(erc20_transport(storage=storage), address=CONTRACT, selected_chain_id=8453)
    fmap = res.finding_map()
    assert fmap['proxy_type'].value == 'transparent'
    assert fmap['proxy_type'].confidence == od.CONFIRMED
    assert fmap['implementation_address'].value == impl
    assert fmap['proxy_admin'].value == admin


def test_uups_proxy_resolved():
    impl = '0x5555555555555555555555555555555555555555'
    code = '0x' + od.SELECTORS['upgradeTo'][2:] + od.SELECTORS['transfer'][2:] + od.SELECTORS['balanceOf'][2:]
    storage = {od.EIP1967_IMPLEMENTATION_SLOT: slot_address(impl)}
    calls = {od.SELECTORS['supportsInterface']: enc_bool(False), od.SELECTORS['totalSupply']: enc_uint(1)}
    res = od.discover_contract(FakeTransport(code=code, storage=storage, calls=calls),
                               address=CONTRACT, selected_chain_id=8453)
    fmap = res.finding_map()
    assert fmap['proxy_type'].value == 'uups'
    assert fmap['implementation_address'].value == impl


# ---------------------------------------------------------------------------
# EOA / bytecode / chain
# ---------------------------------------------------------------------------
def test_eoa_returns_no_deployed_contract():
    res = od.discover_contract(FakeTransport(code='0x'), address=CONTRACT, selected_chain_id=8453)
    assert res.ok is False
    assert res.error_code == 'no_deployed_contract'


def test_wrong_chain_id_rejected():
    res = od.discover_contract(FakeTransport(chain_id=1), address=CONTRACT, selected_chain_id=8453)
    assert res.ok is False
    assert res.error_code == 'chain_mismatch'
    assert res.chain_id == 1


def test_no_selected_chain_accepts_returned_chain():
    res = od.discover_contract(erc20_transport(chain_id=8453), address=CONTRACT, selected_chain_id=None)
    assert res.ok is True
    assert res.chain_id == 8453


# ---------------------------------------------------------------------------
# RPC benchmark ranking
# ---------------------------------------------------------------------------
def _ep(host, transport):
    return od.BenchmarkEndpoint(host=host, redacted_url=f'https://{host}', transport=transport)


class SlowTransport(FakeTransport):
    def __init__(self, host, delay_ms, **kw):
        super().__init__(host=host, **kw)
        self._delay = delay_ms

    def timed_call(self, method, params, *, timeout=None):
        res = super().timed_call(method, params, timeout=timeout)
        res.latency_ms = self._delay
        return res


def test_slow_healthy_ranked_below_fast_healthy():
    fast = _ep('fast.rpc', SlowTransport('fast.rpc', 10, chain_id=8453, block=1000))
    slow = _ep('slow.rpc', SlowTransport('slow.rpc', 400, chain_id=8453, block=1000))
    results, summary = od.run_rpc_benchmark([slow, fast], selected_chain_id=8453, iterations=2)
    assert summary['primary_host'] == 'fast.rpc'
    assert summary['fallback_host'] == 'slow.rpc'
    by_host = {r.host: r for r in results}
    assert by_host['fast.rpc'].recommendation == 'primary'
    assert by_host['slow.rpc'].recommendation == 'fallback'


def test_fast_but_unreliable_not_primary():
    fast_good = _ep('good.rpc', SlowTransport('good.rpc', 50, chain_id=8453, block=1000))
    fast_flaky = _ep('flaky.rpc', SlowTransport('flaky.rpc', 5, chain_id=8453, block=1000,
                                                fail_methods={'eth_blockNumber': 'error', 'eth_getCode': 'error'}))
    results, summary = od.run_rpc_benchmark([fast_flaky, fast_good], selected_chain_id=8453,
                                            target_address=CONTRACT, iterations=3)
    assert summary['primary_host'] == 'good.rpc'
    by_host = {r.host: r for r in results}
    assert by_host['flaky.rpc'].recommendation in ('rejected', 'degraded')
    assert by_host['flaky.rpc'].recommendation != 'primary'
    assert by_host['flaky.rpc'].success_rate < 1.0


def test_block_lagging_endpoint_downgraded():
    leader = _ep('leader.rpc', SlowTransport('leader.rpc', 20, chain_id=8453, block=2000))
    lagger = _ep('lagger.rpc', SlowTransport('lagger.rpc', 20, chain_id=8453, block=1900))  # 100 blocks behind
    results, summary = od.run_rpc_benchmark([lagger, leader], selected_chain_id=8453, iterations=2)
    assert summary['primary_host'] == 'leader.rpc'
    by_host = {r.host: r for r in results}
    assert by_host['lagger.rpc'].block_lag == 100
    assert by_host['lagger.rpc'].recommendation != 'primary'


def test_chain_mismatch_endpoint_rejected():
    right = _ep('right.rpc', SlowTransport('right.rpc', 20, chain_id=8453, block=1000))
    wrong = _ep('wrong.rpc', SlowTransport('wrong.rpc', 5, chain_id=1, block=99999))
    results, summary = od.run_rpc_benchmark([wrong, right], selected_chain_id=8453, iterations=2)
    assert summary['primary_host'] == 'right.rpc'
    by_host = {r.host: r for r in results}
    assert by_host['wrong.rpc'].recommendation == 'rejected'
    assert by_host['wrong.rpc'].chain_id_ok is False


def test_rate_limited_endpoint_not_primary():
    healthy = _ep('healthy.rpc', SlowTransport('healthy.rpc', 30, chain_id=8453, block=1000))
    limited = _ep('limited.rpc', SlowTransport('limited.rpc', 5, chain_id=8453, block=1000, rate_limited=True))
    results, summary = od.run_rpc_benchmark([limited, healthy], selected_chain_id=8453, iterations=2)
    assert summary['primary_host'] == 'healthy.rpc'
    by_host = {r.host: r for r in results}
    assert by_host['limited.rpc'].rate_limited is True
    assert by_host['limited.rpc'].recommendation == 'rejected'


def test_provider_timeout_does_not_stop_benchmark():
    healthy = _ep('healthy.rpc', SlowTransport('healthy.rpc', 30, chain_id=8453, block=1000))
    dead = _ep('dead.rpc', SlowTransport('dead.rpc', 10, chain_id=8453,
                                         fail_methods={'eth_chainId': 'timeout', 'eth_blockNumber': 'timeout',
                                                       'eth_getCode': 'timeout'}))
    results, summary = od.run_rpc_benchmark([dead, healthy], selected_chain_id=8453,
                                            target_address=CONTRACT, iterations=2)
    assert summary['primary_host'] == 'healthy.rpc'
    by_host = {r.host: r for r in results}
    assert by_host['dead.rpc'].timeout_count > 0
    assert by_host['dead.rpc'].recommendation == 'rejected'
    # The whole benchmark still produced a usable primary.
    assert summary['explanation'] and 'healthy.rpc' in summary['explanation']


def test_explanation_references_measured_facts():
    fast = _ep('alchemy.test', SlowTransport('alchemy.test', 12, chain_id=8453, block=1000))
    results, summary = od.run_rpc_benchmark([fast], selected_chain_id=8453, iterations=2)
    assert 'alchemy.test' in summary['explanation']
    assert 'chain id' in summary['explanation']


# ---------------------------------------------------------------------------
# SSRF guard + redaction
# ---------------------------------------------------------------------------
@pytest.mark.parametrize('url', [
    'http://localhost:8545',
    'https://127.0.0.1/rpc',
    'https://169.254.169.254/latest/meta-data',
    'https://10.0.0.5:8545',
    'https://192.168.1.10/rpc',
    'ftp://example.com/rpc',
])
def test_ssrf_guard_blocks_private_and_metadata(url, monkeypatch):
    monkeypatch.delenv('ONBOARDING_ALLOW_PRIVATE_RPC', raising=False)
    with pytest.raises(od.SsrfValidationError):
        od.validate_rpc_url(url)


def test_ssrf_guard_allows_public_https(monkeypatch):
    monkeypatch.delenv('ONBOARDING_ALLOW_PRIVATE_RPC', raising=False)
    # Mock DNS so the test is deterministic offline: resolve to a routable public IP.
    monkeypatch.setattr(od.socket, 'getaddrinfo',
                        lambda *a, **k: [(2, 1, 6, '', ('93.184.216.34', 443))])
    host, redacted = od.validate_rpc_url('https://mainnet.base.org/rpc')
    assert host == 'mainnet.base.org'
    assert 'mainnet.base.org' in redacted


def test_ssrf_guard_allows_private_when_explicitly_enabled(monkeypatch):
    monkeypatch.setenv('ONBOARDING_ALLOW_PRIVATE_RPC', 'true')
    host, redacted = od.validate_rpc_url('http://localhost:8545')
    assert host == 'localhost'


def test_redact_rpc_url_strips_api_key():
    redacted = od.redact_rpc_url('https://base-mainnet.g.alchemy.com/v2/AbCdEf0123456789Secret')
    assert 'AbCdEf0123456789Secret' not in redacted
    assert '***' in redacted
    assert 'base-mainnet.g.alchemy.com' in redacted
    assert '/v2' in redacted  # short, meaningful segment preserved


def test_redact_rpc_url_strips_query_secret():
    redacted = od.redact_rpc_url('https://rpc.example.com/?apikey=SUPERSECRETVALUE12345')
    assert 'SUPERSECRETVALUE12345' not in redacted
    assert redacted.endswith('?***')
