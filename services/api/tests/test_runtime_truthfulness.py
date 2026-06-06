"""
Session 13 — Runtime Truthfulness and Contradiction Guards

Tests A-T as specified in the session prompt.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.api.app.runtime_truthfulness import (
    FRESHNESS_THRESHOLDS_SECONDS,
    build_signal_freshness,
    compute_signal_freshness,
    detect_runtime_contradictions,
    derive_confidence_status,
    derive_runtime_status,
)
from services.api.app.workspace_monitoring_summary import build_workspace_monitoring_summary

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

NOW = datetime(2026, 5, 22, 12, 0, tzinfo=timezone.utc)
RECENT = NOW - timedelta(seconds=60)
STALE_HB = NOW - timedelta(seconds=400)      # > heartbeat threshold (300s)
STALE_TEL = NOW - timedelta(seconds=1000)    # > telemetry threshold (900s)
STALE_DETECT = NOW - timedelta(seconds=2000) # > detection threshold (1800s)


def _base_summary(**overrides):
    """Return build_workspace_monitoring_summary with a valid healthy baseline."""
    params = dict(
        now=NOW,
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
        last_poll_at=RECENT,
        last_heartbeat_at=RECENT,
        last_telemetry_at=RECENT,
        last_coverage_telemetry_at=RECENT,
        telemetry_kind='coverage',
        last_detection_at=RECENT,
        evidence_source='live',
        status_reason=None,
        configuration_reason=None,
        valid_protected_asset_count=1,
        linked_monitored_system_count=1,
        persisted_enabled_config_count=1,
        valid_target_system_link_count=1,
        telemetry_window_seconds=300,
    )
    params.update(overrides)
    return build_workspace_monitoring_summary(**params)


# ---------------------------------------------------------------------------
# A. Heartbeat without telemetry does not mark telemetry current
# ---------------------------------------------------------------------------

def test_A_heartbeat_without_telemetry_does_not_mark_telemetry_current():
    sf = build_signal_freshness(
        last_heartbeat_at=RECENT,
        last_poll_at=None,
        last_telemetry_at=None,
        last_detection_at=None,
        last_alert_at=None,
        last_incident_at=None,
        last_response_action_at=None,
        last_evidence_export_at=None,
        now=NOW,
    )
    assert sf['heartbeat'] == 'current'
    assert sf['telemetry'] == 'unavailable', (
        "Heartbeat must not infer telemetry freshness"
    )


# ---------------------------------------------------------------------------
# B. Poll without telemetry does not mark telemetry current
# ---------------------------------------------------------------------------

def test_B_poll_without_telemetry_does_not_mark_telemetry_current():
    sf = build_signal_freshness(
        last_heartbeat_at=None,
        last_poll_at=RECENT,
        last_telemetry_at=None,
        last_detection_at=None,
        last_alert_at=None,
        last_incident_at=None,
        last_response_action_at=None,
        last_evidence_export_at=None,
        now=NOW,
    )
    assert sf['poll'] == 'current'
    assert sf['telemetry'] == 'unavailable', (
        "Poll must not infer telemetry freshness"
    )


# ---------------------------------------------------------------------------
# C. reporting_systems == 0 prevents healthy runtime
# ---------------------------------------------------------------------------

def test_C_reporting_systems_zero_prevents_healthy_runtime():
    flags = detect_runtime_contradictions(
        runtime_status='healthy',
        freshness_status='current',
        reporting_systems=0,
        configured_systems=1,
        protected_assets=1,
        provider_ready=True,
        last_telemetry_at=RECENT,
    )
    assert 'healthy_without_reporting_systems' in flags

    derived = derive_runtime_status(
        contradiction_flags=flags,
        reporting_systems=0,
        last_telemetry_at=RECENT,
        workspace_configured=True,
        raw_runtime_status='healthy',
    )
    assert derived != 'healthy', "Runtime must not be healthy with zero reporting systems"


# ---------------------------------------------------------------------------
# D. current freshness without telemetry creates current_without_telemetry flag
# ---------------------------------------------------------------------------

def test_D_current_freshness_without_telemetry_creates_flag():
    flags = detect_runtime_contradictions(
        runtime_status='live',
        freshness_status='current',
        last_telemetry_at=None,
        reporting_systems=1,
        configured_systems=1,
        protected_assets=1,
        provider_ready=True,
    )
    assert 'current_without_telemetry' in flags


# ---------------------------------------------------------------------------
# E. offline with current telemetry creates offline_with_current_telemetry flag
# ---------------------------------------------------------------------------

def test_E_offline_with_current_telemetry_creates_flag():
    sf = build_signal_freshness(
        last_telemetry_at=RECENT,
        now=NOW,
    )
    flags = detect_runtime_contradictions(
        runtime_status='offline',
        freshness_status='unavailable',
        last_telemetry_at=RECENT,
        signal_freshness=sf,
        reporting_systems=1,
        configured_systems=1,
        protected_assets=1,
        provider_ready=True,
    )
    assert 'offline_with_current_telemetry' in flags


# ---------------------------------------------------------------------------
# F. live mode with simulator evidence creates live_mode_with_simulator_evidence flag
# ---------------------------------------------------------------------------

def test_F_live_mode_simulator_evidence_creates_flag():
    flags = detect_runtime_contradictions(
        runtime_status='live',
        freshness_status='current',
        monitoring_mode='live',
        evidence_source='simulator',
        reporting_systems=1,
        configured_systems=1,
        protected_assets=1,
        provider_ready=True,
        last_telemetry_at=RECENT,
    )
    assert 'live_mode_with_simulator_evidence' in flags


# ---------------------------------------------------------------------------
# G. live_provider evidence without provider readiness creates flag
# ---------------------------------------------------------------------------

def test_G_live_evidence_without_provider_ready_creates_flag():
    flags = detect_runtime_contradictions(
        runtime_status='live',
        freshness_status='current',
        monitoring_mode='live',
        evidence_source='live_provider',
        reporting_systems=1,
        configured_systems=1,
        protected_assets=1,
        provider_ready=False,
        last_telemetry_at=RECENT,
    )
    assert 'live_evidence_without_provider_ready' in flags


def test_G_live_evidence_with_provider_ready_no_flag():
    flags = detect_runtime_contradictions(
        runtime_status='live',
        freshness_status='current',
        monitoring_mode='live',
        evidence_source='live_provider',
        reporting_systems=1,
        configured_systems=1,
        protected_assets=1,
        provider_ready=True,
        last_telemetry_at=RECENT,
    )
    assert 'live_evidence_without_provider_ready' not in flags


# ---------------------------------------------------------------------------
# H. systems_without_protected_assets is flagged
# ---------------------------------------------------------------------------

def test_H_systems_without_protected_assets_flagged():
    flags = detect_runtime_contradictions(
        runtime_status='live',
        freshness_status='current',
        monitoring_mode='live',
        evidence_source='live_provider',
        reporting_systems=1,
        configured_systems=1,
        protected_assets=0,
        provider_ready=True,
        last_telemetry_at=RECENT,
    )
    assert 'systems_without_protected_assets' in flags


def test_H_systems_with_protected_assets_not_flagged():
    flags = detect_runtime_contradictions(
        runtime_status='live',
        freshness_status='current',
        monitoring_mode='live',
        evidence_source='live_provider',
        reporting_systems=1,
        configured_systems=1,
        protected_assets=1,
        provider_ready=True,
        last_telemetry_at=RECENT,
    )
    assert 'systems_without_protected_assets' not in flags


# ---------------------------------------------------------------------------
# I. reporting_exceeds_configured is flagged
# ---------------------------------------------------------------------------

def test_I_reporting_exceeds_configured_flagged():
    flags = detect_runtime_contradictions(
        runtime_status='live',
        freshness_status='current',
        configured_systems=1,
        reporting_systems=3,
        protected_assets=1,
        provider_ready=True,
        last_telemetry_at=RECENT,
    )
    assert 'reporting_exceeds_configured' in flags


def test_I_reporting_equal_configured_not_flagged():
    flags = detect_runtime_contradictions(
        runtime_status='live',
        freshness_status='current',
        configured_systems=2,
        reporting_systems=2,
        protected_assets=1,
        provider_ready=True,
        last_telemetry_at=RECENT,
    )
    assert 'reporting_exceeds_configured' not in flags


# ---------------------------------------------------------------------------
# J. detection_without_telemetry is flagged
# ---------------------------------------------------------------------------

def test_J_detection_without_telemetry_flagged():
    flags = detect_runtime_contradictions(
        runtime_status='live',
        freshness_status='unavailable',
        configured_systems=1,
        reporting_systems=1,
        protected_assets=1,
        provider_ready=True,
        last_telemetry_at=None,
        last_detection_at=RECENT,
    )
    assert 'detection_without_telemetry' in flags


def test_J_detection_with_telemetry_not_flagged():
    flags = detect_runtime_contradictions(
        runtime_status='live',
        freshness_status='current',
        configured_systems=1,
        reporting_systems=1,
        protected_assets=1,
        provider_ready=True,
        last_telemetry_at=RECENT,
        last_detection_at=RECENT,
    )
    assert 'detection_without_telemetry' not in flags


# ---------------------------------------------------------------------------
# K. alert_without_detection is flagged
# ---------------------------------------------------------------------------

def test_K_alert_without_detection_flagged():
    flags = detect_runtime_contradictions(
        runtime_status='live',
        freshness_status='current',
        configured_systems=1,
        reporting_systems=1,
        protected_assets=1,
        provider_ready=True,
        last_telemetry_at=RECENT,
        last_detection_at=None,
        last_alert_at=RECENT,
    )
    assert 'alert_without_detection' in flags


def test_K_alert_with_detection_not_flagged():
    flags = detect_runtime_contradictions(
        runtime_status='live',
        freshness_status='current',
        configured_systems=1,
        reporting_systems=1,
        protected_assets=1,
        provider_ready=True,
        last_telemetry_at=RECENT,
        last_detection_at=RECENT,
        last_alert_at=RECENT,
    )
    assert 'alert_without_detection' not in flags


# ---------------------------------------------------------------------------
# L. incident_without_alert is flagged
# ---------------------------------------------------------------------------

def test_L_incident_without_alert_flagged():
    flags = detect_runtime_contradictions(
        runtime_status='live',
        freshness_status='current',
        configured_systems=1,
        reporting_systems=1,
        protected_assets=1,
        provider_ready=True,
        last_telemetry_at=RECENT,
        last_detection_at=RECENT,
        last_alert_at=None,
        last_incident_at=RECENT,
    )
    assert 'incident_without_alert' in flags


def test_L_incident_with_alert_not_flagged():
    flags = detect_runtime_contradictions(
        runtime_status='live',
        freshness_status='current',
        configured_systems=1,
        reporting_systems=1,
        protected_assets=1,
        provider_ready=True,
        last_telemetry_at=RECENT,
        last_detection_at=RECENT,
        last_alert_at=RECENT,
        last_incident_at=RECENT,
    )
    assert 'incident_without_alert' not in flags


# ---------------------------------------------------------------------------
# M. response_action_without_case is flagged
# ---------------------------------------------------------------------------

def test_M_response_action_without_case_flagged():
    flags = detect_runtime_contradictions(
        runtime_status='live',
        freshness_status='current',
        configured_systems=1,
        reporting_systems=1,
        protected_assets=1,
        provider_ready=True,
        last_telemetry_at=RECENT,
        last_detection_at=RECENT,
        last_alert_at=None,
        last_incident_at=None,
        last_response_action_at=RECENT,
    )
    assert 'response_action_without_case' in flags


def test_M_response_action_with_incident_not_flagged():
    flags = detect_runtime_contradictions(
        runtime_status='live',
        freshness_status='current',
        configured_systems=1,
        reporting_systems=1,
        protected_assets=1,
        provider_ready=True,
        last_telemetry_at=RECENT,
        last_detection_at=RECENT,
        last_alert_at=RECENT,
        last_incident_at=RECENT,
        last_response_action_at=RECENT,
    )
    assert 'response_action_without_case' not in flags


# ---------------------------------------------------------------------------
# N. evidence_export_without_source_truthfulness is flagged
# ---------------------------------------------------------------------------

def test_N_evidence_export_without_source_truthfulness_flagged():
    for unknown_src in ('none', 'unknown', 'unavailable', ''):
        flags = detect_runtime_contradictions(
            runtime_status='live',
            freshness_status='current',
            evidence_source=unknown_src,
            configured_systems=1,
            reporting_systems=1,
            protected_assets=1,
            provider_ready=True,
            last_telemetry_at=RECENT,
            last_evidence_export_at=RECENT,
        )
        assert 'evidence_export_without_source_truthfulness' in flags, (
            f"Expected flag for evidence_source={unknown_src!r}"
        )


def test_N_evidence_export_with_known_source_not_flagged():
    flags = detect_runtime_contradictions(
        runtime_status='live',
        freshness_status='current',
        evidence_source='live_provider',
        configured_systems=1,
        reporting_systems=1,
        protected_assets=1,
        provider_ready=True,
        last_telemetry_at=RECENT,
        last_evidence_export_at=RECENT,
    )
    assert 'evidence_export_without_source_truthfulness' not in flags


# ---------------------------------------------------------------------------
# O. contradiction_flags prevent healthy runtime
# ---------------------------------------------------------------------------

def test_O_contradiction_flags_prevent_healthy_runtime():
    flags = ['healthy_without_reporting_systems']
    derived = derive_runtime_status(
        contradiction_flags=flags,
        reporting_systems=0,
        last_telemetry_at=None,
        workspace_configured=True,
        raw_runtime_status='healthy',
    )
    assert derived != 'healthy'

    flags2 = ['some_contradiction']
    derived2 = derive_runtime_status(
        contradiction_flags=flags2,
        reporting_systems=1,
        last_telemetry_at=RECENT,
        workspace_configured=True,
        raw_runtime_status='live',
    )
    assert derived2 != 'healthy'


# ---------------------------------------------------------------------------
# P. Invalid timestamp does not crash and becomes 'unknown'
# ---------------------------------------------------------------------------

def test_P_invalid_timestamp_returns_unknown_not_crash():
    result = compute_signal_freshness('not-a-valid-iso-timestamp', NOW, 300)
    assert result == 'unknown'

    result2 = compute_signal_freshness(12345, NOW, 300)
    assert result2 == 'unknown'

    result3 = compute_signal_freshness(object(), NOW, 300)
    assert result3 == 'unknown'


def test_P_invalid_timestamp_in_build_signal_freshness_does_not_crash():
    sf = build_signal_freshness(
        last_heartbeat_at='bad-timestamp',
        last_telemetry_at=None,
        now=NOW,
    )
    assert sf['heartbeat'] == 'unknown'
    assert sf['telemetry'] == 'unavailable'


# ---------------------------------------------------------------------------
# Q. Stale telemetry produces freshness_status stale, not current
# ---------------------------------------------------------------------------

def test_Q_stale_telemetry_not_current():
    sf = build_signal_freshness(
        last_telemetry_at=STALE_TEL,
        now=NOW,
    )
    assert sf['telemetry'] == 'stale', (
        f"Expected stale, got {sf['telemetry']}"
    )
    assert sf['telemetry'] != 'current'


def test_Q_stale_detection_not_current():
    sf = build_signal_freshness(
        last_detection_at=STALE_DETECT,
        now=NOW,
    )
    assert sf['detection'] == 'stale'


# ---------------------------------------------------------------------------
# R. Simulator evidence does not satisfy paid-launch readiness
# ---------------------------------------------------------------------------

def test_R_simulator_evidence_does_not_satisfy_paid_launch():
    from services.api.app.paid_launch_readiness import build_paid_launch_readiness
    result = build_paid_launch_readiness(live_evidence={
        'evidence_source': 'simulator',
        'telemetry_evidence_source': 'simulator',
    })
    assert result.get('paid_launch_ready') is not True, (
        "Simulator evidence must not satisfy paid launch readiness"
    )


# ---------------------------------------------------------------------------
# S. API response preserves existing fields (backward compat)
# ---------------------------------------------------------------------------

def test_S_build_workspace_monitoring_summary_preserves_existing_fields():
    summary = _base_summary()
    # Use canonical field names as returned by build_workspace_monitoring_summary
    required_fields = {
        'workspace_configured',
        'runtime_status',
        'monitoring_status',
        'last_poll_at',
        'last_heartbeat_at',
        'last_telemetry_at',
        'last_detection_at',
        'contradiction_flags',
        'guard_flags',
        'status_reason',
        'evidence_source_summary',
        'telemetry_freshness',   # canonical key (not 'freshness_status')
        'confidence',            # canonical key (not 'confidence_status')
        'reporting_systems_count',  # canonical key (not 'reporting_systems')
        'protected_assets_count',   # canonical key (not 'protected_assets')
    }
    for field in required_fields:
        assert field in summary, f"Missing required field: {field}"


def test_S_new_signal_freshness_field_present():
    summary = _base_summary()
    assert 'signal_freshness' in summary
    sf = summary['signal_freshness']
    assert isinstance(sf, dict)
    for signal in ('heartbeat', 'poll', 'telemetry', 'detection'):
        assert signal in sf


def test_S_new_timestamp_fields_present():
    ts = NOW - timedelta(seconds=10)
    summary = _base_summary(
        last_alert_at=ts,
        last_incident_at=ts,
        last_response_action_at=ts,
        last_evidence_export_at=ts,
    )
    assert summary.get('last_alert_at') is not None
    assert summary.get('last_incident_at') is not None
    assert summary.get('last_response_action_at') is not None
    assert summary.get('last_evidence_export_at') is not None


# ---------------------------------------------------------------------------
# T. Workspace-specific runtime summary does not leak from another workspace
# ---------------------------------------------------------------------------

def test_T_workspace_summary_is_independent():
    summary_ws1 = _base_summary(
        last_telemetry_at=RECENT,
        reporting_systems=1,
        protected_assets=2,
    )
    summary_ws2 = _base_summary(
        last_telemetry_at=None,
        last_coverage_telemetry_at=None,
        telemetry_kind=None,
        reporting_systems=0,
        protected_assets=0,
        workspace_configured=False,
    )
    # Use canonical key names: reporting_systems_count, protected_assets_count
    assert summary_ws1['reporting_systems_count'] != summary_ws2['reporting_systems_count'], (
        "Workspace summaries must be independent — no cross-workspace leakage"
    )
    assert summary_ws1['protected_assets_count'] != summary_ws2['protected_assets_count']
    # WS2 should not inherit WS1's telemetry
    assert summary_ws2['last_telemetry_at'] is None
    assert summary_ws2.get('signal_freshness', {}).get('telemetry') == 'unavailable'


# ---------------------------------------------------------------------------
# Additional: freshness thresholds are separately addressable per signal
# ---------------------------------------------------------------------------

def test_freshness_thresholds_are_defined_for_all_signals():
    required_signals = {
        'heartbeat', 'poll', 'telemetry', 'detection',
        'alert', 'incident', 'response_action', 'evidence_export',
    }
    assert required_signals.issubset(FRESHNESS_THRESHOLDS_SECONDS.keys())


def test_compute_signal_freshness_exactly_at_boundary_is_current():
    ts = NOW - timedelta(seconds=FRESHNESS_THRESHOLDS_SECONDS['telemetry'])
    result = compute_signal_freshness(ts, NOW, FRESHNESS_THRESHOLDS_SECONDS['telemetry'])
    assert result == 'current'


def test_compute_signal_freshness_one_second_over_boundary_is_stale():
    ts = NOW - timedelta(seconds=FRESHNESS_THRESHOLDS_SECONDS['telemetry'] + 1)
    result = compute_signal_freshness(ts, NOW, FRESHNESS_THRESHOLDS_SECONDS['telemetry'])
    assert result == 'stale'


def test_null_timestamp_is_unavailable_not_stale():
    result = compute_signal_freshness(None, NOW, 300)
    assert result == 'unavailable'
    assert result != 'stale'


def test_derive_confidence_status_low_when_contradictions_and_known_source():
    result = derive_confidence_status(
        contradiction_flags=['healthy_without_reporting_systems'],
        evidence_source='live_provider',
        signal_freshness={'telemetry': 'current'},
    )
    assert result == 'low'


def test_derive_confidence_status_unavailable_when_contradictions_and_unknown_source():
    result = derive_confidence_status(
        contradiction_flags=['current_without_telemetry'],
        evidence_source='none',
        signal_freshness={'telemetry': 'unavailable'},
    )
    assert result == 'unavailable'


# ---------------------------------------------------------------------------
# New P0-2 tests: derive_confidence_status full range
# ---------------------------------------------------------------------------

def test_derive_confidence_status_unavailable_when_no_live_provider():
    result = derive_confidence_status(
        contradiction_flags=[],
        evidence_source='none',
        signal_freshness={'telemetry': 'current', 'heartbeat': 'current', 'poll': 'current'},
    )
    assert result == 'unavailable', 'No live evidence source must yield unavailable'


def test_derive_confidence_status_unavailable_when_telemetry_missing():
    result = derive_confidence_status(
        contradiction_flags=[],
        evidence_source='live_provider',
        signal_freshness={'telemetry': 'unavailable'},
    )
    assert result == 'unavailable'


def test_derive_confidence_status_unavailable_when_telemetry_unknown():
    result = derive_confidence_status(
        contradiction_flags=[],
        evidence_source='live_provider',
        signal_freshness={'telemetry': 'unknown'},
    )
    assert result == 'unavailable'


def test_derive_confidence_status_low_when_telemetry_stale():
    result = derive_confidence_status(
        contradiction_flags=[],
        evidence_source='live_provider',
        signal_freshness={'telemetry': 'stale', 'heartbeat': 'current', 'poll': 'current'},
    )
    assert result == 'low', 'Stale telemetry must yield low confidence'


def test_derive_confidence_status_high_when_all_current():
    result = derive_confidence_status(
        contradiction_flags=[],
        evidence_source='live_provider',
        signal_freshness={'telemetry': 'current', 'heartbeat': 'current', 'poll': 'current'},
    )
    assert result == 'high', 'All signals current must yield high confidence'


def test_derive_confidence_status_medium_when_only_heartbeat_current():
    result = derive_confidence_status(
        contradiction_flags=[],
        evidence_source='live_provider',
        signal_freshness={'telemetry': 'current', 'heartbeat': 'current', 'poll': 'stale'},
    )
    assert result == 'medium', 'Telemetry + heartbeat but stale poll must yield medium'


def test_derive_confidence_status_medium_when_only_poll_current():
    result = derive_confidence_status(
        contradiction_flags=[],
        evidence_source='live_provider',
        signal_freshness={'telemetry': 'current', 'heartbeat': 'stale', 'poll': 'current'},
    )
    assert result == 'medium', 'Telemetry + poll but stale heartbeat must yield medium'


def test_derive_confidence_status_low_when_telemetry_current_but_no_heartbeat_or_poll():
    result = derive_confidence_status(
        contradiction_flags=[],
        evidence_source='live_provider',
        signal_freshness={'telemetry': 'current'},
    )
    assert result == 'low', 'Telemetry current but no heartbeat/poll must yield low'


def test_derive_confidence_status_contradiction_forces_unavailable_for_unknown_source():
    result = derive_confidence_status(
        contradiction_flags=['healthy_without_reporting_systems'],
        evidence_source='unknown',
        signal_freshness={'telemetry': 'current'},
    )
    assert result == 'unavailable'


def test_derive_confidence_status_high_not_returned_with_stale_telemetry():
    result = derive_confidence_status(
        contradiction_flags=[],
        evidence_source='live_provider',
        signal_freshness={'telemetry': 'stale', 'heartbeat': 'current', 'poll': 'current'},
    )
    assert result != 'high', 'Stale telemetry must never yield high confidence'


def test_derive_confidence_status_high_not_returned_with_unknown_source():
    result = derive_confidence_status(
        contradiction_flags=[],
        evidence_source='',
        signal_freshness={'telemetry': 'current', 'heartbeat': 'current', 'poll': 'current'},
    )
    assert result != 'high', 'Unknown evidence source must never yield high confidence'
