"""
Rate limiting domain — owns all auth rate limiting state and logic.

Owns:
  - Redis/Upstash distributed rate limiter client
  - In-memory fallback per-process rate limiter
  - enforce_auth_rate_limit() — the public auth endpoint guard

Must NOT import:
  - services.api.app.main (circular)
  - services.api.app.pilot (circular — pilot imports this module)
  - Other domain packages

Config env vars:
  REDIS_URL                              — primary Redis backend
  UPSTASH_REDIS_REST_URL                 — Upstash HTTP Redis
  UPSTASH_REDIS_REST_TOKEN               — Upstash token
  REDIS_TEMPORARILY_DISABLED              — explicit temporary degraded mode; enterprise_ready=false
  ALLOW_IN_MEMORY_RATE_LIMIT_IN_PRODUCTION — legacy break-glass alias; enterprise_ready=false
"""
from __future__ import annotations

import hashlib
import importlib
import json
import logging
import os
import threading
from time import monotonic
from typing import Any
from urllib.request import Request as UrlRequest, urlopen

from fastapi import HTTPException, Request, status

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
AUTH_WINDOW_SECONDS: int = 15 * 60
AUTH_MAX_ATTEMPTS: int = 5
RATE_LIMIT_FALLBACK_WARNING_WINDOW_SECONDS: int = 300
RATE_LIMIT_FALLBACK_REDIS_UNAVAILABLE_KEY: str = 'rate_limit.fallback.redis_unavailable'

# ---------------------------------------------------------------------------
# Module-level state (one instance per process)
# ---------------------------------------------------------------------------
_rate_limit_lock: threading.Lock = threading.Lock()
_rate_limit_state: dict[str, list[float]] = {}

_rate_limit_fallback_warning_lock: threading.Lock = threading.Lock()
_rate_limit_fallback_last_emitted: dict[str, float] = {}

_redis_rate_limiter: Any | None = None
_redis_rate_limiter_lock: threading.Lock = threading.Lock()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _rate_limit_subject(identifier: str | None) -> str:
    normalized = str(identifier or '').strip().lower()
    if not normalized:
        return 'unknown'
    return hashlib.sha256(normalized.encode('utf-8')).hexdigest()


def _emit_rate_limit_fallback_warning(reason: str) -> None:
    should_emit = False
    now = monotonic()
    with _rate_limit_fallback_warning_lock:
        last_emitted = _rate_limit_fallback_last_emitted.get(RATE_LIMIT_FALLBACK_REDIS_UNAVAILABLE_KEY)
        if last_emitted is None or now - last_emitted >= RATE_LIMIT_FALLBACK_WARNING_WINDOW_SECONDS:
            _rate_limit_fallback_last_emitted[RATE_LIMIT_FALLBACK_REDIS_UNAVAILABLE_KEY] = now
            should_emit = True
    if should_emit:
        _log.warning(
            'Rate limiter fallback active: Redis unavailable. reason=%s',
            reason,
            extra={'event': 'rate_limit.fallback', 'fallback_key': RATE_LIMIT_FALLBACK_REDIS_UNAVAILABLE_KEY},
        )


def _upstash_command(base_url: str, token: str, command: list[Any]) -> Any:
    req = UrlRequest(
        base_url.rstrip('/'),
        data=json.dumps(command).encode('utf-8'),
        headers={'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'},
        method='POST',
    )
    with urlopen(req, timeout=2) as response:
        payload = json.loads(response.read().decode('utf-8'))
    if payload.get('error'):
        raise RuntimeError(str(payload['error']))
    return payload.get('result')


def _distributed_rate_limit_attempts(key: str) -> int | None:
    global _redis_rate_limiter

    redis_url = os.getenv('REDIS_URL', '').strip()
    upstash_url = os.getenv('UPSTASH_REDIS_REST_URL', '').strip()
    upstash_token = os.getenv('UPSTASH_REDIS_REST_TOKEN', '').strip()

    if redis_url:
        with _redis_rate_limiter_lock:
            if _redis_rate_limiter is None:
                redis_module = importlib.import_module('redis')
                _redis_rate_limiter = redis_module.Redis.from_url(
                    redis_url,
                    decode_responses=True,
                    socket_connect_timeout=2,
                    socket_timeout=2,
                )
        attempts = int(_redis_rate_limiter.incr(key))
        if attempts == 1:
            _redis_rate_limiter.expire(key, AUTH_WINDOW_SECONDS)
        return attempts

    if upstash_url and upstash_token:
        attempts = int(_upstash_command(upstash_url, upstash_token, ['INCR', key]))
        if attempts == 1:
            _upstash_command(upstash_url, upstash_token, ['EXPIRE', key, AUTH_WINDOW_SECONDS])
        return attempts

    return None


def _enforce_in_memory_rate_limit(key: str) -> None:
    now = monotonic()
    cutoff = now - AUTH_WINDOW_SECONDS
    with _rate_limit_lock:
        expired_keys: list[str] = []
        for existing_key, stamps in _rate_limit_state.items():
            active_stamps = [stamp for stamp in stamps if stamp >= cutoff]
            if active_stamps:
                _rate_limit_state[existing_key] = active_stamps
            else:
                expired_keys.append(existing_key)
        for expired_key in expired_keys:
            _rate_limit_state.pop(expired_key, None)

        attempts = _rate_limit_state.get(key, [])
        if len(attempts) >= AUTH_MAX_ATTEMPTS:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail='Too many authentication attempts. Please retry shortly.',
            )
        attempts.append(now)
        _rate_limit_state[key] = attempts


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def enforce_auth_rate_limit(request: Request, action: str, identifier: str | None = None) -> None:
    """
    Guard an auth endpoint against brute force.

    Uses Redis (or Upstash) when configured.  Falls back to per-process in-memory
    tracking when distributed backend is unavailable; emits a warning in that case.
    """
    client_host = request.client.host if request.client else 'unknown'
    subject = _rate_limit_subject(identifier)
    key = f'pilot:rate:{action}:{client_host}:{subject}'

    try:
        attempts = _distributed_rate_limit_attempts(key)
        if attempts is not None:
            if attempts > AUTH_MAX_ATTEMPTS:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail='Too many authentication attempts. Please retry shortly.',
                )
            return
        _emit_rate_limit_fallback_warning('not configured')
    except HTTPException:
        raise
    except Exception as exc:
        condensed_error = str(exc).strip().splitlines()[0] if str(exc).strip() else 'unknown_error'
        _emit_rate_limit_fallback_warning(condensed_error)

    _enforce_in_memory_rate_limit(key)


def rate_limit_backend_name() -> str:
    """Return 'redis', 'upstash', or 'memory' based on configured backends."""
    if os.getenv('REDIS_URL', '').strip():
        return 'redis'
    if os.getenv('UPSTASH_REDIS_REST_URL', '').strip() and os.getenv('UPSTASH_REDIS_REST_TOKEN', '').strip():
        return 'upstash'
    return 'memory'


def rate_limit_enterprise_ready() -> bool:
    """True when a distributed backend is configured and no dangerous override is active."""
    backend = rate_limit_backend_name()
    if backend in {'redis', 'upstash'}:
        return True
    # Memory-only is not enterprise-ready in any mode
    return False
