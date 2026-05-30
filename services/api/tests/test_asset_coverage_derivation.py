"""Tests for asset coverage derivation: monitoring_status, coverage_reason, next_action."""
from __future__ import annotations

import pytest

from app.pilot import _compute_asset_monitoring_status


def _asset(
    monitoring_link_status: str = 'not_configured',
    has_monitoring_target: bool = False,
    has_linked_monitored_system: bool = False,
    has_heartbeat: bool | None = None,
    has_telemetry: bool | None = None,
    telemetry_fresh: bool | None = None,
    monitoring_systems_count: int = 0,
    linked_target_id: str | None = None,
) -> dict:
    return {
        'monitoring_link_status': monitoring_link_status,
        'has_monitoring_target': has_monitoring_target,
        'has_linked_monitored_system': has_linked_monitored_system,
        'has_heartbeat': has_heartbeat,
        'has_telemetry': has_telemetry,
        'telemetry_fresh': telemetry_fresh,
        'monitoring_systems_count': monitoring_systems_count,
        'linked_target_id': linked_target_id,
    }


def test_linked_asset_with_live_telemetry_returns_live_verified() -> None:
    asset = _asset(
        monitoring_link_status='attached',
        has_monitoring_target=True,
        has_linked_monitored_system=True,
        has_heartbeat=True,
        has_telemetry=True,
        telemetry_fresh=True,
        linked_target_id='target-abc',
    )
    result = _compute_asset_monitoring_status(asset, workspace_has_live_telemetry=True)
    assert result['monitoring_status'] == 'live_verified'
    assert result['coverage_reason'] == 'explicit_asset_target_link'
    assert result['next_action_label'] == 'View telemetry'
    assert 'target-abc' in result['next_action_href']


def test_linked_asset_no_telemetry_returns_waiting() -> None:
    asset = _asset(
        monitoring_link_status='attached',
        has_monitoring_target=True,
        has_linked_monitored_system=True,
        has_heartbeat=True,
        has_telemetry=False,
    )
    result = _compute_asset_monitoring_status(asset, workspace_has_live_telemetry=True)
    assert result['monitoring_status'] == 'waiting_for_telemetry'
    assert result['coverage_reason'] == 'explicit_asset_target_link'
    assert result['next_action_label'] == 'Verify telemetry'


def test_linked_asset_stale_telemetry_returns_waiting() -> None:
    asset = _asset(
        monitoring_link_status='attached',
        has_monitoring_target=True,
        has_linked_monitored_system=True,
        has_heartbeat=True,
        has_telemetry=True,
        telemetry_fresh=False,
    )
    result = _compute_asset_monitoring_status(asset, workspace_has_live_telemetry=True)
    assert result['monitoring_status'] == 'waiting_for_telemetry'
    assert result['coverage_reason'] == 'explicit_asset_target_link'


def test_unlinked_asset_workspace_has_live_telemetry_returns_not_linked() -> None:
    asset = _asset(
        monitoring_link_status='not_configured',
        has_monitoring_target=False,
    )
    result = _compute_asset_monitoring_status(asset, workspace_has_live_telemetry=True)
    assert result['monitoring_status'] == 'not_linked'
    assert result['coverage_reason'] == 'workspace_live_telemetry_unlinked'
    assert result['next_action_label'] == 'Link monitoring source'
    assert result['next_action_href'] == '/monitoring-sources'


def test_unlinked_asset_no_workspace_telemetry_returns_not_configured() -> None:
    asset = _asset(
        monitoring_link_status='not_configured',
        has_monitoring_target=False,
    )
    result = _compute_asset_monitoring_status(asset, workspace_has_live_telemetry=False)
    assert result['monitoring_status'] == 'not_configured'
    assert result['coverage_reason'] == 'no_linked_telemetry'
    assert result['next_action_label'] == 'Add monitoring source'


def test_not_linked_is_distinct_from_waiting_for_telemetry() -> None:
    unlinked = _asset(monitoring_link_status='not_configured', has_monitoring_target=False)
    unlinked_result = _compute_asset_monitoring_status(unlinked, workspace_has_live_telemetry=True)
    assert unlinked_result['monitoring_status'] == 'not_linked'
    assert unlinked_result['monitoring_status'] != 'waiting_for_telemetry'


def test_live_verified_requires_telemetry_fresh_true() -> None:
    # has_telemetry=True but telemetry_fresh=False → not live_verified
    asset = _asset(
        monitoring_link_status='attached',
        has_monitoring_target=True,
        has_linked_monitored_system=True,
        has_telemetry=True,
        telemetry_fresh=False,
    )
    result = _compute_asset_monitoring_status(asset, workspace_has_live_telemetry=True)
    assert result['monitoring_status'] != 'live_verified'


def test_live_verified_next_action_href_contains_target_id() -> None:
    asset = _asset(
        monitoring_link_status='attached',
        has_monitoring_target=True,
        has_linked_monitored_system=True,
        has_telemetry=True,
        telemetry_fresh=True,
        linked_target_id='t-999',
    )
    result = _compute_asset_monitoring_status(asset, workspace_has_live_telemetry=True)
    assert result['next_action_href'] == '/monitoring-sources/t-999/telemetry'


def test_live_verified_no_target_id_falls_back_to_monitoring_sources() -> None:
    asset = _asset(
        monitoring_link_status='attached',
        has_monitoring_target=True,
        has_linked_monitored_system=True,
        has_telemetry=True,
        telemetry_fresh=True,
        linked_target_id=None,
    )
    result = _compute_asset_monitoring_status(asset, workspace_has_live_telemetry=True)
    assert result['next_action_href'] == '/monitoring-sources'
