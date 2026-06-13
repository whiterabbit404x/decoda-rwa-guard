"""
Chain/RPC routing correctness for tokenized-asset monitoring.

Regression coverage for the production incident where an Ethereum-labeled target
was served by a Base RPC, causing chain=ethereum / chain_id=1 telemetry to be
written with Base block heights (~47.2M).

Covers:
  A. resolve_chain_rpc routes each chain to its own RPC env var.
  B. A Base target uses the Base RPC and writes chain_id=8453.
  C. An Ethereum target never uses a Base RPC accidentally (fail closed).
  D. A chain mismatch yields a misconfigured result with no block number, so
     coverage telemetry is never persisted.
  E. A correctly-routed Base wallet target emits a fresh coverage heartbeat.
  F. The per-target chain/RPC routing log line carries the required fields.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from services.api.app.evm_activity_provider import fetch_evm_activity, resolve_chain_rpc


BASE_CHAIN_ID = 8453
ETH_CHAIN_ID = 1
BASE_BLOCK = 47_268_900  # realistic Base mainnet height (June 2026)
ETH_BLOCK = 21_000_000   # realistic Ethereum mainnet height
WALLET = '0x5f6f35fd8b10c5576089f99c7c8c351deb851d1f'
COUNTERPARTY = '0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef'

_RPC_ENV_VARS = (
    'EVM_RPC_URL', 'STAGING_EVM_RPC_URL',
    'EVM_RPC_URL_1', 'EVM_RPC_URL_8453', 'EVM_RPC_URL_42161',
    'ETHEREUM_EVM_RPC_URL', 'ETH_EVM_RPC_URL', 'BASE_EVM_RPC_URL',
    'ARBITRUM_EVM_RPC_URL', 'ARB_EVM_RPC_URL',
    'EVM_RPC_FAILOVER_URLS', 'EVM_RPC_FAILOVER_URLS_1', 'EVM_RPC_FAILOVER_URLS_8453',
    'EVM_CHAIN_ID', 'STAGING_EVM_CHAIN_ID', 'LIVE_MONITORING_CHAINS', 'EVM_WS_URL',
)


def _clear_rpc_env(monkeypatch):
    for name in _RPC_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _offline_cycle_telemetry(monkeypatch):
    # Keep tests offline: never reach out to market/oracle providers during a scan.
    monkeypatch.setattr('services.api.app.evm_activity_provider._fetch_market_observations', lambda target: [])
    monkeypatch.setattr('services.api.app.evm_activity_provider._fetch_oracle_observations', lambda target: [])


class _ChainRpc:
    """Mock JSON-RPC client serving a single chain at a single block height.

    Records the methods it was called with so tests can assert that the chain
    gate ran (eth_chainId) and that scanning did/did not happen
    (eth_getBlockByNumber).
    """

    def __init__(self, *, chain_id: int, latest_block: int, tx: dict | None = None) -> None:
        self._chain_id = chain_id
        self._latest = latest_block
        self._tx = tx
        self.methods: list[str] = []

    def call(self, method: str, params: list) -> object:
        self.methods.append(method)
        if method == 'eth_chainId':
            return hex(self._chain_id)
        if method == 'eth_blockNumber':
            return hex(self._latest)
        if method == 'eth_getLogs':
            return []
        if method == 'eth_getBlockByNumber':
            block_num = int(str(params[0]), 16)
            txs = [self._tx] if (self._tx and block_num == self._latest) else []
            return {
                'hash': f'0xblk{block_num}',
                'timestamp': hex(int(datetime(2026, 6, 13, tzinfo=timezone.utc).timestamp())),
                'transactions': txs,
            }
        return {}


def _wallet_tx(tx_hash: str) -> dict:
    return {
        'hash': tx_hash,
        'from': WALLET,
        'to': COUNTERPARTY,
        'value': hex(10 ** 17),
        'input': '0x',
        'blockHash': '0xblk',
    }


def _wallet_target(network: str) -> dict:
    return {
        'id': str(uuid.uuid4()),
        'workspace_id': str(uuid.uuid4()),
        'chain_network': network,
        'target_type': 'wallet',
        'wallet_address': WALLET,
        'contract_identifier': None,
    }


def _scan_env(monkeypatch):
    monkeypatch.setenv('EVM_CONFIRMATIONS_REQUIRED', '0')
    monkeypatch.setenv('MONITOR_REPLAY_BLOCKS', '1')
    monkeypatch.setenv('MONITOR_BATCH_BLOCKS', '1')


# ---------------------------------------------------------------------------
# A. resolve_chain_rpc routing precedence
# ---------------------------------------------------------------------------

def test_resolve_chain_rpc_routes_base_to_base_rpc(monkeypatch):
    _clear_rpc_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL_8453', 'https://base.example/rpc')
    monkeypatch.setenv('EVM_RPC_URL', 'https://global.example/rpc')  # global must NOT win for base
    info = resolve_chain_rpc('base')
    assert info['expected_chain_id'] == 8453
    assert info['rpc_url'] == 'https://base.example/rpc'
    assert info['rpc_url_env'] == 'EVM_RPC_URL_8453'
    assert info['rpc_urls'] == ['https://base.example/rpc']


def test_resolve_chain_rpc_named_alias_for_base(monkeypatch):
    _clear_rpc_env(monkeypatch)
    monkeypatch.setenv('BASE_EVM_RPC_URL', 'https://base-alias.example/rpc')
    info = resolve_chain_rpc('base-mainnet')
    assert info['expected_chain_id'] == 8453
    assert info['rpc_url'] == 'https://base-alias.example/rpc'
    assert info['rpc_url_env'] == 'BASE_EVM_RPC_URL'


def test_resolve_chain_rpc_routes_ethereum_to_eth_rpc_not_base(monkeypatch):
    _clear_rpc_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL_1', 'https://eth.example/rpc')
    monkeypatch.setenv('EVM_RPC_URL_8453', 'https://base.example/rpc')
    info = resolve_chain_rpc('ethereum')
    assert info['expected_chain_id'] == 1
    assert info['rpc_url'] == 'https://eth.example/rpc'
    assert info['rpc_url_env'] == 'EVM_RPC_URL_1'


def test_resolve_chain_rpc_falls_back_to_global(monkeypatch):
    _clear_rpc_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', 'https://global.example/rpc')
    info = resolve_chain_rpc('ethereum')
    assert info['rpc_url'] == 'https://global.example/rpc'
    assert info['rpc_url_env'] == 'EVM_RPC_URL'


def test_resolve_chain_rpc_unknown_network_has_no_expected_chain(monkeypatch):
    _clear_rpc_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', 'https://global.example/rpc')
    info = resolve_chain_rpc('zk-custom')
    assert info['expected_chain_id'] is None


# ---------------------------------------------------------------------------
# B. Base target uses Base RPC and writes chain_id=8453
# ---------------------------------------------------------------------------

def test_base_target_uses_base_rpc_and_chain_id_8453(monkeypatch):
    _clear_rpc_env(monkeypatch)
    _scan_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL_8453', 'https://base.example/rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')

    tx_hash = '0x' + 'ab' * 32
    rpc = _ChainRpc(chain_id=BASE_CHAIN_ID, latest_block=BASE_BLOCK, tx=_wallet_tx(tx_hash))
    events = fetch_evm_activity(_wallet_target('base'), None, rpc_client=rpc)

    tx_events = [e for e in events if e.payload.get('tx_hash') == tx_hash]
    assert tx_events, 'Base wallet transfer must be detected'
    assert tx_events[0].payload.get('chain_id') == 8453
    assert 'eth_chainId' in rpc.methods, 'chain gate must verify the RPC chain'
    assert tx_events[0].payload.get('block_number') <= BASE_BLOCK


# ---------------------------------------------------------------------------
# C. Ethereum target never uses a Base RPC accidentally (fail closed)
# ---------------------------------------------------------------------------

def test_ethereum_target_does_not_use_base_rpc(monkeypatch):
    """Only a global Base RPC is configured. An ethereum-labeled target must fail
    closed (return []) instead of scanning Base and writing chain_id=1 telemetry
    with Base block heights."""
    _clear_rpc_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL', 'https://base-only.example/rpc')  # global == Base
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'ethereum')  # ethereum is allow-listed

    rpc = _ChainRpc(chain_id=BASE_CHAIN_ID, latest_block=BASE_BLOCK, tx=_wallet_tx('0x' + 'cd' * 32))
    events = fetch_evm_activity(_wallet_target('ethereum'), None, rpc_client=rpc)

    assert events == [], 'ethereum target served by a Base RPC must fail closed'
    assert 'eth_getBlockByNumber' not in rpc.methods, 'must not scan blocks on chain mismatch'


def test_ethereum_target_with_ethereum_rpc_proceeds(monkeypatch):
    """Correct routing: an ethereum RPC (chain_id=1) lets the scan run and emit
    chain_id=1 telemetry."""
    _clear_rpc_env(monkeypatch)
    _scan_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL_1', 'https://eth.example/rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'ethereum')

    tx_hash = '0x' + 'ef' * 32
    rpc = _ChainRpc(chain_id=ETH_CHAIN_ID, latest_block=ETH_BLOCK, tx=_wallet_tx(tx_hash))
    events = fetch_evm_activity(_wallet_target('ethereum'), None, rpc_client=rpc)

    tx_events = [e for e in events if e.payload.get('tx_hash') == tx_hash]
    assert tx_events, 'ethereum target on an ethereum RPC must scan and detect'
    assert tx_events[0].payload.get('chain_id') == 1


# ---------------------------------------------------------------------------
# D + E. Coverage path: mismatch blocks persistence; correct chain heartbeats
# ---------------------------------------------------------------------------

def _live_env(monkeypatch):
    _clear_rpc_env(monkeypatch)
    monkeypatch.setenv('MONITORING_INGESTION_MODE', 'live')
    monkeypatch.setenv('LIVE_MONITORING_ENABLED', 'true')
    monkeypatch.setenv('STAGING_EVM_RPC_URL', 'https://staging.rpc/key')


def _base_probe(block: int = BASE_BLOCK) -> dict:
    return {
        'ok': True, 'chain_id_hex': '0x2105', 'chain_id_int': 8453,
        'block_number_hex': hex(block), 'block_number_int': block, 'error': None,
    }


def test_chain_mismatch_blocks_telemetry_persistence(monkeypatch):
    """An ethereum target whose RPC reports Base must yield a misconfigured result
    with no block number. The runner only persists coverage telemetry when
    status=='live' with a block, so this blocks the wrong-chain write."""
    from services.api.app import activity_providers

    _live_env(monkeypatch)
    monkeypatch.setattr(activity_providers, 'fetch_evm_activity', lambda *a, **k: [])
    monkeypatch.setattr(activity_providers, 'probe_rpc_health', lambda: _base_probe())

    target = {
        'id': str(uuid.uuid4()), 'workspace_id': str(uuid.uuid4()),
        'chain_network': 'ethereum', 'target_type': 'contract', 'contract_identifier': '0xabc',
    }
    result = activity_providers.fetch_target_activity_result(target, None)

    assert result.status == 'failed'
    assert result.reason_code == 'CHAIN_RPC_MISMATCH'
    assert result.latest_block is None, 'no block height may be carried on chain mismatch'
    assert result.evidence_present is False


def test_correct_chain_emits_fresh_coverage_heartbeat(monkeypatch):
    """A correctly-routed Base wallet target (e785-style) with no transfer events
    still emits a fresh coverage heartbeat (latest_block + checkpoint)."""
    from services.api.app import activity_providers

    _live_env(monkeypatch)
    monkeypatch.setattr(activity_providers, 'fetch_evm_activity', lambda *a, **k: [])
    monkeypatch.setattr(activity_providers, 'probe_rpc_health', lambda: _base_probe())

    target = {
        'id': 'e7851a52-8fb1-48cd-84a3-d033f591c5dd', 'workspace_id': str(uuid.uuid4()),
        'chain_network': 'base', 'target_type': 'wallet', 'wallet_address': WALLET,
    }
    result = activity_providers.fetch_target_activity_result(target, None)

    assert result.status == 'live'
    assert result.evidence_present is True
    assert result.latest_block == BASE_BLOCK
    assert result.checkpoint == f'coverage:{BASE_BLOCK}'
    assert result.checkpoint_age_seconds == 0


# ---------------------------------------------------------------------------
# F. Per-target chain/RPC routing log
# ---------------------------------------------------------------------------

def test_chain_routing_log_emitted_with_required_fields(monkeypatch, caplog):
    import logging

    _clear_rpc_env(monkeypatch)
    _scan_env(monkeypatch)
    monkeypatch.setenv('EVM_RPC_URL_8453', 'https://base.example/rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')

    rpc = _ChainRpc(chain_id=BASE_CHAIN_ID, latest_block=BASE_BLOCK)
    target = {
        'id': 'e7851a52', 'workspace_id': str(uuid.uuid4()),
        'chain_network': 'base', 'target_type': 'wallet', 'wallet_address': WALLET,
    }
    with caplog.at_level(logging.INFO, logger='services.api.app.evm_activity_provider'):
        fetch_evm_activity(target, None, rpc_client=rpc)

    routing = [r.getMessage() for r in caplog.records if 'evm_chain_routing' in r.getMessage()]
    assert routing, 'evm_chain_routing log line must be emitted'
    line = routing[0]
    assert 'target_id=e7851a52' in line
    assert 'configured_chain=base' in line
    assert 'resolved_chain_id=8453' in line
    assert 'rpc_url_env_used=EVM_RPC_URL_8453' in line
    assert f'latest_block={BASE_BLOCK}' in line
