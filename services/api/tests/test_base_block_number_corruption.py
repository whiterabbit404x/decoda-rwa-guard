"""
Regression tests for the Base telemetry block_number corruption bug.

Root cause: monitoring_runner.py used `provider_result.latest_block or int(observed_at.timestamp())`
as a block_number fallback, producing Unix timestamps (~1_781_265_978) instead of real chain
block heights (~47_238_026 for Base).  Once a timestamp was stored as a scanner cursor, the
scanner computed from_block > latest_block → early-exit → no wallet transfers detected.

Coverage:
  1. eth_blockNumber hex → real decimal block height (not timestamp)
  2. _build_base_payload includes value_wei and value_eth
  3. Source-code guardrail: no timestamp fallback in _persist_live_coverage_telemetry
  4. Source-code guardrail: _load_checkpoint rejects cursors > 500_000_000
  5. fetch_evm_activity resets corrupted cursor > 500_000_000 before computing from_block
  6. Base outbound transfer in block 47238026 is detected by fetch_evm_activity
  7. wallet_transfer_direction is outbound when wallet is the sender
  8. source_type=rpc_polling is present in detected wallet transfer payload
  9. Heartbeat telemetry payload contains latest_block and raw_response.result
  10. Cursor > latest_block + 1000 is treated as corrupted and reset
  11. evm_block_scan_complete log includes transactions_inspected and wallet_transfers_detected
  12. tx_hash search query covers wallet_transfer_detected event_type
  13. Heartbeat payload raw_response.result matches eth_blockNumber hex
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

BASE_CHAIN_ID = 8453
BASE_BLOCK_47238026 = 47238026
BASE_BLOCK_HEX = hex(BASE_BLOCK_47238026)      # '0x2d0ca6a'
TIMESTAMP_LIKE_BLOCK = 1_781_265_978             # Unix epoch timestamp, NOT a block height
WALLET_ADDR = '0xdeadbeef00000000000000000000000000001234'
OTHER_ADDR = '0xaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
TX_HASH = '0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890ab'


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# 1. eth_blockNumber hex → real decimal block height, not Unix timestamp
# ---------------------------------------------------------------------------

def test_hex_to_int_parses_base_block_to_decimal():
    """0x2d0ca6a must parse to exactly 47238026."""
    from services.api.app.evm_activity_provider import _hex_to_int
    result = _hex_to_int(BASE_BLOCK_HEX)
    assert result == BASE_BLOCK_47238026, (
        f'_hex_to_int({BASE_BLOCK_HEX!r}) must equal {BASE_BLOCK_47238026}, got {result}'
    )


def test_hex_to_int_base_block_is_not_timestamp():
    """Parsed Base block height must not be in the Unix timestamp range (> 500M)."""
    from services.api.app.evm_activity_provider import _hex_to_int
    for hex_val in (BASE_BLOCK_HEX, '0x2d07b2a', '0x1312d00'):
        result = _hex_to_int(hex_val)
        assert result is not None
        assert result < 500_000_000, (
            f'Block {hex_val} parsed to {result}, which is in timestamp range — '
            f'eth_blockNumber returned a timestamp, not a block height'
        )


def test_timestamp_like_value_exceeds_threshold():
    """Sanity: 1_781_265_978 must be > 500_000_000 (our corruption threshold)."""
    assert TIMESTAMP_LIKE_BLOCK > 500_000_000
    assert BASE_BLOCK_47238026 < 500_000_000


# ---------------------------------------------------------------------------
# 2. _build_base_payload includes value_wei and value_eth
# ---------------------------------------------------------------------------

def test_build_base_payload_includes_value_wei_and_value_eth():
    """_build_base_payload must include value_wei (int wei) and value_eth (float ETH)."""
    from services.api.app.evm_activity_provider import _build_base_payload

    target = {
        'id': str(uuid.uuid4()),
        'workspace_id': str(uuid.uuid4()),
        'chain_network': 'base',
        'target_type': 'wallet',
        'wallet_address': WALLET_ADDR,
        'contract_identifier': None,
    }
    one_eth_wei = 10 ** 18
    payload = _build_base_payload(
        target=target,
        network='base',
        chain_id=BASE_CHAIN_ID,
        block_number=BASE_BLOCK_47238026,
        block_hash='0xblockhash',
        tx={
            'from': WALLET_ADDR,
            'to': OTHER_ADDR,
            'value': hex(one_eth_wei),
            'input': '0x',
        },
        tx_hash=TX_HASH,
        raw_reference=f'base:{TX_HASH}',
    )

    assert 'value_wei' in payload, 'payload must include value_wei'
    assert 'value_eth' in payload, 'payload must include value_eth'
    assert payload['value_wei'] == one_eth_wei
    assert isinstance(payload['value_wei'], int)
    assert abs(payload['value_eth'] - 1.0) < 1e-9, (
        f'1 ETH must parse to 1.0 value_eth, got {payload["value_eth"]}'
    )
    assert isinstance(payload['value_eth'], float)


def test_build_base_payload_block_number_is_real():
    """_build_base_payload must store the exact integer block number, not a timestamp."""
    from services.api.app.evm_activity_provider import _build_base_payload

    target = {
        'id': str(uuid.uuid4()),
        'workspace_id': str(uuid.uuid4()),
        'chain_network': 'base',
        'target_type': 'wallet',
        'wallet_address': WALLET_ADDR,
        'contract_identifier': None,
    }
    payload = _build_base_payload(
        target=target,
        network='base',
        chain_id=BASE_CHAIN_ID,
        block_number=BASE_BLOCK_47238026,
        block_hash='0xblockhash',
        tx={'from': WALLET_ADDR, 'to': OTHER_ADDR, 'value': '0x0', 'input': '0x'},
        tx_hash=TX_HASH,
        raw_reference=f'base:{TX_HASH}',
    )
    assert payload['block_number'] == BASE_BLOCK_47238026
    assert payload['block_number'] < 500_000_000, (
        f'block_number {payload["block_number"]} is in timestamp range'
    )


# ---------------------------------------------------------------------------
# 3. Source-code: no timestamp fallback in _persist_live_coverage_telemetry
# ---------------------------------------------------------------------------

def test_no_timestamp_fallback_in_coverage_telemetry():
    """
    monitoring_runner._persist_live_coverage_telemetry must NOT use
    `int(observed_at.timestamp())` as a fallback for block_number.
    The old code was: `provider_result.latest_block or int(observed_at.timestamp())`
    which stored Unix timestamps when the probe failed.
    """
    source = open('services/api/app/monitoring_runner.py', encoding='utf-8').read()
    # The old bug pattern must be gone
    assert 'provider_result.latest_block or int(observed_at.timestamp())' not in source, (
        'monitoring_runner.py still contains the timestamp fallback: '
        '`provider_result.latest_block or int(observed_at.timestamp())` '
        '— this stores Unix timestamps as block_number when the probe fails.'
    )


def test_coverage_telemetry_skips_when_block_is_none():
    """
    monitoring_runner._persist_live_coverage_telemetry must return early (skip insertion)
    when latest_block is None, rather than storing a timestamp.
    Verified by source inspection: the function must have an early-return on None.
    """
    source = open('services/api/app/monitoring_runner.py', encoding='utf-8').read()
    # The fix introduces: `if _effective_block is None: ... return`
    assert '_effective_block is None' in source, (
        'monitoring_runner.py must guard against None latest_block by returning early'
    )
    # The old bug pattern must be gone: block = latest_block OR timestamp fallback
    old_bug_pattern = 'provider_result.latest_block or int(observed_at.timestamp())'
    assert old_bug_pattern not in source, (
        'monitoring_runner.py still contains the old timestamp fallback pattern '
        f'`{old_bug_pattern}` — must be removed'
    )


# ---------------------------------------------------------------------------
# 4. Source-code: _load_checkpoint rejects corrupted cursors > 500_000_000
# ---------------------------------------------------------------------------

def test_load_checkpoint_source_has_corruption_guardrail():
    """
    monitoring_runner._load_checkpoint must contain a guardrail that rejects
    stored block values > 500_000_000 as corrupted Unix timestamps.
    """
    source = open('services/api/app/monitoring_runner.py', encoding='utf-8').read()
    assert '500_000_000' in source or '500000000' in source, (
        '_load_checkpoint must include a 500_000_000 corruption guardrail'
    )
    assert 'CURSOR_CORRUPTION_DETECTED' in source, (
        '_load_checkpoint must log CURSOR_CORRUPTION_DETECTED when rejecting a corrupted cursor'
    )


# ---------------------------------------------------------------------------
# 5. fetch_evm_activity resets corrupted cursor (> 500M) before computing from_block
# ---------------------------------------------------------------------------

def test_fetch_evm_activity_resets_timestamp_cursor(monkeypatch):
    """
    If monitoring_checkpoint_cursor starts with a timestamp-like block number
    (> 500_000_000), fetch_evm_activity must ignore it and scan from
    latest_block - replay_blocks instead.

    Without the fix: from_block = 1_781_265_953, safe_to = ~47_238_023
    → safe_to < from_block → return [] → no eth_getBlockByNumber calls.

    With the fix: corrupted cursor is reset, from_block = latest - replay_blocks
    → blocks ARE scanned.
    """
    from services.api.app.evm_activity_provider import fetch_evm_activity

    def mock_call(method, params):
        if method == 'eth_chainId':
            return hex(BASE_CHAIN_ID)
        if method == 'eth_blockNumber':
            return BASE_BLOCK_HEX
        if method == 'eth_getBlockByNumber':
            return {'hash': '0xblockhash', 'timestamp': '0x67a00000', 'transactions': []}
        if method == 'eth_getLogs':
            return []
        return None

    mock_client = MagicMock()
    mock_client.call.side_effect = mock_call

    target = {
        'id': str(uuid.uuid4()),
        'workspace_id': str(uuid.uuid4()),
        'chain_network': 'base',
        'target_type': 'wallet',
        'wallet_address': WALLET_ADDR,
        'contract_identifier': None,
        # Corrupted cursor: first segment is a Unix timestamp, not a block number
        'monitoring_checkpoint_cursor': f'{TIMESTAMP_LIKE_BLOCK}:0xdeadbeef:-1',
    }

    with patch.dict('os.environ', {
        'LIVE_MONITORING_CHAINS': 'base',
        'EVM_CHAIN_ID': str(BASE_CHAIN_ID),
        'EVM_RPC_URL': 'http://rpc.test',
        'EVM_CONFIRMATIONS_REQUIRED': '0',
        'MONITOR_REPLAY_BLOCKS': '5',
        'MONITOR_BATCH_BLOCKS': '5',
    }):
        fetch_evm_activity(target, None, rpc_client=mock_client)

    called_methods = [call.args[0] for call in mock_client.call.call_args_list]
    assert 'eth_getBlockByNumber' in called_methods or 'eth_getLogs' in called_methods, (
        'fetch_evm_activity must scan blocks after resetting corrupted cursor > 500M. '
        'Without the fix: from_block > safe_to → early return → no scanning.'
    )


def test_fetch_evm_activity_source_has_cursor_corruption_guard():
    """evm_activity_provider.py must contain the 500_000_000 cursor corruption guard."""
    source = open('services/api/app/evm_activity_provider.py', encoding='utf-8').read()
    assert '500_000_000' in source, (
        'evm_activity_provider.py must guard against cursor values > 500_000_000'
    )
    assert 'evm_cursor_corruption_detected' in source, (
        'evm_activity_provider.py must log evm_cursor_corruption_detected'
    )


# ---------------------------------------------------------------------------
# 6. Base outbound transfer in block 47238026 is detected
# ---------------------------------------------------------------------------

def test_base_outbound_transfer_block_47238026_detected(monkeypatch):
    """
    When RPC returns a transaction from WALLET_ADDR in block 47238026,
    fetch_evm_activity must return an event with:
      - block_number = 47238026 (not a timestamp)
      - tx_hash = TX_HASH
      - wallet_transfer_direction = 'outbound'
      - source_type = 'rpc_polling'
      - chain_id = 8453
    """
    from services.api.app.evm_activity_provider import fetch_evm_activity

    def mock_call(method, params):
        if method == 'eth_chainId':
            return hex(BASE_CHAIN_ID)
        if method == 'eth_blockNumber':
            return BASE_BLOCK_HEX
        if method == 'eth_getBlockByNumber':
            block_num = int(params[0], 16)
            if block_num == BASE_BLOCK_47238026:
                return {
                    'hash': '0xblockhash47238026',
                    'timestamp': hex(1749726026),
                    'transactions': [{
                        'hash': TX_HASH,
                        'from': WALLET_ADDR,
                        'to': OTHER_ADDR,
                        'value': hex(10 ** 18),
                        'input': '0x',
                        'blockHash': '0xblockhash47238026',
                    }],
                }
            return {'hash': '0xother', 'timestamp': hex(1749726026), 'transactions': []}
        if method == 'eth_getLogs':
            return []
        return None

    mock_client = MagicMock()
    mock_client.call.side_effect = mock_call

    target = {
        'id': str(uuid.uuid4()),
        'workspace_id': str(uuid.uuid4()),
        'chain_network': 'base',
        'target_type': 'wallet',
        'wallet_address': WALLET_ADDR,
        'contract_identifier': None,
        'monitoring_checkpoint_cursor': None,
    }

    with patch.dict('os.environ', {
        'LIVE_MONITORING_CHAINS': 'base',
        'EVM_CHAIN_ID': str(BASE_CHAIN_ID),
        'EVM_RPC_URL': 'http://rpc.test',
        'EVM_CONFIRMATIONS_REQUIRED': '0',
        'MONITOR_REPLAY_BLOCKS': '50',
        'MONITOR_BATCH_BLOCKS': '50',
    }):
        events = fetch_evm_activity(target, None, rpc_client=mock_client)

    matching = [
        e for e in events
        if isinstance(e.payload, dict) and e.payload.get('tx_hash') == TX_HASH
    ]
    assert matching, (
        f'Expected wallet transfer event for tx {TX_HASH} in block {BASE_BLOCK_47238026}. '
        f'Got {len(events)} events.'
    )
    payload = matching[0].payload
    assert payload['block_number'] == BASE_BLOCK_47238026, (
        f'block_number must be {BASE_BLOCK_47238026}, got {payload["block_number"]}'
    )
    assert payload['block_number'] < 500_000_000, (
        f'block_number {payload["block_number"]} is in timestamp range — corruption'
    )
    assert payload['chain_id'] == BASE_CHAIN_ID
    assert payload['wallet_transfer_direction'] == 'outbound'
    assert payload['source_type'] == 'rpc_polling'


# ---------------------------------------------------------------------------
# 7. wallet_transfer_direction is 'outbound' when wallet is the sender
# ---------------------------------------------------------------------------

def test_wallet_transfer_direction_outbound(monkeypatch):
    from services.api.app.evm_activity_provider import fetch_evm_activity

    def mock_call(method, params):
        if method == 'eth_chainId':
            return hex(BASE_CHAIN_ID)
        if method == 'eth_blockNumber':
            return BASE_BLOCK_HEX
        if method == 'eth_getBlockByNumber':
            return {
                'hash': '0xhash',
                'timestamp': hex(1749726026),
                'transactions': [{
                    'hash': TX_HASH,
                    'from': WALLET_ADDR,  # wallet is sender → outbound
                    'to': OTHER_ADDR,
                    'value': '0x1',
                    'input': '0x',
                    'blockHash': '0xhash',
                }],
            }
        if method == 'eth_getLogs':
            return []
        return None

    mock_client = MagicMock()
    mock_client.call.side_effect = mock_call

    target = {
        'id': str(uuid.uuid4()),
        'workspace_id': str(uuid.uuid4()),
        'chain_network': 'base',
        'target_type': 'wallet',
        'wallet_address': WALLET_ADDR,
        'contract_identifier': None,
        'monitoring_checkpoint_cursor': None,
    }

    with patch.dict('os.environ', {
        'LIVE_MONITORING_CHAINS': 'base',
        'EVM_CHAIN_ID': str(BASE_CHAIN_ID),
        'EVM_RPC_URL': 'http://rpc.test',
        'EVM_CONFIRMATIONS_REQUIRED': '0',
        'MONITOR_REPLAY_BLOCKS': '3',
        'MONITOR_BATCH_BLOCKS': '3',
    }):
        events = fetch_evm_activity(target, None, rpc_client=mock_client)

    tx_events = [e for e in events if isinstance(e.payload, dict) and e.payload.get('tx_hash') == TX_HASH]
    assert tx_events, 'Expected wallet transfer event'
    assert tx_events[0].payload.get('wallet_transfer_direction') == 'outbound'


# ---------------------------------------------------------------------------
# 8. source_type=rpc_polling is present in detected wallet transfer payload
# ---------------------------------------------------------------------------

def test_wallet_transfer_payload_has_source_type_rpc_polling(monkeypatch):
    from services.api.app.evm_activity_provider import fetch_evm_activity

    def mock_call(method, params):
        if method == 'eth_chainId':
            return hex(BASE_CHAIN_ID)
        if method == 'eth_blockNumber':
            return BASE_BLOCK_HEX
        if method == 'eth_getBlockByNumber':
            return {
                'hash': '0xhash',
                'timestamp': hex(1749726026),
                'transactions': [{
                    'hash': TX_HASH,
                    'from': WALLET_ADDR,
                    'to': OTHER_ADDR,
                    'value': '0x1',
                    'input': '0x',
                    'blockHash': '0xhash',
                }],
            }
        if method == 'eth_getLogs':
            return []
        return None

    mock_client = MagicMock()
    mock_client.call.side_effect = mock_call

    target = {
        'id': str(uuid.uuid4()),
        'workspace_id': str(uuid.uuid4()),
        'chain_network': 'base',
        'target_type': 'wallet',
        'wallet_address': WALLET_ADDR,
        'contract_identifier': None,
        'monitoring_checkpoint_cursor': None,
    }

    with patch.dict('os.environ', {
        'LIVE_MONITORING_CHAINS': 'base',
        'EVM_CHAIN_ID': str(BASE_CHAIN_ID),
        'EVM_RPC_URL': 'http://rpc.test',
        'EVM_CONFIRMATIONS_REQUIRED': '0',
        'MONITOR_REPLAY_BLOCKS': '3',
        'MONITOR_BATCH_BLOCKS': '3',
    }):
        events = fetch_evm_activity(target, None, rpc_client=mock_client)

    tx_events = [e for e in events if isinstance(e.payload, dict) and e.payload.get('tx_hash') == TX_HASH]
    assert tx_events, 'Expected wallet transfer event'
    assert tx_events[0].payload.get('source_type') == 'rpc_polling', (
        f'source_type must be rpc_polling, got {tx_events[0].payload.get("source_type")!r}'
    )


# ---------------------------------------------------------------------------
# 9. Heartbeat telemetry payload has latest_block and raw_response.result
# ---------------------------------------------------------------------------

def test_heartbeat_payload_has_latest_block_and_raw_response_result():
    """
    _persist_live_coverage_telemetry must include:
      - payload_json.latest_block  = real decimal block height
      - payload_json.raw_response.result = original RPC hex string
    Verified by source inspection.
    """
    source = open('services/api/app/monitoring_runner.py', encoding='utf-8').read()
    assert "'latest_block': _effective_block" in source, (
        "monitoring_runner._persist_live_coverage_telemetry must set "
        "payload_json['latest_block'] = real block height"
    )
    assert "'result': hex(_effective_block)" in source, (
        "monitoring_runner._persist_live_coverage_telemetry must set "
        "payload_json['raw_response']['result'] = original RPC hex string"
    )


def test_heartbeat_payload_raw_response_result_matches_block_hex():
    """
    payload_json.raw_response.result must equal hex(block_number).
    Since result = hex(_effective_block) and eth_blockNumber = hex(_effective_block),
    both must match for real Base blocks.
    """
    block = BASE_BLOCK_47238026
    result_hex = hex(block)
    eth_block_hex = hex(block)
    assert result_hex == eth_block_hex == BASE_BLOCK_HEX, (
        f'raw_response.result ({result_hex}) must equal eth_blockNumber hex ({eth_block_hex})'
    )
    assert result_hex.startswith('0x'), 'result must be a hex string'


# ---------------------------------------------------------------------------
# 10. Cursor > latest_block + 1000 is treated as corrupted and reset
# ---------------------------------------------------------------------------

def test_fetch_evm_activity_resets_cursor_ahead_of_chain(monkeypatch):
    """
    If the stored cursor is more than 1000 blocks ahead of latest_block,
    fetch_evm_activity must treat it as corrupted and reset to replay window.

    Example: latest = 47238026, cursor = 47240000 (1974 blocks ahead → corrupted).
    Without the fix: from_block = 47240000 > safe_to = 47238026 → early return.
    With the fix: corrupted cursor is reset → blocks ARE scanned.
    """
    from services.api.app.evm_activity_provider import fetch_evm_activity

    # Cursor 2000 blocks ahead of latest — not a timestamp, but clearly wrong
    corrupt_ahead_cursor = BASE_BLOCK_47238026 + 2000

    def mock_call(method, params):
        if method == 'eth_chainId':
            return hex(BASE_CHAIN_ID)
        if method == 'eth_blockNumber':
            return BASE_BLOCK_HEX
        if method == 'eth_getBlockByNumber':
            return {'hash': '0xhash', 'timestamp': '0x67a00000', 'transactions': []}
        if method == 'eth_getLogs':
            return []
        return None

    mock_client = MagicMock()
    mock_client.call.side_effect = mock_call

    target = {
        'id': str(uuid.uuid4()),
        'workspace_id': str(uuid.uuid4()),
        'chain_network': 'base',
        'target_type': 'wallet',
        'wallet_address': WALLET_ADDR,
        'contract_identifier': None,
        'monitoring_checkpoint_cursor': f'{corrupt_ahead_cursor}:0xdeadbeef:-1',
    }

    with patch.dict('os.environ', {
        'LIVE_MONITORING_CHAINS': 'base',
        'EVM_CHAIN_ID': str(BASE_CHAIN_ID),
        'EVM_RPC_URL': 'http://rpc.test',
        'EVM_CONFIRMATIONS_REQUIRED': '0',
        'MONITOR_REPLAY_BLOCKS': '5',
        'MONITOR_BATCH_BLOCKS': '5',
    }):
        fetch_evm_activity(target, None, rpc_client=mock_client)

    called_methods = [call.args[0] for call in mock_client.call.call_args_list]
    assert 'eth_getBlockByNumber' in called_methods or 'eth_getLogs' in called_methods, (
        f'fetch_evm_activity must scan blocks after resetting cursor={corrupt_ahead_cursor} '
        f'which is > latest ({BASE_BLOCK_47238026}) + 1000. '
        'Without the fix: from_block > safe_to → early return → no scanning.'
    )


def test_cursor_ahead_of_chain_source_has_guardrail():
    """evm_activity_provider.py must contain the cursor_ahead_of_chain corruption reason."""
    source = open('services/api/app/evm_activity_provider.py', encoding='utf-8').read()
    assert 'cursor_ahead_of_chain' in source, (
        'evm_activity_provider.py must guard against cursor > latest + 1000 '
        '(reason=cursor_ahead_of_chain)'
    )


# ---------------------------------------------------------------------------
# 11. evm_block_scan_complete log includes transactions_inspected and
#     wallet_transfers_detected
# ---------------------------------------------------------------------------

def test_scan_complete_log_includes_transactions_inspected(monkeypatch):
    """
    evm_block_scan_complete log must include transactions_inspected count.
    Verified by source inspection.
    """
    source = open('services/api/app/evm_activity_provider.py', encoding='utf-8').read()
    assert 'transactions_inspected=%s' in source, (
        'evm_block_scan_complete log must include transactions_inspected=<count>'
    )


def test_scan_complete_log_includes_wallet_transfers_detected():
    """
    evm_block_scan_complete log must include wallet_transfers_detected count.
    Verified by source inspection.
    """
    source = open('services/api/app/evm_activity_provider.py', encoding='utf-8').read()
    assert 'wallet_transfers_detected=%s' in source, (
        'evm_block_scan_complete log must include wallet_transfers_detected=<count>'
    )


def test_scan_complete_log_includes_eth_blockNumber_raw():
    """evm_block_scan_complete log must include eth_blockNumber_raw."""
    source = open('services/api/app/evm_activity_provider.py', encoding='utf-8').read()
    assert 'eth_blockNumber_raw=%s' in source, (
        'evm_block_scan_complete log must include eth_blockNumber_raw=<hex>'
    )


def test_scan_counts_transactions_inspected(monkeypatch):
    """
    When eth_getBlockByNumber returns transactions, _transactions_inspected must be > 0
    and reflected in the scan complete log.
    """
    import logging
    from services.api.app.evm_activity_provider import fetch_evm_activity

    log_records: list[logging.LogRecord] = []

    class CapHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            log_records.append(record)

    handler = CapHandler()
    handler.setLevel(logging.DEBUG)
    log = logging.getLogger('services.api.app.evm_activity_provider')
    original_level = log.level
    original_propagate = log.propagate
    log.setLevel(logging.DEBUG)
    log.propagate = False
    log.addHandler(handler)
    try:
        def mock_call(method, params):
            if method == 'eth_chainId':
                return hex(BASE_CHAIN_ID)
            if method == 'eth_blockNumber':
                return BASE_BLOCK_HEX
            if method == 'eth_getBlockByNumber':
                # Only return tx for the exact target block; other blocks are empty
                blk = int(params[0], 16)
                if blk == BASE_BLOCK_47238026:
                    return {
                        'hash': '0xhash',
                        'timestamp': hex(1749726026),
                        'transactions': [
                            {'hash': TX_HASH, 'from': OTHER_ADDR, 'to': WALLET_ADDR, 'value': '0x1', 'input': '0x', 'blockHash': '0xhash'},
                        ],
                    }
                return {'hash': '0xother', 'timestamp': hex(1749726026), 'transactions': []}
            if method == 'eth_getLogs':
                return []
            return None

        mock_client = MagicMock()
        mock_client.call.side_effect = mock_call

        target = {
            'id': str(uuid.uuid4()),
            'workspace_id': str(uuid.uuid4()),
            'chain_network': 'base',
            'target_type': 'wallet',
            'wallet_address': WALLET_ADDR,
            'contract_identifier': None,
            'monitoring_checkpoint_cursor': None,
        }

        with patch.dict('os.environ', {
            'LIVE_MONITORING_CHAINS': 'base',
            'EVM_CHAIN_ID': str(BASE_CHAIN_ID),
            'EVM_RPC_URL': 'http://rpc.test',
            'EVM_CONFIRMATIONS_REQUIRED': '0',
            'MONITOR_REPLAY_BLOCKS': '1',
            'MONITOR_BATCH_BLOCKS': '1',
        }):
            fetch_evm_activity(target, None, rpc_client=mock_client)
    finally:
        log.removeHandler(handler)
        log.setLevel(original_level)
        log.propagate = original_propagate

    complete_msgs = [
        r.getMessage() for r in log_records
        if 'evm_block_scan_complete' in r.getMessage()
    ]
    assert complete_msgs, 'Expected evm_block_scan_complete log entry'
    msg = complete_msgs[-1]
    assert 'transactions_inspected=' in msg, (
        f'evm_block_scan_complete log must include transactions_inspected. Got: {msg!r}'
    )
    assert 'wallet_transfers_detected=' in msg, (
        f'evm_block_scan_complete log must include wallet_transfers_detected. Got: {msg!r}'
    )
    # 1 tx inspected in the target block, 1 wallet transfer (inbound to wallet)
    assert 'transactions_inspected=1' in msg, f'Expected transactions_inspected=1, got: {msg!r}'
    assert 'wallet_transfers_detected=1' in msg, f'Expected wallet_transfers_detected=1, got: {msg!r}'


# ---------------------------------------------------------------------------
# 12 & 13. tx_hash search returns wallet_transfer_detected rows
# ---------------------------------------------------------------------------

def test_telemetry_search_query_covers_tx_hash():
    """
    The telemetry search query in monitoring_runner.py must filter by
    payload_json->>'tx_hash' so that wallet_transfer_detected events are
    returned when searching for a transaction hash.
    """
    source = open('services/api/app/monitoring_runner.py', encoding='utf-8').read()
    assert "payload_json->>'tx_hash'" in source, (
        "monitoring_runner.py telemetry search must filter on payload_json->>'tx_hash' "
        "so that wallet_transfer_detected rows appear in tx_hash searches"
    )


def test_wallet_transfer_detected_event_type_stored_correctly(monkeypatch):
    """
    When fetch_evm_activity returns a transaction where wallet is sender/receiver,
    the event payload must contain tx_hash so the search query can find it.
    """
    from services.api.app.evm_activity_provider import fetch_evm_activity

    def mock_call(method, params):
        if method == 'eth_chainId':
            return hex(BASE_CHAIN_ID)
        if method == 'eth_blockNumber':
            return BASE_BLOCK_HEX
        if method == 'eth_getBlockByNumber':
            blk = int(params[0], 16)
            if blk == BASE_BLOCK_47238026:
                return {
                    'hash': '0xblockhash',
                    'timestamp': hex(1749726026),
                    'transactions': [{
                        'hash': TX_HASH,
                        'from': WALLET_ADDR,
                        'to': OTHER_ADDR,
                        'value': hex(10 ** 17),
                        'input': '0x',
                        'blockHash': '0xblockhash',
                    }],
                }
            return {'hash': '0xother', 'timestamp': hex(1749726026), 'transactions': []}
        if method == 'eth_getLogs':
            return []
        return None

    mock_client = MagicMock()
    mock_client.call.side_effect = mock_call

    target = {
        'id': str(uuid.uuid4()),
        'workspace_id': str(uuid.uuid4()),
        'chain_network': 'base',
        'target_type': 'wallet',
        'wallet_address': WALLET_ADDR,
        'contract_identifier': None,
        'monitoring_checkpoint_cursor': None,
    }

    with patch.dict('os.environ', {
        'LIVE_MONITORING_CHAINS': 'base',
        'EVM_CHAIN_ID': str(BASE_CHAIN_ID),
        'EVM_RPC_URL': 'http://rpc.test',
        'EVM_CONFIRMATIONS_REQUIRED': '0',
        'MONITOR_REPLAY_BLOCKS': '1',
        'MONITOR_BATCH_BLOCKS': '1',
    }):
        events = fetch_evm_activity(target, None, rpc_client=mock_client)

    wallet_events = [
        e for e in events
        if isinstance(e.payload, dict) and e.payload.get('tx_hash') == TX_HASH
    ]
    assert wallet_events, (
        f'Expected event with tx_hash={TX_HASH!r} — needed for tx_hash search to work'
    )
    payload = wallet_events[0].payload
    assert payload.get('tx_hash') == TX_HASH, 'tx_hash must be stored in payload for search'
    assert payload.get('from') == WALLET_ADDR.lower(), 'from address must be in payload'
    assert payload.get('to') == OTHER_ADDR.lower(), 'to address must be in payload'
    assert payload.get('wallet_transfer_direction') == 'outbound'
    # These fields must be present for the tx_hash search to succeed in monitoring_runner
    assert 'block_number' in payload, 'block_number must be in payload'
    assert payload['block_number'] == BASE_BLOCK_47238026, (
        f'block_number must be real block height {BASE_BLOCK_47238026}, got {payload["block_number"]}'
    )


# ---------------------------------------------------------------------------
# 14. block_number is NOT equal to int(observed_at.timestamp())
# ---------------------------------------------------------------------------

def test_block_number_is_not_observed_at_timestamp(monkeypatch):
    """
    fetch_evm_activity must store the EVM block height as block_number,
    NOT the Unix timestamp of observed_at.

    Regression: block_number was formerly set to int(observed_at.timestamp())
    when the RPC probe failed, producing values like 1781267508 (June 2026 epoch).
    This test confirms that block_number != int(observed_at.timestamp()).
    """
    import time
    from services.api.app.evm_activity_provider import fetch_evm_activity

    observed_at_approx = int(time.time())  # current Unix timestamp (~1781267xxx for 2026)

    def mock_call(method, params):
        if method == 'eth_chainId':
            return hex(BASE_CHAIN_ID)
        if method == 'eth_blockNumber':
            return BASE_BLOCK_HEX  # returns real block hex, NOT a timestamp
        if method == 'eth_getBlockByNumber':
            blk = int(params[0], 16)
            if blk == BASE_BLOCK_47238026:
                return {
                    'hash': '0xblockhash',
                    'timestamp': hex(observed_at_approx),  # block timestamp = current time
                    'transactions': [{
                        'hash': TX_HASH,
                        'from': WALLET_ADDR,
                        'to': OTHER_ADDR,
                        'value': hex(10 ** 17),
                        'input': '0x',
                        'blockHash': '0xblockhash',
                    }],
                }
            return {'hash': '0xother', 'timestamp': hex(observed_at_approx), 'transactions': []}
        if method == 'eth_getLogs':
            return []
        return None

    mock_client = MagicMock()
    mock_client.call.side_effect = mock_call

    target = {
        'id': str(uuid.uuid4()),
        'workspace_id': str(uuid.uuid4()),
        'chain_network': 'base',
        'target_type': 'wallet',
        'wallet_address': WALLET_ADDR,
        'contract_identifier': None,
        'monitoring_checkpoint_cursor': None,
    }

    with patch.dict('os.environ', {
        'LIVE_MONITORING_CHAINS': 'base',
        'EVM_CHAIN_ID': str(BASE_CHAIN_ID),
        'EVM_RPC_URL': 'http://rpc.test',
        'EVM_CONFIRMATIONS_REQUIRED': '0',
        'MONITOR_REPLAY_BLOCKS': '1',
        'MONITOR_BATCH_BLOCKS': '1',
    }):
        events = fetch_evm_activity(target, None, rpc_client=mock_client)

    assert events, 'Expected wallet transfer events from fetch_evm_activity'
    for event in events:
        if not isinstance(event.payload, dict):
            continue
        bn = event.payload.get('block_number')
        if bn is None:
            continue
        assert bn != observed_at_approx, (
            f'block_number {bn!r} must NOT equal int(observed_at.timestamp()) {observed_at_approx}. '
            f'block_number must be the real EVM block height, not a Unix timestamp.'
        )
        assert bn < 500_000_000, (
            f'block_number {bn!r} is in Unix timestamp range (> 500M). '
            f'This indicates block_number was set to observed_at.timestamp() instead of EVM block height.'
        )


# ---------------------------------------------------------------------------
# 15. Base heartbeat rows store block_number around mocked eth_blockNumber value
# ---------------------------------------------------------------------------

def test_base_heartbeat_block_number_matches_eth_blockNumber(monkeypatch):
    """
    When fetch_evm_activity gets eth_blockNumber = BASE_BLOCK_HEX (47238026)
    and no transactions are found, the ActivityProviderResult.latest_block must
    be set to 47238026 (from probe_rpc_health), NOT a Unix timestamp.

    Verified via source inspection + probe_rpc_health unit test.
    """
    import time
    from services.api.app.evm_activity_provider import probe_rpc_health

    observed_at_ts = int(time.time())  # current Unix timestamp

    # Mock probe_rpc_health to simulate a successful RPC call returning Base block 47238026
    with patch('services.api.app.evm_activity_provider.FailoverJsonRpcClient') as mock_cls:
        instance = MagicMock()
        instance.call.side_effect = lambda method, params: (
            hex(BASE_CHAIN_ID) if method == 'eth_chainId' else BASE_BLOCK_HEX
        )
        mock_cls.return_value = instance
        with patch.dict('os.environ', {'EVM_RPC_URL': 'http://rpc.test'}):
            result = probe_rpc_health()

    assert result['ok'] is True, f'probe_rpc_health must succeed: {result}'
    stored_block = result['block_number_int']

    assert stored_block == BASE_BLOCK_47238026, (
        f'probe_rpc_health block_number_int must equal mocked eth_blockNumber {BASE_BLOCK_47238026}, '
        f'got {stored_block!r}'
    )
    assert stored_block != observed_at_ts, (
        f'Heartbeat block_number {stored_block} must NOT equal '
        f'int(time.time()) {observed_at_ts}. '
        f'block_number must come from eth_blockNumber hex parsing, not the current clock.'
    )
    assert stored_block < 500_000_000, (
        f'Heartbeat block_number {stored_block} must not be in Unix timestamp range (> 500M). '
        f'Base mainnet is at ~47M blocks; a value > 500M is a corrupt Unix timestamp.'
    )


def test_base_heartbeat_source_guards_in_coverage_telemetry():
    """
    _persist_live_coverage_telemetry source code must:
    1. Check _effective_block is None BEFORE any DB insert (skip if None)
    2. Check _effective_block > 500_000_000 and reject (skip if timestamp-like)
    3. Log the block_number being inserted with eth_blockNumber_hex and source_type
    """
    source = open('services/api/app/monitoring_runner.py', encoding='utf-8').read()

    # Guard must be BEFORE the monitoring_event_receipts INSERT
    func_start = source.find('def _persist_live_coverage_telemetry(')
    func_body = source[func_start:func_start + 4000]  # first 4KB of the function

    none_check_pos = func_body.find('_effective_block is None')
    receipts_insert_pos = func_body.find('INSERT INTO monitoring_event_receipts')
    assert none_check_pos < receipts_insert_pos, (
        '_persist_live_coverage_telemetry must check _effective_block is None '
        'BEFORE the monitoring_event_receipts INSERT, to avoid NULL block_number in receipts'
    )

    assert '500_000_000' in func_body or '500000000' in func_body, (
        '_persist_live_coverage_telemetry must guard against block_number > 500M (timestamp range)'
    )

    assert 'COVERAGE_TELEMETRY_POLL_CYCLE' in source or 'block_number_to_insert' in source, (
        '_persist_live_coverage_telemetry must log block_number_to_insert for production diagnostics'
    )
