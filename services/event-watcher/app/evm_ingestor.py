from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib import request

from services.api.app.evm_activity_provider import (
    APPROVAL_TOPIC,
    TRANSFER_TOPIC,
    _extract_selector,
    _hex_to_int,
    _make_event_id,
    _topic_to_address,
)
from services.api.app.monitoring_runner import ActivityEvent, mark_receipt_removed, process_ingested_event
from services.api.app.pilot import ensure_pilot_schema, log_audit, pg_connection

logger = logging.getLogger(__name__)


class EvmIngestor:
    def __init__(self, *, chain_network: str, rpc_url: str, ws_url: str | None, watcher_name: str):
        self.chain_network = chain_network
        self.rpc_url = rpc_url
        self.ws_url = ws_url
        self.watcher_name = watcher_name
        self.confirmations_required = max(0, int(os.getenv('EVM_CONFIRMATIONS_REQUIRED', '3')))
        self.backfill_chunk = max(1, int(os.getenv('EVM_BACKFILL_MAX_BLOCK_RANGE', '2000')))
        self.lease_seconds = max(10, int(os.getenv('EVENT_WATCHER_LEADER_LEASE_SECONDS', '30')))
        self.heartbeat_seconds = max(5, int(os.getenv('EVENT_WATCHER_HEARTBEAT_SECONDS', '10')))
        self.evidence_window_seconds = max(60, int(os.getenv('MONITORING_EVIDENCE_WINDOW_SECONDS', '900')))
        self.gap_threshold_blocks = max(self.confirmations_required + 1, int(os.getenv('EVM_BACKFILL_GAP_THRESHOLD_BLOCKS', '24')))
        self.state: dict[str, Any] = {
            'source_status': 'degraded',
            'degraded': False,
            'degraded_reason': None,
            'last_processed_block': None,
            'last_head_block': None,
            'last_heartbeat_at': None,
            'metrics': {'events_ingested': 0, 'ws_reconnects': 0, 'rpc_backfills': 0},
        }

    def _rpc_call(self, method: str, params: list[Any]) -> Any:
        payload = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}).encode('utf-8')
        req = request.Request(self.rpc_url, data=payload, headers={'Content-Type': 'application/json'})
        with request.urlopen(req, timeout=15) as resp:  # nosec B310
            body = json.loads(resp.read().decode('utf-8'))
        if body.get('error'):
            raise RuntimeError(f"json-rpc error: {body['error']}")
        return body.get('result')

    def _watched_targets(self) -> list[dict[str, Any]]:
        with pg_connection() as connection:
            ensure_pilot_schema(connection)
            rows = connection.execute(
                '''
                SELECT id, workspace_id, name, target_type, chain_network, wallet_address, contract_identifier,
                       monitoring_enabled, enabled, is_active, updated_by_user_id, created_by_user_id, severity_threshold
                FROM targets
                WHERE deleted_at IS NULL
                  AND target_type IN ('wallet', 'contract')
                  AND monitoring_enabled = TRUE
                  AND enabled = TRUE
                  AND is_active = TRUE
                  AND COALESCE(chain_network, 'ethereum') = %s
                ''',
                (self.chain_network,),
            ).fetchall()
            return [dict(r) for r in rows]

    def _safe_to_process_block(self, block_number: int | None, head: int | None) -> bool:
        if block_number is None or head is None:
            return True
        return block_number <= max(0, head - self.confirmations_required)

    async def _ensure_leader_lease(self) -> bool:
        with pg_connection() as connection:
            ensure_pilot_schema(connection)
            locked = connection.execute('SELECT pg_try_advisory_lock(hashtext(%s)) AS locked', (f'event_watcher:{self.chain_network}',)).fetchone()
            has_lock = bool((locked or {}).get('locked'))
            if not has_lock:
                connection.execute(
                    '''
                    INSERT INTO monitoring_watcher_state (watcher_name, running, status, source_status, ingestion_mode, degraded, degraded_reason, last_heartbeat_at, updated_at)
                    VALUES (%s, FALSE, 'standby', 'degraded', 'live', TRUE, 'standby_not_leader', NOW(), NOW())
                    ON CONFLICT (watcher_name)
                    DO UPDATE SET running = FALSE, status = 'standby', degraded = TRUE, degraded_reason = 'standby_not_leader', last_heartbeat_at = NOW(), updated_at = NOW()
                    ''',
                    (self.watcher_name,),
                )
                connection.commit()
                return False
            row = connection.execute(
                '''
                INSERT INTO monitoring_chain_checkpoints (
                    chain_network, last_updated_at, leader_watcher_name, leader_lease_expires_at
                )
                VALUES (%s, NOW(), %s, NOW() + (%s * INTERVAL '1 second'))
                ON CONFLICT (chain_network)
                DO UPDATE SET leader_watcher_name = EXCLUDED.leader_watcher_name,
                              leader_lease_expires_at = EXCLUDED.leader_lease_expires_at,
                              last_updated_at = NOW()
                RETURNING leader_lease_expires_at
                ''',
                (self.chain_network, self.watcher_name, self.lease_seconds),
            ).fetchone()
            connection.commit()
            logger.info('leader_acquired chain=%s watcher=%s lease_expires_at=%s', self.chain_network, self.watcher_name, row.get('leader_lease_expires_at'))
            return True

    def _record_heartbeat(self) -> None:
        lag = None
        if self.state.get('last_head_block') is not None and self.state.get('last_processed_block') is not None:
            lag = max(0, int(self.state['last_head_block']) - int(self.state['last_processed_block']))
        with pg_connection() as connection:
            ensure_pilot_schema(connection)
            connection.execute(
                '''
                INSERT INTO monitoring_watcher_state (
                    watcher_name, running, status, source_status, ingestion_mode, degraded, degraded_reason,
                    last_started_at, last_heartbeat_at, last_cycle_at, last_processed_block, metrics, updated_at
                )
                VALUES (%s, TRUE, 'running', %s, 'live', %s, %s, COALESCE((SELECT last_started_at FROM monitoring_watcher_state WHERE watcher_name=%s), NOW()), NOW(), NOW(), %s, %s::jsonb, NOW())
                ON CONFLICT (watcher_name)
                DO UPDATE SET running = TRUE, status = 'running', source_status = EXCLUDED.source_status,
                              degraded = EXCLUDED.degraded, degraded_reason = EXCLUDED.degraded_reason,
                              last_heartbeat_at = NOW(), last_cycle_at = NOW(), last_processed_block = EXCLUDED.last_processed_block,
                              metrics = EXCLUDED.metrics, updated_at = NOW()
                ''',
                (
                    self.watcher_name,
                    self.state.get('source_status') or 'degraded',
                    bool(self.state.get('degraded')),
                    self.state.get('degraded_reason'),
                    self.watcher_name,
                    self.state.get('last_processed_block'),
                    json.dumps({**self.state['metrics'], 'lag_blocks': lag}),
                ),
            )
            connection.execute(
                '''
                INSERT INTO monitoring_chain_checkpoints (
                    chain_network, last_finalized_block, last_safe_block, last_head_block, last_updated_at,
                    leader_watcher_name, leader_lease_expires_at
                )
                VALUES (%s, %s, %s, %s, NOW(), %s, NOW() + (%s * INTERVAL '1 second'))
                ON CONFLICT (chain_network)
                DO UPDATE SET last_finalized_block = EXCLUDED.last_finalized_block,
                              last_safe_block = EXCLUDED.last_safe_block,
                              last_head_block = EXCLUDED.last_head_block,
                              last_updated_at = NOW(),
                              leader_watcher_name = EXCLUDED.leader_watcher_name,
                              leader_lease_expires_at = EXCLUDED.leader_lease_expires_at
                ''',
                (
                    self.chain_network,
                    self.state.get('last_processed_block'),
                    self.state.get('last_processed_block'),
                    self.state.get('last_head_block'),
                    self.watcher_name,
                    self.lease_seconds,
                ),
            )
            connection.commit()
        self.state['last_heartbeat_at'] = datetime.now(timezone.utc).isoformat()

    def _build_event_from_log(self, target: dict[str, Any], log: dict[str, Any], ingestion_source: str) -> ActivityEvent:
        block_number = _hex_to_int(log.get('blockNumber')) or 0
        tx_hash = str(log.get('transactionHash') or '')
        log_index = _hex_to_int(log.get('logIndex'))
        topic0 = str((log.get('topics') or [''])[0]).lower()
        owner = _topic_to_address((log.get('topics') or [None, None])[1])
        spender_or_to = _topic_to_address((log.get('topics') or [None, None, None])[2])
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
            'function_selector': _extract_selector(log.get('input')),
            'decode_status': 'partial',
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

    async def _backfill(self, from_block: int, to_block: int) -> int:
        if to_block < from_block:
            return 0
        targets = self._watched_targets()
        processed = 0
        for target in targets:
            watched_address = str(target.get('wallet_address') or target.get('contract_identifier') or '').lower()
            if not watched_address.startswith('0x'):
                continue
            for start in range(from_block, to_block + 1, self.backfill_chunk):
                end = min(to_block, start + self.backfill_chunk - 1)
                params = [{
                    'fromBlock': hex(start),
                    'toBlock': hex(end),
                    'topics': [[TRANSFER_TOPIC, APPROVAL_TOPIC]],
                }]
                logs = self._rpc_call('eth_getLogs', params) or []
                for log in logs:
                    topics = [str(t).lower() for t in (log.get('topics') or [])]
                    addr = str(log.get('address') or '').lower()
                    if watched_address not in topics and watched_address != addr:
                        continue
                    event = self._build_event_from_log(target, log, 'rpc_backfill')
                    with pg_connection() as connection:
                        ensure_pilot_schema(connection)
                        process_ingested_event(connection, target=target, event=event, ingestion_mode='live')
                        connection.commit()
                    processed += 1
                    self.state['last_processed_block'] = max(int(self.state.get('last_processed_block') or 0), int(event.payload.get('block_number') or 0))
        if processed:
            self.state['metrics']['rpc_backfills'] += 1
        return processed

    def _handle_removed_log(self, log: dict[str, Any]) -> None:
        tx_hash = str(log.get('transactionHash') or '') or None
        log_index = _hex_to_int(log.get('logIndex'))
        cursor = f"{_hex_to_int(log.get('blockNumber')) or 0}:{tx_hash or ''}:{-1 if log_index is None else log_index}"
        with pg_connection() as connection:
            ensure_pilot_schema(connection)
            targets = connection.execute(
                "SELECT id, workspace_id FROM targets WHERE deleted_at IS NULL AND monitoring_enabled = TRUE AND enabled = TRUE AND is_active = TRUE AND COALESCE(chain_network, 'ethereum') = %s",
                (self.chain_network,),
            ).fetchall()
            for target in targets:
                mark_receipt_removed(
                    connection,
                    target_id=str(target['id']),
                    event_cursor=cursor,
                    tx_hash=tx_hash,
                    log_index=log_index,
                    metadata={'chain_network': self.chain_network, 'removed': True, 'watcher_name': self.watcher_name},
                )
                log_audit(
                    connection,
                    action='reorg_event',
                    entity_type='target',
                    entity_id=str(target['id']),
                    request=None,
                    user_id=None,
                    workspace_id=str(target['workspace_id']),
                    metadata={'chain_network': self.chain_network, 'tx_hash': tx_hash, 'log_index': log_index, 'event_cursor': cursor},
                )
                connection.execute(
                    '''
                    UPDATE incidents
                    SET timeline = COALESCE(timeline, '[]'::jsonb) || %s::jsonb,
                        updated_at = NOW()
                    WHERE workspace_id = %s
                      AND status IN ('open', 'acknowledged')
                      AND EXISTS (
                          SELECT 1 FROM alerts a
                          WHERE a.id = incidents.alert_id
                            AND a.target_id = %s
                      )
                    ''',
                    (json.dumps([{'at': datetime.now(timezone.utc).isoformat(), 'event': 'chain_reorg_invalidated_evidence', 'event_cursor': cursor}]), str(target['workspace_id']), str(target['id'])),
                )
            connection.commit()
        logger.info('reorg_removed_receipt target_id=%s cursor=%s tx=%s log_index=%s', str(targets[0]['id']) if targets else 'n/a', cursor, tx_hash, log_index)

    async def _ws_subscribe(self) -> None:
        if not self.ws_url:
            raise RuntimeError('ws_url_not_configured')
        import websockets

        async with websockets.connect(self.ws_url, ping_interval=20, ping_timeout=20) as ws:
            await ws.send(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'eth_subscribe', 'params': ['newHeads']}))
            await ws.send(json.dumps({'jsonrpc': '2.0', 'id': 2, 'method': 'eth_subscribe', 'params': ['logs', {'topics': [[TRANSFER_TOPIC, APPROVAL_TOPIC]]}]}))
            logger.info('ws_connected chain=%s subscriptions=newHeads,logs', self.chain_network)
            self.state['source_status'] = 'websocket'
            sub_ids: dict[str, str] = {}
            while True:
                msg = json.loads(await ws.recv())
                if msg.get('id') == 1 and msg.get('result'):
                    sub_ids['newHeads'] = str(msg['result'])
                    continue
                if msg.get('id') == 2 and msg.get('result'):
                    sub_ids['logs'] = str(msg['result'])
                    continue
                params = msg.get('params') or {}
                result = params.get('result') or {}
                sub = params.get('subscription')
                if sub == sub_ids.get('newHeads'):
                    head = _hex_to_int(result.get('number'))
                    if head is not None:
                        self.state['last_head_block'] = head
                        if self.state.get('last_processed_block') is not None and head - int(self.state['last_processed_block']) > self.gap_threshold_blocks:
                            logger.warning('ws_buffer_overflow_or_disconnect -> backfill from_block=%s to_block=%s', int(self.state['last_processed_block']) + 1, head)
                            await self._backfill(int(self.state['last_processed_block']) + 1, head)
                elif sub == sub_ids.get('logs'):
                    if bool(result.get('removed')):
                        self._handle_removed_log(result)
                        continue
                    block_number = _hex_to_int(result.get('blockNumber'))
                    if not self._safe_to_process_block(block_number, self.state.get('last_head_block')):
                        continue
                    for target in self._watched_targets():
                        watched = str(target.get('wallet_address') or target.get('contract_identifier') or '').lower()
                        topics = [str(topic).lower() for topic in (result.get('topics') or [])]
                        address = str(result.get('address') or '').lower()
                        if watched not in topics and watched != address:
                            continue
                        event = self._build_event_from_log(target, result, 'websocket')
                        with pg_connection() as connection:
                            ensure_pilot_schema(connection)
                            process_ingested_event(connection, target=target, event=event, ingestion_mode='live')
                            connection.commit()
                        self.state['metrics']['events_ingested'] += 1
                        self.state['last_processed_block'] = max(int(self.state.get('last_processed_block') or 0), int(event.payload.get('block_number') or 0))

    async def run_forever(self) -> None:
        retry = 1.0
        while True:
            is_leader = await self._ensure_leader_lease()
            if not is_leader:
                await asyncio.sleep(self.heartbeat_seconds)
                continue
            try:
                self.state['degraded'] = False
                self.state['degraded_reason'] = None
                self.state['last_head_block'] = _hex_to_int(self._rpc_call('eth_blockNumber', []))
                if self.state.get('last_processed_block') is None and self.state.get('last_head_block') is not None:
                    self.state['last_processed_block'] = max(0, int(self.state['last_head_block']) - self.confirmations_required)
                self._record_heartbeat()
                if self.ws_url:
                    await asyncio.wait_for(self._ws_subscribe(), timeout=self.heartbeat_seconds)
                else:
                    head = _hex_to_int(self._rpc_call('eth_blockNumber', [])) or 0
                    self.state['source_status'] = 'rpc_backfill'
                    await self._backfill(max(0, head - self.confirmations_required), head)
                    await asyncio.sleep(self.heartbeat_seconds)
                retry = 1.0
            except asyncio.TimeoutError:
                self._record_heartbeat()
                continue
            except Exception as exc:
                self.state['metrics']['ws_reconnects'] += 1
                self.state['degraded'] = True
                self.state['degraded_reason'] = str(exc)[:160]
                head = _hex_to_int(self._rpc_call('eth_blockNumber', [])) if self.rpc_url else None
                last = int(self.state.get('last_processed_block') or max(0, (head or 0) - self.confirmations_required))
                if head is not None and head >= last:
                    logger.warning('ws_buffer_overflow_or_disconnect -> backfill from_block=%s to_block=%s', last, head)
                    await self._backfill(last, head)
                self._record_heartbeat()
                await asyncio.sleep(min(30.0, retry) + random.random())
                retry = min(30.0, retry * 2)
