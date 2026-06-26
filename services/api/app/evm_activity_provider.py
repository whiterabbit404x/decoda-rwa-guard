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


def _arm_host_backoff(host: str, backoff_seconds: float, error_class: str = 'rate_limited') -> str:
    """Arm a backoff window for a single provider host. Returns the until-wall ISO string."""
    now_mono = time.monotonic()
    until_wall = (datetime.now(timezone.utc) + timedelta(seconds=backoff_seconds)).isoformat()
    with _RPC_PROVIDER_LOCK:
        _RPC_HOST_BACKOFF[host] = {
            'until_monotonic': now_mono + backoff_seconds,
            'until_wall': until_wall,
            'error_class': error_class,
        }
    return until_wall


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
    until_wall = ''
    for entry in hosts:
        until_wall = _arm_host_backoff(entry, backoff, 'rate_limited')
    logger.warning(
        'rpc_provider_backoff_set error_class=rate_limited rpc_status=rate_limited '
        'rpc_host=%s backoff_seconds=%s retry_after_seconds=%s backoff_until=%s rpc_call_skipped=true',
        ','.join(hosts),
        int(backoff),
        'none' if retry_after_seconds is None else int(retry_after_seconds),
        until_wall,
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
    for st in live.values():
        rem = float(st.get('until_monotonic') or 0.0) - now_mono
        if rem > remaining:
            remaining = rem
            until_wall = st.get('until_wall')
            error_class = st.get('error_class')
    return {
        'active': rpc_provider_backoff_active(),
        'remaining_seconds': max(0.0, remaining),
        'backoff_until': until_wall,
        'error_class': error_class,
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
        'rpc_failover_used': bool(snap.get('rpc_failover_used', False)),
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


def worker_rpc_chain_id() -> int | None:
    """Chain id this worker's RPC is configured for (EVM_CHAIN_ID / STAGING_EVM_CHAIN_ID)."""
    raw = (os.getenv('STAGING_EVM_CHAIN_ID') or os.getenv('EVM_CHAIN_ID') or '').strip()
    return int(raw) if raw.isdigit() else None


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


def probe_rpc_health(rpc_url: str | None = None) -> dict[str, Any]:
    """
    Call eth_chainId and eth_blockNumber against the configured RPC endpoint.

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
    try:
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
        for attempt in range(max_attempts):
            try:
                with request.urlopen(req, timeout=timeout) as resp:  # nosec B310
                    body = json.loads(resp.read().decode('utf-8'))
                if body.get('error'):
                    raise RuntimeError(f"json-rpc error: {body['error']}")
                return body.get('result')
            except _urllib_error.HTTPError as exc:
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
            # Skip a provider whose 429 backoff window is still open. Only when more than
            # one provider exists — a lone provider is always tried so its own recovery
            # is detected rather than being benched forever.
            if count > 1 and host_backoff_active(host):
                if host not in skipped_hosts:
                    skipped_hosts.append(host)
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
) -> dict[str, Any]:
    """Fetch ERC-20 transfer/approval logs, halving the block range on HTTP 413.

    Scans ``[from_block, to_block]`` in chunks of at most ``max_range`` blocks. When a
    chunk is rejected as too large (HTTP 413 / ``RpcRequestTooLargeError``) the range
    is halved and retried, down to ``min_range`` blocks — so a single oversized query
    reduces the scan window instead of failing the whole poll, and the provider is
    never marked unavailable for a 413. Any non-413 failure (429/400/unreachable)
    stops the log scan for this cycle (the block-by-block scan still runs), preserving
    prior behavior.

    Returns a dict: ``logs``, ``last_complete_block`` (highest block fully covered by a
    SUCCESSFUL eth_getLogs scan; ``from_block - 1`` if even the first chunk failed),
    ``status`` (``ok``/``degraded``/``failed``), ``error_count``, ``too_large_count``,
    and ``min_chunk_size`` (smallest chunk size attempted).
    """
    logs: list[dict[str, Any]] = []
    last_complete = from_block - 1
    status = 'ok'
    error_count = 0
    too_large_count = 0
    min_chunk_size = max_range
    logged_failure = False
    # Stack of (lo, hi) ranges to scan; ascending lo pops first so last_complete
    # advances monotonically and a failed chunk never skips earlier unscanned blocks.
    pending: list[tuple[int, int]] = list(reversed(_iter_block_ranges(from_block, to_block, max_range)))
    while pending:
        lo, hi = pending.pop()
        span = hi - lo + 1
        try:
            logs.extend(_fetch_logs(client, address, lo, hi))
            last_complete = hi
            continue
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
    if last_block is None:
        # No prior cursor: backfill safe_backfill_window blocks to cover at least one polling interval.
        from_block = max(0, safe_to - safe_backfill_window)
    else:
        # Continue from the persisted cursor; replay_blocks of overlap guards against reorgs.
        from_block = max(0, last_block - replay_blocks)

    # Cap blocks scanned per cycle to avoid overwhelming RPCs during catch-up
    # (e.g. after downtime a worker can be 160k+ blocks behind on Base). A very old
    # cursor must catch up GRADUALLY over many cycles, not in one heavy poll.
    # Initial backfill (no cursor) is bounded by safe_backfill_window (≤2000 for Base).
    # Cursor-based catch-up is capped here; the cursor advances incrementally each
    # cycle (plus the live-tail window) until fully caught up. The Base default is
    # BASE_CATCHUP_MAX_BLOCKS_PER_CYCLE (100); the generic MAX_BLOCKS_PER_CYCLE still
    # overrides it when explicitly set.
    _CHAIN_MAX_BLOCKS_PER_CYCLE: dict[str, int] = {'base': 100, 'base-mainnet': 100}
    _chain_default_max = _CHAIN_MAX_BLOCKS_PER_CYCLE.get(network, 5000)
    if network in {'base', 'base-mainnet'}:
        # Base per-cycle block cap. BASE_MAX_BLOCKS_PER_CYCLE (default 100) is the general
        # Base ceiling; BASE_CATCHUP_MAX_BLOCKS_PER_CYCLE overrides it for cursor-based
        # catch-up and falls back to it. Both default to 100 so a deep Base backlog catches
        # up gradually and never reinflates into a heavy 1000-block poll.
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
    # On Base the catch-up cap is authoritative: a large block-scan batch size
    # (MONITOR_BATCH_BLOCKS) must NOT reinflate it into a heavy 1000-block catch-up
    # poll. Other chains keep the historical "at least one batch" floor.
    if network not in {'base', 'base-mainnet'}:
        max_blocks_per_cycle = max(block_scan_chunk, max_blocks_per_cycle)
    if last_block is not None:
        scan_ceiling = min(from_block + max_blocks_per_cycle - 1, safe_to)
    else:
        scan_ceiling = safe_to
    catchup_mode: bool = last_block is not None and scan_ceiling < safe_to
    blocks_deferred: int = max(0, safe_to - scan_ceiling)

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
    if target_type == 'wallet':
        # eth_getLogs is best-effort enrichment for ERC-20 transfer/approval logs and
        # is fetched in ADAPTIVE chunks: a 413 (too large) halves the block range and
        # retries instead of failing the whole poll, and never benches the provider.
        # The block-by-block transaction scan below still detects native wallet
        # transfers if logs cannot be fetched at all.
        _logs_max_range, _logs_min_range = _wallet_logs_block_range(network, block_scan_chunk)
        _adaptive = _fetch_wallet_logs_adaptive(
            client, target_address, from_block, scan_ceiling,
            network=network, target_id=target.get('id'),
            max_range=_logs_max_range, min_range=_logs_min_range,
        )
        logs = _adaptive['logs']
        _logs_fetch_status = _adaptive['status']
        _logs_fetch_error_count = _adaptive['error_count']
        if _adaptive['status'] in {'degraded', 'failed'}:
            # The log scan did not fully cover [from_block, scan_ceiling] — either a 413
            # chunk stayed too large at the minimum range ('degraded'/query_too_large) or a
            # non-413 error stopped the scan ('failed'/logs_fetch_failed). Cap the cursor at
            # the last fully-scanned block so the unscanned blocks are re-scanned next cycle
            # rather than skipped. On a first-chunk failure last_complete_block == from_block-1,
            # which holds the cursor at the previous checkpoint (no forward advance).
            _logs_last_complete_block = _adaptive['last_complete_block']

    # Live-tail window: when catchup_mode, also scan the most recent blocks so new
    # transactions are detected immediately without waiting for the gradual backfill to
    # complete. Configurable via BASE_LIVE_TAIL_BLOCKS (Base) or the generic
    # EVM_LIVE_TAIL_BLOCKS; defaults to 100 recent blocks on Base so the live-tail
    # eth_getLogs window stays within the per-request size that providers accept.
    _live_tail_default = '100' if network in {'base', 'base-mainnet'} else '0'
    if network in {'base', 'base-mainnet'}:
        _live_tail_default = os.getenv('BASE_LIVE_TAIL_BLOCKS', _live_tail_default)
    try:
        live_tail_blocks = max(0, int(os.getenv('EVM_LIVE_TAIL_BLOCKS', _live_tail_default)))
    except (TypeError, ValueError):
        live_tail_blocks = 100 if network in {'base', 'base-mainnet'} else 0
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
    for _range_from, _range_to in _scan_ranges:
        for chunk_from, chunk_to in _iter_block_ranges(_range_from, _range_to, block_scan_chunk):
            for block_number in range(chunk_from, chunk_to + 1):
                try:
                    block = client.call('eth_getBlockByNumber', [hex(block_number), True]) or {}
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
                    if target_type == 'wallet' and target_address in {tx_to, tx_from}:
                        payload['wallet_transfer_direction'] = 'outbound' if tx_from == target_address else 'inbound'
                        _wallet_transfers_detected += 1
                        if tx_hash:
                            _detected_tx_hashes.append(tx_hash)
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
    # so the cycle is reported as degraded (never live-success): query_too_large for a 413
    # that stayed too large at the min chunk, logs_fetch_failed for a non-413 error.
    _logs_status_reason: str | None = (
        'query_too_large' if _logs_fetch_status == 'degraded'
        else ('logs_fetch_failed' if _logs_fetch_status == 'failed' else None)
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
    # Expose the log-scan coverage status so the provider-result layer can report a
    # degraded (not live-success) observation and so a failed/partial log scan never
    # advances the cursor past unscanned blocks. 'ok' | 'degraded' | 'failed' and a
    # canonical reason (None | 'query_too_large' | 'logs_fetch_failed').
    target['_evm_logs_fetch_status'] = _logs_fetch_status
    target['_evm_logs_status_reason'] = _logs_status_reason
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
