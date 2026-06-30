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
    _build_base_payload,
    _extract_selector,
    _hex_to_int,
    _iso_from_block_ts,
    _make_event_id,
    _topic_to_address,
    native_transfer_direction,
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

# Bounded realtime gap backfill: at most this many blocks are scanned per cycle
# so a large gap is closed gradually over successive heads instead of in one
# giant scan that burns provider rate limits. Configurable via
# BASE_REALTIME_BACKFILL_CHUNK_SIZE, but hard-capped at _MAX_BACKFILL_CHUNK_SIZE.
_DEFAULT_BACKFILL_CHUNK_SIZE = 25
# Hard upper bound on the per-cycle backfill chunk. A chunk wider than this is
# rejected so a single eth_getLogs scan can never blow the provider rate limit.
_MAX_BACKFILL_CHUNK_SIZE = 25
# A persisted checkpoint more than this many blocks behind the chain head is
# treated as stale ("no reliable checkpoint") for start-at-latest bootstrap.
_DEFAULT_CHECKPOINT_RELIABLE_MAX_LAG = 50_000

# Provider-wide WSS reconnect-loop circuit breaker. After this many code=1001
# closes WITHOUT last_event_at advancing, the WSS is permanently disabled and the
# worker switches to HTTP fast-tail. This is independent of consecutive_1001 (which
# resets to 0 once any head was ever received) so it still fires after a provider
# delivers thousands of heads and then wedges.
_RECONNECT_LOOP_CLOSE_THRESHOLD = 3
# last_event_at older than this (seconds) while 1001 closes keep happening trips the
# fallback even before the close-count threshold is reached (stale-event detection).
_DEFAULT_STALE_EVENT_THRESHOLD_SECONDS = 120


def _resolve_int_env(name: str, default: int) -> int:
    raw = (os.getenv(name) or '').strip()
    try:
        return max(0, int(raw)) if raw else default
    except (TypeError, ValueError):
        return default


def _resolve_bool_env(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or '').strip().lower()
    if raw in ('1', 'true', 'yes'):
        return True
    if raw in ('0', 'false', 'no'):
        return False
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


def _short_addr(addr: Any) -> str:
    """Truncate an address for noisy candidate logs (e.g. 0x5f6f…1d1f).

    Full addresses are emitted separately by ``realtime_target_diagnostics`` so an
    operator always has the exact monitored address; per-transaction candidate logs
    stay readable with the short form.
    """
    s = str(addr or '')
    if len(s) <= 12 or not s.startswith('0x'):
        return s or 'none'
    return f'{s[:6]}…{s[-4:]}'


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
        # Bounded gap backfill: scan at most this many blocks per cycle so a large
        # gap never triggers a single full-range scan on every head. Clamped to
        # [1, _MAX_BACKFILL_CHUNK_SIZE] so an over-large env value cannot widen the
        # per-scan range past the safe maximum.
        self.backfill_chunk_size = min(
            _MAX_BACKFILL_CHUNK_SIZE,
            max(1, _resolve_int_env('BASE_REALTIME_BACKFILL_CHUNK_SIZE', _DEFAULT_BACKFILL_CHUNK_SIZE)),
        )
        # Safe bootstrap: when no reliable checkpoint exists, start at the latest
        # block minus confirmations instead of replaying a huge historical gap.
        self.start_at_latest = _resolve_bool_env('BASE_REALTIME_START_AT_LATEST', default=False)
        # Monotonic deadline; while time.monotonic() < this, gap backfill is paused
        # (set after a provider rate-limit so we do not re-trigger every block).
        self._backfill_paused_until: float = 0.0
        # Guards the one-time checkpoint bootstrap (DB load) on cold start.
        self._checkpoint_bootstrapped: bool = False

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
        # WARNING: this counter RESETS to 0 the moment any head has ever been received
        # (see _closed_before_first_event). A provider that delivers thousands of heads
        # and then wedges keeps resetting it, so it must NOT be the only breaker signal.
        self._consecutive_1001_closes: int = 0

        # Provider-wide reconnect-loop breaker (does NOT reset on "first event seen").
        # _total_provider_close_count   : every 1001 close, for observability/logging.
        # _total_close_count_since_last_head : 1001 closes since last_event_at last
        #   advanced. This is the canonical fix for the production loop where the WSS
        #   delivered 6038 heads, then closed 1001 forever while consecutive_1001 stayed
        #   0 — events_processed/last_event_at frozen but reconnect_count climbing.
        # _last_event_at_snapshot       : last_event_at value when the counter last reset.
        self._total_provider_close_count: int = 0
        self._total_close_count_since_last_head: int = 0
        self._last_event_at_snapshot: str | None = None
        # last_event_at older than this (seconds) while 1001 closes keep happening
        # trips the fallback even before the close count threshold (requirement 5).
        self._stale_event_threshold_seconds = max(
            30, _resolve_int_env('REALTIME_STALE_EVENT_FALLBACK_SECONDS', _DEFAULT_STALE_EVENT_THRESHOLD_SECONDS)
        )

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
                'backfill_chunks': 0,
                'backfill_rate_limited': 0,
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

    def _closed_before_first_event(self) -> bool:
        """True when no real chain data (a head or an event) has arrived yet.

        A subscription confirmation (``eth_subscribe`` ACK) is NOT a chain event:
        it increments ``_session_messages_received`` but never ``heads_received``
        or ``events_ingested``.  A provider that ACKs the subscription and then
        closes (code=1001) before delivering the first head is still
        "before first event", so the close must count toward HTTP fast-tail
        fallback.  Once any head or event has arrived the provider is proven to
        work and this returns False (resetting the fallback counter), so a
        healthy WSS session is never downgraded.

        Equivalent to ``heads_received == 0`` because ``events_processed`` in the
        heartbeat is ``events_ingested + heads_received``; "heads_received=0 or
        events_processed=0" reduces to no real data at all.
        """
        metrics = self.state['metrics']
        return (
            metrics.get('heads_received', 0) == 0
            and metrics.get('events_ingested', 0) == 0
        )

    def _seconds_since_last_event(self) -> float | None:
        """Seconds since last_event_at, or None when it has never been set.

        last_event_at is set on every newHeads message and every persisted event,
        so it is the canonical "is the provider still delivering data" timestamp.
        """
        raw = self.state.get('last_event_at')
        if not raw:
            return None
        try:
            ts = datetime.fromisoformat(str(raw))
        except (TypeError, ValueError):
            return None
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds()

    def _last_event_is_stale(self) -> bool:
        """True when last_event_at exists but is older than the stale threshold.

        Used together with ongoing code=1001 closes to trip the HTTP fast-tail
        fallback even before the close-count threshold (requirement 5): a provider
        that stops advancing last_event_at for 2+ minutes while the WSS keeps
        closing is wedged, not healthy.
        """
        secs = self._seconds_since_last_event()
        return secs is not None and secs >= self._stale_event_threshold_seconds

    def _note_1001_close_for_breaker(self) -> bool:
        """Account one code=1001 close against the provider-wide reconnect breaker.

        Returns True when the breaker should trip (WSS permanently disabled, switch
        to HTTP fast-tail). Unlike consecutive_1001 this never resets just because a
        head was once received — it only resets when last_event_at actually advances,
        so a provider that delivered thousands of heads then wedged still trips it.

        The before-first-event case (no head ever received) is handled by the
        existing consecutive_1001 / secondary-failover path, so this only counts once
        the provider has proven it can deliver heads.
        """
        self._total_provider_close_count += 1
        if self._closed_before_first_event():
            return False
        current_event_at = self.state.get('last_event_at')
        if current_event_at != self._last_event_at_snapshot:
            # last_event_at advanced since the last close → provider still making
            # progress; reset the loop counter and re-anchor.
            self._last_event_at_snapshot = current_event_at
            self._total_close_count_since_last_head = 0
        else:
            # No new head/event since the last close → wedged. Count this close.
            self._total_close_count_since_last_head += 1
        if self._total_close_count_since_last_head >= _RECONNECT_LOOP_CLOSE_THRESHOLD:
            return True
        # Stale-event fast path: a 2-minute-stale last_event_at with repeated closes
        # is enough on its own (requirement 5) once at least one no-progress close
        # has been observed.
        return self._last_event_is_stale() and self._total_close_count_since_last_head >= 1

    def _trip_reconnect_loop_breaker(self) -> None:
        """Permanently disable WSS and arm the HTTP fast-tail fallback.

        Emits the canonical realtime_ws_disabled_for_provider marker with
        reason=provider_1001_reconnect_loop and the close_count so an operator can
        see exactly why the WSS was given up on.
        """
        self._wss_permanently_disabled = True
        self._ingestion_mode = 'http_fast_tail'
        self.state['source_status'] = 'quicknode_http_fast_tail'
        self.state['degraded'] = True
        self.state['degraded_reason'] = 'provider_1001_reconnect_loop'
        _age = self._seconds_since_last_event()
        logger.warning(
            'realtime_ws_disabled_for_provider reason=provider_1001_reconnect_loop '
            'close_count=%s total_provider_close_count=%s last_event_age_seconds=%s '
            'reconnect_count=%s watcher=%s',
            self._total_close_count_since_last_head,
            self._total_provider_close_count,
            round(_age, 1) if _age is not None else 'none',
            self.state['metrics'].get('ws_reconnects', 0),
            self.watcher_name,
        )

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

    def _build_event_from_log(
        self,
        target: dict[str, Any],
        log: dict[str, Any],
        *,
        source_type: str = REALTIME_INGESTION_SOURCE,
    ) -> ActivityEvent:
        block_number = _hex_to_int(log.get('blockNumber')) or 0
        tx_hash = str(log.get('transactionHash') or '')
        log_index = _hex_to_int(log.get('logIndex'))
        topic0 = str((log.get('topics') or [''])[0]).lower()
        owner = _topic_to_address((log.get('topics') or [None, None])[1])
        spender_or_to = _topic_to_address((log.get('topics') or [None, None, None])[2])
        cursor = f"{block_number}:{tx_hash}:{-1 if log_index is None else log_index}"
        _provider_mode = self.state.get('source_status') or self._ingestion_mode or REALTIME_INGESTION_SOURCE
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
            'ingestion_source': source_type,
            'evidence_source': 'live',
            'source_type': source_type,
            'detected_by': source_type,
            'provider_mode': _provider_mode,
            'observed_block_number': block_number,
            'confirmed_block_number': block_number,
        }
        return ActivityEvent(
            event_id=_make_event_id(str(target['id']), cursor, 'transaction'),
            kind='transaction',
            observed_at=datetime.now(timezone.utc),
            ingestion_source=source_type,
            cursor=cursor,
            payload=payload,
        )

    def _build_native_transfer_event(
        self,
        target: dict[str, Any],
        tx: dict[str, Any],
        *,
        block_number: int,
        block_hash: str | None,
        observed_at: datetime,
        direction: str,
        source_type: str = 'realtime_backfill',
    ) -> ActivityEvent:
        """Build a wallet-transfer ActivityEvent from a native ETH transaction.

        Uses the same cursor shape (``block:tx_hash:-1``) and ``_build_base_payload``
        as the 300 s polling worker so the two paths produce an identical
        idempotency key for the same tx — ON CONFLICT then dedupes them.
        """
        tx_hash = str(tx.get('hash') or '')
        cursor = f"{block_number}:{tx_hash}:-1"
        _provider_mode = self.state.get('source_status') or self._ingestion_mode or source_type
        payload = _build_base_payload(
            target=target,
            network=self.chain_network,
            chain_id=self.chain_id,
            block_number=block_number,
            block_hash=block_hash,
            tx=tx,
            tx_hash=tx_hash,
            raw_reference=f'{self.chain_network}:{tx_hash}',
        )
        payload['observed_at'] = observed_at.isoformat()
        payload['event_type'] = 'transaction'
        payload['wallet_transfer_direction'] = direction
        payload['ingestion_source'] = source_type
        payload['evidence_source'] = 'live'
        payload['source_type'] = source_type
        payload['detected_by'] = source_type
        payload['provider_mode'] = _provider_mode
        payload['observed_latency_seconds'] = round(
            (datetime.now(timezone.utc) - observed_at).total_seconds(), 2
        )
        return ActivityEvent(
            event_id=_make_event_id(str(target['id']), cursor, 'transaction'),
            kind='transaction',
            observed_at=observed_at,
            ingestion_source=source_type,
            cursor=cursor,
            payload=payload,
        )

    def _scan_native_transfers(
        self,
        from_block: int,
        to_block: int,
        watched: list[tuple[dict[str, Any], str]],
        *,
        source_type: str = 'realtime_backfill',
    ) -> int:
        """Detect native ETH transfers to/from watched wallets in a block range.

        Native ETH transfers carry NO logs, so ``eth_getLogs`` can never see them.
        The only way to detect them is to fetch each block's full transaction list
        and match ``tx.from`` / ``tx.to`` against the watched wallet via the shared
        :func:`native_transfer_direction` matcher (same one the polling worker uses).

        Returns the number of matched transfers persisted. Raises on RPC failure so
        the caller's rate-limit / pause handling applies (the checkpoint is then not
        advanced and the range is re-scanned next cycle).
        """
        if to_block < from_block or not watched:
            return 0
        logger.info(
            'realtime_native_transfer_scan_started chain_id=%s from_block=%s to_block=%s '
            'watched_targets=%s detected_by=%s watcher=%s',
            self.chain_id, from_block, to_block, len(watched), source_type, self.watcher_name,
        )
        processed = 0
        for block_number in range(int(from_block), int(to_block) + 1):
            block = self._rpc_call('eth_getBlockByNumber', [hex(block_number), True]) or {}
            block_hash = str(block.get('hash') or '') or None
            observed_at = _iso_from_block_ts(block.get('timestamp'))
            for tx in (block.get('transactions') or []):
                if not isinstance(tx, dict):
                    continue
                tx_hash = str(tx.get('hash') or '')
                matched_any = False
                for target, addr in watched:
                    direction = native_transfer_direction(addr, tx)
                    if direction is None:
                        continue
                    matched_any = True
                    value_wei = _hex_to_int(tx.get('value')) or 0
                    logger.info(
                        'realtime_native_transfer_candidate tx_hash=%s from=%s to=%s value_wei=%s '
                        'detected_by=%s',
                        tx_hash, _short_addr(tx.get('from')), _short_addr(tx.get('to')), value_wei,
                        source_type,
                    )
                    logger.info(
                        'realtime_native_transfer_match target_id=%s direction=%s tx_hash=%s '
                        'detected_by=%s',
                        target.get('id'), direction, tx_hash, source_type,
                    )
                    if self._is_rate_limited():
                        logger.warning(
                            'realtime_rate_limit_exceeded watcher=%s during_native_scan',
                            self.watcher_name,
                        )
                        continue
                    event = self._build_native_transfer_event(
                        target, tx,
                        block_number=block_number, block_hash=block_hash,
                        observed_at=observed_at, direction=direction, source_type=source_type,
                    )
                    result = self._persist_event(target, event)
                    if result.get('status') == 'duplicate_suppressed':
                        logger.debug(
                            'realtime_event_deduped watcher=%s event_id=%s',
                            self.watcher_name, event.event_id,
                        )
                        continue
                    if result.get('status') != 'persist_failed':
                        processed += 1
                        self.state['metrics']['events_ingested'] += 1
                        self.state['last_event_at'] = datetime.now(timezone.utc).isoformat()
                        logger.info(
                            'wallet_transfer_detected tx_hash=%s detected_by=%s',
                            tx_hash, source_type,
                        )
                        logger.info('realtime_event_persisted tx_hash=%s', tx_hash)
                        increment('decoda_realtime_events_total', chain=self.chain_network)
                if not matched_any and tx_hash:
                    logger.debug(
                        'native_transfer_no_match tx_hash=%s reason=address_not_watched',
                        tx_hash,
                    )
        return processed

    def _watched_wallet_pairs(self) -> list[tuple[dict[str, Any], str]]:
        """Load active Base targets as ``(target, lowercase_0x_address)`` pairs.

        Shared by the gap backfill and the new-head native scan so both match the
        watched wallet against the same normalised (lowercase) address.
        """
        pairs: list[tuple[dict[str, Any], str]] = []
        for target in self._watched_targets():
            addr = str(
                target.get('wallet_address') or target.get('contract_identifier') or ''
            ).lower()
            if addr.startswith('0x'):
                pairs.append((target, addr))
        return pairs

    async def _scan_head_native_transfers(self, head: int) -> int:
        """Scan newly confirmed head block(s) directly for native ETH transfers.

        Runs on every ``newHeads`` message in steady state (when the worker is
        keeping up, i.e. the lag is within ``gap_threshold_blocks``). Native ETH
        transfers carry NO logs, so the ``logs`` subscription can never see them —
        only a full-transaction scan of the block can. Without this, a plain ETH
        send to/from a watched wallet was invisible to the realtime worker (the most
        recent blocks are never wide enough to trip the gap backfill), so it only
        ever surfaced minutes later via the 300 s stable polling worker.

        Detections here are tagged ``detected_by=realtime_websocket`` (requirement 7:
        new-head scan), distinct from the gap backfill's ``realtime_backfill``.

        Confirmation-safe: only blocks at or below ``head - confirmations_required``
        are scanned. The checkpoint advances to the last scanned block so each block
        is scanned once; on RPC failure the checkpoint is NOT advanced so the range
        is retried on the next head (no transfers skipped).
        """
        safe_to = int(head) - self.confirmations_required
        if safe_to < 0:
            return 0
        last = self.state.get('last_processed_block')
        from_block = (int(last) + 1) if last is not None else safe_to
        if from_block > safe_to:
            # Nothing newly confirmed since the last scan.
            return 0
        # Defensive bound: a provider that coalesces several heads into one message
        # must not trigger an unbounded block-by-block fetch on the event loop.
        from_block = max(from_block, safe_to - self.backfill_chunk_size + 1)

        watched = self._watched_wallet_pairs()
        if not watched:
            # No Base wallet targets — advance so an empty range is not re-scanned.
            self.state['last_processed_block'] = max(int(last or 0), safe_to)
            return 0

        try:
            processed = self._scan_native_transfers(
                from_block, safe_to, watched, source_type=REALTIME_INGESTION_SOURCE,
            )
        except Exception as exc:
            logger.warning(
                'realtime_head_native_scan_failed from_block=%s to_block=%s watcher=%s error=%s',
                from_block, safe_to, self.watcher_name, str(exc)[:200],
            )
            # Do NOT advance the checkpoint — retry this range on the next head.
            return 0

        self.state['last_processed_block'] = max(int(last or 0), safe_to)
        return processed

    def _log_target_diagnostics(self, targets: list[dict[str, Any]]) -> None:
        """Emit one full-address diagnostic line per watched target.

        Addresses are NOT secrets — operators need the exact monitored address to
        confirm a MetaMask wallet matches what Decoda watches. Truncated forms like
        ``0x5f6f…1d1f`` hide the very mismatch this is meant to catch.
        """
        for target in targets:
            raw_addr = str(
                target.get('wallet_address') or target.get('contract_identifier') or ''
            )
            logger.info(
                'realtime_target_diagnostics target_id=%s workspace_id=%s chain_id=%s '
                'monitored_address_full=%s normalized_address_lowercase=%s watcher=%s',
                target.get('id'), target.get('workspace_id'), self.chain_id,
                raw_addr or 'none', raw_addr.lower() or 'none', self.watcher_name,
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

    def _effective_degraded_reason(self) -> str | None:
        """Return the degraded_reason to publish, fixing stale before-first-event text.

        ``provider_closes_before_first_event`` is only true while no head/event has
        arrived. Once the worker is receiving heads (real WSS heads, or HTTP
        fast-tail polling that fetches block numbers), continuing to publish that
        reason is false — it claims the provider never delivered data when it
        plainly is. So when heads/events have arrived we replace the stale reason:
        in HTTP fast-tail mode with ``http_fast_tail_active`` (still a fallback, but
        truthfully tailing), otherwise we clear it (WSS recovered). This is the
        canonical fact System Health renders, so the customer-facing limitation text
        stops showing 'provider closes before first event' once heads are flowing.
        """
        reason = self.state.get('degraded_reason')
        if reason in (
            'provider_closes_before_first_event',
            'provider_1001_reconnect_loop',
        ) and not self._closed_before_first_event():
            reason = 'http_fast_tail_active' if self._ingestion_mode == 'http_fast_tail' else None
            self.state['degraded_reason'] = reason
        return reason

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
        # provider_mode is a canonical fact: the active source_status string
        # (e.g. 'quicknode_http_fast_tail' once WSS is disabled). fallback_active
        # reflects the permanent WSS-disabled flag so health checks can tell that
        # the worker switched off WSS without inferring it from log scraping.
        _provider_mode = self.state.get('source_status') or self._ingestion_mode
        _fallback_active = bool(self._wss_permanently_disabled)
        # Resolve before reading state so the log line and the persisted row agree.
        _degraded_reason = self._effective_degraded_reason()
        logger.info(
            'realtime_worker_heartbeat watcher_name=%s chain_id=%s chain=%s '
            'last_event_at=%s reconnect_count=%s events_processed=%s '
            'heads_received=%s lag_blocks=%s degraded=%s degraded_reason=%s '
            'active_provider_host=%s provider_mode=%s fallback_active=%s',
            self.watcher_name,
            self.chain_id,
            self.chain_network,
            self.state.get('last_event_at') or 'none',
            self.state['metrics'].get('ws_reconnects', 0),
            _events_processed,
            self.state['metrics'].get('heads_received', 0),
            lag,
            bool(self.state.get('degraded')),
            _degraded_reason or 'none',
            _active_host,
            _provider_mode,
            _fallback_active,
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
                        _degraded_reason,
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

    @staticmethod
    def _is_rate_limit_error(exc: Exception) -> bool:
        """True when an RPC error string indicates provider throttling (HTTP 429)."""
        s = str(exc).lower()
        return '429' in s or 'rate limit' in s or 'rate_limit' in s or 'too many requests' in s

    def _pause_backfill(self, reason: str, cooldown: float) -> None:
        """Pause gap backfill for ``cooldown`` seconds.

        Sets a monotonic deadline so the per-head gap detector does not re-trigger
        a scan on every block from the same from_block while the provider is
        unhealthy (throttling or returning errors).
        """
        self._backfill_paused_until = time.monotonic() + cooldown
        logger.warning(
            'realtime_backfill_paused reason=%s cooldown_seconds=%.0f watcher=%s',
            reason, cooldown, self.watcher_name,
        )

    def _pause_backfill_for_rate_limit(self) -> None:
        """Back off gap backfill for 60-120 s after a provider rate-limit."""
        self.state['metrics']['backfill_rate_limited'] = (
            self.state['metrics'].get('backfill_rate_limited', 0) + 1
        )
        self._pause_backfill('rate_limited', 60.0 + random.random() * 60.0)

    def _backfill_paused(self) -> bool:
        """True while the rate-limit cooldown window is still active."""
        return time.monotonic() < self._backfill_paused_until

    def _persist_checkpoint(self, block: int) -> None:
        """Persist the realtime backfill checkpoint immediately after a successful chunk.

        Best-effort: a DB failure here is logged but never stops ingestion (the
        next heartbeat re-persists last_processed_block).
        """
        logger.info(
            'realtime_checkpoint_updated block=%s watcher=%s',
            block, self.watcher_name,
        )
        try:
            with pg_connection() as conn:
                ensure_pilot_schema(conn)
                conn.execute(
                    '''
                    UPDATE monitoring_watcher_state
                       SET last_processed_block = %s, updated_at = NOW()
                     WHERE watcher_name = %s
                    ''',
                    (int(block), self.watcher_name),
                )
                conn.commit()
        except Exception as exc:
            logger.warning(
                'realtime_checkpoint_persist_failed block=%s watcher=%s error=%s',
                block, self.watcher_name, str(exc)[:200],
            )

    async def _backfill(self, from_block: int, to_block: int) -> int:
        """Close a block gap one bounded chunk at a time, advancing the checkpoint.

        Behaviour (fixes the realtime_gap_detected loop):
        - Scans at most ``backfill_chunk_size`` blocks per call, so a large gap is
          closed gradually across successive heads instead of in a single
          full-range scan that burns provider rate limits.
        - Advances ``last_processed_block`` to the last scanned block even when the
          chunk contained zero matching events, so ``from_block`` always moves
          forward and the same gap is never re-scanned forever.
        - On a rate-limited or failed scan the checkpoint is NOT advanced and
          backfill is paused for a 60-120 s cooldown (no per-block retry storm).
        """
        if to_block < from_block:
            return 0
        if self._backfill_paused():
            # In rate-limit cooldown: do not scan or advance the checkpoint.
            return 0

        # One bounded chunk per call.
        end = min(int(to_block), int(from_block) + self.backfill_chunk_size - 1)

        watched = self._watched_wallet_pairs()

        logger.info(
            'realtime_backfill_chunk_started from_block=%s to_block=%s lag_blocks=%s watcher=%s',
            from_block, end, max(0, int(to_block) - int(from_block)), self.watcher_name,
        )

        processed = 0
        try:
            for target, addr in watched:
                logs = self._rpc_call(
                    'eth_getLogs',
                    [{'fromBlock': hex(int(from_block)), 'toBlock': hex(end),
                      'topics': [[TRANSFER_TOPIC, APPROVAL_TOPIC]]}],
                ) or []
                for log in logs:
                    if bool(log.get('removed')):
                        continue
                    topics = [str(t).lower() for t in (log.get('topics') or [])]
                    log_addr = str(log.get('address') or '').lower()
                    if addr not in topics and addr != log_addr:
                        continue
                    if self._is_rate_limited():
                        logger.warning(
                            'realtime_rate_limit_exceeded watcher=%s during_backfill',
                            self.watcher_name,
                        )
                        continue
                    event = self._build_event_from_log(target, log, source_type='realtime_backfill')
                    self._persist_event(target, event)
                    processed += 1

            # Native ETH transfers emit NO logs, so the eth_getLogs scan above can
            # never see them. Scan the same block range's full transactions so a
            # plain ETH send to/from a watched wallet is detected here instead of
            # only by the 300 s polling worker minutes later.
            processed += self._scan_native_transfers(
                int(from_block), int(end), watched, source_type='realtime_backfill',
            )
        except Exception as exc:
            if self._is_rate_limit_error(exc):
                self._pause_backfill_for_rate_limit()
            else:
                logger.warning(
                    'realtime_backfill_scan_failed from_block=%s to_block=%s watcher=%s error=%s',
                    from_block, end, self.watcher_name, str(exc)[:200],
                )
                # Pause briefly so a failing chunk is not re-attempted from the
                # same from_block on every new head (no per-block retry storm).
                self._pause_backfill('scan_failed', 15.0 + random.random() * 15.0)
            # Failed/throttled scan — do NOT advance the checkpoint.
            return processed

        # Chunk fully scanned: advance and persist the checkpoint to the chunk end,
        # even when zero matching events were found.
        self.state['last_processed_block'] = max(
            int(self.state.get('last_processed_block') or 0), int(end),
        )
        self.state['metrics']['backfill_chunks'] = (
            self.state['metrics'].get('backfill_chunks', 0) + 1
        )
        logger.info(
            'realtime_backfill_chunk_completed last_scanned_block=%s events=%s watcher=%s',
            end, processed, self.watcher_name,
        )
        self._persist_checkpoint(int(end))

        if processed:
            self.state['metrics']['rpc_backfills'] += 1
            increment('decoda_realtime_backfills_total', chain=self.chain_network)
        return processed

    # ------------------------------------------------------------------
    # Checkpoint bootstrap (cold start / start-at-latest)
    # ------------------------------------------------------------------

    def _load_persisted_checkpoint(self) -> int | None:
        """Return this watcher's persisted last_processed_block, or None.

        Best-effort: any DB error (including no DATABASE_URL configured) returns
        None so the caller falls back to start-at-latest / latest-confirmations.
        """
        try:
            with pg_connection() as conn:
                ensure_pilot_schema(conn)
                row = conn.execute(
                    'SELECT last_processed_block FROM monitoring_watcher_state WHERE watcher_name = %s',
                    (self.watcher_name,),
                ).fetchone()
        except Exception as exc:
            logger.warning(
                'realtime_checkpoint_load_failed watcher=%s error=%s',
                self.watcher_name, str(exc)[:200],
            )
            return None
        if not row:
            return None
        val = row.get('last_processed_block') if hasattr(row, 'get') else row[0]
        if val is None:
            return None
        try:
            block = int(val)
        except (TypeError, ValueError):
            return None
        return block if block > 0 else None

    def _bootstrap_checkpoint(self, head: int) -> int:
        """Resolve the starting last_processed_block exactly once on cold start.

        Priority:
        1. When BASE_REALTIME_START_AT_LATEST is enabled and there is a real
           historical gap (checkpoint more than ``gap_threshold_blocks`` behind
           head, or no checkpoint at all), skip the old gap and start from
           head - confirmations. This is the fix for the realtime_gap_detected
           loop where from_block stuck on one old block forever.
        2. Otherwise, a persisted checkpoint within
           ``_DEFAULT_CHECKPOINT_RELIABLE_MAX_LAG`` of head is reliable -> resume
           from it so no blocks are missed.
        3. Otherwise resume from the (stale) checkpoint if present, else start
           from head - confirmations. The bounded chunk backfill closes any gap
           gradually, so even this path never loops on one old block.
        """
        latest_start = max(0, int(head) - self.confirmations_required)
        checkpoint = self._load_persisted_checkpoint()
        gap = (int(head) - checkpoint) if checkpoint is not None else None

        # Start-at-latest takes priority over a stale checkpoint: when enabled and
        # there is a real gap to skip, jump straight to head - confirmations and
        # never replay the old history. A checkpoint within gap_threshold_blocks of
        # head is effectively current, so it is resumed (no recent blocks missed).
        if self.start_at_latest and (gap is None or gap > self.gap_threshold_blocks):
            old_from_block = (checkpoint + 1) if checkpoint is not None else latest_start
            logger.warning(
                'realtime_start_at_latest_applied old_from_block=%s new_checkpoint=%s '
                'head=%s skipped_gap_blocks=%s watcher=%s',
                old_from_block, latest_start, head,
                gap if gap is not None else 'none', self.watcher_name,
            )
            return latest_start

        if checkpoint is not None and gap is not None and gap <= _DEFAULT_CHECKPOINT_RELIABLE_MAX_LAG:
            logger.info(
                'realtime_checkpoint_resumed block=%s head=%s watcher=%s',
                checkpoint, head, self.watcher_name,
            )
            return min(checkpoint, latest_start)

        if checkpoint is not None:
            logger.info(
                'realtime_checkpoint_resumed_stale block=%s head=%s watcher=%s '
                'hint=set_BASE_REALTIME_START_AT_LATEST_true_to_skip_large_gaps',
                checkpoint, head, self.watcher_name,
            )
            return checkpoint

        logger.info(
            'realtime_checkpoint_cold_start start_block=%s head=%s watcher=%s',
            latest_start, head, self.watcher_name,
        )
        return latest_start

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
                # Full monitored address per target so an operator can confirm the
                # watched address matches their MetaMask wallet exactly.
                self._log_target_diagnostics(_startup_targets)
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
                            if self._backfill_paused():
                                # Provider is rate-limited: do not re-trigger gap
                                # backfill on every head. The cooldown clears itself.
                                pass
                            else:
                                logger.warning(
                                    'realtime_gap_detected chain=%s from_block=%s to_block=%s '
                                    'lag_blocks=%s bounded_chunk=%s',
                                    self.chain_network, int(last) + 1, head,
                                    head - int(last), self.backfill_chunk_size,
                                )
                                # Bounded: advances the checkpoint by one chunk per
                                # head, so from_block never sticks on one old block.
                                # Catch-up scan → detected_by=realtime_backfill.
                                await self._backfill(int(last) + 1, head)
                        else:
                            # Steady state (caught up): scan the newly confirmed head
                            # block(s) directly for native ETH transfers, which emit no
                            # logs and so are invisible to the logs subscription. This
                            # is the realtime_websocket detection path — without it a
                            # plain ETH send was only ever caught by stable polling.
                            await self._scan_head_native_transfers(head)

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
        # Preserve a reconnect-loop reason set by the breaker; otherwise default to
        # the before-first-event reason. Both are rewritten to http_fast_tail_active
        # by _effective_degraded_reason once the fast-tail poll starts fetching heads.
        if self.state.get('degraded_reason') != 'provider_1001_reconnect_loop':
            self.state['degraded_reason'] = 'provider_closes_before_first_event'

        _poll_interval = float(
            max(30, min(60, _resolve_int_env('REALTIME_HTTP_FAST_TAIL_INTERVAL_SECONDS', 45)))
        )

        # Count active watched targets for the start log. Never logs IDs/addresses.
        try:
            _ft_target_count = len(self._watched_targets())
        except Exception:
            _ft_target_count = -1

        # Canonical fallback-started marker — heartbeat/health derive provider_mode
        # from this transition. Uses the HTTPS QuickNode RPC env (self.rpc_url),
        # never the WSS endpoint.
        logger.warning(
            'quicknode_fast_tail_started chain_id=%s target_count=%s interval_seconds=%.0f',
            self.chain_id, _ft_target_count, _poll_interval,
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

                # Native ETH transfers (no logs) for the recent tail. Bounded to the
                # most recent blocks so a stale checkpoint can never trigger a giant
                # block-by-block fetch in one poll; the 300 s polling worker's own
                # bounded backfill covers any deeper history. Only advance the cursor
                # below when this also succeeds, so a failed native scan re-scans next
                # poll rather than skipping transfers.
                if scan_all_ok:
                    _ft_watched = [
                        (
                            t,
                            str(t.get('wallet_address') or t.get('contract_identifier') or '').lower(),
                        )
                        for t in targets
                        if str(t.get('wallet_address') or t.get('contract_identifier') or '')
                        .lower().startswith('0x')
                    ]
                    _native_from = max(int(from_block), int(to_block) - _MAX_BACKFILL_CHUNK_SIZE + 1)
                    try:
                        self._scan_native_transfers(
                            _native_from, to_block, _ft_watched,
                            source_type='realtime_http_fast_tail',
                        )
                    except Exception as native_exc:
                        logger.warning(
                            'http_fast_tail_native_scan_failed watcher=%s error=%s',
                            self.watcher_name, str(native_exc)[:200],
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
                    if self.state.get('last_processed_block') is None and not self._checkpoint_bootstrapped:
                        self.state['last_processed_block'] = self._bootstrap_checkpoint(int(head))
                        self._checkpoint_bootstrapped = True

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

                # Provider-wide reconnect-loop / stale breaker (requirements 1, 2, 5).
                # Runs for EVERY 1001 close, independent of subscription mode and of
                # consecutive_1001 (which is useless here because it resets to 0 the
                # moment any head was ever received). Trips once the WSS keeps closing
                # 1001 while last_event_at no longer advances, or last_event_at is
                # 2+ minutes stale — the exact production loop where 6038 heads were
                # delivered, then the socket closed 1001 forever with a frozen cursor.
                if is_1001_close and self._note_1001_close_for_breaker():
                    self._trip_reconnect_loop_breaker()

                # Auto-downgrade from newHeads,logs → newHeads_only after 3 consecutive
                # 1001 closes. After downgrade, fail over to secondary URL if configured.
                # Skipped once the reconnect-loop breaker has disabled WSS above.
                if self._wss_permanently_disabled:
                    pass
                elif is_1001_close and self.subscriptions == 'newHeads,logs':
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
                    # Only count closes that fired before the provider delivered the
                    # first real chain event (a head or a log).  A subscription
                    # confirmation does NOT count — QuickNode ACKs the newHeads
                    # subscription and then closes 1001 before the first head, so
                    # gating on _session_messages_received would reset the counter
                    # forever and the fallback would never fire.  Once a head/event
                    # arrives the provider is proven healthy and the counter resets.
                    if self._closed_before_first_event():
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
                                'close_count=%s watcher=%s',
                                self._consecutive_1001_closes,
                                self.watcher_name,
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
                            if self._closed_before_first_event() else ''
                        )
                        logger.info(
                            'realtime_ws_closed_cleanly chain=%s watcher=%s '
                            'code=1001 reconnecting reconnect_count=%s '
                            'consecutive_1001=%s close_count_since_last_head=%s '
                            'total_provider_close_count=%s last_event_age_seconds=%s '
                            'subscriptions=%s%s',
                            self.chain_network, self.watcher_name,
                            self.state['metrics']['ws_reconnects'],
                            self._consecutive_1001_closes,
                            self._total_close_count_since_last_head,
                            self._total_provider_close_count,
                            (lambda a: round(a, 1) if a is not None else 'none')(
                                self._seconds_since_last_event()
                            ),
                            self.subscriptions,
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
