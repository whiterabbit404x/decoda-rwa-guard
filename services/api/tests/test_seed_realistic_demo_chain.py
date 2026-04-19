from __future__ import annotations

from services.api.scripts.seed import build_realistic_demo_chain


def test_build_realistic_demo_chain_maps_detection_alert_incident_and_simulated_action() -> None:
    chain = build_realistic_demo_chain(
        {
            'workspace_id': 'ws-1',
            'target_id': 'target-1',
            'monitored_system_id': 'system-1',
            'detection_id': 'det-1',
            'alert_id': 'alert-1',
            'incident_id': 'inc-1',
            'response_action_id': 'act-1',
            'response_action_history_id': 'hist-1',
            'evidence_source': 'simulator',
            'telemetry_event_observed_at': '2026-04-19T00:00:00Z',
        }
    )

    assert chain['chain_summary'] == 'detection → alert → incident → simulated response action'
    assert chain['steps'][0] == {'name': 'detection', 'id': 'det-1', 'status': 'created'}
    assert chain['steps'][1] == {'name': 'alert', 'id': 'alert-1', 'status': 'created_from_detection'}
    assert chain['steps'][2] == {'name': 'incident', 'id': 'inc-1', 'status': 'opened_from_alert'}
    assert chain['steps'][3] == {'name': 'response_action', 'id': 'act-1', 'mode': 'simulated', 'status': 'executed'}
