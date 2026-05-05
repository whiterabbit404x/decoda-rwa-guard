from __future__ import annotations

from datetime import datetime, timezone

from services.api.app.workspace_monitoring_summary import (
    _canonical_summary,
    RUNTIME_SETUP_STEP_ORDER,
    build_runtime_setup_chain,
    build_workspace_monitoring_summary_fallback,
)


def test_runtime_summary_fallback_includes_canonical_chain_metadata() -> None:
    summary = build_workspace_monitoring_summary_fallback(status_reason='acceptance')
    summary_v2 = summary['summary_v2']

    assert summary_v2['current_step'] in RUNTIME_SETUP_STEP_ORDER
    assert isinstance(summary_v2['workflow_steps'], list)
    assert summary_v2['next_required_action']
    assert isinstance(summary_v2['reason_codes'], list)
    assert isinstance(summary_v2['contradiction_flags'], list)
    assert isinstance(summary_v2['counts'], dict)
    assert isinstance(summary_v2['timestamps'], dict)
    assert summary_v2['evidence_source'] in {'live_provider', 'simulator', 'none'}


def test_runtime_setup_chain_order_is_canonical_and_total() -> None:
    chain = build_runtime_setup_chain(
        counters={
            'workspaces_count': 1,
            'assets_count': 1,
            'verified_assets_count': 1,
            'targets_count': 1,
            'monitored_systems_count': 1,
            'enabled_monitored_systems_count': 1,
            'detections_count': 1,
            'alerts_count': 1,
            'incidents_count': 1,
            'response_actions_count': 1,
            'evidence_count': 1,
        },
        timestamps={
            'last_poll_at': datetime.now(timezone.utc).isoformat(),
            'last_heartbeat_at': datetime.now(timezone.utc).isoformat(),
            'last_telemetry_at': datetime.now(timezone.utc).isoformat(),
        },
    )

    steps = chain['steps']
    assert [step['id'] for step in steps] == list(RUNTIME_SETUP_STEP_ORDER)
    assert chain['current_step'] == 'evidence_export_ready'
    assert all(step['status'] == 'complete' for step in steps)


def test_canonical_summary_flags_cross_page_count_mismatch() -> None:
    summary = _canonical_summary(
        {
            'workspace_configured': True,
            'runtime_status': 'live',
            'monitoring_status': 'healthy',
            'protected_assets': 3,
            'protected_assets_count': 1,
            'monitored_systems': 2,
            'monitored_systems_count': 1,
            'reporting_systems': 1,
            'reporting_systems_count': 0,
            'contradiction_flags': [],
            'guard_flags': [],
            'reason_codes': [],
        }
    )
    assert 'cross_page_count_mismatch' in summary['contradiction_flags']
    assert 'cross_page_count_mismatch' in summary['guard_flags']
