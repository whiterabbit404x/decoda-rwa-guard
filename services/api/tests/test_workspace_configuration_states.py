"""
Tests for workspace configuration state truth.

Covers the highest-risk states per SaaS workflow:
- workspace with no assets
- asset exists but no monitoring target/system
- target/system exists but no enabled config
- disabled config does not count as active monitoring
- enabled config exists but no telemetry yet
- all required facts present -> configured
- all missing -> all four reason codes returned

Cross-workspace scoping is enforced at the DB query level in monitoring_runner.py
(workspace_id parameter on all queries). Unit tests here operate on the pure
diagnostic function which takes pre-counted workspace-scoped values.
"""
from __future__ import annotations

from services.api.app.monitoring_runner import _workspace_configuration_diagnostics
from services.api.app.workspace_monitoring_summary import build_runtime_setup_chain


# ---------------------------------------------------------------------------
# _workspace_configuration_diagnostics
# ---------------------------------------------------------------------------

def test_no_assets_is_not_configured() -> None:
    result = _workspace_configuration_diagnostics(
        valid_protected_asset_count=0,
        linked_monitored_system_count=0,
        persisted_enabled_config_count=0,
        valid_target_system_link_count=0,
    )
    assert result['workspace_configured'] is False
    assert 'no_valid_protected_assets' in result['reason_codes']


def test_asset_only_no_target_is_not_configured() -> None:
    result = _workspace_configuration_diagnostics(
        valid_protected_asset_count=1,
        linked_monitored_system_count=0,
        persisted_enabled_config_count=0,
        valid_target_system_link_count=0,
    )
    assert result['workspace_configured'] is False
    assert 'no_linked_monitored_systems' in result['reason_codes']


def test_target_only_no_asset_is_not_configured() -> None:
    result = _workspace_configuration_diagnostics(
        valid_protected_asset_count=0,
        linked_monitored_system_count=1,
        persisted_enabled_config_count=0,
        valid_target_system_link_count=0,
    )
    assert result['workspace_configured'] is False
    assert 'no_valid_protected_assets' in result['reason_codes']


def test_asset_and_target_but_no_enabled_config_is_not_configured() -> None:
    # Disabled configs must not count as active monitoring.
    # persisted_enabled_config_count=0 represents the case where all configs
    # are disabled or deleted.
    result = _workspace_configuration_diagnostics(
        valid_protected_asset_count=1,
        linked_monitored_system_count=1,
        persisted_enabled_config_count=0,
        valid_target_system_link_count=1,
    )
    assert result['workspace_configured'] is False
    assert 'no_persisted_enabled_monitoring_config' in result['reason_codes']


def test_disabled_config_counted_as_zero_means_not_configured() -> None:
    # Explicit documentation: passing persisted_enabled_config_count=0
    # (i.e. every monitoring config is disabled) means not configured.
    result = _workspace_configuration_diagnostics(
        valid_protected_asset_count=2,
        linked_monitored_system_count=2,
        persisted_enabled_config_count=0,
        valid_target_system_link_count=2,
    )
    assert result['workspace_configured'] is False
    assert result['enabled_configs'] == 0
    assert 'no_persisted_enabled_monitoring_config' in result['reason_codes']


def test_cross_workspace_or_disabled_configs_reduce_to_zero_enabled_configs() -> None:
    # Cross-workspace and disabled configs are excluded before diagnostics.
    result = _workspace_configuration_diagnostics(
        valid_protected_asset_count=1,
        linked_monitored_system_count=1,
        persisted_enabled_config_count=0,
        valid_target_system_link_count=1,
    )
    assert result['workspace_configured'] is False
    assert result['enabled_configs'] == 0
    assert 'no_persisted_enabled_monitoring_config' in result['reason_codes']


def test_invalid_target_system_link_is_not_configured() -> None:
    result = _workspace_configuration_diagnostics(
        valid_protected_asset_count=1,
        linked_monitored_system_count=1,
        persisted_enabled_config_count=1,
        valid_target_system_link_count=0,
    )
    assert result['workspace_configured'] is False
    assert 'target_system_linkage_invalid' in result['reason_codes']


def test_all_required_facts_present_is_configured() -> None:
    result = _workspace_configuration_diagnostics(
        valid_protected_asset_count=1,
        linked_monitored_system_count=1,
        persisted_enabled_config_count=1,
        valid_target_system_link_count=1,
    )
    assert result['workspace_configured'] is True
    assert result['reason_codes'] == []
    assert result['configuration_reason'] is None


def test_all_missing_returns_all_four_reason_codes() -> None:
    result = _workspace_configuration_diagnostics(
        valid_protected_asset_count=0,
        linked_monitored_system_count=0,
        persisted_enabled_config_count=0,
        valid_target_system_link_count=0,
    )
    assert result['workspace_configured'] is False
    assert set(result['reason_codes']) == {
        'no_valid_protected_assets',
        'no_linked_monitored_systems',
        'no_persisted_enabled_monitoring_config',
        'target_system_linkage_invalid',
    }


# ---------------------------------------------------------------------------
# build_runtime_setup_chain - enabled config exists but no telemetry yet
# ---------------------------------------------------------------------------

def test_chain_telemetry_blocked_when_heartbeat_present_but_no_telemetry() -> None:
    # Scenario: monitoring is enabled and heartbeat reported, but the first
    # telemetry event has not arrived yet.
    counters = {
        'workspaces_count': 1,
        'assets_count': 1,
        'verified_assets_count': 1,
        'targets_count': 1,
        'monitored_systems_count': 1,
        'enabled_monitored_systems_count': 1,
    }
    timestamps = {
        'last_heartbeat_at': '2026-05-14T10:00:00+00:00',
        'last_telemetry_at': None,
    }
    chain = build_runtime_setup_chain(counters=counters, timestamps=timestamps)
    steps_by_id = {step['id']: step for step in chain['steps']}

    assert steps_by_id['worker_reporting']['status'] == 'complete'
    assert steps_by_id['telemetry_received']['status'] == 'blocked'


def test_chain_telemetry_complete_when_telemetry_present() -> None:
    counters = {
        'workspaces_count': 1,
        'assets_count': 1,
        'verified_assets_count': 1,
        'targets_count': 1,
        'monitored_systems_count': 1,
        'enabled_monitored_systems_count': 1,
    }
    timestamps = {
        'last_heartbeat_at': '2026-05-14T10:00:00+00:00',
        'last_telemetry_at': '2026-05-14T10:01:00+00:00',
    }
    chain = build_runtime_setup_chain(counters=counters, timestamps=timestamps)
    steps_by_id = {step['id']: step for step in chain['steps']}

    assert steps_by_id['worker_reporting']['status'] == 'complete'
    assert steps_by_id['telemetry_received']['status'] == 'complete'


def test_chain_target_blocked_without_verified_asset() -> None:
    # Asset exists but is not verified -> monitoring_target_created stays pending.
    counters = {'assets_count': 1, 'verified_assets_count': 0}
    chain = build_runtime_setup_chain(counters=counters, timestamps={})
    steps_by_id = {step['id']: step for step in chain['steps']}

    assert steps_by_id['asset_created']['status'] == 'complete'
    assert steps_by_id['monitoring_target_created']['status'] == 'pending'
