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


def test_market_observations_fail_closed_without_provider_config(monkeypatch):
    monkeypatch.delenv('MARKET_TELEMETRY_SOURCE_URLS', raising=False)
    observations = _fetch_market_observations({'asset_identifier': 'USTB'})
    assert observations
    assert observations[0]['status'] == 'insufficient_real_evidence'


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
