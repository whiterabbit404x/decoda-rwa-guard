from __future__ import annotations

import asyncio
import json
import logging
import os
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import request

from fastapi import FastAPI

from phase1_local.dev_support import load_env_file
from services.api.app.activity_providers import monitoring_ingestion_runtime
from services.api.app.evm_activity_provider import (
    APPROVAL_TOPIC,
    TRANSFER_TOPIC,
    _extract_selector,
    _hex_to_int,
    _make_event_id,
    _topic_to_address,
)
from services.api.app.monitoring_runner import ActivityEvent, process_ingested_event
from services.api.app.pilot import ensure_pilot_schema, pg_connection

load_env_file()

logger = logging.getLogger(__name__)
SERVICE_NAME = 'event-watcher'
PORT = int(os.getenv('PORT', 8005))
CHECKPOINT_PATH = Path(os.getenv('EVENT_WATCHER_CHECKPOINT_PATH', '/tmp/event-watcher-checkpoint.json'))
WATCHER_NAME = os.getenv('EVENT_WATCHER_NAME', 'event-watcher')
POLL_INTERVAL_SECONDS = max(2, int(os.getenv('EVENT_WATCHER_POLL_INTERVAL_SECONDS', '8')))
BACKFILL_CHUNK = max(1, int(os.getenv('EVM_BLOCK_SCAN_CHUNK_SIZE', '25')))

app = FastAPI(title=f'{SERVICE_NAME} service')


STATE: dict[str, Any] = {
    'running': False,
    'ready': False,
    'started_at': None,
    'last_heartbeat': None,
    'last_error': None,
    'ingestion_mode': None,
    'source_status': 'degraded',
    'degraded': False,
    'degraded_reason': None,
    'checkpoints': {'targets': {}, 'last_block': None, 'last_log_cursor': None},
    'metrics': {
        'events_ingested': 0,
        'ws_reconnects': 0,
        'rpc_backfills': 0,
        'duplicate_suppressions': 0,
    },
}
_RUNTIME_TASK: asyncio.Task[Any] | None = None
_STOP = asyncio.Event()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rpc_call(method: str, params: list[Any]) -> Any:
    rpc_url = (os.getenv('EVM_RPC_URL') or '').strip()
    payload = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}).encode('utf-8')
    req = request.Request(rpc_url, data=payload, headers={'Content-Type': 'application/json'})
    with request.urlopen(req, timeout=12) as resp:  # nosec B310
        body = json.loads(resp.read().decode('utf-8'))
    if body.get('error'):
        raise RuntimeError(f"json-rpc error: {body['error']}")
    return body.get('result')


def _load_checkpoint() -> None:
    if CHECKPOINT_PATH.exists():
        payload = json.loads(CHECKPOINT_PATH.read_text())
        if isinstance(payload, dict):
            STATE['checkpoints'].update(payload)


def _persist_checkpoint() -> None:
    CHECKPOINT_PATH.parent.mkdir(parents=True, exist_ok=True)
    CHECKPOINT_PATH.write_text(json.dumps(STATE['checkpoints']))


def _target_cursor(target_id: str) -> dict[str, Any]:
    targets = STATE['checkpoints'].setdefault('targets', {})
    value = targets.setdefault(target_id, {'last_block': None, 'last_cursor': None})
    return value


def _resolve_runtime() -> dict[str, Any]:
    runtime = monitoring_ingestion_runtime()
    runtime['ws_url'] = bool((os.getenv('EVM_WS_URL') or '').strip())
    return runtime


def _load_targets() -> list[dict[str, Any]]:
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        rows = connection.execute(
            '''
            SELECT id, workspace_id, name, target_type, chain_network, wallet_address, contract_identifier,
                   monitoring_enabled, enabled, is_active
            FROM targets
            WHERE deleted_at IS NULL
              AND target_type IN ('wallet', 'contract')
              AND monitoring_enabled = TRUE
              AND enabled = TRUE
              AND is_active = TRUE
            '''
        ).fetchall()
        return [dict(row) for row in rows]


def _emit_event(target: dict[str, Any], event: ActivityEvent) -> None:
    with pg_connection() as connection:
        ensure_pilot_schema(connection)
        result = process_ingested_event(connection, target=target, event=event, ingestion_mode=str(STATE.get('ingestion_mode') or 'live'))
        connection.commit()
    if result.get('status') == 'duplicate_suppressed':
        STATE['metrics']['duplicate_suppressions'] += 1
    else:
        STATE['metrics']['events_ingested'] += 1


def _build_event_from_log(target: dict[str, Any], log: dict[str, Any], ingestion_source: str) -> ActivityEvent:
    block_number = _hex_to_int(log.get('blockNumber')) or 0
    tx_hash = str(log.get('transactionHash') or '')
    log_index = _hex_to_int(log.get('logIndex'))
    topic0 = str((log.get('topics') or [''])[0]).lower()
    owner = _topic_to_address((log.get('topics') or [None, None])[1])
    spender_or_to = _topic_to_address((log.get('topics') or [None, None, None])[2])
    function_selector = _extract_selector(log.get('input'))
    cursor = f"{block_number}:{tx_hash}:{-1 if log_index is None else log_index}"
    payload = {
        'chain_id': int(os.getenv('EVM_CHAIN_ID', '1')),
        'block_number': block_number,
        'tx_hash': tx_hash,
        'log_index': log_index,
        'from': owner,
        'to': spender_or_to if topic0 == TRANSFER_TOPIC else None,
        'contract_address': str(log.get('address') or '').lower() or None,
        'spender': spender_or_to if topic0 == APPROVAL_TOPIC else None,
        'owner': owner,
        'function_selector': function_selector,
        'decoded_function_name': None,
        'decode_status': 'partial' if function_selector else 'none',
        'ingestion_source': ingestion_source,
    }
    return ActivityEvent(
        event_id=_make_event_id(str(target['id']), cursor, 'transaction'),
        kind='transaction',
        observed_at=datetime.now(timezone.utc),
        ingestion_source=ingestion_source,
        cursor=cursor,
        payload=payload,
    )


def _backfill_range(target: dict[str, Any], from_block: int, to_block: int) -> int:
    if to_block < from_block:
        return 0
    watched_address = str(target.get('wallet_address') or target.get('contract_identifier') or '').lower()
    count = 0
    for start in range(from_block, to_block + 1, BACKFILL_CHUNK):
        end = min(to_block, start + BACKFILL_CHUNK - 1)
        params = [{
            'fromBlock': hex(start),
            'toBlock': hex(end),
            'topics': [[TRANSFER_TOPIC, APPROVAL_TOPIC], None, [f"0x{'0'*24}{watched_address[2:]}"]],
        }]
        inbound = _rpc_call('eth_getLogs', params) or []
        params_out = [{
            'fromBlock': hex(start),
            'toBlock': hex(end),
            'topics': [[TRANSFER_TOPIC, APPROVAL_TOPIC], [f"0x{'0'*24}{watched_address[2:]}"], None],
        }]
        outbound = _rpc_call('eth_getLogs', params_out) or []
        for log in [*inbound, *outbound]:
            event = _build_event_from_log(target, log, ingestion_source='rpc_backfill')
            _emit_event(target, event)
            count += 1
            _target_cursor(str(target['id']))['last_cursor'] = event.cursor
            _target_cursor(str(target['id']))['last_block'] = _hex_to_int(log.get('blockNumber'))
    if count:
        STATE['metrics']['rpc_backfills'] += 1
    return count


async def _polling_loop() -> None:
    STATE['source_status'] = 'polling'
    while not _STOP.is_set():
        targets = _load_targets()
        latest = _hex_to_int(_rpc_call('eth_blockNumber', [])) or 0
        STATE['checkpoints']['last_block'] = latest
        cycle_events = 0
        for target in targets:
            cursor = _target_cursor(str(target['id']))
            from_block = int(cursor['last_block'] or max(0, latest - 2))
            if latest >= from_block:
                cycle_events += _backfill_range(target, from_block, latest)
                cursor['last_block'] = latest
        if cycle_events == 0:
            STATE['source_status'] = 'no_evidence'
            STATE['degraded'] = True
            STATE['degraded_reason'] = 'no_real_evidence_observed'
        else:
            STATE['source_status'] = 'polling'
            STATE['degraded'] = False
            STATE['degraded_reason'] = None
        _persist_checkpoint()
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


async def _ws_loop() -> None:
    ws_url = (os.getenv('EVM_WS_URL') or '').strip()
    if not ws_url:
        await _polling_loop()
        return
    try:
        import websockets
    except Exception:
        STATE['source_status'] = 'degraded'
        STATE['degraded'] = True
        STATE['degraded_reason'] = 'websocket_dependency_missing'
        await _polling_loop()
        return

    retry = 1
    sub_ids: dict[str, str] = {}
    targets_cache: list[dict[str, Any]] = []
    while not _STOP.is_set():
        try:
            async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as ws:
                STATE['source_status'] = 'websocket'
                STATE['degraded'] = False
                STATE['degraded_reason'] = None
                targets_cache = _load_targets()
                await ws.send(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'eth_subscribe', 'params': ['newHeads']}))
                await ws.send(json.dumps({'jsonrpc': '2.0', 'id': 2, 'method': 'eth_subscribe', 'params': ['logs', {'topics': [[TRANSFER_TOPIC, APPROVAL_TOPIC]]}]}))
                while not _STOP.is_set():
                    raw = await asyncio.wait_for(ws.recv(), timeout=30)
                    message = json.loads(raw)
                    if message.get('id') == 1 and message.get('result'):
                        sub_ids['newHeads'] = str(message['result'])
                        continue
                    if message.get('id') == 2 and message.get('result'):
                        sub_ids['logs'] = str(message['result'])
                        continue
                    params = message.get('params') or {}
                    result = params.get('result') or {}
                    if params.get('subscription') == sub_ids.get('newHeads'):
                        block_number = _hex_to_int(result.get('number'))
                        if block_number is not None:
                            STATE['checkpoints']['last_block'] = block_number
                    elif params.get('subscription') == sub_ids.get('logs'):
                        for target in targets_cache:
                            watched = str(target.get('wallet_address') or target.get('contract_identifier') or '').lower()
                            address = str(result.get('address') or '').lower()
                            topics = [str(topic).lower() for topic in (result.get('topics') or [])]
                            if watched in topics or watched == address:
                                event = _build_event_from_log(target, result, ingestion_source='websocket')
                                _emit_event(target, event)
                                _target_cursor(str(target['id']))['last_cursor'] = event.cursor
                                _target_cursor(str(target['id']))['last_block'] = event.payload.get('block_number')
                    STATE['last_heartbeat'] = _utc_now()
                    _persist_checkpoint()
                retry = 1
        except Exception as exc:
            STATE['last_error'] = str(exc)
            STATE['metrics']['ws_reconnects'] += 1
            STATE['source_status'] = 'rpc_backfill'
            latest = _hex_to_int(_rpc_call('eth_blockNumber', [])) or 0
            for target in targets_cache or _load_targets():
                cursor_block = int(_target_cursor(str(target['id']))['last_block'] or max(0, latest - 2))
                _backfill_range(target, cursor_block, latest)
                _target_cursor(str(target['id']))['last_block'] = latest
            _persist_checkpoint()
            await asyncio.sleep(min(60, retry) + random.random())
            retry = min(60, retry * 2)


async def _run_loop() -> None:
    while not _STOP.is_set():
        STATE['last_heartbeat'] = _utc_now()
        runtime = _resolve_runtime()
        STATE['ingestion_mode'] = runtime.get('mode')
        if runtime.get('mode') == 'demo':
            STATE['source_status'] = 'degraded'
            STATE['degraded'] = True
            STATE['degraded_reason'] = 'demo_mode'
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            continue
        await _ws_loop()


def startup() -> None:
    runtime = _resolve_runtime()
    if runtime['mode'] == 'live' and runtime['degraded']:
        raise RuntimeError(f"event-watcher live mode requires chain connectivity: {runtime.get('reason')}")
    if runtime['mode'] in {'live', 'hybrid'} and not (os.getenv('DATABASE_URL') or '').strip():
        raise RuntimeError('event-watcher requires DATABASE_URL for durable target loading/checkpointing')
    _load_checkpoint()
    STATE['running'] = True
    STATE['ready'] = True
    STATE['started_at'] = _utc_now()
    STATE['last_heartbeat'] = _utc_now()
    STATE['ingestion_mode'] = runtime.get('mode')


@app.on_event('startup')
async def startup_event() -> None:
    startup()
    global _RUNTIME_TASK
    _STOP.clear()
    _RUNTIME_TASK = asyncio.create_task(_run_loop())


@app.on_event('shutdown')
async def shutdown_event() -> None:
    _STOP.set()
    global _RUNTIME_TASK
    if _RUNTIME_TASK:
        _RUNTIME_TASK.cancel()
        _RUNTIME_TASK = None


@app.get('/health')
def health() -> dict[str, object]:
    STATE['last_heartbeat'] = _utc_now()
    return {
        'status': 'ok' if not STATE.get('degraded') else 'degraded',
        'service': SERVICE_NAME,
        'port': PORT,
        'ready': STATE.get('ready'),
        'running': STATE.get('running'),
        'ingestion_mode': STATE.get('ingestion_mode'),
        'source_status': STATE.get('source_status'),
        'degraded': STATE.get('degraded'),
        'degraded_reason': STATE.get('degraded_reason'),
        'last_error': STATE.get('last_error'),
    }


@app.get('/status')
def status() -> dict[str, Any]:
    STATE['last_heartbeat'] = _utc_now()
    return {**STATE, 'checkpoint_path': str(CHECKPOINT_PATH), 'watcher_name': WATCHER_NAME}


@app.get('/ready')
def ready() -> dict[str, Any]:
    return {'ready': bool(STATE.get('ready')), 'running': bool(STATE.get('running')), 'source_status': STATE.get('source_status')}


@app.post('/internal/checkpoint')
def update_checkpoint(payload: dict[str, Any]) -> dict[str, Any]:
    for key in ('last_block', 'last_log_cursor'):
        if key in payload:
            STATE['checkpoints'][key] = payload[key]
    _persist_checkpoint()
    return {'ok': True, 'checkpoints': STATE['checkpoints']}
