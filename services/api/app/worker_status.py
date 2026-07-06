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
    # Bounded tx-hash import (base_realtime_ingestor._backfill_tx_by_hash): recovers an
    # old transfer below the fast-tail catch-up window without advancing the cursor.
    'realtime_tx_import',
    # HTTP fast-tail fallback. 'quicknode_http_fast_tail' is the canonical tag
    # (base_realtime_ingestor.HTTP_FAST_TAIL_SOURCE); 'realtime_http_fast_tail' is
    # retained so any rows persisted before the rename still classify as realtime.
    'quicknode_http_fast_tail',
    'realtime_http_fast_tail',
    # QuickNode Streams webhook push ingestion (services/api/app/quicknode_streams.py).
    # A separate push-based detection path from the WSS/HTTP-fast-tail realtime
    # worker, but still realtime *telemetry* like the other tags in this tuple.
    'quicknode_stream',
)
STABLE_DETECTED_BY = 'stable_rpc_polling'

# detected_by values that PROVE the realtime WebSocket pipeline delivered a
# detection. Acceptance rule: a test transaction only counts as realtime proof when
# Detected By is Realtime WebSocket or Realtime Backfill — quicknode_http_fast_tail
# and realtime_tx_import classify as realtime *telemetry* (REALTIME_DETECTED_BY) but
# are fallback/recovery paths, and stable_rpc_polling is never realtime proof.
REALTIME_PROOF_DETECTED_BY: tuple[str, ...] = ('realtime_websocket', 'realtime_backfill')


def is_realtime_detection_proof(detected_by: Any) -> bool:
    """True only when ``detected_by`` proves the realtime WSS pipeline detected it.

    Fail-closed: unknown/blank values, the stable polling tag, and the fallback
    tags (HTTP fast-tail, tx import) all return False — a transfer detected while
    realtime was degraded must never be claimed as realtime working.
    """
    return str(detected_by or '').strip().lower() in REALTIME_PROOF_DETECTED_BY

# ingestion_source values written by the stable RPC polling path (ActivityEvent
# ingestion_source / monitoring_event_receipts.ingestion_source). They all mean the
# transfer was detected by the 300s stable polling worker, not the realtime worker.
_STABLE_INGESTION_SOURCES: tuple[str, ...] = ('polling', 'rpc_polling', 'evm_rpc', 'rpc_backfill')


def detected_by_from_ingestion_source(source: Any) -> str:
    """Map a receipt/event ingestion_source to the canonical detected_by tag.

    Used when the realtime worker hits a duplicate: the existing row's
    ingestion_source says WHO detected the tx first, and the duplicate log must
    name it truthfully (``existing_detected_by=stable_rpc_polling`` when the
    stable polling worker got there first). Unknown/missing sources return
    ``'unknown'`` — never a false claim of either detection path.
    """
    src = str(source or '').strip().lower()
    if not src:
        return 'unknown'
    if src in REALTIME_DETECTED_BY:
        return src
    if src in _STABLE_INGESTION_SOURCES or src == STABLE_DETECTED_BY:
        return STABLE_DETECTED_BY
    return src


# source_type / ingestion_method values that name the single-tx import path. The
# ops import-tx endpoint historically wrote source_type='tx_hash_import' with no
# detected_by, which rendered a blank customer-facing "Detected By". Both spellings
# resolve to the canonical realtime_tx_import tag.
_TX_IMPORT_SOURCES: tuple[str, ...] = ('tx_hash_import', 'realtime_tx_import')

# Telemetry event_type values that render as "Wallet transfer detected" in the UI
# and therefore must never have a blank detected_by (acceptance rule).
WALLET_TRANSFER_EVENT_TYPES: tuple[str, ...] = ('wallet_transfer_detected', 'native_transfer')


def _canonical_detected_by_or_none(source: Any) -> str | None:
    """Strict variant of :func:`detected_by_from_ingestion_source`.

    Returns a canonical detected_by tag (realtime_websocket / realtime_backfill /
    realtime_tx_import / quicknode_http_fast_tail / stable_rpc_polling) or ``None``
    when the value names no known detection path. Never passes unknown strings
    through — the caller decides how to fail closed.
    """
    src = str(source or '').strip().lower()
    if not src:
        return None
    if src in REALTIME_DETECTED_BY:
        return src
    if src in _TX_IMPORT_SOURCES:
        return 'realtime_tx_import'
    if src in _STABLE_INGESTION_SOURCES or src == STABLE_DETECTED_BY:
        return STABLE_DETECTED_BY
    return None


def resolve_telemetry_detected_by(payload: Any) -> str | None:
    """Resolve the canonical detected_by tag for a telemetry row payload.

    The telemetry_events table has no top-level detected_by column — the fact
    lives inside payload_json, and older writers spread it across several keys.
    Resolution order (first canonical answer wins, never invented):

      1. payload.detected_by
      2. payload.details.detected_by, then payload.metadata.detected_by
      3. payload.source_type, then details.source_type / metadata.source_type
         (e.g. 'rpc_polling' -> stable_rpc_polling, 'tx_hash_import' ->
         realtime_tx_import)
      4. payload.ingestion_source / payload.ingestion_method

    Returns ``None`` when no fact names a detection path — callers must render
    that as an explicit "unknown", never silently claim a path (CLAUDE.md
    truthfulness: fail closed, no invented status).
    """
    if not isinstance(payload, dict):
        return None
    details = payload.get('details') if isinstance(payload.get('details'), dict) else {}
    metadata = payload.get('metadata') if isinstance(payload.get('metadata'), dict) else {}
    candidates: tuple[Any, ...] = (
        payload.get('detected_by'),
        details.get('detected_by'),
        metadata.get('detected_by'),
        payload.get('source_type'),
        details.get('source_type'),
        metadata.get('source_type'),
        payload.get('ingestion_source'),
        payload.get('ingestion_method'),
    )
    for candidate in candidates:
        resolved = _canonical_detected_by_or_none(candidate)
        if resolved is not None:
            return resolved
    return None


# telemetry_events.provider_type values written by the stable-polling family of
# writers: process_monitoring_target uses the provider's name
# ('evm_activity_provider', generic fallback 'monitoring_provider'); the ops
# import-tx and block-range replay endpoints write 'evm_rpc'. Every realtime
# writer has stamped payload ingestion markers (ingestion_source / source_type /
# detected_by) since its first commit, so a LIVE wallet-transfer row whose
# payload carries no marker can only have been written by this stable family —
# rows persisted before the payload stamps existed.
STABLE_PROVIDER_TYPES: tuple[str, ...] = ('evm_activity_provider', 'monitoring_provider', 'evm_rpc')

# Basis values for classify_wallet_transfer_detected_by: name WHICH fact decided
# the detected_by tag so the UI/debug output can distinguish a hard payload fact
# from a row-level inference (requirement: warn only when truly unclassifiable).
DETECTED_BY_BASIS_PAYLOAD = 'payload'
DETECTED_BY_BASIS_EVIDENCE = 'evidence_source'
DETECTED_BY_BASIS_PROVIDER_TYPE = 'provider_type'
DETECTED_BY_BASIS_STABLE_INFERENCE = 'stable_polling_inference'
DETECTED_BY_BASIS_UNCLASSIFIED = 'unclassified'


def classify_wallet_transfer_detected_by(
    *,
    payload: Any,
    provider_type: Any = None,
    event_type: Any = None,
    evidence_source: Any = None,
) -> tuple[str | None, str]:
    """Classify a telemetry ROW's detection path from every persisted fact.

    Resolution tiers (first truthful answer wins, never invented):

      1. payload facts — :func:`resolve_telemetry_detected_by` (detected_by /
         details / metadata / source_type / ingestion markers).
      2. non-live evidence — a simulator/replay wallet row names its evidence
         source, never a live detection path (CLAUDE.md truthfulness).
      3. the row's ``provider_type`` column — realtime tags map to themselves;
         the stable-family writer names ('evm_activity_provider',
         'monitoring_provider', 'evm_rpc') map to stable_rpc_polling.
      4. stable-polling inference — a LIVE wallet-transfer row with NO payload
         markers and NO provider_type can only predate the payload stamps, and
         every realtime-family writer has stamped markers since its first
         commit, so the writer was the stable polling family. This is the only
         inference tier and it never claims a realtime path.

    Returns ``(detected_by, basis)``. ``detected_by`` is ``None`` only when the
    row names a foreign writer (unknown provider_type with no payload facts) —
    callers keep failing closed to an explicit 'unknown' for wallet rows.
    """
    resolved = resolve_telemetry_detected_by(payload)
    if resolved:
        return resolved, DETECTED_BY_BASIS_PAYLOAD
    etype = str(event_type or '').strip().lower()
    is_wallet = etype in WALLET_TRANSFER_EVENT_TYPES
    evidence = str(evidence_source or '').strip().lower()
    if is_wallet and evidence and evidence != 'live':
        return evidence, DETECTED_BY_BASIS_EVIDENCE
    ptype = str(provider_type or '').strip().lower()
    mapped = _canonical_detected_by_or_none(ptype)
    if mapped is None and ptype in STABLE_PROVIDER_TYPES:
        mapped = STABLE_DETECTED_BY
    if mapped:
        return mapped, DETECTED_BY_BASIS_PROVIDER_TYPE
    if is_wallet and not ptype and evidence in ('', 'live'):
        return STABLE_DETECTED_BY, DETECTED_BY_BASIS_STABLE_INFERENCE
    return None, DETECTED_BY_BASIS_UNCLASSIFIED


def classify_realtime_tx_verdict(
    *,
    tx_found: bool,
    matched: bool,
    existing_detected_by: str | None,
    was_block_scanned: bool,
    rate_limited_at_tx_time: bool,
    below_checkpoint: bool,
    imported_by: str | None = None,
) -> str:
    """Return the single canonical verdict for a tx-hash diagnosis.

    This is the acceptance contract for "why did/didn't realtime detect this tx":
    exactly one clear answer, shared by the worker's tx-hash debug
    (``base_realtime_ingestor._debug_tx_match``) and the read-only
    ``/ops/monitoring/diagnose-tx`` endpoint so the two can never disagree.

    Priority order (strongest truth first):
      1. tx not found / not matched — nothing for realtime to detect.
      2. A persisted row already exists — report WHO detected it
         (realtime_websocket / fast-tail => matched-and-persisted;
         realtime_backfill / realtime_tx_import => recovered via import;
         stable_rpc_polling => realtime duplicate was skipped).
      3. The tx was just imported by this diagnosis run (bounded backfill).
      4. The block was scanned but no row exists — matching/persistence bug,
         surfaced loudly instead of being explained away.
      5. Provider was rate-limited when the tx landed — realtime missed it;
         stable polling remains the fallback.
      6. Not scanned: below the checkpoint means the forward scan will never
         reach it (import is the recovery); above means it is still pending.
    """
    if not tx_found:
        return 'transaction_not_found'
    if not matched:
        return 'not_matched_no_watched_wallet_in_tx'
    if existing_detected_by:
        if existing_detected_by in ('realtime_backfill', 'realtime_tx_import'):
            return f'outside_scanned_window_imported_by_{existing_detected_by}'
        if existing_detected_by in REALTIME_DETECTED_BY:
            return f'matched_and_persisted_by_{existing_detected_by}'
        if existing_detected_by == STABLE_DETECTED_BY:
            return 'already_exists_stable_rpc_polling_realtime_duplicate_skipped'
        return f'already_exists_detected_by_{existing_detected_by}'
    if imported_by:
        return f'outside_scanned_window_imported_by_{imported_by}'
    if was_block_scanned:
        return 'scanned_but_not_persisted_check_matching'
    if rate_limited_at_tx_time:
        return 'missed_provider_rate_limited'
    if below_checkpoint:
        return 'outside_scanned_window_not_yet_imported'
    return 'pending_forward_scan'


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


def realtime_active_by_watcher_facts(watcher: dict[str, Any] | None) -> bool:
    """True when the realtime watcher row PROVES the WebSocket worker is live.

    Canonical backend facts, written only by the realtime worker into
    ``monitoring_watcher_state``:

      * ``provider_mode`` / ``source_status`` == ``realtime_websocket``
      * not degraded
      * not provider-rate-limited
      * ``heads_received`` > 0 (heads are actually arriving)

    Runtime status must derive from these facts (CLAUDE.md: "Runtime status must be
    derived from canonical backend facts"), so a worker that is demonstrably
    receiving heads reads **Active** even when THIS API process was never given
    ``BASE_REALTIME_ENABLED`` — the exact mismatch where the UI said "Realtime
    WebSocket Paused / Disabled" while the worker logs showed realtime active
    (requirement 5).
    """
    if not isinstance(watcher, dict) or not watcher:
        return False
    metrics = watcher.get('metrics') if isinstance(watcher.get('metrics'), dict) else {}
    if bool(metrics.get('rate_limited')):
        return False
    if bool(watcher.get('degraded')):
        return False
    provider_mode = str(
        metrics.get('provider_mode') or watcher.get('source_status') or ''
    ).strip().lower()
    try:
        heads_received = int(metrics.get('heads_received') or 0)
    except (TypeError, ValueError):
        heads_received = 0
    return provider_mode == 'realtime_websocket' and heads_received > 0


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
    # Canonical fallback facts written by the realtime worker's heartbeat: when the
    # WSS is degraded past its breaker thresholds (TLS provider failure, reconnect
    # loop) fallback_active=True and provider_mode names the active fallback path
    # (quicknode_http_fast_tail or stable_rpc_polling_fallback). The UI renders
    # "Realtime degraded — stable polling fallback active" from these, never from a
    # bare degraded flag.
    realtime_fallback_active = bool(metrics.get('fallback_active'))
    realtime_provider_mode = (
        str(metrics.get('provider_mode') or watcher.get('source_status') or '').strip().lower()
        or None
    )

    # Canonical proof the realtime WebSocket worker is live, independent of THIS
    # process's BASE_REALTIME_ENABLED env (requirement 5): the watcher row reports
    # provider_mode=realtime_websocket, not degraded/rate-limited, heads increasing.
    realtime_active_by_facts = realtime_active_by_watcher_facts(watcher)
    # Effective enablement drives the provider-health verdict, headline, and the
    # customer-facing ``enabled`` flag so a worker proven active reads as such even
    # when the env flag was only set on the worker process, not the API process.
    effective_realtime_enabled = bool(realtime_is_enabled or realtime_active_by_facts)

    if realtime_active_by_facts:
        # Backend facts win: the worker is delivering heads on the WSS right now.
        realtime_state = 'active'
        realtime_reason = None
    elif not realtime_is_enabled:
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
    elif not effective_realtime_enabled:
        provider_state = 'not_applicable'
    elif watcher_has_row and not watcher_degraded:
        provider_state = 'healthy'
    else:
        provider_state = 'unknown'

    # --- Truthful headline ---------------------------------------------------------
    # Only mention "heartbeat is stale" when STABLE polling is actually stale.
    if stable_active and not effective_realtime_enabled:
        headline = 'Stable polling active. Realtime WebSocket paused.'
    elif stable_active and realtime_state == 'rate_limited':
        headline = 'Stable polling active. Realtime WebSocket rate limited (provider cooldown).'
    elif stable_active and realtime_state == 'active':
        headline = 'Stable polling active. Realtime WebSocket active.'
    elif stable_active and realtime_state == 'degraded':
        headline = (
            'Stable polling active. Realtime degraded — stable polling fallback active.'
            if realtime_fallback_active
            else 'Stable polling active. Realtime WebSocket degraded.'
        )
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
            # Effective enablement: True when the env flag is set OR the watcher row
            # proves the worker is actively delivering heads (requirement 5).
            'enabled': bool(effective_realtime_enabled),
            'state': realtime_state,
            'last_event_at': _iso(realtime_last_event_at),
            'reason': realtime_reason,
            # Fallback facts from the worker heartbeat: True when realtime detection
            # has handed off to a fallback path; provider_mode names it
            # (quicknode_http_fast_tail / stable_rpc_polling_fallback / rate_limited
            # / realtime_websocket when healthy).
            'fallback_active': realtime_fallback_active,
            'provider_mode': realtime_provider_mode,
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
