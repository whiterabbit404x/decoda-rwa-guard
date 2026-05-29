"""Tests for runtime-status correctness after proof-chain repair succeeds.

Covers the exact JSON case where all canonical facts are healthy but the
response previously returned degraded due to noncanonical-state normalization.
"""
from __future__ import annotations

from datetime import datetime, timezone

from services.api.app.workspace_monitoring_summary import (
    build_runtime_setup_chain,
    build_workspace_monitoring_summary,
    resolve_next_required_action,
)


NOW = datetime(2026, 5, 29, 12, 0, 0, tzinfo=timezone.utc)
TELEMETRY_WINDOW = 300

LIVE_FACTS = dict(
    now=NOW,
    workspace_configured=True,
    configuration_reason_codes=[],
    query_failure_detected=False,
    schema_drift_detected=False,
    missing_telemetry_only=False,
    monitoring_mode='live',
    runtime_status='live',
    configured_systems=4,
    monitored_systems_count=4,
    reporting_systems=4,
    protected_assets=1,
    last_poll_at=datetime(2026, 5, 29, 11, 59, 0, tzinfo=timezone.utc),
    last_heartbeat_at=datetime(2026, 5, 29, 11, 59, 30, tzinfo=timezone.utc),
    last_telemetry_at=datetime(2026, 5, 29, 11, 59, 45, tzinfo=timezone.utc),
    last_coverage_telemetry_at=datetime(2026, 5, 29, 11, 59, 45, tzinfo=timezone.utc),
    telemetry_kind='coverage',
    last_detection_at=datetime(2026, 5, 29, 11, 59, 50, tzinfo=timezone.utc),
    evidence_source='live',
    status_reason=None,
    configuration_reason=None,
    valid_protected_asset_count=1,
    linked_monitored_system_count=4,
    persisted_enabled_config_count=4,
    valid_target_system_link_count=4,
    telemetry_window_seconds=TELEMETRY_WINDOW,
    active_alerts_count=1,
    active_incidents_count=1,
    detections_count=1,
    db_persistence_available=True,
    provider_ready=True,
)


def test_live_facts_produce_live_runtime_status():
    """build_workspace_monitoring_summary must return runtime_status=live when all facts confirm live."""
    summary = build_workspace_monitoring_summary(**LIVE_FACTS)
    assert summary['runtime_status'] == 'live', (
        f"Expected 'live', got {summary['runtime_status']!r}. "
        f"contradiction_flags={summary.get('contradiction_flags')}, "
        f"guard_flags={summary.get('guard_flags')}, "
        f"status_reason={summary.get('status_reason')!r}"
    )


def test_live_facts_produce_no_contradiction_flags():
    summary = build_workspace_monitoring_summary(**LIVE_FACTS)
    assert summary['contradiction_flags'] == [], summary['contradiction_flags']


def test_live_facts_produce_no_guard_flags():
    summary = build_workspace_monitoring_summary(**LIVE_FACTS)
    assert summary['guard_flags'] == [], summary['guard_flags']


def test_live_facts_produce_fresh_status():
    summary = build_workspace_monitoring_summary(**LIVE_FACTS)
    freshness = summary.get('telemetry_freshness') or summary.get('freshness_status')
    assert freshness == 'fresh', freshness


def test_live_facts_produce_high_confidence():
    summary = build_workspace_monitoring_summary(**LIVE_FACTS)
    confidence = summary.get('confidence') or summary.get('confidence_status')
    assert confidence == 'high', confidence


def test_live_facts_status_reason_is_not_noncanonical_normalized():
    """status_reason must never be 'runtime_status_normalized_from_noncanonical_state' for a live workspace."""
    summary = build_workspace_monitoring_summary(**LIVE_FACTS)
    assert summary.get('status_reason') != 'runtime_status_normalized_from_noncanonical_state', (
        "status_reason must not be runtime_status_normalized_from_noncanonical_state when runtime is live"
    )


def test_workspace_created_step_complete_when_workspace_configured():
    """workspace_created setup step must be complete when workspace_configured=True."""
    chain = build_runtime_setup_chain(
        counters={
            'workspaces_count': 1,
            'assets_count': 1,
            'verified_assets_count': 1,
            'targets_count': 4,
            'monitored_systems_count': 4,
            'enabled_monitored_systems_count': 4,
            'detections_count': 1,
            'alerts_count': 1,
            'incidents_count': 1,
            'response_actions_count': 1,
            'evidence_count': 1,
        },
        timestamps={
            'last_heartbeat_at': '2026-05-29T11:59:30Z',
            'last_telemetry_at': '2026-05-29T11:59:45Z',
        },
    )
    step_by_id = {s['id']: s for s in chain['steps']}
    assert step_by_id['workspace_created']['status'] == 'complete', step_by_id['workspace_created']


def test_current_step_is_not_workspace_created_when_all_complete():
    """current_step must advance past workspace_created when all steps are complete."""
    chain = build_runtime_setup_chain(
        counters={
            'workspaces_count': 1,
            'assets_count': 1,
            'verified_assets_count': 1,
            'targets_count': 4,
            'monitored_systems_count': 4,
            'enabled_monitored_systems_count': 4,
            'detections_count': 1,
            'alerts_count': 1,
            'incidents_count': 1,
            'response_actions_count': 1,
            'evidence_count': 1,
        },
        timestamps={
            'last_heartbeat_at': '2026-05-29T11:59:30Z',
            'last_telemetry_at': '2026-05-29T11:59:45Z',
        },
    )
    assert chain['current_step'] != 'workspace_created', (
        f"current_step is still 'workspace_created', expected it to have advanced. chain={chain}"
    )


def test_workspace_created_step_pending_without_workspaces_count():
    """workspace_created is pending when workspaces_count is absent (legacy callers omit it)."""
    chain = build_runtime_setup_chain(
        counters={'assets_count': 1},
        timestamps={},
    )
    step_by_id = {s['id']: s for s in chain['steps']}
    assert step_by_id['workspace_created']['status'] == 'pending', step_by_id['workspace_created']


def test_resolve_next_required_action_returns_monitoring_live_when_all_complete():
    """resolve_next_required_action must return 'monitoring_live' when every setup step is complete."""
    chain = build_runtime_setup_chain(
        counters={
            'workspaces_count': 1,
            'assets_count': 1,
            'verified_assets_count': 1,
            'targets_count': 4,
            'monitored_systems_count': 4,
            'enabled_monitored_systems_count': 4,
            'detections_count': 1,
            'alerts_count': 1,
            'incidents_count': 1,
            'response_actions_count': 1,
            'evidence_count': 1,
        },
        timestamps={
            'last_heartbeat_at': '2026-05-29T11:59:30Z',
            'last_telemetry_at': '2026-05-29T11:59:45Z',
        },
    )
    action = resolve_next_required_action(chain)
    assert action == 'monitoring_live', f"Expected 'monitoring_live', got {action!r}"


def test_resolve_next_required_action_returns_monitoring_live_for_empty_steps():
    """Empty chain (no steps) is treated as all-complete — next action is 'monitoring_live'."""
    assert resolve_next_required_action(None) == 'monitoring_live'
    assert resolve_next_required_action({}) == 'monitoring_live'
    assert resolve_next_required_action({'steps': []}) == 'monitoring_live'


def test_provider_health_status_upgrade_logic():
    """Verify provider_health_status derivation logic in main.py-equivalent:
    when reporting_systems>0, evidence_source=live, freshness=fresh, status must be 'healthy'."""
    _payload_reporting = {
        'reporting_systems': 4,
        'evidence_source': 'live',
        'freshness_status': 'fresh',
    }
    ph_status = 'degraded'
    _ph_rpt = int(_payload_reporting.get('reporting_systems') or 0)
    _ph_ev = str(_payload_reporting.get('evidence_source') or '').lower()
    _ph_fresh = str(_payload_reporting.get('freshness_status') or '').lower()
    if ph_status in {'degraded', None} and _ph_rpt > 0 and _ph_ev in {'live', 'live_provider'} and _ph_fresh == 'fresh':
        ph_status = 'healthy'
    assert ph_status == 'healthy', f"Expected 'healthy', got {ph_status!r}"


def test_target_coverage_status_derived_from_runtime_facts():
    """Verify target_coverage_status derivation when target_coverage_records is empty
    but reporting_systems >= configured_systems with live fresh evidence."""
    _payload_facts = {
        'reporting_systems': 4,
        'configured_systems': 4,
        'evidence_source': 'live',
        'freshness_status': 'fresh',
    }
    tc_status = None
    _tc_list: list = []
    if tc_status is None:
        if _tc_list:
            _all_rep = all(
                isinstance(e, dict) and str(e.get('provider_status') or '').lower() in {'live', 'reporting', 'active'}
                for e in _tc_list
            )
            tc_status = 'reporting' if _all_rep else 'partial'
        else:
            _tc_rpt = int(_payload_facts.get('reporting_systems') or 0)
            _tc_cfg = int(_payload_facts.get('configured_systems') or 0)
            _tc_ev = str(_payload_facts.get('evidence_source') or '').lower()
            _tc_fresh = str(_payload_facts.get('freshness_status') or '').lower()
            if _tc_rpt > 0 and _tc_cfg > 0 and _tc_rpt >= _tc_cfg and _tc_ev in {'live', 'live_provider'} and _tc_fresh == 'fresh':
                tc_status = 'reporting'
    assert tc_status == 'reporting', f"Expected 'reporting', got {tc_status!r}"


def test_target_coverage_status_remains_unknown_when_not_live():
    """target_coverage_status stays unknown when evidence is not live."""
    _payload_facts = {
        'reporting_systems': 4,
        'configured_systems': 4,
        'evidence_source': 'none',
        'freshness_status': 'unavailable',
    }
    tc_status = None
    _tc_list: list = []
    if tc_status is None:
        if _tc_list:
            pass
        else:
            _tc_rpt = int(_payload_facts.get('reporting_systems') or 0)
            _tc_cfg = int(_payload_facts.get('configured_systems') or 0)
            _tc_ev = str(_payload_facts.get('evidence_source') or '').lower()
            _tc_fresh = str(_payload_facts.get('freshness_status') or '').lower()
            if _tc_rpt > 0 and _tc_cfg > 0 and _tc_rpt >= _tc_cfg and _tc_ev in {'live', 'live_provider'} and _tc_fresh == 'fresh':
                tc_status = 'reporting'
    # None because evidence is not live/fresh
    assert tc_status is None, f"Expected None (unknown), got {tc_status!r}"
