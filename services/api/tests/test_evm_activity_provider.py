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


def test_eth_getBlockByNumber_params_are_hex_and_full_tx(monkeypatch):
    """eth_getBlockByNumber must be called with [hex_block_number, True] (full transactions)."""
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'ethereum')

    calls: list[tuple[str, list]] = []

    class _RecordingRpc:
        def call(self, method: str, params: list) -> object:
            calls.append((method, params))
            if method == 'eth_blockNumber':
                return hex(110)
            if method == 'eth_getLogs':
                return []
            if method == 'eth_getBlockByNumber':
                return {'hash': '0xblk', 'timestamp': hex(1_700_000_000), 'transactions': []}
            return {}

    target = {
        'id': 'target-params-test',
        'target_type': 'wallet',
        'chain_network': 'ethereum',
        'wallet_address': '0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
    }
    fetch_evm_activity(target, None, rpc_client=_RecordingRpc())

    block_calls = [(m, p) for m, p in calls if m == 'eth_getBlockByNumber']
    assert block_calls, 'eth_getBlockByNumber must have been called'
    for _, params in block_calls:
        assert len(params) == 2, 'eth_getBlockByNumber must have exactly 2 params'
        block_arg = params[0]
        full_tx_arg = params[1]
        assert isinstance(block_arg, str) and block_arg.startswith('0x'), (
            f'first param must be a hex string, got {block_arg!r}'
        )
        assert int(block_arg, 16) >= 0, 'hex block number must be non-negative'
        assert full_tx_arg is True, 'second param must be True to get full transaction objects'


def test_block_fetch_failure_logs_exception_and_continues(monkeypatch, caplog):
    """A single block fetch failure must be logged and scanning continues for remaining blocks."""
    import logging
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'ethereum')

    fail_block = 90
    succeed_block = 95
    blocks_fetched: list[int] = []

    class _FailOneRpc:
        def call(self, method: str, params: list) -> object:
            if method == 'eth_blockNumber':
                return hex(110)
            if method == 'eth_getLogs':
                return []
            if method == 'eth_getBlockByNumber':
                block_num = int(str(params[0]), 16)
                if block_num == fail_block:
                    raise ConnectionError(f'RPC timeout on block {block_num}')
                blocks_fetched.append(block_num)
                return {'hash': f'0xblk{block_num}', 'timestamp': hex(1_700_000_000 + block_num), 'transactions': []}
            return {}

    target = {
        'id': 'target-fail-test',
        'target_type': 'wallet',
        'chain_network': 'ethereum',
        'wallet_address': '0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
    }
    with caplog.at_level(logging.WARNING, logger='services.api.app.evm_activity_provider'):
        fetch_evm_activity(target, None, rpc_client=_FailOneRpc())

    log_text = '\n'.join(r.getMessage() for r in caplog.records)
    assert 'evm_block_fetch_failed' in log_text, 'must log evm_block_fetch_failed on block error'
    assert str(fail_block) in log_text, 'failed block number must appear in log'
    assert 'ConnectionError' in log_text or 'RPC timeout' in log_text, 'error detail must be logged'
    assert succeed_block in blocks_fetched, 'scanning must continue past the failed block'


def test_provider_error_sets_source_type_rpc_polling(monkeypatch):
    """When fetch_evm_activity raises, ActivityProviderResult.source_type must be rpc_polling."""
    from services.api.app.activity_providers import fetch_target_activity_result
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MODE_ENABLED', 'true')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'ethereum')

    def _raise(*_args, **_kwargs):
        raise RuntimeError('simulated RPC network failure')

    monkeypatch.setattr('services.api.app.activity_providers.fetch_evm_activity', _raise)

    target = {
        'id': 'target-err',
        'target_type': 'wallet',
        'chain_network': 'ethereum',
        'wallet_address': '0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
    }
    result = fetch_target_activity_result(target, None)
    assert result.status == 'failed'
    assert result.source_type == 'rpc_polling', (
        f'source_type must be rpc_polling on provider error, got {result.source_type!r}'
    )
    assert result.degraded_reason == 'provider_error'


def test_outbound_wallet_transfer_fields_present(monkeypatch):
    """An outbound transaction from the monitored wallet must yield all required telemetry fields."""
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'ethereum')

    wallet = '0x5f6f35fd8b10c5576089f99c7c8c351deb851d1f'
    counterparty = '0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef'
    tx_hash = '0xaabbccdd' + '00' * 28

    class _OutboundRpc:
        def call(self, method: str, params: list) -> object:
            if method == 'eth_blockNumber':
                return hex(1000)
            if method == 'eth_getLogs':
                return []
            if method == 'eth_getBlockByNumber':
                block_num = int(str(params[0]), 16)
                return {
                    'hash': f'0xblk{block_num}',
                    'timestamp': hex(1_700_000_000 + block_num),
                    'transactions': [
                        {
                            'hash': tx_hash,
                            'from': wallet,
                            'to': counterparty,
                            'value': hex(1_000_000_000_000_000_000),
                            'input': '0x',
                            'blockNumber': hex(block_num),
                            'blockHash': f'0xblk{block_num}',
                        }
                    ],
                }
            return {}

    target = {
        'id': 'target-outbound',
        'target_type': 'wallet',
        'chain_network': 'ethereum',
        'wallet_address': wallet,
    }
    events = fetch_evm_activity(target, None, rpc_client=_OutboundRpc())
    assert events, 'must detect at least one event'
    tx_events = [e for e in events if e.payload.get('tx_hash') == tx_hash]
    assert tx_events, f'no event with tx_hash={tx_hash}'
    payload = tx_events[0].payload
    assert payload.get('tx_hash') == tx_hash
    assert str(payload.get('from') or '').lower() == wallet
    assert str(payload.get('to') or '').lower() == counterparty
    assert payload.get('block_number') is not None
    assert payload.get('wallet_transfer_direction') == 'outbound'
    assert payload.get('source_type') == 'rpc_polling'
    assert payload.get('chain_id') == 1  # ethereum in this fixture
    assert payload.get('value_wei') == 1_000_000_000_000_000_000
    assert payload.get('value_eth') == 1.0


def test_inbound_wallet_transfer_detected(monkeypatch):
    """An inbound transaction to the monitored wallet must be detected with direction=inbound."""
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')
    monkeypatch.setenv('EVM_CHAIN_ID', '8453')

    wallet = '0x5f6f35fd8b10c5576089f99c7c8c351deb851d1f'
    counterparty = '0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef'
    tx_hash = '0x11223344' + '00' * 28

    class _InboundRpc:
        def call(self, method: str, params: list) -> object:
            if method == 'eth_chainId':
                return hex(8453)
            if method == 'eth_blockNumber':
                return hex(1000)
            if method == 'eth_getLogs':
                return []
            if method == 'eth_getBlockByNumber':
                block_num = int(str(params[0]), 16)
                return {
                    'hash': f'0xblk{block_num}',
                    'timestamp': hex(1_700_000_000 + block_num),
                    'transactions': [
                        {
                            'hash': tx_hash,
                            'from': counterparty,
                            'to': wallet.upper(),  # mixed case must still match
                            'value': hex(5_000_000_000_000_000_000),
                            'input': '0x',
                            'blockNumber': hex(block_num),
                            'blockHash': f'0xblk{block_num}',
                        }
                    ],
                }
            return {}

    target = {
        'id': 'target-inbound',
        'target_type': 'wallet',
        'chain_network': 'base',
        'wallet_address': wallet,
    }
    events = fetch_evm_activity(target, None, rpc_client=_InboundRpc())
    tx_events = [e for e in events if e.payload.get('tx_hash') == tx_hash]
    assert tx_events, f'no event with tx_hash={tx_hash}'
    payload = tx_events[0].payload
    assert payload.get('wallet_transfer_direction') == 'inbound'
    assert payload.get('chain_id') == 8453
    assert str(payload.get('to') or '').lower() == wallet
    assert payload.get('value_wei') == 5_000_000_000_000_000_000


def test_scanner_handles_empty_blocks(monkeypatch):
    """Blocks with no transactions must not crash and produce no wallet transfer events."""
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'ethereum')

    class _EmptyBlocksRpc:
        def call(self, method: str, params: list) -> object:
            if method == 'eth_blockNumber':
                return hex(500)
            if method == 'eth_getLogs':
                return []
            if method == 'eth_getBlockByNumber':
                block_num = int(str(params[0]), 16)
                return {'hash': f'0xblk{block_num}', 'timestamp': hex(1_700_000_000 + block_num), 'transactions': []}
            return {}

    target = {
        'id': 'target-empty',
        'target_type': 'wallet',
        'chain_network': 'ethereum',
        'wallet_address': '0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
    }
    events = fetch_evm_activity(target, None, rpc_client=_EmptyBlocksRpc())
    assert events == [], 'empty blocks must yield no wallet transfer events'


def test_eth_getLogs_failure_is_non_fatal_and_block_scan_continues(monkeypatch, caplog):
    """eth_getLogs failure must NOT collapse the scan into provider_error.

    This is the production blocker: Base RPC rejects the eth_getLogs topic filter,
    which previously propagated out of fetch_evm_activity and was reported as
    generic provider_error. The block-by-block transaction scan must still run and
    detect the wallet transfer, and evm_block_scan_summary must be logged.
    """
    import logging
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'base')
    monkeypatch.setenv('EVM_CHAIN_ID', '8453')

    wallet = '0x5f6f35fd8b10c5576089f99c7c8c351deb851d1f'
    tx_hash = '0xfeedface' + '00' * 28

    class _LogsFailRpc:
        def call(self, method: str, params: list) -> object:
            if method == 'eth_chainId':
                return hex(8453)
            if method == 'eth_blockNumber':
                return hex(2000)
            if method == 'eth_getLogs':
                raise RuntimeError('json-rpc error: query exceeds max block range')
            if method == 'eth_getBlockByNumber':
                block_num = int(str(params[0]), 16)
                return {
                    'hash': f'0xblk{block_num}',
                    'timestamp': hex(1_700_000_000 + block_num),
                    'transactions': [
                        {
                            'hash': tx_hash,
                            'from': wallet,
                            'to': '0xdeadbeefdeadbeefdeadbeefdeadbeefdeadbeef',
                            'value': hex(7),
                            'input': '0x',
                            'blockNumber': hex(block_num),
                            'blockHash': f'0xblk{block_num}',
                        }
                    ],
                }
            return {}

    target = {
        'id': 'target-logs-fail',
        'target_type': 'wallet',
        'chain_network': 'base',
        'wallet_address': wallet,
    }
    with caplog.at_level(logging.INFO, logger='services.api.app.evm_activity_provider'):
        events = fetch_evm_activity(target, None, rpc_client=_LogsFailRpc())

    # The scan did not raise; the wallet transfer was still detected via the tx scan.
    tx_events = [e for e in events if e.payload.get('tx_hash') == tx_hash]
    assert tx_events, 'wallet transfer must be detected even when eth_getLogs fails'
    assert tx_events[0].payload.get('wallet_transfer_direction') == 'outbound'

    log_text = '\n'.join(r.getMessage() for r in caplog.records)
    assert 'evm_logs_fetch_failed' in log_text, 'eth_getLogs failure must be logged with detail'
    assert 'evm_block_scan_summary' in log_text, 'scan_start must be followed by scan_summary'
    assert 'logs_fetch_status=failed' in log_text


def test_evm_block_scan_summary_reports_failed_blocks(monkeypatch, caplog):
    """evm_block_scan_summary must report failed_blocks while continuing the scan."""
    import logging
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'ethereum')

    fail_block = 480

    class _OneBlockFailsRpc:
        def call(self, method: str, params: list) -> object:
            if method == 'eth_blockNumber':
                return hex(500)
            if method == 'eth_getLogs':
                return []
            if method == 'eth_getBlockByNumber':
                block_num = int(str(params[0]), 16)
                if block_num == fail_block:
                    raise TimeoutError('rpc read timeout')
                return {'hash': f'0xblk{block_num}', 'timestamp': hex(1_700_000_000 + block_num), 'transactions': []}
            return {}

    target = {
        'id': 'target-failed-block-summary',
        'target_type': 'wallet',
        'chain_network': 'ethereum',
        'wallet_address': '0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
    }
    with caplog.at_level(logging.INFO, logger='services.api.app.evm_activity_provider'):
        fetch_evm_activity(target, None, rpc_client=_OneBlockFailsRpc())

    summary_msgs = [r.getMessage() for r in caplog.records if 'evm_block_scan_summary' in r.getMessage()]
    assert summary_msgs, 'evm_block_scan_summary must be logged'
    assert str(fail_block) in summary_msgs[0], 'failed block number must appear in summary failed_blocks'
    assert 'source_type=rpc_polling' in summary_msgs[0]


def test_provider_error_logs_exact_exception(monkeypatch, caplog):
    """A provider error must log the exact exception class and message, not a bare provider_error."""
    import logging
    from services.api.app.activity_providers import fetch_target_activity_result
    monkeypatch.setenv('EVM_RPC_URL', 'http://rpc')
    monkeypatch.setenv('LIVE_MODE_ENABLED', 'true')
    monkeypatch.setenv('LIVE_MONITORING_CHAINS', 'ethereum')

    def _raise(*_args, **_kwargs):
        raise ValueError('boom: malformed rpc response')

    monkeypatch.setattr('services.api.app.activity_providers.fetch_evm_activity', _raise)

    target = {
        'id': 'target-exact-exc',
        'target_type': 'wallet',
        'chain_network': 'ethereum',
        'wallet_address': '0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
    }
    with caplog.at_level(logging.ERROR, logger='services.api.app.activity_providers'):
        result = fetch_target_activity_result(target, None)

    assert result.error_code == 'ValueError'
    log_text = '\n'.join(r.getMessage() for r in caplog.records)
    assert 'evm_provider_error' in log_text
    assert 'ValueError' in log_text
    assert 'boom: malformed rpc response' in log_text
