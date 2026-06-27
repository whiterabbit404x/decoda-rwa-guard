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
from urllib.parse import urlparse as _urlparse

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

# All chain_network values that map to Base (chain_id=8453).
# Must stay in sync with CHAIN_MAP in evm_activity_provider.py.
_BASE_NETWORK_ALIASES: tuple[str, ...] = ('base', 'base-mainnet')

# Env vars read at ingestor construction time (not module load) so tests can monkeypatch.
_DEFAULT_CONFIRMATIONS = 1
_DEFAULT_MAX_EVENTS_PER_MINUTE = 1000
_DEFAULT_HEARTBEAT_SECONDS = 10
_DEFAULT_BACKFILL_CHUNK = 2000
_DEFAULT_GAP_THRESHOLD_BLOCKS = 24
_BLOCK_NUMBER_MIN_INTERVAL = 60.0  # min seconds between eth_blockNumber RPC calls
_DEFAULT_SUBSCRIPTIONS = 'newHeads,logs'


def _resolve_int_env(name: str, default: int) -> int:
    raw = (os.getenv(name) or '').strip()
    try:
        return max(0, int(raw)) if raw else default
    except (TypeError, ValueError):
        return default


def _resolve_subscriptions_mode(raw: str) -> str:
    """Normalise BASE_REALTIME_SUBSCRIPTIONS. Unknown values fall back to full mode."""
    mode = (raw or '').strip().lower().replace('-', '_').replace(' ', '')
    if mode in ('newheads_only',):
        return 'newHeads_only'
    return 'newHeads,logs'


def _ws_url_host(url: str) -> str:
    """Return hostname only — never the path, key, or credentials."""
    try:
        return _urlparse(url).hostname or 'unknown'
    except Exception:
        return 'unknown'


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
        subscriptions: str | None = None,
        ws_url_secondary: str | None = None,
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
        self.subscriptions = _resolve_subscriptions_mode(
            subscriptions if subscriptions is not None
            else (os.getenv('BASE_REALTIME_SUBSCRIPTIONS') or _DEFAULT_SUBSCRIPTIONS)
        )
        self.backfill_chunk = max(1, _resolve_int_env('EVM_BACKFILL_MAX_BLOCK_RANGE', _DEFAULT_BACKFILL_CHUNK))
        self.gap_threshold_blocks = max(
            self.confirmations_required + 1,
            _resolve_int_env('EVM_BACKFILL_GAP_THRESHOLD_BLOCKS', _DEFAULT_GAP_THRESHOLD_BLOCKS),
        )

        # Secondary WS URL for failover after repeated 1001 closes on primary.
        self.ws_url_secondary: str | None = ws_url_secondary or None
        # Active URL — may be swapped to secondary on failover.
        self._current_ws_url: str = ws_url
        # Messages received in the current WS session (reset at start of each _ws_subscribe call).
        self._session_messages_received: int = 0
        # Set True after 3 × 1001 closes before first event with no secondary; switches to HTTP fast-tail.
        self._wss_permanently_disabled: bool = False
        # Current ingestion mode: 'realtime' (WSS) or 'http_fast_tail'.
        self._ingestion_mode: str = 'realtime'

        # Tracks consecutive clean-close (code=1001) errors; used for downgrade and failover.
        self._consecutive_1001_closes: int = 0

        # WebSocket keepalive settings — configurable via env vars.
        _ping_iv = _resolve_int_env('BASE_WS_PING_INTERVAL', 30)
        self._ws_ping_interval: float | None = float(_ping_iv) if _ping_iv > 0 else None
        _ping_to = _resolve_int_env('BASE_WS_PING_TIMEOUT', 30)
        self._ws_ping_timeout: float | None = float(_ping_to) if _ping_to > 0 else None
        _open_to = _resolve_int_env('BASE_WS_OPEN_TIMEOUT', 30)
        self._ws_open_timeout: float | None = float(_open_to) if _open_to > 0 else None

        # Sliding-window rate limiter: stores monotonic timestamps of recent events.
        self._event_timestamps: deque[float] = deque()

        # Block-number cache: avoids calling eth_blockNumber more than once per
        # _BLOCK_NUMBER_MIN_INTERVAL seconds.  Updated both from newHeads subscription
        # messages and from direct RPC calls.
        self._block_number_cache: int | None = None
        self._block_number_fetched_at: float = 0.0
        self._last_head_block_at: float = 0.0  # monotonic time of last newHeads update

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
                'heads_received': 0,
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

    def _throttled_block_number(self) -> int | None:
        """Return the latest block number without spamming eth_blockNumber.

        Priority order (cheapest first):
        1. last_head_block already tracked via a recent newHeads message (zero RPC cost).
        2. Cached eth_blockNumber result if fetched within _BLOCK_NUMBER_MIN_INTERVAL seconds.
        3. Fresh eth_blockNumber RPC call (updates the cache); on failure returns stale cache.
        """
        now = time.monotonic()
        # Use newHeads-derived value if it arrived within the last 30 s
        if (
            self.state.get('last_head_block') is not None
            and now - self._last_head_block_at < 30.0
        ):
            return int(self.state['last_head_block'])

        # Use cached RPC result if still fresh
        if (
            self._block_number_cache is not None
            and now - self._block_number_fetched_at < _BLOCK_NUMBER_MIN_INTERVAL
        ):
            return self._block_number_cache

        # Fall back to a real RPC call, caching the result
        try:
            result = _hex_to_int(self._rpc_call('eth_blockNumber', []))
        except Exception:
            return self._block_number_cache  # stale cache beats None
        if result is not None:
            self._block_number_cache = result
            self._block_number_fetched_at = now
        return result

    def _compute_reconnect_sleep(self, exc: Exception, retry: float) -> float:
        """Return seconds to sleep before the next reconnect attempt.

        HTTP 429 (rate-limited) uses a much longer backoff (60-120 s) to let the
        provider recover.  ConnectionClosed errors (code 1001 / no close frame)
        start at a moderate floor of 5 s to reduce reconnect spam.  All other
        errors use the standard exponential backoff.
        """
        exc_str = str(exc)
        if '429' in exc_str:
            return 60.0 + random.random() * 60.0
        # WebSocket close (clean 1001 or no-frame drop): exponential from 5s floor up to 120s.
        # Jitter is ±25% to spread reconnect storms without overly padding each sleep.
        if 'ConnectionClosed' in type(exc).__name__ or '1001' in exc_str:
            effective = max(5.0, min(120.0, retry))
            return effective + random.random() * max(1.0, effective * 0.25)
        return min(120.0, retry) + random.random() * max(1.0, min(10.0, retry * 0.5))

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
                  AND (
                    LOWER(COALESCE(chain_network, 'base')) IN ('base', 'base-mainnet')
                    OR chain_id = 8453
                  )
                ''',
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

        _events_processed = (
            self.state['metrics'].get('events_ingested', 0)
            + self.state['metrics'].get('heads_received', 0)
        )
        _active_host = _ws_url_host(self._current_ws_url)
        logger.info(
            'realtime_worker_heartbeat watcher_name=%s chain_id=%s chain=%s '
            'last_event_at=%s reconnect_count=%s events_processed=%s '
            'heads_received=%s lag_blocks=%s degraded=%s active_provider_host=%s',
            self.watcher_name,
            self.chain_id,
            self.chain_network,
            self.state.get('last_event_at') or 'none',
            self.state['metrics'].get('ws_reconnects', 0),
            _events_processed,
            self.state['metrics'].get('heads_received', 0),
            lag,
            bool(self.state.get('degraded')),
            _active_host,
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
                        %s, TRUE, 'running', %s, %s,
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
                        ingestion_mode = EXCLUDED.ingestion_mode,
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
                        self._ingestion_mode,
                        bool(self.state.get('degraded')),
                        self.state.get('degraded_reason'),
                        self.watcher_name,
                        self.state.get('last_processed_block'),
                        json.dumps({**self.state['metrics'], 'lag_blocks': lag, 'active_provider_host': _active_host}),
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

        self._session_messages_received = 0  # reset per connection
        _include_logs = (self.subscriptions == 'newHeads,logs')

        async with websockets.connect(
            self._current_ws_url,
            ping_interval=self._ws_ping_interval,
            ping_timeout=self._ws_ping_timeout,
            open_timeout=self._ws_open_timeout,
        ) as ws:
            logger.info(
                'realtime_subscription_request_sent subscription_type=newHeads watcher=%s',
                self.watcher_name,
            )
            await ws.send(json.dumps({
                'jsonrpc': '2.0', 'id': 1, 'method': 'eth_subscribe', 'params': ['newHeads'],
            }))
            if _include_logs:
                logger.info(
                    'realtime_subscription_request_sent subscription_type=logs watcher=%s',
                    self.watcher_name,
                )
                await ws.send(json.dumps({
                    'jsonrpc': '2.0', 'id': 2, 'method': 'eth_subscribe',
                    'params': ['logs', {'topics': [[TRANSFER_TOPIC, APPROVAL_TOPIC]]}],
                }))
            logger.info(
                'realtime_ws_connected chain=%s chain_id=%s watcher=%s subscriptions=%s',
                self.chain_network, self.chain_id, self.watcher_name, self.subscriptions,
            )
            self.state['source_status'] = 'realtime_websocket'
            self.state['degraded'] = False
            self.state['degraded_reason'] = None
            sub_ids: dict[str, str] = {}

            # Log target count at connection time; events are still matched per-message.
            try:
                _startup_targets = self._watched_targets()
                _target_count = len(_startup_targets)
                _workspace_count = len({str(t.get('workspace_id')) for t in _startup_targets})
                _target_ids = [str(t.get('id', '')) for t in _startup_targets]
                logger.info(
                    'realtime_targets_loaded count=%s chain_id=%s chain_network=%s '
                    'workspace_count=%s watcher=%s target_ids=%s',
                    _target_count, self.chain_id, self.chain_network,
                    _workspace_count, self.watcher_name,
                    ','.join(_target_ids[:20]),  # IDs only — no addresses or secrets
                )
                if _target_count == 0:
                    logger.warning(
                        'realtime_no_targets_loaded chain_id=%s chain_network=%s watcher=%s '
                        'worker_healthy_but_no_events_will_be_processed',
                        self.chain_id, self.chain_network, self.watcher_name,
                    )
            except Exception as _load_exc:
                logger.warning(
                    'realtime_targets_load_failed watcher=%s error=%s',
                    self.watcher_name, str(_load_exc)[:200],
                )

            while True:
                msg = json.loads(await ws.recv())
                self._session_messages_received += 1

                # Subscription confirmation — log presence of ID, never the ID value itself.
                if msg.get('id') == 1 and msg.get('result'):
                    sub_ids['newHeads'] = str(msg['result'])
                    logger.info(
                        'realtime_subscription_created subscription_type=newHeads '
                        'subscription_id_present=%s watcher=%s',
                        bool(msg['result']), self.watcher_name,
                    )
                    continue
                if msg.get('id') == 2 and msg.get('result'):
                    sub_ids['logs'] = str(msg['result'])
                    logger.info(
                        'realtime_subscription_created subscription_type=logs '
                        'subscription_id_present=%s watcher=%s',
                        bool(msg['result']), self.watcher_name,
                    )
                    continue

                params = msg.get('params') or {}
                result = params.get('result') or {}
                sub = params.get('subscription')

                if sub == sub_ids.get('newHeads'):
                    # Track chain head to enforce confirmation safety and count block activity.
                    head = _hex_to_int(result.get('number'))
                    if head is not None:
                        self.state['last_head_block'] = head
                        self._last_head_block_at = time.monotonic()
                        self.state['metrics']['heads_received'] = (
                            self.state['metrics'].get('heads_received', 0) + 1
                        )
                        self.state['last_event_at'] = datetime.now(timezone.utc).isoformat()
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
    # HTTP fast-tail fallback (QuickNode WSS permanently disabled)
    # ------------------------------------------------------------------

    async def _run_http_fast_tail(self) -> None:
        """HTTP polling fallback when WSS is permanently disabled for the provider.

        Polls QuickNode HTTP RPC every 30-60 s, scans eth_getLogs for active Base
        watched wallet targets.  Uses the same process_ingested_event path so
        deduplication with the stable 300-s polling worker is automatic.

        Cursor is only advanced when all target scans succeed — failed scans retry
        the same block range on the next poll so no events are missed.
        """
        self._ingestion_mode = 'http_fast_tail'
        self.state['source_status'] = 'quicknode_http_fast_tail'
        self.state['degraded'] = True
        self.state['degraded_reason'] = 'provider_closes_before_first_event'

        _poll_interval = float(
            max(30, min(60, _resolve_int_env('REALTIME_HTTP_FAST_TAIL_INTERVAL_SECONDS', 45)))
        )

        logger.warning(
            'realtime_http_fast_tail_started watcher=%s rpc_host=%s poll_interval=%.0fs',
            self.watcher_name,
            _ws_url_host(self.rpc_url),
            _poll_interval,
        )

        _next_heartbeat = time.monotonic()

        while True:
            now = time.monotonic()
            if now >= _next_heartbeat:
                self._record_heartbeat()
                _next_heartbeat = now + self.heartbeat_seconds

            try:
                head_raw = self._rpc_call('eth_blockNumber', [])
                head_num = _hex_to_int(head_raw)
                if head_num is None:
                    logger.warning(
                        'http_fast_tail_no_block_number watcher=%s', self.watcher_name,
                    )
                    await asyncio.sleep(_poll_interval)
                    continue

                self.state['last_head_block'] = head_num
                self._last_head_block_at = time.monotonic()

                last = int(
                    self.state.get('last_processed_block')
                    or max(0, head_num - self.confirmations_required - 1)
                )
                if last >= head_num:
                    await asyncio.sleep(_poll_interval)
                    continue

                from_block = last + 1
                to_block = head_num

                logger.info(
                    'realtime_http_fast_tail_scan watcher=%s from_block=%s to_block=%s',
                    self.watcher_name, from_block, to_block,
                )

                targets = self._watched_targets()
                scan_all_ok = True

                for target in targets:
                    watched = str(
                        target.get('wallet_address') or target.get('contract_identifier') or ''
                    ).lower()
                    if not watched.startswith('0x'):
                        continue

                    try:
                        logs = self._rpc_call(
                            'eth_getLogs',
                            [{
                                'fromBlock': hex(from_block),
                                'toBlock': hex(to_block),
                                'topics': [[TRANSFER_TOPIC, APPROVAL_TOPIC]],
                            }],
                        ) or []

                        for log in logs:
                            if bool(log.get('removed')):
                                continue
                            topics = [str(t).lower() for t in (log.get('topics') or [])]
                            addr = str(log.get('address') or '').lower()
                            if watched not in topics and watched != addr:
                                continue

                            if self._is_rate_limited():
                                logger.warning(
                                    'http_fast_tail_rate_limited watcher=%s', self.watcher_name,
                                )
                                continue

                            event = self._build_event_from_log(target, log)
                            result = self._persist_event(target, event)

                            if result.get('status') == 'duplicate_suppressed':
                                logger.debug(
                                    'http_fast_tail_deduped watcher=%s event_id=%s',
                                    self.watcher_name, event.event_id,
                                )
                            elif result.get('status') != 'persist_failed':
                                self.state['metrics']['events_ingested'] += 1
                                self.state['last_event_at'] = datetime.now(timezone.utc).isoformat()
                                increment('decoda_realtime_events_total', chain=self.chain_network)

                    except Exception as scan_exc:
                        logger.warning(
                            'http_fast_tail_scan_failed watcher=%s target=%s error=%s',
                            self.watcher_name, target.get('id'), str(scan_exc)[:200],
                        )
                        scan_all_ok = False

                if scan_all_ok:
                    self.state['last_processed_block'] = to_block
                    self.state['metrics']['heads_received'] = (
                        self.state['metrics'].get('heads_received', 0) + 1
                    )

            except Exception as exc:
                logger.warning(
                    'http_fast_tail_error watcher=%s error=%s',
                    self.watcher_name, str(exc)[:200],
                )

            self._record_heartbeat()
            _next_heartbeat = time.monotonic() + self.heartbeat_seconds
            await asyncio.sleep(_poll_interval)

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
        _was_degraded = True  # start as degraded; cleared after first stable heartbeat

        # Optional: detect clean WebSocket closes (code 1001 "going away") separately
        try:
            from websockets.exceptions import ConnectionClosedOK as _ConnectionClosedOK  # type: ignore[import]
        except Exception:
            _ConnectionClosedOK = None  # type: ignore[assignment,misc]

        while not self._wss_permanently_disabled:
            # Periodic heartbeat regardless of whether WebSocket is up
            now = time.monotonic()
            if now >= _next_heartbeat:
                self._record_heartbeat()
                _next_heartbeat = time.monotonic() + self.heartbeat_seconds

            try:
                # Throttled block number: prefers newHeads, falls back to cached/RPC.
                # Avoids calling eth_blockNumber on every 10-second reconnect cycle.
                head = self._throttled_block_number()
                if head is not None:
                    self.state['last_head_block'] = head
                    if self.state.get('last_processed_block') is None:
                        self.state['last_processed_block'] = max(0, int(head) - self.confirmations_required)

                self._record_heartbeat()
                _next_heartbeat = time.monotonic() + self.heartbeat_seconds

                await asyncio.wait_for(self._ws_subscribe(), timeout=float(self.heartbeat_seconds))
                retry = 1.0
                _last_error_key = None

            except asyncio.TimeoutError:
                # Normal: heartbeat interval elapsed; WebSocket is alive
                if _was_degraded and not self.state.get('degraded'):
                    logger.info(
                        'realtime_recovered chain=%s watcher=%s events_processed=%s',
                        self.chain_network, self.watcher_name,
                        self.state['metrics'].get('events_ingested', 0),
                    )
                    _was_degraded = False
                self._record_heartbeat()
                _next_heartbeat = time.monotonic() + self.heartbeat_seconds
                continue

            except Exception as exc:
                self.state['metrics']['ws_reconnects'] += 1
                self.state['degraded'] = True
                _was_degraded = True
                exc_str = str(exc)
                error_key = f'{type(exc).__name__}:{exc_str[:80]}'
                self.state['degraded_reason'] = exc_str[:160]
                self.state['source_status'] = 'degraded'

                is_rate_limit = '429' in exc_str
                is_clean_close = (
                    _ConnectionClosedOK is not None
                    and isinstance(exc, _ConnectionClosedOK)
                )
                # Broader 1001 check: catches ConnectionClosedOK instances AND
                # exceptions that carry '1001' in their string representation.
                is_1001_close = (
                    is_clean_close
                    or '1001' in exc_str
                    or 'ConnectionClosedOK' in type(exc).__name__
                )

                # Auto-downgrade from newHeads,logs → newHeads_only after 3 consecutive
                # 1001 closes. After downgrade, fail over to secondary URL if configured.
                if is_1001_close and self.subscriptions == 'newHeads,logs':
                    self._consecutive_1001_closes += 1
                    if self._consecutive_1001_closes >= 3:
                        self.subscriptions = 'newHeads_only'
                        self._consecutive_1001_closes = 0
                        logger.warning(
                            'realtime_subscription_downgraded '
                            'reason=provider_closed_logs_subscription '
                            'chain=%s watcher=%s new_subscriptions=%s',
                            self.chain_network, self.watcher_name, self.subscriptions,
                        )
                elif is_1001_close and self.subscriptions == 'newHeads_only':
                    # Only count closes that fired before any message was received.
                    # If the provider did respond (subscription confirmation arrived),
                    # give it a clean slate rather than penalising transient instability.
                    if self._session_messages_received == 0:
                        self._consecutive_1001_closes += 1
                    else:
                        self._consecutive_1001_closes = 0
                    if self._consecutive_1001_closes >= 3:
                        if self.ws_url_secondary:
                            _failover_old_host = _ws_url_host(self._current_ws_url)
                            self._current_ws_url = (
                                self.ws_url_secondary
                                if self._current_ws_url != self.ws_url_secondary
                                else self.ws_url
                            )
                            self._consecutive_1001_closes = 0
                            logger.warning(
                                'realtime_provider_failover '
                                'old_host=%s new_host=%s watcher=%s',
                                _failover_old_host,
                                _ws_url_host(self._current_ws_url),
                                self.watcher_name,
                            )
                        else:
                            self._wss_permanently_disabled = True
                            self._ingestion_mode = 'http_fast_tail'
                            self.state['source_status'] = 'quicknode_http_fast_tail'
                            self.state['degraded'] = True
                            self.state['degraded_reason'] = 'provider_closes_before_first_event'
                            logger.warning(
                                'realtime_ws_disabled_for_provider '
                                'reason=provider_closes_before_first_event '
                                'watcher=%s consecutive_1001=%s',
                                self.watcher_name,
                                self._consecutive_1001_closes,
                            )
                elif not is_1001_close:
                    self._consecutive_1001_closes = 0

                increment(
                    'decoda_realtime_provider_failures_total',
                    provider='base_realtime_websocket',
                    error_type=type(exc).__name__,
                )

                now_log = time.monotonic()
                if error_key != _last_error_key or now_log - _last_error_logged_at >= _ERROR_LOG_WINDOW:
                    if is_clean_close or is_1001_close:
                        _before_first_flag = (
                            ' provider_closed_before_first_event=True'
                            if self._session_messages_received == 0 else ''
                        )
                        logger.info(
                            'realtime_ws_closed_cleanly chain=%s watcher=%s '
                            'code=1001 reconnecting reconnect_count=%s '
                            'consecutive_1001=%s subscriptions=%s%s',
                            self.chain_network, self.watcher_name,
                            self.state['metrics']['ws_reconnects'],
                            self._consecutive_1001_closes, self.subscriptions,
                            _before_first_flag,
                        )
                    elif is_rate_limit:
                        logger.warning(
                            'realtime_rpc_rate_limited chain=%s watcher=%s '
                            'backing_off_min=60s reconnect_count=%s',
                            self.chain_network, self.watcher_name,
                            self.state['metrics']['ws_reconnects'],
                        )
                    else:
                        logger.warning(
                            'realtime_ingestor_error chain=%s chain_id=%s '
                            'error_type=%s error=%s retry_in=%.1fs reconnect_count=%s',
                            self.chain_network, self.chain_id,
                            type(exc).__name__, exc_str[:200], min(120.0, retry),
                            self.state['metrics']['ws_reconnects'],
                        )
                    _last_error_key = error_key
                    _last_error_logged_at = now_log

                # Backfill blocks missed during disconnect.
                # Skip on rate-limit: eth_getLogs would also get 429.
                if not is_rate_limit:
                    head = self._throttled_block_number()
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

                if self._wss_permanently_disabled:
                    break

                sleep_for = self._compute_reconnect_sleep(exc, retry)
                await asyncio.sleep(sleep_for)
                if not is_rate_limit:
                    retry = min(120.0, retry * 2)

        if self._wss_permanently_disabled:
            await self._run_http_fast_tail()
