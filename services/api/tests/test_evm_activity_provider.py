from __future__ import annotations

from datetime import datetime, timezone
import json

from services.api.app.evm_activity_provider import APPROVAL_TOPIC, TRANSFER_TOPIC, _fetch_market_observations, fetch_evm_activity


class _Rpc:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[object]]] = []
        self.logs_calls = 0

    def call(self, method: str, params: list[object]) -> object:
        self.calls.append((method, params))
        if method == 'eth_blockNumber':
            return hex(120)
        if method == 'eth_getLogs':
            self.logs_calls += 1
            if self.logs_calls == 1:
                return [
                    {
                        'transactionHash': '0xtx1',
                        'logIndex': hex(2),
                        'blockNumber': hex(117),
                        'blockHash': '0xblock117',
                        'address': '0xtoken',
                        'topics': [TRANSFER_TOPIC, '0x' + ('0' * 24) + '1111111111111111111111111111111111111111', '0x' + ('0' * 24) + 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'],
                        'data': hex(9),
                    }
                ]
            return [
                {
                    'transactionHash': '0xtx2',
                    'logIndex': hex(3),
                    'blockNumber': hex(118),
                    'blockHash': '0xblock118',
                    'address': '0xtoken',
                    'topics': [APPROVAL_TOPIC, '0x' + ('0' * 24) + 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', '0x' + ('0' * 24) + '2222222222222222222222222222222222222222'],
                    'data': hex(15),
                }
            ]
        if method == 'eth_getBlockByNumber':
            block_number = int(str(params[0]), 16)
            if block_number in {116, 117, 118}:
                return {
                    'hash': f'0xblock{block_number}',
                    'timestamp': hex(int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()) + block_number),
                    'transactions': [
                        {
                            'hash': f'0xcontracttx{block_number}',
                            'from': '0x1',
                            'to': '0xcccccccccccccccccccccccccccccccccccccccc',
                            'value': hex(0),
                            'input': '0x3659cfe6ffff',
                            'blockNumber': hex(block_number),
                            'blockHash': f'0xblock{block_number}',
                        }
                    ],
                }
            return {'hash': f'0xblock{block_number}', 'timestamp': hex(int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp())), 'transactions': []}
        if method == 'eth_getBlockByHash':
            return {'timestamp': hex(int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()))}
        if method == 'eth_getTransactionByHash':
            return {'hash': params[0], 'from': '0x1', 'to': '0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'value': hex(1), 'input': '0x095ea7b3aaaa'}
        return {}


def test_wallet_transfers_and_approvals_normalized(monkeypatch):
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'ethereum')
    monkeypatch.setenv('EVM_BLOCK_SCAN_CHUNK_SIZE', '2')
    target = {'id': 't1', 'target_type': 'wallet', 'chain_network': 'ethereum', 'wallet_address': '0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'}
    events = fetch_evm_activity(target, None, rpc_client=_Rpc())
    assert any(e.payload.get('kind_hint') == 'erc20_transfer' for e in events)
    assert any(e.payload.get('kind_hint') == 'erc20_approval' for e in events)
    assert all(e.ingestion_source in {'polling', 'rpc_backfill', 'websocket'} for e in events)


def test_contract_selector_decode_and_cursor(monkeypatch):
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('EVM_BLOCK_LOOKBACK', '5')
    rpc = _Rpc()
    target = {'id': 't2', 'target_type': 'contract', 'chain_network': 'ethereum', 'contract_identifier': '0xcccccccccccccccccccccccccccccccccccccccc'}
    events = fetch_evm_activity(target, None, rpc_client=rpc)
    assert events
    assert events[0].payload['function_selector'] == '0x3659cfe6'
    assert events[0].payload['decoded_function_name'] == 'upgradeTo'
    scanned_blocks = [int(str(params[0]), 16) for method, params in rpc.calls if method == 'eth_getBlockByNumber']
    assert min(scanned_blocks) <= 116
    assert 117 in scanned_blocks
    target['monitoring_checkpoint_cursor'] = events[-1].cursor
    later = fetch_evm_activity(target, None, rpc_client=_Rpc())
    assert later == []


def test_websocket_mode_selected_when_ws_head_available(monkeypatch):
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('EVM_WS_URL', 'ws://rpc')
    async def _ws_head(*_args, **_kwargs):
        return 120
    monkeypatch.setattr('services.api.app.evm_activity_provider._ws_subscribe_new_head', _ws_head)
    target = {'id': 't3', 'target_type': 'contract', 'chain_network': 'ethereum', 'contract_identifier': '0xcccccccccccccccccccccccccccccccccccccccc'}
    events = fetch_evm_activity(target, None, rpc_client=_Rpc())
    assert events
    assert any(event.ingestion_source == 'websocket' for event in events)


def test_polling_fallback_when_ws_unavailable(monkeypatch):
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('EVM_WS_URL', 'ws://rpc')
    async def _ws_none(*_args, **_kwargs):
        return None
    monkeypatch.setattr('services.api.app.evm_activity_provider._ws_subscribe_new_head', _ws_none)
    target = {'id': 't4', 'target_type': 'contract', 'chain_network': 'ethereum', 'contract_identifier': '0xcccccccccccccccccccccccccccccccccccccccc'}
    events = fetch_evm_activity(target, None, rpc_client=_Rpc())
    assert events
    assert all(event.ingestion_source in {'polling', 'rpc_backfill'} for event in events)


def test_market_observations_fail_closed_without_provider_config(monkeypatch):
    monkeypatch.delenv('MARKET_TELEMETRY_SOURCE_URLS', raising=False)
    observations = _fetch_market_observations({'asset_identifier': 'USTB'})
    assert observations
    assert observations[0]['status'] == 'insufficient_real_evidence'
    assert observations[0]['provider_status'] == 'no_provider_configured'


def test_market_observations_reads_external_provider(monkeypatch):
    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({'observations': [{'status': 'ok', 'rolling_volume': 123, 'source_name': 'market-a'}]}).encode('utf-8')

    monkeypatch.setenv('MARKET_TELEMETRY_SOURCE_URLS', 'market-a=http://market/api')
    monkeypatch.setattr('services.api.app.evm_activity_provider.request.urlopen', lambda *_args, **_kwargs: _Resp())
    observations = _fetch_market_observations({'asset_identifier': 'USTB'})
    assert observations
    assert observations[0]['status'] == 'ok'
    assert observations[0]['rolling_volume'] == 123
    assert observations[0]['provider_name'] == 'market-a'


# --- Wallet address extraction and Base transfer detection ---


WALLET = '0x5f6f3000000000000000000000000000000051d1'
OTHER = '0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef'
BASE_BLOCK = 47247942


class _BaseRpc:
    """Minimal RPC stub for Base wallet transfer tests."""

    def __init__(self, wallet: str = WALLET, block: int = BASE_BLOCK, *, is_outbound: bool = True) -> None:
        self.wallet = wallet.lower()
        self.block = block
        self.is_outbound = is_outbound
        self.blocks_fetched: list[int] = []

    def call(self, method: str, params: list[object]) -> object:
        if method == 'eth_blockNumber':
            return hex(self.block + 3)
        if method == 'eth_getLogs':
            return []
        if method == 'eth_getBlockByNumber':
            block_number = int(str(params[0]), 16)
            self.blocks_fetched.append(block_number)
            if block_number == self.block:
                tx_from = self.wallet if self.is_outbound else OTHER
                tx_to = OTHER if self.is_outbound else self.wallet
                return {
                    'hash': f'0xblock{block_number}',
                    'timestamp': hex(1749000000 + block_number),
                    'transactions': [{
                        'hash': '0xtestwalletxfer',
                        'from': tx_from,
                        'to': tx_to,
                        'value': hex(10 ** 17),
                        'input': '0x',
                        'blockNumber': hex(block_number),
                        'blockHash': f'0xblock{block_number}',
                    }],
                }
            return {'hash': f'0xblock{block_number}', 'timestamp': hex(1749000000), 'transactions': []}
        if method == 'eth_getBlockByHash':
            return {'timestamp': hex(1749000000)}
        if method == 'eth_chainId':
            return hex(8453)
        return {}


def test_wallet_target_missing_address_returns_empty_with_error_log(monkeypatch, caplog):
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')
    target = {'id': 'twallet-missing', 'target_type': 'wallet', 'chain_network': 'base', 'wallet_address': None}
    import logging
    with caplog.at_level(logging.ERROR, logger='services.api.app.evm_activity_provider'):
        events = fetch_evm_activity(target, None, rpc_client=_BaseRpc())
    assert events == []
    assert any('evm_wallet_target_missing_address' in r.message for r in caplog.records)
    assert any('wallet_address_required' in r.message for r in caplog.records)


def test_wallet_target_empty_string_address_returns_empty_with_error_log(monkeypatch, caplog):
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')
    target = {'id': 'twallet-empty', 'target_type': 'wallet', 'chain_network': 'base', 'wallet_address': ''}
    import logging
    with caplog.at_level(logging.ERROR, logger='services.api.app.evm_activity_provider'):
        events = fetch_evm_activity(target, None, rpc_client=_BaseRpc())
    assert events == []
    assert any('evm_wallet_target_missing_address' in r.message for r in caplog.records)


def test_outbound_base_transfer_detected(monkeypatch):
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')
    monkeypatch.setenv('MONITOR_REPLAY_BLOCKS', '5')
    target = {
        'id': 'twallet-out',
        'target_type': 'wallet',
        'chain_network': 'base',
        'wallet_address': WALLET,
    }
    rpc = _BaseRpc(wallet=WALLET, block=BASE_BLOCK, is_outbound=True)
    events = fetch_evm_activity(target, None, rpc_client=rpc)
    wallet_events = [e for e in events if isinstance(e.payload, dict) and e.payload.get('event_type') == 'transaction']
    assert wallet_events, 'Expected at least one transaction event'
    e = wallet_events[0]
    assert str(e.payload.get('tx_hash') or '') == '0xtestwalletxfer'
    assert str(e.payload.get('from') or '').lower() == WALLET.lower()
    assert e.payload.get('wallet_transfer_direction') == 'outbound'
    assert e.payload.get('source_type') == 'rpc_polling'


def test_inbound_base_transfer_detected(monkeypatch):
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')
    monkeypatch.setenv('MONITOR_REPLAY_BLOCKS', '5')
    target = {
        'id': 'twallet-in',
        'target_type': 'wallet',
        'chain_network': 'base',
        'wallet_address': WALLET,
    }
    rpc = _BaseRpc(wallet=WALLET, block=BASE_BLOCK, is_outbound=False)
    events = fetch_evm_activity(target, None, rpc_client=rpc)
    wallet_events = [e for e in events if isinstance(e.payload, dict) and e.payload.get('event_type') == 'transaction']
    assert wallet_events, 'Expected at least one transaction event'
    e = wallet_events[0]
    assert str(e.payload.get('to') or '').lower() == WALLET.lower()
    assert e.payload.get('wallet_transfer_direction') == 'inbound'


def test_non_matching_tx_ignored(monkeypatch):
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')
    monkeypatch.setenv('MONITOR_REPLAY_BLOCKS', '5')
    different_wallet = '0x' + 'a' * 40
    target = {
        'id': 'twallet-nomatch',
        'target_type': 'wallet',
        'chain_network': 'base',
        'wallet_address': different_wallet,
    }
    rpc = _BaseRpc(wallet=WALLET, block=BASE_BLOCK, is_outbound=True)
    events = fetch_evm_activity(target, None, rpc_client=rpc)
    tx_events = [e for e in events if isinstance(e.payload, dict) and e.payload.get('event_type') == 'transaction']
    assert tx_events == [], f'Expected no tx events for non-matching wallet, got: {tx_events}'


def test_tx_hash_in_wallet_transfer_payload(monkeypatch):
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')
    monkeypatch.setenv('MONITOR_REPLAY_BLOCKS', '5')
    target = {
        'id': 'twallet-hash',
        'target_type': 'wallet',
        'chain_network': 'base',
        'wallet_address': WALLET,
    }
    events = fetch_evm_activity(target, None, rpc_client=_BaseRpc(wallet=WALLET, block=BASE_BLOCK))
    tx_events = [e for e in events if isinstance(e.payload, dict) and e.payload.get('event_type') == 'transaction']
    assert tx_events
    assert str(tx_events[0].payload.get('tx_hash') or '') == '0xtestwalletxfer'
    assert str(tx_events[0].payload.get('block_number') or '') == str(BASE_BLOCK)


def test_block_range_scan_catches_tx_between_heartbeat_rows(monkeypatch):
    """A tx in block N is detected even when heartbeats exist for blocks N-1 and N+1."""
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')
    monkeypatch.setenv('MONITOR_REPLAY_BLOCKS', '10')
    target = {
        'id': 'twallet-range',
        'target_type': 'wallet',
        'chain_network': 'base',
        'wallet_address': WALLET,
        'monitoring_checkpoint_cursor': f'{BASE_BLOCK - 10}:checkpoint:-1',
    }
    rpc = _BaseRpc(wallet=WALLET, block=BASE_BLOCK, is_outbound=True)
    events = fetch_evm_activity(target, None, rpc_client=rpc)
    block_nums = [e.payload.get('block_number') for e in events if isinstance(e.payload, dict)]
    assert BASE_BLOCK in block_nums, f'Expected block {BASE_BLOCK} in results, got: {block_nums}'


def test_cursor_advances_only_after_events_found(monkeypatch):
    """When no matching tx exists, events list is empty and cursor should not advance."""
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')
    monkeypatch.setenv('MONITOR_REPLAY_BLOCKS', '5')
    different_wallet = '0x' + 'b' * 40
    target = {
        'id': 'twallet-cursor',
        'target_type': 'wallet',
        'chain_network': 'base',
        'wallet_address': different_wallet,
        'monitoring_checkpoint_cursor': f'{BASE_BLOCK - 5}:checkpoint:-1',
    }
    rpc = _BaseRpc(wallet=WALLET, block=BASE_BLOCK, is_outbound=True)
    events = fetch_evm_activity(target, None, rpc_client=rpc)
    tx_events = [e for e in events if isinstance(e.payload, dict) and e.payload.get('event_type') == 'transaction']
    assert tx_events == [], 'Non-matching tx should produce no cursor-advancing events'
