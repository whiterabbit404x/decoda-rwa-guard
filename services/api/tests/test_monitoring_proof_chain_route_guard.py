from __future__ import annotations

from fastapi.testclient import TestClient

from services.api.app import main as api_main


client = TestClient(api_main.app)
WORKSPACE_ID = '11111111-1111-1111-1111-111111111111'


def test_proof_chain_ensure_allows_simulator_mode(monkeypatch):
    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setattr(api_main, 'monitoring_runtime_status', lambda _request: {'evidence_source': 'simulator'})
    monkeypatch.setattr(
        api_main,
        'ensure_monitoring_proof_chain',
        lambda workspace_id, _request: {
            'workspace_id': workspace_id,
            'monitoring_run_id': 'run-1',
            'status': 'degraded',
            'reason': 'simulated_chain_explicitly_labeled_not_live_created',
            'evidence_source': 'simulator',
        },
    )

    response = client.post('/ops/monitoring/proof-chain/ensure', headers={'x-workspace-id': WORKSPACE_ID})

    assert response.status_code == 200
    payload = response.json()
    assert payload['workspace_id'] == WORKSPACE_ID
    assert payload['completion_status'] == 'degraded'
    assert payload['evidence_source'] == 'simulator'


def test_proof_chain_ensure_denies_live_mode(monkeypatch):
    monkeypatch.setattr(api_main, 'with_auth_schema_json', lambda handler: handler())
    monkeypatch.setattr(api_main, 'monitoring_runtime_status', lambda _request: {'evidence_source': 'live'})

    response = client.post('/ops/monitoring/proof-chain/ensure', headers={'x-workspace-id': WORKSPACE_ID})

    assert response.status_code == 409
    assert response.json()['detail'] == 'Simulator-only action unavailable in live mode'
