from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib import request


@dataclass
class ActivityEvent:
    event_id: str
    kind: str
    observed_at: datetime
    ingestion_source: str
    cursor: str
    payload: dict[str, Any]


logger = logging.getLogger(__name__)

TRANSFER_TOPIC = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'
APPROVAL_TOPIC = '0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925'

SELECTOR_NAMES = {
    '0x095ea7b3': 'approve',
    '0x39509351': 'increaseAllowance',
    '0x23b872dd': 'transferFrom',
    '0x2f2ff15d': 'grantRole',
    '0xd547741f': 'revokeRole',
    '0x36568abe': 'renounceRole',
    '0x3659cfe6': 'upgradeTo',
    '0x4f1ef286': 'upgradeToAndCall',
    '0xf2fde38b': 'transferOwnership',
    '0x704b6c02': 'setAdmin',
}

CHAIN_MAP = {
    'ethereum': {'chain_id': 1},
    'base': {'chain_id': 8453},
    'arbitrum': {'chain_id': 42161},
}


class RpcClient(Protocol):
    def call(self, method: str, params: list[Any]) -> Any: ...


@dataclass
class JsonRpcClient:
    rpc_url: str

    def call(self, method: str, params: list[Any]) -> Any:
        payload = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}).encode('utf-8')
        req = request.Request(self.rpc_url, data=payload, headers={'Content-Type': 'application/json'})
        with request.urlopen(req, timeout=10) as resp:  # nosec B310
            body = json.loads(resp.read().decode('utf-8'))
        if body.get('error'):
            raise RuntimeError(f"json-rpc error: {body['error']}")
        return body.get('result')


def _hex_to_int(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value, 16)
    except Exception:
        return None


def _topic_to_address(topic: str | None) -> str | None:
    if not topic or len(topic) < 66:
        return None
    return f"0x{topic[-40:]}".lower()


def _extract_selector(input_data: str | None) -> str | None:
    if not input_data or len(input_data) < 10:
        return None
    if not input_data.startswith('0x'):
        return None
    return input_data[:10].lower()


def _event_cursor(block_number: int, tx_hash: str, log_index: int | None) -> str:
    return f"{block_number}:{tx_hash}:{-1 if log_index is None else log_index}"


def _make_event_id(target_id: str, cursor: str, kind: str) -> str:
    return hashlib.sha256(f'{target_id}:{kind}:{cursor}'.encode('utf-8')).hexdigest()[:24]


def _iso_from_block_ts(ts_hex: str | None) -> datetime:
    ts = _hex_to_int(ts_hex) or int(datetime.now(timezone.utc).timestamp())
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def _build_base_payload(*, target: dict[str, Any], network: str, chain_id: int, block_number: int, block_hash: str | None, tx: dict[str, Any], tx_hash: str, raw_reference: str) -> dict[str, Any]:
    selector = _extract_selector(tx.get('input'))
    return {
        'chain_id': chain_id,
        'chain_network': network,
        'block_number': block_number,
        'block_hash': block_hash,
        'tx_hash': tx_hash,
        'from': str(tx.get('from') or '').lower() or None,
        'to': str(tx.get('to') or '').lower() or None,
        'amount': str(_hex_to_int(tx.get('value')) or 0),
        'function_selector': selector,
        'decoded_function_name': SELECTOR_NAMES.get(selector or '', None),
        'decode_status': 'decoded' if SELECTOR_NAMES.get(selector or '') else ('partial' if selector else 'none'),
        'raw_reference': raw_reference,
        'contract_address': str(target.get('contract_identifier') or '').lower() or None,
        'asset_address': None,
        'asset_symbol': None,
    }


def _fetch_logs(client: RpcClient, address: str, from_block: int, to_block: int) -> list[dict[str, Any]]:
    params = [{
        'fromBlock': hex(from_block),
        'toBlock': hex(to_block),
        'topics': [[TRANSFER_TOPIC, APPROVAL_TOPIC], None, [f"0x{'0'*24}{address[2:]}"]],
    }]
    inbound = client.call('eth_getLogs', params) or []
    params_outbound = [{
        'fromBlock': hex(from_block),
        'toBlock': hex(to_block),
        'topics': [[TRANSFER_TOPIC, APPROVAL_TOPIC], [f"0x{'0'*24}{address[2:]}"], None],
    }]
    outbound = client.call('eth_getLogs', params_outbound) or []
    seen: dict[str, dict[str, Any]] = {}
    for log in [*inbound, *outbound]:
        key = f"{log.get('transactionHash')}:{log.get('logIndex')}"
        seen[key] = log
    return list(seen.values())


def _iter_block_ranges(from_block: int, to_block: int, chunk_size: int) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    cursor = from_block
    while cursor <= to_block:
        end = min(to_block, cursor + chunk_size - 1)
        ranges.append((cursor, end))
        cursor = end + 1
    return ranges


def fetch_evm_activity(target: dict[str, Any], since_ts: datetime | None, *, rpc_client: RpcClient | None = None) -> list[ActivityEvent]:
    rpc_url = (os.getenv('EVM_RPC_URL') or '').strip()
    if not rpc_url:
        return []
    network = str(target.get('chain_network') or 'ethereum').strip().lower()
    if network not in {item.strip().lower() for item in (os.getenv('LIVE_MONITORING_CHAINS', 'ethereum').split(','))}:
        return []

    confirmations = max(0, int(os.getenv('EVM_CONFIRMATIONS_REQUIRED', '3')))
    lookback = max(5, int(os.getenv('EVM_BLOCK_LOOKBACK', '25')))
    block_scan_chunk = max(1, int(os.getenv('EVM_BLOCK_SCAN_CHUNK_SIZE', '25')))
    target_address = str(target.get('wallet_address') or target.get('contract_identifier') or '').lower()
    if not target_address.startswith('0x'):
        return []

    client = rpc_client or JsonRpcClient(rpc_url)
    latest = _hex_to_int(client.call('eth_blockNumber', [])) or 0
    safe_to = max(0, latest - confirmations)

    cursor = str(target.get('monitoring_checkpoint_cursor') or '').strip()
    last_block = None
    if cursor and ':' in cursor:
        try:
            last_block = int(cursor.split(':', 1)[0])
        except ValueError:
            last_block = None
    from_block = max(0, safe_to - lookback if last_block is None else max(last_block - lookback, 0))
    if safe_to < from_block:
        return []

    events: list[ActivityEvent] = []
    chain_id = CHAIN_MAP.get(network, {}).get('chain_id', 1)
    block_ts_cache: dict[str, datetime] = {}

    logs: list[dict[str, Any]] = []
    target_type = str(target.get('target_type') or '').lower()
    if target_type == 'wallet':
        for chunk_from, chunk_to in _iter_block_ranges(from_block, safe_to, block_scan_chunk):
            logs.extend(_fetch_logs(client, target_address, chunk_from, chunk_to))

    for chunk_from, chunk_to in _iter_block_ranges(from_block, safe_to, block_scan_chunk):
        for block_number in range(chunk_from, chunk_to + 1):
            block = client.call('eth_getBlockByNumber', [hex(block_number), True]) or {}
            block_hash = str(block.get('hash') or '')
            if block_hash and block_hash not in block_ts_cache:
                block_ts_cache[block_hash] = _iso_from_block_ts(block.get('timestamp'))
            txs = block.get('transactions') or []
            for tx in txs:
                tx_to = str(tx.get('to') or '').lower()
                tx_from = str(tx.get('from') or '').lower()
                if target_type == 'wallet' and target_address not in {tx_to, tx_from}:
                    continue
                if target_type == 'contract' and tx_to != target_address:
                    continue
                tx_hash = str(tx.get('hash') or '')
                observed_at = block_ts_cache.get(block_hash) or _iso_from_block_ts(block.get('timestamp'))
                cursor_value = _event_cursor(block_number, tx_hash, None)
                payload = _build_base_payload(
                    target=target,
                    network=network,
                    chain_id=chain_id,
                    block_number=block_number,
                    block_hash=block_hash or tx.get('blockHash'),
                    tx=tx,
                    tx_hash=tx_hash,
                    raw_reference=f'{network}:{tx_hash}',
                )
                kind = 'transaction' if target_type == 'wallet' else 'contract'
                events.append(ActivityEvent(event_id=_make_event_id(str(target['id']), cursor_value, kind), kind=kind, observed_at=observed_at, ingestion_source='evm_rpc', cursor=cursor_value, payload=payload))

    for log in logs:
        tx_hash = str(log.get('transactionHash') or '')
        tx = client.call('eth_getTransactionByHash', [tx_hash]) or {}
        block_number = _hex_to_int(log.get('blockNumber')) or safe_to
        log_index = _hex_to_int(log.get('logIndex'))
        block_hash = str(log.get('blockHash') or '')
        observed_at = block_ts_cache.get(block_hash)
        if observed_at is None:
            block = client.call('eth_getBlockByHash', [log.get('blockHash'), False]) if log.get('blockHash') else {}
            observed_at = _iso_from_block_ts((block or {}).get('timestamp'))
            if block_hash:
                block_ts_cache[block_hash] = observed_at
        topic0 = str((log.get('topics') or [''])[0]).lower()
        owner = _topic_to_address((log.get('topics') or [None, None])[1])
        spender_or_to = _topic_to_address((log.get('topics') or [None, None, None])[2])
        payload = _build_base_payload(
            target=target,
            network=network,
            chain_id=chain_id,
            block_number=block_number,
            block_hash=log.get('blockHash'),
            tx=tx,
            tx_hash=tx_hash,
            raw_reference=f'{network}:{tx_hash}:{log_index}',
        )
        payload.update(
            {
                'log_index': log_index,
                'contract_address': str(log.get('address') or '').lower() or payload.get('contract_address'),
                'asset_address': str(log.get('address') or '').lower() or None,
                'owner': owner,
                'spender': spender_or_to if topic0 == APPROVAL_TOPIC else None,
                'to': spender_or_to if topic0 == TRANSFER_TOPIC else payload.get('to'),
                'kind_hint': 'erc20_approval' if topic0 == APPROVAL_TOPIC else 'erc20_transfer',
                'amount': str(_hex_to_int(log.get('data')) or 0),
            }
        )
        kind = 'transaction'
        cursor_value = _event_cursor(block_number, tx_hash, log_index)
        events.append(ActivityEvent(event_id=_make_event_id(str(target['id']), cursor_value, 'transaction'), kind=kind, observed_at=observed_at, ingestion_source='evm_rpc', cursor=cursor_value, payload=payload))

    events.sort(key=lambda item: item.cursor)
    deduped: list[ActivityEvent] = []
    for event in events:
        if cursor and event.cursor <= cursor:
            continue
        deduped.append(event)
    logger.info('evm activity fetched target=%s from_block=%s to_block=%s events=%s', target.get('id'), from_block, safe_to, len(deduped))
    return deduped
