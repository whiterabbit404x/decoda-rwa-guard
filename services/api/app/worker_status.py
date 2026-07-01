"""Separated worker status for monitoring runtime surfaces.

Decoda runs TWO independent ingestion workers plus a provider-health signal,
and the customer-facing UI must keep them distinct (CLAUDE.md truthfulness
rules: heartbeat, poll, and telemetry are separate facts).

  * Stable RPC polling worker  — the ~300s loop that always runs and writes the
    RPC polling heartbeat. This is the canonical transfer-detection path and is
    independent of the realtime worker.
  * Realtime WebSocket worker  — optional, gated by ``BASE_REALTIME_ENABLED``.
    When disabled it idles (paused); when enabled it may be rate-limited by the
    provider (e.g. QuickNode WSS HTTP 429) and trip its circuit breaker.
  * Provider realtime health   — whether the realtime provider (WSS) is healthy,
    rate-limited, or in a cooldown window.

The product previously collapsed these into a single "worker heartbeat", so a
paused or rate-limited realtime worker made the whole monitoring source look
dead ("worker heartbeat is stale" / limited coverage) even while stable polling
was alive and detecting transfers. This module derives a truthful, separated
status from canonical facts so the UI can say
"Stable polling active. Realtime WebSocket paused." instead.

Pure functions only — no DB or network access — so the logic is unit-testable.
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Any

# detected_by values written into telemetry_events.payload_json. Kept here so the
# API, runtime status, and telemetry list route classify detection paths the same way.
REALTIME_DETECTED_BY: tuple[str, ...] = (
    'realtime_websocket',
    'realtime_backfill',
    # HTTP fast-tail fallback. 'quicknode_http_fast_tail' is the canonical tag
    # (base_realtime_ingestor.HTTP_FAST_TAIL_SOURCE); 'realtime_http_fast_tail' is
    # retained so any rows persisted before the rename still classify as realtime.
    'quicknode_http_fast_tail',
    'realtime_http_fast_tail',
)
STABLE_DETECTED_BY = 'stable_rpc_polling'

_TRUE_VALUES = {'1', 'true', 'yes', 'on'}

# Mirror run_realtime_worker._resolve_int_env default for the realtime heartbeat
# window so "stale" means the same thing across worker and API.
DEFAULT_REALTIME_HEARTBEAT_TTL_SECONDS = 180

# The stable RPC polling loop runs on a MULTI-MINUTE cadence (per-target interval,
# ~5 minutes in production), so its staleness threshold must be far more forgiving
# than the realtime heartbeat TTL. A heartbeat or poll only 4–5 minutes old at a
# 5-minute polling cadence is HEALTHY, not stale — flagging it stale is the exact
# contradiction this module exists to prevent. Default 900s (15m), overridable via
# MONITORING_STABLE_POLL_STALE_SECONDS, and never stricter than two poll cycles or
# ten minutes.
DEFAULT_STABLE_POLL_STALE_SECONDS = 900
STABLE_POLL_STALE_FLOOR_SECONDS = 600


def stable_poll_stale_threshold_seconds(poll_interval_seconds: int | None = None) -> int:
    """Seconds after which the stable RPC polling worker is treated as stale.

    Fail-open for a normal cadence: returns at least ``max(2 * poll_interval, 600)``
    (two poll cycles / ten minutes) and defaults to 900s, overridable via
    ``MONITORING_STABLE_POLL_STALE_SECONDS``. The SAME threshold must be used by the
    top banner, worker-status card, limitation text, and runtime summary so they never
    disagree about whether stable polling is stale.
    """
    raw = os.getenv('MONITORING_STABLE_POLL_STALE_SECONDS')
    try:
        configured = int(raw) if raw not in (None, '') else DEFAULT_STABLE_POLL_STALE_SECONDS
    except (TypeError, ValueError):
        configured = DEFAULT_STABLE_POLL_STALE_SECONDS
    try:
        interval = max(0, int(poll_interval_seconds or 0))
    except (TypeError, ValueError):
        interval = 0
    return max(configured, interval * 2, STABLE_POLL_STALE_FLOOR_SECONDS, 1)


def realtime_enabled() -> bool:
    """True only when ``BASE_REALTIME_ENABLED`` is explicitly truthy.

    Fail-closed: any unset/blank/unknown value means realtime is disabled, which
    matches ``run_realtime_worker._resolve_bool_env(default=False)``.
    """
    raw = (os.getenv('BASE_REALTIME_ENABLED') or '').strip().lower()
    return raw in _TRUE_VALUES


# --- Live-coverage-gap reason selection ------------------------------------------
# When no fresh live *coverage* telemetry row exists, the runtime must pick a reason
# code that is truthful about WHY, so the customer-facing limitation never blames RPC
# connectivity while the stable RPC polling worker is demonstrably alive.
#
#   * realtime paused + stable polling active  -> realtime is intentionally off; stable
#     RPC polling is the detection path. A quiet coverage gap here is normal, NOT an RPC
#     problem. Surfaced as "Realtime paused; stable polling active".
#   * realtime enabled + stable polling active -> the polling loop is live and simply
#     awaiting new on-chain activity on monitored addresses. Also NOT an RPC problem.
#   * stable polling stale/missing + provider/RPC checks failing -> the ONE case where
#     "Check EVM_RPC_URL connectivity" is truthful.
#
# These map to customer-facing limitation copy in apps/web/app/runtime-summary-context.tsx.
REALTIME_PAUSED_STABLE_ACTIVE_REASON = 'realtime_paused_stable_polling_active'
STABLE_ACTIVE_AWAITING_COVERAGE_REASON = 'stable_polling_active_awaiting_coverage'
NO_LIVE_COVERAGE_RPC_REASON = 'no_fresh_live_coverage_telemetry'


def live_coverage_gap_reason(
    *,
    stable_polling_active: bool,
    realtime_is_enabled: bool,
    provider_failing: bool,
) -> str:
    """Pick a truthful reason code for a missing/stale live-coverage-telemetry gap.

    Requirements (telemetry-limitation task + CLAUDE.md truthfulness rules):
      1-2. When the stable RPC polling worker is proven alive (fresh heartbeat OR poll),
           a missing coverage telemetry row is NOT an RPC connectivity problem — never
           emit the "Check EVM_RPC_URL" reason. With realtime paused the truthful reason
           is simply "Realtime paused; stable polling active".
      3.   ``no_fresh_live_coverage_telemetry`` (which carries the "Check EVM_RPC_URL"
           warning) is only returned when stable polling is stale/missing AND the
           provider/RPC checks are actually failing.
    """
    if stable_polling_active:
        return (
            STABLE_ACTIVE_AWAITING_COVERAGE_REASON
            if realtime_is_enabled
            else REALTIME_PAUSED_STABLE_ACTIVE_REASON
        )
    # Stable polling is stale/missing. Only blame EVM_RPC_URL when the provider/RPC
    # checks are actually failing (requirement 3); otherwise stay non-alarming.
    if provider_failing:
        return NO_LIVE_COVERAGE_RPC_REASON
    return (
        STABLE_ACTIVE_AWAITING_COVERAGE_REASON
        if realtime_is_enabled
        else REALTIME_PAUSED_STABLE_ACTIVE_REASON
    )


def _age_seconds(now: datetime, ts: datetime | None) -> int | None:
    if ts is None:
        return None
    return int((now - ts).total_seconds())


def _iso(ts: datetime | None) -> str | None:
    return ts.isoformat() if ts is not None else None


def _is_future_iso(value: Any, now: datetime) -> bool:
    """True when ``value`` parses to a timestamp after ``now`` (cooldown still open)."""
    if not value:
        return False
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError):
        return False
    if parsed.tzinfo is None:
        # Treat naive timestamps as UTC-aligned with ``now`` to avoid raising.
        parsed = parsed.replace(tzinfo=now.tzinfo)
    try:
        return parsed > now
    except TypeError:
        return False


def build_worker_status(
    *,
    now: datetime,
    realtime_is_enabled: bool,
    stable_last_heartbeat_at: datetime | None,
    stable_last_poll_at: datetime | None,
    heartbeat_ttl_seconds: int,
    realtime_watcher: dict[str, Any] | None = None,
    realtime_last_event_at: datetime | None = None,
    stable_last_coverage_poll_at: datetime | None = None,
) -> dict[str, Any]:
    """Derive a separated, truthful worker status from canonical facts.

    ``stable_last_heartbeat_at`` comes from the RPC polling heartbeat
    (monitoring_heartbeats). ``stable_last_poll_at`` is the monitoring poll
    completion (monitoring_polls). ``stable_last_coverage_poll_at`` is the live
    rpc_polling coverage telemetry timestamp (telemetry_events) — the SAME canonical
    source the Telemetry worker-status card reads for "Last stable poll", so the
    runtime banner and that card agree. Any one of the three being fresh proves the
    stable RPC polling loop is live. The realtime worker only writes
    ``monitoring_watcher_state``; that row is passed as ``realtime_watcher``
    (already json-safe, so timestamps are ISO strings).
    """
    # ``heartbeat_ttl_seconds`` is the STABLE-POLL stale threshold (see
    # ``stable_poll_stale_threshold_seconds``), not the tight realtime heartbeat TTL —
    # callers must pass the forgiving multi-minute value so a 4–5 minute-old heartbeat at
    # a 5-minute cadence never reads as stale.
    ttl = max(int(heartbeat_ttl_seconds or 0), 1)

    # --- Stable RPC polling worker -------------------------------------------------
    # Stable polling is proven live by ANY of: a recent RPC polling heartbeat, a
    # recent monitoring poll completion, or a recent live rpc_polling coverage
    # telemetry row (the same source the Telemetry card shows as "Last stable poll").
    # CLAUDE.md keeps heartbeat and poll as separate facts, but for the *stable
    # polling* verdict any one is sufficient: the heartbeat proves the worker is
    # alive and the poll/coverage proves the monitoring loop ran. Only when ALL are
    # absent/stale is the stable polling worker actually stale — so a lagging
    # heartbeat writer never contradicts a Telemetry page that shows a fresh "Last
    # stable poll" (requirements 1-4).
    stable_age = _age_seconds(now, stable_last_heartbeat_at)
    stable_poll_age = _age_seconds(now, stable_last_poll_at)
    stable_coverage_age = _age_seconds(now, stable_last_coverage_poll_at)
    heartbeat_fresh = stable_age is not None and stable_age <= ttl
    poll_fresh = stable_poll_age is not None and stable_poll_age <= ttl
    coverage_poll_fresh = stable_coverage_age is not None and stable_coverage_age <= ttl
    stable_poll_proof_fresh = poll_fresh or coverage_poll_fresh
    # Age of the freshest stable-polling proof (heartbeat / poll / coverage). This is the
    # single number compared against ``ttl`` for the verdict and exposed for debugging so
    # the banner, card, and runtime summary can be reconciled from one canonical age.
    _proof_ages = [a for a in (stable_age, stable_poll_age, stable_coverage_age) if a is not None]
    stable_poll_age_seconds = min(_proof_ages) if _proof_ages else None
    if heartbeat_fresh or stable_poll_proof_fresh:
        stable_state = 'active'
    elif (
        stable_last_heartbeat_at is None
        and stable_last_poll_at is None
        and stable_last_coverage_poll_at is None
    ):
        stable_state = 'offline'
    else:
        stable_state = 'stale'
    stable_active = stable_state == 'active'

    # --- Realtime WebSocket worker -------------------------------------------------
    watcher = realtime_watcher if isinstance(realtime_watcher, dict) else {}
    metrics = watcher.get('metrics') if isinstance(watcher.get('metrics'), dict) else {}
    provider_rate_limited = bool(metrics.get('rate_limited'))
    next_retry_at = metrics.get('next_retry_at') or None
    provider_host = metrics.get('active_provider_host') or watcher.get('active_provider_host') or None
    watcher_degraded = bool(watcher.get('degraded'))
    watcher_degraded_reason = watcher.get('degraded_reason') or None
    watcher_has_row = bool(watcher)

    if not realtime_is_enabled:
        realtime_state = 'paused'
        realtime_reason = 'BASE_REALTIME_ENABLED_not_true'
    elif provider_rate_limited:
        realtime_state = 'rate_limited'
        realtime_reason = watcher_degraded_reason or 'provider_rate_limited'
    elif watcher_degraded:
        realtime_state = 'degraded'
        realtime_reason = watcher_degraded_reason or 'realtime_worker_degraded'
    elif watcher_has_row:
        realtime_state = 'active'
        realtime_reason = None
    else:
        # Enabled but no heartbeat row yet — worker is starting, not failed.
        realtime_state = 'starting'
        realtime_reason = 'no_realtime_heartbeat_yet'

    # --- Provider realtime health --------------------------------------------------
    if provider_rate_limited:
        provider_state = 'cooldown' if _is_future_iso(next_retry_at, now) else 'rate_limited'
    elif not realtime_is_enabled:
        provider_state = 'not_applicable'
    elif watcher_has_row and not watcher_degraded:
        provider_state = 'healthy'
    else:
        provider_state = 'unknown'

    # --- Truthful headline ---------------------------------------------------------
    # Only mention "heartbeat is stale" when STABLE polling is actually stale.
    if stable_active and not realtime_is_enabled:
        headline = 'Stable polling active. Realtime WebSocket paused.'
    elif stable_active and realtime_state == 'rate_limited':
        headline = 'Stable polling active. Realtime WebSocket rate limited (provider cooldown).'
    elif stable_active and realtime_state == 'active':
        headline = 'Stable polling active. Realtime WebSocket active.'
    elif stable_active and realtime_state == 'degraded':
        headline = 'Stable polling active. Realtime WebSocket degraded.'
    elif stable_active:
        headline = 'Stable polling active.'
    elif stable_state == 'stale':
        headline = 'Stable RPC polling heartbeat is stale.'
    else:
        headline = 'Stable RPC polling worker is not reporting.'

    return {
        'stable_polling': {
            'label': 'Stable RPC Polling',
            'state': stable_state,
            'active': stable_active,
            'last_heartbeat_at': _iso(stable_last_heartbeat_at),
            'last_poll_at': _iso(stable_last_poll_at),
            'last_coverage_poll_at': _iso(stable_last_coverage_poll_at),
            'heartbeat_age_seconds': stable_age,
            'last_poll_age_seconds': stable_poll_age,
            'last_coverage_poll_age_seconds': stable_coverage_age,
            'heartbeat_fresh': heartbeat_fresh,
            'poll_fresh': stable_poll_proof_fresh,
            'heartbeat_ttl_seconds': ttl,
            # Debug / reconciliation fields: the stale threshold actually applied and the
            # freshest-proof age it was compared against. ``status`` mirrors ``state`` so a
            # runtime-status payload can surface ``stable_polling_status`` without re-deriving.
            'stale_threshold_seconds': ttl,
            'age_seconds': stable_poll_age_seconds,
            'status': stable_state,
            # Stable polling is the canonical transfer-detection path; when it is
            # active, transfer detection remains supported regardless of realtime.
            'detection_supported': stable_active,
        },
        'realtime': {
            'label': 'Realtime WebSocket',
            'enabled': bool(realtime_is_enabled),
            'state': realtime_state,
            'last_event_at': _iso(realtime_last_event_at),
            'reason': realtime_reason,
        },
        'provider_realtime': {
            'label': 'Provider realtime status',
            'state': provider_state,
            'rate_limited': provider_rate_limited,
            'next_retry_at': next_retry_at,
            'host': provider_host,
        },
        'headline': headline,
        # Stable polling alive => the monitoring source is NOT dead even if realtime
        # is paused or the realtime provider is rate-limited (requirement 4).
        'monitoring_source_live': stable_active,
    }
