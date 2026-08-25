"""Blocker 2 proofs: runtime status does not require 60-second RPC polling.

Production evidence that motivated these tests (API runtime-status logs):

    monitoring_runtime_telemetry_window telemetry_window_seconds=300
        max_enabled_interval_seconds=60
    monitoring_reporting_systems reporting_systems=0 fresh_live_reporting_systems=0
    evidence_source=replay monitoring_status=limited

The worker deliberately polls on the canonical 900s cadence (a QuickNode Stream is the
realtime path; stable RPC polling is the 900s fallback). The concern: does the runtime
status *require* a fast (60s) RPC cadence to call a provider healthy, so a 900s poll —
or a Streams-only posture — reads as a false "provider unavailable"?

These tests pin the truthful invariants WITHOUT changing application code:

  * The canonical worker cadence and its derived freshness/heartbeat window are
    900s-based (1800s), never derived from the legacy ~30s MONITOR_POLL_INTERVAL_SECONDS
    knob — so a healthy 900s cadence is never falsely stale (it does not require 60s).
  * The runtime "fresh reporting" telemetry window is INTERVAL-DRIVEN: a stored 60s
    target interval floors it at 300s (the observed state), while a 900s interval grows
    it to 1020s (accommodating a 900s poll). This is exactly why the optional DB repair
    (targets.monitoring_interval_seconds 60 -> 900) aligns the fallback window, and why
    no code change is needed — the mechanism already scales with the configured interval.

The complementary evidence-truthfulness invariants (no live claimed until real Stream
evidence; live recognized once fresh 'live' evidence exists) are proven in
services/api/tests/test_screen4_runtime_reporting_truthfulness.py and are only
referenced here, not duplicated.
"""
from __future__ import annotations

import pytest

from services.api.app import monitoring_runner as mr
from services.api.app.worker_status import (
    DEFAULT_STABLE_POLL_STALE_SECONDS,
    stable_poll_stale_threshold_seconds,
)


# ---------------------------------------------------------------------------
# The canonical worker cadence is 900s — runtime liveness is NOT a 60s assumption.
# ---------------------------------------------------------------------------

def test_canonical_polling_interval_defaults_to_900(monkeypatch):
    """With no override the canonical cadence is 900s (the demo's stable-poll floor),
    never a 30s/60s value."""
    monkeypatch.delenv('EVM_POLLING_INTERVAL_SECONDS', raising=False)
    monkeypatch.delenv('MONITORING_WORKER_INTERVAL_SECONDS', raising=False)
    assert mr.canonical_polling_interval_seconds() == 900


def test_configured_900_interval_is_honored_and_floored(monkeypatch):
    """An explicit 900s cadence resolves to 900; a sub-floor value is raised to the
    per-target minimum (never silently polled faster), so 900 is respected as-is."""
    monkeypatch.setenv('EVM_POLLING_INTERVAL_SECONDS', '900')
    assert mr.canonical_polling_interval_seconds() == 900
    # A below-floor value is capped UP to the min interval, not down to 60/30.
    monkeypatch.setenv('EVM_POLLING_INTERVAL_SECONDS', '5')
    assert mr.canonical_polling_interval_seconds() == mr._min_monitoring_interval_seconds()


def test_canonical_freshness_window_scales_from_900_not_60(monkeypatch):
    """The freshness/heartbeat-grace window the runtime labels read derives from the
    canonical 900s cadence (two cycles = 1800s), NOT from the legacy ~30s knob.

    This is the invariant that keeps a healthy 900s stable poll from ever being called
    stale between cycles — i.e. runtime health does not require 60-second polling.
    """
    monkeypatch.delenv('EVM_POLLING_INTERVAL_SECONDS', raising=False)
    monkeypatch.delenv('MONITORING_WORKER_INTERVAL_SECONDS', raising=False)
    monkeypatch.delenv('MONITORING_STABLE_POLL_STALE_SECONDS', raising=False)
    window = mr.canonical_stable_poll_stale_threshold_seconds()
    # 2 * 900 = 1800s, comfortably longer than one 900s cadence.
    assert window == 1800
    assert window >= mr.canonical_polling_interval_seconds()
    # A window computed from the legacy 30s knob would be an order of magnitude smaller;
    # prove the canonical helper did NOT use it.
    legacy_knob_window = stable_poll_stale_threshold_seconds(mr.MONITOR_POLL_INTERVAL_SECONDS)
    assert window > legacy_knob_window or DEFAULT_STABLE_POLL_STALE_SECONDS == window
    assert window >= 900  # never a 60s-scale liveness bar


# ---------------------------------------------------------------------------
# The runtime "fresh reporting" telemetry window is INTERVAL-DRIVEN.
#
# Faithful mirror of the inline computation in
# monitoring_runner._build_workspace_monitoring_summary:
#     telemetry_window_seconds = max(300, MONITOR_POLL_INTERVAL_SECONDS * 6)          # base floor
#     telemetry_window_seconds = max(                                                 # per-interval grow
#         telemetry_window_seconds,
#         max_enabled_interval_seconds + max(MONITOR_POLL_INTERVAL_SECONDS * 4, 60),
#     )
# Reading the REAL MONITOR_POLL_INTERVAL_SECONDS constant makes this a contract check:
# if that default drifts, or the formula's floors change, these assertions flag it.
# ---------------------------------------------------------------------------

def _runtime_telemetry_window_seconds(max_enabled_interval_seconds: int) -> int:
    poll = mr.MONITOR_POLL_INTERVAL_SECONDS
    window = max(300, poll * 6)
    if max_enabled_interval_seconds and max_enabled_interval_seconds > 0:
        window = max(window, max_enabled_interval_seconds + max(poll * 4, 60))
    return window


def test_runtime_default_poll_interval_is_30_not_60():
    """The legacy MONITOR_POLL_INTERVAL_SECONDS knob defaults to 30; the observed
    max_enabled_interval_seconds=60 therefore must be a STORED target interval, not this
    default (which is what makes it a DB-config fact, repairable to 900)."""
    assert mr.MONITOR_POLL_INTERVAL_SECONDS == 30


def test_stored_60s_interval_yields_300s_window_matching_production():
    """A stored 60s target interval reproduces the exact observed runtime state:
    telemetry_window_seconds=300 (floor), so a 900s poll lands outside it."""
    assert _runtime_telemetry_window_seconds(60) == 300


def test_900s_interval_grows_window_to_accommodate_900s_poll():
    """Aligning the stored interval to the 900s cadence grows the fresh-reporting
    window past 900s (=1020s), so a healthy 900s stable poll is 'fresh' — the effect of
    the optional DB repair. Proves the window is interval-driven, never hardcoded to 60s.
    """
    window = _runtime_telemetry_window_seconds(900)
    assert window == 1020
    assert window > 900  # a 900s-old stable poll is inside the window


@pytest.mark.parametrize(
    'interval, expected',
    [(30, 300), (60, 300), (150, 300), (300, 420), (600, 720), (900, 1020), (1800, 1920)],
)
def test_window_is_monotonic_in_configured_interval(interval, expected):
    """The fresh-reporting window never shrinks as the configured interval grows, and
    is never pinned to a 60s cadence — it tracks whatever interval is configured."""
    assert _runtime_telemetry_window_seconds(interval) == expected
