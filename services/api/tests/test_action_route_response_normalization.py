from __future__ import annotations

from services.api.app.main import _normalize_action_list_route_response, _normalize_action_route_response


def test_action_route_response_always_includes_mode_and_audit_metadata() -> None:
    payload = _normalize_action_route_response(
        {
            'id': 'act-1',
            'status': 'pending',
            'execution_state': 'proposed',
            'dry_run': False,
            'provider_request_id': 'req-1',
            'provider_response_id': 'resp-1',
            'safe_tx_hash': '0xabc',
            'error_reason': None,
            'failed_at': '2026-04-28T00:00:00+00:00',
        }
    )

    assert payload['mode'] == 'live'
    assert payload['execution_provenance']['mode'] == 'live'
    assert payload['audit_metadata']['mode'] == 'live'
    assert payload['audit_metadata']['action_id'] == 'act-1'
    assert payload['audit_metadata']['provider_request_id'] == 'req-1'
    assert payload['audit_metadata']['provider_response_id'] == 'resp-1'
    assert payload['audit_metadata']['provider_id'] == 'resp-1'
    assert payload['execution_provenance']['failed_at'] == '2026-04-28T00:00:00+00:00'
    assert payload['audit_metadata']['tx_hash'] == '0xabc'


def test_action_list_route_response_normalizes_each_action_with_audit_metadata() -> None:
    payload = _normalize_action_list_route_response(
        {
            'actions': [
                {'id': 'act-sim', 'mode': 'simulated', 'status': 'executed', 'execution_state': 'simulated_executed'},
                {'id': 'act-live', 'dry_run': False, 'status': 'pending', 'execution_state': 'proposed'},
            ]
        }
    )

    assert payload['actions'][0]['audit_metadata']['mode'] == 'simulated'
    assert payload['actions'][1]['audit_metadata']['mode'] == 'live'
    assert payload['actions'][1]['execution_provenance']['mode'] == 'live'
