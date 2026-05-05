from __future__ import annotations

from services.api.app.workspace_monitoring_summary import _canonical_summary, build_runtime_setup_chain


CHAIN_FIXTURES = [
    ('A', {'assets_count': 1}),
    ('B', {'assets_count': 1, 'verified_assets_count': 1, 'targets_count': 1}),
    ('C', {'assets_count': 1, 'verified_assets_count': 1, 'targets_count': 1, 'monitored_systems_count': 1}),
    ('D', {'assets_count': 1, 'verified_assets_count': 1, 'targets_count': 1, 'monitored_systems_count': 1, 'enabled_monitored_systems_count': 1, 'fresh_heartbeat_count': 1}),
    ('E', {'assets_count': 1, 'verified_assets_count': 1, 'targets_count': 1, 'monitored_systems_count': 1, 'enabled_monitored_systems_count': 1, 'fresh_heartbeat_count': 1, 'fresh_poll_count': 1, 'reporting_systems_count': 1}),
    ('F', {'assets_count': 1, 'verified_assets_count': 1, 'targets_count': 1, 'monitored_systems_count': 1, 'enabled_monitored_systems_count': 1, 'reporting_systems_count': 1, 'detections_count': 1}),
    ('G', {'assets_count': 1, 'verified_assets_count': 1, 'targets_count': 1, 'monitored_systems_count': 1, 'enabled_monitored_systems_count': 1, 'reporting_systems_count': 1, 'detections_count': 1, 'alerts_count': 1}),
    ('H', {'assets_count': 1, 'verified_assets_count': 1, 'targets_count': 1, 'monitored_systems_count': 1, 'enabled_monitored_systems_count': 1, 'reporting_systems_count': 1, 'detections_count': 1, 'alerts_count': 1, 'incidents_count': 1}),
    ('I', {'assets_count': 1, 'verified_assets_count': 1, 'targets_count': 1, 'monitored_systems_count': 1, 'enabled_monitored_systems_count': 1, 'reporting_systems_count': 1, 'detections_count': 1, 'alerts_count': 1, 'incidents_count': 1, 'response_actions_count': 1}),
    ('J', {'assets_count': 1, 'verified_assets_count': 1, 'targets_count': 1, 'monitored_systems_count': 1, 'enabled_monitored_systems_count': 1, 'reporting_systems_count': 1, 'detections_count': 1, 'alerts_count': 1, 'incidents_count': 1, 'evidence_count': 1}),
    ('K', {'assets_count': 1, 'verified_assets_count': 1, 'targets_count': 1, 'monitored_systems_count': 1, 'enabled_monitored_systems_count': 1, 'reporting_systems_count': 0}),
    ('L', {'assets_count': 1, 'verified_assets_count': 1, 'targets_count': 1, 'monitored_systems_count': 1, 'enabled_monitored_systems_count': 1, 'fresh_heartbeat_count': 0, 'fresh_poll_count': 0, 'reporting_systems_count': 0}),
]


def test_cases_a_l_fixtures_keep_canonical_summary_contract_shape():
    for case_id, counters in CHAIN_FIXTURES:
        chain = build_runtime_setup_chain(counters=counters, timestamps={})
        payload = _canonical_summary({
            'workspace_configured': True,
            'runtime_status': 'healthy' if counters.get('reporting_systems_count', 0) > 0 else 'idle',
            'monitoring_status': 'healthy' if counters.get('reporting_systems_count', 0) > 0 else 'degraded',
            'freshness_status': 'fresh' if counters.get('reporting_systems_count', 0) > 0 else 'unavailable',
            'confidence_status': 'high' if counters.get('reporting_systems_count', 0) > 0 else 'unavailable',
            'protected_assets': counters.get('assets_count', 0),
            'monitored_systems': counters.get('monitored_systems_count', 0),
            'reporting_systems': counters.get('reporting_systems_count', 0),
            'protected_assets_count': counters.get('assets_count', 0),
            'monitored_systems_count': counters.get('monitored_systems_count', 0),
            'reporting_systems_count': counters.get('reporting_systems_count', 0),
            'runtime_setup_chain': chain,
            'evidence_source_summary': 'live_provider' if counters.get('reporting_systems_count', 0) > 0 else 'none',
        })
        assert isinstance(payload['runtime_setup_chain'].get('steps'), list), case_id
        assert payload['reporting_systems'] == payload['reporting_systems_count'], case_id
        assert payload['monitored_systems'] == payload['monitored_systems_count'], case_id


def test_zero_reporting_and_unavailable_telemetry_remain_explicit_in_canonical_summary():
    zero_reporting = _canonical_summary({'workspace_configured': True, 'monitoring_status': 'healthy', 'reporting_systems': 0, 'reporting_systems_count': 0, 'evidence_source_summary': 'live_provider'})
    unavailable = _canonical_summary({'workspace_configured': True, 'monitoring_status': 'healthy', 'reporting_systems': 1, 'reporting_systems_count': 1, 'freshness_status': 'unavailable'})
    assert zero_reporting['reporting_systems_count'] == 0
    assert zero_reporting['evidence_source_summary'] == 'live_provider'
    assert unavailable['freshness_status'] == 'unavailable'
