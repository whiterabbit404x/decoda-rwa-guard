"""Tests for the live RPC proof chain gating logic.

A. Live telemetry only → live_telemetry_without_proof_chain guard fires, status != live
B. Live telemetry + detection but no alert/incident → not fully LIVE
C. Full proof chain (detection + alert + incident + response + evidence) → status == live
D. Launch proof: summary fields require full chain to be truthy
E. _build_summary helper exports consistent chain field set
F. monitoring_status == 'live' only when full proof chain exists (no guard flags)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.api.app.workspace_monitoring_summary import (
    HARD_GUARD_FLAGS,
    HARD_GUARD_PRIORITY,
    build_workspace_monitoring_summary,
)


def _now() -> datetime:
    return datetime(2026, 5, 28, 12, 0, tzinfo=timezone.utc)


def _base_params(**overrides: object) -> dict[str, object]:
    now = _now()
    params: dict[str, object] = {
        'now': now,
        'workspace_configured': True,
        'configuration_reason_codes': None,
        'query_failure_detected': False,
        'schema_drift_detected': False,
        'missing_telemetry_only': False,
        'monitoring_mode': 'live',
        'runtime_status': 'live',
        'configured_systems': 1,
        'monitored_systems_count': 1,
        'reporting_systems': 1,
        'protected_assets': 1,
        'last_poll_at': now,
        'last_heartbeat_at': now,
        'last_telemetry_at': now - timedelta(seconds=30),
        'last_coverage_telemetry_at': now - timedelta(seconds=30),
        'telemetry_kind': 'coverage',
        'last_detection_at': None,
        'evidence_source': 'live',
        'status_reason': None,
        'configuration_reason': None,
        'valid_protected_asset_count': 1,
        'linked_monitored_system_count': 1,
        'persisted_enabled_config_count': 1,
        'valid_target_system_link_count': 1,
        'telemetry_window_seconds': 300,
        'active_alerts_count': 0,
        'active_incidents_count': 0,
        'response_actions_count': 0,
        'evidence_packages_count': 0,
        'detections_count': 0,
    }
    params.update(overrides)
    return params


def _build(**overrides: object) -> dict[str, object]:
    return build_workspace_monitoring_summary(**_base_params(**overrides))


# ──────────────────────────────────────────────────────────────────────────────
# A. Live telemetry only → live_telemetry_without_proof_chain guard, not LIVE
# ──────────────────────────────────────────────────────────────────────────────

def test_A_live_telemetry_without_detection_sets_proof_chain_guard() -> None:
    summary = _build(detections_count=0)
    assert 'live_telemetry_without_proof_chain' in summary['contradiction_flags']


def test_A_proof_chain_guard_is_in_hard_guard_flags() -> None:
    assert 'live_telemetry_without_proof_chain' in HARD_GUARD_FLAGS


def test_A_live_telemetry_only_does_not_yield_live_status() -> None:
    summary = _build(detections_count=0)
    assert summary['monitoring_status'] != 'live', (
        f"Expected non-live but got {summary['monitoring_status']}; "
        f"guard_flags={summary['guard_flags']}"
    )


def test_A_guard_flag_present_in_guard_flags_list() -> None:
    summary = _build(detections_count=0)
    assert 'live_telemetry_without_proof_chain' in summary['guard_flags']


# ──────────────────────────────────────────────────────────────────────────────
# B. Partial chain (detection exists, but no alert/incident) → not LIVE
# ──────────────────────────────────────────────────────────────────────────────

def test_B_detection_without_alert_does_not_yield_live_status() -> None:
    now = _now()
    summary = _build(
        detections_count=1,
        last_detection_at=now - timedelta(seconds=10),
        active_alerts_count=0,
        active_incidents_count=0,
    )
    assert summary['monitoring_status'] != 'live', (
        f"Expected non-live; guard_flags={summary['guard_flags']}"
    )


def test_B_proof_chain_incomplete_guard_fires_when_detection_without_alert() -> None:
    now = _now()
    summary = _build(
        detections_count=1,
        last_detection_at=now - timedelta(seconds=10),
        active_alerts_count=0,
        active_incidents_count=0,
    )
    assert 'live_proof_chain_incomplete' in summary['contradiction_flags']


def test_B_telemetry_guard_absent_when_detection_exists() -> None:
    now = _now()
    summary = _build(
        detections_count=1,
        last_detection_at=now - timedelta(seconds=10),
        active_alerts_count=0,
        active_incidents_count=0,
    )
    assert 'live_telemetry_without_proof_chain' not in summary['contradiction_flags']


def test_B_proof_chain_incomplete_is_in_hard_guard_flags() -> None:
    assert 'live_proof_chain_incomplete' in HARD_GUARD_FLAGS
    assert 'live_proof_chain_incomplete' in HARD_GUARD_PRIORITY


def test_B_incident_without_alert_fires_contradiction() -> None:
    now = _now()
    summary = _build(
        detections_count=1,
        last_detection_at=now - timedelta(seconds=10),
        active_alerts_count=0,
        active_incidents_count=1,
    )
    assert 'incident_exists_without_alert' in summary['contradiction_flags']


# ──────────────────────────────────────────────────────────────────────────────
# C. Full proof chain → monitoring_status == 'live'
# ──────────────────────────────────────────────────────────────────────────────

def test_C_full_proof_chain_yields_live_status() -> None:
    now = _now()
    summary = _build(
        detections_count=1,
        last_detection_at=now - timedelta(seconds=10),
        active_alerts_count=1,
        active_incidents_count=1,
        response_actions_count=1,
        evidence_packages_count=1,
        last_alert_at=now - timedelta(seconds=9),
        last_incident_at=now - timedelta(seconds=8),
        last_response_action_at=now - timedelta(seconds=7),
        last_evidence_export_at=now - timedelta(seconds=6),
        telemetry_kind='coverage',
    )
    guard = summary['guard_flags']
    assert summary['monitoring_status'] == 'live', (
        f"Expected live but got {summary['monitoring_status']}; guard_flags={guard}"
    )


def test_C_full_proof_chain_has_no_hard_guard_flags() -> None:
    now = _now()
    summary = _build(
        detections_count=1,
        last_detection_at=now - timedelta(seconds=10),
        active_alerts_count=1,
        active_incidents_count=1,
        response_actions_count=1,
        evidence_packages_count=1,
        last_alert_at=now - timedelta(seconds=9),
        last_incident_at=now - timedelta(seconds=8),
        last_response_action_at=now - timedelta(seconds=7),
        last_evidence_export_at=now - timedelta(seconds=6),
        telemetry_kind='coverage',
    )
    assert summary['guard_flags'] == [], f"Unexpected guard flags: {summary['guard_flags']}"


def test_C_full_proof_chain_no_proof_chain_guard() -> None:
    now = _now()
    summary = _build(
        detections_count=1,
        last_detection_at=now - timedelta(seconds=10),
        active_alerts_count=1,
        active_incidents_count=1,
        telemetry_kind='coverage',
    )
    assert 'live_telemetry_without_proof_chain' not in summary['contradiction_flags']


# ──────────────────────────────────────────────────────────────────────────────
# D. Launch proof strictness: summary fields require full chain
# ──────────────────────────────────────────────────────────────────────────────

def test_D_proof_chain_guard_not_fired_when_no_reporting_systems() -> None:
    # Guard requires reporting_systems > 0 (live telemetry present). Without
    # reporting systems the workspace isn't even in the live coverage window.
    summary = _build(
        reporting_systems=0,
        detections_count=0,
        telemetry_kind='coverage',
    )
    assert 'live_telemetry_without_proof_chain' not in summary['contradiction_flags']


def test_D_proof_chain_guard_not_fired_when_telemetry_stale() -> None:
    now = _now()
    summary = _build(
        detections_count=0,
        last_telemetry_at=now - timedelta(seconds=3600),
        last_coverage_telemetry_at=now - timedelta(seconds=3600),
        telemetry_window_seconds=300,
    )
    assert 'live_telemetry_without_proof_chain' not in summary['contradiction_flags']


def test_D_proof_chain_guard_not_fired_for_simulator_evidence() -> None:
    summary = _build(
        detections_count=0,
        evidence_source='simulator',
    )
    assert 'live_telemetry_without_proof_chain' not in summary['contradiction_flags']


def test_D_proof_chain_guard_not_fired_when_evidence_source_none() -> None:
    summary = _build(
        detections_count=0,
        evidence_source='none',
    )
    assert 'live_telemetry_without_proof_chain' not in summary['contradiction_flags']


# ──────────────────────────────────────────────────────────────────────────────
# E. _build_summary helper: detections_count=0 default doesn't break old callers
# ──────────────────────────────────────────────────────────────────────────────

def test_E_build_workspace_monitoring_summary_accepts_detections_count_param() -> None:
    now = _now()
    result = build_workspace_monitoring_summary(
        now=now,
        workspace_configured=True,
        configuration_reason_codes=None,
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
        last_detection_at=None,
        evidence_source='live',
        status_reason=None,
        configuration_reason=None,
        valid_protected_asset_count=1,
        linked_monitored_system_count=1,
        persisted_enabled_config_count=1,
        valid_target_system_link_count=1,
        telemetry_window_seconds=300,
        detections_count=0,
    )
    assert isinstance(result, dict)
    assert 'monitoring_status' in result


def test_E_omitting_detections_count_does_not_fire_proof_chain_guard() -> None:
    # When detections_count is omitted (default None), the proof chain guard
    # must not fire — this preserves backward compatibility for existing callers.
    now = _now()
    result = build_workspace_monitoring_summary(
        now=now,
        workspace_configured=True,
        configuration_reason_codes=None,
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
        last_detection_at=None,
        evidence_source='live',
        status_reason=None,
        configuration_reason=None,
        valid_protected_asset_count=1,
        linked_monitored_system_count=1,
        persisted_enabled_config_count=1,
        valid_target_system_link_count=1,
        telemetry_window_seconds=300,
    )
    assert 'live_telemetry_without_proof_chain' not in result['contradiction_flags']
    assert 'live_proof_chain_incomplete' not in result['contradiction_flags']


# ──────────────────────────────────────────────────────────────────────────────
# F. UI status: monitoring_status == 'live' only after full proof chain
# ──────────────────────────────────────────────────────────────────────────────

def test_F_no_chain_yields_non_live_status() -> None:
    summary = _build(
        detections_count=0,
        active_alerts_count=0,
        active_incidents_count=0,
        response_actions_count=0,
        evidence_packages_count=0,
    )
    assert summary['monitoring_status'] != 'live'


def test_F_status_transitions_to_live_only_when_full_chain_present() -> None:
    now = _now()
    before_chain = _build(detections_count=0)
    after_chain = _build(
        detections_count=1,
        last_detection_at=now - timedelta(seconds=10),
        active_alerts_count=1,
        active_incidents_count=1,
        response_actions_count=1,
        evidence_packages_count=1,
        last_alert_at=now - timedelta(seconds=9),
        last_incident_at=now - timedelta(seconds=8),
        last_response_action_at=now - timedelta(seconds=7),
        last_evidence_export_at=now - timedelta(seconds=6),
        telemetry_kind='coverage',
    )
    assert before_chain['monitoring_status'] != 'live'
    assert after_chain['monitoring_status'] == 'live'


def test_F_proof_chain_guard_flag_in_hard_guard_set() -> None:
    assert 'live_telemetry_without_proof_chain' in HARD_GUARD_FLAGS
