from __future__ import annotations

from services.api.scripts.seed import build_realistic_demo_chain


def test_build_realistic_demo_chain_maps_detection_alert_incident_and_simulated_action() -> None:
    chain = build_realistic_demo_chain(
        {
            'workspace_id': 'ws-1',
            'target_id': 'target-1',
            'asset_id': 'asset-1',
            'monitored_system_id': 'system-1',
            'monitoring_config_id': 'cfg-1',
            'monitoring_heartbeat_id': 'hb-1',
            'monitoring_poll_id': 'poll-1',
            'telemetry_event_id': 'tel-1',
            'detection_event_id': 'de-1',
            'detection_id': 'det-1',
            'alert_id': 'alert-1',
            'incident_id': 'inc-1',
            'governance_action_id': 'gov-1',
            'response_action_history_id': 'hist-1',
            'evidence_source': 'simulator',
            'telemetry_event_observed_at': '2026-04-19T00:00:00Z',
        }
    )

    assert chain['chain_summary'] == 'protected_asset → monitored_target → monitoring_config → heartbeat → poll → telemetry(simulator) → detection → alert → incident → governance_action(simulation)'
    assert chain['steps'][0] == {'name': 'protected_asset', 'id': 'asset-1', 'status': 'created_or_reused'}
    assert chain['steps'][1] == {'name': 'monitored_target', 'id': 'target-1', 'status': 'enabled'}
    assert chain['steps'][2] == {'name': 'monitoring_config', 'id': 'cfg-1', 'provider_type': 'simulator', 'status': 'active'}
    assert chain['steps'][3] == {'name': 'heartbeat', 'id': 'hb-1', 'status': 'healthy'}
    assert chain['steps'][4] == {'name': 'poll', 'id': 'poll-1', 'status': 'success'}
    assert chain['steps'][5] == {'name': 'telemetry_event', 'id': 'tel-1', 'evidence_source': 'simulator', 'status': 'observed'}
    assert chain['steps'][9] == {'name': 'governance_action', 'id': 'gov-1', 'action_mode': 'simulation', 'status': 'completed'}
    assert chain['runtime_status_evidence_origin'] == 'simulator'
    assert chain['ui_evidence_origin_label'] == 'Simulator evidence (not live)'
    assert chain['production_claim_eligible'] is False
