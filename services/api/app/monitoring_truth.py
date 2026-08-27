from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone


PROVIDER_UNAVAILABLE_REASON = 'provider_unavailable'


@dataclass(frozen=True)
class ReportingSubCounts:
    """Truthful split of an aggregate reporting-systems count (CLAUDE.md).

    The runtime endpoint's legacy ``reporting_systems`` value falls back to legacy/receipt
    coverage rows, so it can be > 0 during a provider outage while ZERO systems are freshly
    reporting live. These explicit sub-counts keep customer-facing status honest:

      * ``fresh_live_reporting_systems``   — systems reporting live telemetry inside the
        freshness window right now (live evidence source required).
      * ``historically_reporting_systems`` — systems that have EVER produced coverage
        evidence (always >= fresh_live).
      * ``replay_only_systems``            — historical minus fresh_live: systems whose
        only evidence is replay/historical.

    ``status_reason`` is ``fresh_coverage_window_Ns`` ONLY when a system is genuinely
    reporting live inside the window; otherwise ``provider_unavailable``. It can never be
    ``fresh_coverage_window`` while the evidence source is replay/none.
    """

    configured_systems: int
    fresh_live_reporting_systems: int
    historically_reporting_systems: int
    replay_only_systems: int
    status_reason: str
    fresh_coverage_window_claimed: bool


def derive_reporting_sub_counts(
    *,
    configured_systems: int,
    fresh_live_reporting_systems: int,
    historically_reporting_systems: int,
    telemetry_window_seconds: int,
    evidence_source: str,
) -> ReportingSubCounts:
    """Derive the truthful reporting sub-counts and status reason.

    Fail-closed: a fresh coverage window is only claimed when there is at least one
    fresh-live reporting system AND the evidence source is live. Replay, historical, none,
    or a degraded/unavailable provider can never present as a fresh live coverage window.
    """
    configured = max(int(configured_systems or 0), 0)
    raw_fresh_live = max(int(fresh_live_reporting_systems or 0), 0)
    normalized_evidence = str(evidence_source or '').strip().lower()
    fresh_coverage_window_claimed = raw_fresh_live > 0 and normalized_evidence == 'live'
    fresh_live = raw_fresh_live if fresh_coverage_window_claimed else 0
    historical = max(int(historically_reporting_systems or 0), fresh_live)
    replay_only = max(historical - fresh_live, 0)
    status_reason = (
        f'fresh_coverage_window_{int(telemetry_window_seconds)}s'
        if fresh_coverage_window_claimed
        else PROVIDER_UNAVAILABLE_REASON
    )
    return ReportingSubCounts(
        configured_systems=configured,
        fresh_live_reporting_systems=fresh_live,
        historically_reporting_systems=historical,
        replay_only_systems=replay_only,
        status_reason=status_reason,
        fresh_coverage_window_claimed=fresh_coverage_window_claimed,
    )


def should_run_historical_backfill(
    *,
    backfill_completed: bool,
    new_historical_rows: bool = False,
    rule_version_changed: bool = False,
    replay_requested: bool = False,
    cursor_recovery_needed: bool = False,
) -> bool:
    """Decide whether a per-target/rule-set historical backfill should run this cycle.

    Section 7: a COMPLETED historical backfill must not rescan all old telemetry every
    scheduled cycle (the production symptom: the same 16 old rows re-deduplicated every
    5 minutes). Once a completion marker/cursor is persisted, the backfill re-runs only
    when one of the explicit triggers is present:

      * ``new_historical_rows``     — historical rows exist beyond the persisted cursor.
      * ``rule_version_changed``    — the rule set changed, so old rows must be re-evaluated.
      * ``replay_requested``        — an operator explicitly requested a replay.
      * ``cursor_recovery_needed``  — the cursor/completion marker was lost and must be rebuilt.

    A backfill that has never completed always runs.
    """
    if not backfill_completed:
        return True
    return bool(
        new_historical_rows
        or rule_version_changed
        or replay_requested
        or cursor_recovery_needed
    )


MONITORING_MODES = {'DEMO', 'LIVE', 'HYBRID', 'DEGRADED'}
EVIDENCE_STATES = {'REAL_EVIDENCE', 'NO_EVIDENCE', 'DEGRADED_EVIDENCE', 'FAILED_EVIDENCE', 'DEMO_EVIDENCE'}
TRUTHFULNESS_STATES = {'CLAIM_SAFE', 'NOT_CLAIM_SAFE', 'UNKNOWN_RISK'}
DETECTION_OUTCOMES = {
    'DETECTION_CONFIRMED',
    'NO_CONFIRMED_ANOMALY_FROM_REAL_EVIDENCE',
    'NO_EVIDENCE',
    'MONITORING_DEGRADED',
    'ANALYSIS_FAILED',
    'DEMO_ONLY',
}


@dataclass(frozen=True)
class MonitoringTruthResult:
    mode: str
    status: str
    evidence_state: str
    truthfulness_state: str
    claim_safe: bool
    synthetic: bool
    evidence_present: bool
    recent_real_event_count: int
    last_real_event_at: datetime | None
    latest_block: int | None
    last_checkpoint_at: datetime | None
    checkpoint_age_seconds: int | None
    provider_name: str
    provider_kind: str
    degraded_reason: str | None
    error_code: str | None

    def validate(self) -> None:
        if self.mode not in MONITORING_MODES:
            raise ValueError(f'invalid mode: {self.mode}')
        if self.evidence_state not in EVIDENCE_STATES:
            raise ValueError(f'invalid evidence_state: {self.evidence_state}')
        if self.truthfulness_state not in TRUTHFULNESS_STATES:
            raise ValueError(f'invalid truthfulness_state: {self.truthfulness_state}')
        if self.mode in {'LIVE', 'HYBRID'} and self.synthetic:
            raise ValueError('LIVE/HYBRID cannot be synthetic')
        if self.mode == 'DEMO' and not self.synthetic:
            raise ValueError('DEMO mode must be synthetic')
        if self.mode in {'LIVE', 'HYBRID'} and self.recent_real_event_count <= 0:
            if self.evidence_state == 'REAL_EVIDENCE':
                raise ValueError('REAL_EVIDENCE requires recent_real_event_count > 0')
            if self.claim_safe:
                raise ValueError('claim_safe must be false when recent_real_event_count == 0')


def api_mode(mode: str) -> str:
    normalized = str(mode or '').strip().upper()
    if normalized in MONITORING_MODES:
        return normalized
    return 'HYBRID'


def api_evidence_state(value: str) -> str:
    normalized = str(value or '').strip().upper()
    return normalized if normalized in EVIDENCE_STATES else 'NO_EVIDENCE'


def api_truthfulness_state(value: str) -> str:
    normalized = str(value or '').strip().upper()
    return normalized if normalized in TRUTHFULNESS_STATES else 'UNKNOWN_RISK'


def ui_evidence_state(value: str) -> str:
    mapping = {
        'REAL_EVIDENCE': 'real',
        'NO_EVIDENCE': 'no_evidence',
        'DEGRADED_EVIDENCE': 'degraded',
        'FAILED_EVIDENCE': 'failed',
        'DEMO_EVIDENCE': 'demo',
    }
    return mapping.get(api_evidence_state(value), 'no_evidence')


def ui_truthfulness_state(value: str) -> str:
    mapping = {
        'CLAIM_SAFE': 'claim_safe',
        'NOT_CLAIM_SAFE': 'not_claim_safe',
        'UNKNOWN_RISK': 'unknown_risk',
    }
    return mapping.get(api_truthfulness_state(value), 'unknown_risk')


# ---------------------------------------------------------------------------
# Realtime (QuickNode Streams) ingestion health — kept SEPARATE from fallback
# RPC health.
#
# Decoda runs two distinct ingestion paths against the same provider:
#
#   1. QuickNode Streams  — realtime webhook delivery at (or within a few blocks
#      of) the Base chain tip. Proven by the ``quicknode:base:live`` checkpoint
#      advancing and, when a monitored wallet actually transacts, by persisted
#      ``evidence_source=live`` / ``detected_by=quicknode_stream`` telemetry.
#   2. QuickNode HTTPS RPC — the stable reconciliation poll on the canonical
#      900s cadence.
#
# These are RELATED but INDEPENDENT facts. A reconciliation poll that has not run
# for a few minutes is normal for a 900s cadence and says NOTHING about whether
# the Stream is delivering; treating "no recent fallback poll" as "provider
# unavailable" is what produced the contradictory runtime state where
# ``fresh_live_reporting_systems=1`` coexisted with
# ``chosen_evidence_source=replay`` and ``provider_degraded_or_unreachable``.
#
# CLAUDE.md keeps heartbeat / poll / telemetry as separate proofs, so this helper
# does too, and returns BOTH:
#
#   * ``healthy``            — the realtime ingestion PATH is delivering near the
#     tip (checkpoint fresh + lag within threshold). Blocks arriving with no
#     matched wallet transfer is the normal quiet case and stays healthy.
#   * ``live_evidence_fresh`` — realtime LIVE EVIDENCE actually arrived inside the
#     freshness window. Only this may be read as live customer evidence.
#
# Realtime live evidence has TWO distinct kinds, and this helper keeps them
# separately named rather than collapsing them into one green flag:
#
#   * ``live_coverage_fresh``           — a healthy Stream block was accepted and the
#     monitored target was loaded and evaluated against it. This proves MONITORING
#     ("Decoda actively monitored this target at this time") and is exactly what the
#     900s fallback RPC poll's own coverage telemetry proves. It asserts NOTHING
#     about whether anything happened on-chain.
#   * ``live_security_telemetry_fresh`` — a real monitored-wallet transfer actually
#     arrived on the Stream inside the window. Only THIS is evidence of an on-chain
#     event; ``matched=0`` must never set it.
#
# ``live_evidence_kind`` names which one is carrying the freshness so a caller can
# never read "we monitored you" as "we saw something". Coverage alone keeps
# ``recent_real_event_count`` at zero, so the runtime's existing
# ``coverage_only_no_events`` evidence state still reports truthfully.
#
# Fail-closed everywhere: streams disabled, an unknown/stale/degraded lane, or no
# realtime evidence at all can never report healthy, and no flag is ever inferred
# from the fallback RPC path.
# ---------------------------------------------------------------------------

REALTIME_INGESTION_HEALTHY = 'healthy'
REALTIME_INGESTION_DEGRADED = 'degraded'
REALTIME_INGESTION_STALE = 'stale'
REALTIME_INGESTION_UNKNOWN = 'unknown'
REALTIME_INGESTION_DISABLED = 'disabled'
REALTIME_INGESTION_NO_EVIDENCE = 'no_evidence'

REALTIME_INGESTION_STATES = {
    REALTIME_INGESTION_HEALTHY,
    REALTIME_INGESTION_DEGRADED,
    REALTIME_INGESTION_STALE,
    REALTIME_INGESTION_UNKNOWN,
    REALTIME_INGESTION_DISABLED,
    REALTIME_INGESTION_NO_EVIDENCE,
}

# Lane states produced by quicknode_streams.classify_quicknode_lane_state.
_LANE_LIVE = 'live'
_LANE_DEGRADED = 'degraded'
_LANE_STALE = 'stale'
_LANE_FAILED = 'failed'
_LANE_CATCHING_UP = 'catching_up'


@dataclass(frozen=True)
class RealtimeIngestionHealth:
    """Canonical realtime-ingestion facts for one workspace's runtime status."""

    status: str
    healthy: bool
    live_evidence_fresh: bool
    live_coverage_fresh: bool
    live_security_telemetry_fresh: bool
    live_evidence_kind: str
    streams_enabled: bool
    lane_state: str | None
    lag_blocks: int | None
    checkpoint_block: int | None
    chain_head: int | None
    checkpoint_age_seconds: int | None
    live_telemetry_age_seconds: int | None
    live_coverage_age_seconds: int | None
    reason: str


def derive_realtime_ingestion_health(
    *,
    streams_enabled: bool,
    lane_state: str | None,
    lag_blocks: int | None = None,
    checkpoint_block: int | None = None,
    chain_head: int | None = None,
    checkpoint_age_seconds: int | None = None,
    live_telemetry_age_seconds: int | None = None,
    live_coverage_age_seconds: int | None = None,
    checkpoint_stale_seconds: int,
    telemetry_window_seconds: int,
) -> RealtimeIngestionHealth:
    """Derive realtime ingestion health from canonical QuickNode Stream facts.

    ``lane_state`` is the state produced by
    :func:`services.api.app.quicknode_streams.classify_quicknode_lane_state` from the
    durable ``quicknode:base:live`` checkpoint — this helper deliberately reuses that
    canonical classification rather than inventing a second stream-health model.

    ``live_telemetry_age_seconds`` is the age of the freshest realtime SECURITY
    telemetry row (a matched monitored-wallet transfer). ``live_coverage_age_seconds``
    is the age of the freshest realtime MONITORING COVERAGE row — written when a
    healthy near-tip Stream block was accepted and the monitored target was loaded and
    evaluated against it, whether or not anything matched. Both are live realtime
    evidence; only the former is evidence of an on-chain event.

    The fallback RPC cadence is NOT an input: a 900s reconciliation poll can never
    make a delivering Stream look unavailable, and a delivering Stream can never make
    a stale RPC poll look fresh.
    """
    stale_window = max(int(checkpoint_stale_seconds or 0), 0)
    telemetry_window = max(int(telemetry_window_seconds or 0), 0)
    normalized_lane = str(lane_state or '').strip().lower() or None
    checkpoint_age = int(checkpoint_age_seconds) if isinstance(checkpoint_age_seconds, int) else None
    telemetry_age = (
        int(live_telemetry_age_seconds) if isinstance(live_telemetry_age_seconds, int) else None
    )
    coverage_age = (
        int(live_coverage_age_seconds) if isinstance(live_coverage_age_seconds, int) else None
    )
    security_telemetry_fresh = telemetry_age is not None and telemetry_age <= telemetry_window
    coverage_fresh = coverage_age is not None and coverage_age <= telemetry_window
    live_evidence_fresh = security_telemetry_fresh or coverage_fresh

    def _result(status: str, healthy: bool, reason: str) -> RealtimeIngestionHealth:
        # Fail-closed: realtime evidence only counts as live while the realtime path
        # itself is healthy. Fresh rows behind a stalled/degraded lane are historical
        # evidence, not proof of current live monitoring.
        resolved_security_fresh = bool(security_telemetry_fresh and healthy)
        resolved_coverage_fresh = bool(coverage_fresh and healthy)
        return RealtimeIngestionHealth(
            status=status,
            healthy=healthy,
            live_evidence_fresh=bool(live_evidence_fresh and healthy),
            live_coverage_fresh=resolved_coverage_fresh,
            live_security_telemetry_fresh=resolved_security_fresh,
            # Never let coverage ("we monitored this target") be read as a security
            # event: security telemetry names itself whenever it is present.
            live_evidence_kind=(
                'security_telemetry' if resolved_security_fresh
                else ('coverage' if resolved_coverage_fresh else 'none')
            ),
            streams_enabled=bool(streams_enabled),
            lane_state=normalized_lane,
            lag_blocks=lag_blocks if isinstance(lag_blocks, int) else None,
            checkpoint_block=checkpoint_block if isinstance(checkpoint_block, int) else None,
            chain_head=chain_head if isinstance(chain_head, int) else None,
            checkpoint_age_seconds=checkpoint_age,
            live_telemetry_age_seconds=telemetry_age,
            live_coverage_age_seconds=coverage_age,
            reason=reason,
        )

    if not streams_enabled:
        return _result(REALTIME_INGESTION_DISABLED, False, 'realtime_streams_disabled')
    if normalized_lane is None and telemetry_age is None and coverage_age is None:
        return _result(REALTIME_INGESTION_NO_EVIDENCE, False, 'no_realtime_stream_evidence')
    if normalized_lane == _LANE_FAILED:
        return _result(REALTIME_INGESTION_DEGRADED, False, 'stream_delivery_failed')
    if normalized_lane == _LANE_STALE:
        return _result(REALTIME_INGESTION_STALE, False, 'stream_checkpoint_stale')
    if normalized_lane == _LANE_DEGRADED:
        return _result(REALTIME_INGESTION_DEGRADED, False, 'stream_far_behind_chain_tip')
    if normalized_lane == _LANE_CATCHING_UP:
        return _result(REALTIME_INGESTION_DEGRADED, False, 'stream_live_lane_not_established')
    if normalized_lane != _LANE_LIVE:
        # No lane classification (unknown chain head, no checkpoint) — never healthy.
        return _result(REALTIME_INGESTION_UNKNOWN, False, 'stream_health_unknown')
    # Lane says live. Re-check the checkpoint age against the SAME canonical stale
    # window so a lane classification computed against a different clock can never
    # paint a stopped stream green.
    if checkpoint_age is None:
        return _result(REALTIME_INGESTION_UNKNOWN, False, 'stream_checkpoint_timestamp_missing')
    if checkpoint_age > stale_window:
        return _result(REALTIME_INGESTION_STALE, False, 'stream_checkpoint_stale')
    if security_telemetry_fresh:
        healthy_reason = 'stream_near_chain_tip_with_fresh_live_telemetry'
    elif coverage_fresh:
        healthy_reason = 'stream_near_chain_tip_with_fresh_coverage'
    else:
        healthy_reason = 'stream_near_chain_tip'
    return _result(REALTIME_INGESTION_HEALTHY, True, healthy_reason)


# ---------------------------------------------------------------------------
# Continuity event-ingestion evidence.
#
# The continuity SLO asks ONE question: is this workspace's live monitoring
# ingestion still flowing right now? Three workspace-scoped canonical facts can
# answer it, produced by independent lanes running at different cadences:
#
#   * matched_security_event   — the freshest MATCHED on-chain event
#                                (detection metadata ``last_real_event_at``).
#   * realtime_stream_*        — the QuickNode Stream lane, refreshing coverage
#                                at half the live-lane stale window.
#   * fallback_rpc_coverage    — the stable reconciliation RPC poll.
#
# The NEWEST TRUSTWORTHY fact is the truthful answer. Selecting with ``or``
# precedence instead let an older matched-event timestamp mask newer coverage,
# and left the freshest lane out of the selection entirely — so a workspace
# genuinely carried by a healthy Stream still read as event_ingestion_stale and
# then event_ingestion_offline as the slower fallback RPC timestamp aged out,
# oscillating back to fresh on every fallback poll.
#
# Quiet wallets are the normal case, not an outage: a monitored wallet can
# legitimately go hours or days without a matched transfer. Continuity measures
# active monitoring/ingestion COVERAGE; it must never require a security event.
#
# Fail-closed in both directions: realtime evidence is admitted ONLY through the
# already-validated :func:`derive_realtime_ingestion_health` verdict (streams
# enabled, lane live, checkpoint inside the canonical stale window, evidence
# inside the freshness window). A stopped, stale, catching_up, degraded, failed
# or checkpoint-expired lane contributes NOTHING here, so historical Stream rows
# can never paint a dead lane green — and the fallback RPC timestamp becomes the
# deciding fact again exactly when the Stream stops proving itself.
# ---------------------------------------------------------------------------

CONTINUITY_EVENT_SOURCE_NONE = 'none'
CONTINUITY_EVENT_SOURCE_MATCHED_SECURITY_EVENT = 'matched_security_event'
CONTINUITY_EVENT_SOURCE_REALTIME_SECURITY_TELEMETRY = 'realtime_stream_security_telemetry'
CONTINUITY_EVENT_SOURCE_REALTIME_COVERAGE = 'realtime_stream_coverage'
CONTINUITY_EVENT_SOURCE_FALLBACK_RPC_COVERAGE = 'fallback_rpc_coverage'

CONTINUITY_EVENT_SOURCES = {
    CONTINUITY_EVENT_SOURCE_NONE,
    CONTINUITY_EVENT_SOURCE_MATCHED_SECURITY_EVENT,
    CONTINUITY_EVENT_SOURCE_REALTIME_SECURITY_TELEMETRY,
    CONTINUITY_EVENT_SOURCE_REALTIME_COVERAGE,
    CONTINUITY_EVENT_SOURCE_FALLBACK_RPC_COVERAGE,
}

REALTIME_EVIDENCE_REJECTED_NO_VERDICT = 'realtime_health_unknown'
REALTIME_EVIDENCE_REJECTED_NO_ROWS = 'no_realtime_rows'


def _as_utc(value: datetime | None) -> datetime | None:
    """Normalize a candidate timestamp so candidates are always comparable.

    A naive timestamp is read as UTC — the storage convention everywhere in this
    codebase. Without this, mixing a naive and an aware candidate raises inside
    the selection (and later inside ``now - ts``) instead of producing a verdict.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def resolve_realtime_live_evidence_at(
    *,
    realtime_ingestion: RealtimeIngestionHealth | None,
    last_security_telemetry_at: datetime | None,
    last_coverage_at: datetime | None,
) -> datetime | None:
    """Newest realtime Stream evidence admissible as CURRENT live monitoring.

    Both realtime evidence kinds count — a matched wallet transfer (security
    telemetry) and a healthy near-tip block evaluated against the monitored
    target (coverage) — but ONLY through the freshness flags on the canonical
    :class:`RealtimeIngestionHealth` verdict, which are already cleared unless
    the realtime lane itself is healthy. ``None`` means "no admissible realtime
    evidence", never "healthy".
    """
    if realtime_ingestion is None:
        return None
    candidates = [
        ts
        for ts, fresh in (
            (_as_utc(last_security_telemetry_at), realtime_ingestion.live_security_telemetry_fresh),
            (_as_utc(last_coverage_at), realtime_ingestion.live_coverage_fresh),
        )
        if fresh and ts is not None
    ]
    return max(candidates) if candidates else None


@dataclass(frozen=True)
class ContinuityEventEvidence:
    """Which workspace-scoped fact answers "is ingestion still flowing?", and why."""

    last_event_at: datetime | None
    source: str
    matched_security_event_at: datetime | None
    fallback_rpc_coverage_at: datetime | None
    realtime_live_evidence_at: datetime | None
    realtime_admitted: bool
    realtime_rejected_reason: str | None


def derive_continuity_event_evidence(
    *,
    recent_last_real_event_at: datetime | None,
    canonical_last_telemetry_at: datetime | None,
    realtime_ingestion: RealtimeIngestionHealth | None = None,
    realtime_last_security_telemetry_at: datetime | None = None,
    realtime_last_coverage_at: datetime | None = None,
) -> ContinuityEventEvidence:
    """Select the newest trustworthy live-monitoring timestamp for the continuity SLO.

    Newest-evidence semantics, NOT ``or`` precedence: whichever admissible lane
    proved live monitoring most recently decides, and the others only fill in
    when it is absent. Realtime candidates are filtered through
    :func:`resolve_realtime_live_evidence_at` first, so an unhealthy Stream is
    simply absent from the selection and the fallback RPC timestamp decides —
    fail-closed degradation is preserved exactly as before.
    """
    matched_at = _as_utc(recent_last_real_event_at)
    fallback_at = _as_utc(canonical_last_telemetry_at)
    realtime_security_at = _as_utc(realtime_last_security_telemetry_at)
    realtime_coverage_at = _as_utc(realtime_last_coverage_at)
    admitted_security_at = (
        realtime_security_at
        if realtime_ingestion is not None and realtime_ingestion.live_security_telemetry_fresh
        else None
    )
    admitted_coverage_at = (
        realtime_coverage_at
        if realtime_ingestion is not None and realtime_ingestion.live_coverage_fresh
        else None
    )
    realtime_live_evidence_at = resolve_realtime_live_evidence_at(
        realtime_ingestion=realtime_ingestion,
        last_security_telemetry_at=realtime_security_at,
        last_coverage_at=realtime_coverage_at,
    )
    if realtime_live_evidence_at is not None:
        realtime_rejected_reason = None
    elif realtime_ingestion is None:
        realtime_rejected_reason = REALTIME_EVIDENCE_REJECTED_NO_VERDICT
    elif realtime_security_at is None and realtime_coverage_at is None:
        realtime_rejected_reason = REALTIME_EVIDENCE_REJECTED_NO_ROWS
    else:
        # Rows exist but the canonical verdict refused them — report the verdict's
        # own reason so the rejection is never anonymous.
        realtime_rejected_reason = str(realtime_ingestion.reason or '').strip() or None

    # Ranked so an exact timestamp tie names the strongest evidence kind. Ranking
    # NEVER overrides recency: the newest timestamp always wins first.
    ranked: list[tuple[datetime | None, str]] = [
        (matched_at, CONTINUITY_EVENT_SOURCE_MATCHED_SECURITY_EVENT),
        (admitted_security_at, CONTINUITY_EVENT_SOURCE_REALTIME_SECURITY_TELEMETRY),
        (admitted_coverage_at, CONTINUITY_EVENT_SOURCE_REALTIME_COVERAGE),
        (fallback_at, CONTINUITY_EVENT_SOURCE_FALLBACK_RPC_COVERAGE),
    ]
    present = [(ts, source) for ts, source in ranked if ts is not None]
    if not present:
        return ContinuityEventEvidence(
            last_event_at=None,
            source=CONTINUITY_EVENT_SOURCE_NONE,
            matched_security_event_at=matched_at,
            fallback_rpc_coverage_at=fallback_at,
            realtime_live_evidence_at=None,
            realtime_admitted=False,
            realtime_rejected_reason=realtime_rejected_reason,
        )
    newest_at = max(ts for ts, _ in present)
    newest_source = next(source for ts, source in present if ts == newest_at)
    return ContinuityEventEvidence(
        last_event_at=newest_at,
        source=newest_source,
        matched_security_event_at=matched_at,
        fallback_rpc_coverage_at=fallback_at,
        realtime_live_evidence_at=realtime_live_evidence_at,
        realtime_admitted=realtime_live_evidence_at is not None,
        realtime_rejected_reason=realtime_rejected_reason,
    )
