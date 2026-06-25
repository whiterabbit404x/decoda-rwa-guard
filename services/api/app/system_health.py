"""
System Health snapshot builder for GET /ops/system-health.

Collects live infrastructure facts (DB, Redis, RPC, worker heartbeat, telemetry,
detection, alert delivery) and assembles a SaaS-grade status response.

Status vocabulary:
  healthy     - checked and passing
  degraded    - configured and partially working but stale / slow / incomplete
  failing     - configured, check ran, but failed
  unavailable - not configured or check cannot be run
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Any
from urllib.request import Request as UrlRequest, urlopen
from urllib.error import URLError, HTTPError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

WORKER_HEARTBEAT_STALE_SECONDS = int(os.getenv('WORKER_HEARTBEAT_TTL_SECONDS', '180')) * 2
TELEMETRY_STALE_SECONDS = 3600        # 1 hour  → degraded
DETECTION_STALE_SECONDS = 86400 * 2   # 48 hours → degraded
POLL_INTERVAL_SECONDS = max(10, int(os.getenv('MONITOR_POLL_INTERVAL_SECONDS', '30')))

# Base mainnet chain id — the canonical monitored chain for the RPC probe.
BASE_CHAIN_ID = 8453
# Safe, bounded timeout for the on-chain eth_blockNumber probe (seconds). A
# slow/rate-limited provider must never block the whole snapshot request.
RPC_PROBE_TIMEOUT_SECONDS = max(1, int(os.getenv('SYSTEM_HEALTH_RPC_TIMEOUT_SECONDS', '8')))
# Cache the Base RPC probe for a short TTL so repeated /ops/system-health page
# refreshes reuse one probe instead of calling the provider on every request.
# Default 60s so the status page never out-paces the worker's 60s poll cadence.
RPC_HEALTH_CACHE_TTL_SECONDS = max(0, int(os.getenv('SYSTEM_HEALTH_RPC_TTL_SECONDS', '60')))

logger = logging.getLogger(__name__)

# Process-local cache for the Base RPC health probe. Keyed by the resolved RPC
# URL so an env change invalidates it; stores the monotonic time of the probe.
_RPC_HEALTH_CACHE: dict[str, Any] = {}

# Structured-log fields from the most recent *live* probe. A cache hit replays
# these (with cache_hit=true) so every served /system-health response is
# observable in logs without re-running a live RPC call. Host only — no secrets.
_LAST_RPC_PROBE_LOG: dict[str, Any] = {}


def _age_seconds(ts: str | None) -> float | None:
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts.replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - parsed).total_seconds()
    except Exception:
        return None


def _human_age(ts: str | None) -> str:
    age = _age_seconds(ts)
    if age is None:
        return 'never'
    age = max(0, age)
    if age < 60:
        return f'{int(age)}s ago'
    if age < 3600:
        return f'{int(age // 60)}m ago'
    if age < 86400:
        return f'{int(age // 3600)}h ago'
    return f'{int(age // 86400)}d ago'


def _sanitize_error(exc: Exception) -> str:
    """Return a safe, non-secret error class name."""
    return type(exc).__name__


def _component(
    status: str,
    message: str,
    *,
    age: str | None = None,
    last_event: str | None = None,
    metric: str | None = None,
    action: str | None = None,
) -> dict[str, Any]:
    return {
        'status': status,
        'message': message,
        'age': age,
        'last_event': last_event,
        'metric': metric,
        'action': action,
    }


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def _check_api() -> dict[str, Any]:
    return _component(
        'healthy',
        'API is responding.',
        metric='active',
    )


def _check_database(connection: Any) -> dict[str, Any]:
    try:
        connection.execute('SELECT 1').fetchone()
        return _component('healthy', 'Database is reachable.')
    except Exception as exc:
        return _component(
            'failing',
            f'Database query failed ({_sanitize_error(exc)}).',
            action='Verify DATABASE_URL and check database connectivity.',
        )


def _check_redis() -> dict[str, Any]:
    redis_url = os.getenv('REDIS_URL', '').strip()
    upstash_url = os.getenv('UPSTASH_REDIS_REST_URL', '').strip()
    upstash_token = os.getenv('UPSTASH_REDIS_REST_TOKEN', '').strip()
    configured = bool(redis_url or (upstash_url and upstash_token))
    if not configured:
        return _component(
            'unavailable',
            'Redis is not configured.',
            action='Set REDIS_URL (or UPSTASH_REDIS_REST_URL + UPSTASH_REDIS_REST_TOKEN) to enable Redis.',
        )
    try:
        from services.api.app.domains.rate_limit import rate_limit_connectivity
        health = rate_limit_connectivity()
        if health.get('connected'):
            return _component('healthy', 'Redis ping succeeded.', metric=health.get('backend', 'redis'))
        return _component(
            'failing',
            'Redis is configured but ping failed.',
            action='Verify REDIS_URL and Redis server health.',
        )
    except Exception as exc:
        return _component(
            'failing',
            f'Redis check failed ({_sanitize_error(exc)}).',
            action='Verify Redis connectivity.',
        )


# Operator-facing sentence shown when Base RPC is configured but the on-chain
# call fails. The leading sentence is intentionally stable; a sanitized host and
# a categorized reason are appended so operators can act without reading logs.
_BASE_RPC_FAILED_MESSAGE = 'Base RPC request failed. Check provider key, network, or rate limit.'


def _resolve_base_rpc_url() -> str:
    """Resolve the Base (chain 8453) RPC URL exactly as the worker does for a Base
    target: ``EVM_RPC_URL_8453`` → ``BASE_EVM_RPC_URL``/``EVM_BASE_RPC_URL`` →
    global ``STAGING_EVM_RPC_URL``/``EVM_RPC_URL``.

    Using the worker's own per-chain resolver (``resolve_chain_rpc('base')``)
    guarantees System Health and the worker never disagree about which endpoint
    serves Base — regardless of ``EVM_CHAIN_ID``/``STAGING_EVM_CHAIN_ID``. The
    legacy global resolver keyed Base lookups off ``EVM_CHAIN_ID``, so a worker
    polling Base via ``EVM_RPC_URL_8453`` while ``EVM_CHAIN_ID`` was unset/``1``
    showed "Base RPC failing" here even though polling was healthy.
    """
    try:
        from services.api.app.evm_activity_provider import resolve_chain_rpc as _resolve_chain
        return (_resolve_chain('base').get('rpc_url') or '').strip()
    except Exception:
        return (
            os.getenv('EVM_RPC_URL_8453')
            or os.getenv('BASE_EVM_RPC_URL')
            or os.getenv('EVM_BASE_RPC_URL')
            or os.getenv('STAGING_EVM_RPC_URL')
            or os.getenv('EVM_RPC_URL')
            or ''
        ).strip()


def _rpc_failure_action(reason: str) -> str:
    """Map a categorized failure reason to a secret-free remediation hint."""
    r = reason.lower()
    if 'unauthorized' in r or '401' in r or '403' in r or 'forbidden' in r:
        return 'RPC provider rejected the request. Check provider key or endpoint.'
    if 'rate' in r or '429' in r:
        return 'Provider is rate-limiting. Increase RPC quota or reduce polling frequency.'
    if 'timeout' in r:
        return f'RPC endpoint did not respond within {RPC_PROBE_TIMEOUT_SECONDS}s. Check provider availability.'
    if 'hostname' in r or 'bad_url' in r:
        return 'RPC hostname cannot be resolved. Verify the EVM_RPC_URL hostname.'
    if 'refused' in r:
        return 'RPC connection refused. Check provider availability.'
    return 'Check EVM_RPC_URL connectivity, provider key, and quota in the worker service.'


def _rpc_failure_lead(reason: str) -> str:
    """Reason-specific operator sentence for a failed Base RPC probe.

    A timeout leads with its own mandated sentence so operators can act without
    reading logs; every other failure keeps the stable generic lead, with
    rate-limit and invalid-key remediation surfaced via the action. The sanitized
    host and categorized reason are appended by the caller — never the URL path,
    key, query, or credentials.
    """
    r = (reason or '').lower()
    if 'timeout' in r:
        return 'Base RPC request timed out.'
    return _BASE_RPC_FAILED_MESSAGE


def _rpc_failed(rpc_host: str, reason: str) -> dict[str, Any]:
    # Lead with the mandated operator sentence, then append the sanitized host and
    # categorized reason. Never includes the URL path, key, query, or credentials.
    return _component(
        'failing',
        f'{_rpc_failure_lead(reason)} (host: {rpc_host}, reason: {reason})',
        action=_rpc_failure_action(reason),
    )


def _rpc_status_class(reason: str) -> str:
    """Map a categorized failure reason to a coarse status label for structured logs."""
    r = (reason or '').lower()
    if 'rate' in r or '429' in r:
        return 'rate_limited'
    return 'failing'


def _parse_retry_after(headers: Any) -> float | None:
    """Return the numeric Retry-After value (seconds), bounded, or None.

    Respecting Retry-After lets a rate-limited provider tell us how long to wait;
    System Health uses it to extend the probe cache so refreshes never compound a
    429. Only the standard numeric-seconds form is honored (HTTP-date is rare for
    JSON-RPC providers). The value is clamped so a hostile header cannot pin the
    status page on a stale probe indefinitely.
    """
    try:
        raw = headers.get('Retry-After') if headers is not None else None
    except Exception:
        raw = None
    if not raw:
        return None
    try:
        return max(0.0, min(300.0, float(str(raw).strip())))
    except (TypeError, ValueError):
        return None


def _log_rpc_probe(
    *,
    rpc_configured: bool,
    rpc_host: str,
    rpc_status: str,
    response_time_ms: int,
    last_error_class: str | None,
    retry_after_seconds: float | None = None,
    cache_hit: bool = False,
) -> None:
    """Emit one structured, secret-free log line for the Base RPC probe.

    Only the host is ever logged — never the URL path, key, query, or credentials.
    A live probe (``cache_hit=False``) records its fields so a later cache hit can
    replay the same line marked ``cache_hit=true`` without re-hitting the provider.
    """
    if not cache_hit:
        _LAST_RPC_PROBE_LOG.clear()
        _LAST_RPC_PROBE_LOG.update(
            rpc_configured=rpc_configured,
            rpc_host=rpc_host,
            rpc_status=rpc_status,
            response_time_ms=response_time_ms,
            last_error_class=last_error_class,
            retry_after_seconds=retry_after_seconds,
        )
    logger.info(
        'rpc_probe rpc_configured=%s rpc_host=%s chain_id=%s rpc_status=%s '
        'response_time_ms=%s last_error_class=%s polling_interval_seconds=%s '
        'retry_after_seconds=%s cache_hit=%s',
        rpc_configured,
        rpc_host,
        BASE_CHAIN_ID,
        rpc_status,
        response_time_ms,
        last_error_class or 'none',
        POLL_INTERVAL_SECONDS,
        'none' if retry_after_seconds is None else retry_after_seconds,
        str(bool(cache_hit)).lower(),
    )


def _resolve_base_rpc_urls() -> list[str]:
    """Resolve the ordered Base (chain 8453) RPC provider list the worker would use.

    Mirrors ``_resolve_base_rpc_url`` but returns the full primary + failover list
    (EVM_RPC_URLS / EVM_RPC_FAILOVER_URLS / per-chain failover) so System Health can
    report provider failover (Operational / Degraded / Failing) the same way the
    worker polls Base.
    """
    try:
        from services.api.app.evm_activity_provider import resolve_chain_rpc as _resolve_chain
        urls = _resolve_chain('base').get('rpc_urls') or []
        deduped = list(dict.fromkeys(u for u in urls if u))
        if deduped:
            return deduped
    except Exception:
        pass
    single = _resolve_base_rpc_url()
    return [single] if single else []


def _host_of_rpc(rpc_url: str) -> str:
    """Lowercase hostname for a provider URL (never the path/key/query/credentials)."""
    try:
        from urllib.parse import urlparse as _up
        return (_up(rpc_url).hostname or 'configured')
    except Exception:
        return 'configured'


def _check_rpc() -> dict[str, Any]:
    """Probe the Base RPC provider(s) and report a SaaS-grade status.

    With a single configured provider this behaves exactly as before. With multiple
    Base providers (EVM_RPC_URLS) it reports provider failover truthfully:
      * Operational — the first reachable provider answered.
      * Degraded    — a provider failed or is in 429 backoff but another answered.
      * Failing     — every provider failed/was benched.
    Only provider hosts are ever surfaced — never the URL path, key, or credentials.
    """
    urls = _resolve_base_rpc_urls()
    if not urls:
        _log_rpc_probe(
            rpc_configured=False, rpc_host='unconfigured', rpc_status='unavailable',
            response_time_ms=0, last_error_class='rpc_url_not_configured',
        )
        return _component(
            'unavailable',
            'Base RPC URL is missing in worker service. Set EVM_RPC_URL or STAGING_EVM_RPC_URL.',
            action=(
                'Set EVM_RPC_URL or STAGING_EVM_RPC_URL in the Railway worker service. '
                'For Base mainnet you may instead set EVM_RPC_URL_8453 (or BASE_EVM_RPC_URL) '
                'or EVM_RPC_URLS for multiple providers.'
            ),
        )
    if len(urls) == 1:
        comp = _probe_one_rpc(urls[0])
        comp.pop('_rpc_reason', None)
        return comp
    return _check_rpc_failover(urls)


def _check_rpc_failover(urls: list[str]) -> dict[str, Any]:
    """Probe multiple Base providers in order; report Operational/Degraded/Failing.

    Providers whose host is in an active 429 backoff window are skipped (never
    re-dialed during their window). The first reachable provider wins; if any earlier
    provider failed or was benched, the result is Degraded (failover active) rather
    than Operational. If no provider answers, the result is Failing. Host-only.
    """
    try:
        from services.api.app.evm_activity_provider import host_backoff_active as _host_bo
    except Exception:
        def _host_bo(_host: str) -> bool:  # pragma: no cover - defensive
            return False

    failed: list[tuple[str, str]] = []   # (host, reason)
    benched: list[str] = []              # hosts skipped due to active backoff
    for rpc_url in urls:
        host = _host_of_rpc(rpc_url)
        if _host_bo(host):
            if host not in benched:
                benched.append(host)
            continue
        comp = _probe_one_rpc(rpc_url)
        reason = comp.pop('_rpc_reason', None)
        if comp.get('status') == 'healthy':
            if not failed and not benched:
                return comp  # primary reachable → Operational
            return _rpc_failover_degraded(host, comp, failed, benched)
        failed.append((host, reason or 'failing'))
    return _rpc_all_failing(failed, benched)


def _describe_host_reasons(failed: list[tuple[str, str]], benched: list[str]) -> str:
    parts: list[str] = []
    if failed:
        parts.append('failing: ' + ', '.join(f'{host} ({reason})' for host, reason in failed))
    if benched:
        parts.append('in backoff: ' + ', '.join(benched))
    return '; '.join(parts)


def _rpc_failover_degraded(
    active_host: str, comp: dict[str, Any], failed: list[tuple[str, str]], benched: list[str]
) -> dict[str, Any]:
    """Degraded Base RPC component: one provider failed/benched but another answered."""
    return _component(
        'degraded',
        f'Base RPC: Degraded. Provider failover active — serving via {active_host} '
        f'({_describe_host_reasons(failed, benched)}).',
        metric=comp.get('metric'),
        last_event=comp.get('last_event'),
        action=(
            'A Base RPC provider is rate-limited or failing; monitoring continues via a '
            'healthy provider. Add RPC quota or check the affected provider.'
        ),
    )


def _rpc_all_failing(failed: list[tuple[str, str]], benched: list[str]) -> dict[str, Any]:
    """Failing Base RPC component: every configured provider failed or is benched."""
    detail = _describe_host_reasons(failed, benched) or 'all providers unavailable'
    reason = failed[0][1] if failed else 'rate_limited'
    return _component(
        'failing',
        f'Base RPC: Failing. All providers unavailable ({detail}).',
        action=_rpc_failure_action(reason),
    )


def _probe_one_rpc(rpc_url: str) -> dict[str, Any]:
    """Make one blocking on-chain probe against a single provider URL.

    Returns a status component. A failure component carries a private ``_rpc_reason``
    key (stripped by callers before rendering) so the failover orchestrator can
    describe which provider failed and why. Only the host is ever surfaced.
    """
    # Sanitize: only the host is ever surfaced — never the path/key/query/credentials.
    try:
        from urllib.parse import urlparse as _up
        rpc_host = _up(rpc_url).hostname or 'unconfigured'
    except Exception:
        rpc_host = 'configured'

    started = time.monotonic()

    def _elapsed_ms() -> int:
        return int((time.monotonic() - started) * 1000)

    def _failed(reason: str, retry_after: float | None = None) -> dict[str, Any]:
        # One blocking on-chain call per probe — classify, log (host only), return.
        _log_rpc_probe(
            rpc_configured=True, rpc_host=rpc_host, rpc_status=_rpc_status_class(reason),
            response_time_ms=_elapsed_ms(), last_error_class=reason,
            retry_after_seconds=retry_after,
        )
        if _rpc_status_class(reason) == 'rate_limited':
            # Arm THIS provider host's backoff so the worker and later page refreshes
            # skip only the rate-limited provider, not the healthy ones, instead of
            # re-probing into the rate limit.
            try:
                from services.api.app.evm_activity_provider import record_rpc_rate_limited
                record_rpc_rate_limited(retry_after, host=rpc_host)
            except Exception:
                pass
        result = _rpc_failed(rpc_host, reason)
        if retry_after is not None:
            # Non-rendered hint consumed by _cached_base_rpc_health to back off.
            result['retry_after'] = retry_after
        # Private hint for the failover orchestrator (stripped before rendering).
        result['_rpc_reason'] = reason
        return result

    payload = b'{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}'
    try:
        req = UrlRequest(
            rpc_url,
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        with urlopen(req, timeout=RPC_PROBE_TIMEOUT_SECONDS) as resp:
            import json as _json
            body = _json.loads(resp.read())
            if isinstance(body, dict) and body.get('error'):
                rpc_err = body['error']
                err_msg = str(rpc_err.get('message', '')) if isinstance(rpc_err, dict) else str(rpc_err)
                err_code = rpc_err.get('code', 0) if isinstance(rpc_err, dict) else 0
                if err_code in (-32000, -32003) or 'unauthorized' in err_msg.lower() or 'invalid key' in err_msg.lower():
                    reason = 'unauthorized_key'
                elif err_code == 429 or 'rate' in err_msg.lower() or 'too many' in err_msg.lower():
                    reason = 'rate_limited'
                else:
                    reason = 'provider_error'
                return _failed(reason)
            block_hex = body.get('result')
            if block_hex:
                block_num = int(block_hex, 16)
                elapsed_ms = _elapsed_ms()
                checked_at = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
                _log_rpc_probe(
                    rpc_configured=True, rpc_host=rpc_host, rpc_status='healthy',
                    response_time_ms=elapsed_ms, last_error_class=None,
                )
                # metric carries latest block + response time; last_event is the
                # timestamp of this successful check (rendered by the SaaS page).
                return _component(
                    'healthy',
                    f'eth_blockNumber succeeded (host: {rpc_host}).',
                    metric=f'block #{block_num} · {elapsed_ms}ms',
                    last_event=checked_at,
                )
            return _failed('empty_result')
    except HTTPError as exc:
        if exc.code in (401, 403):
            reason = f'unauthorized_key (HTTP {exc.code})'
        elif exc.code == 429:
            reason = f'rate_limited (HTTP {exc.code})'
            # Honor Retry-After so repeated page refreshes back off for as long as
            # the provider asked, instead of re-probing into the rate limit.
            return _failed(reason, retry_after=_parse_retry_after(getattr(exc, 'headers', None)))
        else:
            reason = f'http_{exc.code}'
        return _failed(reason)
    except (URLError, OSError) as exc:
        err_str = str(getattr(exc, 'reason', exc)).lower()
        if 'timed out' in err_str or 'timeout' in err_str:
            reason = 'timeout'
        elif any(s in err_str for s in ('name or service not known', 'nodename nor servname', 'getaddrinfo failed', 'name resolution')):
            reason = 'bad_url_or_hostname'
        elif 'connection refused' in err_str:
            reason = 'connection_refused'
        else:
            reason = 'network_error'
        return _failed(reason)
    except Exception as exc:
        return _failed(_sanitize_error(exc))


def _reset_rpc_health_cache() -> None:
    """Clear the cached Base RPC health probe (used by tests/ops)."""
    _RPC_HEALTH_CACHE.clear()


def _rpc_backoff_component(status: dict[str, Any], rpc_host: str) -> dict[str, Any]:
    """Failing Base RPC component shown while a provider HTTP 429 backoff is active.

    Surfaces the backoff window so operators see the probe was intentionally
    skipped (not a fresh failure) and when it will retry. Host only — no secrets.
    """
    until = status.get('backoff_until') or 'shortly'
    return _component(
        'failing',
        f'Base RPC: Failing. RPC provider is in backoff until {until} after HTTP 429. (host: {rpc_host})',
        action='Provider is rate-limiting. Increase RPC quota or reduce polling frequency.',
    )


def _cached_base_rpc_health(force: bool = False) -> dict[str, Any]:
    """Return the Base RPC health probe, cached for ``RPC_HEALTH_CACHE_TTL_SECONDS``.

    The /ops/system-health page is refreshed frequently; without caching, every
    refresh fires a live ``eth_blockNumber`` call which — combined with worker
    polling — pushed the provider over its rate limit and made Base RPC flap to
    "failing". Caching one probe for a short TTL keeps the page truthful (the
    cached result is a real probe, never simulated) without hammering the
    provider. The cache key is the resolved RPC URL so an env change invalidates
    it immediately. Pass ``force=True`` to bypass the cache.
    """
    key = _resolve_base_rpc_url() or '<unconfigured>'
    # A recorded HTTP 429 backoff (armed by the worker or a prior probe) means we
    # must NOT call the provider again — surface the backoff window truthfully and
    # skip the live eth_blockNumber call. cache_hit=true: this is a replayed state.
    try:
        from services.api.app.evm_activity_provider import (
            rpc_provider_backoff_active as _bo_active,
            rpc_provider_backoff_status as _bo_status,
        )
        if not force and _bo_active():
            _st = _bo_status()
            try:
                from urllib.parse import urlparse as _up
                _host = _up(key).hostname or 'configured'
            except Exception:
                _host = 'configured'
            _log_rpc_probe(
                rpc_configured=True, rpc_host=_host, rpc_status='rate_limited',
                response_time_ms=0, last_error_class='provider_backoff_active', cache_hit=True,
            )
            return _rpc_backoff_component(_st, _host)
    except Exception:
        pass
    now_mono = time.monotonic()
    entry = _RPC_HEALTH_CACHE.get('entry')
    if (
        not force
        and RPC_HEALTH_CACHE_TTL_SECONDS > 0
        and entry is not None
        and entry.get('key') == key
        and now_mono < float(entry.get('expires_monotonic', 0.0))
    ):
        # Serve the cached probe (no live RPC call). Replay the original probe's
        # structured log line marked cache_hit=true so the served response stays
        # observable without re-hitting the provider.
        log_fields = entry.get('log_fields')
        if log_fields:
            _log_rpc_probe(cache_hit=True, **log_fields)
        return dict(entry['result'])
    result = _check_rpc()
    # A rate-limited probe may carry a Retry-After hint; cache it for at least that
    # long so the status page honors the provider's backoff instead of hammering it.
    ttl = float(RPC_HEALTH_CACHE_TTL_SECONDS)
    retry_after = result.get('retry_after') if isinstance(result, dict) else None
    if isinstance(retry_after, (int, float)) and retry_after > ttl:
        ttl = float(retry_after)
    _RPC_HEALTH_CACHE['entry'] = {
        'key': key,
        'expires_monotonic': now_mono + ttl,
        'result': result,
        # Captured from this live probe so a later cache hit replays the same line.
        'log_fields': dict(_LAST_RPC_PROBE_LOG),
    }
    return dict(result)


def _worker_enabled_state() -> dict[str, Any]:
    """Resolve worker-enabled exactly as the worker does (shared resolver).

    Falls back to a fail-closed disabled state if the import is unavailable so the
    status page never claims live monitoring is running when it cannot confirm it.
    """
    try:
        from services.api.app.worker_enable import resolve_worker_enabled
        return resolve_worker_enabled()
    except Exception:
        return {'enabled': False, 'source': 'none', 'env_var': None}


_WORKER_ENABLE_HINT = (
    'Set STAGING_WORKER_ENABLED=true (or WORKER_ENABLED / MONITORING_WORKER_ENABLED / '
    'LIVE_MODE_ENABLED) in the worker service to enable live monitoring.'
)


def _check_worker(connection: Any, workspace_id: str | None) -> dict[str, Any]:
    worker_enabled = _worker_enabled_state()['enabled']
    try:
        if workspace_id:
            row = connection.execute(
                "SELECT last_heartbeat_at FROM monitoring_heartbeats "
                "WHERE workspace_id = %s ORDER BY last_heartbeat_at DESC LIMIT 1",
                (workspace_id,),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT last_heartbeat_at FROM monitoring_heartbeats "
                "ORDER BY last_heartbeat_at DESC LIMIT 1"
            ).fetchone()
    except Exception:
        row = None

    last_hb = None
    if row:
        val = row.get('last_heartbeat_at') if isinstance(row, dict) else (row[0] if row else None)
        if val is not None:
            last_hb = val.isoformat() if isinstance(val, datetime) else str(val)

    age = _age_seconds(last_hb)
    if last_hb is None:
        if worker_enabled:
            return _component(
                'failing',
                'Worker heartbeat not received. Worker is configured but not reporting.',
                action='Check the worker service is running and that a worker-enable flag is set (e.g. STAGING_WORKER_ENABLED=true).',
            )
        return _component(
            'unavailable',
            'Live monitoring is disabled (no worker-enable flag set).',
            action=_WORKER_ENABLE_HINT,
        )

    if age is not None and age <= POLL_INTERVAL_SECONDS * 2:
        # Fresh heartbeat proves the process is alive — but "alive" is not "monitoring".
        # When no enable flag is set, the loop runs no live polling, so we must not
        # render the worker as healthy/Operational (that would imply live monitoring).
        if not worker_enabled:
            return _component(
                'degraded',
                'Worker process is running, but live monitoring is disabled.',
                age=_human_age(last_hb),
                last_event=last_hb,
                action=_WORKER_ENABLE_HINT,
            )
        return _component(
            'healthy',
            f'Worker heartbeat is fresh ({_human_age(last_hb)}).',
            age=_human_age(last_hb),
            last_event=last_hb,
        )
    if age is not None and age <= WORKER_HEARTBEAT_STALE_SECONDS:
        return _component(
            'degraded',
            f'Worker heartbeat is recent but approaching stale ({_human_age(last_hb)}).',
            age=_human_age(last_hb),
            last_event=last_hb,
        )
    return _component(
        'degraded',
        f'Worker heartbeat is stale ({_human_age(last_hb)}). Worker may have stopped.',
        age=_human_age(last_hb),
        last_event=last_hb,
        action='Check the worker service logs in Railway.',
    )


def _check_live_polling(connection: Any, workspace_id: str | None) -> dict[str, Any]:
    """Check last monitoring poll time (monitoring_polls or monitoring_runs)."""
    last_poll = None
    try:
        if workspace_id:
            row = connection.execute(
                "SELECT poll_started_at FROM monitoring_polls "
                "WHERE workspace_id = %s ORDER BY poll_started_at DESC LIMIT 1",
                (workspace_id,),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT poll_started_at FROM monitoring_polls "
                "ORDER BY poll_started_at DESC LIMIT 1"
            ).fetchone()
        if row:
            val = row.get('poll_started_at') if isinstance(row, dict) else (row[0] if row else None)
            if val is not None:
                last_poll = val.isoformat() if isinstance(val, datetime) else str(val)
    except Exception:
        pass

    if last_poll is None:
        try:
            if workspace_id:
                row = connection.execute(
                    "SELECT started_at FROM monitoring_runs "
                    "WHERE workspace_id = %s ORDER BY started_at DESC LIMIT 1",
                    (workspace_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT started_at FROM monitoring_runs "
                    "ORDER BY started_at DESC LIMIT 1"
                ).fetchone()
            if row:
                val = row.get('started_at') if isinstance(row, dict) else (row[0] if row else None)
                if val is not None:
                    last_poll = val.isoformat() if isinstance(val, datetime) else str(val)
        except Exception:
            pass

    if last_poll is None:
        return _component('unavailable', 'No polling records found.', action='Ensure worker is running and targets are configured.')
    age = _age_seconds(last_poll)
    if age is not None and age <= POLL_INTERVAL_SECONDS * 3:
        return _component('healthy', f'Live polling is active ({_human_age(last_poll)}).', age=_human_age(last_poll), last_event=last_poll)
    return _component('degraded', f'Last poll is stale ({_human_age(last_poll)}).', age=_human_age(last_poll), last_event=last_poll, action='Check worker polling loop.')


def _check_telemetry(connection: Any, workspace_id: str | None) -> dict[str, Any]:
    try:
        if workspace_id:
            row = connection.execute(
                "SELECT observed_at FROM telemetry_events "
                "WHERE workspace_id = %s ORDER BY observed_at DESC LIMIT 1",
                (workspace_id,),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT observed_at FROM telemetry_events "
                "ORDER BY observed_at DESC LIMIT 1"
            ).fetchone()
        if row:
            val = row.get('observed_at') if isinstance(row, dict) else (row[0] if row else None)
            last_ts = val.isoformat() if isinstance(val, datetime) else str(val) if val else None
        else:
            last_ts = None
    except Exception:
        return _component('unavailable', 'Telemetry table not accessible.', action='Check database migrations.')

    if last_ts is None:
        return _component('unavailable', 'No telemetry events received.', action='Check worker, RPC connectivity, and monitoring targets.')

    age = _age_seconds(last_ts)
    if age is not None and age <= TELEMETRY_STALE_SECONDS:
        return _component('healthy', f'Telemetry is fresh ({_human_age(last_ts)}).', age=_human_age(last_ts), last_event=last_ts)
    return _component(
        'degraded',
        f'Last telemetry is stale ({_human_age(last_ts)}). Worker may be running but chain data is not flowing.',
        age=_human_age(last_ts),
        last_event=last_ts,
        action='Check EVM_RPC_URL connectivity and whether monitored addresses have on-chain activity.',
    )


def _check_detection(connection: Any, workspace_id: str | None) -> dict[str, Any]:
    last_ts = None
    for table, col in [('detection_events', 'created_at'), ('detections', 'created_at')]:
        try:
            if workspace_id:
                row = connection.execute(
                    f"SELECT {col} FROM {table} WHERE workspace_id = %s ORDER BY {col} DESC LIMIT 1",
                    (workspace_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    f"SELECT {col} FROM {table} ORDER BY {col} DESC LIMIT 1"
                ).fetchone()
            if row:
                val = row.get(col) if isinstance(row, dict) else (row[0] if row else None)
                if val is not None:
                    last_ts = val.isoformat() if isinstance(val, datetime) else str(val)
                    break
        except Exception:
            continue

    if last_ts is None:
        return _component(
            'unavailable',
            'No detection events found.',
            action='Detection requires live telemetry. Check telemetry ingestion first.',
        )

    age = _age_seconds(last_ts)
    if age is not None and age <= DETECTION_STALE_SECONDS:
        return _component('healthy', f'Detection is recent ({_human_age(last_ts)}).', age=_human_age(last_ts), last_event=last_ts)
    return _component(
        'degraded',
        f'Last detection is stale ({_human_age(last_ts)}). Telemetry may be flowing but no detections triggered.',
        age=_human_age(last_ts),
        last_event=last_ts,
        action='Check detection rules and whether monitored wallets have relevant on-chain activity.',
    )


def _check_alert_delivery() -> dict[str, Any]:
    try:
        from services.api.app.domains import alert_delivery
        snapshot = alert_delivery.health_snapshot()
        ready = bool(snapshot.get('ready'))
        outbox = snapshot.get('outbox') or {}
        pending = outbox.get('pending') or 0
        dead_letter = outbox.get('dead_letter') or 0
        if ready:
            msg = 'Alert delivery is healthy.'
            if pending:
                msg += f' Outbox pending: {pending}.'
            if dead_letter:
                msg += f' Dead-letter: {dead_letter}.'
            status = 'healthy' if not dead_letter else 'degraded'
            return _component(status, msg, metric=f'pending={pending}, dead_letter={dead_letter}')
        return _component(
            'degraded',
            'Alert delivery is not ready.',
            action='Check Redis connectivity for alert stream delivery.',
        )
    except Exception as exc:
        return _component(
            'unavailable',
            f'Alert delivery check failed ({_sanitize_error(exc)}).',
        )


# ---------------------------------------------------------------------------
# Live chain monitoring section
# ---------------------------------------------------------------------------

def _build_live_chain_monitoring(
    connection: Any, workspace_id: str | None, rpc_check: dict[str, Any] | None = None
) -> dict[str, Any]:
    _worker_state = _worker_enabled_state()
    worker_enabled = _worker_state['enabled']
    worker_enabled_source = _worker_state['source']
    # Use the same Base resolver as _check_rpc and the worker so rpc_configured
    # reflects the endpoint the worker actually polls Base with.
    rpc_url = _resolve_base_rpc_url()
    rpc_configured = bool(rpc_url)
    chain_id_str = (os.getenv('STAGING_EVM_CHAIN_ID') or os.getenv('EVM_CHAIN_ID') or '').strip()
    expected_chain_id = int(chain_id_str) if chain_id_str.isdigit() else 8453

    # Heartbeat
    last_heartbeat_at: str | None = None
    try:
        if workspace_id:
            row = connection.execute(
                "SELECT last_heartbeat_at FROM monitoring_heartbeats "
                "WHERE workspace_id = %s ORDER BY last_heartbeat_at DESC LIMIT 1",
                (workspace_id,),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT last_heartbeat_at FROM monitoring_heartbeats "
                "ORDER BY last_heartbeat_at DESC LIMIT 1"
            ).fetchone()
        if row:
            val = row.get('last_heartbeat_at') if isinstance(row, dict) else (row[0] if row else None)
            if val is not None:
                last_heartbeat_at = val.isoformat() if isinstance(val, datetime) else str(val)
    except Exception:
        pass

    heartbeat_age = _age_seconds(last_heartbeat_at)

    # Last poll
    last_poll_at: str | None = None
    last_successful_poll_at: str | None = None
    latest_polled_block: int | None = None
    try:
        if workspace_id:
            row = connection.execute(
                "SELECT poll_started_at, poll_finished_at, status FROM monitoring_polls "
                "WHERE workspace_id = %s ORDER BY poll_started_at DESC LIMIT 1",
                (workspace_id,),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT poll_started_at, poll_finished_at, status FROM monitoring_polls "
                "ORDER BY poll_started_at DESC LIMIT 1"
            ).fetchone()
        if row:
            d = dict(row) if hasattr(row, 'keys') else {}
            ps = d.get('poll_started_at') or (row[0] if len(row) > 0 else None)
            pf = d.get('poll_finished_at') or (row[1] if len(row) > 1 else None)
            st = d.get('status') or (row[2] if len(row) > 2 else None)
            last_poll_at = ps.isoformat() if isinstance(ps, datetime) else str(ps) if ps else None
            if st == 'success' and pf:
                last_successful_poll_at = pf.isoformat() if isinstance(pf, datetime) else str(pf)
    except Exception:
        pass

    # Telemetry counts
    last_telemetry_at: str | None = None
    recent_telemetry_1h: int = 0
    recent_telemetry_24h: int = 0
    try:
        if workspace_id:
            row = connection.execute(
                "SELECT observed_at FROM telemetry_events WHERE workspace_id = %s ORDER BY observed_at DESC LIMIT 1",
                (workspace_id,),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT observed_at FROM telemetry_events ORDER BY observed_at DESC LIMIT 1"
            ).fetchone()
        if row:
            val = row.get('observed_at') if isinstance(row, dict) else (row[0] if row else None)
            if val is not None:
                last_telemetry_at = val.isoformat() if isinstance(val, datetime) else str(val)
        # Recent counts
        if workspace_id:
            r1 = connection.execute(
                "SELECT COUNT(*) AS cnt FROM telemetry_events WHERE workspace_id = %s AND observed_at >= NOW() - INTERVAL '1 hour'",
                (workspace_id,),
            ).fetchone()
            r24 = connection.execute(
                "SELECT COUNT(*) AS cnt FROM telemetry_events WHERE workspace_id = %s AND observed_at >= NOW() - INTERVAL '24 hours'",
                (workspace_id,),
            ).fetchone()
        else:
            r1 = connection.execute(
                "SELECT COUNT(*) AS cnt FROM telemetry_events WHERE observed_at >= NOW() - INTERVAL '1 hour'"
            ).fetchone()
            r24 = connection.execute(
                "SELECT COUNT(*) AS cnt FROM telemetry_events WHERE observed_at >= NOW() - INTERVAL '24 hours'"
            ).fetchone()
        recent_telemetry_1h = int((r1 or {}).get('cnt') or (r1[0] if r1 else 0) or 0)
        recent_telemetry_24h = int((r24 or {}).get('cnt') or (r24[0] if r24 else 0) or 0)
    except Exception:
        pass

    # Detection counts
    last_detection_at: str | None = None
    recent_detections_1h: int = 0
    recent_detections_24h: int = 0
    for table, col in [('detection_events', 'created_at'), ('detections', 'created_at')]:
        try:
            if workspace_id:
                row = connection.execute(
                    f"SELECT {col} FROM {table} WHERE workspace_id = %s ORDER BY {col} DESC LIMIT 1",
                    (workspace_id,),
                ).fetchone()
            else:
                row = connection.execute(
                    f"SELECT {col} FROM {table} ORDER BY {col} DESC LIMIT 1"
                ).fetchone()
            if row:
                val = row.get(col) if isinstance(row, dict) else (row[0] if row else None)
                if val is not None:
                    last_detection_at = val.isoformat() if isinstance(val, datetime) else str(val)
                    if workspace_id:
                        r1 = connection.execute(
                            f"SELECT COUNT(*) AS cnt FROM {table} WHERE workspace_id = %s AND {col} >= NOW() - INTERVAL '1 hour'",
                            (workspace_id,),
                        ).fetchone()
                        r24 = connection.execute(
                            f"SELECT COUNT(*) AS cnt FROM {table} WHERE workspace_id = %s AND {col} >= NOW() - INTERVAL '24 hours'",
                            (workspace_id,),
                        ).fetchone()
                    else:
                        r1 = connection.execute(
                            f"SELECT COUNT(*) AS cnt FROM {table} WHERE {col} >= NOW() - INTERVAL '1 hour'"
                        ).fetchone()
                        r24 = connection.execute(
                            f"SELECT COUNT(*) AS cnt FROM {table} WHERE {col} >= NOW() - INTERVAL '24 hours'"
                        ).fetchone()
                    recent_detections_1h = int((r1 or {}).get('cnt') or (r1[0] if r1 else 0) or 0)
                    recent_detections_24h = int((r24 or {}).get('cnt') or (r24[0] if r24 else 0) or 0)
                    break
        except Exception:
            continue

    # Build diagnosis
    hb_age = _age_seconds(last_heartbeat_at)
    hb_fresh = hb_age is not None and hb_age <= POLL_INTERVAL_SECONDS * 2
    tel_age = _age_seconds(last_telemetry_at)
    tel_fresh = tel_age is not None and tel_age <= TELEMETRY_STALE_SECONDS

    # Reuse the already-computed Base RPC probe when provided. The probe makes a
    # blocking on-chain call (up to 8s); recomputing it here would multiply the
    # endpoint's response time and is the main reason the client used to time out.
    if rpc_check is None:
        rpc_check = _check_rpc()
    rpc_healthy = rpc_check['status'] == 'healthy'
    # A 'degraded' Base RPC means provider failover is active — a provider is
    # rate-limited/failing but another is still serving chain data, so monitoring is
    # NOT blocked. Treat it as usable so the diagnosis does not claim chain data
    # cannot be fetched.
    rpc_usable = rpc_check['status'] in ('healthy', 'degraded')

    if not worker_enabled:
        if hb_fresh:
            # The process is alive (fresh heartbeat) but no enable flag is set, so
            # the loop runs no live polling. State that plainly — never "disabled"
            # alone when the worker is demonstrably running.
            diagnosis = 'Worker process is running, but live monitoring is disabled. ' + _WORKER_ENABLE_HINT
        else:
            diagnosis = 'Live monitoring is disabled (no worker-enable flag set). ' + _WORKER_ENABLE_HINT
    elif not rpc_configured:
        diagnosis = (
            'EVM RPC URL is not configured. Set EVM_RPC_URL (or EVM_RPC_URL_8453 / BASE_EVM_RPC_URL '
            'with EVM_CHAIN_ID=8453) in the worker service.'
        )
    elif not rpc_usable:
        rpc_msg = rpc_check.get('message', 'RPC probe failed.')
        diagnosis = f'Base RPC is failing: {rpc_msg} Chain data cannot be fetched.'
    elif not rpc_healthy:
        # Degraded: provider failover is active. Surface it without claiming an outage.
        rpc_msg = rpc_check.get('message', 'Base RPC provider failover active.')
        diagnosis = f'Base RPC provider failover active: {rpc_msg} Monitoring continues via a healthy provider.'
    elif last_heartbeat_at is None:
        diagnosis = 'RPC is configured but no worker heartbeat received. Worker may not be running.'
    elif not hb_fresh:
        diagnosis = f'Worker heartbeat is stale ({_human_age(last_heartbeat_at)}). Worker may have stopped.'
    elif last_telemetry_at is None:
        diagnosis = 'Worker is healthy and RPC is reachable, but no telemetry has been ingested. Check monitored targets.'
    elif not tel_fresh:
        diagnosis = f'RPC is healthy and worker is polling, but telemetry ingestion is stale ({_human_age(last_telemetry_at)}).'
    elif last_detection_at is None:
        diagnosis = 'Telemetry is flowing, but no detection events were found. Check detection rules and on-chain activity.'
    elif recent_detections_24h == 0:
        diagnosis = 'Telemetry is flowing, but no detections in the last 24h. Monitoring is running but no new events triggered.'
    else:
        diagnosis = 'All monitored systems are operational. Worker is healthy, RPC is reachable, telemetry is flowing, detections are running.'

    return {
        'expected_chain_id': expected_chain_id,
        'rpc_configured': rpc_configured,
        'latest_rpc_block': rpc_check.get('metric'),
        'worker_enabled': worker_enabled,
        'worker_enabled_source': worker_enabled_source,
        'last_heartbeat_at': last_heartbeat_at,
        'heartbeat_age_seconds': int(heartbeat_age) if heartbeat_age is not None else None,
        'heartbeat_age_human': _human_age(last_heartbeat_at),
        'polling_interval_seconds': POLL_INTERVAL_SECONDS,
        'last_poll_at': last_poll_at,
        'last_successful_poll_at': last_successful_poll_at,
        'latest_polled_block': latest_polled_block,
        'last_telemetry_at': last_telemetry_at,
        'last_detection_at': last_detection_at,
        'recent_telemetry_1h': recent_telemetry_1h,
        'recent_telemetry_24h': recent_telemetry_24h,
        'recent_detections_1h': recent_detections_1h,
        'recent_detections_24h': recent_detections_24h,
        'diagnosis': diagnosis,
    }


# ---------------------------------------------------------------------------
# Provider health section
# ---------------------------------------------------------------------------

def _build_providers(
    connection: Any, workspace_id: str | None, rpc_check: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    providers: list[dict[str, Any]] = []

    # Base RPC provider — reuse the already-computed probe to avoid a third
    # blocking on-chain call per request.
    rpc = rpc_check if rpc_check is not None else _check_rpc()
    providers.append({
        'name': 'Base RPC (EVM)',
        'type': 'rpc',
        'status': rpc['status'],
        'message': rpc['message'],
        'action': rpc.get('action'),
    })

    # Redis
    redis = _check_redis()
    providers.append({
        'name': 'Redis',
        'type': 'cache/queue',
        'status': redis['status'],
        'message': redis['message'],
        'action': redis.get('action'),
    })

    # Database
    db = _check_database(connection)
    providers.append({
        'name': 'Database',
        'type': 'postgresql',
        'status': db['status'],
        'message': db['message'],
        'action': db.get('action'),
    })

    # Try provider_health_records
    try:
        if workspace_id:
            rows = connection.execute(
                "SELECT provider_type, status, checked_at, latency_ms FROM provider_health_records "
                "WHERE workspace_id = %s ORDER BY checked_at DESC LIMIT 10",
                (workspace_id,),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT provider_type, status, checked_at, latency_ms FROM provider_health_records "
                "ORDER BY checked_at DESC LIMIT 10"
            ).fetchall()
        for row in rows:
            d = dict(row) if hasattr(row, 'keys') else {}
            provider_type = d.get('provider_type') or (row[0] if row else None)
            status_raw = d.get('status') or (row[1] if len(row) > 1 else None)
            checked_at = d.get('checked_at') or (row[2] if len(row) > 2 else None)
            latency = d.get('latency_ms') or (row[3] if len(row) > 3 else None)
            if not provider_type:
                continue
            status = 'healthy' if str(status_raw or '').lower() in {'ok', 'healthy', 'pass', 'success'} else 'degraded'
            providers.append({
                'name': str(provider_type),
                'type': 'provider',
                'status': status,
                'message': f'Status: {status_raw}',
                'last_event': checked_at.isoformat() if isinstance(checked_at, datetime) else str(checked_at) if checked_at else None,
                'metric': f'{latency}ms' if latency else None,
            })
    except Exception:
        pass

    return providers


# ---------------------------------------------------------------------------
# Events / timeline section
# ---------------------------------------------------------------------------

def _build_events(connection: Any, workspace_id: str | None) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []

    # Recent monitoring worker errors
    try:
        if workspace_id:
            rows = connection.execute(
                "SELECT poll_started_at, error_message FROM monitoring_polls "
                "WHERE workspace_id = %s AND status = 'error' ORDER BY poll_started_at DESC LIMIT 5",
                (workspace_id,),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT poll_started_at, error_message FROM monitoring_polls "
                "WHERE status = 'error' ORDER BY poll_started_at DESC LIMIT 5"
            ).fetchall()
        for row in rows:
            d = dict(row) if hasattr(row, 'keys') else {}
            ts = d.get('poll_started_at') or (row[0] if row else None)
            err = d.get('error_message') or (row[1] if len(row) > 1 else None)
            if ts:
                ts_str = ts.isoformat() if isinstance(ts, datetime) else str(ts)
                events.append({
                    'time': ts_str,
                    'component': 'Polling Worker',
                    'event': f'Poll error: {str(err or "unknown")[:120]}' if err else 'Poll failed.',
                    'severity': 'high',
                    'kind': 'poll_error',
                })
    except Exception:
        pass

    # Recent provider health failures
    try:
        if workspace_id:
            rows = connection.execute(
                "SELECT checked_at, provider_type, status FROM provider_health_records "
                "WHERE workspace_id = %s AND status NOT IN ('ok','healthy','pass') ORDER BY checked_at DESC LIMIT 5",
                (workspace_id,),
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT checked_at, provider_type, status FROM provider_health_records "
                "WHERE status NOT IN ('ok','healthy','pass') ORDER BY checked_at DESC LIMIT 5"
            ).fetchall()
        for row in rows:
            d = dict(row) if hasattr(row, 'keys') else {}
            ts = d.get('checked_at') or (row[0] if row else None)
            pt = d.get('provider_type') or (row[1] if len(row) > 1 else None)
            st = d.get('status') or (row[2] if len(row) > 2 else None)
            if ts:
                ts_str = ts.isoformat() if isinstance(ts, datetime) else str(ts)
                events.append({
                    'time': ts_str,
                    'component': str(pt or 'Provider'),
                    'event': f'Provider health check returned: {st}',
                    'severity': 'medium',
                    'kind': 'provider_health',
                })
    except Exception:
        pass

    events.sort(key=lambda e: e.get('time') or '', reverse=True)
    return events[:20]


# ---------------------------------------------------------------------------
# Reliability snapshot
# ---------------------------------------------------------------------------

def _build_reliability(connection: Any, workspace_id: str | None) -> dict[str, Any]:
    result: dict[str, Any] = {}

    # Active monitoring targets count
    try:
        if workspace_id:
            row = connection.execute(
                "SELECT COUNT(*) AS cnt FROM monitoring_targets WHERE workspace_id = %s AND COALESCE(is_enabled, true) = true",
                (workspace_id,),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT COUNT(*) AS cnt FROM monitoring_targets WHERE COALESCE(is_enabled, true) = true"
            ).fetchone()
        result['active_targets'] = int((row or {}).get('cnt') or (row[0] if row else 0) or 0)
    except Exception:
        result['active_targets'] = None

    # Monitored chains
    try:
        if workspace_id:
            row = connection.execute(
                "SELECT COUNT(DISTINCT chain_id) AS cnt FROM monitoring_targets WHERE workspace_id = %s",
                (workspace_id,),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT COUNT(DISTINCT chain_id) AS cnt FROM monitoring_targets"
            ).fetchone()
        result['monitored_chains'] = int((row or {}).get('cnt') or (row[0] if row else 0) or 0)
    except Exception:
        result['monitored_chains'] = None

    # RPC success rate from provider_health_records (last 100)
    try:
        if workspace_id:
            row = connection.execute(
                "SELECT COUNT(*) FILTER (WHERE status IN ('ok','healthy','pass')) AS ok_cnt, COUNT(*) AS total "
                "FROM provider_health_records WHERE workspace_id = %s AND provider_type LIKE '%%rpc%%'",
                (workspace_id,),
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT COUNT(*) FILTER (WHERE status IN ('ok','healthy','pass')) AS ok_cnt, COUNT(*) AS total "
                "FROM provider_health_records WHERE provider_type LIKE '%%rpc%%'"
            ).fetchone()
        if row:
            d = dict(row) if hasattr(row, 'keys') else {}
            ok = int(d.get('ok_cnt') or (row[0] if row else 0) or 0)
            total = int(d.get('total') or (row[1] if len(row) > 1 else 0) or 0)
            result['rpc_success_rate'] = f'{ok}/{total}' if total > 0 else 'unavailable: no records'
        else:
            result['rpc_success_rate'] = 'unavailable: no records'
    except Exception:
        result['rpc_success_rate'] = 'unavailable: metric not implemented'

    return result


# ---------------------------------------------------------------------------
# Overall status computation
# ---------------------------------------------------------------------------

STATUS_ORDER = {'failing': 0, 'degraded': 1, 'healthy': 2, 'unavailable': 3}


def _aggregate_status(components: dict[str, dict[str, Any]]) -> str:
    statuses = [c.get('status', 'unavailable') for c in components.values()]
    if any(s == 'failing' for s in statuses):
        return 'failing'
    if any(s == 'degraded' for s in statuses):
        return 'degraded'
    if all(s == 'unavailable' for s in statuses):
        return 'unavailable'
    if any(s == 'healthy' for s in statuses):
        return 'degraded' if any(s in {'failing', 'degraded'} for s in statuses) else 'healthy'
    return 'unavailable'


def _build_summary(components: dict[str, dict[str, Any]], chain_monitoring: dict[str, Any]) -> str:
    failing = [k for k, c in components.items() if c.get('status') == 'failing']
    degraded = [k for k, c in components.items() if c.get('status') == 'degraded']
    if not failing and not degraded:
        return 'All monitored systems are operational.'
    parts = []
    if failing:
        parts.append(f'{", ".join(failing).replace("_", " ")} is failing')
    if degraded:
        parts.append(f'{", ".join(degraded).replace("_", " ")} is degraded')
    return '; '.join(parts).capitalize() + '.'


def _build_primary_action(components: dict[str, dict[str, Any]]) -> str | None:
    for key in ('base_rpc', 'worker', 'telemetry', 'database', 'redis', 'detection', 'alert_delivery', 'live_polling'):
        comp = components.get(key, {})
        if comp.get('status') in ('failing', 'degraded') and comp.get('action'):
            return comp['action']
    return None


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------

def build_system_health_snapshot(request: Any = None) -> dict[str, Any]:
    from services.api.app.pilot import pg_connection, runtime_environment_identity
    import os

    generated_at = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')

    env_raw = os.getenv('APP_ENV', os.getenv('APP_MODE', 'development')).strip().lower()
    if env_raw in {'production', 'prod'}:
        environment = 'production'
    elif env_raw in {'staging'}:
        environment = 'staging'
    elif env_raw in {'local', 'development', 'dev'}:
        environment = 'local'
    else:
        environment = 'unknown'

    version: str | None = None
    git_commit: str | None = None
    try:
        from services.api.app.main import BACKEND_BUILD_ID, BACKEND_GIT_COMMIT
        version = BACKEND_BUILD_ID
        git_commit = BACKEND_GIT_COMMIT
    except Exception:
        pass

    # Resolve workspace_id safely
    workspace_id: str | None = None
    if request is not None:
        try:
            workspace_id = str(request.headers.get('x-workspace-id') or '').strip() or None
        except Exception:
            pass

    components: dict[str, dict[str, Any]] = {}
    components['api'] = _check_api()

    try:
        with pg_connection() as connection:
            components['database'] = _check_database(connection)
            components['redis'] = _check_redis()
            components['worker'] = _check_worker(connection, workspace_id)
            # Compute the Base RPC probe once and reuse it everywhere it is needed
            # (component, live chain monitoring, providers) to keep the endpoint fast.
            # The probe is cached for a short TTL so repeated page refreshes do not
            # re-hit the provider on every request.
            base_rpc_check = _cached_base_rpc_health()
            components['base_rpc'] = base_rpc_check
            components['live_polling'] = _check_live_polling(connection, workspace_id)
            components['telemetry'] = _check_telemetry(connection, workspace_id)
            components['detection'] = _check_detection(connection, workspace_id)
            components['alert_delivery'] = _check_alert_delivery()

            chain_monitoring = _build_live_chain_monitoring(connection, workspace_id, rpc_check=base_rpc_check)
            events = _build_events(connection, workspace_id)
            providers = _build_providers(connection, workspace_id, rpc_check=base_rpc_check)
            reliability = _build_reliability(connection, workspace_id)
    except Exception as exc:
        # DB connection failed entirely
        components['database'] = _component(
            'failing',
            f'Database connection failed ({_sanitize_error(exc)}).',
            action='Verify DATABASE_URL is configured correctly.',
        )
        components.setdefault('redis', _check_redis())
        components.setdefault('worker', _component('unavailable', 'Cannot check worker: database unavailable.'))
        components.setdefault('base_rpc', _cached_base_rpc_health())
        components.setdefault('live_polling', _component('unavailable', 'Cannot check polling: database unavailable.'))
        components.setdefault('telemetry', _component('unavailable', 'Cannot check telemetry: database unavailable.'))
        components.setdefault('detection', _component('unavailable', 'Cannot check detection: database unavailable.'))
        components.setdefault('alert_delivery', _check_alert_delivery())
        _fallback_worker_state = _worker_enabled_state()
        chain_monitoring = {
            'expected_chain_id': 8453,
            'rpc_configured': bool(_resolve_base_rpc_url()),
            'latest_rpc_block': None,
            'worker_enabled': _fallback_worker_state['enabled'],
            'worker_enabled_source': _fallback_worker_state['source'],
            'last_heartbeat_at': None,
            'heartbeat_age_seconds': None,
            'heartbeat_age_human': 'unavailable',
            'polling_interval_seconds': POLL_INTERVAL_SECONDS,
            'last_poll_at': None,
            'last_successful_poll_at': None,
            'latest_polled_block': None,
            'last_telemetry_at': None,
            'last_detection_at': None,
            'recent_telemetry_1h': 0,
            'recent_telemetry_24h': 0,
            'recent_detections_1h': 0,
            'recent_detections_24h': 0,
            'diagnosis': 'Database is unavailable. Cannot evaluate chain monitoring status.',
        }
        events = []
        providers = [
            {'name': 'Database', 'type': 'postgresql', 'status': 'failing', 'message': f'Connection failed ({_sanitize_error(exc)}).'},
        ]
        reliability = {}

    overall_status = _aggregate_status(components)
    summary = _build_summary(components, chain_monitoring)
    primary_action = _build_primary_action(components)

    return {
        'generated_at': generated_at,
        'environment': environment,
        'version': version,
        'git_commit': git_commit,
        'overall_status': overall_status,
        'summary': summary,
        'primary_action': primary_action,
        'components': components,
        'live_chain_monitoring': chain_monitoring,
        'events': events,
        'providers': providers,
        'reliability': reliability,
    }
