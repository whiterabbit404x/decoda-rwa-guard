from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol
from urllib import parse, request


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


class MarketTelemetryProvider(Protocol):
    def fetch(self, *, asset_identifier: str, now: datetime) -> list[dict[str, Any]]: ...


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


@dataclass
class HttpJsonMarketTelemetryProvider:
    source_name: str
    source_type: str
    url: str

    def fetch(self, *, asset_identifier: str, now: datetime) -> list[dict[str, Any]]:
        query = parse.urlencode({'asset_identifier': asset_identifier}) if asset_identifier else ''
        url = f'{self.url}?{query}' if query else self.url
        req = request.Request(url, headers={'Accept': 'application/json'})
        with request.urlopen(req, timeout=10) as resp:  # nosec B310
            body = json.loads(resp.read().decode('utf-8') or '{}')
        observations = body.get('observations') if isinstance(body, dict) else body
        if not isinstance(observations, list):
            return []
        items: list[dict[str, Any]] = []
        for item in observations:
            if not isinstance(item, dict):
                continue
            items.append(
                {
                    **item,
                    'provider_name': self.source_name,
                    'source_name': str(item.get('source_name') or self.source_name),
                    'source_type': str(item.get('source_type') or self.source_type),
                    'telemetry_kind': str(item.get('telemetry_kind') or 'external_market'),
                    'provenance': {
                        'provider_layer': 'evm_activity_provider',
                        'provider_kind': 'http_json',
                        'provider_url': self.url,
                        'fetched_at': now.isoformat(),
                    },
                }
            )
        return items


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
        'event_type': 'transaction',
        'observed_at': None,
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
    ws_configured = bool((os.getenv('EVM_WS_URL') or '').strip())
    preferred_source = 'websocket' if ws_configured else 'polling'
    fallback_source = 'rpc_backfill' if ws_configured else 'polling'
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
                payload['observed_at'] = observed_at.isoformat()
                payload['event_type'] = 'transaction' if target_type == 'wallet' else 'contract_interaction'
                kind = 'transaction' if target_type == 'wallet' else 'contract'
                events.append(ActivityEvent(event_id=_make_event_id(str(target['id']), cursor_value, kind), kind=kind, observed_at=observed_at, ingestion_source=preferred_source, cursor=cursor_value, payload=payload))

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
                'event_type': 'approval' if topic0 == APPROVAL_TOPIC else 'transfer',
                'amount': str(_hex_to_int(log.get('data')) or 0),
                'observed_at': observed_at.isoformat(),
            }
        )
        kind = 'transaction'
        cursor_value = _event_cursor(block_number, tx_hash, log_index)
        events.append(ActivityEvent(event_id=_make_event_id(str(target['id']), cursor_value, 'transaction'), kind=kind, observed_at=observed_at, ingestion_source=fallback_source, cursor=cursor_value, payload=payload))

    events.sort(key=lambda item: item.cursor)
    deduped: list[ActivityEvent] = []
    for event in events:
        if cursor and event.cursor <= cursor:
            continue
        deduped.append(event)
    telemetry = _build_cycle_telemetry(target, deduped)
    for event in deduped:
        payload = event.payload if isinstance(event.payload, dict) else {}
        payload['market_observations'] = telemetry['market_observations']
        payload['oracle_observations'] = telemetry['oracle_observations']
        payload['liquidity_observations'] = telemetry['liquidity_observations']
        payload['venue_observations'] = telemetry['venue_observations']
        event.payload = payload
    logger.info('evm activity fetched target=%s from_block=%s to_block=%s events=%s', target.get('id'), from_block, safe_to, len(deduped))
    return deduped


def _build_cycle_telemetry(target: dict[str, Any], events: list[ActivityEvent]) -> dict[str, list[dict[str, Any]]]:
    market_observations = _fetch_market_observations(target)
    oracle_observations = _fetch_oracle_observations(target)
    liquidity_observation = _build_liquidity_observation(target, events)
    venue_observation = _build_venue_observation(target, events, liquidity_observation, market_observations)
    primary_market = market_observations[0] if market_observations and isinstance(market_observations[0], dict) else {}
    if liquidity_observation and str(primary_market.get('status') or '').lower() == 'ok':
        for key in (
            'rolling_volume',
            'rolling_transfer_count',
            'transfer_count',
            'unique_counterparties',
            'concentration_ratio',
            'abnormal_outflow_ratio',
            'burst_score',
            'route_distribution',
            'venue_distribution',
        ):
            if key in primary_market:
                liquidity_observation[key] = primary_market.get(key)
        liquidity_observation['provider_name'] = str(primary_market.get('provider_name') or primary_market.get('source_name') or 'external_market_provider')
        liquidity_observation['telemetry_kind'] = str(primary_market.get('telemetry_kind') or 'external_market')
        liquidity_observation['status'] = str(primary_market.get('status') or 'ok')
        liquidity_observation['telemetry_state'] = 'real_telemetry_present'
        liquidity_observation['market_observations'] = market_observations
    if liquidity_observation is None:
        liquidity_observation = {
            'provider_name': 'evm_activity_provider',
            'status': 'insufficient_real_evidence',
            'reason': 'no_transfer_events_in_window',
            'rolling_volume': 0.0,
            'rolling_transfer_count': 0,
            'unique_counterparties': 0,
            'concentration_ratio': 0.0,
            'abnormal_outflow_ratio': 0.0,
            'burst_score': 0.0,
            'route_distribution': {},
            'venue_distribution': {},
            'asset_identifier': str(target.get('asset_identifier') or target.get('asset_symbol') or target.get('id') or ''),
            'observed_at': datetime.now(timezone.utc).isoformat(),
            'market_observations': market_observations,
        }
    if venue_observation is None:
        venue_observation = {
            'provider_name': 'evm_activity_provider',
            'status': 'insufficient_real_evidence',
            'reason': 'venue_distribution_unavailable',
            'venue_distribution': {},
            'route_distribution': liquidity_observation.get('route_distribution') if isinstance(liquidity_observation, dict) else {},
            'venue_labels': [str(v).lower() for v in (target.get('venue_labels') or []) if str(v).strip()],
            'observed_at': datetime.now(timezone.utc).isoformat(),
            'market_observations': market_observations,
        }
    return {
        'market_observations': market_observations,
        'oracle_observations': oracle_observations,
        'liquidity_observations': [liquidity_observation],
        'venue_observations': [venue_observation],
    }


def _market_provider_configs() -> list[dict[str, str]]:
    raw = str(os.getenv('MARKET_TELEMETRY_SOURCE_URLS') or '').strip()
    configs: list[dict[str, str]] = []
    for chunk in [item.strip() for item in raw.split(',') if item.strip()]:
        if '=' in chunk:
            name, url = chunk.split('=', 1)
            configs.append({'source_name': name.strip() or 'external-market', 'source_type': 'market_api', 'url': url.strip()})
        else:
            configs.append({'source_name': parse.urlparse(chunk).netloc or 'external-market', 'source_type': 'market_api', 'url': chunk})
    return [item for item in configs if item.get('url')]


def _fetch_market_observations(target: dict[str, Any]) -> list[dict[str, Any]]:
    asset_identifier = str(target.get('asset_identifier') or target.get('asset_symbol') or target.get('id') or '').strip()
    providers = _market_provider_configs()
    now = datetime.now(timezone.utc)
    if not providers:
        return [{
            'provider_name': 'external_market_provider',
            'source_name': 'external_market_provider',
            'source_type': 'market_api',
            'asset_identifier': asset_identifier or None,
            'telemetry_kind': 'external_market',
            'status': 'insufficient_real_evidence',
            'reason': 'external_market_provider_not_configured',
            'observed_at': now.isoformat(),
            'provenance': {'provider_layer': 'evm_activity_provider'},
        }]
    observations: list[dict[str, Any]] = []
    for provider in providers:
        fetcher = HttpJsonMarketTelemetryProvider(
            source_name=str(provider.get('source_name') or 'external-market'),
            source_type=str(provider.get('source_type') or 'market_api'),
            url=str(provider.get('url') or ''),
        )
        try:
            fetched = fetcher.fetch(asset_identifier=asset_identifier, now=now)
            if fetched:
                observations.extend(fetched)
                continue
            observations.append(
                {
                    'provider_name': str(provider.get('source_name') or 'external-market'),
                    'source_name': str(provider.get('source_name') or 'external-market'),
                    'source_type': str(provider.get('source_type') or 'market_api'),
                    'asset_identifier': asset_identifier or None,
                    'telemetry_kind': 'external_market',
                    'status': 'insufficient_real_evidence',
                    'reason': 'provider_returned_no_observations',
                    'observed_at': now.isoformat(),
                    'provenance': {'provider_layer': 'evm_activity_provider', 'provider_url': str(provider.get('url') or '')},
                }
            )
        except Exception:
            observations.append(
                {
                    'provider_name': str(provider.get('source_name') or 'external-market'),
                    'source_name': str(provider.get('source_name') or 'external-market'),
                    'source_type': str(provider.get('source_type') or 'market_api'),
                    'asset_identifier': asset_identifier or None,
                    'telemetry_kind': 'external_market',
                    'status': 'unavailable',
                    'reason': 'provider_unreachable',
                    'observed_at': now.isoformat(),
                    'provenance': {'provider_layer': 'evm_activity_provider', 'provider_url': str(provider.get('url') or '')},
                }
            )
    return observations


def _fetch_oracle_observations(target: dict[str, Any]) -> list[dict[str, Any]]:
    oracle_url = (os.getenv('ORACLE_API_URL') or 'http://localhost:8002').rstrip('/')
    asset_identifier = str(
        target.get('asset_identifier')
        or target.get('asset_symbol')
        or target.get('contract_identifier')
        or target.get('wallet_address')
        or ''
    ).strip()
    if not oracle_url:
        return [{
            'source_name': 'oracle-service',
            'source_type': 'oracle_api',
            'asset_identifier': asset_identifier or None,
            'observed_value': None,
            'observed_at': None,
            'freshness_seconds': None,
            'status': 'no_real_telemetry',
            'provenance': {'provider_layer': 'evm_activity_provider', 'reason': 'ORACLE_API_URL missing'},
            'update_interval_seconds': None,
            'block_number': None,
        }]
    params = parse.urlencode({'asset_identifier': asset_identifier}) if asset_identifier else ''
    url = f'{oracle_url}/oracle/observations'
    if params:
        url = f'{url}?{params}'
    try:
        req = request.Request(url, headers={'Accept': 'application/json'})
        with request.urlopen(req, timeout=10) as resp:  # nosec B310
            body = json.loads(resp.read().decode('utf-8'))
    except Exception:
        return [{
            'source_name': 'oracle-service',
            'source_type': 'oracle_api',
            'asset_identifier': asset_identifier or None,
            'observed_value': None,
            'observed_at': None,
            'freshness_seconds': None,
            'status': 'insufficient_real_evidence',
            'provenance': {'provider_layer': 'evm_activity_provider', 'reason': 'oracle_service_unreachable'},
            'update_interval_seconds': None,
            'block_number': None,
        }]
    observations = body.get('observations') if isinstance(body, dict) else []
    status = str(body.get('status') or 'ok') if isinstance(body, dict) else 'ok'
    if not isinstance(observations, list):
        observations = []
    normalized: list[dict[str, Any]] = []
    for item in observations:
        if not isinstance(item, dict):
            continue
        normalized.append(
            {
                'source_name': item.get('source_name'),
                'source_type': item.get('source_type'),
                'asset_identifier': item.get('asset_identifier') or asset_identifier,
                'observed_value': item.get('observed_value'),
                'observed_at': item.get('observed_at'),
                'freshness_seconds': item.get('freshness_seconds'),
                'status': item.get('status') or status,
                'provenance': item.get('provenance') if isinstance(item.get('provenance'), dict) else {},
                'update_interval_seconds': item.get('update_interval_seconds'),
                'block_number': item.get('block_number'),
            }
        )
    if normalized:
        return normalized
    return [{
        'source_name': 'oracle-service',
        'source_type': 'oracle_api',
        'asset_identifier': asset_identifier or None,
        'observed_value': None,
        'observed_at': None,
        'freshness_seconds': None,
        'status': str(body.get('status') or 'insufficient_real_evidence') if isinstance(body, dict) else 'insufficient_real_evidence',
        'provenance': {'provider_layer': 'evm_activity_provider', 'reason': str(body.get('reason') or 'no_observations') if isinstance(body, dict) else 'no_observations'},
        'update_interval_seconds': None,
        'block_number': None,
    }]


def _build_liquidity_observation(target: dict[str, Any], events: list[ActivityEvent]) -> dict[str, Any] | None:
    if not events:
        return None
    window_seconds = max(60, int(os.getenv('EVM_LIQUIDITY_WINDOW_SECONDS', '1800')))
    now = datetime.now(timezone.utc)
    window_start = now.timestamp() - window_seconds
    transfer_events = [
        event for event in events
        if str((event.payload or {}).get('kind_hint') or '').lower() == 'erc20_transfer'
        and event.observed_at.timestamp() >= window_start
    ]
    if not transfer_events:
        return None
    total_volume = 0.0
    counterparties: set[str] = set()
    outbound_by_destination: dict[str, float] = {}
    route_counts: dict[str, int] = {}
    venue_counts: dict[str, int] = {}
    outflow_volume = 0.0
    for event in transfer_events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        try:
            amount = float(payload.get('amount') or 0)
        except Exception:
            amount = 0.0
        total_volume += max(amount, 0.0)
        from_addr = str(payload.get('from') or payload.get('owner') or '').lower()
        to_addr = str(payload.get('to') or '').lower()
        source_class = 'protected_wallet' if from_addr == str(target.get('wallet_address') or '').lower() else 'external'
        destination_class = 'monitored_venue' if to_addr in {str(v).lower() for v in (target.get('venue_labels') or []) if str(v).strip()} else ('protected_wallet' if to_addr == str(target.get('wallet_address') or '').lower() else 'unknown_path')
        route_key = f'{source_class}->{destination_class}'
        route_counts[route_key] = route_counts.get(route_key, 0) + 1
        if from_addr:
            counterparties.add(from_addr)
        if to_addr:
            counterparties.add(to_addr)
            outbound_by_destination[to_addr] = outbound_by_destination.get(to_addr, 0.0) + max(amount, 0.0)
            venue_counts[to_addr] = venue_counts.get(to_addr, 0) + 1
        if from_addr == str(target.get('wallet_address') or '').lower():
            outflow_volume += max(amount, 0.0)
    dominant_destination_volume = max(outbound_by_destination.values()) if outbound_by_destination else 0.0
    concentration_ratio = dominant_destination_volume / total_volume if total_volume > 0 else 0.0
    transfer_count = len(transfer_events)
    route_distribution = {key: round(value / transfer_count, 6) for key, value in route_counts.items()}
    venue_distribution = {key: round(value / transfer_count, 6) for key, value in venue_counts.items()}
    abnormal_outflow_ratio = (outflow_volume / total_volume) if total_volume > 0 else 0.0
    burst_baseline = max(1, int(os.getenv('EVM_BURST_BASELINE_TRANSFER_COUNT', '5')))
    burst_score = round(transfer_count / burst_baseline, 6)
    return {
        'provider_name': 'evm_activity_provider',
        'telemetry_kind': 'liquidity_rollup',
        'window_seconds': window_seconds,
        'window_event_count': len(transfer_events),
        'rolling_volume': total_volume,
        'rolling_transfer_count': transfer_count,
        'transfer_count': transfer_count,
        'unique_counterparties': len(counterparties),
        'concentration_ratio': concentration_ratio,
        'route_distribution': route_distribution,
        'venue_distribution': venue_distribution,
        'abnormal_outflow_ratio': abnormal_outflow_ratio,
        'burst_score': burst_score,
        'observed_at': now.isoformat(),
        'asset_identifier': str(target.get('asset_identifier') or target.get('asset_symbol') or target.get('id') or ''),
        'status': 'ok' if transfer_count >= int(os.getenv('EVM_MIN_TRANSFER_EVIDENCE', '3')) else 'insufficient_real_evidence',
        'telemetry_state': 'real_telemetry_present' if transfer_count >= int(os.getenv('EVM_MIN_TRANSFER_EVIDENCE', '3')) else 'insufficient_real_evidence',
    }


def _build_venue_observation(
    target: dict[str, Any],
    events: list[ActivityEvent],
    liquidity_observation: dict[str, Any] | None,
    market_observations: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if not events:
        return None
    venue_labels = target.get('venue_labels')
    configured = [str(v).lower() for v in venue_labels] if isinstance(venue_labels, list) else []
    if not configured:
        return None
    counts = {item: 0 for item in configured}
    unknown = 0
    for event in events:
        payload = event.payload if isinstance(event.payload, dict) else {}
        destination = str(payload.get('to') or '').lower()
        matched = False
        for venue in configured:
            if destination == venue:
                counts[venue] += 1
                matched = True
                break
        if not matched and destination:
            unknown += 1
    total = sum(counts.values()) + unknown
    if total <= 0:
        return None
    distribution = {venue: round(count / total, 6) for venue, count in counts.items()}
    if unknown:
        distribution['unknown'] = round(unknown / total, 6)
    return {
        'provider_name': 'evm_activity_provider',
        'telemetry_kind': 'venue_rollup',
        'venue_distribution': distribution,
        'route_distribution': (liquidity_observation or {}).get('route_distribution', {}),
        'route_classification': {
            'known_venue_share': round(1 - distribution.get('unknown', 0.0), 6),
            'unknown_path_share': distribution.get('unknown', 0.0),
            'expected_flow_patterns': target.get('expected_flow_patterns') if isinstance(target.get('expected_flow_patterns'), list) else [],
        },
        'venue_labels': configured,
        'observed_at': datetime.now(timezone.utc).isoformat(),
        'rolling_volume': float((liquidity_observation or {}).get('rolling_volume') or 0.0),
        'status': 'ok',
        'telemetry_state': 'real_telemetry_present',
        'market_observations': market_observations,
    }
