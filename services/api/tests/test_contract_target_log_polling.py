"""Contract-target polling via the stable Alchemy RPC path (Screen 4 live evidence).

The USDC monitor is a *contract* target (chain_network=base, contract_identifier=USDC).
Its on-chain activity is ERC-20 Transfer/Approval events that live in receipt LOGS keyed
by the emitting token contract — NOT only in transactions whose ``to`` is the contract.
Before this fix, ``fetch_evm_activity`` fetched eth_getLogs only for wallet targets, so a
contract target could only ever be detected via a direct ``tx.to == contract`` block scan
and router/DEX-mediated transfers were invisible.

Covers section 10 acceptance tests:
  #2  Contract targets do not require wallet-address resolution.
  #4  Contract polling calls the assigned provider with eth_getLogs (address filter).
  #11 A poll with no matching event still completes successfully (no exception).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from services.api.app.evm_activity_provider import (
    APPROVAL_TOPIC,
    TRANSFER_TOPIC,
    fetch_evm_activity,
)

# Base mainnet USDC — the production monitored contract (task PRODUCTION FACTS).
USDC_CONTRACT = '0x833589fcd6edb6e08f4c7c32d4f71b54bda02913'
BASE_LATEST_BLOCK = 1_000
BASE_CONFIRMATIONS = 1
BASE_SAFE_TO = BASE_LATEST_BLOCK - BASE_CONFIRMATIONS  # 999

# A holder-to-holder transfer of USDC routed through a DEX: the transaction's ``to`` is a
# router, NEVER the USDC contract, yet the USDC contract EMITS the Transfer log.
HOLDER_A = '0x1111111111111111111111111111111111111111'
HOLDER_B = '0x2222222222222222222222222222222222222222'
ROUTER = '0x9999999999999999999999999999999999999999'


def _topic_addr(addr: str) -> str:
    return '0x' + ('0' * 24) + addr[2:]


def _base_env(monkeypatch) -> None:
    monkeypatch.setenv('EVM_RPC_URL', 'http://base-rpc.example')
    monkeypatch.setenv('EVM_CHAIN_ID', '8453')
    monkeypatch.delenv('STAGING_EVM_CHAIN_ID', raising=False)
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')
    # Keep the block-by-block scan tiny and deterministic.
    monkeypatch.setenv('EVM_CONFIRMATIONS_REQUIRED', str(BASE_CONFIRMATIONS))
    monkeypatch.setenv('MONITOR_REPLAY_BLOCKS', '2')
    monkeypatch.setenv('EVM_BLOCK_LOOKBACK', '2')
    monkeypatch.setenv('MONITOR_BATCH_BLOCKS', '5')
    monkeypatch.delenv('EVM_WS_URL', raising=False)


def _make_contract_target() -> dict:
    # NB: no wallet_address — a contract target must poll without one.
    return {
        'id': str(uuid.uuid4()),
        'workspace_id': '4fffd3f9-d55f-456f-8a7e-8b9ed2083721',
        'name': 'USDC Monitor',
        'target_type': 'contract',
        'chain_network': 'base',
        'contract_identifier': USDC_CONTRACT,
        'wallet_address': None,
        # Start near the head so only a handful of blocks are scanned.
        'monitoring_checkpoint_cursor': f'{BASE_SAFE_TO - 3}:checkpoint:-1',
        'monitoring_interval_seconds': 300,
    }


class _ContractLogRpc:
    """RPC stub: the USDC contract emits ONE Transfer log; NO transaction is sent to it."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list]] = []
        self.getlogs_params: list[dict] = []
        self.transfer_block = BASE_SAFE_TO - 1

    def call(self, method: str, params: list) -> object:
        self.calls.append((method, params))
        if method == 'eth_chainId':
            return hex(8453)
        if method == 'eth_blockNumber':
            return hex(BASE_LATEST_BLOCK)
        if method == 'eth_getLogs':
            spec = params[0] if params else {}
            self.getlogs_params.append(spec)
            # Only the CONTRACT-address-filtered query returns the Transfer log; a
            # wallet-style topic-only query (no address) must return nothing here.
            if str(spec.get('address') or '').lower() == USDC_CONTRACT:
                return [
                    {
                        'transactionHash': '0xroutertx',
                        'logIndex': hex(4),
                        'blockNumber': hex(self.transfer_block),
                        'blockHash': f'0xblock{self.transfer_block}',
                        'address': USDC_CONTRACT,
                        'topics': [TRANSFER_TOPIC, _topic_addr(HOLDER_A), _topic_addr(HOLDER_B)],
                        'data': hex(5_000_000),  # 5 USDC (6 decimals)
                    }
                ]
            return []
        if method == 'eth_getBlockByNumber':
            block_number = int(str(params[0]), 16)
            # Every block routes through the DEX router — the USDC contract is NEVER tx.to.
            return {
                'hash': f'0xblock{block_number}',
                'number': hex(block_number),
                'timestamp': hex(int(datetime(2026, 7, 17, tzinfo=timezone.utc).timestamp()) + block_number),
                'transactions': [
                    {
                        'hash': '0xroutertx' if block_number == self.transfer_block else f'0xtx{block_number}',
                        'from': HOLDER_A,
                        'to': ROUTER,
                        'value': hex(0),
                        'input': '0x38ed1739',
                        'blockNumber': hex(block_number),
                        'blockHash': f'0xblock{block_number}',
                    }
                ],
            }
        if method == 'eth_getBlockByHash':
            return {'timestamp': hex(int(datetime(2026, 7, 17, tzinfo=timezone.utc).timestamp()))}
        if method == 'eth_getTransactionByHash':
            return {'hash': params[0], 'from': HOLDER_A, 'to': ROUTER, 'value': hex(0), 'input': '0x38ed1739'}
        return {}


def test_contract_target_detects_erc20_transfer_from_receipt_logs(monkeypatch):
    """USDC Transfer emitted by the contract is detected even when NO tx.to == contract."""
    _base_env(monkeypatch)
    rpc = _ContractLogRpc()
    target = _make_contract_target()

    events = fetch_evm_activity(target, None, rpc_client=rpc)

    transfer_events = [e for e in events if e.payload.get('kind_hint') == 'erc20_transfer']
    assert transfer_events, 'contract ERC-20 transfer must be detected from receipt logs'
    ev = transfer_events[0]
    assert ev.payload.get('contract_address') == USDC_CONTRACT
    assert ev.payload.get('event_type') == 'transfer'
    assert ev.payload.get('to') == HOLDER_B
    # The transfer's tx.to is the router, never the monitored contract — proving the
    # detection did NOT rely on transaction-level address matching.
    assert ev.payload.get('to') != USDC_CONTRACT


def test_contract_log_query_uses_address_filter_not_wallet_topics(monkeypatch):
    """The contract log scan must filter eth_getLogs by the emitting contract address."""
    _base_env(monkeypatch)
    rpc = _ContractLogRpc()
    target = _make_contract_target()

    fetch_evm_activity(target, None, rpc_client=rpc)

    address_filtered = [p for p in rpc.getlogs_params if str(p.get('address') or '').lower() == USDC_CONTRACT]
    assert address_filtered, 'contract polling must issue an address-filtered eth_getLogs'
    # Transfer + Approval are requested together via the OR topic filter.
    topics0 = address_filtered[0].get('topics', [[]])[0]
    assert TRANSFER_TOPIC in topics0
    assert APPROVAL_TOPIC in topics0


def test_contract_target_polls_without_wallet_address(monkeypatch):
    """A contract target must NOT require wallet-address resolution (no exception)."""
    _base_env(monkeypatch)
    rpc = _ContractLogRpc()
    target = _make_contract_target()
    assert target.get('wallet_address') is None

    # Must not raise MonitoredWalletNotConfigured — that guard is wallet-only.
    events = fetch_evm_activity(target, None, rpc_client=rpc)
    assert isinstance(events, list)


def test_contract_poll_with_no_matching_event_still_succeeds(monkeypatch):
    """No Transfer/Approval and no direct tx: the poll completes cleanly (empty list)."""
    _base_env(monkeypatch)

    class _QuietRpc(_ContractLogRpc):
        def call(self, method: str, params: list) -> object:
            if method == 'eth_getLogs':
                self.calls.append((method, params))
                self.getlogs_params.append(params[0] if params else {})
                return []
            if method == 'eth_getBlockByNumber':
                block_number = int(str(params[0]), 16)
                self.calls.append((method, params))
                return {
                    'hash': f'0xblock{block_number}',
                    'number': hex(block_number),
                    'timestamp': hex(int(datetime(2026, 7, 17, tzinfo=timezone.utc).timestamp()) + block_number),
                    'transactions': [],
                }
            return super().call(method, params)

    rpc = _QuietRpc()
    target = _make_contract_target()

    events = fetch_evm_activity(target, None, rpc_client=rpc)
    assert events == []
    # The address-filtered log scan still ran — a successful, evidence-eligible poll.
    assert any(str(p.get('address') or '').lower() == USDC_CONTRACT for p in rpc.getlogs_params)
