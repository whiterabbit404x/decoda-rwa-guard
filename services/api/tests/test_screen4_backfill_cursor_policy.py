"""Screen 4 Section 7: completed historical backfill must not repeat every cycle.

Encodes the re-run policy for the per-target/rule-set historical backfill: once a
completion marker/cursor is persisted, the backfill only re-runs on an explicit trigger
(new historical rows, rule version change, explicit replay, or cursor recovery).
"""
from __future__ import annotations

from services.api.app.monitoring_truth import should_run_historical_backfill


# ---------------------------------------------------------------------------
# Test 12: completed historical backfill is not repeated every cycle
# ---------------------------------------------------------------------------

def test_completed_backfill_does_not_repeat_by_default():
    assert should_run_historical_backfill(backfill_completed=True) is False


def test_never_completed_backfill_runs():
    assert should_run_historical_backfill(backfill_completed=False) is True


def test_new_historical_rows_trigger_rerun():
    assert should_run_historical_backfill(
        backfill_completed=True, new_historical_rows=True
    ) is True


def test_rule_version_change_triggers_rerun():
    assert should_run_historical_backfill(
        backfill_completed=True, rule_version_changed=True
    ) is True


def test_explicit_replay_triggers_rerun():
    assert should_run_historical_backfill(
        backfill_completed=True, replay_requested=True
    ) is True


def test_cursor_recovery_triggers_rerun():
    assert should_run_historical_backfill(
        backfill_completed=True, cursor_recovery_needed=True
    ) is True


def test_completed_backfill_stays_idle_across_repeated_cycles():
    # Simulate five scheduled cycles with no new rows / rule change: never re-runs.
    for _ in range(5):
        assert should_run_historical_backfill(backfill_completed=True) is False
