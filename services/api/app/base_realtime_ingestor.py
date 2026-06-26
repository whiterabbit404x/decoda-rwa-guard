"""Real-time Base chain event ingestion via WebSocket.

Separate from the 300s polling worker which continues to run as backup/backfill.
Guards on BASE_REALTIME_ENABLED=false by default.

Architecture note: each event opens a short DB transaction, persists
telemetry/detection/alert, commits, then releases the connection.
No DB connection is held open while waiting on WebSocket messages.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any
from urllib import error as _urllib_error, request

from services.api.app.evm_activity_provider import (
    APPROVAL_TOPIC,
    TRANSFER_TOPIC,
    _extract_selector,
    _hex_to_int,
    _make_event_id,
    _topic_to_address,
)
from services.api.app.monitoring_runner import ActivityEvent, process_ingested_event
from services.api.app.observability import increment, gauge
from services.api.app.pilot import ensure_pilot_schema, pg_connection

logger = logging.getLogger(__name__)

BASE_CHAIN_ID = 8453
BASE_CHAIN_NETWORK = 'base'
REALTIME_INGESTION_SOURCE = 'realtime_websocket'

# Env vars read at ingestor construction time (not module load) so tests can monkeypatch.
_DEFAULT_CONFIRMATIONS = 1
_DEFAULT_MAX_EVENTS_PER_MINUTE = 1000
_DEFAULT_HEARTBEAT_SECONDS = 10
_DEFAULT_BACKFILL_CHUNK = 2000
_DEFAULT_GAP_THRESHOLD_BLOCKS = 24


def _resolve_int_env(name: str, default: int) -> int:
    raw = (os.getenv(name) or '').strip()
    try:
        return max(0, int(raw)) if raw else default
    except (TypeError, ValueError):
        return default


class BaseRealtimeIngestor:
    """WebSocket-driven Base chain ingestion worker.

    Designed to co-exist with the 300s polling worker:
    - Uses the same ``process_ingested_event`` function (same dedupe logic).
    - Polling worker seeing the same tx later logs duplicate_suppressed, not a new alert.
    - Each event is committed in its own short transaction (no idle-in-transaction).
    - On DB failure: retries once with a fresh connection, then logs and continues.
    - Workspace isolation: only targets matching chain_network='base' are loaded.
    """

    def __init__(
        self,
        *,
        rpc_url: str,
        ws_url: str,
        watcher_name: str,
        confirmations_required: int | None = None,
        max_events_per_minute: int | None = None,
    ) -> None:
        self.rpc_url = rpc_url
        self.ws_url = ws_url
        self.watcher_name = watcher_name
        self.chain_network = BASE_CHAIN_NETWORK
        self.chain_id = BASE_CHAIN_ID

        self.confirmations_required = (
            confirmations_required
            if confirmations_required is not None
            else _resolve_int_env('BASE_REALTIME_CONFIRMATIONS', _DEFAULT_CONFIRMATIONS)
        )
        self.max_events_per_minute = (
            max_events_per_minute
            if max_events_per_minute is not None
            else _resolve_int_env('BASE_REALTIME_MAX_EVENTS_PER_MINUTE', _DEFAULT_MAX_EVENTS_PER_MINUTE)
        )
        self.heartbeat_seconds = _resolve_int_env('EVENT_WATCHER_HEARTBEAT_SECONDS', _DEFAULT_HEARTBEAT_SECONDS)
        self.backfill_chunk = max(1, _resolve_int_env('EVM_BACKFILL_MAX_BLOCK_RANGE', _DEFAULT_BACKFILL_CHUNK))
        self.gap_threshold_blocks = max(
            self.confirmations_required + 1,
            _resolve_int_env('EVM_BACKFILL_GAP_THRESHOLD_BLOCKS', _DEFAULT_GAP_THRESHOLD_BLOCKS),
        )

        # Sliding-window rate limiter: stores monotonic timestamps of recent events.
        self._event_timestamps: deque[float] = deque()

        self.state: dict[str, Any] = {
            'source_status': 'degraded',
            'degraded': False,
            'degraded_reason': None,
            'last_processed_block': None,
            'last_head_block': None,
            'last_heartbeat_at': None,
            'last_event_at': None,
            'metrics': {
                'events_ingested': 0,
                'ws_reconnects': 0,
                'rpc_backfills': 0,
                'rate_limited_dropped': 0,
                'persist_retried': 0,
                'persist_failed': 0,
            },
        }

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    def _rpc_call(self, method: str, params: list[Any]) -> Any:
        payload = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}).encode()
        req = request.Request(self.rpc_url, data=payload, headers={'Content-Type': 'application/json'})
        backoff = 2.0
        for attempt in range(4):
            try:
                with request.urlopen(req, timeout=15) as resp:  # nosec B310
                    body = json.loads(resp.read().decode())
                if body.get('error'):
                    raise RuntimeError(f"json-rpc error: {body['error']}")
                return body.get('result')
            except _urllib_error.HTTPError as exc:
                if exc.code in (429, 500, 502, 503, 504) and attempt < 3:
                    time.sleep(backoff)
                    backoff = min(30.0, backoff * 2)
                    continue
                raise RuntimeError(f"rpc_http_error:{exc.code} method={method}")
        return None

    def _safe_to_process_block(self, block_number: int | None, head: int | None) -> bool:
        """True when block_number has enough confirmations relative to head."""
        if block_number is None or head is None:
            return True
        return block_number <= max(0, head - self.confirmations_required)

    def _is_rate_limited(self) -> bool:
        """Sliding-window check: drops event and returns True when limit exceeded."""
        now = time.monotonic()
        window_start = now - 60.0
        while self._event_timestamps and self._event_timestamps[0] < window_start:
            self._event_timestamps.popleft()
        if len(self._event_timestamps) >= self.max_events_per_minute:
            self.state['metrics']['rate_limited_dropped'] += 1
            return True
        self._event_timestamps.append(now)
        return False

    # ------------------------------------------------------------------
    # Target loading (workspace-scoped)
    # ------------------------------------------------------------------

    def _watched_targets(self) -> list[dict[str, Any]]:
        """Load all active monitoring targets scoped to Base chain.

        Each call opens and closes its own connection so no connection is
        held while waiting for the next WebSocket message.
        """
        with pg_connection() as conn:
            ensure_pilot_schema(conn)
            rows = conn.execute(
                '''
                SELECT id, workspace_id, name, target_type, chain_network,
                       wallet_address, contract_identifier,
                       monitoring_enabled, enabled, is_active,
                       updated_by_user_id, created_by_user_id, severity_threshold
                FROM targets
                WHERE deleted_at IS NULL
                  AND target_type IN ('wallet', 'contract')
                  AND monitoring_enabled = TRUE
                  AND enabled = TRUE
                  AND is_active = TRUE
                  AND COALESCE(chain_network, 'base') = %s
                ''',
                (self.chain_network,),
            ).fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Event building
    # ------------------------------------------------------------------

    def _build_event_from_log(self, target: dict[str, Any], log: dict[str, Any]) -> ActivityEvent:
        block_number = _hex_to_int(log.get('blockNumber')) or 0
        tx_hash = str(log.get('transactionHash') or '')
        log_index = _hex_to_int(log.get('logIndex'))
        topic0 = str((log.get('topics') or [''])[0]).lower()
        owner = _topic_to_address((log.get('topics') or [None, None])[1])
        spender_or_to = _topic_to_address((log.get('topics') or [None, None, None])[2])
        cursor = f"{block_number}:{tx_hash}:{-1 if log_index is None else log_index}"
        payload: dict[str, Any] = {
            'chain_id': self.chain_id,
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
            'ingestion_source': REALTIME_INGESTION_SOURCE,
            'evidence_source': 'live',
            'source_type': REALTIME_INGESTION_SOURCE,
            'observed_block_number': block_number,
            'confirmed_block_number': block_number,
        }
        return ActivityEvent(
            event_id=_make_event_id(str(target['id']), cursor, 'transaction'),
            kind='transaction',
            observed_at=datetime.now(timezone.utc),
            ingestion_source=REALTIME_INGESTION_SOURCE,
            cursor=cursor,
            payload=payload,
        )

    # ------------------------------------------------------------------
    # Persistence (short transaction per event)
    # ------------------------------------------------------------------

    def _persist_event(self, target: dict[str, Any], event: ActivityEvent) -> dict[str, Any]:
        """Persist one event; retries once with a fresh connection on failure.

        Never holds the connection open between events.
        Logs realtime_event_persist_failed on final failure but does not crash the worker.
        """
        try:
            with pg_connection() as conn:
                ensure_pilot_schema(conn)
                result = process_ingested_event(conn, target=target, event=event, ingestion_mode='live')
                conn.commit()
                return result
        except Exception as exc:
            logger.warning(
                'realtime_event_persist_failed watcher=%s attempt=1 event_id=%s error=%s',
                self.watcher_name, event.event_id, str(exc)[:200],
            )
            self.state['metrics']['persist_retried'] += 1
            try:
                with pg_connection() as conn2:
                    ensure_pilot_schema(conn2)
                    result = process_ingested_event(conn2, target=target, event=event, ingestion_mode='live')
                    conn2.commit()
                    return result
            except Exception as exc2:
                logger.error(
                    'realtime_event_persist_failed watcher=%s attempt=2 event_id=%s error=%s giving_up',
                    self.watcher_name, event.event_id, str(exc2)[:200],
                )
                self.state['metrics']['persist_failed'] += 1
                return {'status': 'persist_failed', 'event_id': event.event_id}

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------

    def _record_heartbeat(self) -> None:
        """Emit realtime_worker_heartbeat log and upsert monitoring_watcher_state."""
        lag: int | None = None
        if self.state.get('last_head_block') is not None and self.state.get('last_processed_block') is not None:
            lag = max(0, int(self.state['last_head_block']) - int(self.state['last_processed_block']))

        logger.info(
            'realtime_worker_heartbeat watcher_name=%s chain_id=%s chain=%s '
            'last_event_at=%s reconnect_count=%s events_processed=%s lag_blocks=%s degraded=%s',
            self.watcher_name,
            self.chain_id,
            self.chain_network,
            self.state.get('last_event_at') or 'none',
            self.state['metrics'].get('ws_reconnects', 0),
            self.state['metrics'].get('events_ingested', 0),
            lag,
            bool(self.state.get('degraded')),
        )

        try:
            with pg_connection() as conn:
                ensure_pilot_schema(conn)
                conn.execute(
                    '''
                    INSERT INTO monitoring_watcher_state (
                        watcher_name, running, status, source_status, ingestion_mode,
                        degraded, degraded_reason,
                        last_started_at, last_heartbeat_at, last_cycle_at,
                        last_processed_block, metrics, updated_at
                    )
                    VALUES (
                        %s, TRUE, 'running', %s, 'realtime',
                        %s, %s,
                        COALESCE(
                            (SELECT last_started_at FROM monitoring_watcher_state WHERE watcher_name = %s),
                            NOW()
                        ),
                        NOW(), NOW(),
                        %s, %s::jsonb, NOW()
                    )
                    ON CONFLICT (watcher_name) DO UPDATE SET
                        running = TRUE,
                        status = 'running',
                        source_status = EXCLUDED.source_status,
                        degraded = EXCLUDED.degraded,
                        degraded_reason = EXCLUDED.degraded_reason,
                        last_heartbeat_at = NOW(),
                        last_cycle_at = NOW(),
                        last_processed_block = EXCLUDED.last_processed_block,
                        metrics = EXCLUDED.metrics,
                        updated_at = NOW()
                    ''',
                    (
                        self.watcher_name,
                        self.state.get('source_status') or 'realtime_websocket',
                        bool(self.state.get('degraded')),
                        self.state.get('degraded_reason'),
                        self.watcher_name,
                        self.state.get('last_processed_block'),
                        json.dumps({**self.state['metrics'], 'lag_blocks': lag}),
                    ),
                )
                conn.commit()
        except Exception as exc:
            logger.warning('realtime_worker_heartbeat_persist_failed error=%s', str(exc)[:200])

        self.state['last_heartbeat_at'] = datetime.now(timezone.utc).isoformat()

    # ------------------------------------------------------------------
    # Backfill (gap-fill after reconnect / gap detected)
    # ------------------------------------------------------------------

    async def _backfill(self, from_block: int, to_block: int) -> int:
        if to_block < from_block:
            return 0
        targets = self._watched_targets()
        processed = 0
        for target in targets:
            watched = str(target.get('wallet_address') or target.get('contract_identifier') or '').lower()
            if not watched.startswith('0x'):
                continue
            for start in range(from_block, to_block + 1, self.backfill_chunk):
                end = min(to_block, start + self.backfill_chunk - 1)
                logs = self._rpc_call(
                    'eth_getLogs',
                    [{'fromBlock': hex(start), 'toBlock': hex(end), 'topics': [[TRANSFER_TOPIC, APPROVAL_TOPIC]]}],
                ) or []
                for log in logs:
                    topics = [str(t).lower() for t in (log.get('topics') or [])]
                    addr = str(log.get('address') or '').lower()
                    if watched not in topics and watched != addr:
                        continue
                    if self._is_rate_limited():
                        logger.warning(
                            'realtime_rate_limit_exceeded watcher=%s during_backfill',
                            self.watcher_name,
                        )
                        continue
                    event = self._build_event_from_log(target, log)
                    self._persist_event(target, event)
                    processed += 1
                    self.state['last_processed_block'] = max(
                        int(self.state.get('last_processed_block') or 0),
                        int(event.payload.get('block_number') or 0),
                    )
        if processed:
            self.state['metrics']['rpc_backfills'] += 1
            increment('decoda_realtime_backfills_total', chain=self.chain_network)
        return processed

    # ------------------------------------------------------------------
    # WebSocket subscription loop
    # ------------------------------------------------------------------

    async def _ws_subscribe(self) -> None:
        import websockets  # type: ignore[import]

        async with websockets.connect(self.ws_url, ping_interval=20, ping_timeout=20) as ws:
            await ws.send(json.dumps({
                'jsonrpc': '2.0', 'id': 1, 'method': 'eth_subscribe', 'params': ['newHeads'],
            }))
            await ws.send(json.dumps({
                'jsonrpc': '2.0', 'id': 2, 'method': 'eth_subscribe',
                'params': ['logs', {'topics': [[TRANSFER_TOPIC, APPROVAL_TOPIC]]}],
            }))
            logger.info(
                'realtime_ws_connected chain=%s chain_id=%s watcher=%s subscriptions=newHeads,logs',
                self.chain_network, self.chain_id, self.watcher_name,
            )
            self.state['source_status'] = 'realtime_websocket'
            sub_ids: dict[str, str] = {}

            while True:
                msg = json.loads(await ws.recv())

                # Subscription confirmation
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
                    # Track chain head to enforce confirmation safety
                    head = _hex_to_int(result.get('number'))
                    if head is not None:
                        self.state['last_head_block'] = head
                        last = self.state.get('last_processed_block')
                        if last is not None and head - int(last) > self.gap_threshold_blocks:
                            logger.warning(
                                'realtime_gap_detected chain=%s from_block=%s to_block=%s triggering_backfill',
                                self.chain_network, int(last) + 1, head,
                            )
                            await self._backfill(int(last) + 1, head)

                elif sub == sub_ids.get('logs'):
                    # Skip reorg-removed logs in the realtime path
                    if bool(result.get('removed')):
                        continue

                    block_number = _hex_to_int(result.get('blockNumber'))
                    if not self._safe_to_process_block(block_number, self.state.get('last_head_block')):
                        continue

                    if self._is_rate_limited():
                        logger.warning(
                            'realtime_rate_limit_exceeded watcher=%s chain=%s',
                            self.watcher_name, self.chain_network,
                        )
                        continue

                    for target in self._watched_targets():
                        watched = str(
                            target.get('wallet_address') or target.get('contract_identifier') or ''
                        ).lower()
                        topics = [str(t).lower() for t in (result.get('topics') or [])]
                        address = str(result.get('address') or '').lower()
                        if watched not in topics and watched != address:
                            continue

                        event = self._build_event_from_log(target, result)
                        persist_result = self._persist_event(target, event)

                        if persist_result.get('status') == 'duplicate_suppressed':
                            logger.debug(
                                'realtime_event_deduped watcher=%s event_id=%s',
                                self.watcher_name, event.event_id,
                            )
                            continue

                        if persist_result.get('status') != 'persist_failed':
                            self.state['metrics']['events_ingested'] += 1
                            self.state['last_event_at'] = datetime.now(timezone.utc).isoformat()
                            self.state['last_processed_block'] = max(
                                int(self.state.get('last_processed_block') or 0),
                                int(event.payload.get('block_number') or 0),
                            )
                            increment('decoda_realtime_events_total', chain=self.chain_network)

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def run_forever(self) -> None:
        """Run the realtime ingestor loop with exponential reconnect backoff."""
        retry = 1.0
        _last_error_key: str | None = None
        _last_error_logged_at = 0.0
        _ERROR_LOG_WINDOW = 60.0
        _next_heartbeat = time.monotonic()

        while True:
            # Periodic heartbeat regardless of whether WebSocket is up
            now = time.monotonic()
            if now >= _next_heartbeat:
                self._record_heartbeat()
                _next_heartbeat = time.monotonic() + self.heartbeat_seconds

            try:
                self.state['degraded'] = False
                self.state['degraded_reason'] = None
                head = _hex_to_int(self._rpc_call('eth_blockNumber', []))
                self.state['last_head_block'] = head
                if self.state.get('last_processed_block') is None and head is not None:
                    self.state['last_processed_block'] = max(0, int(head) - self.confirmations_required)
                self._record_heartbeat()
                _next_heartbeat = time.monotonic() + self.heartbeat_seconds

                await asyncio.wait_for(self._ws_subscribe(), timeout=float(self.heartbeat_seconds))
                retry = 1.0
                _last_error_key = None

            except asyncio.TimeoutError:
                # Normal: WebSocket was quiet for heartbeat_seconds; loop back
                self._record_heartbeat()
                _next_heartbeat = time.monotonic() + self.heartbeat_seconds
                continue

            except Exception as exc:
                self.state['metrics']['ws_reconnects'] += 1
                self.state['degraded'] = True
                error_key = f'{type(exc).__name__}:{str(exc)[:80]}'
                self.state['degraded_reason'] = str(exc)[:160]
                self.state['source_status'] = 'degraded'

                increment(
                    'decoda_realtime_provider_failures_total',
                    provider='base_realtime_websocket',
                    error_type=type(exc).__name__,
                )

                now_log = time.monotonic()
                if error_key != _last_error_key or now_log - _last_error_logged_at >= _ERROR_LOG_WINDOW:
                    logger.warning(
                        'realtime_ingestor_error chain=%s chain_id=%s '
                        'error_type=%s error=%s retry_in=%.1fs reconnect_count=%s',
                        self.chain_network, self.chain_id,
                        type(exc).__name__, str(exc)[:200], min(30.0, retry),
                        self.state['metrics']['ws_reconnects'],
                    )
                    _last_error_key = error_key
                    _last_error_logged_at = now_log

                # Attempt backfill for blocks we may have missed during disconnect
                try:
                    head = _hex_to_int(self._rpc_call('eth_blockNumber', [])) if self.rpc_url else None
                except Exception:
                    head = None
                last = int(
                    self.state.get('last_processed_block')
                    or max(0, (head or 0) - self.confirmations_required)
                )
                if head is not None and head >= last:
                    logger.warning(
                        'realtime_reconnect_backfill from_block=%s to_block=%s watcher=%s',
                        last, head, self.watcher_name,
                    )
                    await self._backfill(last, head)

                self._record_heartbeat()
                _next_heartbeat = time.monotonic() + self.heartbeat_seconds
                gauge('decoda_realtime_worker_healthy', 0, watcher=self.watcher_name)
                await asyncio.sleep(min(30.0, retry) + random.random())
                retry = min(30.0, retry * 2)
