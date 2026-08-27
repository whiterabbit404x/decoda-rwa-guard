"""Continuity SLO event-ingestion evidence selection (production regression).

Production read-only verification, commit 388efea, Base Mainnet workspace::

    now_utc:                            2026-08-26 17:25:03.101523+00
    slo_event_ingestion_input:          2026-08-26 17:11:30.174925+00   (~813s old)
    stream_last_coverage_at:            2026-08-26 17:24:32.351291+00   (~31s old)
    stream_checkpoint_at:               2026-08-26 17:25:01.080417+00   (~2s old)
    latest_analysis_last_real_event_at: NULL
    stream_last_security_telemetry_at:  2026-08-25 08:43:12.794678+00   (>1 day old)
    last_heartbeat_at:                  2026-08-26 17:11:26.370481+00
    last_poll_at:                       2026-08-26 17:11:32.027689+00
    max_enabled_interval_seconds:       300

What that proves: the QuickNode Stream lane was genuinely current (checkpoint 2s,
coverage 31s) while the continuity SLO was reading a ~813s-old timestamp — i.e. it
was judging the workspace solely on the slow fallback RPC-poll lane, with fresh
validated Stream coverage sitting right there, unused.

Root cause::

    _continuity_last_event_at = recent_last_real_event_at or canonical_last_telemetry_at

Wrong twice over:

  1. ``or`` precedence: an OLDER matched-security-event timestamp masks NEWER
     coverage, because the first non-None candidate wins regardless of age.
  2. The realtime Stream lane — the fastest-refreshing evidence the workspace has
     — was not a candidate at all.

Consequence in production: as the fallback RPC timestamp ages between polls the
workspace walks fresh → stale → offline and snaps back to fresh on the next
fallback poll, while the Stream never stopped delivering. That is an oscillation
driven by the SLOWEST lane, reported as if live monitoring had failed.

The fix selects the NEWEST TRUSTWORTHY workspace-scoped timestamp across all three
lanes. Fail-closed is preserved, not weakened: realtime evidence is admitted only
through the already-validated ``derive_realtime_ingestion_health`` verdict, so a
stopped / stale / catching_up / degraded / failed / checkpoint-expired lane
contributes nothing and the fallback RPC timestamp decides again.

Covered here (numbering matches the task's required test list):

  1.  healthy validated Stream + fresh coverage + stale RPC   → continuity passes
  2.  same, RPC > 900s                                        → no event_ingestion_offline
  3.  healthy Stream across a full 900s fallback cycle        → no oscillation
  4.  old matched event + newer Stream coverage               → Stream coverage wins
  5.  old matched event + newer RPC coverage                  → RPC coverage wins
  6.  newest matched event                                    → matched event wins
  7.  Stream stopped + stale RPC                              → continuity degrades
  8.  checkpoint stale + fresh-looking historical Stream row  → continuity degrades
  9.  Stream catching_up                                      → not healthy evidence
  10. Stream disabled                                         → legacy behaviour
  11. quiet wallet, no security event, fresh coverage         → continuity healthy
  12. workspace isolation of all three candidate reads
  13. the telemetry window is DERIVED, never assumed to be 60s
  14. 150s Stream coverage refresh unchanged
  15. 900s stable worker polling unchanged
  16. QuickNode RPC volume behaviour unchanged
"""
from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone

import pytest

from services.api.app.monitoring_runner import (
    QUICKNODE_STREAM_STALE_SECONDS,
    canonical_polling_interval_seconds,
    canonical_runtime_telemetry_window_seconds,
    monitoring_runtime_status,
)
from services.api.app.monitoring_truth import (
    CONTINUITY_EVENT_SOURCE_FALLBACK_RPC_COVERAGE,
    CONTINUITY_EVENT_SOURCE_MATCHED_SECURITY_EVENT,
    CONTINUITY_EVENT_SOURCE_NONE,
    CONTINUITY_EVENT_SOURCE_REALTIME_COVERAGE,
    CONTINUITY_EVENT_SOURCE_REALTIME_SECURITY_TELEMETRY,
    derive_continuity_event_evidence,
    derive_realtime_ingestion_health,
    resolve_realtime_live_evidence_at,
)
from services.api.app.pilot import evaluate_workspace_monitoring_continuity


# --- Production facts, used verbatim so the tests stay anchored to real evidence ---
NOW = datetime(2026, 8, 26, 17, 25, 3, 101523, tzinfo=timezone.utc)
PROD_SLO_EVENT_INGESTION_INPUT = datetime(2026, 8, 26, 17, 11, 30, 174925, tzinfo=timezone.utc)
PROD_STREAM_LAST_COVERAGE_AT = datetime(2026, 8, 26, 17, 24, 32, 351291, tzinfo=timezone.utc)
PROD_STREAM_CHECKPOINT_AT = datetime(2026, 8, 26, 17, 25, 1, 80417, tzinfo=timezone.utc)
PROD_STREAM_LAST_SECURITY_TELEMETRY_AT = datetime(2026, 8, 25, 8, 43, 12, 794678, tzinfo=timezone.utc)
PROD_LAST_HEARTBEAT_AT = datetime(2026, 8, 26, 17, 11, 26, 370481, tzinfo=timezone.utc)
PROD_MAX_ENABLED_INTERVAL_SECONDS = 300

# The stable worker's fallback polling cadence. Referenced (never redefined) so the
# "does it oscillate across a full fallback cycle" test walks the REAL cycle length.
FALLBACK_POLL_CYCLE_SECONDS = canonical_polling_interval_seconds()


def _window(max_enabled_interval_seconds: int | None = PROD_MAX_ENABLED_INTERVAL_SECONDS) -> int:
    """Derive the telemetry window through the canonical function — never hard-coded.

    Production reports ``max_enabled_interval_seconds=300`` today and reported 60
    earlier; both must flow through the same derivation, so no test may assume
    either number produces a particular window.
    """
    return canonical_runtime_telemetry_window_seconds(max_enabled_interval_seconds)


def _stream_health(
    *,
    streams_enabled: bool = True,
    lane_state: str | None = 'live',
    checkpoint_at: datetime | None = PROD_STREAM_CHECKPOINT_AT,
    last_coverage_at: datetime | None = PROD_STREAM_LAST_COVERAGE_AT,
    last_security_telemetry_at: datetime | None = PROD_STREAM_LAST_SECURITY_TELEMETRY_AT,
    now: datetime = NOW,
    telemetry_window_seconds: int | None = None,
):
    """Build the REAL canonical realtime verdict — never a hand-made stub.

    Reusing ``derive_realtime_ingestion_health`` is the point: the continuity fix
    must not introduce a second stream-health test it could disagree with.
    """
    window = _window() if telemetry_window_seconds is None else telemetry_window_seconds

    def _age(ts: datetime | None) -> int | None:
        return None if ts is None else int((now - ts).total_seconds())

    return derive_realtime_ingestion_health(
        streams_enabled=streams_enabled,
        lane_state=lane_state,
        lag_blocks=0,
        checkpoint_block=50_429_022,
        chain_head=50_429_022,
        checkpoint_age_seconds=_age(checkpoint_at),
        live_telemetry_age_seconds=_age(last_security_telemetry_at),
        live_coverage_age_seconds=_age(last_coverage_at),
        checkpoint_stale_seconds=QUICKNODE_STREAM_STALE_SECONDS,
        telemetry_window_seconds=window,
    )


def _continuity(
    *,
    last_event_at: datetime | None,
    now: datetime = NOW,
    last_heartbeat_at: datetime | None = None,
    last_detection_at: datetime | None = None,
    telemetry_window_seconds: int | None = None,
) -> dict:
    """Run the real continuity evaluator on a selected event timestamp."""
    window = _window() if telemetry_window_seconds is None else telemetry_window_seconds
    return evaluate_workspace_monitoring_continuity(
        now=now,
        workspace_configured=True,
        worker_running=True,
        last_heartbeat_at=now - timedelta(seconds=30) if last_heartbeat_at is None else last_heartbeat_at,
        last_event_at=last_event_at,
        last_detection_at=now - timedelta(seconds=60) if last_detection_at is None else last_detection_at,
        heartbeat_ttl_seconds=FALLBACK_POLL_CYCLE_SECONDS,
        telemetry_window_seconds=window,
        detection_window_seconds=max(900, FALLBACK_POLL_CYCLE_SECONDS),
    )


# ---------------------------------------------------------------------------
# Production replay: the exact query-time state must resolve to the Stream lane.
# ---------------------------------------------------------------------------

def test_production_query_state_selects_fresh_stream_coverage() -> None:
    """The exact production read-only state resolves to fresh Stream coverage."""
    realtime = _stream_health()
    assert realtime.healthy is True
    assert realtime.live_coverage_fresh is True
    # >1 day old: the security-telemetry lane is NOT what carries this workspace.
    assert realtime.live_security_telemetry_fresh is False

    evidence = derive_continuity_event_evidence(
        recent_last_real_event_at=None,  # latest_analysis_last_real_event_at: NULL
        canonical_last_telemetry_at=PROD_SLO_EVENT_INGESTION_INPUT,
        realtime_ingestion=realtime,
        realtime_last_security_telemetry_at=PROD_STREAM_LAST_SECURITY_TELEMETRY_AT,
        realtime_last_coverage_at=PROD_STREAM_LAST_COVERAGE_AT,
    )
    assert evidence.last_event_at == PROD_STREAM_LAST_COVERAGE_AT
    assert evidence.source == CONTINUITY_EVENT_SOURCE_REALTIME_COVERAGE
    assert evidence.realtime_admitted is True
    # The fallback fact is reported, never silently dropped.
    assert evidence.fallback_rpc_coverage_at == PROD_SLO_EVENT_INGESTION_INPUT


def test_production_query_state_pre_fix_input_was_stale_not_offline() -> None:
    """Correction 2: at query time the ~813s RPC input is STALE, not OFFLINE.

    The earlier ``event_ingestion_offline`` log came from a moment before this
    query, when the same RPC timestamp had aged past the offline boundary. Both
    readings are consistent — and both are the oscillation this fix removes.
    """
    window = _window()
    age = int((NOW - PROD_SLO_EVENT_INGESTION_INPUT).total_seconds())
    assert 800 <= age <= 830, age
    assert age > window, 'the RPC input is outside the fresh window at query time'

    pre_fix = _continuity(last_event_at=PROD_SLO_EVENT_INGESTION_INPUT)
    assert pre_fix['ingestion_freshness'] == 'stale'
    assert 'event_ingestion_stale' in pre_fix['continuity_reason_codes']
    assert 'event_ingestion_offline' not in pre_fix['continuity_reason_codes']
    assert pre_fix['continuity_slo_pass'] is False


# ---------------------------------------------------------------------------
# 1. healthy validated Stream + fresh Stream coverage + stale RPC → passes
# ---------------------------------------------------------------------------

def test_1_healthy_stream_with_stale_rpc_poll_passes_continuity() -> None:
    realtime = _stream_health()
    evidence = derive_continuity_event_evidence(
        recent_last_real_event_at=None,
        canonical_last_telemetry_at=PROD_SLO_EVENT_INGESTION_INPUT,
        realtime_ingestion=realtime,
        realtime_last_security_telemetry_at=PROD_STREAM_LAST_SECURITY_TELEMETRY_AT,
        realtime_last_coverage_at=PROD_STREAM_LAST_COVERAGE_AT,
    )
    payload = _continuity(last_event_at=evidence.last_event_at)
    assert payload['continuity_slo_pass'] is True
    assert payload['ingestion_freshness'] == 'fresh'
    assert payload['continuity_status'] == 'continuous_live'
    assert not [c for c in payload['continuity_reason_codes'] if c.startswith('event_ingestion_')]


# ---------------------------------------------------------------------------
# 2. healthy Stream + RPC timestamp > 900s → no false event_ingestion_offline
# ---------------------------------------------------------------------------

def test_2_rpc_timestamp_beyond_offline_boundary_does_not_report_offline() -> None:
    window = _window()
    # Comfortably past the offline boundary (>3x the window) AND past 900s.
    rpc_age = max(window * 3 + 120, 901)
    realtime = _stream_health()

    stale_only = _continuity(last_event_at=NOW - timedelta(seconds=rpc_age))
    assert stale_only['ingestion_freshness'] == 'offline'
    assert 'event_ingestion_offline' in stale_only['continuity_reason_codes']

    evidence = derive_continuity_event_evidence(
        recent_last_real_event_at=None,
        canonical_last_telemetry_at=NOW - timedelta(seconds=rpc_age),
        realtime_ingestion=realtime,
        realtime_last_security_telemetry_at=None,
        realtime_last_coverage_at=PROD_STREAM_LAST_COVERAGE_AT,
    )
    fixed = _continuity(last_event_at=evidence.last_event_at)
    assert 'event_ingestion_offline' not in fixed['continuity_reason_codes']
    assert 'event_ingestion_stale' not in fixed['continuity_reason_codes']
    assert fixed['ingestion_freshness'] == 'fresh'
    assert fixed['continuity_slo_pass'] is True


# ---------------------------------------------------------------------------
# 3. no oscillation across a full fallback polling cycle
# ---------------------------------------------------------------------------

def test_3_no_oscillation_across_full_fallback_polling_cycle() -> None:
    """A healthy Stream must not flap healthy → stale → offline → healthy.

    The fallback RPC timestamp is pinned at t0 and allowed to age across the whole
    cycle, exactly as it does between two stable worker polls, while the Stream
    keeps refreshing coverage at its own (much faster) cadence.
    """
    window = _window()
    stream_refresh_seconds = _stream_coverage_refresh_seconds()
    t0 = NOW
    rpc_at = t0  # refreshed by the fallback poll at t0, then left to age

    observed_states: set[str] = set()
    sources_after_rpc_expired: set[str] = set()
    for elapsed in range(0, FALLBACK_POLL_CYCLE_SECONDS + 1, 30):
        now = t0 + timedelta(seconds=elapsed)
        # The Stream refreshed coverage at most one refresh interval ago.
        coverage_at = now - timedelta(seconds=stream_refresh_seconds)
        realtime = _stream_health(
            checkpoint_at=now - timedelta(seconds=2),
            last_coverage_at=coverage_at,
            last_security_telemetry_at=PROD_STREAM_LAST_SECURITY_TELEMETRY_AT,
            now=now,
        )
        assert realtime.healthy is True, elapsed
        evidence = derive_continuity_event_evidence(
            recent_last_real_event_at=None,
            canonical_last_telemetry_at=rpc_at,
            realtime_ingestion=realtime,
            realtime_last_security_telemetry_at=PROD_STREAM_LAST_SECURITY_TELEMETRY_AT,
            realtime_last_coverage_at=coverage_at,
        )
        payload = _continuity(last_event_at=evidence.last_event_at, now=now)
        observed_states.add(payload['ingestion_freshness'])
        assert payload['continuity_slo_pass'] is True, (elapsed, payload['continuity_reason_codes'])
        if elapsed > window:
            # Past the fresh window the fallback timestamp can no longer carry the
            # SLO — from here on the Stream must be what holds continuity up.
            sources_after_rpc_expired.add(evidence.source)

    # ONE state for the entire cycle: no healthy/stale/offline oscillation.
    assert observed_states == {'fresh'}
    assert sources_after_rpc_expired == {CONTINUITY_EVENT_SOURCE_REALTIME_COVERAGE}
    # Not a vacuous pass: within one cycle the fallback timestamp really does leave
    # the fresh window and go stale on its own...
    assert FALLBACK_POLL_CYCLE_SECONDS > window
    cycle_end = t0 + timedelta(seconds=FALLBACK_POLL_CYCLE_SECONDS)
    assert _continuity(last_event_at=rpc_at, now=cycle_end)['ingestion_freshness'] == 'stale'
    # ...and a poll that slips past 3x the window carries it all the way to offline —
    # the earlier production `event_ingestion_offline` reading. The Stream lane holds
    # continuity through BOTH, which is the oscillation this fix removes.
    late = t0 + timedelta(seconds=window * 3 + 120)
    assert _continuity(last_event_at=rpc_at, now=late)['ingestion_freshness'] == 'offline'
    late_coverage_at = late - timedelta(seconds=stream_refresh_seconds)
    late_evidence = derive_continuity_event_evidence(
        recent_last_real_event_at=None,
        canonical_last_telemetry_at=rpc_at,
        realtime_ingestion=_stream_health(
            checkpoint_at=late - timedelta(seconds=2),
            last_coverage_at=late_coverage_at,
            last_security_telemetry_at=PROD_STREAM_LAST_SECURITY_TELEMETRY_AT,
            now=late,
        ),
        realtime_last_security_telemetry_at=PROD_STREAM_LAST_SECURITY_TELEMETRY_AT,
        realtime_last_coverage_at=late_coverage_at,
    )
    late_payload = _continuity(last_event_at=late_evidence.last_event_at, now=late)
    assert late_payload['ingestion_freshness'] == 'fresh'
    assert late_payload['continuity_slo_pass'] is True


# ---------------------------------------------------------------------------
# 4-6. newest-evidence semantics replace `or` precedence
# ---------------------------------------------------------------------------

def test_4_old_matched_event_loses_to_newer_stream_coverage() -> None:
    """The exact `or`-precedence bug: an older matched event masked newer coverage."""
    matched_at = NOW - timedelta(hours=30)
    coverage_at = NOW - timedelta(seconds=31)
    realtime = _stream_health(last_coverage_at=coverage_at, last_security_telemetry_at=None)

    evidence = derive_continuity_event_evidence(
        recent_last_real_event_at=matched_at,
        canonical_last_telemetry_at=None,
        realtime_ingestion=realtime,
        realtime_last_security_telemetry_at=None,
        realtime_last_coverage_at=coverage_at,
    )
    assert evidence.last_event_at == coverage_at
    assert evidence.source == CONTINUITY_EVENT_SOURCE_REALTIME_COVERAGE
    # Pre-fix `or` precedence would have returned the 30h-old matched event.
    assert evidence.last_event_at != matched_at
    assert _continuity(last_event_at=evidence.last_event_at)['continuity_slo_pass'] is True


def test_5_old_matched_event_loses_to_newer_rpc_coverage() -> None:
    matched_at = NOW - timedelta(hours=30)
    rpc_at = NOW - timedelta(seconds=45)

    evidence = derive_continuity_event_evidence(
        recent_last_real_event_at=matched_at,
        canonical_last_telemetry_at=rpc_at,
        realtime_ingestion=_stream_health(streams_enabled=False),
        realtime_last_security_telemetry_at=None,
        realtime_last_coverage_at=None,
    )
    assert evidence.last_event_at == rpc_at
    assert evidence.source == CONTINUITY_EVENT_SOURCE_FALLBACK_RPC_COVERAGE
    assert evidence.last_event_at != matched_at
    assert _continuity(last_event_at=evidence.last_event_at)['continuity_slo_pass'] is True


def test_6_newest_matched_event_wins_over_older_coverage() -> None:
    matched_at = NOW - timedelta(seconds=10)
    coverage_at = NOW - timedelta(seconds=140)
    rpc_at = NOW - timedelta(seconds=300)
    realtime = _stream_health(last_coverage_at=coverage_at, last_security_telemetry_at=None)

    evidence = derive_continuity_event_evidence(
        recent_last_real_event_at=matched_at,
        canonical_last_telemetry_at=rpc_at,
        realtime_ingestion=realtime,
        realtime_last_security_telemetry_at=None,
        realtime_last_coverage_at=coverage_at,
    )
    assert evidence.last_event_at == matched_at
    assert evidence.source == CONTINUITY_EVENT_SOURCE_MATCHED_SECURITY_EVENT


def test_6b_newest_realtime_security_telemetry_is_named_as_such() -> None:
    """Coverage must never be reported as a security event, and vice versa."""
    security_at = NOW - timedelta(seconds=20)
    coverage_at = NOW - timedelta(seconds=90)
    realtime = _stream_health(last_coverage_at=coverage_at, last_security_telemetry_at=security_at)

    evidence = derive_continuity_event_evidence(
        recent_last_real_event_at=None,
        canonical_last_telemetry_at=None,
        realtime_ingestion=realtime,
        realtime_last_security_telemetry_at=security_at,
        realtime_last_coverage_at=coverage_at,
    )
    assert evidence.last_event_at == security_at
    assert evidence.source == CONTINUITY_EVENT_SOURCE_REALTIME_SECURITY_TELEMETRY


# ---------------------------------------------------------------------------
# 7-9. fail-closed: an unhealthy Stream can never carry continuity
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    'lane_state',
    ['stale', 'failed', 'degraded', 'catching_up', None],
)
def test_7_stream_stopped_with_stale_rpc_degrades_continuity(lane_state: str | None) -> None:
    """Stream not delivering + stale RPC → the workspace degrades, as it must."""
    window = _window()
    rpc_at = NOW - timedelta(seconds=window * 3 + 120)
    realtime = _stream_health(lane_state=lane_state)
    assert realtime.healthy is False
    assert realtime.live_coverage_fresh is False
    assert realtime.live_security_telemetry_fresh is False

    evidence = derive_continuity_event_evidence(
        recent_last_real_event_at=None,
        canonical_last_telemetry_at=rpc_at,
        realtime_ingestion=realtime,
        realtime_last_security_telemetry_at=PROD_STREAM_LAST_SECURITY_TELEMETRY_AT,
        realtime_last_coverage_at=PROD_STREAM_LAST_COVERAGE_AT,
    )
    # Fresh-looking Stream rows exist, and are refused.
    assert evidence.realtime_admitted is False
    assert evidence.realtime_live_evidence_at is None
    assert evidence.realtime_rejected_reason
    assert evidence.last_event_at == rpc_at
    assert evidence.source == CONTINUITY_EVENT_SOURCE_FALLBACK_RPC_COVERAGE

    payload = _continuity(last_event_at=evidence.last_event_at)
    assert payload['continuity_slo_pass'] is False
    assert payload['continuity_status'] == 'degraded'
    assert 'event_ingestion_offline' in payload['continuity_reason_codes']


def test_7b_stream_stopped_and_no_rpc_evidence_reports_missing_not_healthy() -> None:
    """No data must never be shown as safe (CLAUDE.md)."""
    realtime = _stream_health(lane_state='stale')
    evidence = derive_continuity_event_evidence(
        recent_last_real_event_at=None,
        canonical_last_telemetry_at=None,
        realtime_ingestion=realtime,
        realtime_last_security_telemetry_at=PROD_STREAM_LAST_SECURITY_TELEMETRY_AT,
        realtime_last_coverage_at=PROD_STREAM_LAST_COVERAGE_AT,
    )
    assert evidence.last_event_at is None
    assert evidence.source == CONTINUITY_EVENT_SOURCE_NONE
    payload = _continuity(last_event_at=None)
    assert payload['continuity_slo_pass'] is False
    assert 'event_ingestion_missing' in payload['continuity_reason_codes']


def test_8_expired_checkpoint_with_fresh_looking_coverage_row_degrades() -> None:
    """A historical coverage row behind a dead checkpoint is not live evidence.

    The lane still *claims* 'live' and the coverage row still *looks* fresh; only
    the checkpoint has expired. The canonical verdict must catch that, otherwise a
    stopped Stream could paint the workspace green forever.
    """
    window = _window()
    expired_checkpoint_at = NOW - timedelta(seconds=QUICKNODE_STREAM_STALE_SECONDS + 60)
    realtime = _stream_health(
        lane_state='live',
        checkpoint_at=expired_checkpoint_at,
        last_coverage_at=NOW - timedelta(seconds=31),
        last_security_telemetry_at=None,
    )
    assert realtime.healthy is False
    assert realtime.status == 'stale'
    assert realtime.reason == 'stream_checkpoint_stale'

    rpc_at = NOW - timedelta(seconds=window * 3 + 120)
    evidence = derive_continuity_event_evidence(
        recent_last_real_event_at=None,
        canonical_last_telemetry_at=rpc_at,
        realtime_ingestion=realtime,
        realtime_last_security_telemetry_at=None,
        realtime_last_coverage_at=NOW - timedelta(seconds=31),
    )
    assert evidence.realtime_admitted is False
    assert evidence.last_event_at == rpc_at
    payload = _continuity(last_event_at=evidence.last_event_at)
    assert payload['continuity_slo_pass'] is False
    assert payload['continuity_status'] == 'degraded'


def test_8b_missing_checkpoint_timestamp_is_never_healthy() -> None:
    realtime = _stream_health(lane_state='live', checkpoint_at=None)
    assert realtime.healthy is False
    assert realtime.reason == 'stream_checkpoint_timestamp_missing'
    assert resolve_realtime_live_evidence_at(
        realtime_ingestion=realtime,
        last_security_telemetry_at=PROD_STREAM_LAST_SECURITY_TELEMETRY_AT,
        last_coverage_at=PROD_STREAM_LAST_COVERAGE_AT,
    ) is None


def test_9_catching_up_stream_is_not_healthy_continuity_evidence() -> None:
    """Streams ENABLED never means healthy: a catching-up lane proves nothing yet."""
    realtime = _stream_health(lane_state='catching_up', last_coverage_at=NOW - timedelta(seconds=10))
    assert realtime.streams_enabled is True
    assert realtime.healthy is False
    assert realtime.reason == 'stream_live_lane_not_established'

    evidence = derive_continuity_event_evidence(
        recent_last_real_event_at=None,
        canonical_last_telemetry_at=None,
        realtime_ingestion=realtime,
        realtime_last_security_telemetry_at=None,
        realtime_last_coverage_at=NOW - timedelta(seconds=10),
    )
    assert evidence.last_event_at is None
    assert evidence.realtime_admitted is False
    assert evidence.realtime_rejected_reason == 'stream_live_lane_not_established'
    assert _continuity(last_event_at=evidence.last_event_at)['continuity_slo_pass'] is False


# ---------------------------------------------------------------------------
# 10. Streams disabled → legacy behaviour, minus the `or` bug
# ---------------------------------------------------------------------------

def test_10_streams_disabled_keeps_legacy_candidate_set() -> None:
    realtime = _stream_health(streams_enabled=False)
    assert realtime.status == 'disabled'
    assert realtime.healthy is False

    rpc_at = NOW - timedelta(seconds=60)
    evidence = derive_continuity_event_evidence(
        recent_last_real_event_at=None,
        canonical_last_telemetry_at=rpc_at,
        realtime_ingestion=realtime,
        realtime_last_security_telemetry_at=PROD_STREAM_LAST_SECURITY_TELEMETRY_AT,
        realtime_last_coverage_at=PROD_STREAM_LAST_COVERAGE_AT,
    )
    # Stream rows exist but Streams are off: only the legacy two candidates count.
    assert evidence.realtime_admitted is False
    assert evidence.last_event_at == rpc_at
    assert evidence.source == CONTINUITY_EVENT_SOURCE_FALLBACK_RPC_COVERAGE
    assert _continuity(last_event_at=evidence.last_event_at)['continuity_slo_pass'] is True

    # And with no RPC evidence either, disabled Streams degrade exactly as before.
    dead = derive_continuity_event_evidence(
        recent_last_real_event_at=None,
        canonical_last_telemetry_at=None,
        realtime_ingestion=realtime,
        realtime_last_security_telemetry_at=PROD_STREAM_LAST_SECURITY_TELEMETRY_AT,
        realtime_last_coverage_at=PROD_STREAM_LAST_COVERAGE_AT,
    )
    assert dead.last_event_at is None
    assert dead.source == CONTINUITY_EVENT_SOURCE_NONE


def test_10b_no_realtime_verdict_falls_back_to_legacy_candidates() -> None:
    """A failed realtime read leaves the verdict unknown — fail-closed, not green."""
    rpc_at = NOW - timedelta(seconds=60)
    evidence = derive_continuity_event_evidence(
        recent_last_real_event_at=None,
        canonical_last_telemetry_at=rpc_at,
        realtime_ingestion=None,
        realtime_last_security_telemetry_at=PROD_STREAM_LAST_SECURITY_TELEMETRY_AT,
        realtime_last_coverage_at=PROD_STREAM_LAST_COVERAGE_AT,
    )
    assert evidence.realtime_admitted is False
    assert evidence.realtime_rejected_reason == 'realtime_health_unknown'
    assert evidence.last_event_at == rpc_at


# ---------------------------------------------------------------------------
# 11. quiet wallets
# ---------------------------------------------------------------------------

def test_11_quiet_wallet_with_no_security_event_stays_healthy() -> None:
    """A wallet with no matched transfer for >1 day is quiet, not offline.

    Continuity measures active monitoring/ingestion coverage. Requiring a security
    event would report every quiet wallet as an ingestion outage — the exact
    production shape: security telemetry from Aug 25, coverage fresh on Aug 26.
    """
    realtime = _stream_health()
    assert realtime.live_security_telemetry_fresh is False
    assert realtime.live_coverage_fresh is True
    # Coverage must never be relabelled as a security event.
    assert realtime.live_evidence_kind == 'coverage'

    evidence = derive_continuity_event_evidence(
        recent_last_real_event_at=None,
        canonical_last_telemetry_at=PROD_SLO_EVENT_INGESTION_INPUT,
        realtime_ingestion=realtime,
        realtime_last_security_telemetry_at=PROD_STREAM_LAST_SECURITY_TELEMETRY_AT,
        realtime_last_coverage_at=PROD_STREAM_LAST_COVERAGE_AT,
    )
    assert evidence.source == CONTINUITY_EVENT_SOURCE_REALTIME_COVERAGE
    payload = _continuity(last_event_at=evidence.last_event_at)
    assert payload['continuity_slo_pass'] is True
    assert 'event_ingestion_offline' not in payload['continuity_reason_codes']


# ---------------------------------------------------------------------------
# 12. workspace isolation
# ---------------------------------------------------------------------------

def test_12_all_continuity_candidate_reads_are_workspace_scoped() -> None:
    """Every candidate feeding continuity is read per-workspace, never cross-tenant."""
    source = inspect.getsource(monitoring_runtime_status)
    candidate_tables = (
        # fallback RPC coverage + realtime Stream security telemetry / coverage all
        # read telemetry_events; every one of those reads must carry the scope.
        'FROM telemetry_events',
        'FROM detection_events',
        'FROM monitoring_heartbeats',
        'FROM monitoring_polls',
    )
    for table in candidate_tables:
        blocks = source.split(table)[1:]
        assert blocks, f'expected a {table} read in monitoring_runtime_status'
        for block in blocks:
            head = block[:600]
            assert 'workspace_id = %s::uuid' in head, f'unscoped {table} read: {head[:200]}'

    # The selection helper itself takes only already-scoped timestamps — it has no
    # way to reach across tenants.
    params = set(inspect.signature(derive_continuity_event_evidence).parameters)
    assert params == {
        'recent_last_real_event_at',
        'canonical_last_telemetry_at',
        'realtime_ingestion',
        'realtime_last_security_telemetry_at',
        'realtime_last_coverage_at',
    }


# ---------------------------------------------------------------------------
# 13. the telemetry window is derived, never assumed
# ---------------------------------------------------------------------------

@pytest.mark.parametrize('max_enabled_interval_seconds', [60, 300, 900])
def test_13_window_is_derived_not_assumed_to_be_60(max_enabled_interval_seconds: int) -> None:
    """The fix must hold for whatever window the canonical derivation produces.

    Production reported ``max_enabled_interval_seconds=60`` during the earlier
    investigation and reports 300 now; neither value is baked in here.
    """
    window = canonical_runtime_telemetry_window_seconds(max_enabled_interval_seconds)
    assert window >= canonical_runtime_telemetry_window_seconds()

    coverage_at = NOW - timedelta(seconds=31)
    rpc_at = NOW - timedelta(seconds=window * 3 + 120)
    realtime = _stream_health(
        last_coverage_at=coverage_at,
        last_security_telemetry_at=None,
        telemetry_window_seconds=window,
    )
    evidence = derive_continuity_event_evidence(
        recent_last_real_event_at=None,
        canonical_last_telemetry_at=rpc_at,
        realtime_ingestion=realtime,
        realtime_last_security_telemetry_at=None,
        realtime_last_coverage_at=coverage_at,
    )
    payload = _continuity(last_event_at=evidence.last_event_at, telemetry_window_seconds=window)
    assert payload['continuity_slo_pass'] is True
    assert payload['telemetry_threshold_seconds'] == window


def test_13b_production_interval_300_is_not_the_60_second_window() -> None:
    """Correction 1 lock: 300 and 60 do NOT derive the same window.

    Any test that hard-coded the old 60s production value would silently assert the
    wrong threshold today, which is why the window must be derived.
    """
    assert canonical_runtime_telemetry_window_seconds(60) != (
        canonical_runtime_telemetry_window_seconds(PROD_MAX_ENABLED_INTERVAL_SECONDS)
    )
    # And this PR does not change the configured interval itself.
    assert PROD_MAX_ENABLED_INTERVAL_SECONDS == 300


# ---------------------------------------------------------------------------
# 14-16. untouched-subsystem locks
# ---------------------------------------------------------------------------

def _stream_coverage_refresh_seconds() -> int:
    from services.api.app.quicknode_streams import stream_coverage_refresh_seconds

    return int(stream_coverage_refresh_seconds())


def test_14_stream_coverage_refresh_interval_unchanged() -> None:
    """150s == half the canonical live-lane stale window. Not touched by this fix."""
    from services.api.app.quicknode_streams import live_stale_seconds, stream_coverage_refresh_seconds

    assert stream_coverage_refresh_seconds() == pytest.approx(live_stale_seconds() / 2.0)
    assert stream_coverage_refresh_seconds() == pytest.approx(150.0)


def test_15_stable_worker_polling_interval_unchanged() -> None:
    assert canonical_polling_interval_seconds() == 900
    assert FALLBACK_POLL_CYCLE_SECONDS == 900


def test_16_no_added_provider_calls_in_continuity_selection() -> None:
    """QuickNode RPC volume is unchanged: the selection is pure, in-process logic.

    It reads no provider, opens no connection, and issues no additional query — it
    only re-ranks timestamps the runtime status had already fetched.
    """
    import services.api.app.monitoring_truth as monitoring_truth

    truth_source = inspect.getsource(monitoring_truth)
    for forbidden in (
        'import requests',
        'import httpx',
        'import psycopg',
        'urlopen',
        'rpc_call',
        'connection.execute',
        'pg_connection',
    ):
        assert forbidden not in truth_source, forbidden

    # The per-target coverage-refresh throttle (the thing that bounds Stream write
    # volume) still throttles, and this fix did not add a write path to it.
    from services.api.app.quicknode_streams import (
        _should_refresh_stream_coverage,
        _mark_stream_coverage_refreshed,
        reset_stream_coverage_refresh_state,
    )

    reset_stream_coverage_refresh_state()
    try:
        assert _should_refresh_stream_coverage('target-a', now_mono=1000.0) is True
        _mark_stream_coverage_refreshed('target-a', now_mono=1000.0)
        assert _should_refresh_stream_coverage('target-a', now_mono=1000.0 + 149.0) is False
        assert _should_refresh_stream_coverage('target-a', now_mono=1000.0 + 150.0) is True
    finally:
        reset_stream_coverage_refresh_state()


# ---------------------------------------------------------------------------
# Selection hygiene
# ---------------------------------------------------------------------------

def test_naive_and_aware_candidates_are_comparable() -> None:
    """A naive DB timestamp must not crash the selection (or the age arithmetic)."""
    naive_newer = datetime(2026, 8, 26, 17, 24, 32, 351291)
    evidence = derive_continuity_event_evidence(
        recent_last_real_event_at=None,
        canonical_last_telemetry_at=naive_newer,
        realtime_ingestion=_stream_health(streams_enabled=False),
        realtime_last_security_telemetry_at=None,
        realtime_last_coverage_at=None,
    )
    assert evidence.last_event_at == PROD_STREAM_LAST_COVERAGE_AT
    assert evidence.last_event_at.tzinfo is not None
    assert _continuity(last_event_at=evidence.last_event_at)['continuity_slo_pass'] is True


def test_selection_never_uses_or_precedence() -> None:
    """Guard the regression itself: `a or b` must not survive in the runner."""
    source = inspect.getsource(monitoring_runtime_status)
    assert '_continuity_last_event_at = recent_last_real_event_at or canonical_last_telemetry_at' not in source
    assert 'derive_continuity_event_evidence(' in source


# ---------------------------------------------------------------------------
# Fail-closed companion: continuity passing must not paint a blocked workspace green
# ---------------------------------------------------------------------------

def _isolation_fixtures():
    from services.api.tests.test_target_source_workspace_isolation import (
        WORKSPACE_A,
        WORKSPACE_B,
        _production_tenants,
        _runtime_payload,
    )

    return WORKSPACE_A, WORKSPACE_B, _production_tenants, _runtime_payload


def test_own_dead_lettered_target_still_degrades_with_continuity_passing(monkeypatch) -> None:
    """A blocked target of THIS workspace refuses the green label on its own.

    Before this fix these workspaces were rescued from 'healthy' only because the
    continuity SLO happened to fail on the stale fallback timestamp. Making
    continuity truthful must not hand a workspace with an unmonitored target a
    green status, so the rule is now stated directly.
    """
    workspace_a, workspace_b, production_tenants, runtime_payload = _isolation_fixtures()
    conn = production_tenants(
        targets_by_workspace={workspace_a: ['active'], workspace_b: ['degraded']},
        dead_lettered_by_workspace={workspace_a: 1},
    )
    payload = runtime_payload(monkeypatch, conn, workspace_id=workspace_a)

    # Continuity itself is now truthful: the healthy Stream carries it.
    assert payload['continuity_slo_pass'] is True
    assert payload['continuity_event_evidence']['realtime_admitted'] is True
    # …and the workspace still fails closed on its OWN blocked target.
    assert payload['dead_lettered_targets'] == 1
    assert payload['runtime_status'] != 'healthy'
    assert payload['runtime_status'] != 'live'
    assert payload['status_reason'] == 'targets_blocked'


def test_own_degraded_target_still_degrades_with_continuity_passing(monkeypatch) -> None:
    workspace_a, workspace_b, production_tenants, runtime_payload = _isolation_fixtures()
    conn = production_tenants(
        targets_by_workspace={workspace_a: ['degraded'], workspace_b: ['active']},
    )
    payload = runtime_payload(monkeypatch, conn, workspace_id=workspace_a)

    assert payload['continuity_slo_pass'] is True
    assert payload['runtime_status'] != 'healthy'
    assert payload['fallback_rpc']['degraded_or_unreachable'] is True
    assert payload['fallback_rpc']['degraded_targets'] == 1


def test_another_tenants_blocked_target_still_does_not_degrade_this_workspace(monkeypatch) -> None:
    """The fail-closed guard stays workspace-scoped — no cross-tenant degradation."""
    workspace_a, workspace_b, production_tenants, runtime_payload = _isolation_fixtures()
    conn = production_tenants(
        targets_by_workspace={workspace_a: ['active'], workspace_b: ['degraded']},
        dead_lettered_by_workspace={workspace_b: 1},
    )
    payload = runtime_payload(monkeypatch, conn, workspace_id=workspace_a)

    assert payload['dead_lettered_targets'] == 0
    assert payload['fallback_rpc']['degraded_targets'] == 0
    assert payload['continuity_slo_pass'] is True
    assert payload.get('degraded_reason') != 'target_source_degraded'


def test_production_shape_workspace_is_not_degraded_by_a_stale_fallback_poll(monkeypatch) -> None:
    """The end state this fix exists for.

    Clean workspace, healthy near-tip Stream, fresh coverage, fallback RPC poll an
    hour old — the production shape. Continuity must be carried by the Stream, and
    the workspace must not be degraded solely because the fallback timestamp is old.
    """
    workspace_a, workspace_b, production_tenants, runtime_payload = _isolation_fixtures()
    payload = runtime_payload(
        monkeypatch,
        production_tenants(
            targets_by_workspace={workspace_a: ['active'], workspace_b: ['active']},
        ),
        workspace_id=workspace_a,
    )

    assert payload['realtime_ingestion']['healthy'] is True
    assert payload['continuity_slo_pass'] is True
    assert payload['continuity_status'] == 'continuous_live'
    assert payload['continuity_failed_checks'] == []
    assert payload['continuity_event_evidence']['realtime_admitted'] is True
    assert payload['continuity_event_evidence']['source'].startswith('realtime_stream_')
    # Not a vacuous pass: the fallback poll really is outside the fresh window, and
    # it is still reported under its own name rather than hidden.
    fallback_poll_at = payload['fallback_rpc']['last_poll_at']
    assert fallback_poll_at
    fallback_age = (
        datetime.now(timezone.utc) - datetime.fromisoformat(str(fallback_poll_at))
    ).total_seconds()
    assert fallback_age > canonical_runtime_telemetry_window_seconds()
    assert payload['runtime_status'] not in {'degraded', 'offline'}
