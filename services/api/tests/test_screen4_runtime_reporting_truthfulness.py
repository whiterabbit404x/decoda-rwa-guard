"""Screen 4 runtime reporting + header truthfulness (Sections 1, 2, 9).

These tests pin the CLAUDE.md truthfulness rules for the current production outage
(Rabbit / Datto USDC: all_rpc_providers_unavailable, latest_block=None, success=false,
coverage_persisted=false):

  * Replay/historical coverage must never be reported as fresh live reporting.
  * The runtime status can never simultaneously claim a fresh coverage window and a
    replay/none evidence source.
  * A worker that is alive while every RPC provider is unavailable must produce the
    truthful "Worker active; RPC polling unavailable." header — never "Stable polling
    active" (which is reserved for a successful scheduled provider poll in the window).

The reporting split is exercised through the single pure helper
``derive_reporting_sub_counts`` (the same one the runtime endpoint uses), and the header
through ``build_worker_status``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from services.api.app.monitoring_truth import (
    PROVIDER_UNAVAILABLE_REASON,
    derive_reporting_sub_counts,
)
from services.api.app.worker_status import (
    WORKER_ALIVE_RPC_UNAVAILABLE_HEADLINE,
    WORKER_ALIVE_RPC_UNAVAILABLE_STREAM_SUFFIX,
    build_worker_status,
)


def _now() -> datetime:
    return datetime(2026, 7, 18, 12, 0, tzinfo=timezone.utc)


TTL = 900
WINDOW = 300


# ---------------------------------------------------------------------------
# Test 1: replay-only evidence produces fresh_live_reporting_systems=0
# ---------------------------------------------------------------------------

def test_replay_only_evidence_yields_zero_fresh_live_reporting():
    """The exact Rabbit/Datto outage shape: one configured system with historical
    coverage but replay evidence. fresh_live must be 0; historical/replay expose the
    stale coverage separately so the UI never reads it as live."""
    counts = derive_reporting_sub_counts(
        configured_systems=1,
        # A legacy/receipt row makes the aggregate reporting_systems=1, but the canonical
        # fresh-live count is 0 because the evidence source is replay.
        fresh_live_reporting_systems=1,
        historically_reporting_systems=1,
        telemetry_window_seconds=WINDOW,
        evidence_source='replay',
    )
    assert counts.configured_systems == 1
    assert counts.fresh_live_reporting_systems == 0
    assert counts.historically_reporting_systems == 1
    assert counts.replay_only_systems == 1
    assert counts.status_reason == PROVIDER_UNAVAILABLE_REASON
    assert counts.fresh_coverage_window_claimed is False


def test_none_evidence_is_not_fresh_live():
    counts = derive_reporting_sub_counts(
        configured_systems=1,
        fresh_live_reporting_systems=0,
        historically_reporting_systems=0,
        telemetry_window_seconds=WINDOW,
        evidence_source='none',
    )
    assert counts.fresh_live_reporting_systems == 0
    assert counts.replay_only_systems == 0
    assert counts.status_reason == PROVIDER_UNAVAILABLE_REASON


def test_live_evidence_with_fresh_reporting_claims_window():
    """After recovery a genuine live poll reports fresh coverage inside the window."""
    counts = derive_reporting_sub_counts(
        configured_systems=1,
        fresh_live_reporting_systems=1,
        historically_reporting_systems=1,
        telemetry_window_seconds=WINDOW,
        evidence_source='live',
    )
    assert counts.fresh_live_reporting_systems == 1
    assert counts.replay_only_systems == 0
    assert counts.status_reason == f'fresh_coverage_window_{WINDOW}s'
    assert counts.fresh_coverage_window_claimed is True


# ---------------------------------------------------------------------------
# Test 2: runtime status cannot report fresh coverage and replay simultaneously
# ---------------------------------------------------------------------------

def test_fresh_coverage_window_never_claimed_with_non_live_evidence():
    """Fail-closed invariant: for every non-live evidence source the status reason is
    provider_unavailable, never fresh_coverage_window_Ns — even if an aggregate
    reporting count is > 0."""
    for evidence in ('replay', 'none', 'simulator', 'replay_or_none', 'unknown', ''):
        counts = derive_reporting_sub_counts(
            configured_systems=1,
            fresh_live_reporting_systems=3,  # aggregate says "reporting"
            historically_reporting_systems=3,
            telemetry_window_seconds=WINDOW,
            evidence_source=evidence,
        )
        assert counts.fresh_coverage_window_claimed is False, evidence
        assert not counts.status_reason.startswith('fresh_coverage_window'), evidence
        assert counts.status_reason == PROVIDER_UNAVAILABLE_REASON, evidence
        assert counts.fresh_live_reporting_systems == 0, evidence


def test_fresh_coverage_and_replay_are_mutually_exclusive():
    """A fresh coverage window and replay-only systems can never both be non-trivially
    claimed: claiming the window forces replay_only to 0."""
    live = derive_reporting_sub_counts(
        configured_systems=2,
        fresh_live_reporting_systems=2,
        historically_reporting_systems=2,
        telemetry_window_seconds=WINDOW,
        evidence_source='live',
    )
    assert live.fresh_coverage_window_claimed is True
    assert live.replay_only_systems == 0

    replay = derive_reporting_sub_counts(
        configured_systems=2,
        fresh_live_reporting_systems=2,
        historically_reporting_systems=2,
        telemetry_window_seconds=WINDOW,
        evidence_source='replay',
    )
    assert replay.fresh_coverage_window_claimed is False
    assert replay.fresh_live_reporting_systems == 0
    assert replay.replay_only_systems == 2


# ---------------------------------------------------------------------------
# Test 3: worker alive with no provider produces truthful header text
# ---------------------------------------------------------------------------

def test_worker_alive_no_provider_poll_shows_rpc_unavailable_header():
    """Worker heartbeat fresh (loop alive) but the scheduled provider poll did not
    succeed (all_rpc_providers_unavailable) => truthful RPC-unavailable header, never
    'Stable polling active'."""
    now = _now()
    status = build_worker_status(
        now=now,
        realtime_is_enabled=False,
        stable_last_heartbeat_at=now - timedelta(seconds=30),  # worker alive
        stable_last_poll_at=now - timedelta(seconds=30),       # loop ran...
        stable_last_coverage_poll_at=None,                     # ...but nothing persisted
        heartbeat_ttl_seconds=TTL,
        realtime_watcher=None,
        stable_poll_succeeded=False,                           # poll failed (no latest block)
    )
    assert status['headline'] == WORKER_ALIVE_RPC_UNAVAILABLE_HEADLINE
    assert 'Stable polling active' not in status['headline']
    assert status['rpc_polling_unavailable'] is True
    assert status['stable_polling']['rpc_polling_available'] is False
    assert status['stable_polling']['poll_succeeded'] is False


def test_worker_alive_no_provider_with_quicknode_stream_receiving():
    """When the QuickNode stream is receiving blocks, the header adds the truthful
    provider-verification-unavailable clause."""
    now = _now()
    status = build_worker_status(
        now=now,
        realtime_is_enabled=False,
        stable_last_heartbeat_at=now - timedelta(seconds=30),
        stable_last_poll_at=now - timedelta(seconds=30),
        heartbeat_ttl_seconds=TTL,
        realtime_watcher=None,
        stable_poll_succeeded=False,
        quicknode_stream_receiving=True,
    )
    assert status['headline'] == (
        WORKER_ALIVE_RPC_UNAVAILABLE_HEADLINE + WORKER_ALIVE_RPC_UNAVAILABLE_STREAM_SUFFIX
    )
    assert 'QuickNode stream is receiving blocks' in status['headline']
    assert 'provider verification is unavailable' in status['headline']


def test_successful_scheduled_poll_restores_stable_polling_header():
    """A successful scheduled provider poll inside the window restores the
    'Stable polling active' header."""
    now = _now()
    status = build_worker_status(
        now=now,
        realtime_is_enabled=False,
        stable_last_heartbeat_at=now - timedelta(seconds=30),
        stable_last_poll_at=now - timedelta(seconds=30),
        stable_last_coverage_poll_at=now - timedelta(seconds=30),
        heartbeat_ttl_seconds=TTL,
        realtime_watcher=None,
        stable_poll_succeeded=True,
    )
    assert status['headline'] == 'Stable polling active. Realtime WebSocket paused.'
    assert status['rpc_polling_unavailable'] is False
    assert status['stable_polling']['rpc_polling_available'] is True


def test_unknown_poll_success_preserves_legacy_header():
    """Backward compatibility: when stable_poll_succeeded is not supplied (None) the
    header logic is unchanged (heartbeat/poll freshness wins)."""
    now = _now()
    status = build_worker_status(
        now=now,
        realtime_is_enabled=False,
        stable_last_heartbeat_at=now - timedelta(seconds=30),
        stable_last_poll_at=now - timedelta(seconds=30),
        heartbeat_ttl_seconds=TTL,
        realtime_watcher=None,
    )
    assert status['headline'] == 'Stable polling active. Realtime WebSocket paused.'
    assert status['rpc_polling_unavailable'] is False
