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
from datetime import datetime, timedelta, timezone
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
    explain_wallet_transfer_match,
    native_transfer_direction,
    resolve_monitored_wallet,
)
from services.api.app.monitoring_runner import (
    ActivityEvent,
    _load_target_asset_context,
    process_ingested_event,
)
from services.api.app.observability import increment, gauge
from services.api.app.pilot import ensure_pilot_schema, pg_connection
from services.api.app.worker_status import (
    classify_realtime_tx_verdict,
    detected_by_from_ingestion_source,
)

logger = logging.getLogger(__name__)

BASE_CHAIN_ID = 8453
BASE_CHAIN_NETWORK = 'base'
REALTIME_INGESTION_SOURCE = 'realtime_websocket'
# detected_by tag for the HTTP fast-tail fallback. Native ETH transfers and ERC20
# logs surfaced by the HTTPS RPC fast-tail loop carry this so the customer-facing
# "Detected by" reads quicknode_http_fast_tail (never realtime_websocket, which
# would falsely claim the WSS delivered them). Registered in
# worker_status.REALTIME_DETECTED_BY so those rows classify as realtime telemetry.
HTTP_FAST_TAIL_SOURCE = 'quicknode_http_fast_tail'

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

# Provider rate-limit circuit breaker. When QuickNode rejects the WebSocket upgrade
# with HTTP 429 it is a hard, provider-wide rate limit — reconnecting every 60-120s
# just hammers the same limit. Instead the worker trips a cooldown: it stops WSS
# reconnects for this many seconds (default 15 minutes), publishes provider_rate_limited
# plus a next-retry timestamp, and lets the independent 300s stable polling worker keep
# detecting transfers. Configurable via BASE_REALTIME_RATE_LIMIT_COOLDOWN_SECONDS.
_DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS = 900

# HTTP fast-tail fallback tuning (requirement 3). The fast-tail loop polls the
# HTTPS RPC (never the WSS) on this interval and scans at most this many of the
# most-recent blocks per cycle, so a stale checkpoint can never trigger a giant
# historical scan — the independent 300 s stable polling worker closes any deeper
# gap. Configurable via BASE_REALTIME_FAST_TAIL_INTERVAL_SECONDS /
# BASE_REALTIME_FAST_TAIL_CHUNK_SIZE.
_DEFAULT_FAST_TAIL_INTERVAL_SECONDS = 60
_DEFAULT_FAST_TAIL_CHUNK_SIZE = 10
# Maximum block lag the HTTP fast-tail will auto-catch-up. When the checkpoint is
# more than this many blocks behind head, replaying the whole gap would burn provider
# quota (the production 40k-block lag explosion), so the fast-tail logs
# realtime_fast_tail_lag_too_large, fast-forwards the cursor to a bounded window
# behind head (lag stops growing), and leaves the skipped span to tx-hash import /
# bounded backfill and the independent 300 s stable poller. Configurable via
# BASE_REALTIME_FAST_TAIL_MAX_CATCHUP_BLOCKS; floored at fast_tail_chunk_size.
_DEFAULT_FAST_TAIL_MAX_CATCHUP_BLOCKS = 100

# Bounds for the in-memory (and heartbeat-persisted) tx-diagnosis facts. Scanned
# spans merge into a handful of contiguous ranges in practice; rate-limit windows
# accrue one per cooldown. Both are capped so heartbeat metrics stay small.
_MAX_SCANNED_SPANS = 100
_MAX_RATE_LIMIT_WINDOWS = 20


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


def _resolve_tx_hash_list_env(name: str) -> list[str]:
    """Parse a comma/space-separated list of 0x tx hashes from an env var.

    Backs the tx-hash debug mode (``BASE_REALTIME_DEBUG_TX_HASHES``). Only
    well-formed 0x-prefixed 32-byte hex strings are kept; anything else is dropped
    so a mis-set value can never turn into an ``eth_getTransactionByHash`` call with
    a bogus hash. Deduped, lowercased, order preserved.
    """
    raw = (os.getenv(name) or '').strip()
    if not raw:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for part in raw.replace(',', ' ').split():
        h = part.strip().lower()
        if not (h.startswith('0x') and len(h) == 66):
            continue
        try:
            int(h, 16)
        except ValueError:
            continue
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out


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
        # Set True after the gap backfill's eth_getLogs returns HTTP 413 enough times
        # that its chunk cannot be shrunk further. Native ETH detection never depends
        # on eth_getLogs (requirement E), so the log scan is disabled and only the
        # native full-transaction scan runs — never looping on the same 413 chunk.
        self._backfill_log_scan_disabled: bool = False
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

        # Provider rate-limit circuit breaker (HTTP 429 on the WSS handshake).
        # On a 429 the worker stops WSS reconnects for this cooldown window instead
        # of reconnecting every 60-120s into the same rate limit (requirements 1-3).
        self.rate_limit_cooldown_seconds = max(
            1,
            _resolve_int_env(
                'BASE_REALTIME_RATE_LIMIT_COOLDOWN_SECONDS', _DEFAULT_RATE_LIMIT_COOLDOWN_SECONDS
            ),
        )
        # HTTP fast-tail polls the SAME QuickNode HTTP quota, so starting it during a
        # 429 makes the rate limit worse. Default OFF — only run fast-tail during a
        # rate limit when a SEPARATE HTTP budget exists (requirement 4).
        self.fast_tail_enabled = _resolve_bool_env('BASE_REALTIME_FAST_TAIL_ENABLED', default=False)
        # Fast-tail poll cadence (HTTPS RPC only). Floored at 5 s so a mis-set value
        # cannot busy-loop the provider; default 60 s (requirement 3).
        self.fast_tail_interval_seconds = max(
            5, _resolve_int_env('BASE_REALTIME_FAST_TAIL_INTERVAL_SECONDS', _DEFAULT_FAST_TAIL_INTERVAL_SECONDS)
        )
        # Most-recent blocks scanned per fast-tail cycle. Clamped to
        # [1, _MAX_BACKFILL_CHUNK_SIZE] so it can never widen a single scan past the
        # safe maximum; default 10. Bounds both the eth_getLogs range and the native
        # transaction scan so a huge historical gap is never scanned in one poll.
        self.fast_tail_chunk_size = min(
            _MAX_BACKFILL_CHUNK_SIZE,
            max(1, _resolve_int_env('BASE_REALTIME_FAST_TAIL_CHUNK_SIZE', _DEFAULT_FAST_TAIL_CHUNK_SIZE)),
        )
        # Max block lag the fast-tail auto-catches-up (requirement 4). A larger gap is
        # never scanned in-loop; the cursor is fast-forwarded so lag cannot grow forever.
        # Floored at fast_tail_chunk_size so it can never be smaller than a single scan.
        self.fast_tail_max_catchup_blocks = max(
            self.fast_tail_chunk_size,
            _resolve_int_env(
                'BASE_REALTIME_FAST_TAIL_MAX_CATCHUP_BLOCKS', _DEFAULT_FAST_TAIL_MAX_CATCHUP_BLOCKS
            ),
        )
        # Set True after eth_getLogs returns HTTP 413 (payload too large) in the
        # fast-tail loop. eth_getLogs will 413 forever for this workload, so the
        # log-based scan is disabled and only the native ETH transaction scan runs
        # (requirement 3) — native detection never depends on eth_getLogs.
        self._fast_tail_log_scan_disabled: bool = False
        # Optional tx-hash debug mode (requirements 1-2). When
        # BASE_REALTIME_DEBUG_TX_HASHES lists one or more tx hashes, the worker
        # fetches each via eth_getTransactionByHash once on WSS / fast-tail startup,
        # logs a full match diagnostic (realtime_tx_debug), and — when the tx is
        # at/below the realtime checkpoint so the forward head scan will never reach
        # it — runs a bounded ±2-block backfill around it
        # (realtime_tx_skipped_by_checkpoint). Safe: gated behind the env var, scans
        # at most 5 blocks per hash, and only reads chain data (never sends a tx).
        self.debug_tx_hashes: list[str] = _resolve_tx_hash_list_env('BASE_REALTIME_DEBUG_TX_HASHES')
        # Guards the one-shot startup debug so a reconnect does not re-run it.
        self._tx_debug_completed: bool = False

        # True while inside a provider rate-limit cooldown (WSS reconnects paused).
        self._provider_rate_limited: bool = False
        # Monotonic deadline; while time.monotonic() < this, no WSS reconnect happens.
        self._rate_limit_cooldown_until: float = 0.0
        # Wall-clock ISO timestamp of the next WSS retry, surfaced to System Health/UI.
        self._rate_limit_retry_at: str | None = None
        # Count of distinct rate-limit trips, for observability.
        self._rate_limit_count: int = 0
        # Wall-clock history of rate-limit cooldown windows
        # ({'started_at', 'ended_at', 'next_retry_at'} ISO strings; ended_at None while
        # the cooldown is still open). Backs the tx-hash diagnosis question "was the
        # provider rate-limited when this tx landed?" (rate_limited_at_time /
        # realtime_tx_missed_due_to_rate_limit). Bounded; persisted in heartbeat
        # metrics so the read-only diagnose-tx endpoint can answer it too.
        self._rate_limit_windows: list[dict[str, Any]] = []

        # Block ranges this process ACTUALLY native-scanned (merged, sorted,
        # disjoint [from, to] pairs). This is the truthful basis for
        # was_block_scanned in the tx-hash diagnosis: the old
        # [scan_start_block, last_processed_block] inference over-claimed after a
        # rate-limit cooldown, when the live-tail fast-forwards the checkpoint past
        # blocks that were never scanned (the production "matches=0 but tx exists"
        # case). Recording what was scanned — instead of inferring it — can never
        # claim coverage of a skipped span. Bounded; persisted in heartbeat metrics.
        self._scanned_spans: list[list[int]] = []

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

        # Live-tail single-flight + coalescing (requirement C). Only ONE block scan
        # runs at a time; newHeads that arrive while a scan is in flight are coalesced
        # into the next scan window (only the latest head is scanned) so a burst of
        # heads never fans out into one RPC-heavy scan per head. The live-tail scan
        # itself always uses the newHeads-supplied block number, never eth_blockNumber.
        self._head_scan_in_flight: bool = False
        self._coalesced_head: int | None = None

        # Target-loading (monitored-address) degraded signal. Set True when at least
        # one active Base target has no resolvable monitored address so it is excluded
        # from realtime matching (requirement 3). Kept SEPARATE from the WSS/provider
        # degraded flag so a config gap never marks a healthy socket degraded.
        self._target_loading_degraded: bool = False
        self._target_loading_degraded_reason: str | None = None

        self.state: dict[str, Any] = {
            'source_status': 'degraded',
            'degraded': False,
            'degraded_reason': None,
            'last_processed_block': None,
            # Lowest block the forward head scan will ever cover (the cold-start /
            # resume checkpoint). Anything strictly below this was skipped at cold
            # start and can only be recovered via the bounded tx-hash backfill or the
            # import-tx endpoint — surfaced as was_block_scanned in the tx debug.
            'scan_start_block': None,
            # Most recent live-tail native scan window (requirement: the tx-hash
            # debug reports live_tail_from_block / live_tail_to_block so an operator
            # can see whether the tx block fell inside the window actually tailed).
            'live_tail_from_block': None,
            'live_tail_to_block': None,
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
    # Provider rate-limit circuit breaker (HTTP 429 on the WSS handshake)
    # ------------------------------------------------------------------

    def _rate_limit_cooldown_active(self) -> bool:
        """True while the provider rate-limit cooldown window is still open."""
        return self._provider_rate_limited and time.monotonic() < self._rate_limit_cooldown_until

    def _rate_limit_cooldown_remaining(self) -> float:
        """Seconds left in the rate-limit cooldown (0 when not active)."""
        if not self._provider_rate_limited:
            return 0.0
        return max(0.0, self._rate_limit_cooldown_until - time.monotonic())

    def _fallback_is_active(self) -> bool:
        """True when a realtime fallback path (HTTP fast-tail) is actually running.

        A permanent WSS-disable always runs fast-tail. A provider rate-limit only
        runs fast-tail when BASE_REALTIME_FAST_TAIL_ENABLED is set (a separate HTTP
        budget); by default it does NOT, so no realtime fallback runs and only the
        independent 300s stable polling worker covers detection — the heartbeat then
        truthfully reports fallback_active=False (requirement 4).
        """
        if self._wss_permanently_disabled:
            return True
        if self._provider_rate_limited:
            return self.fast_tail_enabled
        return False

    def _enter_provider_rate_limit_cooldown(self) -> None:
        """Trip the rate-limit breaker after an HTTP 429 on the WSS handshake.

        Stops the WSS reconnect loop for the cooldown window instead of reconnecting
        every 60-120s into the same rate limit. Publishes the canonical
        ``realtime_ws_disabled_for_provider reason=rate_limited_http_429`` marker plus
        a next-retry timestamp so System Health renders "rate limited" (not a generic
        degraded). Stable RPC polling (a separate worker) keeps detecting transfers.
        """
        self._provider_rate_limited = True
        self._rate_limit_count += 1
        self._rate_limit_cooldown_until = time.monotonic() + self.rate_limit_cooldown_seconds
        retry_at = datetime.now(timezone.utc) + timedelta(seconds=self.rate_limit_cooldown_seconds)
        self._rate_limit_retry_at = retry_at.isoformat()
        # Record the cooldown window (wall clock) so the tx-hash diagnosis can later
        # answer "was the provider rate-limited when this tx landed?" truthfully.
        self._rate_limit_windows.append({
            'started_at': datetime.now(timezone.utc).isoformat(),
            'ended_at': None,
            'next_retry_at': self._rate_limit_retry_at,
        })
        if len(self._rate_limit_windows) > _MAX_RATE_LIMIT_WINDOWS:
            self._rate_limit_windows = self._rate_limit_windows[-_MAX_RATE_LIMIT_WINDOWS:]
        self.state['source_status'] = 'provider_rate_limited'
        self.state['degraded'] = True
        self.state['degraded_reason'] = 'provider_rate_limited'
        logger.warning(
            'realtime_ws_disabled_for_provider reason=rate_limited_http_429 '
            'cooldown_seconds=%s next_retry_at=%s fast_tail_enabled=%s '
            'rate_limit_count=%s watcher=%s',
            self.rate_limit_cooldown_seconds,
            self._rate_limit_retry_at,
            self.fast_tail_enabled,
            self._rate_limit_count,
            self.watcher_name,
        )
        # Canonical cooldown-started marker (requirement 4). Distinct from the
        # ws-disabled marker above so an operator/log query can key on the cooldown
        # window and its next-retry deadline explicitly.
        logger.warning(
            'realtime_rate_limit_cooldown_started seconds=%s next_retry_at=%s watcher=%s',
            self.rate_limit_cooldown_seconds,
            self._rate_limit_retry_at,
            self.watcher_name,
        )

    def _resume_after_rate_limit_cooldown(self) -> None:
        """Clear the rate-limit breaker once the cooldown window has elapsed.

        The next loop iteration attempts a fresh WSS connection (realtime resumes).
        """
        self._provider_rate_limited = False
        self._rate_limit_cooldown_until = 0.0
        self._rate_limit_retry_at = None
        # Close the open cooldown window so rate_limited_at_time stays accurate for
        # transactions that land after realtime scanning resumes.
        if self._rate_limit_windows and self._rate_limit_windows[-1].get('ended_at') is None:
            self._rate_limit_windows[-1]['ended_at'] = datetime.now(timezone.utc).isoformat()
        self.state['degraded_reason'] = 'provider_rate_limit_cooldown_cleared'
        logger.info(
            'realtime_rate_limit_cooldown_cleared resuming_wss rate_limit_count=%s watcher=%s',
            self._rate_limit_count,
            self.watcher_name,
        )

    def _rate_limit_window_covering(self, ts: datetime) -> dict[str, Any] | None:
        """Return the rate-limit cooldown window covering wall-clock ``ts``, if any.

        A window still open (``ended_at`` None) covers up to its ``next_retry_at``
        deadline. Timestamps that parse badly fail closed to "not covered" so the
        diagnosis never claims a rate limit it cannot prove.
        """
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        for window in reversed(self._rate_limit_windows):
            try:
                started = datetime.fromisoformat(str(window.get('started_at')))
                ended_raw = window.get('ended_at') or window.get('next_retry_at')
                ended = datetime.fromisoformat(str(ended_raw)) if ended_raw else None
            except (TypeError, ValueError):
                continue
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            if ended is not None and ended.tzinfo is None:
                ended = ended.replace(tzinfo=timezone.utc)
            if started <= ts and (ended is None or ts <= ended):
                return window
        return None

    # ------------------------------------------------------------------
    # Scanned-block spans (truthful was_block_scanned for tx diagnosis)
    # ------------------------------------------------------------------

    def _note_scanned_range(self, from_block: int, to_block: int) -> None:
        """Record that ``[from_block, to_block]`` was fully native-scanned.

        Merges into a sorted list of disjoint spans (adjacent spans coalesce) so the
        common case — a contiguous live tail — stays a single entry. Only called
        after a scan completed every block in the range, so a mid-range RPC failure
        never records unproven coverage (fail-closed).
        """
        if to_block < from_block:
            return
        spans = self._scanned_spans + [[int(from_block), int(to_block)]]
        spans.sort()
        merged: list[list[int]] = []
        for span in spans:
            if merged and span[0] <= merged[-1][1] + 1:
                merged[-1][1] = max(merged[-1][1], span[1])
            else:
                merged.append(span)
        self._scanned_spans = merged[-_MAX_SCANNED_SPANS:]

    def _was_block_scanned(self, block_number: int | None) -> bool:
        """True only when this process actually native-scanned ``block_number``.

        Span-based, never inferred from [scan_start_block, checkpoint]: after a
        rate-limit cooldown the live-tail fast-forwards the checkpoint past blocks
        it never scanned, and the old inference falsely claimed those were covered.
        """
        if block_number is None:
            return False
        blk = int(block_number)
        return any(span[0] <= blk <= span[1] for span in self._scanned_spans)

    def _scanned_window_bounds(self) -> tuple[int | None, int | None]:
        """Overall (lowest, highest) block this process has native-scanned."""
        if not self._scanned_spans:
            return None, None
        return self._scanned_spans[0][0], self._scanned_spans[-1][1]

    # ------------------------------------------------------------------
    # Target loading (workspace-scoped)
    # ------------------------------------------------------------------

    def _watched_targets(self) -> list[dict[str, Any]]:
        """Load all active monitoring targets scoped to Base chain.

        Each call opens and closes its own connection so no connection is
        held while waiting for the next WebSocket message.

        Loads the SAME columns the stable RPC polling worker reads
        (``asset_id``, ``chain_id``, ``target_metadata``) in addition to the
        canonical ``wallet_address`` / ``contract_identifier`` so
        :func:`resolve_monitored_wallet` can resolve a monitored address that is
        stored in a fallback location (the linked asset's identifier or
        ``target_metadata``) — the exact case where realtime previously logged
        ``monitored_address_full=none`` while stable polling detected transfers.
        """
        with pg_connection() as conn:
            ensure_pilot_schema(conn)
            rows = conn.execute(
                '''
                SELECT id, workspace_id, name, target_type, chain_network, chain_id,
                       wallet_address, contract_identifier, asset_id, target_metadata,
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
    # Monitored-address resolution (shared with stable RPC polling)
    # ------------------------------------------------------------------

    def _resolve_target_address(self, target: dict[str, Any]) -> str | None:
        """Resolve a target's monitored EVM address the SAME way stable polling does.

        Uses :func:`resolve_monitored_wallet` — the canonical resolver the 300 s
        stable RPC polling worker relies on — which checks ``wallet_address``, then
        ``contract_identifier``, the linked asset's identifier (``asset_context``),
        and ``target_metadata``. For a wallet target whose ``wallet_address`` column
        is empty, the linked asset context is loaded on demand (mirroring
        ``monitoring_runner.process_monitoring_target``) so realtime resolves the
        exact address stable polling already detects transfers from. Returns a
        lowercase ``0x`` address, or ``None`` when no valid address is configured
        anywhere (fail-closed — the caller excludes the target from matching).
        """
        addr = resolve_monitored_wallet(target)
        if addr:
            return addr
        if (
            str(target.get('target_type') or '').lower() == 'wallet'
            and not target.get('wallet_address')
            and target.get('asset_context') is None
            and target.get('asset_id')
        ):
            ctx = self._load_asset_context_for(target)
            if isinstance(ctx, dict):
                target['asset_context'] = ctx
                addr = resolve_monitored_wallet(target)
        return addr

    def _load_asset_context_for(self, target: dict[str, Any]) -> dict[str, Any] | None:
        """Load the linked asset context for a target (best-effort, own connection)."""
        try:
            with pg_connection() as conn:
                ensure_pilot_schema(conn)
                return _load_target_asset_context(
                    conn, workspace_id=str(target.get('workspace_id') or ''), target=target
                )
        except Exception as exc:
            logger.warning(
                'realtime_target_asset_context_load_failed target_id=%s error=%s',
                target.get('id'), str(exc)[:160],
            )
            return None

    def _note_target_loading(self, *, loaded: int, with_address: int, missing: int) -> None:
        """Record target-loading facts and the fail-closed missing-address degraded flag.

        Requirement 3: an active target with no resolvable monitored address is
        excluded from realtime matching and target loading is marked degraded with
        ``reason=missing_monitored_address``. This is a TARGET-LOADING signal kept
        distinct from the WSS/provider ``degraded`` flag, so a target that cannot be
        resolved never falsely flips a healthy realtime socket to degraded — while a
        genuinely healthy load (every target resolved) clears it.
        """
        metrics = self.state['metrics']
        metrics['targets_loaded'] = loaded
        metrics['targets_with_address'] = with_address
        metrics['targets_missing_address'] = missing
        if missing > 0:
            self._target_loading_degraded = True
            self._target_loading_degraded_reason = 'missing_monitored_address'
        else:
            self._target_loading_degraded = False
            self._target_loading_degraded_reason = None
        metrics['target_loading_degraded'] = self._target_loading_degraded
        metrics['target_loading_degraded_reason'] = self._target_loading_degraded_reason

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
        live_tail: bool = False,
        result_by_tx: dict[str, dict[str, Any]] | None = None,
    ) -> int:
        """Detect native ETH transfers to/from watched wallets in a block range.

        Native ETH transfers carry NO logs, so ``eth_getLogs`` can never see them.
        The only way to detect them is to fetch each block's full transaction list
        (``eth_getBlockByNumber`` with full=True) and match ``tx.from`` / ``tx.to``
        against the watched wallet via the shared :func:`native_transfer_direction`
        matcher (same one the polling worker uses) — never ``eth_getLogs``
        (requirement B).

        When ``live_tail`` is True the scan is the newest-head live-tail path
        (requirement A): in addition to the generic ``realtime_native_transfer_*``
        markers it emits the canonical ``realtime_live_tail_scan_started`` /
        ``realtime_live_tail_match`` / ``realtime_live_tail_persisted`` /
        ``realtime_live_tail_scan_complete`` lines so a log query can prove the
        live-tail scanned the current blocks independently of any gap backfill.

        Returns the number of matched transfers persisted. Raises on RPC failure so
        the caller's rate-limit / pause handling applies (the checkpoint is then not
        advanced and the range is re-scanned next cycle).
        """
        if to_block < from_block or not watched:
            return 0
        if live_tail:
            # Record the most recent live-tail window as a canonical fact; the
            # tx-hash debug reports it as live_tail_from_block / live_tail_to_block.
            self.state['live_tail_from_block'] = int(from_block)
            self.state['live_tail_to_block'] = int(to_block)
            # Requirement A: canonical live-tail start marker naming the exact newest
            # block window scanned. Emitted regardless of gap/backfill state so an
            # operator can confirm the current blocks were scanned immediately.
            logger.info(
                'realtime_live_tail_scan_started chain_id=%s from_block=%s to_block=%s '
                'watched_targets=%s detected_by=%s watcher=%s',
                self.chain_id, from_block, to_block, len(watched), source_type,
                self.watcher_name,
            )
        _is_fast_tail = source_type == HTTP_FAST_TAIL_SOURCE
        if _is_fast_tail:
            # Requirement 6: canonical fast-tail native-scan start marker. Emitted in
            # addition to the generic line below so a log query keyed on the fast-tail
            # fallback finds it without matching the WSS/backfill native scans.
            logger.info(
                'realtime_fast_tail_native_scan_started chain_id=%s from_block=%s to_block=%s '
                'watched_targets=%s watcher=%s',
                self.chain_id, from_block, to_block, len(watched), self.watcher_name,
            )
        logger.info(
            'realtime_native_transfer_scan_started chain_id=%s from_block=%s to_block=%s '
            'watched_targets=%s detected_by=%s watcher=%s',
            self.chain_id, from_block, to_block, len(watched), source_type, self.watcher_name,
        )
        processed = 0
        blocks_scanned = 0
        txs_seen = 0
        matches = 0
        for block_number in range(int(from_block), int(to_block) + 1):
            block = self._rpc_call('eth_getBlockByNumber', [hex(block_number), True]) or {}
            blocks_scanned += 1
            block_hash = str(block.get('hash') or '') or None
            observed_at = _iso_from_block_ts(block.get('timestamp'))
            for tx in (block.get('transactions') or []):
                # A hash-only block (eth_getBlockByNumber called without full=True, or a
                # provider that ignores it) yields str entries, never a dict — those can
                # never be matched. Counting only dict txs in txs_seen makes the
                # scan_complete line reveal that case (txs_seen=0 despite a full block).
                if not isinstance(tx, dict):
                    continue
                txs_seen += 1
                tx_hash = str(tx.get('hash') or '')
                matched_any = False
                for target, addr in watched:
                    direction = native_transfer_direction(addr, tx)
                    if direction is None:
                        continue
                    matched_any = True
                    matches += 1
                    value_wei = _hex_to_int(tx.get('value')) or 0
                    logger.info(
                        'realtime_native_transfer_candidate tx_hash=%s from=%s to=%s value_wei=%s '
                        'detected_by=%s',
                        tx_hash, _short_addr(tx.get('from')), _short_addr(tx.get('to')), value_wei,
                        source_type,
                    )
                    # Full from/to/value/block_number so an operator can confirm the exact
                    # matched transfer without cross-referencing the truncated candidate line.
                    logger.info(
                        'realtime_native_transfer_match target_id=%s direction=%s tx_hash=%s '
                        'from=%s to=%s value=%s block_number=%s detected_by=%s',
                        target.get('id'), direction, tx_hash,
                        str(tx.get('from') or '').lower() or 'none',
                        str(tx.get('to') or '').lower() or 'none',
                        value_wei, block_number, source_type,
                    )
                    if live_tail:
                        # Requirement A: live-tail match marker (distinct from the
                        # generic native match) so the current-block detection path is
                        # unambiguous in logs even during a concurrent gap backfill.
                        logger.info(
                            'realtime_live_tail_match target_id=%s direction=%s tx_hash=%s '
                            'block_number=%s detected_by=%s',
                            target.get('id'), direction, tx_hash, block_number, source_type,
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
                        # The tx already exists — usually because the independent 300s
                        # stable polling worker detected it first. Log WHO owns the
                        # existing row so "realtime saw it but UI says stable polling"
                        # is explained in one line, and the UI stays truthful
                        # (detected_by keeps the first detector; realtime skipped a dup).
                        _existing_by = detected_by_from_ingestion_source(
                            result.get('existing_detected_by')
                            or result.get('existing_ingestion_source')
                        )
                        logger.info(
                            'realtime_duplicate_existing_tx tx_hash=%s existing_detected_by=%s '
                            'attempted_detected_by=%s target_id=%s watcher=%s',
                            tx_hash, _existing_by, source_type, target.get('id'),
                            self.watcher_name,
                        )
                        logger.debug(
                            'realtime_event_deduped watcher=%s event_id=%s',
                            self.watcher_name, event.event_id,
                        )
                        if result_by_tx is not None and tx_hash:
                            result_by_tx[tx_hash.lower()] = {
                                'status': 'duplicate_suppressed',
                                'existing_detected_by': _existing_by,
                            }
                        continue
                    if result.get('status') != 'persist_failed':
                        if result_by_tx is not None and tx_hash:
                            result_by_tx[tx_hash.lower()] = {
                                'status': 'processed', 'detected_by': source_type,
                            }
                        processed += 1
                        self.state['metrics']['events_ingested'] += 1
                        self.state['last_event_at'] = datetime.now(timezone.utc).isoformat()
                        logger.info(
                            'wallet_transfer_detected tx_hash=%s detected_by=%s',
                            tx_hash, source_type,
                        )
                        # Canonical persisted marker: names the customer-facing event
                        # class (wallet_transfer_detected) and the detected_by/source_type
                        # tag so the realtime path is unambiguous in logs.
                        logger.info(
                            'realtime_event_persisted event_type=wallet_transfer_detected '
                            'tx_hash=%s detected_by=%s source_type=%s',
                            tx_hash, source_type, source_type,
                        )
                        if live_tail:
                            # Requirement A: live-tail persisted marker — proves a
                            # current-block transfer reached telemetry via the live-tail
                            # path within one/two newHeads, not via a later gap backfill
                            # or the 300s stable poller.
                            logger.info(
                                'realtime_live_tail_persisted event_type=wallet_transfer_detected '
                                'tx_hash=%s block_number=%s detected_by=%s source_type=%s',
                                tx_hash, block_number, source_type, source_type,
                            )
                        increment('decoda_realtime_events_total', chain=self.chain_network)
                if not matched_any and tx_hash:
                    logger.debug(
                        'native_transfer_no_match tx_hash=%s reason=address_not_watched',
                        tx_hash,
                    )
        # Every block in the range was fetched and inspected — record the span so
        # the tx-hash diagnosis can answer was_block_scanned truthfully (a raised
        # RPC error above never reaches this line, so partial scans are not
        # recorded — fail-closed).
        self._note_scanned_range(int(from_block), int(to_block))
        # Completion marker with the counts an operator needs to explain why a scan
        # produced no telemetry: txs_seen=0 → the block came back with no full
        # transactions (e.g. a hash-only eth_getBlockByNumber response); txs_seen>0 with
        # matches=0 → transactions were inspected but none touched a watched wallet.
        # Without this line, "scan_started" followed by silence was undiagnosable.
        logger.info(
            'realtime_native_transfer_scan_complete chain_id=%s from_block=%s to_block=%s '
            'blocks_scanned=%s txs_seen=%s watched_targets=%s matches=%s detected_by=%s watcher=%s',
            self.chain_id, from_block, to_block, blocks_scanned, txs_seen, len(watched),
            matches, source_type, self.watcher_name,
        )
        if live_tail:
            # Requirement A: live-tail completion marker carrying the same counts as
            # the generic scan_complete so a fast-tail/backfill scan is never mistaken
            # for the live-tail path. txs_seen>0 with matches=0 explains a quiet scan.
            logger.info(
                'realtime_live_tail_scan_complete chain_id=%s from_block=%s to_block=%s '
                'blocks_scanned=%s txs_seen=%s matches=%s detected_by=%s watcher=%s',
                self.chain_id, from_block, to_block, blocks_scanned, txs_seen, matches,
                source_type, self.watcher_name,
            )
        if _is_fast_tail:
            # Requirement 6: canonical fast-tail native-scan completion marker carrying
            # blocks_scanned / txs_seen / matches so an operator can confirm the fast-tail
            # native path actually inspected transactions (txs_seen>0) even when it
            # produced no matches.
            logger.info(
                'realtime_fast_tail_native_scan_complete chain_id=%s from_block=%s to_block=%s '
                'blocks_scanned=%s txs_seen=%s matches=%s watcher=%s',
                self.chain_id, from_block, to_block, blocks_scanned, txs_seen, matches,
                self.watcher_name,
            )
        return processed

    def _watched_wallet_pairs(self, *, log_summary: bool = False) -> list[tuple[dict[str, Any], str]]:
        """Load active Base targets as ``(target, lowercase_0x_address)`` pairs.

        Shared by the gap backfill, the new-head native scan, the realtime logs
        subscription, and the HTTP fast-tail so every path matches the watched
        wallet against the SAME normalised (lowercase) address resolved by
        :meth:`_resolve_target_address` (which reuses stable polling's
        ``resolve_monitored_wallet``). A target with no resolvable monitored
        address is excluded from matching and marks target loading degraded
        (requirement 3). When ``log_summary`` is True (startup / fast-tail start),
        emits the canonical ``realtime_targets_loaded count=<n> address_count=<n>``
        line and a per-target ``realtime_target_address_missing`` warning; the
        hot per-head path leaves ``log_summary`` False to avoid log spam.
        """
        targets = self._watched_targets()
        pairs: list[tuple[dict[str, Any], str]] = []
        missing = 0
        for target in targets:
            addr = (self._resolve_target_address(target) or '')
            if addr.startswith('0x'):
                pairs.append((target, addr))
            else:
                missing += 1
                if log_summary:
                    logger.warning(
                        'realtime_target_address_missing target_id=%s workspace_id=%s '
                        'chain_id=%s reason=missing_monitored_address watcher=%s',
                        target.get('id'), target.get('workspace_id'), self.chain_id,
                        self.watcher_name,
                    )
        self._note_target_loading(loaded=len(targets), with_address=len(pairs), missing=missing)
        if log_summary:
            _workspace_count = len({str(t.get('workspace_id')) for t in targets})
            logger.info(
                'realtime_targets_loaded count=%s address_count=%s chain_id=%s '
                'workspace_count=%s degraded=%s degraded_reason=%s watcher=%s',
                len(targets), len(pairs), self.chain_id, _workspace_count,
                self._target_loading_degraded,
                self._target_loading_degraded_reason or 'none',
                self.watcher_name,
            )
        return pairs

    async def _scan_head_native_transfers(self, head: int) -> int:
        """Scan newly confirmed head block(s) directly for native ETH transfers.

        This is the LIVE-TAIL path (requirement A): it runs on EVERY ``newHeads``
        message via :meth:`_handle_new_head`, independent of any gap backfill. Native
        ETH transfers carry NO logs, so the ``logs`` subscription can never see them —
        only a full-transaction ``eth_getBlockByNumber`` scan of the block can
        (requirement B). Without this, a plain ETH send to/from a watched wallet was
        invisible to the realtime worker until the 300 s stable polling worker caught
        it minutes later.

        Detections here are tagged ``detected_by=realtime_websocket`` (requirement A:
        live-tail scan), distinct from the gap backfill's ``realtime_backfill``.

        Confirmation-safe: only blocks at or below ``head - confirmations_required``
        are scanned. The scan is bounded to the newest ``backfill_chunk_size`` blocks
        (requirement C) and the checkpoint advances to the last scanned block so lag
        collapses to ~0-2 even after a large gap; on RPC failure the checkpoint is NOT
        advanced so the range is retried on the next head (no transfers skipped).
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
        # must not trigger an unbounded block-by-block fetch on the event loop. The
        # window reaches at least ``gap_threshold_blocks`` back so anything the gap
        # backfill would NOT pick up (lag <= threshold) is always covered by live-tail
        # — and it stays independent of ``backfill_chunk_size`` so a 413-driven chunk
        # shrink on the eth_getLogs backfill never narrows the live-tail's reach.
        _live_tail_window = max(self.backfill_chunk_size, self.gap_threshold_blocks)
        from_block = max(from_block, safe_to - _live_tail_window + 1)

        watched = self._watched_wallet_pairs()
        if not watched:
            # No Base wallet targets — advance so an empty range is not re-scanned.
            self.state['last_processed_block'] = max(int(last or 0), safe_to)
            return 0

        try:
            processed = self._scan_native_transfers(
                from_block, safe_to, watched,
                source_type=REALTIME_INGESTION_SOURCE, live_tail=True,
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

    async def _handle_new_head(self, head: int) -> None:
        """Dispatch a ``newHeads`` block: live-tail first, gap backfill separately.

        Requirement A — the live-tail scan of the newest confirmed block(s) ALWAYS
        runs first and is fully independent of the gap backfill: a failing, paused,
        or 413-ing backfill can NEVER block detection of current blocks. Requirement
        C — single-flight + coalescing: at most one block scan runs at a time, and
        newHeads that arrive while a scan is in flight are coalesced so only the
        latest head is scanned (a burst of heads never fans out into one RPC-heavy
        scan per head, which is what pushed the provider into HTTP 429).
        """
        self._coalesced_head = (
            head if self._coalesced_head is None else max(int(self._coalesced_head), int(head))
        )
        if self._head_scan_in_flight:
            # A scan is already active (requirement C: max one active block scan). It
            # will pick up self._coalesced_head when it finishes — never start a second
            # concurrent scan nor one scan per buffered head.
            return
        self._head_scan_in_flight = True
        try:
            while self._coalesced_head is not None:
                current_head = int(self._coalesced_head)
                self._coalesced_head = None

                # Checkpoint BEFORE the live-tail scan advances it — used only to size
                # the separate historical-gap backfill below.
                last_before = self.state.get('last_processed_block')

                # LIVE-TAIL (always runs): scan the newest confirmed block(s) for
                # native ETH transfers. detected_by=realtime_websocket. Its own
                # try/except means an RPC error here never propagates to the backfill
                # step, and vice-versa — the two paths are independent.
                await self._scan_head_native_transfers(current_head)

                # GAP BACKFILL (separate, best-effort): when a real historical gap
                # remains below the live-tail window, close ONE bounded chunk of the
                # older skipped range (ERC20 logs + native). Skipped while paused; a
                # failure here never affects the live-tail scan above. The independent
                # 300 s stable polling worker covers any deeper span (requirement D).
                if (
                    last_before is not None
                    and current_head - int(last_before) > self.gap_threshold_blocks
                    and not self._backfill_paused()
                ):
                    logger.warning(
                        'realtime_gap_detected chain=%s from_block=%s to_block=%s '
                        'lag_blocks=%s bounded_chunk=%s live_tail_scanned=True',
                        self.chain_network, int(last_before) + 1, current_head,
                        current_head - int(last_before), self.backfill_chunk_size,
                    )
                    # Defensive isolation (requirement A): a backfill failure must
                    # never propagate to break the live-tail above or the WSS session.
                    # ``_backfill`` handles rate-limit/413 internally, but any
                    # unexpected error here is logged and swallowed so current-block
                    # detection is never blocked by a historical-gap problem.
                    try:
                        await self._backfill(int(last_before) + 1, current_head)
                    except Exception as bf_exc:
                        logger.warning(
                            'realtime_gap_backfill_failed_isolated from_block=%s to_block=%s '
                            'watcher=%s error=%s live_tail_unaffected=True',
                            int(last_before) + 1, current_head, self.watcher_name,
                            str(bf_exc)[:200],
                        )
        finally:
            self._head_scan_in_flight = False

    def _log_target_diagnostics(self, targets: list[dict[str, Any]]) -> None:
        """Emit one full-address diagnostic line per watched target.

        Addresses are NOT secrets — operators need the exact monitored address to
        confirm a MetaMask wallet matches what Decoda watches. Truncated forms like
        ``0x5f6f…1d1f`` hide the very mismatch this is meant to catch.
        """
        for target in targets:
            # Resolve via the shared stable-polling resolver so a monitored address
            # stored in a fallback location (asset identifier / target_metadata) is
            # surfaced here instead of logging monitored_address_full=none.
            raw_addr = self._resolve_target_address(target) or ''
            logger.info(
                'realtime_target_diagnostics target_id=%s workspace_id=%s chain_id=%s '
                'monitored_address_full=%s normalized_address_lowercase=%s watcher=%s',
                target.get('id'), target.get('workspace_id'), self.chain_id,
                raw_addr or 'none', raw_addr.lower() or 'none', self.watcher_name,
            )

    # ------------------------------------------------------------------
    # tx-hash debug mode (requirements 1-2)
    # ------------------------------------------------------------------

    def _debug_tx_match(
        self,
        tx_hash: str,
        watched: list[tuple[dict[str, Any], str]],
        *,
        run_backfill: bool = True,
    ) -> dict[str, Any]:
        """Fetch one tx by hash and log a full match diagnostic (+ bounded backfill).

        Requirement 1: ``eth_getTransactionByHash`` + ``eth_getTransactionReceipt`` →
        one ``realtime_tx_debug`` line per watched target carrying
        block_number/from/to/value/status/chain_id, per-target ``from_matches``/
        ``to_matches``, the normalized from/to/target addresses, the live-tail window
        (``live_tail_from_block``/``live_tail_to_block``), the span-truthful
        ``was_block_scanned``, and ``provider_mode_at_time``/``rate_limited_at_time``.
        The match is computed by the canonical :func:`explain_wallet_transfer_match`
        helper (same normalisation the live scan uses) so the debug view can never
        disagree with the real matcher.

        Requirement 2: when the tx's block was never actually scanned it logs
        ``realtime_tx_not_in_scanned_window`` (and, below the checkpoint, the legacy
        ``realtime_tx_skipped_by_checkpoint``) and — unless ``run_backfill`` is False,
        the provider was rate-limited when the tx landed (requirement 5: stable
        polling remains the fallback), or a row already exists — runs a bounded
        ``tx_block-2 .. tx_block+2`` native scan persisting
        ``detected_by=realtime_backfill``. The forward checkpoint is deliberately
        NOT advanced: a backfill of an older block must never move the live cursor.

        Requirement 4: when the tx already exists from the stable polling worker the
        debug logs ``realtime_duplicate_existing_tx existing_detected_by=...`` and
        skips the import — the customer-facing detected_by stays truthful.

        Requirement 5: when the tx landed inside a provider rate-limit cooldown
        window and realtime missed it, logs ``realtime_tx_missed_due_to_rate_limit``
        with the cooldown's next_retry_at.

        Acceptance: ends with exactly one ``realtime_tx_verdict`` line (see
        :func:`worker_status.classify_realtime_tx_verdict`).

        Read-only apart from the bounded backfill's own idempotent event
        persistence, so it is safe to run on startup for an operator-supplied tx
        hash without sending more ETH.
        """
        try:
            tx = self._rpc_call('eth_getTransactionByHash', [tx_hash])
        except Exception as exc:
            logger.warning(
                'realtime_tx_debug_failed tx_hash=%s error=%s watcher=%s',
                tx_hash, str(exc)[:200], self.watcher_name,
            )
            return {'tx_hash': tx_hash, 'found': False, 'error': str(exc)[:200]}

        tx = tx if isinstance(tx, dict) else {}
        if not tx:
            logger.warning(
                'realtime_tx_debug tx_hash=%s found=False reason=transaction_not_found watcher=%s',
                tx_hash, self.watcher_name,
            )
            return {'tx_hash': tx_hash, 'found': False}

        block_number = _hex_to_int(tx.get('blockNumber'))
        value_wei = _hex_to_int(tx.get('value')) or 0
        chain_id = _hex_to_int(tx.get('chainId'))
        raw_from = str(tx.get('from') or '') or 'none'
        raw_to = str(tx.get('to') or '') or 'none'

        # Requirement 1: also fetch the receipt so the debug can report the on-chain
        # execution status (1=success, 0=reverted, None=pending/unknown). A reverted
        # send explains "no telemetry" without any Decoda-side bug. Best-effort — a
        # receipt RPC failure must never abort the diagnostic.
        try:
            receipt = self._rpc_call('eth_getTransactionReceipt', [tx_hash])
        except Exception:
            receipt = None
        receipt = receipt if isinstance(receipt, dict) else {}
        tx_status = _hex_to_int(receipt.get('status'))

        # Requirement 1: checkpoint + scan-window context. was_block_scanned is now
        # SPAN-truthful: it is True only when this process actually native-scanned
        # the tx's block (_note_scanned_range records every completed scan range).
        # The old [scan_start_block, checkpoint] inference over-claimed after a
        # rate-limit cooldown, when the live-tail fast-forwards the checkpoint past
        # blocks it never scanned — the production "tx exists but matches=0" case.
        checkpoint_block = self.state.get('last_processed_block')
        scan_start_block = self.state.get('scan_start_block')
        was_block_scanned = self._was_block_scanned(block_number)
        scanned_from, scanned_to = self._scanned_window_bounds()
        live_tail_from = self.state.get('live_tail_from_block')
        live_tail_to = self.state.get('live_tail_to_block')

        # Requirement 5: was the provider rate-limited when this tx landed? Answered
        # from the recorded cooldown windows against the tx block's on-chain
        # timestamp. The block header is fetched lazily — only when a cooldown was
        # ever recorded — so the debug adds no RPC cost in the healthy case.
        # rate_limited_at_time is True / False / 'unknown' (header unavailable):
        # never a claim the worker cannot prove.
        rate_limited_at_time: Any = False
        rate_limit_next_retry_at: str | None = None
        if self._rate_limit_windows and block_number is not None:
            rate_limited_at_time = 'unknown'
            try:
                header = self._rpc_call('eth_getBlockByNumber', [hex(int(block_number)), False])
            except Exception:
                header = None
            header = header if isinstance(header, dict) else {}
            ts_int = _hex_to_int(header.get('timestamp'))
            if ts_int is not None:
                tx_time = datetime.fromtimestamp(ts_int, tz=timezone.utc)
                window = self._rate_limit_window_covering(tx_time)
                rate_limited_at_time = window is not None
                if window is not None:
                    rate_limit_next_retry_at = window.get('next_retry_at')

        if rate_limited_at_time is True:
            provider_mode_at_time = 'rate_limited'
        elif was_block_scanned:
            provider_mode_at_time = (
                self.state.get('source_status') or self._ingestion_mode or 'realtime_websocket'
            )
        else:
            provider_mode_at_time = 'unknown'

        matched: list[dict[str, Any]] = []
        for target, addr in watched:
            explanation = explain_wallet_transfer_match(addr, tx)
            norm_target = explanation.get('monitored_wallet') or (addr or '').lower() or 'none'
            norm_from = explanation.get('tx_from') or 'none'
            norm_to = explanation.get('tx_to') or 'none'
            from_matches = norm_from != 'none' and norm_from == norm_target
            to_matches = norm_to != 'none' and norm_to == norm_target
            logger.info(
                'realtime_tx_debug tx_hash=%s block_number=%s from=%s to=%s value=%s status=%s '
                'chain_id=%s monitored_address=%s from_matches=%s to_matches=%s '
                'normalized_from=%s normalized_to=%s normalized_target=%s '
                'live_tail_from_block=%s live_tail_to_block=%s '
                'checkpoint_block=%s scan_start_block=%s was_block_scanned=%s '
                'provider_mode_at_time=%s rate_limited_at_time=%s '
                'matched=%s direction=%s target_id=%s watcher=%s',
                tx_hash, block_number if block_number is not None else 'none',
                raw_from, raw_to, value_wei,
                tx_status if tx_status is not None else 'none',
                chain_id if chain_id is not None else 'none',
                addr, from_matches, to_matches,
                norm_from, norm_to, norm_target,
                live_tail_from if live_tail_from is not None else 'none',
                live_tail_to if live_tail_to is not None else 'none',
                checkpoint_block if checkpoint_block is not None else 'none',
                scan_start_block if scan_start_block is not None else 'none',
                was_block_scanned,
                provider_mode_at_time,
                rate_limited_at_time,
                bool(explanation.get('matched')),
                explanation.get('wallet_transfer_direction') or 'none',
                target.get('id'), self.watcher_name,
            )
            if explanation.get('matched'):
                matched.append({'target_id': target.get('id'),
                                'direction': explanation.get('wallet_transfer_direction')})

        result: dict[str, Any] = {
            'tx_hash': tx_hash,
            'found': True,
            'block_number': block_number,
            'from': (raw_from.lower() if raw_from != 'none' else None),
            'to': (raw_to.lower() if raw_to != 'none' else None),
            'value_wei': value_wei,
            'chain_id': chain_id,
            'status': tx_status,
            'checkpoint_block': int(checkpoint_block) if checkpoint_block is not None else None,
            'scan_start_block': int(scan_start_block) if scan_start_block is not None else None,
            'was_block_scanned': was_block_scanned,
            'live_tail_from_block': live_tail_from,
            'live_tail_to_block': live_tail_to,
            'provider_mode_at_time': provider_mode_at_time,
            'rate_limited_at_time': rate_limited_at_time,
            'matched_target_count': len(matched),
            'skipped_by_checkpoint': False,
            'backfill_triggered': False,
        }

        # WHO already has this tx (if anyone). Realtime rows keep their tag; a row
        # from the 300 s stable polling worker means realtime must SKIP the import
        # (requirement 4) so the customer-facing detected_by stays truthful.
        existing_detected_by = (
            self._existing_telemetry_detected_by(tx_hash, watched) if matched else None
        )
        result['existing_detected_by'] = existing_detected_by
        if matched and existing_detected_by:
            logger.info(
                'realtime_duplicate_existing_tx tx_hash=%s existing_detected_by=%s '
                'attempted_detected_by=realtime_tx_debug watcher=%s',
                tx_hash, existing_detected_by, self.watcher_name,
            )

        # Requirement 5: the tx landed inside a provider rate-limit cooldown and no
        # realtime path scanned its block — realtime missed it because scanning was
        # paused. The independent stable polling worker remains the fallback, so no
        # bounded import fires here (a stable row will/should appear; if it already
        # has, the duplicate branch above owns the verdict).
        if (
            matched and existing_detected_by is None
            and rate_limited_at_time is True and not was_block_scanned
        ):
            logger.warning(
                'realtime_tx_missed_due_to_rate_limit tx_hash=%s block_number=%s '
                'next_retry_at=%s watcher=%s',
                tx_hash, block_number if block_number is not None else 'none',
                rate_limit_next_retry_at or 'none', self.watcher_name,
            )

        # Requirement 2: canonical marker naming the exact scanned window whenever
        # the tx block was never actually scanned and nothing was persisted for it.
        if (
            block_number is not None and not was_block_scanned
            and existing_detected_by is None
        ):
            logger.warning(
                'realtime_tx_not_in_scanned_window tx_hash=%s tx_block=%s '
                'scanned_from=%s scanned_to=%s watcher=%s',
                tx_hash, block_number,
                scanned_from if scanned_from is not None else 'none',
                scanned_to if scanned_to is not None else 'none',
                self.watcher_name,
            )

        checkpoint = self.state.get('last_processed_block')
        below_checkpoint = bool(
            block_number is not None and checkpoint is not None
            and block_number <= int(checkpoint)
        )
        if was_block_scanned and matched and existing_detected_by is None:
            # The block WAS scanned and the tx matches a watched wallet, yet no row
            # exists — a matching/persistence defect, surfaced loudly (requirement 3)
            # before the recovery import below makes the customer whole.
            logger.error(
                'realtime_tx_scanned_but_not_persisted tx_hash=%s tx_block=%s '
                'matched_target_count=%s action=recovery_import watcher=%s',
                tx_hash, block_number, len(matched), self.watcher_name,
            )
        imported_by: str | None = None
        if below_checkpoint:
            logger.warning(
                'realtime_tx_skipped_by_checkpoint tx_hash=%s tx_block=%s checkpoint_block=%s watcher=%s',
                tx_hash, block_number, int(checkpoint), self.watcher_name,
            )
            result['skipped_by_checkpoint'] = True
            # Bounded recovery import. Skipped when a row already exists (dedupe
            # would no-op; requirement 4 keeps the stable row authoritative) and
            # when the miss is rate-limit-explained (requirement 5: stable polling
            # remains the fallback).
            if (
                run_backfill and watched
                and existing_detected_by is None
                and rate_limited_at_time is not True
            ):
                _from = max(0, int(block_number) - 2)
                _to = int(block_number) + 2
                logger.info(
                    'realtime_bounded_backfill_started tx_hash=%s from_block=%s to_block=%s watcher=%s',
                    tx_hash, _from, _to, self.watcher_name,
                )
                try:
                    # Bounded ±2-block native scan tagged realtime_backfill (NOT
                    # realtime_websocket): this is a recovery scan of an OLD block below
                    # the live forward cursor, so it must not claim the WebSocket
                    # delivered it. It persists the missed transfer but does NOT touch
                    # last_processed_block — the live forward cursor is unchanged.
                    _backfill_results: dict[str, dict[str, Any]] = {}
                    self._scan_native_transfers(
                        _from, _to, watched, source_type='realtime_backfill',
                        result_by_tx=_backfill_results,
                    )
                    result['backfill_triggered'] = True
                    result['backfill_from_block'] = _from
                    result['backfill_to_block'] = _to
                    _tx_outcome = _backfill_results.get(tx_hash.lower()) or {}
                    if _tx_outcome.get('status') == 'processed':
                        imported_by = str(_tx_outcome.get('detected_by') or 'realtime_backfill')
                    elif _tx_outcome.get('status') == 'duplicate_suppressed':
                        # A row appeared between the check above and the scan —
                        # report its true owner instead of claiming an import.
                        existing_detected_by = str(
                            _tx_outcome.get('existing_detected_by') or 'unknown'
                        )
                        result['existing_detected_by'] = existing_detected_by
                except Exception as exc:
                    logger.warning(
                        'realtime_bounded_backfill_failed tx_hash=%s from_block=%s to_block=%s '
                        'error=%s watcher=%s',
                        tx_hash, _from, _to, str(exc)[:200], self.watcher_name,
                    )

        # Acceptance: exactly one canonical verdict for this tx hash, shared with
        # the read-only diagnose-tx endpoint via classify_realtime_tx_verdict.
        verdict = classify_realtime_tx_verdict(
            tx_found=True,
            matched=bool(matched),
            existing_detected_by=existing_detected_by,
            was_block_scanned=was_block_scanned,
            rate_limited_at_tx_time=rate_limited_at_time is True,
            below_checkpoint=below_checkpoint,
            imported_by=imported_by,
        )
        result['verdict'] = verdict
        result['imported_by'] = imported_by
        logger.info(
            'realtime_tx_verdict tx_hash=%s verdict=%s block_number=%s '
            'was_block_scanned=%s rate_limited_at_time=%s existing_detected_by=%s '
            'imported_by=%s next_retry_at=%s watcher=%s',
            tx_hash, verdict, block_number if block_number is not None else 'none',
            was_block_scanned, rate_limited_at_time, existing_detected_by or 'none',
            imported_by or 'none', rate_limit_next_retry_at or 'none', self.watcher_name,
        )
        return result

    def _existing_telemetry_detected_by(
        self,
        tx_hash: str,
        watched: list[tuple[dict[str, Any], str]],
    ) -> str | None:
        """Return the detected_by of an already-persisted telemetry row for this tx.

        Checks every watched target's workspace-scoped telemetry for the tx hash and
        returns the canonical detected_by tag of the first row found (rows persisted
        by the stable polling worker before the detected_by field existed default to
        ``stable_rpc_polling``). Best-effort: any DB failure returns ``None`` so the
        debug degrades to "no known row" — the bounded import that may follow is
        idempotent, so a wrong ``None`` can never create a duplicate.
        """
        tx_hash_norm = str(tx_hash or '').lower()
        if not tx_hash_norm:
            return None
        try:
            with pg_connection() as conn:
                ensure_pilot_schema(conn)
                for target, _addr in watched:
                    row = conn.execute(
                        '''
                        SELECT payload_json->>'detected_by' AS detected_by
                        FROM telemetry_events
                        WHERE workspace_id = %s AND target_id = %s
                          AND lower(payload_json->>'tx_hash') = %s
                        LIMIT 1
                        ''',
                        (target.get('workspace_id'), target.get('id'), tx_hash_norm),
                    ).fetchone()
                    if row is not None:
                        row = dict(row) if not isinstance(row, dict) else row
                        return detected_by_from_ingestion_source(
                            row.get('detected_by') or 'stable_rpc_polling'
                        )
        except Exception as exc:
            logger.warning(
                'realtime_tx_existing_row_check_failed tx_hash=%s error=%s watcher=%s',
                tx_hash_norm, str(exc)[:160], self.watcher_name,
            )
        return None

    def _run_configured_tx_debug(self) -> None:
        """Run tx-hash debug once for every hash in BASE_REALTIME_DEBUG_TX_HASHES.

        No-op unless the env var is set, so it is inert in normal operation. Guarded
        by ``_tx_debug_completed`` so a WSS reconnect (or fast-tail restart) does not
        re-run the diagnostic and re-trigger the bounded backfill on every cycle.
        """
        if self._tx_debug_completed or not self.debug_tx_hashes:
            return
        self._tx_debug_completed = True
        watched = self._watched_wallet_pairs()
        logger.info(
            'realtime_tx_debug_mode_active tx_hash_count=%s watched_targets=%s '
            'checkpoint_block=%s watcher=%s',
            len(self.debug_tx_hashes), len(watched),
            self.state.get('last_processed_block'), self.watcher_name,
        )
        for tx_hash in self.debug_tx_hashes:
            try:
                self._debug_tx_match(tx_hash, watched)
            except Exception as exc:
                logger.warning(
                    'realtime_tx_debug_unexpected_error tx_hash=%s error=%s watcher=%s',
                    tx_hash, str(exc)[:200], self.watcher_name,
                )

    def _backfill_tx_by_hash(
        self,
        tx_hash: str,
        watched: list[tuple[dict[str, Any], str]] | None = None,
        *,
        source_type: str = 'realtime_tx_import',
    ) -> dict[str, Any]:
        """Import a single old transaction by hash via a bounded ±2-block native scan.

        Requirement 5: recover a transfer whose block is older than the fast-tail
        catch-up window (below the forward checkpoint) without replaying a huge range.

        Steps:
          1. ``eth_getTransactionByHash`` → the tx (block number, from, to, value).
          2. ``eth_getTransactionReceipt`` → on-chain status (best-effort; a receipt
             failure never aborts the import).
          3. Scan ``tx_block-2 .. tx_block+2`` with :meth:`_scan_native_transfers`,
             matching ``tx.from`` / ``tx.to`` against every resolved monitored wallet
             and persisting ``wallet_transfer_detected`` telemetry tagged
             ``detected_by=source_type`` (default ``realtime_tx_import``).

        The forward checkpoint (``last_processed_block``) is deliberately NOT advanced —
        importing an OLD block must never move the live cursor forward and skip newer
        blocks. Idempotent: re-importing the same tx dedupes by event_id, so it is safe
        to call repeatedly. Read-only apart from that idempotent persistence.
        """
        if watched is None:
            watched = self._watched_wallet_pairs()
        try:
            tx = self._rpc_call('eth_getTransactionByHash', [tx_hash])
        except Exception as exc:
            logger.warning(
                'realtime_tx_import_failed tx_hash=%s error=%s watcher=%s',
                tx_hash, str(exc)[:200], self.watcher_name,
            )
            return {'tx_hash': tx_hash, 'found': False, 'imported': 0, 'error': str(exc)[:200]}

        tx = tx if isinstance(tx, dict) else {}
        block_number = _hex_to_int(tx.get('blockNumber'))
        if not tx or block_number is None:
            logger.warning(
                'realtime_tx_import tx_hash=%s found=False reason=transaction_not_found_or_pending '
                'watcher=%s',
                tx_hash, self.watcher_name,
            )
            return {'tx_hash': tx_hash, 'found': False, 'imported': 0}

        # Receipt is best-effort context (execution status); a failure must not abort.
        try:
            receipt = self._rpc_call('eth_getTransactionReceipt', [tx_hash])
        except Exception:
            receipt = None
        tx_status = _hex_to_int((receipt or {}).get('status')) if isinstance(receipt, dict) else None

        _from = max(0, int(block_number) - 2)
        _to = int(block_number) + 2
        _checkpoint_before = self.state.get('last_processed_block')
        logger.info(
            'realtime_tx_import_started tx_hash=%s tx_block=%s from_block=%s to_block=%s '
            'status=%s detected_by=%s watcher=%s',
            tx_hash, block_number, _from, _to,
            tx_status if tx_status is not None else 'none', source_type, self.watcher_name,
        )
        imported = 0
        try:
            imported = self._scan_native_transfers(
                _from, _to, watched, source_type=source_type,
            )
        except Exception as exc:
            logger.warning(
                'realtime_tx_import_scan_failed tx_hash=%s from_block=%s to_block=%s error=%s watcher=%s',
                tx_hash, _from, _to, str(exc)[:200], self.watcher_name,
            )
        # Fail-closed invariant: the bounded backfill must never move the forward
        # cursor (it scans OLD blocks). Restore it in case a shared helper touched it.
        self.state['last_processed_block'] = _checkpoint_before
        logger.info(
            'realtime_tx_import_complete tx_hash=%s tx_block=%s imported=%s detected_by=%s '
            'checkpoint_block=%s watcher=%s',
            tx_hash, block_number, imported, source_type,
            _checkpoint_before if _checkpoint_before is not None else 'none', self.watcher_name,
        )
        return {
            'tx_hash': tx_hash,
            'found': True,
            'block_number': block_number,
            'status': tx_status,
            'imported': imported,
            'detected_by': source_type,
            'backfill_from_block': _from,
            'backfill_to_block': _to,
        }

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
        # During a provider rate-limit cooldown the canonical operating mode is
        # 'rate_limited' (requirements 1, 2, 4). This is deliberately distinct from
        # degraded_reason ('provider_rate_limited') and from the persisted
        # source_status column, which stays 'provider_rate_limited' so the System
        # Health detector keeps classifying the worker as rate-limited. Once the WSS
        # is permanently disabled and HTTP fast-tail took over (fast_tail_enabled),
        # the mode reflects the fast-tail source_status instead of 'rate_limited'.
        if self._provider_rate_limited and not self._wss_permanently_disabled:
            _provider_mode = 'rate_limited'
        else:
            _provider_mode = self.state.get('source_status') or self._ingestion_mode
        _fallback_active = self._fallback_is_active()
        # realtime_scanning_active is the canonical fact for requirement D: is ANY
        # realtime detection path (WSS live-tail or HTTP fast-tail) actually able to
        # scan right now? It is False during a provider rate-limit cooldown with no
        # fast-tail — the ONE case where fallback_active=False would otherwise be
        # ambiguous. When it is False the independent 300 s stable RPC polling worker
        # is the detection path; the runtime-status layer (worker_status.py) derives
        # "Realtime paused; stable polling active" from the persisted rate_limited /
        # next_retry_at facts. This flag never claims stable polling is alive itself
        # (that is the stable worker's own heartbeat fact), only that realtime is not.
        _realtime_scanning_active = not (
            self._provider_rate_limited and not self._fallback_is_active()
        )
        # Resolve before reading state so the log line and the persisted row agree.
        _degraded_reason = self._effective_degraded_reason()
        logger.info(
            'realtime_worker_heartbeat watcher_name=%s chain_id=%s chain=%s '
            'last_event_at=%s reconnect_count=%s events_processed=%s '
            'heads_received=%s lag_blocks=%s degraded=%s degraded_reason=%s '
            'active_provider_host=%s provider_mode=%s fallback_active=%s '
            'realtime_scanning_active=%s next_retry_at=%s',
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
            _realtime_scanning_active,
            self._rate_limit_retry_at or 'none',
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
                        json.dumps({
                            **self.state['metrics'],
                            'lag_blocks': lag,
                            'active_provider_host': _active_host,
                            'provider_mode': _provider_mode,
                            'rate_limited': self._provider_rate_limited,
                            'next_retry_at': self._rate_limit_retry_at,
                            # Requirement D: whether any realtime detection path can
                            # scan right now. False during a rate-limit cooldown with no
                            # fast-tail — the runtime-status layer then surfaces
                            # "Realtime paused; stable polling active" from these facts.
                            'realtime_scanning_active': _realtime_scanning_active,
                            'fallback_active': _fallback_active,
                            # Cold-start floor so read-only diagnostics (diagnose-tx)
                            # can tell whether a tx block was ever forward-scanned.
                            'scan_start_block': self.state.get('scan_start_block'),
                            # Span-truthful scan coverage + rate-limit cooldown history +
                            # most recent live-tail window, so the read-only diagnose-tx
                            # endpoint answers was_block_scanned / rate_limited_at_time
                            # from the same facts the worker's tx debug uses.
                            'scanned_spans': [list(s) for s in self._scanned_spans],
                            'rate_limit_windows': list(self._rate_limit_windows),
                            'live_tail_from_block': self.state.get('live_tail_from_block'),
                            'live_tail_to_block': self.state.get('live_tail_to_block'),
                        }),
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

    @staticmethod
    def _is_payload_too_large_error(exc: Exception) -> bool:
        """True when an RPC error indicates HTTP 413 (payload / response too large).

        ``_rpc_call`` raises ``rpc_http_error:413 method=...`` for an HTTP 413; some
        providers also phrase it as "payload too large" / "request entity too large".
        This is a PERMANENT condition for the current eth_getLogs workload (unlike a
        transient 429), so the fast-tail disables the log scan instead of retrying it
        forever (requirement 3).
        """
        s = str(exc).lower()
        return (
            'rpc_http_error:413' in s
            or ':413' in s
            or 'payload too large' in s
            or 'request entity too large' in s
        )

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

    def _note_backfill_413(self, from_block: int, to_block: int) -> None:
        """Handle an eth_getLogs HTTP 413 in the gap backfill (requirement E).

        A 413 means the requested log range/response is too large. Rather than
        failing the whole chunk and re-attempting the SAME failing from_block on the
        next head (the production ``realtime_backfill_scan_failed error=413`` loop),
        the backfill:

        * halves ``backfill_chunk_size`` (floored at 1) so subsequent chunks are
          smaller and more likely to fit, and
        * disables the eth_getLogs scan once the chunk can shrink no further — native
          ETH detection never depends on eth_getLogs (requirement B), so the native
          full-transaction scan still runs and the checkpoint still advances after it.

        Never retries the same failing chunk: the caller continues to the native scan
        and advances the cursor past ``to_block`` after it verifies.
        """
        old_chunk = self.backfill_chunk_size
        self.backfill_chunk_size = max(1, self.backfill_chunk_size // 2)
        if self.backfill_chunk_size <= 1:
            # Cannot shrink further — the log scan will 413 forever for this workload.
            self._backfill_log_scan_disabled = True
        self.state['metrics']['backfill_payload_too_large'] = (
            self.state['metrics'].get('backfill_payload_too_large', 0) + 1
        )
        logger.warning(
            'realtime_backfill_payload_too_large method=eth_getLogs from_block=%s to_block=%s '
            'old_chunk=%s new_chunk=%s log_scan_disabled=%s '
            'action=chunk_reduced_native_scan_continues watcher=%s',
            from_block, to_block, old_chunk, self.backfill_chunk_size,
            self._backfill_log_scan_disabled, self.watcher_name,
        )

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
        - On an eth_getLogs HTTP 413 (requirement E) the ERC20 log scan is shrunk /
          disabled but the native full-transaction scan STILL runs and the checkpoint
          STILL advances after it — native ETH detection never depends on eth_getLogs,
          and the same 413 chunk is never retried forever.
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
            # ERC20/contract log scan (OPTIONAL). eth_getLogs can return HTTP 413 for a
            # wide range/response; native ETH detection NEVER depends on it, so a 413
            # here shrinks the chunk, may disable the log scan, and we CONTINUE to the
            # native scan below (requirement E) — never failing the whole chunk nor
            # re-attempting the same failing from_block forever.
            if not self._backfill_log_scan_disabled:
                for target, addr in watched:
                    try:
                        logs = self._rpc_call(
                            'eth_getLogs',
                            [{'fromBlock': hex(int(from_block)), 'toBlock': hex(end),
                              'topics': [[TRANSFER_TOPIC, APPROVAL_TOPIC]]}],
                        ) or []
                    except Exception as log_exc:
                        if self._is_payload_too_large_error(log_exc):
                            # 413: reduce chunk + (maybe) disable log scan, then break
                            # out of the ERC20 loop so the native scan still runs.
                            self._note_backfill_413(int(from_block), int(end))
                            break
                        # Rate-limit / other errors propagate to the outer handler
                        # (pause + do-not-advance), same as before.
                        raise
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
            # never see them — and it runs even when the log scan was 413-disabled.
            # Scan the same block range's full transactions (eth_getBlockByNumber) so a
            # plain ETH send to/from a watched wallet is detected here instead of only
            # by the 300 s polling worker minutes later.
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
                # Resolve each target's monitored address (shared stable-polling
                # resolver) and emit realtime_targets_loaded count/address_count plus a
                # per-target realtime_target_address_missing warning + degraded flag.
                _startup_pairs = self._watched_wallet_pairs(log_summary=True)
                if _target_count == 0:
                    logger.warning(
                        'realtime_no_targets_loaded chain_id=%s chain_network=%s watcher=%s '
                        'worker_healthy_but_no_events_will_be_processed',
                        self.chain_id, self.chain_network, self.watcher_name,
                    )
                elif not _startup_pairs:
                    logger.warning(
                        'realtime_no_target_addresses_resolved chain_id=%s chain_network=%s '
                        'watcher=%s reason=missing_monitored_address',
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

            # Optional tx-hash debug + below-checkpoint backfill (requirements 1-2).
            # Runs once when BASE_REALTIME_DEBUG_TX_HASHES is set; inert otherwise.
            self._run_configured_tx_debug()

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
                    # Track chain head to enforce confirmation safety and count block
                    # activity. The head block number comes straight from the newHeads
                    # message — eth_blockNumber is never called on this path
                    # (requirement C: avoid RPC spam that trips the provider 429).
                    head = _hex_to_int(result.get('number'))
                    if head is not None:
                        self.state['last_head_block'] = head
                        self._last_head_block_at = time.monotonic()
                        self.state['metrics']['heads_received'] = (
                            self.state['metrics'].get('heads_received', 0) + 1
                        )
                        self.state['last_event_at'] = datetime.now(timezone.utc).isoformat()
                        # Live-tail always runs; gap backfill is separate and never
                        # blocks it (requirement A). Single-flight + coalescing keeps at
                        # most one block scan active (requirement C).
                        await self._handle_new_head(head)

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

                    # Match against the SAME resolved monitored addresses the native
                    # scan uses; a target with no resolvable address is excluded.
                    for target, watched in self._watched_wallet_pairs():
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

        Polls QuickNode HTTPS RPC (self.rpc_url, never the WSS) every
        ``fast_tail_interval_seconds`` (default 60 s), scanning at most
        ``fast_tail_chunk_size`` of the most-recent blocks for both ERC20 Transfer/
        Approval logs and native ETH transactions for active Base watched targets.
        Detections are tagged ``detected_by=quicknode_http_fast_tail`` and flow
        through the same process_ingested_event path, so deduplication with the
        stable 300 s polling worker is automatic.

        Cursor is only advanced when all scans succeed — a failed or rate-limited
        scan retries the same block range on the next poll so no events are missed.
        """
        self._ingestion_mode = 'http_fast_tail'
        self.state['source_status'] = 'quicknode_http_fast_tail'
        self.state['degraded'] = True
        # Preserve a reconnect-loop reason set by the breaker; otherwise default to
        # the before-first-event reason. Both are rewritten to http_fast_tail_active
        # by _effective_degraded_reason once the fast-tail poll starts fetching heads.
        if self.state.get('degraded_reason') != 'provider_1001_reconnect_loop':
            self.state['degraded_reason'] = 'provider_closes_before_first_event'

        _poll_interval = float(self.fast_tail_interval_seconds)

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

        # Optional tx-hash debug + below-checkpoint backfill (requirements 1-2).
        # Runs once when BASE_REALTIME_DEBUG_TX_HASHES is set; inert otherwise. Also
        # runs here so an operator can debug via the HTTP fast-tail path when the WSS
        # is disabled.
        self._run_configured_tx_debug()

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

                # Requirement 4: never auto-catch-up a huge gap. When the checkpoint is
                # more than max_catchup_blocks behind head, replaying the middle would
                # burn QuickNode quota (the 40k-block production lag explosion). Log it,
                # fast-forward the cursor to a bounded window behind head so lag stops
                # growing, and leave the skipped span to tx-hash import / bounded
                # backfill and the independent 300 s stable poller.
                lag_blocks = head_num - last
                if lag_blocks > self.fast_tail_max_catchup_blocks:
                    logger.warning(
                        'realtime_fast_tail_lag_too_large lag_blocks=%s max_catchup_blocks=%s watcher=%s',
                        lag_blocks, self.fast_tail_max_catchup_blocks, self.watcher_name,
                    )
                    last = head_num - self.fast_tail_max_catchup_blocks

                to_block = head_num
                from_block = last + 1
                # Bound to the most-recent chunk so a stale checkpoint (or a provider
                # that jumps the head far ahead) never triggers a huge historical scan
                # in one poll. The independent 300 s stable polling worker closes any
                # deeper gap; overlapping blocks dedupe against it by event_id.
                if to_block - from_block + 1 > self.fast_tail_chunk_size:
                    from_block = to_block - self.fast_tail_chunk_size + 1

                logger.info(
                    'realtime_http_fast_tail_scan watcher=%s from_block=%s to_block=%s chunk_size=%s',
                    self.watcher_name, from_block, to_block, self.fast_tail_chunk_size,
                )

                # Resolve monitored addresses via the shared stable-polling resolver;
                # targets with no resolvable address are excluded (fail-closed).
                watched_pairs = self._watched_wallet_pairs()
                # scan_all_ok gates the checkpoint advance. It is cleared ONLY by a
                # RETRYABLE failure (rate-limit or unexpected error). A 413 on the
                # optional eth_getLogs scan is a graceful skip that must NOT hold the
                # checkpoint back — otherwise lag grows forever (the production bug).
                scan_all_ok = True

                # Requirement 1-2: the native ETH transaction scan is the PRIMARY
                # fast-tail detection path and ALWAYS runs first. Native ETH sends emit
                # NO logs, so only a full-transaction (eth_getBlockByNumber) scan can see
                # them; this never depends on eth_getLogs succeeding. Bounded to the most
                # recent chunk so a stale checkpoint can never trigger a giant
                # block-by-block fetch in one poll.
                _native_from = max(int(from_block), int(to_block) - self.fast_tail_chunk_size + 1)
                native_rate_limited = False
                try:
                    self._scan_native_transfers(
                        _native_from, to_block, watched_pairs,
                        source_type=HTTP_FAST_TAIL_SOURCE,
                    )
                except Exception as native_exc:
                    if self._is_rate_limit_error(native_exc):
                        logger.warning(
                            'quicknode_fast_tail_paused reason=rate_limited '
                            'watcher=%s from_block=%s to_block=%s scan=native',
                            self.watcher_name, _native_from, to_block,
                        )
                        native_rate_limited = True
                    else:
                        logger.warning(
                            'http_fast_tail_native_scan_failed watcher=%s error=%s',
                            self.watcher_name, str(native_exc)[:200],
                        )
                    scan_all_ok = False

                # Requirement 2-3: ERC20 / log-based detection is OPTIONAL. Skipped when
                # the provider is already rate-limiting (429 is provider-wide) or the log
                # scan was disabled by a prior 413. A 413 here logs
                # provider_payload_too_large, disables further log scans, and does NOT
                # clear scan_all_ok — the native scan already covered this range so the
                # checkpoint still advances and lag cannot grow forever.
                if not native_rate_limited and not self._fast_tail_log_scan_disabled:
                    for target, watched in watched_pairs:
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

                                event = self._build_event_from_log(
                                    target, log, source_type=HTTP_FAST_TAIL_SOURCE,
                                )
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
                            if self._is_payload_too_large_error(scan_exc):
                                # Requirement 3: eth_getLogs HTTP 413. The requested range/
                                # response is too large; eth_getLogs will 413 forever for
                                # this workload, so disable it (native ETH detection
                                # continues) instead of failing every poll. This is a
                                # graceful skip — it does NOT clear scan_all_ok, so the
                                # checkpoint still advances.
                                logger.warning(
                                    'provider_payload_too_large method=eth_getLogs '
                                    'watcher=%s from_block=%s to_block=%s action=log_scan_disabled',
                                    self.watcher_name, from_block, to_block,
                                )
                                self._fast_tail_log_scan_disabled = True
                                break
                            # A rate-limited or otherwise failed log scan must NOT advance
                            # the checkpoint (so the range is retried on the next poll).
                            if self._is_rate_limit_error(scan_exc):
                                logger.warning(
                                    'quicknode_fast_tail_paused reason=rate_limited '
                                    'watcher=%s from_block=%s to_block=%s scan=logs',
                                    self.watcher_name, from_block, to_block,
                                )
                                self.state['metrics']['backfill_rate_limited'] = (
                                    self.state['metrics'].get('backfill_rate_limited', 0) + 1
                                )
                            else:
                                logger.warning(
                                    'http_fast_tail_scan_failed watcher=%s target=%s error=%s',
                                    self.watcher_name, target.get('id'), str(scan_exc)[:200],
                                )
                            scan_all_ok = False
                            break

                if scan_all_ok:
                    # Advance the checkpoint when no retryable failure occurred, then
                    # publish the canonical fast-tail success marker. A 413-disabled log
                    # scan still reaches here (native scan covered the range), so lag
                    # collapses instead of growing forever.
                    _scanned_blocks = int(to_block) - int(from_block) + 1
                    self.state['last_processed_block'] = to_block
                    logger.info(
                        'quicknode_fast_tail_scan_ok latest_block=%s scanned_blocks=%s '
                        'chain_id=%s watcher=%s',
                        to_block, _scanned_blocks, self.chain_id, self.watcher_name,
                    )
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
            # Provider rate-limit cooldown (HTTP 429): do NOT attempt any WSS
            # reconnect until the cooldown window clears — reconnecting here would
            # hammer the same rate-limited provider. Keep heartbeating so System
            # Health shows provider_rate_limited + the next retry time; the
            # independent 300s stable polling worker keeps detecting transfers.
            if self._rate_limit_cooldown_active():
                self._record_heartbeat()
                _next_heartbeat = time.monotonic() + self.heartbeat_seconds
                await asyncio.sleep(
                    max(1.0, min(float(self.heartbeat_seconds), self._rate_limit_cooldown_remaining()))
                )
                continue
            if self._provider_rate_limited:
                # Cooldown elapsed → clear the breaker and attempt a fresh WSS
                # connection (realtime resumes only after the cooldown clears).
                self._resume_after_rate_limit_cooldown()
                retry = 1.0
                _last_error_key = None

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
                        _start_block = self._bootstrap_checkpoint(int(head))
                        self.state['last_processed_block'] = _start_block
                        # Remember the cold-start floor so the tx debug can report
                        # whether a given tx block was ever in the forward scan window.
                        self.state['scan_start_block'] = _start_block
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

                is_rate_limit = '429' in exc_str

                # --- Provider rate-limit circuit breaker (HTTP 429 on the WSS) ---
                # QuickNode rejecting the WebSocket upgrade with HTTP 429 is a hard,
                # provider-wide rate limit, not a transient close. Reconnecting every
                # 60-120s just hammers the same limit, so instead of falling through
                # to the reconnect backoff we trip a cooldown breaker: stop WSS
                # reconnects for rate_limit_cooldown_seconds, publish
                # provider_rate_limited + next retry, and let the independent stable
                # polling worker keep detecting. Realtime resumes once the cooldown
                # clears (requirements 1-3).
                if is_rate_limit:
                    increment(
                        'decoda_realtime_provider_failures_total',
                        provider='base_realtime_websocket',
                        error_type='rate_limited_http_429',
                    )
                    now_log = time.monotonic()
                    if error_key != _last_error_key or now_log - _last_error_logged_at >= _ERROR_LOG_WINDOW:
                        logger.warning(
                            'realtime_rpc_rate_limited chain=%s watcher=%s '
                            'cooldown_seconds=%s reconnect_count=%s',
                            self.chain_network, self.watcher_name,
                            self.rate_limit_cooldown_seconds,
                            self.state['metrics']['ws_reconnects'],
                        )
                        _last_error_key = error_key
                        _last_error_logged_at = now_log
                    self._enter_provider_rate_limit_cooldown()
                    gauge('decoda_realtime_worker_healthy', 0, watcher=self.watcher_name)
                    self._record_heartbeat()
                    _next_heartbeat = time.monotonic() + self.heartbeat_seconds
                    if self.fast_tail_enabled:
                        # Operator asserts a SEPARATE HTTP budget — tail via HTTP
                        # during the WSS outage instead of going dark (requirement 4).
                        self._wss_permanently_disabled = True
                        self._ingestion_mode = 'http_fast_tail'
                        break
                    # Default: no fast-tail (it would burn the same QuickNode quota
                    # and worsen the 429). The top-of-loop cooldown guard waits out
                    # the window with no WSS reconnect.
                    continue

                self.state['degraded_reason'] = exc_str[:160]
                self.state['source_status'] = 'degraded'

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
