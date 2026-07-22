from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import re
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
import time
import threading
from urllib import error as _urllib_error, parse, request


@dataclass
class ActivityEvent:
    event_id: str
    kind: str
    observed_at: datetime
    ingestion_source: str
    cursor: str
    payload: dict[str, Any]


logger = logging.getLogger(__name__)


class RpcRequestTooLargeError(RuntimeError):
    """An RPC request (e.g. eth_getLogs) was rejected as too large (HTTP 413).

    This is a QUERY-SIZE problem, not a provider outage or a rate limit: the
    provider is reachable and healthy, the requested block range simply returned
    too much data. Classified distinctly (``error_class=request_too_large``,
    ``status_reason=query_too_large``) so callers REDUCE the scan window instead of
    benching the provider or failing the whole poll. Never arms a provider backoff
    and never marks the provider globally unavailable.
    """


# An EVM address is exactly 20 bytes rendered as 40 lowercase hex chars with a 0x prefix.
_EVM_ADDRESS_RE = re.compile(r'^0x[0-9a-f]{40}$')


def _rpc_timeout_seconds() -> float:
    """Bounded per-request RPC timeout (seconds). Configurable via EVM_RPC_TIMEOUT_SECONDS."""
    try:
        return max(1.0, float(os.getenv('EVM_RPC_TIMEOUT_SECONDS', '10')))
    except (TypeError, ValueError):
        return 10.0


def _rpc_max_attempts() -> int:
    """Total attempts per call = 1 + EVM_RPC_MAX_RETRIES (default 3 retries → 4 attempts)."""
    try:
        return max(1, int(os.getenv('EVM_RPC_MAX_RETRIES', '3')) + 1)
    except (TypeError, ValueError):
        return 4


def _rpc_backoff_base_seconds() -> float:
    """Initial exponential backoff between RPC retries. Configurable via EVM_RPC_BACKOFF_SECONDS."""
    try:
        return max(0.0, float(os.getenv('EVM_RPC_BACKOFF_SECONDS', '1')))
    except (TypeError, ValueError):
        return 1.0


def _retry_after_seconds(exc: _urllib_error.HTTPError, fallback: float) -> float:
    """Honor a numeric Retry-After header on 429 responses; otherwise use ``fallback``.

    Respecting Retry-After means a rate-limited provider tells us how long to wait,
    so retries never compound the rate limit.
    """
    try:
        raw = exc.headers.get('Retry-After') if getattr(exc, 'headers', None) else None
    except Exception:
        raw = None
    if raw:
        try:
            return max(0.0, min(60.0, float(str(raw).strip())))
        except (TypeError, ValueError):
            return fallback
    return fallback


# ---------------------------------------------------------------------------
# Per-provider-host Base RPC backoff + last-known health (process-local).
#
# One worker process polls Base, so this state is intentionally process-local
# (not distributed). Backoff is tracked PER PROVIDER HOST: a single rate-limited
# provider (e.g. Alchemy HTTP 429) is benched on its own while the other configured
# Base RPC providers (e.g. QuickNode) keep serving the poll loop. rpc_provider_backoff_active()
# stays True only when EVERY configured provider host is benched — that is the one
# condition under which the poll loop, coverage probe, probe_rpc_health, and
# /ops/system-health skip RPC entirely. No secrets are ever stored — only host
# strings, timing, a coarse error class, and the last health dict.
# ---------------------------------------------------------------------------
_RPC_PROVIDER_LOCK = threading.Lock()

# Sentinel host benched when a 429 is recorded without a resolvable provider host
# (legacy callers / no URL configured). It backs off "the provider" globally so
# single-provider deployments behave exactly as before per-host tracking landed.
_GLOBAL_BACKOFF_HOST = '*'

# host -> {'until_monotonic': float, 'until_wall': str | None, 'error_class': str}
_RPC_HOST_BACKOFF: dict[str, dict[str, Any]] = {}

_RPC_PROVIDER_STATE: dict[str, Any] = {
    'last_health': None,               # last probe_rpc_health() result dict
    'last_health_at_monotonic': 0.0,
}

# Snapshot of the most recent FailoverJsonRpcClient.call() outcome, for host-only
# structured logging and /system-health observability. Never holds a URL or secret.
_RPC_FAILOVER_SNAPSHOT: dict[str, Any] = {
    'rpc_provider_count': 0,
    'active_rpc_host': None,
    'failed_rpc_hosts': [],
    'rpc_failover_used': False,
}

# Process-local "eth_getLogs query too large" signal (HTTP 413). Set by the poll
# loop when a log scan request was rejected as too large and the scan window had to
# be reduced; read by /system-health so it reports "provider reachable, log scan
# reduced" instead of a generic provider outage. Cleared when a full (un-reduced)
# log scan later succeeds. Host-only — never a URL or secret. This is deliberately
# SEPARATE from provider backoff: a 413 must never bench the provider.
_RPC_QUERY_TOO_LARGE: dict[str, Any] = {
    'active': False,
    'host': None,
    'reduced_chunk_size': None,
    'at_wall': None,
}

# Administratively disabled provider routes: host -> reason. A route benched here is
# a KNOWN-INVALID endpoint (e.g. a QuickNode host failing TLS every dial) that must
# NOT be re-dialed every polling cycle. It stays "configured" (still reported by the
# validators and route inventory) but is NOT "operational" — the failover dial path
# skips it until an operator re-enables it. Distinct from the transient 429 backoff.
_RPC_ROUTE_DISABLED: dict[str, str] = {}

# Rate-limits the repetitive "request skipped (no network attempt)" logs so a
# per-webhook storm cannot flood Railway. key -> last-emit monotonic time.
_RPC_SKIP_LOG_AT: dict[str, float] = {}
_RPC_SKIP_LOG_WINDOW_SECONDS = 60.0

# Per-provider validated chain-id cache: host -> chain_id (int). Once a provider has
# answered eth_chainId successfully we never need to re-ask it — the chain a URL points
# at does not change. Caching it here means a health probe against an already-validated
# provider skips the redundant eth_chainId network call (Section 4: "Cache chain ID per
# validated provider. No repeated eth_chainId inside the same poll."). Cleared by
# reset_rpc_provider_state so tests start from a clean cache.
_RPC_CHAIN_ID_CACHE: dict[str, int] = {}

# Bounded per-host RPC request-volume counters for the periodic
# rpc_request_volume_summary. Never logged per request — a summary is emitted at
# most once per window per host so the source of a rate-limit storm (which method,
# which caller) is visible without one-line-per-call spam. Host-only, no secrets.
_RPC_VOLUME_WINDOW_SECONDS = 60.0
_RPC_VOLUME: dict[str, Any] = {'window_start_monotonic': None, 'hosts': {}}


def _host_of(url: str | None) -> str:
    """Return the lowercase hostname of an RPC URL, or 'unknown'. Never the path/key."""
    try:
        return (parse.urlparse(str(url or '')).hostname or 'unknown').lower()
    except Exception:
        return 'unknown'


def _is_production_like_runtime() -> bool:
    """True for production/prod/staging runtimes (APP_ENV, then APP_MODE).

    Defined locally (not imported) so this low-level provider module stays free of
    circular imports. Mirrors the production gate used elsewhere in the codebase.
    """
    return os.getenv('APP_ENV', os.getenv('APP_MODE', 'development')).strip().lower() in {
        'production', 'prod', 'staging'
    }


def _rpc_backoff_min_seconds() -> float:
    """Minimum provider backoff after an HTTP 429 (seconds).

    A 429 means the provider is rate-limiting, so we wait at least this long before
    any RPC call. Production-like runtimes (APP_ENV in production/prod/staging) wait
    at least 10 minutes so a throttled Alchemy quota is given real time to recover
    and the worker never re-hits the provider in a tight loop; non-production keeps
    a shorter 120s floor for fast local iteration. RPC_PROVIDER_BACKOFF_MIN_SECONDS
    overrides both.
    """
    _default = 600.0 if _is_production_like_runtime() else 120.0
    try:
        return max(1.0, float(os.getenv('RPC_PROVIDER_BACKOFF_MIN_SECONDS', str(_default))))
    except (TypeError, ValueError):
        return _default


def _rpc_backoff_jitter_seconds() -> float:
    """Max random seconds added on top of the backoff window (default 30).

    Jitter de-synchronizes the resume instant across the poll loop, coverage probe,
    and /system-health so they do not all dial the provider the moment the window
    expires and immediately re-trip the rate limit. Jitter only ever *extends* the
    window, so the configured minimum is always honored. Set to 0 to disable.
    """
    try:
        return max(0.0, float(os.getenv('RPC_PROVIDER_BACKOFF_JITTER_SECONDS', '30')))
    except (TypeError, ValueError):
        return 30.0


def _retry_after_for_backoff(exc: _urllib_error.HTTPError) -> float | None:
    """Numeric Retry-After (seconds) for the provider backoff window, or None.

    Unlike :func:`_retry_after_seconds` (clamped to 60s for an inline retry), the
    provider backoff honors a larger window so a provider asking for minutes is
    respected. Clamped to 1 hour so a hostile header cannot pin us forever.
    """
    try:
        raw = exc.headers.get('Retry-After') if getattr(exc, 'headers', None) else None
    except Exception:
        raw = None
    if not raw:
        return None
    try:
        return max(0.0, min(3600.0, float(str(raw).strip())))
    except (TypeError, ValueError):
        return None


def _arm_host_backoff(
    host: str,
    backoff_seconds: float,
    error_class: str = 'rate_limited',
    *,
    retry_after_seconds: float | None = None,
) -> tuple[str, bool]:
    """Arm a backoff window for a single provider host (circuit breaker).

    Returns ``(until_wall, armed)``:

    * ``armed=True``  — this call CREATED a fresh window because the host was not
      already benched (a newly observed provider failure). ``until_wall`` is the new
      expiration. The original failure timestamp and any provider-supplied
      ``retry_after`` are recorded for observability.
    * ``armed=False`` — the host was ALREADY inside an active backoff window, so the
      existing expiration is returned UNCHANGED. This is the circuit-breaker
      invariant: a call skipped (or re-observed) because of an existing backoff must
      never push ``backoff_until`` forward. Using monotonic time means a wall-clock
      change can never spuriously "reopen" the window.
    """
    now_mono = time.monotonic()
    now_wall = datetime.now(timezone.utc)
    with _RPC_PROVIDER_LOCK:
        existing = _RPC_HOST_BACKOFF.get(host)
        if existing is not None and now_mono < float(existing.get('until_monotonic') or 0.0):
            # Already benched: keep the existing window, do NOT extend it.
            return str(existing.get('until_wall') or ''), False
        until_wall = (now_wall + timedelta(seconds=backoff_seconds)).isoformat()
        _RPC_HOST_BACKOFF[host] = {
            'until_monotonic': now_mono + backoff_seconds,
            'until_wall': until_wall,
            'error_class': error_class,
            # Original observed-failure timestamp + the retry_after the provider gave
            # us (when any), retained across the window so a later skipped observation
            # never rewrites them.
            'first_failure_at': now_wall.isoformat(),
            'retry_after_seconds': retry_after_seconds,
        }
    return until_wall, True


def _active_backoff_hosts() -> set[str]:
    """Set of provider hosts whose backoff window has not yet elapsed."""
    now_mono = time.monotonic()
    with _RPC_PROVIDER_LOCK:
        return {
            host for host, st in _RPC_HOST_BACKOFF.items()
            if now_mono < float(st.get('until_monotonic') or 0.0)
        }


def _configured_provider_hosts() -> list[str]:
    """Hosts of the currently-configured Base/global RPC providers (deduped, ordered)."""
    hosts: list[str] = []
    for url in _resolve_evm_rpc_urls():
        host = _host_of(url)
        if host and host not in hosts:
            hosts.append(host)
    return hosts


def record_rpc_rate_limited(retry_after_seconds: float | None = None, *, host: str | None = None) -> float:
    """Record an HTTP 429 from a Base RPC provider and arm a PER-HOST backoff.

    When ``host`` is given, only that provider host is benched — the other configured
    providers keep serving the poll loop (Alchemy 429 → Alchemy backoff, QuickNode
    still polled). When ``host`` is omitted, every currently-configured provider host
    is benched (legacy whole-provider behavior); with no configured providers a global
    sentinel host is benched so single-provider deployments back off exactly as before.

    The window is at least ``RPC_PROVIDER_BACKOFF_MIN_SECONDS`` (10 minutes in
    production, 120s otherwise), honors a larger ``Retry-After`` when present, and adds
    bounded jitter so providers do not all resume at the same instant. Returns the
    effective backoff in seconds.
    """
    backoff = _rpc_backoff_min_seconds()
    if retry_after_seconds is not None and float(retry_after_seconds) > backoff:
        backoff = float(retry_after_seconds)
    # Jitter only extends the window, so the minimum / Retry-After floor still holds.
    jitter = _rpc_backoff_jitter_seconds()
    if jitter > 0:
        backoff += random.uniform(0.0, jitter)
    if host is not None:
        hosts = [host]
    else:
        hosts = _configured_provider_hosts() or [_GLOBAL_BACKOFF_HOST]
    # This function is only ever reached AFTER a real HTTP 429 response (JsonRpcClient
    # or the /system-health probe) — a genuine network attempt that failed. Log that
    # fact explicitly (network_attempted=true) so a real failure is never conflated
    # with a request the circuit breaker skipped WITHOUT dialing (logged separately in
    # the failover path with network_attempted=false).
    logger.warning(
        'event=rpc_provider_request_failed network_attempted=true http_status=429 '
        'rpc_host=%s retry_after_seconds=%s',
        ','.join(hosts),
        'none' if retry_after_seconds is None else int(retry_after_seconds),
    )
    for entry in hosts:
        _record_rpc_volume(entry, method='http_429', caller='provider_429', rate_limited=True)
    armed_hosts: list[str] = []
    not_extended: list[tuple[str, str]] = []   # (host, existing_until_wall)
    armed_until_wall = ''
    for entry in hosts:
        until_wall, armed = _arm_host_backoff(
            entry, backoff, 'rate_limited', retry_after_seconds=retry_after_seconds,
        )
        if armed:
            armed_hosts.append(entry)
            armed_until_wall = until_wall
        else:
            not_extended.append((entry, until_wall))
    # A real 429 re-observed for a host already inside an active window: keep the
    # existing expiration (circuit-breaker invariant — never push backoff_until
    # forward). This is a REAL network failure (network_attempted=true) that simply did
    # not extend the window — it is NOT a skipped call, so it is NOT logged as
    # rpc_call_skipped_existing_backoff (which means "no network attempt was made").
    for entry, existing_until in not_extended:
        logger.info(
            'event=rpc_provider_backoff_not_extended rpc_host=%s http_status=429 '
            'network_attempted=true backoff_until=%s backoff_extended=false',
            entry,
            existing_until or 'unknown',
        )
    # Only a newly observed provider failure (the host was not already benched) arms a
    # fresh window and emits rpc_provider_backoff_set. A real 429 was received, so the
    # event carries network_attempted=true — never rpc_call_skipped=true (a skip means
    # no network attempt, the opposite of what happened here).
    if armed_hosts:
        logger.warning(
            'event=rpc_provider_backoff_set error_class=rate_limited rpc_status=rate_limited '
            'rpc_host=%s backoff_seconds=%s retry_after_seconds=%s backoff_until=%s '
            'network_attempted=true backoff_extended=true',
            ','.join(armed_hosts),
            int(backoff),
            'none' if retry_after_seconds is None else int(retry_after_seconds),
            armed_until_wall,
        )
    return backoff


def rpc_provider_backoff_active() -> bool:
    """True only when EVERY configured Base RPC provider host is in backoff.

    This is the one condition under which the poll loop, coverage probe, and
    /system-health skip RPC entirely. While at least one configured provider is
    still outside its backoff window, polling continues via that provider (failover).
    """
    active = _active_backoff_hosts()
    if not active:
        return False
    if _GLOBAL_BACKOFF_HOST in active:
        return True
    configured = _configured_provider_hosts()
    if not configured:
        # No provider list to compare against — any active host backoff means skip
        # (preserves legacy behavior when a 429 was recorded for an ad-hoc URL).
        return True
    return all(host in active for host in configured)


def host_backoff_active(host: str) -> bool:
    """True while the given provider host's backoff window has not elapsed."""
    return host in _active_backoff_hosts()


def backoff_hosts() -> list[str]:
    """Sorted list of provider hosts currently in backoff (host-only, no secrets)."""
    return sorted(_active_backoff_hosts())


def rpc_provider_backoff_status() -> dict[str, Any]:
    """Aggregate snapshot of provider backoff across hosts (operator messages/logs)."""
    now_mono = time.monotonic()
    with _RPC_PROVIDER_LOCK:
        live = {
            host: dict(st) for host, st in _RPC_HOST_BACKOFF.items()
            if now_mono < float(st.get('until_monotonic') or 0.0)
        }
    remaining = 0.0
    until_wall = None
    error_class = None
    first_failure_at = None
    retry_after_seconds = None
    for st in live.values():
        rem = float(st.get('until_monotonic') or 0.0) - now_mono
        if rem > remaining:
            remaining = rem
            until_wall = st.get('until_wall')
            error_class = st.get('error_class')
            first_failure_at = st.get('first_failure_at')
            retry_after_seconds = st.get('retry_after_seconds')
    return {
        'active': rpc_provider_backoff_active(),
        'remaining_seconds': max(0.0, remaining),
        'backoff_until': until_wall,
        'error_class': error_class,
        # Original failure timestamp + provider Retry-After, for the onboarding
        # "retry disabled during backoff" affordance (retry_after/backoff expiry).
        'first_failure_at': first_failure_at,
        'retry_after_seconds': retry_after_seconds,
        'backoff_hosts': sorted(live.keys()),
    }


def clear_rpc_provider_backoff(host: str | None = None) -> None:
    """Clear a single provider host's backoff, or all hosts when ``host`` is None.

    Clearing a specific host also clears the global sentinel: a confirmed-healthy
    provider means the whole-provider rate limit recorded without a host is over.
    """
    with _RPC_PROVIDER_LOCK:
        if host is None:
            _RPC_HOST_BACKOFF.clear()
        else:
            _RPC_HOST_BACKOFF.pop(host, None)
            _RPC_HOST_BACKOFF.pop(_GLOBAL_BACKOFF_HOST, None)


def record_rpc_provider_success(
    host: str,
    *,
    provider_count: int | None = None,
    failed_hosts: list[str] | None = None,
    failover_used: bool = False,
) -> None:
    """A provider host served a call: clear its backoff and update the failover snapshot."""
    clear_rpc_provider_backoff(host)
    with _RPC_PROVIDER_LOCK:
        if provider_count is not None:
            _RPC_FAILOVER_SNAPSHOT['rpc_provider_count'] = int(provider_count)
        _RPC_FAILOVER_SNAPSHOT['active_rpc_host'] = host
        _RPC_FAILOVER_SNAPSHOT['failed_rpc_hosts'] = list(failed_hosts or [])
        _RPC_FAILOVER_SNAPSHOT['rpc_failover_used'] = bool(failover_used)


def _record_failover_unavailable(provider_count: int, failed_hosts: list[str]) -> None:
    """Record that every provider was skipped/failed (no provider served the call)."""
    with _RPC_PROVIDER_LOCK:
        _RPC_FAILOVER_SNAPSHOT['rpc_provider_count'] = int(provider_count)
        _RPC_FAILOVER_SNAPSHOT['active_rpc_host'] = None
        _RPC_FAILOVER_SNAPSHOT['failed_rpc_hosts'] = list(failed_hosts)
        _RPC_FAILOVER_SNAPSHOT['rpc_failover_used'] = True


def rpc_provider_log_fields() -> dict[str, Any]:
    """Host-only structured-log fields describing the current provider/failover state."""
    with _RPC_PROVIDER_LOCK:
        snap = dict(_RPC_FAILOVER_SNAPSHOT)
    return {
        'rpc_provider_count': snap.get('rpc_provider_count', 0),
        'active_rpc_host': snap.get('active_rpc_host'),
        'failed_rpc_hosts': list(snap.get('failed_rpc_hosts') or []),
        'backoff_hosts': backoff_hosts(),
        'disabled_rpc_routes': disabled_rpc_routes(),
        'rpc_failover_used': bool(snap.get('rpc_failover_used', False)),
    }


# ---------------------------------------------------------------------------
# Administratively disabled provider routes (known-invalid endpoints).
# ---------------------------------------------------------------------------

def disable_rpc_route(host: str, reason: str = 'known_invalid') -> None:
    """Bench a KNOWN-INVALID provider host so the dial path stops re-trying it.

    Use for an endpoint that fails deterministically every cycle (e.g. a QuickNode
    host returning ``TLSV1_ALERT_INTERNAL_ERROR`` on every TLS handshake). The route
    stays *configured* — validators and the route inventory still report it — but it is
    no longer *operational*: :class:`FailoverJsonRpcClient` skips it entirely (no
    network attempt) until :func:`enable_rpc_route` clears it. This is deliberately
    distinct from the transient 429 backoff, which auto-expires; a disabled route stays
    disabled until an operator (or a passing re-validation) re-enables it, so a broken
    TLS route is never dialed every polling cycle.
    """
    host = (host or '').strip().lower()
    if not host:
        return
    with _RPC_PROVIDER_LOCK:
        newly = host not in _RPC_ROUTE_DISABLED
        _RPC_ROUTE_DISABLED[host] = str(reason or 'known_invalid')
    if newly:
        logger.warning(
            'event=rpc_route_disabled rpc_host=%s reason=%s network_attempted=false',
            host, reason,
        )


def enable_rpc_route(host: str | None = None) -> None:
    """Re-enable a previously disabled route, or all routes when ``host`` is None."""
    host = (host or '').strip().lower() if host is not None else None
    with _RPC_PROVIDER_LOCK:
        if host is None:
            had = bool(_RPC_ROUTE_DISABLED)
            _RPC_ROUTE_DISABLED.clear()
        else:
            had = _RPC_ROUTE_DISABLED.pop(host, None) is not None
    if had:
        logger.info('event=rpc_route_enabled rpc_host=%s', host or 'all')


def is_rpc_route_disabled(host: str | None) -> bool:
    """True while ``host`` is administratively disabled (known-invalid route)."""
    with _RPC_PROVIDER_LOCK:
        return (host or '').strip().lower() in _RPC_ROUTE_DISABLED


def disabled_rpc_routes() -> list[str]:
    """Sorted list of currently disabled provider hosts (host-only, no secrets)."""
    with _RPC_PROVIDER_LOCK:
        return sorted(_RPC_ROUTE_DISABLED.keys())


def _should_emit_skip_log(key: str, *, now: float | None = None) -> bool:
    """True at most once per window for ``key`` — collapses per-block skip storms."""
    now = now if now is not None else time.monotonic()
    with _RPC_PROVIDER_LOCK:
        last = _RPC_SKIP_LOG_AT.get(key)
        if last is None or (now - last) >= _RPC_SKIP_LOG_WINDOW_SECONDS:
            _RPC_SKIP_LOG_AT[key] = now
            return True
    return False


def _log_rpc_call_skipped(reason: str, host: str, method: str) -> None:
    """Log a request skipped WITHOUT a network attempt (rate-limited per host/reason).

    ``network_attempted=false`` is the defining property: unlike a real 429, no packet
    left the process — the circuit breaker (existing backoff) or a disabled route
    short-circuited the dial. Kept distinct from rpc_provider_backoff_set so logs never
    imply a network failure that did not happen.
    """
    if not _should_emit_skip_log(f'{reason}:{host}'):
        return
    if reason == 'disabled_route':
        logger.info(
            'event=rpc_call_skipped_disabled_route rpc_host=%s method=%s '
            'network_attempted=false backoff_extended=false',
            host, method,
        )
    else:
        logger.info(
            'event=rpc_call_skipped_existing_backoff rpc_host=%s method=%s '
            'network_attempted=false backoff_extended=false',
            host, method,
        )


# ---------------------------------------------------------------------------
# Bounded RPC request-volume instrumentation (rpc_request_volume_summary).
# ---------------------------------------------------------------------------

def _record_rpc_volume(
    host: str, *, method: str, caller: str = 'unspecified', rate_limited: bool = False,
    retry: bool = False,
) -> None:
    """Count one RPC request for the periodic per-host volume summary.

    Cheap and bounded: increments in-memory counters keyed by host/method/caller and,
    when the window has elapsed, emits ONE rpc_request_volume_summary per host and
    resets. Never logs per request. Host/method/caller only — no URL, path, or token.
    """
    host = (host or 'unknown').strip().lower()
    caller = (caller or 'unspecified').strip() or 'unspecified'
    now = time.monotonic()
    due: list[tuple[str, dict[str, Any]]] = []
    with _RPC_PROVIDER_LOCK:
        if _RPC_VOLUME['window_start_monotonic'] is None:
            _RPC_VOLUME['window_start_monotonic'] = now
        bucket = _RPC_VOLUME['hosts'].setdefault(
            host, {'calls_total': 0, 'by_method': {}, 'by_caller': {}, 'rate_limited': 0, 'retries': 0},
        )
        bucket['calls_total'] += 1
        bucket['by_method'][method] = bucket['by_method'].get(method, 0) + 1
        bucket['by_caller'][caller] = bucket['by_caller'].get(caller, 0) + 1
        if rate_limited:
            bucket['rate_limited'] += 1
        if retry:
            bucket['retries'] += 1
        started = float(_RPC_VOLUME['window_start_monotonic'])
        if (now - started) >= _RPC_VOLUME_WINDOW_SECONDS:
            due = list(_RPC_VOLUME['hosts'].items())
            _RPC_VOLUME['hosts'] = {}
            _RPC_VOLUME['window_start_monotonic'] = now
            window = now - started
        else:
            window = None
    if due and window is not None:
        for h, b in due:
            logger.info(
                'event=rpc_request_volume_summary window_seconds=%s rpc_host=%s calls_total=%s '
                'calls_by_method=%s calls_by_caller=%s rate_limited=%s retries=%s',
                int(round(window)), h, b['calls_total'],
                json.dumps(b['by_method'], sort_keys=True), json.dumps(b['by_caller'], sort_keys=True),
                b['rate_limited'], b['retries'],
            )


def rpc_request_volume_snapshot() -> dict[str, Any]:
    """Current (un-emitted) request-volume counters, host-only (tests/ops)."""
    with _RPC_PROVIDER_LOCK:
        return {
            'window_start_monotonic': _RPC_VOLUME['window_start_monotonic'],
            'hosts': {h: {'calls_total': b['calls_total'],
                          'by_method': dict(b['by_method']),
                          'by_caller': dict(b['by_caller']),
                          'rate_limited': b['rate_limited'],
                          'retries': b['retries']}
                      for h, b in _RPC_VOLUME['hosts'].items()},
        }


# Optional per-thread caller tag so the volume summary can attribute calls to the
# scheduled poll vs chain-head refresh vs onboarding discovery. Defaults to
# 'unspecified' when a caller does not set it.
_RPC_CALLER = threading.local()


def current_rpc_caller() -> str:
    return getattr(_RPC_CALLER, 'name', None) or 'unspecified'


class rpc_caller_scope:
    """Tag every RPC call made in this ``with`` block with a caller name."""

    def __init__(self, name: str) -> None:
        self.name = str(name or 'unspecified')
        self._prev = 'unspecified'

    def __enter__(self) -> 'rpc_caller_scope':
        self._prev = current_rpc_caller()
        _RPC_CALLER.name = self.name
        return self

    def __exit__(self, *exc: Any) -> None:
        _RPC_CALLER.name = self._prev


# ---------------------------------------------------------------------------
# Per-request RPC latency samples (Screen-4 truthfulness: RPC request latency is
# separated from the full poll/scan duration). A single JSON-RPC request's network
# latency is measured with a monotonic clock inside the canonical JsonRpcClient.call
# and recorded to a thread-local sink WHEN a capture block is active. This lets the
# scheduled worker persist a single successful ``eth_blockNumber`` request latency as
# the canonical current provider latency — never the 11k-ms full scan duration — and
# keep the P95 fed only by real successful RPC network samples.
#
# Never recorded (they are not real network-request latencies):
#   * a route skipped for an active 429 backoff or a disabled endpoint (no network
#     attempt was made — handled in FailoverJsonRpcClient, which never reaches call());
#   * a cache hit (no network round-trip);
#   * retry sleep / provider backoff time (only the per-attempt request is timed).
# ---------------------------------------------------------------------------
_RPC_METRICS = threading.local()


def _record_rpc_request_sample(
    *,
    method: str,
    host: str | None,
    success: bool,
    latency_ms: int | None,
    network_attempted: bool = True,
    cache_hit: bool = False,
    http_status: int | None = None,
    error_category: str | None = None,
) -> None:
    """Record ONE real RPC network request into the active capture sink (if any)."""
    sink = getattr(_RPC_METRICS, 'sink', None)
    if sink is None:
        return
    sink.append({
        'method': str(method),
        'provider_host': host,
        'success': bool(success),
        'latency_ms': int(latency_ms) if latency_ms is not None else None,
        'network_attempted': bool(network_attempted),
        'cache_hit': bool(cache_hit),
        'http_status': http_status,
        'error_category': error_category,
        'caller': current_rpc_caller(),
    })


class rpc_metrics_capture:
    """Collect per-request RPC latency samples made within this ``with`` block.

    After the block exits, ``capture.samples`` holds every real network request the
    canonical JsonRpcClient made (successful and failed). Nested captures are supported
    — the previous sink is restored on exit so an inner probe never steals an outer
    poll's samples.
    """

    def __init__(self) -> None:
        self.samples: list[dict[str, Any]] = []
        self._prev: list[dict[str, Any]] | None = None

    def __enter__(self) -> 'rpc_metrics_capture':
        self._prev = getattr(_RPC_METRICS, 'sink', None)
        _RPC_METRICS.sink = self.samples
        return self

    def __exit__(self, *exc: Any) -> None:
        _RPC_METRICS.sink = self._prev

    def successful_request_latency_ms(self, *, prefer_method: str = 'eth_blockNumber') -> int | None:
        """Canonical current provider latency: the latency of a successful ``prefer_method``
        request (``eth_blockNumber`` is always part of provider validation), else the
        first successful real network request. None when no successful network sample
        exists — never invented, never the scan duration."""
        successful = [
            s for s in self.samples
            if s.get('success') and s.get('network_attempted') and not s.get('cache_hit')
            and s.get('latency_ms') is not None
        ]
        if not successful:
            return None
        for s in successful:
            if s.get('method') == prefer_method:
                return int(s['latency_ms'])
        return int(successful[0]['latency_ms'])


# ---------------------------------------------------------------------------
# Live RPC endpoint probe (bounded DNS / TLS / HTTP / JSON-RPC validation).
# ---------------------------------------------------------------------------

def _classify_probe_error(exc: BaseException) -> str:
    """Map a probe exception to a stable, secret-free safe_error_category."""
    text = str(exc).lower()
    if isinstance(exc, _urllib_error.HTTPError):
        if exc.code == 429:
            return 'rate_limited'
        if exc.code == 413:
            return 'request_too_large'
        return f'http_{exc.code}'
    if 'tlsv1_alert_internal_error' in text or 'internal_error' in text and 'tls' in text:
        return 'tls_internal_error'
    if 'certificate verify failed' in text or 'certificate_verify_failed' in text:
        return 'tls_certificate_invalid'
    if 'ssl' in text or 'tls' in text:
        return 'tls_error'
    if 'name or service not known' in text or 'nodename nor servname' in text or 'getaddrinfo' in text:
        return 'dns_failure'
    if 'timed out' in text or 'timeout' in text:
        return 'timeout'
    if 'connection refused' in text or 'refused' in text:
        return 'connection_refused'
    return 'connection_error'


def probe_rpc_endpoint(
    url: str | None,
    *,
    expected_chain_id: int | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Actively validate ONE Base RPC endpoint from the worker runtime, safely.

    Runs a bounded ladder — DNS resolution, TLS handshake (SNI = host), an HTTP POST
    of ``eth_chainId``, then ``eth_blockNumber`` — and returns a host-only diagnostic
    dict. Emits exactly one ``event=rpc_endpoint_validation`` line with
    ``dns_ok`` / ``tls_ok`` / ``http_ok`` / ``json_rpc_ok`` / ``chain_id`` /
    ``safe_error_category``. The URL, path, and API token are NEVER logged or returned —
    only the redacted hostname. This is the operator tool for diagnosing a TLS-broken or
    mis-copied Railway RPC variable without ever printing a secret.
    """
    import socket
    import ssl

    timeout = _rpc_timeout_seconds() if timeout is None else max(1.0, float(timeout))
    shape = validate_rpc_endpoint(url, expected_chain=(f'{expected_chain_id}' if expected_chain_id else 'base-mainnet'))
    host = shape.get('host')
    result: dict[str, Any] = {
        'host': host,
        'scheme_ok': shape.get('scheme_ok', False),
        'malformed': shape.get('malformed', False),
        'dns_ok': False,
        'tls_ok': False,
        'http_ok': False,
        'json_rpc_ok': False,
        'chain_id': None,
        'latest_block': None,
        'chain_id_matches': None,
        'safe_error_category': 'ok',
    }

    def _emit() -> dict[str, Any]:
        logger.info(
            'event=rpc_endpoint_validation rpc_host=%s dns_ok=%s tls_ok=%s http_ok=%s '
            'json_rpc_ok=%s chain_id=%s safe_error_category=%s',
            host or 'unknown', str(result['dns_ok']).lower(), str(result['tls_ok']).lower(),
            str(result['http_ok']).lower(), str(result['json_rpc_ok']).lower(),
            result['chain_id'] if result['chain_id'] is not None else 'none',
            result['safe_error_category'],
        )
        return result

    if shape.get('malformed') or not shape.get('scheme_ok') or not host:
        result['safe_error_category'] = shape.get('reason') or 'malformed_url'
        return _emit()

    parsed = parse.urlparse(str(url or '').strip())
    port = parsed.port or 443
    # 1) DNS
    try:
        socket.getaddrinfo(host, port)
        result['dns_ok'] = True
    except Exception as exc:  # pragma: no cover - network dependent
        result['safe_error_category'] = _classify_probe_error(exc)
        return _emit()
    # 2) TLS handshake (SNI = host)
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host):
                result['tls_ok'] = True
    except Exception as exc:  # pragma: no cover - network dependent
        result['safe_error_category'] = _classify_probe_error(exc)
        return _emit()
    # 3 & 4) HTTP POST eth_chainId / eth_blockNumber via the same client the poller uses
    try:
        client = JsonRpcClient(str(url).strip())
        chain_hex = client.call('eth_chainId', [])
        result['http_ok'] = True
        chain_id = _hex_to_int(chain_hex) if isinstance(chain_hex, str) else (int(chain_hex) if isinstance(chain_hex, int) else None)
        result['chain_id'] = chain_id
        result['json_rpc_ok'] = chain_id is not None
        if expected_chain_id is not None and chain_id is not None:
            result['chain_id_matches'] = (chain_id == int(expected_chain_id))
        block_hex = client.call('eth_blockNumber', [])
        result['latest_block'] = _hex_to_int(block_hex) if isinstance(block_hex, str) else None
    except Exception as exc:  # pragma: no cover - network dependent
        result['safe_error_category'] = _classify_probe_error(exc)
        return _emit()
    return _emit()


def probe_worker_rpc_endpoints(*, expected_chain_id: int | None = 8453) -> dict[str, Any]:
    """Actively probe every configured Base RPC endpoint and disable hard-broken ones.

    An endpoint whose TLS handshake fails deterministically (e.g. QuickNode
    ``TLSV1_ALERT_INTERNAL_ERROR``) is a KNOWN-INVALID route: it is disabled via
    :func:`disable_rpc_route` so the poll loop stops re-dialing it every cycle. A
    rate-limited (429) endpoint is left alone — that is a transient throttle handled by
    the 429 backoff, not a broken route. Returns a host-only aggregate report.
    """
    reports: list[dict[str, Any]] = []
    for url in _resolve_evm_rpc_urls():
        report = probe_rpc_endpoint(url, expected_chain_id=expected_chain_id)
        host = report.get('host')
        category = report.get('safe_error_category')
        # Only bench a route for a DETERMINISTIC endpoint fault, never for a transient
        # rate limit (that is the 429 backoff's job) or a chain-id mismatch we surface.
        if host and category in {'tls_internal_error', 'tls_certificate_invalid', 'tls_error',
                                 'scheme_not_https', 'missing_path_or_key', 'malformed_url',
                                 'contains_whitespace', 'invalid_url_encoding'}:
            disable_rpc_route(host, reason=category)
        reports.append(report)
    return {
        'endpoint_count': len(reports),
        'all_operational': bool(reports) and all(r['json_rpc_ok'] for r in reports),
        'endpoints': reports,
        'disabled_rpc_routes': disabled_rpc_routes(),
        'backoff_hosts': backoff_hosts(),
    }


def validate_rpc_endpoint(url: str | None, *, expected_chain: str = 'base-mainnet') -> dict[str, Any]:
    """Safely validate a single worker RPC endpoint WITHOUT printing the URL or token.

    Returns a host-only diagnostic dict (``scheme_ok``, ``host_present``,
    ``path_present``, ``token_placed`` — whether a key/path segment is present —,
    ``looks_like_expected_chain``, ``malformed`` and a stable ``reason`` code) so an
    operator can confirm a Railway RPC variable holds a valid HTTPS Base Mainnet RPC
    URL, and catch a malformed / mis-copied endpoint, from logs alone. Only the
    redacted hostname is ever surfaced — never the full URL, path, or API token.
    """
    raw = str(url or '').strip()
    result: dict[str, Any] = {
        'host': None,
        'scheme_ok': False,
        'host_present': False,
        'path_present': False,
        'token_placed': False,
        'looks_like_expected_chain': False,
        'malformed': False,
        'valid': False,
        'reason': 'empty',
    }
    if not raw:
        return result
    # Detect obviously malformed URLs (whitespace, or broken percent-encoding from a
    # bad copy/paste) before parsing so a mis-encoded token is caught, not dialed.
    if any(ch.isspace() for ch in raw):
        result.update(malformed=True, reason='contains_whitespace')
        return result
    try:
        parse.unquote(raw, errors='strict')
    except Exception:
        result.update(malformed=True, reason='invalid_url_encoding')
        return result
    try:
        parsed = parse.urlparse(raw)
    except Exception:
        result.update(malformed=True, reason='unparseable_url')
        return result
    host = (parsed.hostname or '').lower()
    result['host'] = host or None
    result['scheme_ok'] = parsed.scheme == 'https'
    result['host_present'] = bool(host)
    path = (parsed.path or '').strip('/')
    result['path_present'] = bool(path)
    # A credentialed RPC endpoint carries the key in the path (or query); presence
    # only — the value is never inspected or logged.
    result['token_placed'] = bool(path) or bool(parsed.query)
    expected = str(expected_chain or '').strip().lower()
    chain_token = expected.split('-')[0] if expected else ''
    result['looks_like_expected_chain'] = bool(chain_token and chain_token in host)
    if not result['scheme_ok']:
        result['reason'] = 'scheme_not_https'
    elif not result['host_present']:
        result['reason'] = 'missing_host'
    elif not result['path_present']:
        result['reason'] = 'missing_path_or_key'
    else:
        result['reason'] = 'ok'
        result['valid'] = True
    return result


def validate_worker_rpc_endpoints(*, expected_chain: str = 'base-mainnet') -> dict[str, Any]:
    """Validate every configured Base/global RPC endpoint, host-only (no secrets).

    A safe operational check the worker/ops can run to confirm both providers
    (e.g. QuickNode + Alchemy) are configured with valid HTTPS Base Mainnet RPC
    URLs. Surfaces one per-endpoint report plus the aggregate, and the current
    backoff hosts, so a TLS-broken or mis-copied endpoint is diagnosable without
    ever logging a URL or token.
    """
    reports = [validate_rpc_endpoint(url, expected_chain=expected_chain) for url in _resolve_evm_rpc_urls()]
    return {
        'endpoint_count': len(reports),
        'all_valid': bool(reports) and all(r['valid'] for r in reports),
        'endpoints': reports,
        'backoff_hosts': backoff_hosts(),
    }


def record_rpc_query_too_large(host: str | None, *, reduced_chunk_size: int) -> None:
    """Record that an eth_getLogs request was rejected as too large (HTTP 413).

    Marks the process-local query-too-large signal active so /system-health can
    truthfully report "provider reachable, log scan query too large, scan window
    reduced" rather than a generic outage. Never arms a provider backoff and never
    benches the host — a 413 is a query-size problem, not a provider failure.
    """
    with _RPC_PROVIDER_LOCK:
        _RPC_QUERY_TOO_LARGE.update(
            active=True,
            host=(host or 'unknown'),
            reduced_chunk_size=int(reduced_chunk_size),
            at_wall=datetime.now(timezone.utc).isoformat(),
        )


def clear_rpc_query_too_large() -> None:
    """Clear the query-too-large signal (a full, un-reduced log scan succeeded)."""
    with _RPC_PROVIDER_LOCK:
        _RPC_QUERY_TOO_LARGE.update(active=False, host=None, reduced_chunk_size=None, at_wall=None)


def rpc_query_too_large_status() -> dict[str, Any]:
    """Snapshot of the query-too-large signal (host-only, no secrets)."""
    with _RPC_PROVIDER_LOCK:
        return dict(_RPC_QUERY_TOO_LARGE)


def _store_rpc_health(result: dict[str, Any]) -> None:
    """Remember the last probe result so a backoff window can replay it (cache_hit)."""
    with _RPC_PROVIDER_LOCK:
        _RPC_PROVIDER_STATE['last_health'] = dict(result)
        _RPC_PROVIDER_STATE['last_health_at_monotonic'] = time.monotonic()


def last_rpc_health() -> dict[str, Any] | None:
    """Last probe_rpc_health() result, if any (used to replay during backoff)."""
    with _RPC_PROVIDER_LOCK:
        last = _RPC_PROVIDER_STATE['last_health']
        return dict(last) if isinstance(last, dict) else None


def reset_rpc_provider_state() -> None:
    """Reset all process-local provider backoff/health/failover state (tests/ops)."""
    with _RPC_PROVIDER_LOCK:
        _RPC_HOST_BACKOFF.clear()
        _RPC_PROVIDER_STATE.update(last_health=None, last_health_at_monotonic=0.0)
        _RPC_FAILOVER_SNAPSHOT.update(
            rpc_provider_count=0,
            active_rpc_host=None,
            failed_rpc_hosts=[],
            rpc_failover_used=False,
        )
        _RPC_QUERY_TOO_LARGE.update(active=False, host=None, reduced_chunk_size=None, at_wall=None)
        _RPC_ROUTE_DISABLED.clear()
        _RPC_SKIP_LOG_AT.clear()
        _RPC_CHAIN_ID_CACHE.clear()
        _RPC_VOLUME['window_start_monotonic'] = None
        _RPC_VOLUME['hosts'] = {}


def worker_rpc_chain_id() -> int | None:
    """Chain id this worker's RPC is configured for (EVM_CHAIN_ID / STAGING_EVM_CHAIN_ID)."""
    raw = (os.getenv('STAGING_EVM_CHAIN_ID') or os.getenv('EVM_CHAIN_ID') or '').strip()
    return int(raw) if raw.isdigit() else None


def cached_provider_chain_id(host: str | None) -> int | None:
    """Return the previously validated chain id for ``host``, or None if not yet known.

    The chain a provider URL points at is immutable, so once eth_chainId succeeds we can
    reuse it for every subsequent probe of that host instead of re-dialing eth_chainId.
    """
    key = str(host or '').strip().lower()
    if not key:
        return None
    with _RPC_PROVIDER_LOCK:
        value = _RPC_CHAIN_ID_CACHE.get(key)
    return int(value) if isinstance(value, int) else None


def remember_provider_chain_id(host: str | None, chain_id: int | None) -> None:
    """Cache a validated chain id for ``host`` (Section 4: cache chain id per provider)."""
    key = str(host or '').strip().lower()
    if not key or not isinstance(chain_id, int):
        return
    with _RPC_PROVIDER_LOCK:
        _RPC_CHAIN_ID_CACHE[key] = int(chain_id)


def target_chain_id_for(network: str | None) -> int | None:
    """Canonical chain id for a target's labeled network, or None when unknown."""
    return (CHAIN_MAP.get(str(network or '').strip().lower()) or {}).get('chain_id')


def evaluate_chain_mismatch(network: str | None) -> tuple[bool, int | None, int | None]:
    """Return ``(hard_skip, target_chain_id, rpc_chain_id)`` for a target's network.

    ``hard_skip`` is True only when BOTH the worker's configured RPC chain id and
    the target's chain id are known and differ. An unknown/unset id never forces a
    skip — this preserves single-chain deployments and injected unit-test clients
    that do not set EVM_CHAIN_ID. A Base worker (8453) therefore hard-skips an
    Ethereum-labeled (1) target before any RPC call is made.
    """
    rpc_chain_id = worker_rpc_chain_id()
    target_chain_id = target_chain_id_for(network)
    hard_skip = bool(
        rpc_chain_id is not None
        and target_chain_id is not None
        and target_chain_id != rpc_chain_id
    )
    return hard_skip, target_chain_id, rpc_chain_id


class MonitoredWalletNotConfigured(Exception):
    """Raised when a wallet-type target has no resolvable monitored wallet address.

    Surfaced as a fail-closed misconfiguration signal rather than silently
    producing coverage-only telemetry that would hide the broken target.
    """


def _normalize_evm_address(value: Any) -> str | None:
    """Return a lowercase 0x-prefixed EVM address, or None when not a valid address."""
    text = str(value or '').strip().lower()
    return text if _EVM_ADDRESS_RE.match(text) else None


def resolve_monitored_wallet(target: dict[str, Any]) -> str | None:
    """Resolve the monitored EVM wallet for a wallet-type target.

    The canonical storage location is ``targets.wallet_address``. Targets created
    or migrated through alternate paths may instead carry the wallet in
    ``contract_identifier`` (address typed into the wrong field), in the linked
    asset's identifier (exposed on the target as ``asset_context``), or in
    ``target_metadata``. We resolve from the canonical column first, then fall
    back to those known locations. Returns a lowercase 0x address, or None when
    no valid wallet address is configured anywhere.
    """
    asset_context = target.get('asset_context') if isinstance(target.get('asset_context'), dict) else {}
    metadata = target.get('target_metadata') if isinstance(target.get('target_metadata'), dict) else {}
    candidates = (
        target.get('wallet_address'),
        target.get('contract_identifier'),
        asset_context.get('asset_identifier'),
        asset_context.get('identifier'),
        metadata.get('wallet_address'),
        metadata.get('monitored_wallet'),
    )
    for candidate in candidates:
        normalized = _normalize_evm_address(candidate)
        if normalized:
            return normalized
    return None


def explain_wallet_transfer_match(monitored_wallet: str | None, tx: dict[str, Any] | None) -> dict[str, Any]:
    """Explain whether a transaction involves the monitored wallet.

    Pure helper backing the debug command: given a monitored wallet and a raw
    ``eth_getTransactionByHash`` result, report matched/not matched and why.
    """
    wallet = _normalize_evm_address(monitored_wallet)
    tx = tx if isinstance(tx, dict) else {}
    tx_from = _normalize_evm_address(tx.get('from'))
    tx_to = _normalize_evm_address(tx.get('to'))
    if not wallet:
        return {
            'matched': False,
            'reason': 'monitored_wallet_not_configured',
            'monitored_wallet': None,
            'tx_from': tx_from,
            'tx_to': tx_to,
        }
    if not tx:
        return {
            'matched': False,
            'reason': 'transaction_not_found',
            'monitored_wallet': wallet,
            'tx_from': None,
            'tx_to': None,
        }
    direction = None
    if wallet == tx_from:
        direction = 'outbound'
    elif wallet == tx_to:
        direction = 'inbound'
    matched = direction is not None
    value_wei = _hex_to_int(tx.get('value')) or 0
    return {
        'matched': matched,
        'reason': f'wallet_transfer_{direction}' if matched else 'wallet_not_in_from_or_to',
        'monitored_wallet': wallet,
        'tx_from': tx_from,
        'tx_to': tx_to,
        'wallet_transfer_direction': direction,
        'tx_hash': str(tx.get('hash') or '') or None,
        'value_wei': value_wei,
        'value_eth': round(value_wei / 10 ** 18, 18),
    }


def native_transfer_direction(watched_address: Any, tx: dict[str, Any] | None) -> str | None:
    """Canonical native ETH transfer matcher shared by stable polling and the
    real-time worker.

    Returns ``'outbound'`` when the watched wallet is ``tx.from``, ``'inbound'``
    when it is ``tx.to``, otherwise ``None``. Both the watched address and the
    transaction's ``from``/``to`` are normalised to lowercase 0x form, so a
    checksum-cased address from MetaMask and a lowercase address in the DB never
    cause a miss. A native ETH transfer carries no logs, so this transaction-level
    match is the only way it can be detected — both the realtime backfill and the
    300 s polling worker MUST use this function so their behaviour cannot drift.
    """
    watched = _normalize_evm_address(watched_address)
    if not watched:
        return None
    tx = tx if isinstance(tx, dict) else {}
    tx_from = _normalize_evm_address(tx.get('from'))
    tx_to = _normalize_evm_address(tx.get('to'))
    if watched == tx_from:
        return 'outbound'
    if watched == tx_to:
        return 'inbound'
    return None


def _split_rpc_urls(raw: str | None) -> list[str]:
    """Split a comma-separated RPC URL list into trimmed, non-empty entries."""
    return [part.strip() for part in str(raw or '').split(',') if part.strip()]


def _resolve_evm_rpc_url() -> str:
    """Resolve the global EVM RPC URL for health checks and legacy single-chain deployments.

    Resolution order:
      1. EVM_RPC_URL_<chain_id>  (e.g. EVM_RPC_URL_8453 for Base when EVM_CHAIN_ID=8453)
      2. Named chain alias       (e.g. BASE_EVM_RPC_URL when EVM_CHAIN_ID=8453)
      3. EVM_RPC_URLS            (first of the comma-separated multi-provider list)
      4. STAGING_EVM_RPC_URL
      5. EVM_RPC_URL
    """
    chain_id_raw = (os.getenv('STAGING_EVM_CHAIN_ID') or os.getenv('EVM_CHAIN_ID') or '').strip()
    if chain_id_raw.isdigit():
        chain_specific = (os.getenv(f'EVM_RPC_URL_{chain_id_raw}') or '').strip()
        if chain_specific:
            return chain_specific
        _chain_aliases: dict[str, tuple[str, ...]] = {
            '1': ('ETHEREUM_EVM_RPC_URL', 'EVM_ETHEREUM_RPC_URL', 'ETH_EVM_RPC_URL'),
            '8453': ('BASE_EVM_RPC_URL', 'EVM_BASE_RPC_URL'),
            '42161': ('ARBITRUM_EVM_RPC_URL', 'ARB_EVM_RPC_URL'),
        }
        for alias in _chain_aliases.get(chain_id_raw, ()):
            value = (os.getenv(alias) or '').strip()
            if value:
                return value
    # EVM_RPC_URLS (multi-provider) takes precedence over the single global vars.
    multi = _split_rpc_urls(os.getenv('EVM_RPC_URLS'))
    if multi:
        return multi[0]
    return (os.getenv('STAGING_EVM_RPC_URL') or os.getenv('EVM_RPC_URL') or '').strip()


def _resolve_evm_rpc_urls() -> list[str]:
    """Ordered Base/global RPC endpoints (primary + failover) for the failover client.

    ``EVM_RPC_URLS`` (comma-separated) is the canonical multi-provider list and, when
    set, defines the full provider order. When it is absent the single resolver
    (EVM_RPC_URL_<chain> / alias / STAGING_EVM_RPC_URL / EVM_RPC_URL) is used for
    backward compatibility. Legacy ``EVM_RPC_FAILOVER_URLS`` are appended either way.
    The endpoints are returned to the dialing client only — never exposed in health
    payloads, where only provider hosts are surfaced.
    """
    multi = _split_rpc_urls(os.getenv('EVM_RPC_URLS'))
    values = list(multi) if multi else [_resolve_evm_rpc_url()]
    values.extend(_split_rpc_urls(os.getenv('EVM_RPC_FAILOVER_URLS')))
    return list(dict.fromkeys(value for value in values if value))


def probe_rpc_health(rpc_url: str | None = None, *, caller: str | None = None) -> dict[str, Any]:
    """
    Call eth_chainId and eth_blockNumber against the configured RPC endpoint.

    ``caller`` tags every RPC request made by this probe (Section 4: every request must
    carry a caller category so the rpc_request_volume_summary can attribute the load —
    scheduled_poll / worker_health_check / startup_validation / source_diagnostic / etc.).
    Precedence: an explicit ``caller`` argument wins; otherwise the ambient
    ``rpc_caller_scope`` set by the call site is used; failing both it falls back to the
    concrete ``worker_health_check`` category so a request is never attributed to
    'unspecified'.

    Returns a dict with keys:
      ok: bool
      chain_id_hex: str | None
      chain_id_int: int | None
      block_number_hex: str | None
      block_number_int: int | None
      error: str | None
    """
    url = (rpc_url or _resolve_evm_rpc_url()).strip()
    if not url:
        return {'ok': False, 'chain_id_hex': None, 'chain_id_int': None, 'block_number_hex': None, 'block_number_int': None, 'error': 'rpc_url_not_configured'}
    # Provider backoff short-circuit: a recent HTTP 429 armed a process-wide
    # backoff. Skip the live eth_blockNumber call so we never compound the rate
    # limit; replay the last known health (cache_hit) if we have it, else return a
    # backoff failure. This is what keeps the worker recheck, the coverage probe,
    # and /system-health from each hitting Alchemy again inside the same window.
    if rpc_provider_backoff_active():
        _bo = rpc_provider_backoff_status()
        cached = last_rpc_health()
        logger.info(
            'rpc_health_probe cache_hit=true reason=provider_backoff_active '
            'backoff_until=%s backoff_remaining_seconds=%s',
            _bo.get('backoff_until') or 'unknown',
            int(_bo.get('remaining_seconds') or 0),
        )
        if cached is not None:
            result = dict(cached)
            result['provider_backoff_active'] = True
            result['cache_hit'] = True
            return result
        return {
            'ok': False, 'chain_id_hex': None, 'chain_id_int': None,
            'block_number_hex': None, 'block_number_int': None,
            'error': 'provider_backoff_active', 'provider_backoff_active': True, 'cache_hit': True,
        }
    client = FailoverJsonRpcClient(_resolve_evm_rpc_urls()) if rpc_url is None else JsonRpcClient(url)
    # Section 4: reuse a previously validated chain id for a known provider host instead of
    # re-dialing eth_chainId every probe. Only meaningful for the single-URL path where the
    # host is known before the call; the failover path caches after the served host resolves.
    probe_host = _host_of(url) if rpc_url is not None else None
    cached_chain_int = cached_provider_chain_id(probe_host) if probe_host else None
    chain_id_from_cache = False
    # Tag every RPC request from this probe with the caller category so the periodic
    # rpc_request_volume_summary can attribute the load (never per-request logging).
    # Explicit arg > ambient scope set by the call site > concrete default.
    _ambient_caller = current_rpc_caller()
    effective_caller = caller or (
        _ambient_caller if _ambient_caller and _ambient_caller != 'unspecified' else 'worker_health_check'
    )
    with rpc_caller_scope(effective_caller):
        try:
            if cached_chain_int is not None:
                chain_hex = hex(cached_chain_int)
                chain_id_from_cache = True
            else:
                chain_hex = str(client.call('eth_chainId', []) or '')
            block_hex = str(client.call('eth_blockNumber', []) or '')
        except Exception as exc:
            result = {'ok': False, 'chain_id_hex': None, 'chain_id_int': None, 'block_number_hex': None, 'block_number_int': None, 'error': str(exc)[:200], 'cache_hit': False}
            _store_rpc_health(result)
            return result
    try:
        chain_int = int(chain_hex, 16)
        block_int = int(block_hex, 16)
    except (TypeError, ValueError):
        chain_int = _hex_to_int(chain_hex)
        block_int = None
    logger.info(
        'rpc_eth_blockNumber_result chain_id=%s raw_eth_blockNumber_hex=%s parsed_block_number_decimal=%s cache_hit=false',
        chain_int,
        block_hex or 'missing',
        block_int,
    )
    if chain_int is None or block_int is None:
        result = {'ok': False, 'chain_id_hex': chain_hex or None, 'chain_id_int': chain_int, 'block_number_hex': block_hex or None, 'block_number_int': block_int, 'error': 'invalid_rpc_response', 'cache_hit': False}
        _store_rpc_health(result)
        return result
    # A successful probe means this provider recovered — clear its host backoff.
    # The failover client already cleared the serving host on success; clear the
    # explicitly probed host too for the single-URL path so a recovered provider
    # resumes. Resolve the served host from rpc_url (single) or the client's
    # active_host (failover) without an isinstance check, so a patched/mocked client
    # in tests never breaks the probe.
    served_host = _host_of(url) if rpc_url is not None else getattr(client, 'active_host', None)
    clear_rpc_provider_backoff(served_host)
    # Section 4: remember this provider's validated chain id so the next probe of the same
    # host skips the eth_chainId dial (the chain a URL points at is immutable).
    if not chain_id_from_cache:
        remember_provider_chain_id(served_host, chain_int)
    # Host-only provider/failover observability (no URL or key). Emitted for the
    # canonical multi-provider probe so logs show which provider served and whether
    # failover was used. Low-frequency: startup + unhealthy rechecks only.
    if rpc_url is None:
        _fields = rpc_provider_log_fields()
        logger.info(
            'rpc_provider_health rpc_provider_count=%s active_rpc_host=%s failed_rpc_hosts=%s '
            'backoff_hosts=%s rpc_failover_used=%s',
            _fields['rpc_provider_count'], _fields['active_rpc_host'] or 'none',
            ','.join(_fields['failed_rpc_hosts']) or 'none',
            ','.join(_fields['backoff_hosts']) or 'none',
            str(_fields['rpc_failover_used']).lower(),
        )
    result = {'ok': True, 'chain_id_hex': chain_hex, 'chain_id_int': chain_int, 'block_number_hex': block_hex, 'block_number_int': block_int, 'error': None, 'cache_hit': False}
    _store_rpc_health(result)
    return result


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
    'ethereum-mainnet': {'chain_id': 1},
    'mainnet': {'chain_id': 1},
    'eth': {'chain_id': 1},
    'eth-mainnet': {'chain_id': 1},
    'base': {'chain_id': 8453},
    'base-mainnet': {'chain_id': 8453},
    'arbitrum': {'chain_id': 42161},
    'arbitrum-one': {'chain_id': 42161},
}

# Per-chain RPC endpoint env-var aliases. ``EVM_RPC_URL_<chain_id>`` is always
# checked first; these named aliases are accepted for operator readability.
# Both ``BASE_EVM_RPC_URL`` and ``EVM_BASE_RPC_URL`` are accepted so operators
# can use either prefix convention. Same for Ethereum.
_CHAIN_RPC_ENV_ALIASES = {
    1: ('ETHEREUM_EVM_RPC_URL', 'EVM_ETHEREUM_RPC_URL', 'ETH_EVM_RPC_URL'),
    8453: ('BASE_EVM_RPC_URL', 'EVM_BASE_RPC_URL'),
    42161: ('ARBITRUM_EVM_RPC_URL', 'ARB_EVM_RPC_URL'),
}


def resolve_chain_rpc(network: str | None) -> dict[str, Any]:
    """Resolve the RPC endpoint that serves a target's labeled chain.

    Routing precedence (most specific first):
      1. ``EVM_RPC_URL_<chain_id>``      e.g. ``EVM_RPC_URL_8453`` for Base
      2. ``<CHAIN>_EVM_RPC_URL`` alias   e.g. ``BASE_EVM_RPC_URL``
      3. Global ``STAGING_EVM_RPC_URL`` / ``EVM_RPC_URL`` (legacy single-chain)

    The global fallback is intentionally last and is only safe because
    :func:`fetch_evm_activity` probes ``eth_chainId`` and fails closed when the
    resolved endpoint does not actually serve ``expected_chain_id``. This is what
    stops a single global Base RPC from being used for an Ethereum-labeled target.

    Returns a dict with: ``network``, ``expected_chain_id`` (from ``CHAIN_MAP``,
    ``None`` when the network is unknown), ``rpc_url``, ``rpc_url_env`` (the env
    var name that supplied the URL — for logs; never the URL/secret itself), and
    ``rpc_urls`` (primary + per-chain failover, deduplicated).
    """
    network = (network or '').strip().lower()
    expected_chain_id = (CHAIN_MAP.get(network) or {}).get('chain_id')
    rpc_url = ''
    rpc_url_env: str | None = None
    failover_raw = ''
    if expected_chain_id is not None:
        for name in (f'EVM_RPC_URL_{expected_chain_id}', *_CHAIN_RPC_ENV_ALIASES.get(expected_chain_id, ())):
            value = (os.getenv(name) or '').strip()
            if value:
                rpc_url, rpc_url_env = value, name
                failover_raw = (os.getenv(f'EVM_RPC_FAILOVER_URLS_{expected_chain_id}') or '').strip()
                break
    if not rpc_url:
        rpc_url = _resolve_evm_rpc_url()
        if rpc_url:
            # Label the env that actually supplied the URL, matching _resolve_evm_rpc_url's
            # precedence (EVM_RPC_URLS → STAGING_EVM_RPC_URL → EVM_RPC_URL) so logs are truthful.
            if _split_rpc_urls(os.getenv('EVM_RPC_URLS')):
                rpc_url_env = 'EVM_RPC_URLS'
            elif (os.getenv('STAGING_EVM_RPC_URL') or '').strip():
                rpc_url_env = 'STAGING_EVM_RPC_URL'
            else:
                rpc_url_env = 'EVM_RPC_URL'
    if rpc_url_env in ('STAGING_EVM_RPC_URL', 'EVM_RPC_URL', 'EVM_RPC_URLS'):
        rpc_urls = _resolve_evm_rpc_urls()
    else:
        ordered = [rpc_url] if rpc_url else []
        ordered.extend(part.strip() for part in failover_raw.split(',') if part.strip())
        rpc_urls = list(dict.fromkeys(url for url in ordered if url))
    return {
        'network': network,
        'expected_chain_id': expected_chain_id,
        'rpc_url': rpc_url,
        'rpc_url_env': rpc_url_env,
        'rpc_urls': rpc_urls,
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
        timeout = _rpc_timeout_seconds()
        max_attempts = _rpc_max_attempts()
        backoff = _rpc_backoff_base_seconds()
        # Count this request for the periodic rpc_request_volume_summary (host/method/
        # caller only). One count per call() — inner retries are counted as retries.
        _host = _host_of(self.rpc_url)
        _record_rpc_volume(_host, method=method, caller=current_rpc_caller())
        for attempt in range(max_attempts):
            if attempt > 0:
                _record_rpc_volume(_host, method=method,
                                   caller=current_rpc_caller(), retry=True)
            # Time THIS network attempt only with a monotonic clock — never the retry
            # sleeps between attempts — so the recorded sample is a single real RPC
            # request latency, not the accumulated retry/backoff time.
            _req_started = time.monotonic()
            try:
                with request.urlopen(req, timeout=timeout) as resp:  # nosec B310
                    body = json.loads(resp.read().decode('utf-8'))
                if body.get('error'):
                    raise RuntimeError(f"json-rpc error: {body['error']}")
                _record_rpc_request_sample(
                    method=method, host=_host, success=True,
                    latency_ms=int(round((time.monotonic() - _req_started) * 1000)),
                    http_status=200,
                )
                return body.get('result')
            except _urllib_error.HTTPError as exc:
                _record_rpc_request_sample(
                    method=method, host=_host, success=False,
                    latency_ms=int(round((time.monotonic() - _req_started) * 1000)),
                    http_status=getattr(exc, 'code', None),
                    error_category=(f'http_{getattr(exc, "code", "err")}'),
                )
                # HTTP 413 (request/response too large): a QUERY-SIZE problem, not a
                # provider outage or rate limit. Classify distinctly so the caller
                # reduces the block range — never retried (an identical large query
                # fails the same way) and never arms a provider backoff. The message
                # carries the host only (no URL/key) for string-based classification.
                if exc.code == 413:
                    raise RpcRequestTooLargeError(
                        f'request_too_large:HTTP Error 413 (host {_host_of(self.rpc_url)})'
                    ) from exc
                # Retry transient/rate-limit responses with exponential backoff. On a
                # 429 we respect the provider's Retry-After header when present so
                # retries never compound the rate limit.
                if exc.code in (429, 500, 502, 503, 504) and attempt < max_attempts - 1:
                    sleep_for = _retry_after_seconds(exc, backoff) if exc.code == 429 else backoff
                    time.sleep(sleep_for)
                    backoff = min(30.0, backoff * 2)
                    continue
                if exc.code == 429:
                    # Final 429 (retries exhausted): arm a PER-HOST provider backoff so
                    # later cycles/probes skip THIS provider instead of compounding the
                    # rate limit, while other configured providers keep serving. Honors a
                    # (larger) Retry-After when present.
                    record_rpc_rate_limited(_retry_after_for_backoff(exc), host=_host_of(self.rpc_url))
                raise
        return None  # unreachable


@dataclass
class FailoverJsonRpcClient:
    """Try each configured provider in order with PER-HOST failover.

    A provider whose host is in an active 429 backoff window is skipped (so a
    rate-limited Alchemy is not re-dialed during its window). On a successful call the
    serving provider becomes sticky (``active_index``) and its host backoff is cleared;
    on a 429 the underlying ``JsonRpcClient`` arms that host's backoff and we move to the
    next provider. An HTTP 413 (``RpcRequestTooLargeError``) is a query-size problem,
    NOT a provider outage: the host is never benched, never counted toward
    ``all_rpc_providers_unavailable``, and the failover snapshot is left untouched so
    System Health keeps reporting the provider reachable — the size error is re-raised
    so the caller reduces the block range. When every provider is skipped or fails for
    a non-size reason, raises ``all_rpc_providers_unavailable``. Only provider hosts are
    ever logged — never the URL path, key, or credentials.
    """

    rpc_urls: list[str]
    active_index: int = 0
    active_host: str | None = field(default=None, init=False)
    _logged_failover: bool = field(default=False, init=False)

    def call(self, method: str, params: list[Any]) -> Any:
        if not self.rpc_urls:
            raise RuntimeError('all_rpc_providers_unavailable:rpc_url_not_configured')
        count = len(self.rpc_urls)
        errors: list[str] = []
        failed_hosts: list[str] = []
        skipped_hosts: list[str] = []
        too_large_errors: list[str] = []   # hosts that returned HTTP 413 (query too large)
        for offset in range(count):
            index = (self.active_index + offset) % count
            url = self.rpc_urls[index]
            host = _host_of(url)
            # Skip an administratively disabled route (known-invalid endpoint, e.g. a
            # TLS-broken QuickNode host). NO network attempt is made — a broken route is
            # never re-dialed every cycle. Always skipped, even when it is the only
            # route (an operator disabled it deliberately; recovery is re-enabling it).
            if is_rpc_route_disabled(host):
                if host not in skipped_hosts:
                    skipped_hosts.append(host)
                _log_rpc_call_skipped('disabled_route', host, method)
                continue
            # Skip a provider whose 429 backoff window is still open. Only when more than
            # one provider exists — a lone provider is always tried so its own recovery
            # is detected rather than being benched forever. NO network attempt is made,
            # so this is logged as a skip (network_attempted=false), never a failure.
            if count > 1 and host_backoff_active(host):
                if host not in skipped_hosts:
                    skipped_hosts.append(host)
                _log_rpc_call_skipped('existing_backoff', host, method)
                continue
            try:
                result = JsonRpcClient(url).call(method, params)
            except RpcRequestTooLargeError as exc:
                # 413: the query is too large for this provider. Do NOT bench the host
                # and do NOT count it toward all_providers_unavailable. Remember it and
                # try the remaining providers (one may have a higher limit); if none
                # succeed we re-raise a size error so the caller reduces the range.
                too_large_errors.append(str(exc)[:160] or exc.__class__.__name__)
                continue
            except Exception as exc:
                errors.append(str(exc)[:160] or exc.__class__.__name__)
                if host not in failed_hosts:
                    failed_hosts.append(host)
                continue
            self.active_index = index
            self.active_host = host
            failover_used = bool(offset != 0 or failed_hosts or skipped_hosts)
            record_rpc_provider_success(
                host, provider_count=count, failed_hosts=failed_hosts, failover_used=failover_used,
            )
            # Log the failover at most once per client instance so a multi-call poll
            # cycle does not flood the log. Host-only — never the URL/key.
            if failover_used and not self._logged_failover:
                self._logged_failover = True
                logger.warning(
                    'rpc_failover rpc_provider_count=%s active_rpc_host=%s failed_rpc_hosts=%s '
                    'backoff_hosts=%s rpc_failover_used=true',
                    count, host, ','.join(failed_hosts + skipped_hosts) or 'none',
                    ','.join(backoff_hosts()) or 'none',
                )
            return result
        # No provider returned a result this call.
        if too_large_errors:
            # At least one provider rejected the request as too large and none answered.
            # Recoverable by reducing the block range — re-raise a size error instead of
            # marking providers unavailable. The failover snapshot is intentionally left
            # untouched (no _record_failover_unavailable) so a reachable provider is not
            # reported as a generic outage.
            logger.warning(
                'rpc_query_too_large_all_providers rpc_provider_count=%s active_rpc_host=%s '
                'too_large_count=%s failed_rpc_hosts=%s backoff_hosts=%s '
                'error_class=request_too_large status_reason=query_too_large',
                count, self.active_host or 'none', len(too_large_errors),
                ','.join(failed_hosts + skipped_hosts) or 'none',
                ','.join(backoff_hosts()) or 'none',
            )
            raise RpcRequestTooLargeError(f"request_too_large:{','.join(too_large_errors)}")
        # Every provider was skipped (in backoff) or failed this call.
        _record_failover_unavailable(count, failed_hosts or skipped_hosts)
        logger.error(
            'rpc_all_providers_unavailable rpc_provider_count=%s active_rpc_host=none '
            'failed_rpc_hosts=%s backoff_hosts=%s rpc_failover_used=true',
            count, ','.join(failed_hosts + skipped_hosts) or 'none',
            ','.join(backoff_hosts()) or 'none',
        )
        raise RuntimeError(f"all_rpc_providers_unavailable:{','.join(errors) or 'all_hosts_in_backoff'}")


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


def _normalize_market_observation(item: dict[str, Any], *, provider_name: str, asset_identifier: str, now: datetime) -> dict[str, Any]:
    observed_at = str(item.get('observed_at') or now.isoformat())
    try:
        parsed_observed_at = datetime.fromisoformat(observed_at.replace('Z', '+00:00'))
        freshness_seconds = max(0, int((now - parsed_observed_at).total_seconds()))
    except Exception:
        freshness_seconds = int(item.get('freshness_seconds') or 0)
    return {
        'provider_name': str(item.get('provider_name') or provider_name),
        'asset_identifier': str(item.get('asset_identifier') or asset_identifier or ''),
        'observed_at': observed_at,
        'venue_distribution': item.get('venue_distribution') if isinstance(item.get('venue_distribution'), dict) else {},
        'route_distribution': item.get('route_distribution') if isinstance(item.get('route_distribution'), dict) else {},
        'rolling_volume': float(item.get('rolling_volume') or 0.0),
        'rolling_transfer_count': int(item.get('rolling_transfer_count') or item.get('transfer_count') or 0),
        'unique_counterparties': int(item.get('unique_counterparties') or 0),
        'concentration_ratio': float(item.get('concentration_ratio') or 0.0),
        'abnormal_outflow_ratio': float(item.get('abnormal_outflow_ratio') or 0.0),
        'burst_score': float(item.get('burst_score') or 0.0),
        'provider_status': str(item.get('provider_status') or item.get('status') or 'insufficient_real_evidence'),
        'status': str(item.get('status') or 'insufficient_real_evidence'),
        'freshness_seconds': freshness_seconds,
        'telemetry_kind': str(item.get('telemetry_kind') or 'external_market'),
        'observation_kind': 'real_external_market_observation' if str(item.get('status') or '').lower() == 'ok' else 'external_market_observation_unusable',
        'provenance': item.get('provenance') if isinstance(item.get('provenance'), dict) else {'provider_layer': 'evm_activity_provider'},
    }


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
    _value_wei = _hex_to_int(tx.get('value')) or 0
    return {
        'chain_id': chain_id,
        'chain_network': network,
        'block_number': block_number,
        'block_hash': block_hash,
        'tx_hash': tx_hash,
        'from': str(tx.get('from') or '').lower() or None,
        'to': str(tx.get('to') or '').lower() or None,
        # Canonical *_address aliases persisted alongside from/to so a wallet-transfer
        # telemetry row carries explicit from_address/to_address (the fields the
        # customer-facing telemetry view and downstream evidence readers look up).
        # Additive: existing readers keep using from/to.
        'from_address': str(tx.get('from') or '').lower() or None,
        'to_address': str(tx.get('to') or '').lower() or None,
        'amount': str(_value_wei),
        'value_wei': _value_wei,
        'value_eth': round(_value_wei / 10 ** 18, 18),
        'function_selector': selector,
        'decoded_function_name': SELECTOR_NAMES.get(selector or '', None),
        'decode_status': 'decoded' if SELECTOR_NAMES.get(selector or '') else ('partial' if selector else 'none'),
        'raw_reference': raw_reference,
        'contract_address': str(target.get('contract_identifier') or '').lower() or None,
        'asset_address': None,
        'asset_symbol': str(target.get('asset_symbol') or (target.get('asset_context') or {}).get('asset_symbol') or '') or None,
        'asset_context': _asset_context_from_target(target),
        'event_type': 'transaction',
        'observed_at': None,
    }


def _asset_context_from_target(target: dict[str, Any]) -> dict[str, Any]:
    context = target.get('asset_context') if isinstance(target.get('asset_context'), dict) else target
    return {
        'asset_id': context.get('asset_id') or context.get('id') or target.get('asset_id'),
        'asset_identifier': context.get('asset_identifier') or context.get('identifier') or target.get('asset_identifier'),
        'asset_symbol': context.get('asset_symbol') or target.get('asset_symbol'),
        'token_contract_address': context.get('token_contract_address') or target.get('token_contract_address') or target.get('contract_identifier'),
        'token_name': context.get('token_name'),
        'token_decimals': context.get('token_decimals'),
        'token_standard': context.get('token_standard'),
        'chainlink_feeds': context.get('chainlink_feeds') if isinstance(context.get('chainlink_feeds'), list) else [],
        'treasury_ops_wallets': context.get('treasury_ops_wallets') if isinstance(context.get('treasury_ops_wallets'), list) else [],
        'custody_wallets': context.get('custody_wallets') if isinstance(context.get('custody_wallets'), list) else [],
        'expected_counterparties': context.get('expected_counterparties') if isinstance(context.get('expected_counterparties'), list) else [],
        'venue_labels': context.get('venue_labels') if isinstance(context.get('venue_labels'), list) else [],
    }


def _fetch_logs(client: RpcClient, address: str, from_block: int, to_block: int) -> list[dict[str, Any]]:
    """Fetch ERC-20 Transfer/Approval logs where a monitored WALLET is the indexed
    from/to party (topics filter), across two calls (inbound + outbound)."""
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


def _fetch_contract_logs(client: RpcClient, address: str, from_block: int, to_block: int) -> list[dict[str, Any]]:
    """Fetch ERC-20 Transfer/Approval logs EMITTED BY a monitored contract address.

    Unlike :func:`_fetch_logs` (which filters by a monitored WALLET appearing in a
    Transfer's indexed from/to topics), this filters by the log's ``address`` field —
    i.e. every Transfer/Approval event the monitored contract itself emits. This is the
    canonical way to observe a monitored token contract's on-chain activity: ERC-20
    transfers appear in receipt logs keyed by the emitting token contract, NOT only in
    transactions whose ``to`` is the contract. Router/DEX-mediated transfers (tx.to is a
    router, not the token) are therefore captured here even though the block-by-block
    ``tx.to == contract`` scan misses them. A single eth_getLogs call covers both
    Transfer and Approval via the OR topic filter, so the adaptive halving/413 handling
    in :func:`_fetch_wallet_logs_adaptive` still applies unchanged.
    """
    params = [{
        'fromBlock': hex(from_block),
        'toBlock': hex(to_block),
        'address': address,
        'topics': [[TRANSFER_TOPIC, APPROVAL_TOPIC]],
    }]
    logs = client.call('eth_getLogs', params) or []
    seen: dict[str, dict[str, Any]] = {}
    for log in logs:
        key = f"{log.get('transactionHash')}:{log.get('logIndex')}"
        seen[key] = log
    return list(seen.values())


def _iter_block_ranges(from_block: int, to_block: int, chunk_size: int) -> list[tuple[int, int]]:
    ranges: list[tuple[int, int]] = []
    cursor = from_block
    chunk_size = max(1, chunk_size)
    while cursor <= to_block:
        end = min(to_block, cursor + chunk_size - 1)
        ranges.append((cursor, end))
        cursor = end + 1
    return ranges


def _http_status_from_exc(exc: Exception) -> int | None:
    """Best-effort HTTP status for an RPC error (no secret read).

    Handles a real ``HTTPError``, our ``RpcRequestTooLargeError`` (→ 413), and a
    failover/stub ``RuntimeError`` whose message embeds ``HTTP Error NNN`` (so
    injected unit-test stubs that raise ``RuntimeError('HTTP Error 413: ...')`` are
    classified too). 413 is checked first so a combined message still reduces size.
    """
    if isinstance(exc, RpcRequestTooLargeError):
        return 413
    if isinstance(exc, _urllib_error.HTTPError):
        return int(getattr(exc, 'code', 0)) or None
    text = str(exc)
    for code in (413, 429, 400):
        for sentinel in (f'HTTP Error {code}', f'status {code}', f'code {code}'):
            if sentinel in text:
                return code
    return None


def _wallet_logs_block_range(network: str, default_chunk: int) -> tuple[int, int]:
    """(max, min) block range per eth_getLogs request.

    Base caps the per-request range at ``BASE_MAX_LOGS_BLOCK_RANGE`` (default 25) and
    never drops below ``BASE_MIN_LOGS_BLOCK_RANGE`` (default 5), DECOUPLED from the
    block-by-block scan chunk so a heavy/busy Base wallet never issues a 1000-block
    eth_getLogs request that providers reject with HTTP 413. The conservative 25-block
    default keeps QuickNode under its response-size limit on the first attempt; the
    adaptive halving in :func:`_fetch_wallet_logs_adaptive` reduces further toward the
    5-block minimum when a busy wallet still returns 413. Non-Base chains keep the
    historical single-size behavior (no adaptive halving).
    """
    def _int_env(name: str, fallback: int) -> int:
        try:
            return max(1, int(os.getenv(name, str(fallback))))
        except (TypeError, ValueError):
            return fallback

    if network in {'base', 'base-mainnet'}:
        max_range = _int_env('BASE_MAX_LOGS_BLOCK_RANGE', 25)
        min_range = min(_int_env('BASE_MIN_LOGS_BLOCK_RANGE', 5), max_range)
        return max_range, min_range
    size = max(1, default_chunk)
    return size, size


def _provider_host_for_log(client: RpcClient) -> str:
    """Best-effort provider host for structured logs (failover active_host, else unknown)."""
    host = getattr(client, 'active_host', None)
    return str(host) if host else 'unknown'


def _fetch_wallet_logs_adaptive(
    client: RpcClient,
    address: str,
    from_block: int,
    to_block: int,
    *,
    network: str,
    target_id: Any,
    max_range: int,
    min_range: int,
    logs_fetcher: Any = None,
    budget: 'PollBudget | None' = None,
    max_blocks_ceiling: int | None = None,
) -> dict[str, Any]:
    """Fetch ERC-20 transfer/approval logs, halving the block range on HTTP 413.

    Scans ``[from_block, to_block]`` in chunks of at most ``max_range`` blocks. When a
    chunk is rejected as too large (HTTP 413 / ``RpcRequestTooLargeError``) the range
    is halved and retried, down to ``min_range`` blocks — so a single oversized query
    reduces the scan window instead of failing the whole poll, and the provider is
    never marked unavailable for a 413. Any non-413 failure (429/400/unreachable)
    stops the log scan for this cycle (the block-by-block scan still runs), preserving
    prior behavior.

    ``logs_fetcher`` selects the per-chunk eth_getLogs call: :func:`_fetch_logs` (the
    default) filters by a monitored WALLET in the Transfer topics; pass
    :func:`_fetch_contract_logs` to filter by the emitting CONTRACT ``address`` instead.
    Both share the identical adaptive halving / 413 / cursor-capping behavior.

    Returns a dict: ``logs``, ``last_complete_block`` (highest block fully covered by a
    SUCCESSFUL eth_getLogs scan; ``from_block - 1`` if even the first chunk failed),
    ``status`` (``ok``/``degraded``/``failed``), ``error_count``, ``too_large_count``,
    and ``min_chunk_size`` (smallest chunk size attempted).
    """
    _logs_fetcher = logs_fetcher or _fetch_logs
    logs: list[dict[str, Any]] = []
    last_complete = from_block - 1
    status = 'ok'
    error_count = 0
    too_large_count = 0
    min_chunk_size = max_range
    logged_failure = False
    budget_stopped = False
    # Stack of (lo, hi) ranges to scan; ascending lo pops first so last_complete
    # advances monotonically and a failed chunk never skips earlier unscanned blocks.
    pending: list[tuple[int, int]] = list(reversed(_iter_block_ranges(from_block, to_block, max_range)))
    while pending:
        lo, hi = pending.pop()
        span = hi - lo + 1
        try:
            # Section 1: defensive invariant checked immediately BEFORE the eth_getLogs
            # leaves the process — the actual queried range must never exceed the
            # per-cycle block ceiling. Raises ScanRangeInvariantError (RPC not issued) on
            # violation so an oversized query can never reach the provider.
            if max_blocks_ceiling is not None:
                _assert_getlogs_range_within_budget(lo, hi, max_blocks_ceiling, target_id=target_id)
            _chunk_logs = _logs_fetcher(client, address, lo, hi)
            # Section 6: if a chunk alone would blow the per-cycle LOG budget, split it
            # into smaller sub-ranges (down to a single block) instead of loading an
            # unbounded response into memory. A single block that still exceeds the budget
            # marks the poll degraded and stops (cursor held at the last complete block).
            if budget is not None:
                _remaining_logs = budget.max_logs - budget.logs_received
                if len(_chunk_logs) > _remaining_logs:
                    if span > 1:
                        _sub = max(1, span // 2)
                        logger.warning(
                            'rpc_log_chunk_split target_id=%s chain=%s chunk_from_block=%s '
                            'chunk_to_block=%s chunk_logs=%s remaining_log_budget=%s '
                            'reduced_chunk_size=%s action=split_before_load',
                            target_id, network, lo, hi, len(_chunk_logs), _remaining_logs, _sub,
                        )
                        for srange in reversed(_iter_block_ranges(lo, hi, _sub)):
                            pending.append(srange)
                        continue
                    # A single block still exceeds the remaining log budget: do NOT load it,
                    # mark the poll partial, and stop with the cursor at the last complete
                    # block (this block is re-scanned next cycle). Section 5/6.
                    logger.warning(
                        'monitoring_poll_log_budget_exhausted target_id=%s chain=%s '
                        'chunk_from_block=%s chunk_to_block=%s chunk_logs=%s '
                        'logs_received=%s max_logs_per_target_per_cycle=%s '
                        'action=stop_persist_partial',
                        target_id, network, lo, hi, len(_chunk_logs),
                        budget.logs_received, budget.max_logs,
                    )
                    budget.exhausted_reason = 'log_budget'
                    budget.exhausted_event = 'monitoring_poll_log_budget_exhausted'
                    status = 'degraded' if status == 'ok' else status
                    budget_stopped = True
                    break
                budget.logs_received += len(_chunk_logs)
            logs.extend(_chunk_logs)
            last_complete = hi
            # Stop cleanly once the cumulative log budget is fully consumed. Section 5.
            if budget is not None and budget.logs_received >= budget.max_logs:
                logger.warning(
                    'monitoring_poll_log_budget_exhausted target_id=%s chain=%s '
                    'logs_received=%s max_logs_per_target_per_cycle=%s last_complete_block=%s '
                    'action=stop_persist_partial',
                    target_id, network, budget.logs_received, budget.max_logs, last_complete,
                )
                budget.exhausted_reason = 'log_budget'
                budget.exhausted_event = 'monitoring_poll_log_budget_exhausted'
                status = 'degraded' if status == 'ok' else status
                budget_stopped = True
                break
            continue
        except (PollBudgetExhausted, RpcCircuitBreakerTripped, ScanRangeInvariantError):
            # Control-flow signals (budget exhausted / breaker open / range invariant):
            # never swallowed as a logs-fetch failure — propagate so the poll stops
            # cleanly with the last fully-scanned block held as the cursor.
            raise
        except Exception as exc:
            code = _http_status_from_exc(exc)
            host = _provider_host_for_log(client)
            if code == 413:
                too_large_count += 1
                if span > min_range:
                    new_size = max(min_range, span // 2)
                    min_chunk_size = min(min_chunk_size, new_size)
                    logger.warning(
                        'rpc_query_too_large target_id=%s chain=%s provider_host=%s '
                        'original_from_block=%s original_to_block=%s reduced_chunk_size=%s '
                        'chunk_from_block=%s chunk_to_block=%s retry_count=%s '
                        'error_class=request_too_large status_reason=query_too_large '
                        'message="RPC query was too large. Reducing scan window."',
                        target_id, network, host,
                        lo, hi, new_size, lo, hi, too_large_count,
                    )
                    record_rpc_query_too_large(host, reduced_chunk_size=new_size)
                    # Re-queue the smaller sub-ranges (push reversed so the lowest lo pops first).
                    for srange in reversed(_iter_block_ranges(lo, hi, new_size)):
                        pending.append(srange)
                    continue
                # Already at the minimum range and still too large: stop here so the
                # cursor is NOT advanced past these unscanned blocks. Provider stays
                # usable (no long backoff); the block-by-block scan still runs.
                status = 'degraded'
                min_chunk_size = min(min_chunk_size, span)
                record_rpc_query_too_large(host, reduced_chunk_size=min_range)
                if not logged_failure:
                    logged_failure = True
                    logger.warning(
                        'rpc_query_too_large_min_reached target_id=%s chain=%s provider_host=%s '
                        'chunk_from_block=%s chunk_to_block=%s min_chunk_size=%s retry_count=%s '
                        'error_class=request_too_large status_reason=query_too_large '
                        'action=stop_logs_cursor_capped',
                        target_id, network, host, lo, hi, min_range, too_large_count,
                    )
                break
            # Non-413 failure: log once (host/status only) and stop the log scan for
            # this cycle. The block-by-block scan below still detects native transfers.
            error_count += 1
            if status == 'ok':
                status = 'failed'
            if not logged_failure:
                logged_failure = True
                logger.warning(
                    'evm_logs_fetch_failed target_id=%s chain=%s from_block=%s to_block=%s '
                    'error_type=%s http_status=%s error=%s action=continue_with_block_scan',
                    target_id, network, lo, hi,
                    type(exc).__name__, code, str(exc)[:300],
                )
                if code == 400 and isinstance(exc, _urllib_error.HTTPError):
                    try:
                        _body = exc.read().decode('utf-8', errors='replace')[:500]
                        if _body:
                            logger.warning(
                                'evm_logs_fetch_failed_400_body target_id=%s chain=%s http_body=%s',
                                target_id, network, _body,
                            )
                    except Exception:
                        pass
            break
    # A full, un-reduced scan succeeded → clear the process-wide query-too-large
    # signal so System Health stops reporting a reduced scan window.
    if too_large_count == 0 and status == 'ok':
        clear_rpc_query_too_large()
    return {
        'logs': logs,
        'last_complete_block': last_complete,
        'status': status,
        'error_count': error_count,
        'too_large_count': too_large_count,
        'min_chunk_size': min_chunk_size,
        'budget_stopped': budget_stopped,
    }


async def _ws_subscribe_new_head(ws_url: str, timeout_seconds: float = 1.0) -> int | None:
    try:
        import websockets
    except Exception:
        return None
    try:
        async with websockets.connect(ws_url, ping_interval=20, open_timeout=3) as socket:
            await socket.send(json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'eth_subscribe', 'params': ['newHeads']}))
            _ = await asyncio.wait_for(socket.recv(), timeout=timeout_seconds)
            payload_raw = await asyncio.wait_for(socket.recv(), timeout=timeout_seconds)
            payload = json.loads(payload_raw)
            params = payload.get('params') if isinstance(payload, dict) else {}
            result = params.get('result') if isinstance(params, dict) else {}
            head_number = result.get('number') if isinstance(result, dict) else None
            if isinstance(head_number, str):
                return _hex_to_int(head_number)
    except Exception:
        return None
    return None


# ===========================================================================
# Production RPC-safety guards (Datto USDC scanner runaway fix)
# ---------------------------------------------------------------------------
# A restored Base USDC *contract* target with no cursor scanned a 2,001-block
# range in its first health poll (safe_backfill_window=2000 bypassed the
# 25-block cap, which was only applied to the cursor-based catch-up path), then
# issued ~1 eth_getTransactionByHash per Transfer log (119,163 logs → an
# eth_getTransactionByHash storm) and spiked Alchemy CU. These guards make the
# runaway structurally impossible: a hard per-poll budget, a bounded live-tail
# start for new targets, a defensive range invariant checked immediately before
# every eth_getLogs, local ERC-20 decoding (no per-log tx lookup), and a
# process-wide RPC circuit breaker that works even if a future logic bug
# bypasses the per-target budgets.
# ===========================================================================


class ScanRangeInvariantError(RuntimeError):
    """A planned eth_getLogs range exceeded ``max_blocks_per_cycle``.

    Raised by :func:`_assert_getlogs_range_within_budget` immediately BEFORE the
    RPC leaves the process, so the oversized query is never issued. The poll is
    marked a bounded failure, the durable cursor is left unchanged, and the range
    is retried (bounded) next cycle. This is the last-line defense for the exact
    contradiction the incident logged: max_blocks_per_cycle=25 yet a 2,001-block
    contract log query.
    """

    def __init__(self, requested_blocks: int, max_blocks: int) -> None:
        self.requested_blocks = int(requested_blocks)
        self.max_blocks = int(max_blocks)
        super().__init__(
            f'scan_range_invariant_failed requested_blocks={requested_blocks} '
            f'max_blocks_per_cycle={max_blocks}'
        )


class PollBudgetExhausted(RuntimeError):
    """A hard per-poll safety budget (Section 5) was exhausted.

    Carries the canonical ``event`` name (e.g. monitoring_poll_rpc_budget_exhausted)
    and a short ``reason`` so the poll stops cleanly, persists a partial/degraded
    terminal status, keeps the last fully-completed cursor, and schedules a
    continuation instead of loading an unbounded response into memory.
    """

    def __init__(self, reason: str, event: str) -> None:
        self.reason = str(reason)
        self.event = str(event)
        super().__init__(f'{event} reason={reason}')


class RpcCircuitBreakerTripped(RuntimeError):
    """The process-wide RPC rate ceiling (Section 12) was reached this window.

    A production-safe kill switch independent of the per-target budgets: once the
    process issues MONITORING_RPC_MAX_CALLS_PER_MINUTE calls in a rolling minute,
    new RPC work stops (persistence and heartbeat writes still proceed) until the
    next window. Works even if a future logic bug bypasses the per-target budget.
    """


# --- Process-wide RPC circuit breaker (Section 12) -------------------------
# A single rolling-minute counter shared by every scheduled monitoring poll in
# this process. Disabled (limit 0) unless MONITORING_RPC_MAX_CALLS_PER_MINUTE is
# configured — the canary/production plan sets it explicitly (200 prod, 60
# canary) so it never surprises an existing deployment, and the per-target
# budgets (always on) remain the primary bound.
_RPC_CIRCUIT_LOCK = threading.Lock()
_RPC_CIRCUIT_STATE: dict[str, Any] = {'window_start': 0.0, 'count': 0, 'tripped': False}


def monitoring_rpc_max_calls_per_minute() -> int:
    """Process-wide RPC ceiling per rolling minute. 0 (default) disables the breaker."""
    try:
        return max(0, int(os.getenv('MONITORING_RPC_MAX_CALLS_PER_MINUTE', '0')))
    except (TypeError, ValueError):
        return 0


def reset_rpc_circuit_breaker() -> None:
    """Reset the process-wide breaker window (used by tests and worker startup)."""
    with _RPC_CIRCUIT_LOCK:
        _RPC_CIRCUIT_STATE['window_start'] = 0.0
        _RPC_CIRCUIT_STATE['count'] = 0
        _RPC_CIRCUIT_STATE['tripped'] = False


def rpc_circuit_breaker_snapshot() -> dict[str, Any]:
    """Read-only breaker state for status/logging (limit, count, tripped)."""
    limit = monitoring_rpc_max_calls_per_minute()
    with _RPC_CIRCUIT_LOCK:
        return {
            'limit': limit,
            'count': int(_RPC_CIRCUIT_STATE['count']),
            'tripped': bool(_RPC_CIRCUIT_STATE['tripped']),
            'enabled': limit > 0,
        }


def _rpc_circuit_breaker_admit(*, now: float | None = None) -> bool:
    """Admit ONE RPC call against the process-wide per-minute ceiling.

    Increments the rolling-minute counter and returns ``True`` when the call may
    proceed. Returns ``False`` once the ceiling is reached (caller must stop new
    RPC work). A single state-transition event is logged when the breaker trips
    and when it recovers into a fresh window. Limit 0 always admits.
    """
    limit = monitoring_rpc_max_calls_per_minute()
    if limit <= 0:
        return True
    _now = time.monotonic() if now is None else now
    with _RPC_CIRCUIT_LOCK:
        if _now - float(_RPC_CIRCUIT_STATE['window_start']) >= 60.0:
            # New rolling window: reset the counter and log recovery once.
            if _RPC_CIRCUIT_STATE['tripped']:
                logger.warning(
                    'monitoring_rpc_circuit_breaker_reset limit=%s previous_window_count=%s '
                    'state_transition=tripped_to_closed action=resume_rpc_work',
                    limit, _RPC_CIRCUIT_STATE['count'],
                )
            _RPC_CIRCUIT_STATE['window_start'] = _now
            _RPC_CIRCUIT_STATE['count'] = 0
            _RPC_CIRCUIT_STATE['tripped'] = False
        if int(_RPC_CIRCUIT_STATE['count']) >= limit:
            if not _RPC_CIRCUIT_STATE['tripped']:
                _RPC_CIRCUIT_STATE['tripped'] = True
                logger.error(
                    'monitoring_rpc_circuit_breaker_open limit=%s window_count=%s '
                    'state_transition=closed_to_tripped action=stop_new_rpc_work',
                    limit, _RPC_CIRCUIT_STATE['count'],
                )
            return False
        _RPC_CIRCUIT_STATE['count'] = int(_RPC_CIRCUIT_STATE['count']) + 1
        return True


def _budget_int_env(name: str, default: int, *, minimum: int = 0) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


@dataclass
class PollBudget:
    """Hard per-target-per-cycle ceilings (Section 5) — ceilings, not hints.

    Read once at poll start with conservative production defaults. Before every
    RPC the poll verifies the remaining budget; when any dimension is exhausted the
    poll stops cleanly (:class:`PollBudgetExhausted`), persists a partial/degraded
    status, keeps the last fully-completed cursor (never advancing over unprocessed
    logs), writes heartbeat/poll result, schedules a continuation, and logs the
    exact budget reason. The circuit breaker is checked here too so a single
    chokepoint enforces both the per-target budget and the process-wide ceiling.
    """

    max_blocks: int = 25
    max_logs: int = 2000
    max_tx_enrichments: int = 25
    max_rpc_calls: int = 100
    max_duration_seconds: float = 45.0
    max_log_query_chunk_blocks: int = 5
    rpc_calls_used: int = 0
    blocks_scanned: int = 0
    logs_received: int = 0
    logs_processed: int = 0
    transaction_enrichments: int = 0
    started_at: float = field(default_factory=time.monotonic)
    exhausted_event: str | None = None
    exhausted_reason: str | None = None

    @property
    def elapsed_seconds(self) -> float:
        return round(time.monotonic() - self.started_at, 3)

    def as_dict(self) -> dict[str, Any]:
        return {
            'rpc_calls_used': self.rpc_calls_used,
            'blocks_scanned': self.blocks_scanned,
            'logs_received': self.logs_received,
            'logs_processed': self.logs_processed,
            'transaction_enrichments': self.transaction_enrichments,
            'elapsed_seconds': self.elapsed_seconds,
            'max_blocks': self.max_blocks,
            'max_logs': self.max_logs,
            'max_tx_enrichments': self.max_tx_enrichments,
            'max_rpc_calls': self.max_rpc_calls,
            'max_duration_seconds': self.max_duration_seconds,
            'exhausted_reason': self.exhausted_reason,
        }

    def _stop(self, reason: str, event: str) -> None:
        self.exhausted_reason = reason
        self.exhausted_event = event
        raise PollBudgetExhausted(reason, event)

    def check_time(self, *, target_id: Any) -> None:
        if self.elapsed_seconds >= self.max_duration_seconds:
            logger.warning(
                'monitoring_poll_time_budget_exhausted target_id=%s elapsed_seconds=%s '
                'max_poll_duration_seconds=%s action=stop_persist_partial',
                target_id, self.elapsed_seconds, self.max_duration_seconds,
            )
            self._stop('time_budget', 'monitoring_poll_time_budget_exhausted')

    def before_rpc(self, *, target_id: Any) -> None:
        """Verify budget + circuit breaker immediately before an RPC leaves the process."""
        self.check_time(target_id=target_id)
        if self.rpc_calls_used >= self.max_rpc_calls:
            logger.warning(
                'monitoring_poll_rpc_budget_exhausted target_id=%s rpc_calls_used=%s '
                'max_rpc_calls_per_target_per_cycle=%s action=stop_persist_partial',
                target_id, self.rpc_calls_used, self.max_rpc_calls,
            )
            self._stop('rpc_budget', 'monitoring_poll_rpc_budget_exhausted')
        if not _rpc_circuit_breaker_admit():
            snap = rpc_circuit_breaker_snapshot()
            logger.error(
                'monitoring_poll_rpc_circuit_breaker target_id=%s process_rpc_calls_this_minute=%s '
                'limit=%s action=stop_persist_partial',
                target_id, snap['count'], snap['limit'],
            )
            self.exhausted_reason = 'circuit_breaker'
            self.exhausted_event = 'monitoring_poll_rpc_circuit_breaker'
            raise RpcCircuitBreakerTripped(
                f'circuit_breaker_open limit={snap["limit"]} count={snap["count"]}'
            )
        self.rpc_calls_used += 1

    def note_logs(self, count: int, *, target_id: Any) -> None:
        self.logs_received += max(0, int(count))
        if self.logs_received > self.max_logs:
            logger.warning(
                'monitoring_poll_log_budget_exhausted target_id=%s logs_received=%s '
                'max_logs_per_target_per_cycle=%s action=stop_persist_partial',
                target_id, self.logs_received, self.max_logs,
            )
            self._stop('log_budget', 'monitoring_poll_log_budget_exhausted')

    def can_enrich(self) -> bool:
        return self.transaction_enrichments < self.max_tx_enrichments


def load_poll_budget() -> PollBudget:
    """Build a :class:`PollBudget` from env with conservative production defaults."""
    return PollBudget(
        max_blocks=_budget_int_env('MAX_BLOCKS_PER_TARGET_PER_CYCLE', 25, minimum=1),
        max_logs=_budget_int_env('MAX_LOGS_PER_TARGET_PER_CYCLE', 2000, minimum=1),
        max_tx_enrichments=_budget_int_env('MAX_TX_ENRICHMENTS_PER_TARGET_PER_CYCLE', 25, minimum=0),
        max_rpc_calls=_budget_int_env('MAX_RPC_CALLS_PER_TARGET_PER_CYCLE', 100, minimum=1),
        max_duration_seconds=float(_budget_int_env('MAX_POLL_DURATION_SECONDS', 45, minimum=1)),
        max_log_query_chunk_blocks=_budget_int_env('MAX_LOG_QUERY_CHUNK_BLOCKS', 5, minimum=1),
    )


def initial_live_tail_blocks() -> int:
    """Section 2: blocks scanned for a target with NO cursor.

    A new contract target must not backfill thousands of blocks in its first
    normal health poll. Default 10 recent blocks, hard-capped at 25 (never wider
    than one bounded chunk beyond the live head).
    """
    return min(25, _budget_int_env('INITIAL_LIVE_TAIL_BLOCKS', 10, minimum=1))


def historical_backfill_enabled() -> bool:
    """Section 13: historical backfill is OFF in the polling-only MVP.

    Scheduled polling scans only the recent live tail; deep historical backfill
    must be an explicit operator action with its own RPC budget and cursor.
    """
    return str(os.getenv('HISTORICAL_BACKFILL_ENABLED', 'false')).strip().lower() in {'1', 'true', 'yes', 'on'}


def _assert_getlogs_range_within_budget(
    from_block: int, to_block: int, max_blocks: int, *, target_id: Any,
) -> None:
    """Section 1 invariant, checked immediately before EVERY eth_getLogs call.

    Enforces ``queried_to_block - queried_from_block + 1 <= max_blocks_per_cycle``.
    On violation the RPC is NOT issued — the poll is a bounded failure and the
    durable cursor is left unchanged so the range is retried (bounded) next cycle.
    """
    requested = to_block - from_block + 1
    if requested > max_blocks:
        logger.error(
            'monitoring_scan_range_invariant_failed target_id=%s requested_blocks=%s '
            'max_blocks_per_cycle=%s from_block=%s to_block=%s action=do_not_issue_rpc',
            target_id, requested, max_blocks, from_block, to_block,
        )
        raise ScanRangeInvariantError(requested, max_blocks)


def _decode_transfer_log(log: dict[str, Any]) -> dict[str, Any] | None:
    """Section 3: decode an ERC-20 Transfer/Approval log LOCALLY (no RPC).

    eth_getLogs already returns everything a normalized telemetry row needs —
    address, topics, data, transactionHash, blockNumber, logIndex,
    transactionIndex — so a Transfer/Approval is decoded without an
    eth_getTransactionByHash round-trip:

        topic0  keccak256("Transfer(address,address,uint256)") / Approval(...)
        topic1  from / owner   (indexed address)
        topic2  to   / spender  (indexed address)
        data    uint256 amount / allowance

    Returns ``None`` for a log whose topic0 is neither Transfer nor Approval.
    """
    topics = log.get('topics') or []
    topic0 = str((topics[0] if len(topics) > 0 else '') or '').lower()
    if topic0 not in {TRANSFER_TOPIC, APPROVAL_TOPIC}:
        return None
    is_approval = topic0 == APPROVAL_TOPIC
    return {
        'event_type': 'approval' if is_approval else 'transfer',
        'kind_hint': 'erc20_approval' if is_approval else 'erc20_transfer',
        'contract_address': str(log.get('address') or '').lower() or None,
        'from_address': _topic_to_address(topics[1] if len(topics) > 1 else None),
        'to_address': _topic_to_address(topics[2] if len(topics) > 2 else None),
        'amount': str(_hex_to_int(log.get('data')) or 0),
        'transaction_hash': str(log.get('transactionHash') or ''),
        'block_number': _hex_to_int(log.get('blockNumber')),
        'block_hash': str(log.get('blockHash') or '') or None,
        'log_index': _hex_to_int(log.get('logIndex')),
        'transaction_index': _hex_to_int(log.get('transactionIndex')),
        'topic0': topic0,
    }


def _dedupe_decoded_logs(decoded: list[dict[str, Any]], chain_id: int) -> list[dict[str, Any]]:
    """Section 4: dedupe by (chain_id, transaction_hash, log_index) BEFORE enrichment."""
    seen: set[tuple[int, str, Any]] = set()
    unique: list[dict[str, Any]] = []
    for item in decoded:
        key = (int(chain_id), item.get('transaction_hash') or '', item.get('log_index'))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
    return unique


def _plan_tx_enrichments(decoded: list[dict[str, Any]], *, max_enrichments: int) -> list[str]:
    """Section 4: which transaction hashes to enrich, deduped and capped.

    Only logs explicitly flagged ``requires_enrichment`` (a rule needs a field the
    log does not carry) are considered. Each transaction is enriched at most once
    even when several matching logs share it, and enrichment stops at
    ``max_enrichments``. Ordinary USDC Transfer events (no matching rule) are never
    enriched, so the N+1 eth_getTransactionByHash storm cannot recur.
    """
    seen: set[str] = set()
    plan: list[str] = []
    if max_enrichments <= 0:
        return plan
    for item in decoded:
        if not item.get('requires_enrichment'):
            continue
        tx_hash = str(item.get('transaction_hash') or '')
        if not tx_hash or tx_hash in seen:
            continue
        seen.add(tx_hash)
        plan.append(tx_hash)
        if len(plan) >= max_enrichments:
            break
    return plan


class _BudgetedRpcClient:
    """Wraps an :class:`RpcClient` so every scan-phase RPC is verified against the
    per-target budget + process-wide circuit breaker BEFORE it leaves the process,
    and caller-tagged by method (Section 5/10/12).

    A single chokepoint means the runaway is structurally impossible regardless of
    which scan phase issues the call: once the per-target RPC ceiling or the
    process-wide per-minute ceiling is reached, :meth:`call` raises
    (:class:`PollBudgetExhausted` / :class:`RpcCircuitBreakerTripped`) so the poll
    stops cleanly. Method → caller mapping attributes the load precisely in the
    periodic rpc_request_volume_summary (no more caller=unspecified).
    """

    _CALLER_BY_METHOD = {
        'eth_getlogs': 'scheduled_poll_contract_logs',
        'eth_getblockbynumber': 'scheduled_poll_block_lookup',
        'eth_getblockbyhash': 'scheduled_poll_block_lookup',
        'eth_blocknumber': 'scheduled_poll_block_lookup',
        'eth_gettransactionbyhash': 'scheduled_poll_transaction_enrichment',
        'eth_gettransactionreceipt': 'scheduled_poll_transaction_enrichment',
        'eth_chainid': 'scheduled_poll_provider_check',
        'eth_getcode': 'scheduled_poll_provider_check',
    }

    def __init__(self, inner: RpcClient, budget: PollBudget, target_id: Any) -> None:
        self._inner = inner
        self._budget = budget
        self._target_id = target_id

    @property
    def active_host(self) -> str | None:
        return getattr(self._inner, 'active_host', None)

    def call(self, method: str, params: list[Any]) -> Any:
        self._budget.before_rpc(target_id=self._target_id)
        caller = self._CALLER_BY_METHOD.get(str(method).lower(), 'scheduled_poll_contract_logs')
        with rpc_caller_scope(caller):
            return self._inner.call(method, params)


def fetch_evm_activity(target: dict[str, Any], since_ts: datetime | None, *, rpc_client: RpcClient | None = None) -> list[ActivityEvent]:
    network = str(target.get('chain_network') or 'ethereum').strip().lower()

    # --- Hard skip: chain mismatch (no RPC, no backfill, no coverage) ---
    # A worker configured for chain_id=X must not run ANY RPC work for a target on
    # a different chain. This fires before eth_chainId/eth_blockNumber so a
    # mismatched (e.g. Ethereum) target never touches the Base provider.
    _hard_skip, _t_chain_id, _rpc_chain_id = evaluate_chain_mismatch(network)
    if _hard_skip:
        target['_evm_chain_mismatch'] = True
        target['_evm_chain_mismatch_reason'] = (
            f'chain_mismatch target_chain_id={_t_chain_id} rpc_chain_id={_rpc_chain_id}'
        )
        target['_evm_skip_reason'] = 'chain_mismatch'
        logger.warning(
            'evm_chain_mismatch_hard_skip target_id=%s configured_chain=%s '
            'target_chain_id=%s rpc_chain_id=%s action=hard_skip_no_rpc',
            target.get('id'), network, _t_chain_id, _rpc_chain_id,
        )
        return []

    # --- Skip while the provider is in a 429 backoff window (no RPC) ---
    # A recent HTTP 429 armed a process-wide backoff; skip live polling so we never
    # call eth_blockNumber again and compound the rate limit.
    if rpc_provider_backoff_active():
        _bo = rpc_provider_backoff_status()
        target['_evm_provider_backoff'] = True
        target['_evm_skip_reason'] = 'provider_backoff_active'
        logger.warning(
            'evm_poll_skipped_provider_backoff target_id=%s chain=%s reason=provider_backoff_active '
            'backoff_until=%s backoff_remaining_seconds=%s action=skip_no_rpc',
            target.get('id'), network,
            _bo.get('backoff_until') or 'unknown', int(_bo.get('remaining_seconds') or 0),
        )
        return []

    # Route to the RPC endpoint that serves this target's labeled chain. A single
    # global Base RPC must never silently serve an Ethereum-labeled target.
    _chain_rpc = resolve_chain_rpc(network)
    rpc_url = _chain_rpc['rpc_url']
    rpc_url_env_used = _chain_rpc['rpc_url_env']
    expected_chain_id = _chain_rpc['expected_chain_id']
    if not rpc_url:
        logger.warning(
            'evm_rpc_not_configured target_id=%s configured_chain=%s resolved_chain_id=%s '
            'rpc_url_env_used=%s reason=no_rpc_url_for_chain action=skip',
            target.get('id'), network, expected_chain_id, rpc_url_env_used,
        )
        return []

    client = rpc_client or FailoverJsonRpcClient(_chain_rpc['rpc_urls'])

    _allowed_chains = {item.strip().lower() for item in (os.getenv('LIVE_MONITORING_CHAINS', 'ethereum').split(',')) if item.strip()}
    if network not in _allowed_chains:
        # Network is not operator-allow-listed. Allow it only when we can confirm
        # the RPC serves this network's chain id — either EVM_CHAIN_ID matches it,
        # or an eth_chainId probe matches (auto-detect for chains like Base).
        _configured_chain_id = int(os.getenv('STAGING_EVM_CHAIN_ID') or os.getenv('EVM_CHAIN_ID') or 0) or None
        if not (_configured_chain_id and expected_chain_id and _configured_chain_id == expected_chain_id):
            if not expected_chain_id:
                return []
            try:
                _probed_chain_id = _hex_to_int(client.call('eth_chainId', []))
            except Exception:
                _probed_chain_id = None
            if _probed_chain_id != expected_chain_id:
                logger.error(
                    'evm_chain_rpc_mismatch target_id=%s configured_chain=%s resolved_chain_id=%s '
                    'rpc_chain_id=%s rpc_url_env_used=%s action=skip_no_telemetry '
                    'reason=chain_not_allowlisted_and_rpc_chain_mismatch',
                    target.get('id'), network, expected_chain_id, _probed_chain_id, rpc_url_env_used,
                )
                return []
    elif expected_chain_id is not None:
        # Network IS allow-listed (e.g. ethereum). Still verify the RPC actually
        # serves this chain so an ethereum-labeled target can never be scanned
        # against a Base RPC and have its Base block height written as chain_id=1.
        # Fail closed on a definite mismatch; tolerate an indeterminate probe
        # (None) so injected unit-test clients without eth_chainId still scan.
        try:
            _probed_chain_id = _hex_to_int(client.call('eth_chainId', []))
        except Exception:
            _probed_chain_id = None
        if _probed_chain_id is not None and _probed_chain_id != expected_chain_id:
            logger.error(
                'evm_chain_rpc_mismatch target_id=%s configured_chain=%s resolved_chain_id=%s '
                'rpc_chain_id=%s rpc_url_env_used=%s action=skip_no_telemetry '
                'reason=allowlisted_chain_rpc_serves_different_chain',
                target.get('id'), network, expected_chain_id, _probed_chain_id, rpc_url_env_used,
            )
            # Mark target so the runner can reduce poll frequency / flag as unhealthy
            # without blocking targets on the correct chain.
            target['_evm_chain_mismatch'] = True
            target['_evm_chain_mismatch_reason'] = (
                f'configured_chain={network} expected_chain_id={expected_chain_id} '
                f'rpc_chain_id={_probed_chain_id}'
            )
            return []

    confirmations = max(0, int(os.getenv('EVM_CONFIRMATIONS_REQUIRED', '3')))
    replay_blocks = max(1, int(os.getenv('MONITOR_REPLAY_BLOCKS', os.getenv('EVM_BLOCK_LOOKBACK', '25'))))
    block_scan_chunk = max(1, int(os.getenv('MONITOR_BATCH_BLOCKS', os.getenv('EVM_BLOCK_SCAN_CHUNK_SIZE', '25'))))
    # Per-chain initial backfill when no prior cursor exists.  Base runs ~2 s/block so
    # a 300-second polling interval spans ~150 blocks; use 300 for safety (~10 minutes).
    # MONITOR_SAFE_BACKFILL can raise the window but cannot lower it below the
    # chain-specific minimum — prevents a misconfigured env var from creating gaps.
    _CHAIN_SAFE_BACKFILL: dict[str, int] = {'base': 2000, 'base-mainnet': 2000}
    _CHAIN_MIN_BACKFILL: dict[str, int] = {'base': 2000, 'base-mainnet': 2000}
    safe_backfill_window: int = max(
        replay_blocks,
        _CHAIN_MIN_BACKFILL.get(network, 0),
        int(os.getenv('MONITOR_SAFE_BACKFILL', str(_CHAIN_SAFE_BACKFILL.get(network, max(150, replay_blocks))))),
    )
    target_type = str(target.get('target_type') or '').lower()
    if target_type == 'wallet':
        target_address = resolve_monitored_wallet(target) or ''
        if not target_address:
            logger.error(
                'wallet_address_misconfigured target_id=%s chain=%s '
                'reason=monitored_wallet_not_configured action=fail_closed',
                target.get('id'), network,
            )
            raise MonitoredWalletNotConfigured(str(target.get('id') or ''))
        # Normalize the resolved wallet back onto the target so downstream logs
        # and detection (which read target['wallet_address']) use the real value
        # instead of n/a when the address lived in a fallback location.
        target['wallet_address'] = target_address
    else:
        target_address = str(target.get('wallet_address') or target.get('contract_identifier') or '').lower()
    if not target_address.startswith('0x'):
        return []

    ws_configured = bool((os.getenv('EVM_WS_URL') or '').strip())
    preferred_source = 'polling'
    fallback_source = 'polling'
    latest = None
    if ws_configured:
        latest = asyncio.run(_ws_subscribe_new_head((os.getenv('EVM_WS_URL') or '').strip()))
        if latest is not None:
            preferred_source = 'websocket'
            fallback_source = 'rpc_backfill'
    if latest is None:
        # Section 7/10: the chain-head lookup is the bounded provider-health check
        # (proves connectivity + current block) and is tagged as its own caller so
        # the periodic RPC volume summary attributes it precisely.
        with rpc_caller_scope('scheduled_poll_provider_check'):
            _raw_block_result = client.call('eth_blockNumber', [])
        _raw_block_hex = str(_raw_block_result or '')
        try:
            latest = int(_raw_block_hex, 16)
        except (TypeError, ValueError):
            latest = 0
        logger.info(
            'evm_poll_eth_blockNumber target_id=%s chain=%s source_type=rpc_polling '
            'eth_blockNumber_raw_hex=%s latest_block_decimal=%s observed_at=%s',
            target.get('id'), network,
            _raw_block_hex or '0x0', latest,
            datetime.now(timezone.utc).isoformat(),
        )
        if network in {'base', 'base-mainnet'} and latest > 100_000_000:
            logger.error(
                'invalid_base_block_number source=fetch_evm_activity '
                'target_id=%s chain=%s chain_id=8453 raw_eth_blockNumber_hex=%s '
                'parsed_block_number_decimal=%s action=zero_out',
                target.get('id'), network, _raw_block_hex, latest,
            )
            latest = 0
        elif latest > 500_000_000:
            logger.error(
                'code=ETH_BLOCK_NUMBER_TIMESTAMP_RANGE source=fetch_evm_activity '
                'target_id=%s chain=%s eth_blockNumber_raw=%s parsed_block=%s '
                'action=zero_out reason=value_in_timestamp_range',
                target.get('id'), network, _raw_block_hex, latest,
            )
            latest = 0
    safe_to = max(0, latest - confirmations)

    # Canonical per-target chain/RPC routing record: proves which endpoint served
    # this target and the chain it resolved to. Fields: target_id, configured_chain,
    # resolved_chain_id, rpc_url_env_used, latest_block.
    logger.info(
        'evm_chain_routing target_id=%s configured_chain=%s resolved_chain_id=%s '
        'rpc_url_env_used=%s latest_block=%s',
        target.get('id'), network, expected_chain_id, rpc_url_env_used, latest,
    )

    latest_block_raw_hex = hex(latest) if latest else '0x0'
    cursor = str(target.get('monitoring_checkpoint_cursor') or '').strip()
    last_block = None
    if cursor and ':' in cursor:
        try:
            last_block = int(cursor.split(':', 1)[0])
        except ValueError:
            last_block = None
    # Guardrail: Unix timestamps (~1.78B for 2026) are not valid block heights.
    # Also guard against cursors that are more than 1000 blocks ahead of the chain
    # head — this catches stale cursors from wrong chains or corrupt writes even
    # when the value is below the 500M timestamp threshold.
    _corrupt_cursor_reason: str | None = None
    if last_block is not None:
        if last_block > 500_000_000:
            _corrupt_cursor_reason = 'timestamp_range'
        elif latest and last_block > latest + 1000:
            _corrupt_cursor_reason = 'cursor_ahead_of_chain'
    if _corrupt_cursor_reason is not None:
        logger.warning(
            'evm_cursor_corruption_detected target_id=%s chain=%s corrupt_cursor=%s '
            'latest_block=%s reason=%s previous_cursor=%s repaired_cursor=reset_to_replay_window',
            target.get('id'), network, last_block, latest,
            _corrupt_cursor_reason, cursor or 'none',
        )
        last_block = None

    # Hard per-target-per-cycle safety budget (Section 5). Read once at poll start with
    # conservative production defaults; verified before every RPC below.
    budget = load_poll_budget()
    _initial_tail = initial_live_tail_blocks()

    # --- Per-cycle block ceiling (Section 1 + 5) ---
    # MAX_BLOCKS_PER_TARGET_PER_CYCLE (default 25) is the authoritative HARD ceiling and
    # caps EVERY scan path. A chain/env cap can only ever LOWER the scanned range, never
    # raise it above the safety ceiling. This closes the exact bypass the incident hit: the
    # 25-block cap was previously applied ONLY when a cursor existed, so a no-cursor contract
    # poll set scan_ceiling=safe_to and queried the full 2,001-block window.
    _CHAIN_MAX_BLOCKS_PER_CYCLE: dict[str, int] = {'base': 100, 'base-mainnet': 100}
    _chain_default_max = _CHAIN_MAX_BLOCKS_PER_CYCLE.get(network, 5000)
    if network in {'base', 'base-mainnet'}:
        def _base_int_env(name: str, fallback: int) -> int:
            try:
                return max(1, int(os.getenv(name, str(fallback))))
            except (TypeError, ValueError):
                return fallback
        _base_default = _base_int_env('BASE_MAX_BLOCKS_PER_CYCLE', _chain_default_max)
        _chain_default_max = _base_int_env('BASE_CATCHUP_MAX_BLOCKS_PER_CYCLE', _base_default)
    try:
        max_blocks_per_cycle = max(1, int(os.getenv('MAX_BLOCKS_PER_CYCLE', str(_chain_default_max))))
    except (TypeError, ValueError):
        max_blocks_per_cycle = _chain_default_max
    if network not in {'base', 'base-mainnet'}:
        max_blocks_per_cycle = max(block_scan_chunk, max_blocks_per_cycle)
    max_blocks_per_cycle = min(max_blocks_per_cycle, budget.max_blocks)

    # --- Scan window ---
    _backfill_on = historical_backfill_enabled()
    if _backfill_on:
        # Operator-enabled deep historical backfill (OFF by default; Section 13). Preserved
        # cursor-based catch-up: a no-cursor target seeds from safe_backfill_window and an
        # existing cursor advances incrementally, each still hard-capped at max_blocks_per_cycle
        # so a deep backlog catches up GRADUALLY over many cycles (never one heavy poll).
        if last_block is None:
            from_block = max(0, safe_to - safe_backfill_window + 1)
        else:
            # The reorg overlap must not consume the whole per-cycle budget, or catch-up
            # makes zero forward progress; shrink it when it is as large as the budget.
            _effective_replay = replay_blocks
            if _effective_replay >= max_blocks_per_cycle:
                _effective_replay = max(1, max_blocks_per_cycle // 3)
            from_block = max(0, last_block - _effective_replay)
        scan_ceiling = min(from_block + max_blocks_per_cycle - 1, safe_to)
        logger.info(
            'evm_scan_window_backfill target_id=%s chain=%s mode=historical_backfill '
            'from_block=%s to_block=%s max_blocks_per_cycle=%s',
            target.get('id'), network, from_block, scan_ceiling, max_blocks_per_cycle,
        )
    else:
        # Polling-only MVP live-tail sampling (Section 2 + 13): a scheduled health poll
        # scans ONLY the recent live tail near the head, never a deep historical backfill.
        #   * No cursor  -> INITIAL_LIVE_TAIL_BLOCKS (10) ending at safe_head.
        #   * With cursor -> from just after the cursor (small reorg overlap) up to the head,
        #     but never more than max_blocks_per_cycle behind it — so on a fast chain (Base
        #     ~450 blocks / 15 min) coverage tracks the head instead of the cursor lagging
        #     25 blocks/cycle forever. The cursor still DEDUPES already-emitted events; the
        #     skipped gap (cursor+1 .. window_start-1) is deferred backfill, never scanned
        #     during a health poll. This is why contracts (no wallet fast-forward) stay current.
        if last_block is None:
            from_block = max(0, safe_to - _initial_tail + 1)
        else:
            _overlap = min(replay_blocks, max(0, max_blocks_per_cycle - 1))
            from_block = max(max(0, last_block - _overlap), max(0, safe_to - max_blocks_per_cycle + 1))
        scan_ceiling = safe_to
        logger.info(
            'evm_scan_window_live_tail target_id=%s chain=%s mode=live_tail_only '
            'has_cursor=%s from_block=%s to_block=%s window_blocks=%s '
            'initial_live_tail_blocks=%s max_blocks_per_cycle=%s historical_backfill_enabled=false',
            target.get('id'), network, last_block is not None, from_block, scan_ceiling,
            max(0, scan_ceiling - from_block + 1), _initial_tail, max_blocks_per_cycle,
        )
    catchup_mode: bool = scan_ceiling < safe_to
    blocks_deferred: int = max(0, safe_to - scan_ceiling)

    # Live-tail window size (recent blocks always scanned during catch-up so new
    # transactions are detected without waiting for the gradual backfill). Resolved here,
    # ahead of the scan, so the deep-backlog fast-forward decision below can reuse it.
    # Configurable via BASE_LIVE_TAIL_BLOCKS (Base) or the generic EVM_LIVE_TAIL_BLOCKS;
    # defaults to 100 recent blocks on Base so the live-tail eth_getLogs window stays
    # within the per-request size that providers accept.
    _live_tail_default = '100' if network in {'base', 'base-mainnet'} else '0'
    if network in {'base', 'base-mainnet'}:
        _live_tail_default = os.getenv('BASE_LIVE_TAIL_BLOCKS', _live_tail_default)
    try:
        live_tail_blocks = max(0, int(os.getenv('EVM_LIVE_TAIL_BLOCKS', _live_tail_default)))
    except (TypeError, ValueError):
        live_tail_blocks = 100 if network in {'base', 'base-mainnet'} else 0
    # Section 1/5: the live-tail window (used by catch-up AND the deep-backlog fast-forward)
    # can never exceed the per-target block ceiling — otherwise a 100-block live tail would
    # block-scan 100 blocks and blow the 100-RPC budget. Bound it to max_blocks_per_cycle so
    # every scan range (backfill, catch-up, fast-forward, live tail) stays within budget.
    live_tail_blocks = min(live_tail_blocks, max_blocks_per_cycle)

    # --- Deep-backlog fast-forward (live wallet monitoring) ---
    # When a live wallet target's previous_cursor is so far behind the chain head that the
    # deferred backlog exceeds a safe threshold, gradual catch-up (max_blocks_per_cycle at
    # a time) can NEVER converge on a fast chain like Base — the chain produces new blocks
    # faster than a capped catch-up cycle scans them. Worse, persisting the stale catch-up
    # ceiling as the latest processed block keeps Monitoring Sources degraded/no-evidence
    # and the reported block lag pinned to the old checkpoint, even though the RPC is
    # healthy. Past the threshold we abandon the (bounded, optional) historical backfill for
    # this cycle and fast-forward the cursor to the live tail: scan only latest-live_tail..
    # latest so provider health + coverage telemetry reflect the REAL chain head and stay
    # fresh. The skipped range is deferred backfill and must never block live telemetry
    # freshness. Configurable via BASE_CATCHUP_FAST_FORWARD_THRESHOLD /
    # EVM_CATCHUP_FAST_FORWARD_THRESHOLD; 0 disables fast-forward. The default is high enough
    # that moderate catch-up (which the live-tail window already covers) still proceeds
    # gradually and only an extreme, unrecoverable backlog triggers the fast-forward.
    _ff_default = 150_000 if network in {'base', 'base-mainnet'} else 0
    if network in {'base', 'base-mainnet'}:
        try:
            _ff_default = max(0, int(os.getenv('BASE_CATCHUP_FAST_FORWARD_THRESHOLD', str(_ff_default))))
        except (TypeError, ValueError):
            pass
    try:
        fast_forward_threshold = max(0, int(os.getenv('EVM_CATCHUP_FAST_FORWARD_THRESHOLD', str(_ff_default))))
    except (TypeError, ValueError):
        fast_forward_threshold = _ff_default
    cursor_fast_forwarded = False
    if (
        target_type == 'wallet'
        and catchup_mode
        and live_tail_blocks > 0
        and fast_forward_threshold > 0
        and blocks_deferred > fast_forward_threshold
    ):
        _ff_new_from = max(0, safe_to - live_tail_blocks)
        # Only ever fast-forward FORWARD — never move the scan window backward (guards the
        # degenerate case where the live tail already overlaps the planned scan ceiling).
        if _ff_new_from > scan_ceiling:
            _ff_old_cursor = last_block if last_block is not None else from_block
            logger.warning(
                'evm_cursor_fast_forward target_id=%s chain=%s monitored_wallet=%s '
                'cursor_fast_forwarded=true old_cursor=%s new_cursor=%s latest_block=%s '
                'safe_to=%s live_tail_from=%s live_tail_to=%s live_tail_window=%s '
                'blocks_deferred=%s fast_forward_threshold=%s '
                'action=scan_live_tail_only reason=backlog_exceeds_threshold_backfill_deferred',
                target.get('id'), network, target_address,
                _ff_old_cursor, _ff_new_from, latest,
                safe_to, _ff_new_from, safe_to, live_tail_blocks,
                blocks_deferred, fast_forward_threshold,
            )
            from_block = _ff_new_from
            scan_ceiling = safe_to
            # The cursor now sits at the live tail, so this cycle is no longer catching up
            # and defers no blocks; the skipped history is intentionally abandoned backfill.
            catchup_mode = False
            blocks_deferred = 0
            cursor_fast_forwarded = True

    logger.info(
        'evm_block_scan_start target_id=%s chain=%s monitored_wallet=%s '
        'latest_block_hex=%s latest_block_decimal=%s previous_cursor=%s '
        'repaired_cursor=%s from_block=%s to_block=%s blocks_to_scan=%s '
        'safe_backfill_window=%s catchup_mode=%s max_blocks_per_cycle=%s '
        'planned_from_block=%s planned_to_block=%s blocks_deferred=%s',
        target.get('id'), network,
        target_address if target_type == 'wallet' else 'n/a',
        latest_block_raw_hex, latest,
        cursor or 'none',
        'yes' if (cursor and last_block is None and ':' in cursor) else 'no',
        from_block, scan_ceiling, max(0, scan_ceiling - from_block + 1),
        safe_backfill_window if last_block is None else max_blocks_per_cycle,
        catchup_mode, max_blocks_per_cycle,
        from_block, scan_ceiling, blocks_deferred,
    )
    if scan_ceiling < from_block:
        return []

    events: list[ActivityEvent] = []
    _env_chain_id = int(os.getenv('STAGING_EVM_CHAIN_ID') or os.getenv('EVM_CHAIN_ID') or 0) or 1
    chain_id = CHAIN_MAP.get(network, {}).get('chain_id') or _env_chain_id
    block_ts_cache: dict[str, datetime] = {}

    # Every scan-phase RPC goes through the budgeted, caller-tagging client so the
    # per-target budget + process-wide circuit breaker are enforced at a single
    # chokepoint (Section 5/10/12). A budget/breaker stop raises; it is caught below
    # and turned into a terminal partial/degraded status with the cursor held.
    scan_client = _BudgetedRpcClient(client, budget, target.get('id'))
    _budget_stop_event: str | None = None
    _budget_stop_reason: str | None = None

    logs: list[dict[str, Any]] = []
    _logs_fetch_status = 'ok'
    _logs_fetch_error_count = 0
    # Highest block whose ERC-20 logs were fully scanned. Set below scan_ceiling whenever
    # the log scan did NOT fully cover the range — a 413 that stayed too large even at the
    # minimum chunk size ('degraded'), OR a non-413 failure like 429/400/unreachable
    # ('failed') — so the cursor is never advanced past blocks whose logs were never
    # scanned (fail-closed). For a first-chunk failure this equals from_block-1 (no advance).
    _logs_last_complete_block: int | None = None
    target_type = str(target.get('target_type') or '').lower()
    if target_type in {'wallet', 'contract'}:
        # eth_getLogs captures ERC-20 transfer/approval activity and is fetched in
        # ADAPTIVE chunks: a 413 (too large) halves the block range and retries instead
        # of failing the whole poll, and never benches the provider. For a WALLET target
        # this is best-effort enrichment (the block-by-block tx scan below still detects
        # native transfers if logs cannot be fetched). For a CONTRACT target it is the
        # PRIMARY signal: ERC-20 transfers live in receipt logs keyed by the emitting
        # token contract, not only in transactions whose ``to`` is the contract — so the
        # contract log scan (address-filtered) captures router/DEX-mediated transfers the
        # ``tx.to == contract`` block scan misses. Contract address matching must never
        # require the contract to appear as a transaction ``from``/``to``.
        _logs_max_range, _logs_min_range = _wallet_logs_block_range(network, block_scan_chunk)
        # Section 6: never request more than MAX_LOG_QUERY_CHUNK_BLOCKS (default 5) blocks
        # per eth_getLogs — for a 25-block cycle that is ≤5 blocks per RPC call. On a 413
        # (or a chunk that would blow the log budget) the range is split recursively down to
        # a SINGLE block, so the halving floor is 1 (never wider than the 5-block ceiling).
        _logs_max_range = min(_logs_max_range, budget.max_log_query_chunk_blocks)
        _logs_min_range = 1
        _logs_fetcher = _fetch_logs if target_type == 'wallet' else _fetch_contract_logs
        try:
            _adaptive = _fetch_wallet_logs_adaptive(
                scan_client, target_address, from_block, scan_ceiling,
                network=network, target_id=target.get('id'),
                max_range=_logs_max_range, min_range=_logs_min_range,
                logs_fetcher=_logs_fetcher,
                budget=budget, max_blocks_ceiling=max_blocks_per_cycle,
            )
        except (PollBudgetExhausted, RpcCircuitBreakerTripped, ScanRangeInvariantError) as _bstop:
            # Budget/breaker/invariant stop during the log scan: fail closed. Emit no
            # events, hold the cursor at the previous checkpoint (from_block-1, never
            # advancing past unscanned blocks), and record the terminal reason so the
            # cycle reports partial/degraded (never a live success). Section 1/5/12.
            _budget_stop_event = getattr(_bstop, 'event', None) or 'monitoring_scan_range_invariant_failed'
            _budget_stop_reason = getattr(_bstop, 'reason', None) or (
                'scan_range_invariant' if isinstance(_bstop, ScanRangeInvariantError) else 'circuit_breaker'
            )
            _logs_fetch_status = 'degraded'
            _logs_last_complete_block = from_block - 1
            logs = []
            logger.warning(
                'monitoring_poll_stopped target_id=%s chain=%s phase=contract_log_scan '
                'event=%s reason=%s from_block=%s to_block=%s rpc_calls_used=%s '
                'elapsed_seconds=%s action=hold_cursor_persist_partial',
                target.get('id'), network, _budget_stop_event, _budget_stop_reason,
                from_block, scan_ceiling, budget.rpc_calls_used, budget.elapsed_seconds,
            )
        else:
            logs = _adaptive['logs']
            _logs_fetch_status = _adaptive['status']
            _logs_fetch_error_count = _adaptive['error_count']
            budget.logs_processed += len(logs)
            if _adaptive.get('budget_stopped'):
                _budget_stop_event = budget.exhausted_event or 'monitoring_poll_log_budget_exhausted'
                _budget_stop_reason = budget.exhausted_reason or 'log_budget'
            if target_type == 'contract':
                logger.info(
                    'evm_contract_log_scan target_id=%s chain=%s contract_address=%s '
                    'from_block=%s to_block=%s logs_found=%s logs_fetch_status=%s '
                    'action=erc20_receipt_logs_not_tx_to_matching',
                    target.get('id'), network, target_address,
                    from_block, scan_ceiling, len(logs), _adaptive['status'],
                )
            if _adaptive['status'] in {'degraded', 'failed'}:
                # The log scan did not fully cover [from_block, scan_ceiling] — either a 413
                # chunk stayed too large at the minimum range ('degraded'/query_too_large) or a
                # non-413 error stopped the scan ('failed'/logs_fetch_failed) or the per-cycle
                # log budget was reached. Cap the cursor at the last fully-scanned block so the
                # unscanned blocks are re-scanned next cycle rather than skipped. On a
                # first-chunk failure last_complete_block == from_block-1, which holds the
                # cursor at the previous checkpoint (no forward advance).
                _logs_last_complete_block = _adaptive['last_complete_block']

    # Live-tail window: when still in catchup_mode (a moderate backlog that did NOT
    # trigger the deep-backlog fast-forward above), also scan the most recent blocks so
    # new transactions are detected immediately without waiting for the gradual backfill
    # to complete. live_tail_blocks was resolved earlier, before the fast-forward decision.
    # After a fast-forward catchup_mode is False and the primary scan range IS the live
    # tail, so no separate live-tail range is appended here.
    live_tail_from: int | None = None
    if catchup_mode and live_tail_blocks > 0:
        _lt_candidate = max(scan_ceiling + 1, safe_to - live_tail_blocks)
        if _lt_candidate <= safe_to:
            live_tail_from = _lt_candidate
            logger.info(
                'live_tail_scan_planned target_id=%s chain=%s backfill_ceiling=%s '
                'live_tail_from=%s live_tail_to=%s live_tail_blocks=%s',
                target.get('id'), network, scan_ceiling,
                live_tail_from, safe_to, live_tail_blocks,
            )

    # Build list of (from, to) ranges to scan: always the backfill range, plus
    # an optional live-tail range when in catchup_mode.
    _scan_ranges: list[tuple[int, int]] = [(from_block, scan_ceiling)]
    if live_tail_from is not None:
        _scan_ranges.append((live_tail_from, safe_to))

    _transactions_inspected = 0
    _wallet_transfers_detected = 0
    _detected_tx_hashes: list[str] = []
    _failed_blocks: list[int] = []
    # Supplementary block-by-block scan (tx.to == contract / native wallet transfers).
    # Skipped entirely when the log scan already hit a budget/breaker/invariant stop —
    # no new RPC work after a clean stop (Section 5/12). Every eth_getBlockByNumber goes
    # through the budgeted client so this loop is bounded to ≤ max_blocks calls and can
    # never re-inflate into the 2,001-block scan the incident logged.
    if _budget_stop_event is None:
        try:
            for _range_from, _range_to in _scan_ranges:
                for chunk_from, chunk_to in _iter_block_ranges(_range_from, _range_to, block_scan_chunk):
                    for block_number in range(chunk_from, chunk_to + 1):
                        try:
                            block = scan_client.call('eth_getBlockByNumber', [hex(block_number), True]) or {}
                            budget.blocks_scanned += 1
                        except (PollBudgetExhausted, RpcCircuitBreakerTripped, ScanRangeInvariantError):
                            # Budget/breaker/invariant: propagate to stop the whole scan cleanly.
                            raise
                        except Exception as block_exc:
                            _failed_blocks.append(block_number)
                            logger.warning(
                                'evm_block_fetch_failed target_id=%s chain=%s block_number=%s block_number_hex=%s '
                                'from_block=%s to_block=%s error_type=%s http_status=%s error=%s action=continue_remaining_blocks',
                                target.get('id'), network, block_number, hex(block_number),
                                _range_from, _range_to,
                                type(block_exc).__name__, getattr(block_exc, 'code', None) if isinstance(block_exc, _urllib_error.HTTPError) else None,
                                str(block_exc)[:200],
                            )
                            continue
                        block_hash = str(block.get('hash') or '')
                        if block_hash and block_hash not in block_ts_cache:
                            block_ts_cache[block_hash] = _iso_from_block_ts(block.get('timestamp'))
                        txs = block.get('transactions') or []
                        for tx in txs:
                            _transactions_inspected += 1
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
                            payload['source_type'] = 'rpc_polling'
                            payload['detected_by'] = 'stable_rpc_polling'
                            payload['provider_mode'] = 'stable_rpc_polling'
                            _latency = round((datetime.now(timezone.utc) - observed_at).total_seconds(), 2) if isinstance(observed_at, datetime) else None
                            payload['observed_latency_seconds'] = _latency
                            if target_type == 'wallet':
                                # Shared native-ETH matcher (also used by the realtime worker)
                                # so both detection paths normalise addresses identically.
                                _native_direction = native_transfer_direction(target_address, tx)
                                if _native_direction is not None:
                                    payload['wallet_transfer_direction'] = _native_direction
                                    _wallet_transfers_detected += 1
                                    if tx_hash:
                                        _detected_tx_hashes.append(tx_hash)
                            kind = 'transaction' if target_type == 'wallet' else 'contract'
                            events.append(ActivityEvent(event_id=_make_event_id(str(target['id']), cursor_value, kind), kind=kind, observed_at=observed_at, ingestion_source=preferred_source, cursor=cursor_value, payload=payload))
        except (PollBudgetExhausted, RpcCircuitBreakerTripped, ScanRangeInvariantError) as _bstop:
            # Budget/breaker/invariant stop during the block scan: stop cleanly, hold the
            # cursor at the previous checkpoint, and report degraded (never live success).
            _budget_stop_event = getattr(_bstop, 'event', None) or 'monitoring_scan_range_invariant_failed'
            _budget_stop_reason = getattr(_bstop, 'reason', None) or (
                'scan_range_invariant' if isinstance(_bstop, ScanRangeInvariantError) else 'circuit_breaker'
            )
            if _logs_fetch_status == 'ok':
                _logs_fetch_status = 'degraded'
            _logs_last_complete_block = from_block - 1
            logger.warning(
                'monitoring_poll_stopped target_id=%s chain=%s phase=block_scan event=%s reason=%s '
                'from_block=%s to_block=%s rpc_calls_used=%s blocks_scanned=%s elapsed_seconds=%s '
                'action=hold_cursor_persist_partial',
                target.get('id'), network, _budget_stop_event, _budget_stop_reason,
                from_block, scan_ceiling, budget.rpc_calls_used, budget.blocks_scanned,
                budget.elapsed_seconds,
            )

    # Section 3 + 4: build normalized telemetry from the ERC-20 logs LOCALLY. eth_getLogs
    # already carries address, topics, data, transactionHash, blockNumber and logIndex, so
    # a Transfer/Approval is decoded WITHOUT an eth_getTransactionByHash per log — removing
    # the N+1 storm (119,163 logs previously became 119,163 tx lookups). Logs are decoded,
    # deduped by (chain_id, transaction_hash, log_index), and only then optionally enriched.
    _decoded_logs: list[dict[str, Any]] = []
    _enrichment_required = (
        str(target.get('requires_tx_enrichment') or '').strip().lower() in {'1', 'true', 'yes', 'on'}
        or str(os.getenv('EVM_TX_ENRICHMENT_ENABLED', 'false')).strip().lower() in {'1', 'true', 'yes', 'on'}
    )
    for log in logs:
        decoded = _decode_transfer_log(log)
        if decoded is None:
            # Not a Transfer/Approval event: never decoded as one, never enriched.
            continue
        decoded['requires_enrichment'] = _enrichment_required
        decoded['_blockHash_raw'] = log.get('blockHash')
        _decoded_logs.append(decoded)
    _decoded_logs = _dedupe_decoded_logs(_decoded_logs, chain_id)

    # Optional, bounded, rule-gated transaction enrichment (Section 4). Default OFF: an
    # ordinary USDC Transfer that matches no rule is never enriched. When enabled the plan
    # is deduped by transaction hash (each tx enriched at most once) and hard-capped at
    # MAX_TX_ENRICHMENTS_PER_TARGET_PER_CYCLE.
    _enriched_tx: dict[str, dict[str, Any]] = {}
    if _budget_stop_event is None and _enrichment_required and budget.max_tx_enrichments > 0:
        for _enrich_hash in _plan_tx_enrichments(_decoded_logs, max_enrichments=budget.max_tx_enrichments):
            if not budget.can_enrich():
                break
            try:
                _enriched_tx[_enrich_hash] = scan_client.call('eth_getTransactionByHash', [_enrich_hash]) or {}
            except (PollBudgetExhausted, RpcCircuitBreakerTripped, ScanRangeInvariantError) as _bstop:
                _budget_stop_event = getattr(_bstop, 'event', None) or 'monitoring_poll_enrichment_budget_exhausted'
                _budget_stop_reason = getattr(_bstop, 'reason', None) or 'enrichment_budget'
                if _logs_fetch_status == 'ok':
                    _logs_fetch_status = 'degraded'
                break
            budget.transaction_enrichments += 1

    for decoded in _decoded_logs:
        tx_hash = decoded['transaction_hash']
        block_number = decoded['block_number'] if decoded['block_number'] is not None else safe_to
        log_index = decoded['log_index']
        block_hash = decoded['block_hash'] or ''
        observed_at = block_ts_cache.get(block_hash)
        if observed_at is None:
            # Block timestamp not already cached from the block scan: at most ONE bounded,
            # budget-gated eth_getBlockByHash per UNIQUE block (deduped by the cache) —
            # never one per log. Skipped after a budget/breaker stop.
            _blk: dict[str, Any] = {}
            if decoded.get('_blockHash_raw') and _budget_stop_event is None:
                try:
                    _blk = scan_client.call('eth_getBlockByHash', [decoded['_blockHash_raw'], False]) or {}
                except (PollBudgetExhausted, RpcCircuitBreakerTripped, ScanRangeInvariantError) as _bstop:
                    _budget_stop_event = getattr(_bstop, 'event', None) or 'monitoring_poll_rpc_budget_exhausted'
                    _budget_stop_reason = getattr(_bstop, 'reason', None) or 'rpc_budget'
                    if _logs_fetch_status == 'ok':
                        _logs_fetch_status = 'degraded'
            observed_at = _iso_from_block_ts((_blk or {}).get('timestamp'))
            if block_hash:
                block_ts_cache[block_hash] = observed_at
        is_approval = decoded['topic0'] == APPROVAL_TOPIC
        # Synthetic tx built from the log (from/to only) so _build_base_payload is populated
        # from log data; enrichment, when present, overlays the real tx fields.
        _enriched = _enriched_tx.get(tx_hash) or {}
        _synthetic_tx = {
            'hash': tx_hash,
            'from': _enriched.get('from') or decoded['from_address'],
            'to': _enriched.get('to') or decoded['to_address'],
            'value': _enriched.get('value'),
            'input': _enriched.get('input'),
        }
        payload = _build_base_payload(
            target=target,
            network=network,
            chain_id=chain_id,
            block_number=block_number,
            block_hash=decoded.get('_blockHash_raw'),
            tx=_synthetic_tx,
            tx_hash=tx_hash,
            raw_reference=f'{network}:{tx_hash}:{log_index}',
        )
        payload.update(
            {
                'log_index': log_index,
                'transaction_index': decoded['transaction_index'],
                'contract_address': decoded['contract_address'] or payload.get('contract_address'),
                'asset_address': decoded['contract_address'],
                'owner': decoded['from_address'],
                'from': decoded['from_address'],
                'from_address': decoded['from_address'],
                'spender': decoded['to_address'] if is_approval else None,
                'to': decoded['to_address'],
                'to_address': decoded['to_address'],
                'kind_hint': decoded['kind_hint'],
                'event_type': decoded['event_type'],
                'amount': decoded['amount'],
                'observed_at': observed_at.isoformat(),
                # Locally decoded from the receipt log (no tx lookup) unless a rule required
                # enrichment; tag the detection path so Detected By is never blank.
                'source_type': 'rpc_polling',
                'detected_by': 'stable_rpc_polling',
                'provider_mode': 'stable_rpc_polling',
                'enrichment_status': 'enriched' if tx_hash in _enriched_tx else 'log_only',
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
    _blocks_scanned = max(0, scan_ceiling - from_block + 1) - len(_failed_blocks)
    logger.info(
        'evm_block_scan_complete target_id=%s chain=%s monitored_wallet=%s '
        'eth_blockNumber_raw=%s from_block=%s to_block=%s '
        'blocks_scanned=%s transactions_inspected=%s wallet_transfers_detected=%s '
        'detected_tx_hashes=%s matches_found=%s catchup_mode=%s blocks_deferred=%s',
        target.get('id'), network,
        target_address if target_type == 'wallet' else 'n/a',
        latest_block_raw_hex,
        from_block, scan_ceiling,
        max(0, _blocks_scanned),
        _transactions_inspected,
        _wallet_transfers_detected,
        _detected_tx_hashes[:25],
        len(deduped),
        catchup_mode, blocks_deferred,
    )
    # Canonical reason for an incomplete log scan, surfaced to the provider-result layer
    # so the cycle is reported as degraded (never live-success). A per-poll budget/breaker/
    # invariant stop takes precedence (its exact reason), then query_too_large for a 413
    # that stayed too large at the min chunk, then logs_fetch_failed for a non-413 error.
    _logs_status_reason: str | None = (
        _budget_stop_reason if _budget_stop_reason
        else ('query_too_large' if _logs_fetch_status == 'degraded'
              else ('logs_fetch_failed' if _logs_fetch_status == 'failed' else None))
    )
    # Cursor advancement target. Normally the full block-scan ceiling, BUT when the
    # eth_getLogs scan did not fully cover the range (413 too large at min, or a non-413
    # failure) we cap the cursor at the last block whose logs were fully scanned — never
    # advancing past unscanned blocks and never moving the cursor backward (max with the
    # prior cursor floor).
    _scan_to_block = scan_ceiling
    if _logs_last_complete_block is not None and _logs_last_complete_block < scan_ceiling:
        _cursor_floor = last_block if last_block is not None else (from_block - 1)
        _scan_to_block = min(scan_ceiling, max(_cursor_floor, _logs_last_complete_block))
        _cursor_reason = _logs_status_reason or 'logs_fetch_failed'
        _cursor_error_class = 'request_too_large' if _logs_fetch_status == 'degraded' else 'logs_fetch_failed'
        # Fail-closed cursor guard: the log scan did not cover (capped, scan_ceiling], so the
        # cursor is held at the last fully-scanned block and that range is re-scanned next
        # cycle instead of being skipped without log coverage.
        logger.warning(
            'cursor_not_advanced target_id=%s chain=%s reason=%s '
            'failed_from_block=%s failed_to_block=%s previous_cursor=%s '
            'last_complete_block=%s capped_scan_to_block=%s scan_ceiling=%s '
            'error_class=%s status_reason=%s action=do_not_advance_past_unscanned_blocks',
            target.get('id'), network, _cursor_reason,
            _scan_to_block + 1, scan_ceiling, cursor or 'none',
            _logs_last_complete_block, _scan_to_block, scan_ceiling,
            _cursor_error_class, _cursor_reason,
        )
    # Canonical end-of-scan summary. evm_block_scan_start must always be followed by
    # this line (never a bare provider_error): it proves the scan loop ran to completion
    # and reports exactly what was inspected, what failed, and what was detected.
    # When in catchup_mode the cursor advances only to scan_ceiling (not the chain head)
    # so the next cycle picks up the next chunk automatically.
    _persisted_cursor = f"{_scan_to_block}:checkpoint:-1"
    logger.info(
        'evm_block_scan_summary target_id=%s monitored_wallet=%s chain=%s chain_id=%s '
        'source_type=rpc_polling from_block=%s to_block=%s blocks_scanned=%s failed_blocks=%s '
        'logs_fetch_status=%s logs_fetch_error_count=%s transactions_inspected=%s wallet_transfers_detected=%s '
        'detected_tx_hashes=%s events_emitted=%s persisted_cursor=%s '
        'catchup_mode=%s max_blocks_per_cycle=%s blocks_deferred=%s checkpoint_persisted=%s',
        target.get('id'),
        target_address if target_type == 'wallet' else 'n/a',
        network, chain_id,
        from_block, scan_ceiling,
        max(0, _blocks_scanned),
        _failed_blocks[:25],
        _logs_fetch_status,
        _logs_fetch_error_count,
        _transactions_inspected,
        _wallet_transfers_detected,
        _detected_tx_hashes[:25],
        len(deduped),
        _persisted_cursor,
        catchup_mode, max_blocks_per_cycle, blocks_deferred,
        scan_ceiling,
    )
    # Expose the exact block we scanned up to so the runner can advance the cursor
    # even on empty scans (no events), preventing repeated small-window polling.
    # In catchup_mode this is scan_ceiling (not the chain head), so the next cycle
    # starts from here and advances another max_blocks_per_cycle until caught up. When a
    # 413 capped the scan, this is the last fully-scanned block (≤ scan_ceiling).
    target['_evm_scan_to_block'] = _scan_to_block
    # Expose the RAW observed chain head (eth_blockNumber) separately from the scan cursor.
    # The scan cursor (_evm_scan_to_block) is the confirmed block we processed up to; this
    # is the actual chain tip the provider reported this cycle. The runner persists it as
    # provider_health_records.latest_block so provider health reflects the REAL latest chain
    # head instead of a stale catch-up checkpoint. Left None when the head is unavailable.
    target['_evm_observed_chain_head'] = latest if latest else None
    # True when this cycle abandoned a deep historical backlog and fast-forwarded the cursor
    # to the live tail (see evm_cursor_fast_forward). The runner logs it for diagnostics.
    target['_evm_cursor_fast_forwarded'] = cursor_fast_forwarded
    # Expose the log-scan coverage status so the provider-result layer can report a
    # degraded (not live-success) observation and so a failed/partial log scan never
    # advances the cursor past unscanned blocks. 'ok' | 'degraded' | 'failed' and a
    # canonical reason (None | 'query_too_large' | 'logs_fetch_failed').
    target['_evm_logs_fetch_status'] = _logs_fetch_status
    target['_evm_logs_status_reason'] = _logs_status_reason
    # Section 5: always finish a poll with a terminal, persisted budget summary — proof of
    # exactly what the cycle consumed (RPC calls, blocks, logs, enrichments, seconds) and
    # whether a hard ceiling stopped it early. The runner persists this alongside the poll
    # result and heartbeat.
    _poll_terminal_status = 'partial' if _budget_stop_event else (
        'degraded' if _logs_fetch_status in {'degraded', 'failed'} else 'complete'
    )
    budget.logs_processed = max(budget.logs_processed, len(_decoded_logs))
    target['_evm_poll_budget'] = budget.as_dict()
    target['_evm_poll_terminal_status'] = _poll_terminal_status
    target['_evm_poll_stopped_event'] = _budget_stop_event
    target['_evm_poll_stopped_reason'] = _budget_stop_reason
    if _budget_stop_event:
        # Canonical budget-exhaustion event (monitoring_poll_rpc_budget_exhausted /
        # monitoring_poll_log_budget_exhausted / monitoring_poll_enrichment_budget_exhausted /
        # monitoring_poll_time_budget_exhausted / monitoring_poll_rpc_circuit_breaker /
        # monitoring_scan_range_invariant_failed).
        logger.warning(
            '%s target_id=%s chain=%s reason=%s rpc_calls_used=%s blocks_scanned=%s '
            'logs_received=%s transaction_enrichments=%s elapsed_seconds=%s '
            'terminal_status=%s persisted_cursor=%s',
            _budget_stop_event, target.get('id'), network, _budget_stop_reason,
            budget.rpc_calls_used, budget.blocks_scanned, budget.logs_received,
            budget.transaction_enrichments, budget.elapsed_seconds,
            _poll_terminal_status, _persisted_cursor,
        )
    logger.info(
        'monitoring_poll_budget_summary target_id=%s chain=%s terminal_status=%s '
        'rpc_calls_used=%s max_rpc_calls=%s blocks_scanned=%s max_blocks=%s '
        'logs_received=%s max_logs=%s transaction_enrichments=%s max_tx_enrichments=%s '
        'elapsed_seconds=%s max_poll_duration_seconds=%s stopped_reason=%s',
        target.get('id'), network, _poll_terminal_status,
        budget.rpc_calls_used, budget.max_rpc_calls, budget.blocks_scanned, budget.max_blocks,
        budget.logs_received, budget.max_logs, budget.transaction_enrichments,
        budget.max_tx_enrichments, budget.elapsed_seconds, budget.max_duration_seconds,
        _budget_stop_reason or 'none',
    )
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
        liquidity_observation['observation_kind'] = 'real_external_market_observation'
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
            'observation_kind': 'supporting_onchain_rollup',
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
            'provider_status': 'no_provider_configured',
            'reason': 'external_market_provider_not_configured',
            'observed_at': now.isoformat(),
            'venue_distribution': {},
            'route_distribution': {},
            'rolling_volume': 0.0,
            'rolling_transfer_count': 0,
            'unique_counterparties': 0,
            'concentration_ratio': 0.0,
            'abnormal_outflow_ratio': 0.0,
            'burst_score': 0.0,
            'freshness_seconds': None,
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
                observations.extend([
                    _normalize_market_observation(item, provider_name=str(provider.get('source_name') or 'external-market'), asset_identifier=asset_identifier, now=now)
                    for item in fetched
                    if isinstance(item, dict)
                ])
                continue
            observations.append(
                {
                    'provider_name': str(provider.get('source_name') or 'external-market'),
                    'source_name': str(provider.get('source_name') or 'external-market'),
                    'source_type': str(provider.get('source_type') or 'market_api'),
                    'asset_identifier': asset_identifier or None,
                    'telemetry_kind': 'external_market',
                    'status': 'insufficient_real_evidence',
                    'provider_status': 'provider_returned_no_observations',
                    'reason': 'provider_returned_no_observations',
                    'observed_at': now.isoformat(),
                    'venue_distribution': {},
                    'route_distribution': {},
                    'rolling_volume': 0.0,
                    'rolling_transfer_count': 0,
                    'unique_counterparties': 0,
                    'concentration_ratio': 0.0,
                    'abnormal_outflow_ratio': 0.0,
                    'burst_score': 0.0,
                    'freshness_seconds': None,
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
                    'provider_status': 'provider_unreachable',
                    'reason': 'provider_unreachable',
                    'observed_at': now.isoformat(),
                    'venue_distribution': {},
                    'route_distribution': {},
                    'rolling_volume': 0.0,
                    'rolling_transfer_count': 0,
                    'unique_counterparties': 0,
                    'concentration_ratio': 0.0,
                    'abnormal_outflow_ratio': 0.0,
                    'burst_score': 0.0,
                    'freshness_seconds': None,
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
                'provider_name': item.get('provider_name') or item.get('source_name'),
                'source_type': item.get('source_type'),
                'asset_identifier': item.get('asset_identifier') or asset_identifier,
                'observed_value': item.get('observed_value'),
                'observed_at': item.get('observed_at'),
                'freshness_seconds': item.get('freshness_seconds'),
                'status': item.get('status') or status,
                'provider_status': item.get('provider_status') or item.get('status') or status,
                'provenance': item.get('provenance') if isinstance(item.get('provenance'), dict) else {},
                'update_interval_seconds': item.get('update_interval_seconds'),
                'block_number': item.get('block_number'),
            }
        )
    if normalized:
        return normalized
    return [{
        'source_name': 'oracle-service',
        'provider_name': 'oracle-service',
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
        'observation_kind': 'supporting_onchain_rollup',
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
