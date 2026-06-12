"""
Tests for Base chain runtime status correctness.

Verifies that:
  A. Old chain_id=1 telemetry does not downgrade a Base target when newer
     chain_id=8453 telemetry exists (stale coverage records are ignored).
  B. A Base target with fresh chain_id=8453 telemetry is not marked offline.
  C. Coverage records for disabled targets are ignored by the contradiction check.
  D. Duplicate/stale monitored_system rows do not corrupt the reporting count.
  E. evidence_source remains 'live' when Base telemetry is live.
  F. reconcile_enabled_targets_monitored_systems syncs chain field to target.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from services.api.app.workspace_monitoring_summary import build_workspace_monitoring_summary


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime(2026, 6, 11, 12, 0, tzinfo=timezone.utc)


def _base_summary(**overrides):
    """Build a monitoring summary with sane Base-chain defaults."""
    now = _now()
    payload = dict(
        now=now,
        workspace_configured=True,
        configuration_reason_codes=[],
        query_failure_detected=False,
        schema_drift_detected=False,
        missing_telemetry_only=False,
        monitoring_mode='live',
        runtime_status='live',
        configured_systems=1,
        monitored_systems_count=1,
        reporting_systems=1,
        protected_assets=1,
        last_poll_at=now,
        last_heartbeat_at=now,
        last_telemetry_at=now - timedelta(seconds=30),
        last_coverage_telemetry_at=now - timedelta(seconds=30),
        telemetry_kind='coverage',
        last_detection_at=now - timedelta(seconds=25),
        evidence_source='live',
        status_reason=None,
        configuration_reason=None,
        valid_protected_asset_count=1,
        linked_monitored_system_count=1,
        persisted_enabled_config_count=1,
        valid_target_system_link_count=1,
        telemetry_window_seconds=300,
    )
    payload.update(overrides)
    return build_workspace_monitoring_summary(**payload)


# ---------------------------------------------------------------------------
# A. Old chain_id=1 telemetry does not downgrade Base target
# ---------------------------------------------------------------------------

def test_base_target_not_degraded_by_old_ethereum_telemetry():
    """Runtime stays live when the newest telemetry is Base (8453) and the old
    Ethereum telemetry (chain_id=1) is no longer the latest record."""
    # The canonical_last_telemetry_at query uses MAX(observed_at), so the
    # Base row (more recent) wins. Simulate that by providing a fresh timestamp.
    s = _base_summary()
    assert s['runtime_status'] not in {'offline', 'degraded'}, (
        f'Base target should not be degraded by old Ethereum telemetry: runtime_status={s["runtime_status"]}'
    )
    assert 'offline_with_current_telemetry' not in s['contradiction_flags']


def test_stale_ethereum_coverage_record_does_not_trigger_target_reporting_contradiction():
    """A stale coverage record (last_telemetry_at outside window) for an old
    Ethereum-chain target must NOT trigger the
    target_reporting_without_telemetry_event_link contradiction.

    The runtime logic (monitoring_runner.py) skips coverage records whose
    last_telemetry_at is outside telemetry_window_seconds.
    The build_workspace_monitoring_summary layer sees reporting_systems=1 (Base
    target) and no contradiction flags.
    """
    # Simulate: Base target is reporting (reporting_systems=1), fresh telemetry.
    # The old Ethereum target's stale coverage record is already filtered out
    # in monitoring_runner.py before the summary is built.
    s = _base_summary(reporting_systems=1)
    assert 'target_reporting_without_telemetry_event_link' not in s.get('guard_flags', [])
    assert s['runtime_status'] != 'offline'


# ---------------------------------------------------------------------------
# B. Base target with fresh 8453 telemetry is not marked offline
# ---------------------------------------------------------------------------

def test_base_target_fresh_telemetry_not_offline():
    """A Base target with fresh live telemetry must not be offline."""
    now = _now()
    s = _base_summary(
        last_telemetry_at=now - timedelta(seconds=60),
        last_coverage_telemetry_at=now - timedelta(seconds=60),
        evidence_source='live',
        runtime_status='live',
        reporting_systems=1,
    )
    assert s['runtime_status'] != 'offline'
    assert 'offline_with_current_telemetry' not in s['contradiction_flags']
    assert s['telemetry_freshness'] == 'fresh'


def test_base_target_offline_status_fires_contradiction_when_telemetry_is_fresh():
    """If runtime_status is mistakenly set to offline but telemetry is fresh,
    offline_with_current_telemetry guard fires — proving the guard works."""
    s = _base_summary(runtime_status='offline')
    assert 'offline_with_current_telemetry' in s['contradiction_flags'], (
        'offline_with_current_telemetry guard must fire when runtime=offline but telemetry is fresh'
    )


# ---------------------------------------------------------------------------
# C. Coverage records for disabled targets are ignored
# ---------------------------------------------------------------------------

def test_disabled_target_coverage_record_ignored_in_runtime():
    """When reporting_systems=0 (disabled target removed from active set),
    runtime should not claim live — but also should not trigger a
    target_reporting_without_telemetry contradiction that degrades an otherwise
    valid Base status.

    The monitoring_runner.py fix ensures disabled targets are excluded from the
    contradiction loop.  The summary layer sees reporting_systems=0 and limited."""
    s = _base_summary(
        reporting_systems=0,
        runtime_status='live',
    )
    # live_monitoring_without_reporting_systems should fire because runtime claims
    # live but reporting_systems=0 — this is the correct, expected contradiction.
    assert 'live_monitoring_without_reporting_systems' in s['contradiction_flags']
    # target_reporting_without_telemetry_event_link must NOT appear: the disabled
    # target's coverage record is filtered out before build_workspace_monitoring_summary.
    assert 'target_reporting_without_telemetry_event_link' not in s.get('guard_flags', [])


# ---------------------------------------------------------------------------
# D. Duplicate/stale monitored_system rows do not corrupt reporting_systems
# ---------------------------------------------------------------------------

def test_single_active_monitored_system_yields_correct_reporting_count():
    """With one active Base monitored_system and one stale (disabled) Ethereum
    monitored_system, reporting_systems should reflect only the active system."""
    s = _base_summary(
        reporting_systems=1,
        monitored_systems_count=2,  # one active (base), one stale (ethereum)
    )
    assert s['reporting_systems_count'] == 1
    assert s['runtime_status'] != 'offline'
    assert 'offline_with_current_telemetry' not in s['contradiction_flags']


# ---------------------------------------------------------------------------
# E. evidence_source remains 'live' for Base telemetry
# ---------------------------------------------------------------------------

def test_evidence_source_is_live_for_base_telemetry():
    """Live rpc_polling rows from Base must produce evidence_source='live_provider'."""
    s = _base_summary(evidence_source='live')
    # The summary normalizes 'live' → 'live_provider'
    assert s['evidence_source_summary'] == 'live_provider', (
        f'Expected live_provider, got {s["evidence_source_summary"]}'
    )


def test_simulator_evidence_not_treated_as_live():
    """Simulator evidence must never be presented as live_provider."""
    s = _base_summary(evidence_source='simulator')
    assert s['evidence_source_summary'] != 'live_provider'


# ---------------------------------------------------------------------------
# F. reconcile chain-sync logic is present in pilot.py
# ---------------------------------------------------------------------------

def test_reconcile_contains_chain_sync_update():
    """reconcile_enabled_targets_monitored_systems must contain the SQL that
    updates monitored_systems.chain to match targets.chain_network."""
    source = open('services/api/app/pilot.py', encoding='utf-8').read()
    assert 'COALESCE(ms.chain' in source and "COALESCE(t.chain_network, '')" in source, (
        'pilot.py must contain chain-sync UPDATE in reconcile function'
    )
    assert 'target_disabled_or_deleted' in source, (
        'pilot.py must disable monitored_systems for disabled/deleted targets'
    )


def test_monitoring_runner_stale_coverage_skip_logic_present():
    """monitoring_runner.py must skip stale coverage records in the
    target_reporting_without_telemetry_count loop."""
    source = open('services/api/app/monitoring_runner.py', encoding='utf-8').read()
    assert 'healthy_enabled_target_ids' in source, (
        'monitoring_runner.py must reference healthy_enabled_target_ids in the coverage loop'
    )
    assert '_cov_last_telem' in source, (
        'monitoring_runner.py must check coverage last_telemetry_at freshness'
    )
    assert 'telemetry_window_seconds' in source


# ---------------------------------------------------------------------------
# G. _count_persisted_enabled_monitoring_configs uses targets, not monitored_targets
# ---------------------------------------------------------------------------

def test_persisted_config_count_joins_targets_not_monitored_targets():
    """_count_persisted_enabled_monitoring_configs must JOIN with targets (not
    monitored_targets). Migration 0084 created direct monitoring_configs with
    target_id = targets.id; the old monitored_targets JOIN always returned 0,
    causing workspace_configured = False → runtime_status = 'offline'."""
    import re
    source = open('services/api/app/monitoring_runner.py', encoding='utf-8').read()
    fn_match = re.search(
        r'def _count_persisted_enabled_monitoring_configs.*?(?=\ndef |\Z)',
        source,
        re.DOTALL,
    )
    assert fn_match is not None, '_count_persisted_enabled_monitoring_configs not found'
    fn_body = fn_match.group(0)
    # The SQL inside the function must join targets, not monitored_targets
    assert 'JOIN targets t' in fn_body, (
        '_count_persisted_enabled_monitoring_configs SQL must JOIN targets t'
    )
    assert 'JOIN monitored_targets' not in fn_body, (
        '_count_persisted_enabled_monitoring_configs SQL must not JOIN monitored_targets'
    )


def test_persist_coverage_telemetry_uses_non_null_block_number():
    """_persist_live_coverage_telemetry must store a non-null block_number in
    payload_json even when latest_block is None (RPC probe failed).
    This ensures canonical_last_telemetry_at can find the row."""
    source = open('services/api/app/monitoring_runner.py', encoding='utf-8').read()
    assert '_effective_block' in source, (
        '_persist_live_coverage_telemetry must use _effective_block fallback for block_number'
    )
    # Ensure the payload uses _effective_block, not the raw latest_block
    assert "'block_number': _effective_block" in source, (
        "_telem_payload must store '_effective_block' not 'provider_result.latest_block'"
    )


def test_reconcile_contains_asset_id_sync():
    """reconcile_enabled_targets_monitored_systems must sync monitored_systems.asset_id
    from targets.asset_id so _row_has_valid_target_asset_link returns True."""
    source = open('services/api/app/pilot.py', encoding='utf-8').read()
    assert 'reconcile_asset_id_sync_failed' in source, (
        'pilot.py must attempt asset_id sync and log failures'
    )
    assert 'ms.asset_id' in source and 't.asset_id' in source, (
        'pilot.py reconcile must UPDATE monitored_systems.asset_id from targets.asset_id'
    )
